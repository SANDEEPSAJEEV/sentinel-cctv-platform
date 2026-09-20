"""Frame integrity gate: is this frame actually a picture of the scene?

A decoder that joins an H.265 stream mid-GOP, or loses packets, keeps emitting
frames. OpenCV reports them as successful reads and they look like this:
large flat grey smears where motion-compensated blocks referenced pictures that
never arrived. Measured on a 60 s recording of the organiser's cam06: clean
frames had mean saturation 26-44 and 23-42% flat blocks; once sync was lost the
same camera produced saturation 6-10 and 74-97% flat blocks, and never
recovered for the remaining 55 seconds.

Feeding those frames to a detector produces confident nonsense — on that clip
RF-DETR reported "potted plant", "airplane" and "boat" on an empty road — and
feeding them to the capability profiler understates what a camera can resolve.
Neither failure announces itself, which is why this gate exists.

The test is relative, not absolute: a night scene is legitimately grey, so a
fixed saturation threshold would reject it. Each camera's own recent history is
the baseline, and a frame is rejected when it departs sharply from it.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

import cv2
import numpy as np

BLOCK = 16
FLAT_VARIANCE = 8.0          # below this, a 16x16 block carries no detail


@dataclass
class FrameMetrics:
    saturation: float
    flat_fraction: float
    laplacian: float

    def as_dict(self) -> dict[str, float]:
        return {"saturation": round(self.saturation, 1),
                "flat_fraction": round(self.flat_fraction, 3),
                "laplacian": round(self.laplacian, 1)}


def measure(image: np.ndarray) -> FrameMetrics:
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    h, w = grey.shape
    bh, bw = h // BLOCK, w // BLOCK
    if bh and bw:
        blocks = (grey[:bh * BLOCK, :bw * BLOCK]
                  .reshape(bh, BLOCK, bw, BLOCK).swapaxes(1, 2)
                  .reshape(-1, BLOCK * BLOCK).astype(np.float32))
        flat = float((blocks.var(axis=1) < FLAT_VARIANCE).mean())
    else:
        flat = 0.0
    sat = float(cv2.cvtColor(image, cv2.COLOR_BGR2HSV)[:, :, 1].mean())
    return FrameMetrics(saturation=sat, flat_fraction=flat,
                        laplacian=float(cv2.Laplacian(grey, cv2.CV_64F).var()))


@dataclass
class FrameGate:
    """Rolling per-camera baseline. Accepts the first `bootstrap` frames to
    learn what this camera normally looks like, then rejects departures."""

    bootstrap: int = 8
    window: int = 60
    saturation_drop: float = 0.5     # fraction of the baseline
    severe_drop: float = 0.35        # this far below baseline is corrupt on its own
    flat_rise: float = 0.2           # absolute rise in flat-block fraction
    # Absolute floor, used before a baseline exists: a frame this colourless
    # AND this featureless is not a picture of anything. It would also reject a
    # pitch-black night frame, which carries no usable detail either.
    floor_saturation: float = 10.0
    floor_flat: float = 0.55

    _sat: list[float] = field(default_factory=list)
    _flat: list[float] = field(default_factory=list)
    accepted: int = 0
    rejected: int = 0
    corrupt_streak: int = 0
    longest_streak: int = 0

    def check(self, image: np.ndarray) -> tuple[bool, FrameMetrics]:
        m = measure(image)
        if m.saturation < self.floor_saturation and m.flat_fraction > self.floor_flat:
            return self._reject(m)

        if len(self._sat) < self.bootstrap:
            self._remember(m)
            self.accepted += 1
            self.corrupt_streak = 0
            return True, m

        base_sat = statistics.median(self._sat)
        base_flat = statistics.median(self._flat)
        washed_out = m.saturation < base_sat * self.saturation_drop
        smeared = m.flat_fraction > base_flat + self.flat_rise
        # Both, not either: a genuinely dull scene loses colour without
        # gaining flat blocks, and a plain wall gains flat blocks without
        # losing colour. Corruption does both at once — except when it washes
        # the frame out completely, which severe_drop catches on its own.
        if m.saturation < base_sat * self.severe_drop or (washed_out and smeared):
            return self._reject(m)

        self._remember(m)
        self.accepted += 1
        self.corrupt_streak = 0
        return True, m

    def reset(self) -> None:
        """Forget the baseline. Call after rejoining a stream: the frames just
        after a join are often corrupt, and learning the baseline from those
        teaches the gate that mush is normal."""
        self._sat.clear()
        self._flat.clear()
        self.corrupt_streak = 0

    def _reject(self, m: FrameMetrics) -> tuple[bool, FrameMetrics]:
        self.rejected += 1
        self.corrupt_streak += 1
        self.longest_streak = max(self.longest_streak, self.corrupt_streak)
        return False, m

    def _remember(self, m: FrameMetrics) -> None:
        self._sat.append(m.saturation)
        self._flat.append(m.flat_fraction)
        if len(self._sat) > self.window:
            self._sat.pop(0)
            self._flat.pop(0)

    @property
    def corrupt_fraction(self) -> float:
        total = self.accepted + self.rejected
        return self.rejected / total if total else 0.0

    def stats(self) -> dict[str, float | int]:
        return {"frames_accepted": self.accepted,
                "frames_rejected_corrupt": self.rejected,
                "corrupt_fraction": round(self.corrupt_fraction, 3),
                "longest_corrupt_streak": self.longest_streak}
