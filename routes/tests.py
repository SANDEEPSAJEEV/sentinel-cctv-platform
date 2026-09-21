"""Checks for route reconstruction that need no database.

    python -m routes.tests
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from anpr.tests import check
from routes.matching import candidates, distance, match
from routes.reconstruct import (RoadDistance, Sighting, build_legs, fold_loops,
                                haversine_km, split_passes)

T0 = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)

# Ahmedabad and Junagadh, ~300 km apart.
AHMEDABAD = (23.03, 72.56)
JUNAGADH = (21.51, 70.46)


def sighting(camera: str, minutes: float, *, lat: float | None = None, lon: float | None = None,
             plate: str = "GJ01AB1234", usable: bool = True, epoch: int = 0,
             kind: str = "exact", score: float = 1.0) -> Sighting:
    return Sighting(
        read_id=0, plate_read=plate, camera_code=camera, camera_name=camera,
        district="test", lat=lat, lon=lon, location_confidence="surveyed",
        capability_grade="RECOGNISE", observed_at=T0 + timedelta(minutes=minutes),
        pts_ms=0.0, epoch=epoch, confidence=0.9, reads=4, agreeing_reads=3,
        valid_format=True, usable=usable, plate_width_px=90.0, vehicle_class="car",
        match_kind=kind, match_score=score)


def test_distance() -> None:
    print("confusion-aware distance")
    check("identical plates", distance("GJ01AB1234", "GJ01AB1234") == 0)
    check("O/0 is cheap", distance("GJ01AB1234", "GJO1AB1234") == 0.25)
    check("ordinary error costs more",
          distance("GJ01AB1234", "GJ01AB1294") == 1.0)
    check("four confusions still under two",
          distance("GJ01AB1258", "GJO1AB125B") == 0.5, str(distance("GJ01AB1258", "GJO1AB125B")))


def test_match() -> None:
    print("matching")
    check("exact matches", match("GJ01AB1234", "GJ01AB1234").kind == "exact")
    check("repairable read counts as exact",
          match("GJ01AB1234", "GJO1AB1234").kind == "exact")
    check("one real error is fuzzy",
          match("GJ01AB1234", "GJ01AB1294").kind == "fuzzy")
    check("different plate rejected",
          match("GJ01AB1234", "MH12XY9999").kind == "rejected")
    check("empty read rejected", match("GJ01AB1234", "").kind == "rejected")
    check("candidates include the query", "GJ01AB1234" in candidates("GJ01AB1234"))


def test_geometry() -> None:
    print("geometry")
    km = haversine_km(*AHMEDABAD, *JUNAGADH)
    check("Ahmedabad to Junagadh is ~250-320 km", 250 < km < 320, f"{km:.0f} km")


def test_passes_and_folding() -> None:
    print("loop folding")
    # The same three-camera journey, three times — what a looping sandbox does.
    one_pass = [("cam01", 0), ("cam02", 5), ("cam03", 9)]
    sightings = []
    for loop in range(3):
        for cam, minute in one_pass:
            sightings.append(sighting(cam, minute + loop * 120))
    passes = split_passes(sightings)
    check("three passes found", len(passes) == 3, f"{len(passes)}")

    folded, repeats, notes = fold_loops(passes)
    check("folded to one journey", len(folded) == 3, f"{len(folded)} sightings")
    check("two repeats recorded", repeats == 2, str(repeats))
    check("explained in a note", any("loop artifact" in n for n in notes))

    # A genuinely different journey must not be folded away.
    other = [sighting("cam09", 400), sighting("cam11", 405)]
    folded2, repeats2, _ = fold_loops(split_passes(sightings + other))
    check("distinct journey not counted as a repeat", repeats2 == 2, str(repeats2))


def test_legs_and_flags() -> None:
    print("legs and plausibility")
    a = sighting("cam01", 0, lat=AHMEDABAD[0], lon=AHMEDABAD[1])
    b = sighting("cam06", 20, lat=JUNAGADH[0], lon=JUNAGADH[1])
    legs = build_legs([a, b], RoadDistance(osrm_url=None))
    check("one leg built", len(legs) == 1)
    check("impossible speed flagged", "impossible_speed" in legs[0].flags,
          str(legs[0].implied_kmh))

    slow = build_legs([a, sighting("cam06", 60 * 5, lat=JUNAGADH[0], lon=JUNAGADH[1])],
                      RoadDistance(osrm_url=None))
    check("a plausible leg is not flagged", "impossible_speed" not in slow[0].flags,
          str(slow[0].implied_kmh))

    across_loop = build_legs(
        [a, sighting("cam06", 20, lat=JUNAGADH[0], lon=JUNAGADH[1], epoch=1)],
        RoadDistance(osrm_url=None))
    check("loop boundary is not an impossible journey",
          "crosses_loop_boundary" in across_loop[0].flags
          and "impossible_speed" not in across_loop[0].flags)

    unlocated = build_legs([a, sighting("cam23", 30)], RoadDistance(osrm_url=None))
    check("missing location handled", "distance_unknown" in unlocated[0].flags)
    check("no distance invented", unlocated[0].km is None)


def main() -> None:
    for fn in (test_distance, test_match, test_geometry, test_passes_and_folding,
               test_legs_and_flags):
        fn()
    print("\nall route checks passed")


if __name__ == "__main__":
    main()
