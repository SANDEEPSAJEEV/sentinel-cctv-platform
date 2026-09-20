"""Checks for the parts of the ANPR pipeline that do not need model weights.

    python -m anpr.tests

Deliberately plain asserts and no test framework: this runs anywhere, including
on a locked-down machine with no pytest.
"""

from __future__ import annotations

import numpy as np

from anpr.plates import PlateRead, classify, clean, is_valid, repair, vote
from anpr.tracker import ByteTrackLite, Detection, iou_matrix


def check(label: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{label} failed {detail}")
    print(f"  ok  {label}")


def test_plate_formats() -> None:
    print("plate formats")
    check("standard plate valid", is_valid("GJ01AB1234"))
    check("short series valid", is_valid("GJ1A1234"))
    check("three-letter series valid", is_valid("DL1CAA1234"))
    check("BH series valid", is_valid("22BH1234AA"))
    check("unknown state rejected", not is_valid("ZZ01AB1234"))
    check("gibberish rejected", not is_valid("ABCDEFGH"))
    check("cleaning strips separators", clean("gj-01 ab 1234") == "GJ01AB1234")
    check("format named", classify("GJ01AB1234").name == "standard")


def test_repair() -> None:
    print("OCR confusion repair")
    check("O to zero in digit slot", repair("GJO1AB1234") == "GJ01AB1234")
    check("S to five in digit slot", repair("GJ01AB123S") == "GJ01AB1235")
    check("zero to O in letter slot", repair("GJ010B1234") == "GJ01OB1234",
          repair("GJ010B1234"))
    check("valid plate untouched", repair("GJ01AB1234") == "GJ01AB1234")
    check("hopeless read left alone", repair("XQ##") == "XQ")


def test_vote() -> None:
    print("multi-frame voting")
    # Ten reads of one plate, each wrong in a different place — the situation
    # CLAUDE.md §5 describes, where no single frame is right.
    noisy = ["GJ01AB1234", "GJ01AB1Z34", "GJ01A81234", "GJ0IAB1234", "GJ01AB1284",
             "GJ01AB1234", "6J01AB1234", "GJ01AB1234", "GJ01AB1234", "GJ01AB1734"]
    reads = [PlateRead(text=t, confidence=0.5, pts_ms=i * 100.0, plate_width_px=70)
             for i, t in enumerate(noisy)]
    result = vote(reads)
    check("voted through the noise", result.plate == "GJ01AB1234", result.plate)
    check("format valid", result.valid_format)
    check("counted every read", result.reads == 10)
    check("usable", result.is_usable)

    single = vote([PlateRead(text="GJ01AB1234", confidence=0.9, pts_ms=0.0)])
    check("single read is not corroborated", not single.is_usable)

    low = vote([PlateRead(text="GJ0", confidence=0.2, pts_ms=0.0),
                PlateRead(text="GJ0", confidence=0.2, pts_ms=50.0)])
    check("invalid format flagged", not low.valid_format)
    check("no reads gives nothing", vote([]) is None)


def test_iou() -> None:
    print("geometry")
    a = np.array([[0, 0, 10, 10]], dtype=float)
    b = np.array([[0, 0, 10, 10], [5, 5, 15, 15], [20, 20, 30, 30]], dtype=float)
    ious = iou_matrix(a, b)
    check("identical boxes", abs(ious[0, 0] - 1.0) < 1e-6)
    check("quarter overlap", abs(ious[0, 1] - (25 / 175)) < 1e-6, str(ious[0, 1]))
    check("disjoint boxes", ious[0, 2] == 0.0)


def test_tracker_constant_velocity() -> None:
    print("tracker: steady motion")
    tr = ByteTrackLite(confirm_hits=2)
    ids = []
    for i in range(10):
        pts = i * 100.0                      # 10 fps
        det = Detection(xyxy=(100 + 20 * i, 200, 160 + 20 * i, 260), confidence=0.9, label="car")
        live = tr.update([det], pts_ms=pts)
        ids += [t.track_id for t in live]
    check("one identity throughout", len(set(ids)) == 1, str(set(ids)))
    track = tr.tracks[0]
    # 20 px per 100 ms = 200 px/s.
    check("velocity in px/s", abs(track.speed_px_s - 200) < 60, f"{track.speed_px_s:.0f}")


def test_tracker_survives_a_stall() -> None:
    print("tracker: feed stalls for 2 s")
    # The grid stalls: measured gaps up to 2040 ms. A frame-counting tracker
    # treats the resumed frame as one step and computes an impossible velocity.
    steady = ByteTrackLite(confirm_hits=2)
    for i in range(5):
        steady.update([Detection((100 + 20 * i, 200, 160 + 20 * i, 260), 0.9, "car")],
                      pts_ms=i * 100.0)
    before = steady.tracks[0].track_id

    # 2 s gap; the vehicle has moved 400 px, consistent with 200 px/s.
    live = steady.update([Detection((580, 200, 640, 260), 0.9, "car")], pts_ms=2400.0)
    check("identity held across the stall", any(t.track_id == before for t in live),
          f"ids now {[t.track_id for t in live]}")
    check("velocity still sane", steady.tracks[0].speed_px_s < 400,
          f"{steady.tracks[0].speed_px_s:.0f} px/s")


def test_tracker_low_confidence_recovery() -> None:
    print("tracker: second-stage association")
    tr = ByteTrackLite(confirm_hits=2)
    for i in range(4):
        tr.update([Detection((100 + 20 * i, 200, 160 + 20 * i, 260), 0.9, "car")],
                  pts_ms=i * 100.0)
    tid = tr.tracks[0].track_id
    # Motion blur: the detector drops to 0.2 confidence. ByteTrack's whole point
    # is that this still continues the track instead of starting a new one.
    tr.update([Detection((180, 200, 240, 260), 0.2, "car")], pts_ms=400.0)
    check("low-confidence detection continued the track",
          len(tr.tracks) == 1 and tr.tracks[0].track_id == tid,
          f"{len(tr.tracks)} track(s)")


def test_tracker_resets_on_loop() -> None:
    print("tracker: loop discontinuity")
    tr = ByteTrackLite(confirm_hits=1)
    tr.update([Detection((100, 200, 160, 260), 0.9, "car")], pts_ms=1000.0)
    first = tr.tracks[0].track_id
    live = tr.update([Detection((100, 200, 160, 260), 0.9, "car")], pts_ms=10.0, epoch=1)
    check("state dropped at the cut", all(t.track_id != first for t in tr.tracks),
          "a track survived a scene change")
    check("new epoch recorded", tr.tracks[0].epoch == 1)


def main() -> None:
    for fn in (test_plate_formats, test_repair, test_vote, test_iou,
               test_tracker_constant_velocity, test_tracker_survives_a_stall,
               test_tracker_low_confidence_recovery, test_tracker_resets_on_loop):
        fn()
    print("\nall checks passed")


if __name__ == "__main__":
    main()
