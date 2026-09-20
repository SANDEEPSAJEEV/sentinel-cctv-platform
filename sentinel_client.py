"""
Robust RTSP capture for the Sentinel camera grid.

Everything the organiser's Resources page warns about is handled here, so the
rest of the pipeline never has to think about it:

  * RTSP forced over TCP                    (UDP corrupts frames behind NAT)
  * all timing from PTS, never arrival time (the gateway replays a buffered GOP
                                             on connect, so the first 1-2 s
                                             arrive faster than real time)
  * non-uniform frame intervals tolerated   (gaps are not disconnects)
  * automatic reconnect with backoff        (feeds are supervised and restart)
  * decoder warnings at join are not fatal
  * loop discontinuity detected and exposed as an "epoch" counter, so
    re-identification galleries and track ids can reset on a hard cut

Usage
-----
    from sentinel_client import catalogue, SentinelCapture

    for cam in catalogue("http://localhost:8080"):
        with SentinelCapture(cam["urls"]["rtsp"]) as cap:
            for f in cap.frames(max_seconds=30):
                if not f.realtime:
                    continue          # inside the GOP-replay burst
                process(f.image, pts_ms=f.pts_ms, epoch=f.epoch)
"""

from __future__ import annotations

import os

# MUST be set before cv2 is imported, or FFmpeg will not pick it up.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")

import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Iterator

import cv2
import numpy as np
import requests

log = logging.getLogger("sentinel")

# A backwards jump in PTS larger than this means the recording looped.
LOOP_BACKSTEP_MS = 1_000.0
# GOP replay: on connect the gateway dumps a buffered GOP faster than real
# time. The burst is over once frames arrive at the pace their PTS advances —
# judged over a short window, then latched until the next reconnect. (Comparing
# PTS lead against the first frame does not work: after the burst the lead
# stays at the burst length forever, so it never drops back under a threshold.)
REPLAY_WINDOW = 8          # frames
REPLAY_PACE = 0.6          # wall span / PTS span at or above this = live pace
REPLAY_MAX_S = 5.0         # no burst lasts longer than this; latch live anyway

# Credentials are embedded in RTSP/WHEP URLs; never let them reach a log file.
_CRED_RE = re.compile(r"(?<=//)[^/@\s]+:[^/@\s]+(?=@)")


@dataclass(slots=True)
class Frame:
    image: np.ndarray
    pts_ms: float          # presentation timestamp — the only trustworthy clock
    epoch: int             # increments at each loop discontinuity
    index: int             # frame counter within this capture session
    realtime: bool         # False while inside the GOP-replay burst
    gap_ms: float          # PTS delta from the previous frame (0.0 for the first)


def catalogue(host: str, timeout: float = 10.0) -> list[dict[str, Any]]:
    """Read the camera catalogue. The catalogue is the contract; never
    hard-code stream URLs."""
    url = host.rstrip("/") + "/api/ingest"
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    payload = resp.json()
    if isinstance(payload, dict):
        for key in ("cameras", "items", "data", "results"):
            if isinstance(payload.get(key), list):
                return payload[key]
        # some deployments return a bare mapping of id -> camera
        return [v for v in payload.values() if isinstance(v, dict)]
    if isinstance(payload, list):
        return payload
    raise ValueError(f"unexpected catalogue shape from {url}: {type(payload)}")


def rtsp_url_of(cam: dict[str, Any]) -> str | None:
    """Pull the RTSP URL out of a catalogue entry without assuming its shape."""
    urls = cam.get("urls")
    if isinstance(urls, dict):
        for key in ("rtsp", "RTSP", "rtsp_url"):
            if isinstance(urls.get(key), str):
                return urls[key]
    for key in ("rtsp", "rtsp_url", "url"):
        val = cam.get(key)
        if isinstance(val, str) and val.startswith("rtsp"):
            return val
    return None


class SentinelCapture:
    """A VideoCapture that survives the things this grid actually does."""

    def __init__(
        self,
        url: str,
        *,
        capture_options: str | None = None,
        backoff_start: float = 2.0,
        backoff_cap: float = 30.0,
        max_reconnects: int = 1_000,
        open_timeout_ms: int = 10_000,
    ) -> None:
        self.url = url
        # RTSP needs rtsp_transport;tcp — HLS off the CDN needs the session
        # cookie instead. SentinelSession.capture_options() builds either.
        self.capture_options = capture_options or (
            "rtsp_transport;tcp" if url.startswith("rtsp") else ""
        )
        self.backoff_start = backoff_start
        self.backoff_cap = backoff_cap
        self.max_reconnects = max_reconnects
        self.open_timeout_ms = open_timeout_ms

        self._cap: cv2.VideoCapture | None = None
        self.epoch = 0
        self.reconnects = 0
        self.decode_warnings = 0
        self._reconnect_requested = False

    def request_reconnect(self, reason: str = "") -> None:
        """Drop the connection and rejoin at the next frame.

        The only way back from a decoder that has lost sync: it keeps emitting
        frames built on references that never arrived, and no amount of waiting
        fixes that if the stream's next IDR is minutes away. Callers use this
        when frame *content* goes bad while frames keep arriving.
        """
        self._reconnect_requested = True
        if reason:
            log.info("%s: reconnect requested (%s)", self.safe_url, reason)

    @property
    def safe_url(self) -> str:
        """URL with embedded credentials stripped — use this in every log line.
        RTSP URLs carry your email and access password in plaintext."""
        return _CRED_RE.sub("***:***", self.url)

    # -- lifecycle --------------------------------------------------------
    def __enter__(self) -> "SentinelCapture":
        self._open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _open(self) -> bool:
        self.close()
        # OpenCV reads this env var when the capture is constructed, so it must
        # be set per-URL rather than once at import.
        if self.capture_options:
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = self.capture_options
        # Timeouts only take effect as open-time params; cap.set() after the
        # constructor is too late and FFmpeg's 30 s default applies.
        params = [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, self.open_timeout_ms,
            cv2.CAP_PROP_READ_TIMEOUT_MSEC, self.open_timeout_ms,
        ]
        cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG, params)
        if not cap.isOpened():
            cap.release()
            return False
        self._cap = cap
        return True

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def stream_info(self) -> dict[str, Any]:
        """Declared codec and fps, for the record only. Never time anything
        from declared fps — the grid declares 30 on feeds delivering 15."""
        if self._cap is None:
            return {}
        fourcc = int(self._cap.get(cv2.CAP_PROP_FOURCC))
        codec = fourcc.to_bytes(4, "little").decode("ascii", "replace").strip("\x00 ")
        return {"codec": codec or None, "declared_fps": self._cap.get(cv2.CAP_PROP_FPS) or None}

    # -- frames -----------------------------------------------------------
    def frames(
        self,
        *,
        max_seconds: float | None = None,
        max_frames: int | None = None,
    ) -> Iterator[Frame]:
        """Yield frames until the limits are hit. Reconnects transparently."""
        started = time.monotonic()
        index = 0
        backoff = self.backoff_start

        while True:
            if max_seconds is not None and time.monotonic() - started >= max_seconds:
                return
            if max_frames is not None and index >= max_frames:
                return

            if self._cap is None and not self._open():
                self.reconnects += 1
                if self.reconnects > self.max_reconnects:
                    log.warning("%s: giving up after %d reconnects", self.safe_url,self.reconnects)
                    return
                log.info("%s: open failed, retrying in %.1fs", self.safe_url,backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, self.backoff_cap)
                continue

            connect_wall = time.monotonic()
            recent: deque[tuple[float, float]] = deque(maxlen=REPLAY_WINDOW)
            live = False
            last_pts: float | None = None
            consecutive_failures = 0

            assert self._cap is not None
            while True:
                if max_seconds is not None and time.monotonic() - started >= max_seconds:
                    return
                if max_frames is not None and index >= max_frames:
                    return

                ok, image = self._cap.read()
                if not ok or image is None:
                    # A decode hiccup at join is normal and self-corrects once the
                    # first IDR arrives; only treat a run of them as a disconnect.
                    consecutive_failures += 1
                    self.decode_warnings += 1
                    if consecutive_failures < 15:
                        time.sleep(0.02)
                        continue
                    log.info("%s: stream dropped, reconnecting in %.1fs", self.safe_url,backoff)
                    self.close()
                    self.reconnects += 1
                    time.sleep(backoff)
                    backoff = min(backoff * 2, self.backoff_cap)
                    break

                consecutive_failures = 0
                backoff = self.backoff_start  # healthy read resets the backoff

                pts_ms = float(self._cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0)
                now = time.monotonic()

                # Loop discontinuity: the recording restarted. Downstream state
                # (background models, re-id gallery, track ids) must reset.
                gap_ms = 0.0
                if last_pts is not None:
                    gap_ms = pts_ms - last_pts
                    if gap_ms < -LOOP_BACKSTEP_MS:
                        self.epoch += 1
                        log.info("%s: loop discontinuity -> epoch %d", self.safe_url,self.epoch)
                        recent.clear()
                        gap_ms = 0.0
                last_pts = pts_ms

                # GOP replay: frames arriving faster than their PTS advances.
                if not live:
                    recent.append((now, pts_ms))
                    if len(recent) == REPLAY_WINDOW:
                        wall_span = (recent[-1][0] - recent[0][0]) * 1000.0
                        pts_span = recent[-1][1] - recent[0][1]
                        live = pts_span <= 0 or wall_span >= REPLAY_PACE * pts_span
                    if now - connect_wall > REPLAY_MAX_S:
                        live = True

                yield Frame(
                    image=image,
                    pts_ms=pts_ms,
                    epoch=self.epoch,
                    index=index,
                    realtime=live,
                    gap_ms=gap_ms,
                )
                index += 1

                if self._reconnect_requested:
                    self._reconnect_requested = False
                    self.close()
                    self.reconnects += 1
                    time.sleep(self.backoff_start)
                    break
