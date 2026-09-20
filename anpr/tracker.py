"""ByteTrack-style multi-object tracking with a time-aware motion model.

Follows the ByteTrack association scheme (Zhang et al., 2022): associate the
high-confidence detections first, then recover tracks from the low-confidence
ones that a threshold would normally throw away. That second pass is what keeps
a vehicle's identity through occlusion and through the motion blur that makes a
detector hesitate.

Why this is written here instead of importing the reference implementation:
upstream advances its Kalman filter one *frame* per step, which assumes frames
arrive at a fixed rate. This grid does not deliver that. Measured inter-frame
gaps on the organiser's cameras run from 20 ms to 2040 ms, feeds stall for
seconds, and CLAUDE.md §4 requires that motion models be fed PTS deltas rather
than frame counts. A tracker that counts frames computes an impossible velocity
after every stall and splits one vehicle into several tracks. So the state here
is in units per second, and every predict step takes the real dt.

Not the original ByteTrack code. Same algorithm, different clock.

State vector: [cx, cy, w, h, vx, vy] — centre, size, and centre velocity in
pixels per second. Size is treated as slowly varying rather than modelled with
its own velocity, which is stable when a vehicle is only a few dozen pixels
across.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np


@dataclass(slots=True)
class Detection:
    xyxy: tuple[float, float, float, float]
    confidence: float
    label: str = ""


@dataclass
class Track:
    track_id: int
    label: str
    mean: np.ndarray                 # [cx, cy, w, h, vx, vy]
    covariance: np.ndarray
    confidence: float
    epoch: int
    first_pts_ms: float
    last_pts_ms: float
    hits: int = 1
    age_ms: float = 0.0
    time_since_update_ms: float = 0.0
    state: str = "tentative"         # tentative -> confirmed -> lost
    history: list[tuple[float, tuple[float, float, float, float]]] = field(default_factory=list)

    @property
    def xyxy(self) -> tuple[float, float, float, float]:
        cx, cy, w, h = self.mean[:4]
        return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)

    @property
    def speed_px_s(self) -> float:
        return float(np.hypot(self.mean[4], self.mean[5]))


def _to_xywh(xyxy: Sequence[float]) -> np.ndarray:
    x1, y1, x2, y2 = xyxy
    return np.array([(x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1], dtype=float)


def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """IoU between two sets of xyxy boxes."""
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    lt = np.maximum(a[:, None, :2], b[None, :, :2])
    rb = np.minimum(a[:, None, 2:], b[None, :, 2:])
    wh = np.clip(rb - lt, 0, None)
    inter = wh[..., 0] * wh[..., 1]
    return inter / np.maximum(area_a[:, None] + area_b[None, :] - inter, 1e-9)


class KalmanBox:
    """Constant-velocity filter in pixels and seconds.

    Process and measurement noise scale with box size, as in SORT/ByteTrack: a
    plate-sized box 30 px across should not be held to the same positional
    tolerance as a lorry filling the frame.
    """

    def __init__(self, xyxy: Sequence[float]) -> None:
        cx, cy, w, h = _to_xywh(xyxy)
        self.mean = np.array([cx, cy, w, h, 0.0, 0.0])
        self.covariance = np.diag([w, h, w, h, 10 * w, 10 * h]) ** 2 / 4

    def predict(self, dt_s: float) -> None:
        dt_s = max(0.0, min(dt_s, 2.0))   # a longer gap than this is a scene change
        f = np.eye(6)
        f[0, 4] = f[1, 5] = dt_s
        self.mean = f @ self.mean
        w, h = max(self.mean[2], 1.0), max(self.mean[3], 1.0)
        q = np.diag([0.05 * w, 0.05 * h, 0.02 * w, 0.02 * h, 0.5 * w, 0.5 * h]) ** 2
        self.covariance = f @ self.covariance @ f.T + q * max(dt_s, 1e-3)

    def update(self, xyxy: Sequence[float]) -> None:
        z = _to_xywh(xyxy)
        h_mat = np.zeros((4, 6))
        h_mat[:4, :4] = np.eye(4)
        w, ht = max(z[2], 1.0), max(z[3], 1.0)
        r = np.diag([0.05 * w, 0.05 * ht, 0.05 * w, 0.05 * ht]) ** 2
        s = h_mat @ self.covariance @ h_mat.T + r
        k = self.covariance @ h_mat.T @ np.linalg.inv(s)
        self.mean = self.mean + k @ (z - h_mat @ self.mean)
        self.covariance = (np.eye(6) - k @ h_mat) @ self.covariance


class ByteTrackLite:
    """Two-stage association tracker driven by PTS, not by frame counts.

    All thresholds that would normally be "number of frames" are milliseconds
    of media time, so behaviour is identical on a 30 fps feed and on the 5 fps
    one next to it.
    """

    def __init__(
        self,
        *,
        high_thresh: float = 0.5,
        low_thresh: float = 0.1,
        match_iou: float = 0.3,
        second_match_iou: float = 0.5,
        max_lost_ms: float = 1500.0,
        confirm_hits: int = 3,
    ) -> None:
        self.high_thresh = high_thresh
        self.low_thresh = low_thresh
        self.match_iou = match_iou
        self.second_match_iou = second_match_iou
        self.max_lost_ms = max_lost_ms
        self.confirm_hits = confirm_hits

        self.tracks: list[Track] = []
        self._filters: dict[int, KalmanBox] = {}
        self._next_id = 1
        self._last_pts: float | None = None
        self._epoch = 0

    # -- lifecycle --------------------------------------------------------
    def reset(self, epoch: int) -> None:
        """Drop all state at a loop discontinuity. After a hard cut the scene
        is unrelated to what came before; carrying tracks across it invents
        journeys that never happened."""
        self.tracks.clear()
        self._filters.clear()
        self._last_pts = None
        self._epoch = epoch

    def update(self, detections: Sequence[Detection], *, pts_ms: float,
               epoch: int = 0) -> list[Track]:
        if epoch != self._epoch:
            self.reset(epoch)

        dt_s = 0.0 if self._last_pts is None else max(0.0, (pts_ms - self._last_pts) / 1000.0)
        self._last_pts = pts_ms

        for track in self.tracks:
            self._filters[track.track_id].predict(dt_s)
            track.mean = self._filters[track.track_id].mean
            track.age_ms += dt_s * 1000.0
            track.time_since_update_ms += dt_s * 1000.0

        high = [d for d in detections if d.confidence >= self.high_thresh]
        low = [d for d in detections if self.low_thresh <= d.confidence < self.high_thresh]

        # Stage 1: confirmed and tentative tracks against confident detections.
        unmatched_tracks, unmatched_high = self._associate(self.tracks, high, self.match_iou, pts_ms)

        # Stage 2: whatever is left, against the detections a threshold would
        # have discarded. This is the part that survives motion blur.
        still_unmatched, _ = self._associate(
            [self.tracks[i] for i in unmatched_tracks], low, self.second_match_iou, pts_ms)
        lost_idx = {unmatched_tracks[i] for i in still_unmatched}

        for i, track in enumerate(self.tracks):
            if i in lost_idx and track.time_since_update_ms > 0:
                track.state = "lost"

        for d in (high[i] for i in unmatched_high):
            self._spawn(d, pts_ms)

        self.tracks = [t for t in self.tracks if t.time_since_update_ms <= self.max_lost_ms]
        live = {t.track_id for t in self.tracks}
        self._filters = {k: v for k, v in self._filters.items() if k in live}
        return [t for t in self.tracks if t.state == "confirmed"]

    # -- internals --------------------------------------------------------
    def _associate(self, tracks: list[Track], dets: list[Detection], thresh: float,
                   pts_ms: float) -> tuple[list[int], list[int]]:
        """Returns (unmatched track indices, unmatched detection indices)."""
        if not tracks or not dets:
            return list(range(len(tracks))), list(range(len(dets)))
        t_boxes = np.array([t.xyxy for t in tracks])
        d_boxes = np.array([d.xyxy for d in dets])
        ious = iou_matrix(t_boxes, d_boxes)
        # Greedy best-first matching rather than Hungarian: it needs no scipy
        # (a 45 MB dependency to vendor into an egress-restricted deployment),
        # and with a few dozen boxes per frame the assignments agree in all but
        # pathological overlap. ByteTrack's benefit is the two-stage pass below,
        # not the solver.
        matched_t: set[int] = set()
        matched_d: set[int] = set()
        order = np.dstack(np.unravel_index(np.argsort(-ious, axis=None), ious.shape))[0]
        for r, c in order:
            r, c = int(r), int(c)
            if ious[r, c] < thresh:
                break
            if r in matched_t or c in matched_d:
                continue
            self._apply_match(tracks[r], dets[c], pts_ms)
            matched_t.add(r)
            matched_d.add(c)
        return ([i for i in range(len(tracks)) if i not in matched_t],
                [i for i in range(len(dets)) if i not in matched_d])

    def _apply_match(self, track: Track, det: Detection, pts_ms: float) -> None:
        kf = self._filters[track.track_id]
        kf.update(det.xyxy)
        track.mean = kf.mean
        track.covariance = kf.covariance
        track.confidence = det.confidence
        track.hits += 1
        track.last_pts_ms = pts_ms
        track.time_since_update_ms = 0.0
        track.history.append((pts_ms, det.xyxy))
        if det.label:
            track.label = det.label
        if track.state != "confirmed" and track.hits >= self.confirm_hits:
            track.state = "confirmed"
        elif track.state == "lost":
            track.state = "confirmed"

    def _spawn(self, det: Detection, pts_ms: float) -> None:
        kf = KalmanBox(det.xyxy)
        track = Track(
            track_id=self._next_id,
            label=det.label,
            mean=kf.mean,
            covariance=kf.covariance,
            confidence=det.confidence,
            epoch=self._epoch,
            first_pts_ms=pts_ms,
            last_pts_ms=pts_ms,
            history=[(pts_ms, det.xyxy)],
        )
        # A single sighting is not a vehicle yet; confirm_hits guards against
        # the detector's one-frame false positives (signage, taillights).
        if self.confirm_hits <= 1:
            track.state = "confirmed"
        self._filters[self._next_id] = kf
        self.tracks.append(track)
        self._next_id += 1


def crop(image: np.ndarray, xyxy: Iterable[float], pad: float = 0.0) -> np.ndarray:
    x1, y1, x2, y2 = (float(v) for v in xyxy)
    px, py = (x2 - x1) * pad, (y2 - y1) * pad
    h, w = image.shape[:2]
    x1 = max(0, int(x1 - px)); y1 = max(0, int(y1 - py))
    x2 = min(w, int(x2 + px)); y2 = min(h, int(y2 + py))
    if x2 <= x1 or y2 <= y1:
        return np.empty((0, 0, 3), dtype=image.dtype)
    return image[y1:y2, x1:x2]
