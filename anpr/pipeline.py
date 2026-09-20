"""ANPR pipeline: vehicle detect -> track -> plate detect -> OCR -> vote.

The order matters, and so does the last step. Reading a plate on one frame and
keeping the answer is what produces confident nonsense on cameras like this
grid's: characters 7-10 px tall, glare, motion blur. Reading it on every frame
of the track and voting is what turns ten poor reads into one correct string.

Plates are searched inside the vehicle crop rather than the whole frame. On a
1920x1080 feed a plate is 30-100 px wide; a detector resized to 608 px sees it
at a fraction of that, while the same detector run on a 300 px vehicle crop
sees it at full resolution.

A track is only emitted when it *ends* — when the vehicle leaves or the feed
loops — because until then more frames may still improve the vote.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import cv2
import numpy as np

from anpr.plates import PlateRead, VoteResult, vote
from anpr.quality import FrameGate
from anpr.tracker import ByteTrackLite, Detection, Track, crop

log = logging.getLogger("anpr.pipeline")

# COCO classes worth tracking. Bicycles are in: a registration plate never
# appears on one, but an object that the detector calls a bicycle is often a
# motorcycle seen badly, and two-wheelers are most of Indian traffic.
VEHICLE_LABELS = {"car", "motorcycle", "bus", "truck", "bicycle", "train"}


@dataclass
class PlateEvent:
    """A finished track with its voted plate."""
    camera: str
    track_key: str
    track_id: int
    epoch: int
    vehicle_class: str
    plate: str
    confidence: float
    reads: int
    agreeing_reads: int
    valid_format: bool
    repaired: bool
    usable: bool
    plate_width_px: float
    char_height_px: float
    first_pts_ms: float
    last_pts_ms: float
    best_pts_ms: float
    observed_at: datetime
    frames: int
    model: str

    def as_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["observed_at"] = self.observed_at.isoformat()
        return d


@dataclass
class _TrackState:
    reads: list[PlateRead] = field(default_factory=list)
    frames: int = 0
    label: str = ""
    first_pts_ms: float = 0.0
    last_pts_ms: float = 0.0
    epoch: int = 0


class AnprPipeline:
    def __init__(
        self,
        camera: str,
        *,
        vehicle_model: str | None = "rf-detr-small-512-coco",
        plate_model: str = "yolo-v9-s-608-license-plate-end2end",
        ocr_model: str | None = "cct-xs-v2-global-model",
        vehicle_conf: float = 0.35,
        min_plate_px: float = 20.0,
        run_id: str | None = None,
        media_epoch_wall: datetime | None = None,
    ) -> None:
        self.camera = camera
        self.run_id = run_id or f"{camera}-{int(time.time())}"
        self.vehicle_conf = vehicle_conf
        self.min_plate_px = min_plate_px
        self.model_note = f"{vehicle_model or 'plates-only'} + {plate_model} + {ocr_model or 'no-ocr'}"

        self.tracker = ByteTrackLite()
        self.gate = FrameGate()
        self._states: dict[int, _TrackState] = {}
        self._seen_ids: set[int] = set()

        # Media time -> wall clock. The sandbox replays recorded footage, so a
        # frame's PTS is not "now". Anchor once and map linearly; in production
        # this anchor comes from the camera's RTCP/NTP mapping instead.
        self._wall_anchor = media_epoch_wall or datetime.now(timezone.utc)
        self._pts_anchor: float | None = None

        self.vehicle_detector = self._load_detector(vehicle_model) if vehicle_model else None
        self.plate_detector = self._load_detector(plate_model)
        self.ocr = self._load_ocr(ocr_model) if ocr_model else None

        self.stats = {"frames": 0, "vehicles": 0, "plate_boxes": 0, "ocr_calls": 0,
                      "detect_ms": 0.0, "plate_ms": 0.0, "ocr_ms": 0.0}

    # -- model loading ----------------------------------------------------
    @staticmethod
    def _load_detector(model: str) -> Any:
        from open_image_models import create_detector

        return create_detector(model)

    @staticmethod
    def _load_ocr(model: str) -> Any:
        from fast_plate_ocr import LicensePlateRecognizer

        return LicensePlateRecognizer(model)

    # -- helpers ----------------------------------------------------------
    def _wall_time(self, pts_ms: float) -> datetime:
        if self._pts_anchor is None:
            self._pts_anchor = pts_ms
        return self._wall_anchor + timedelta(milliseconds=pts_ms - self._pts_anchor)

    @staticmethod
    def _boxes(detector: Any, image: np.ndarray) -> list[tuple[tuple[float, float, float, float], float, str]]:
        if image.size == 0:
            return []
        out = []
        for det in detector.predict(image) or []:
            box = getattr(det, "bounding_box", None)
            if box is None:
                continue
            out.append(((float(box.x1), float(box.y1), float(box.x2), float(box.y2)),
                        float(getattr(det, "confidence", 0.0)),
                        str(getattr(det, "label", ""))))
        return out

    def _read_plate(self, plate_img: np.ndarray) -> tuple[str, float, tuple[float, ...]] | None:
        """One OCR call. Returns the text, its mean confidence, and the
        per-character probabilities that the vote weights by."""
        if self.ocr is None or plate_img.size == 0:
            return None
        t0 = time.perf_counter()
        try:
            # The cct models take RGB (the config declares 64x128x3); passing
            # grayscale fails the input shape check.
            rgb = cv2.cvtColor(plate_img, cv2.COLOR_BGR2RGB)
            preds = self.ocr.run(rgb, return_confidence=True)
        except Exception as exc:
            log.debug("OCR failed: %r", exc)
            return None
        finally:
            self.stats["ocr_ms"] += (time.perf_counter() - t0) * 1000
            self.stats["ocr_calls"] += 1
        if not preds:
            return None
        pred = preds[0]
        text = str(getattr(pred, "plate", "") or "")
        probs = getattr(pred, "char_probs", None)
        char_confs = tuple(float(p) for p in probs) if probs is not None else ()
        conf = float(np.mean(char_confs)) if char_confs else 0.0
        return text, conf, char_confs

    # -- main loop --------------------------------------------------------
    def process(self, image: np.ndarray, *, pts_ms: float, epoch: int = 0) -> list[PlateEvent]:
        """Feed one frame. Returns events for any track that ended on it."""
        self.stats["frames"] += 1
        events: list[PlateEvent] = []

        # A corrupt frame is worse than no frame: the detector answers it
        # confidently. Drop it before anything else looks at it.
        good, _ = self.gate.check(image)
        if not good:
            return events

        if self.vehicle_detector is not None:
            t0 = time.perf_counter()
            raw = self._boxes(self.vehicle_detector, image)
            self.stats["detect_ms"] += (time.perf_counter() - t0) * 1000
            dets = [Detection(b, c, lab) for b, c, lab in raw
                    if lab in VEHICLE_LABELS and c >= self.vehicle_conf]
        else:
            # Plate-only mode: track the plates themselves.
            t0 = time.perf_counter()
            raw = self._boxes(self.plate_detector, image)
            self.stats["plate_ms"] += (time.perf_counter() - t0) * 1000
            dets = [Detection(b, c, "plate") for b, c, _ in raw]

        before = {t.track_id for t in self.tracker.tracks}
        live = self.tracker.update(dets, pts_ms=pts_ms, epoch=epoch)
        self.stats["vehicles"] += len(live)

        for track in live:
            state = self._states.setdefault(track.track_id, _TrackState(
                label=track.label, first_pts_ms=track.first_pts_ms, epoch=epoch))
            state.frames += 1
            state.last_pts_ms = pts_ms
            state.label = track.label or state.label
            self._seen_ids.add(track.track_id)
            self._read_track(image, track, state, pts_ms)

        # A track that is gone from the tracker has ended: vote and emit.
        after = {t.track_id for t in self.tracker.tracks}
        for track_id in before - after:
            event = self._finish(track_id)
            if event:
                events.append(event)
        return events

    def _read_track(self, image: np.ndarray, track: Track, state: _TrackState,
                    pts_ms: float) -> None:
        if self.vehicle_detector is None:
            # The track box is already the plate.
            x1, y1, x2, y2 = track.xyxy
            plate_img = crop(image, (x1, y1, x2, y2), pad=0.08)
            width = x2 - x1
            if width >= self.min_plate_px:
                self._record_read(state, plate_img, width, y2 - y1, pts_ms)
            return

        vehicle = crop(image, track.xyxy, pad=0.05)
        if vehicle.size == 0:
            return
        t0 = time.perf_counter()
        plates = self._boxes(self.plate_detector, vehicle)
        self.stats["plate_ms"] += (time.perf_counter() - t0) * 1000
        if not plates:
            return
        self.stats["plate_boxes"] += len(plates)
        (px1, py1, px2, py2), _, _ = max(plates, key=lambda p: (p[0][2] - p[0][0]))
        width, height = px2 - px1, py2 - py1
        if width < self.min_plate_px:
            return
        self._record_read(state, crop(vehicle, (px1, py1, px2, py2), pad=0.08),
                          width, height, pts_ms)

    def _record_read(self, state: _TrackState, plate_img: np.ndarray, width: float,
                     height: float, pts_ms: float) -> None:
        read = self._read_plate(plate_img)
        if read is None:
            return
        text, conf, char_confs = read
        state.reads.append(PlateRead(text=text, confidence=conf, pts_ms=pts_ms,
                                     plate_width_px=width, plate_height_px=height,
                                     char_confidences=char_confs))

    def _finish(self, track_id: int) -> PlateEvent | None:
        state = self._states.pop(track_id, None)
        if state is None or not state.reads:
            return None
        result: VoteResult | None = vote(state.reads)
        if result is None:
            return None
        best = max(state.reads, key=lambda r: r.confidence)
        return PlateEvent(
            camera=self.camera,
            track_key=f"{self.run_id}:{track_id}",
            track_id=track_id,
            epoch=state.epoch,
            vehicle_class=state.label or "unknown",
            plate=result.plate,
            confidence=round(result.confidence, 3),
            reads=result.reads,
            agreeing_reads=result.agreeing_reads,
            valid_format=result.valid_format,
            repaired=result.repaired,
            usable=result.is_usable,
            plate_width_px=round(result.best_width_px, 1),
            char_height_px=round(best.plate_height_px * 0.5, 1),
            first_pts_ms=state.first_pts_ms,
            last_pts_ms=state.last_pts_ms,
            best_pts_ms=result.best_pts_ms,
            observed_at=self._wall_time(result.best_pts_ms),
            frames=state.frames,
            model=self.model_note,
        )

    def flush(self) -> Iterator[PlateEvent]:
        """Vote on every track still open. Call when the stream ends."""
        for track_id in list(self._states):
            event = self._finish(track_id)
            if event:
                yield event

    def summary(self) -> dict[str, Any]:
        s = dict(self.stats)
        s["tracks_seen"] = len(self._seen_ids)
        s.update(self.gate.stats())
        for key in ("detect_ms", "plate_ms", "ocr_ms"):
            s[key] = round(s[key], 1)
        if s["frames"]:
            s["ms_per_frame"] = round((s["detect_ms"] + s["plate_ms"] + s["ocr_ms"]) / s["frames"], 1)
        return s
