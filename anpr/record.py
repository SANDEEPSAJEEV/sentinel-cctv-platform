"""Record a short clip from a camera, with its real timestamps preserved.

    python -m anpr.record --auth --camera cam06 --seconds 60 --out out/cam06.mp4

Why this exists: grid watch time is metered (CLAUDE.md §3), and CPU inference
is far slower than real time. Streaming live while the pipeline crawls spends
quota to watch the pipeline fall behind. Record once, iterate offline.

The clip is written at a nominal frame rate, which throws away the real frame
intervals — and this grid's intervals are the whole problem (20 ms to 2040 ms).
So every frame's PTS goes to a sidecar JSON, and the worker replays the clip on
those timestamps rather than on the container's fps.

Nothing in the recording carries credentials: the URL is never drawn onto a
frame and never written to the sidecar.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone

import cv2

log = logging.getLogger("anpr.record")


def main() -> None:
    ap = argparse.ArgumentParser(description="Record a clip with its PTS sidecar")
    ap.add_argument("--camera", required=True)
    ap.add_argument("--auth", action="store_true", help="the real grid (spends quota)")
    ap.add_argument("--host", default="http://127.0.0.1:8080")
    ap.add_argument("--transport", choices=["rtsp", "hls"], default=None)
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fps", type=float, default=25.0, help="nominal container fps")
    ap.add_argument("--corrupt-limit", type=int, default=75,
                    help="corrupt frames in a row before rejoining (about 3 s at 25 fps)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)

    from sentinel_client import SentinelCapture, catalogue, rtsp_url_of

    if args.auth:
        from sentinel_auth import SentinelSession, camera_id_of

        session = SentinelSession()
        session.login()
        cams = session.catalogue()
        match = [c for c in cams if camera_id_of(c) == args.camera]
        if not match:
            raise SystemExit(f"camera {args.camera} is not in the catalogue")
        transport = args.transport or session.choose_transport()
        url = session.stream_url(args.camera, transport)
        cap_opts = session.capture_options(transport)
    else:
        cams = catalogue(args.host)
        match = [c for c in cams if str(c.get("id")) == args.camera]
        if not match:
            raise SystemExit(f"camera {args.camera} is not in {args.host}")
        url, cap_opts = rtsp_url_of(match[0]), None

    from anpr.quality import FrameGate

    writer = None
    meta: list[dict[str, float | int]] = []
    started = datetime.now(timezone.utc)
    gate = FrameGate()

    with SentinelCapture(url, capture_options=cap_opts) as cap:
        for frame in cap.frames(max_seconds=args.seconds):
            if not frame.realtime:            # skip the GOP-replay burst
                continue
            # Don't record decoder mush. A desynced H.265 decoder keeps
            # producing frames; on cam06 that was 75% of a 60 s recording.
            good, _ = gate.check(frame.image)
            if not good:
                # Joining mid-GOP produces corrupt frames until the next IDR,
                # so give the decoder room to sync before giving up on it —
                # reconnecting too eagerly just starts the same problem again.
                if gate.corrupt_streak >= args.corrupt_limit:
                    cap.request_reconnect(f"{gate.corrupt_streak} corrupt frames")
                    gate.reset()
                continue
            if writer is None:
                h, w = frame.image.shape[:2]
                writer = cv2.VideoWriter(args.out, cv2.VideoWriter_fourcc(*"mp4v"),
                                         args.fps, (w, h))
                log.info("recording %dx%d to %s", w, h, args.out)
            writer.write(frame.image)
            # session = reconnect count. PTS restarts near zero on every
            # rejoin, so a replay that ignores this sees time run backwards.
            meta.append({"pts_ms": round(frame.pts_ms, 1), "epoch": frame.epoch,
                         "session": cap.reconnects, "gap_ms": round(frame.gap_ms, 1)})

    if writer is None:
        raise SystemExit("no frames recorded")
    writer.release()

    sidecar = args.out + ".pts.json"
    with open(sidecar, "w", encoding="utf-8") as fh:
        json.dump({"camera": args.camera, "recorded_utc": started.isoformat(),
                   "frames": len(meta), "nominal_fps": args.fps,
                   "quality": gate.stats(), "reconnects": cap.reconnects, "pts": meta}, fh)

    span = 0.0
    seg_first = seg_last = None
    seg_key = None
    for m in meta:
        key = (m["session"], m["epoch"])
        if key != seg_key:
            if seg_first is not None:
                span += (seg_last - seg_first) / 1000.0
            seg_key, seg_first = key, m["pts_ms"]
        seg_last = m["pts_ms"]
    if seg_first is not None:
        span += (seg_last - seg_first) / 1000.0
    log.info("%d frames kept, %.1f s of media, %d dropped as corrupt (%.0f%%), %d reconnect(s)",
             len(meta), span, gate.rejected, gate.corrupt_fraction * 100, cap.reconnects)


if __name__ == "__main__":
    main()
