"""Route reconstruction: a registration in, a timestamped route out.

This is the graded test case (CLAUDE.md §2): given a vehicle registration,
produce its complete timestamped, location-wise route across the integrated
cameras. Everything else in the platform exists to make this answerable.

Four things this does that a naive "SELECT ... ORDER BY time" does not.

1. **Matches fuzzily, and says so.** Reads off this grid are noisy in exactly
   the characters OCR confuses. An exact-match query reports "not seen" for a
   vehicle that was seen. Every sighting carries its match kind and score.

2. **Folds the loop.** The sandbox replays recorded footage on a cycle, so the
   same vehicle makes the same journey again every loop. Reported literally,
   the route says a car drove Junagadh to Rajkot eleven times overnight. Passes
   with the same camera sequence are folded into one canonical route, with the
   repeat count kept as a loop artifact rather than deleted.

3. **Checks whether the route is physically possible.** Implied speed between
   consecutive sightings is computed from real distance and real time. An
   impossible leg is a finding — a cloned plate, a misread, or a camera whose
   location is wrong — not something to smooth over.

4. **Carries its own evidence.** Each sighting reports the camera's capability
   grade, the plate width in pixels, how many frames voted, and whether the
   read was corroborated. A route built from single unconfirmed reads on
   DETECT-grade cameras should look weak, because it is.

Distances are straight-line by default. A self-hosted OSRM Match service gives
road distances instead when OSRM_URL is set; the interface is the same and the
plausibility checks simply get sharper.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

from registry import db
from routes.matching import Match, candidates, match

# A vehicle that appears to have exceeded this between two cameras did not.
IMPOSSIBLE_KMH = 150.0
# Two sightings this far apart in time are different journeys, not one.
PASS_GAP_MINUTES = 30.0
# Sightings closer together than this at the same camera are one pass.
SAME_PASS_SECONDS = 120.0


@dataclass
class Sighting:
    read_id: int
    plate_read: str
    camera_code: str
    camera_name: str
    district: str | None
    lat: float | None
    lon: float | None
    location_confidence: str
    capability_grade: str | None
    observed_at: datetime
    pts_ms: float | None
    epoch: int
    confidence: float
    reads: int
    agreeing_reads: int
    valid_format: bool
    usable: bool
    plate_width_px: float | None
    vehicle_class: str | None
    match_kind: str = "exact"
    match_score: float = 1.0

    @property
    def located(self) -> bool:
        return self.lat is not None and self.lon is not None

    def as_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["observed_at"] = self.observed_at.isoformat()
        return d


@dataclass
class Leg:
    from_camera: str
    to_camera: str
    departed_at: datetime
    arrived_at: datetime
    seconds: float
    km: float | None
    implied_kmh: float | None
    distance_basis: str
    flags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["departed_at"] = self.departed_at.isoformat()
        d["arrived_at"] = self.arrived_at.isoformat()
        return d


@dataclass
class Route:
    plate: str
    sightings: list[Sighting]
    legs: list[Leg]
    loop_repeats: int
    passes_folded: int
    window_start: datetime | None
    window_end: datetime | None
    notes: list[str] = field(default_factory=list)

    @property
    def cameras(self) -> list[str]:
        return [s.camera_code for s in self.sightings]

    def as_dict(self) -> dict[str, Any]:
        return {
            "plate": self.plate,
            "sightings": [s.as_dict() for s in self.sightings],
            "legs": [l.as_dict() for l in self.legs],
            "summary": self.summary(),
            "notes": self.notes,
        }

    def summary(self) -> dict[str, Any]:
        located = [s for s in self.sightings if s.located]
        corroborated = [s for s in self.sightings if s.usable]
        return {
            "sightings": len(self.sightings),
            "cameras": len(set(self.cameras)),
            "corroborated_sightings": len(corroborated),
            "fuzzy_matches": sum(1 for s in self.sightings if s.match_kind == "fuzzy"),
            "unlocated_sightings": len(self.sightings) - len(located),
            "first_seen": self.sightings[0].observed_at.isoformat() if self.sightings else None,
            "last_seen": self.sightings[-1].observed_at.isoformat() if self.sightings else None,
            "distance_km": round(sum(l.km for l in self.legs if l.km), 2) if self.legs else 0.0,
            "loop_repeats_folded": self.loop_repeats,
            "passes_seen": self.passes_folded,
            "flags": sorted({f for l in self.legs for f in l.flags}),
            "evidence_strength": self.evidence_strength(),
        }

    def evidence_strength(self) -> str:
        """How much weight this route can carry. Deliberately blunt."""
        if not self.sightings:
            return "none"
        corroborated = sum(1 for s in self.sightings if s.usable)
        if corroborated == 0:
            return "weak — no sighting was corroborated across frames"
        if corroborated < len(self.sightings) / 2:
            return "mixed — fewer than half the sightings were corroborated"
        if any(s.match_kind == "fuzzy" for s in self.sightings):
            return "good — but includes fuzzy plate matches"
        return "strong"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class RoadDistance:
    """Straight line, or OSRM road distance when one is configured.

    Straight line always understates road distance, so an implied speed
    computed from it is a lower bound — which keeps the impossible-journey
    check conservative: it will not invent a violation that isn't there.
    """

    def __init__(self, osrm_url: str | None = None) -> None:
        self.osrm_url = osrm_url or os.environ.get("OSRM_URL")

    def between(self, a: Sighting, b: Sighting) -> tuple[float | None, str]:
        if not (a.located and b.located):
            return None, "unknown — a camera has no confirmed location"
        straight = haversine_km(a.lat, a.lon, b.lat, b.lon)
        if not self.osrm_url:
            return straight, "straight line"
        try:
            import requests

            url = (f"{self.osrm_url.rstrip('/')}/route/v1/driving/"
                   f"{a.lon},{a.lat};{b.lon},{b.lat}?overview=false")
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            routes = resp.json().get("routes") or []
            if routes:
                return routes[0]["distance"] / 1000.0, "OSRM road distance"
        except Exception:
            pass
        return straight, "straight line (OSRM unavailable)"


# ------------------------------------------------------------------ querying
SIGHTING_SQL = """
    SELECT r.id AS read_id, r.plate, r.observed_at, r.pts_ms, r.epoch, r.confidence,
           r.reads, r.agreeing_reads, r.valid_format, r.usable, r.plate_width_px,
           r.vehicle_class,
           c.code AS camera_code, c.name AS camera_name, c.district,
           c.location_confidence,
           ST_Y(c.geom) AS lat, ST_X(c.geom) AS lon,
           cc.capability_grade
    FROM plate_read r
    JOIN camera c ON c.id = r.camera_id
    LEFT JOIN camera_current cc ON cc.id = c.id
    WHERE (%(since)s::timestamptz IS NULL OR r.observed_at >= %(since)s)
      AND (%(until)s::timestamptz IS NULL OR r.observed_at <= %(until)s)
      AND (r.plate = ANY(%(candidates)s) OR r.plate ILIKE %(prefix)s)
    ORDER BY r.observed_at
"""


def find_sightings(plate: str, *, since: datetime | None = None,
                   until: datetime | None = None, tolerance: float = 1.0,
                   include_uncorroborated: bool = True) -> list[Sighting]:
    """Every read that could be this vehicle, scored."""
    variants = candidates(plate)
    prefix = f"{plate[:4]}%" if len(plate) >= 4 else f"{plate}%"
    rows = db.query(SIGHTING_SQL, {"candidates": variants, "prefix": prefix,
                                   "since": since, "until": until})
    def num(value: Any) -> float | None:
        # psycopg returns NUMERIC as Decimal, which json cannot serialise and
        # arithmetic mixes badly with float.
        return None if value is None else float(value)

    out: list[Sighting] = []
    for row in rows:
        m: Match = match(plate, row["plate"], tolerance=tolerance)
        if m.kind == "rejected":
            continue
        if not include_uncorroborated and not row["usable"]:
            continue
        out.append(Sighting(
            read_id=row["read_id"], plate_read=row["plate"],
            camera_code=row["camera_code"], camera_name=row["camera_name"],
            district=row["district"], lat=num(row["lat"]), lon=num(row["lon"]),
            location_confidence=row["location_confidence"],
            capability_grade=row["capability_grade"],
            observed_at=row["observed_at"], pts_ms=num(row["pts_ms"]),
            epoch=row["epoch"] or 0, confidence=float(row["confidence"] or 0),
            reads=row["reads"] or 0, agreeing_reads=row["agreeing_reads"] or 0,
            valid_format=bool(row["valid_format"]), usable=bool(row["usable"]),
            plate_width_px=num(row["plate_width_px"]), vehicle_class=row["vehicle_class"],
            match_kind=m.kind, match_score=m.score,
        ))
    return out


# ------------------------------------------------------------- loop folding
def split_passes(sightings: list[Sighting],
                 gap_minutes: float = PASS_GAP_MINUTES) -> list[list[Sighting]]:
    """Cut the timeline into journeys wherever the vehicle goes unseen for a
    while, or wherever the camera sequence starts over."""
    passes: list[list[Sighting]] = []
    current: list[Sighting] = []
    for s in sightings:
        if not current:
            current = [s]
            continue
        gap = (s.observed_at - current[-1].observed_at).total_seconds() / 60.0
        revisit = (s.camera_code == current[0].camera_code
                   and (s.observed_at - current[0].observed_at).total_seconds() > SAME_PASS_SECONDS)
        if gap > gap_minutes or revisit:
            passes.append(current)
            current = [s]
        else:
            current.append(s)
    if current:
        passes.append(current)
    return passes


def fold_loops(passes: list[list[Sighting]]) -> tuple[list[Sighting], int, list[str]]:
    """Collapse passes that are the same journey seen again on the next loop.

    Two passes are the same journey when they visit the same cameras in the
    same order. The canonical pass kept is the one with the strongest evidence,
    not simply the first.
    """
    if len(passes) <= 1:
        return (passes[0] if passes else []), 0, []

    groups: dict[tuple[str, ...], list[list[Sighting]]] = {}
    for p in passes:
        groups.setdefault(tuple(s.camera_code for s in p), []).append(p)

    notes: list[str] = []
    repeats = 0
    best_group: list[Sighting] = []
    best_score = -1.0
    for signature, members in groups.items():
        if len(members) > 1:
            repeats += len(members) - 1
            notes.append(
                f"{len(members)} passes over {' -> '.join(signature)} were identical; "
                "folded to one. The sandbox replays recorded footage on a loop, so a "
                "repeat is a loop artifact, not another journey.")
        for p in members:
            score = sum(s.match_score + (1.0 if s.usable else 0.0) for s in p)
            if score > best_score:
                best_score, best_group = score, p
    if len(groups) > 1:
        notes.append(f"{len(groups)} distinct camera sequences seen; reporting the "
                     "best-evidenced one. Query a narrower window to see the others.")
    return best_group, repeats, notes


# ------------------------------------------------------------------- legs
def build_legs(sightings: list[Sighting], distances: RoadDistance) -> list[Leg]:
    legs: list[Leg] = []
    for a, b in zip(sightings, sightings[1:]):
        seconds = (b.observed_at - a.observed_at).total_seconds()
        km, basis = distances.between(a, b)
        kmh = (km / (seconds / 3600.0)) if km is not None and seconds > 0 else None
        flags: list[str] = []
        if km is None:
            flags.append("distance_unknown")
        if kmh is not None and kmh > IMPOSSIBLE_KMH:
            # Same epoch matters: across a loop boundary the clock restarts and
            # any speed computed over it is meaningless.
            if a.epoch == b.epoch:
                flags.append("impossible_speed")
            else:
                flags.append("crosses_loop_boundary")
        if seconds <= 0 and a.camera_code != b.camera_code:
            flags.append("same_instant_two_cameras")
        if "approx" in (a.location_confidence or "") or "approx" in (b.location_confidence or ""):
            flags.append("approximate_camera_location")
        legs.append(Leg(from_camera=a.camera_code, to_camera=b.camera_code,
                        departed_at=a.observed_at, arrived_at=b.observed_at,
                        seconds=round(seconds, 1),
                        km=round(km, 2) if km is not None else None,
                        implied_kmh=round(kmh, 1) if kmh is not None else None,
                        distance_basis=basis, flags=flags))
    return legs


def reconstruct(plate: str, *, since: datetime | None = None, until: datetime | None = None,
                tolerance: float = 1.0, fold: bool = True,
                include_uncorroborated: bool = True,
                osrm_url: str | None = None, purpose_ref: str | None = None) -> Route:
    """The whole answer for one registration."""
    sightings = find_sightings(plate, since=since, until=until, tolerance=tolerance,
                               include_uncorroborated=include_uncorroborated)
    notes: list[str] = []
    repeats = 0
    passes = split_passes(sightings)
    if fold and sightings:
        sightings, repeats, notes = fold_loops(passes)

    distances = RoadDistance(osrm_url)
    legs = build_legs(sightings, distances)

    if any(s.match_kind == "fuzzy" for s in sightings):
        notes.append("Some sightings matched fuzzily: the read differs from the queried "
                     "plate only in characters OCR is known to confuse. Each one carries "
                     "its score — check the crops before acting on a fuzzy-only route.")
    if sightings and not any(s.usable for s in sightings):
        notes.append("No sighting was corroborated across frames. On this grid that "
                     "usually means the plate was too small to read reliably; treat the "
                     "route as a lead, not evidence.")
    unlocated = [s.camera_code for s in sightings if not s.located]
    if unlocated:
        notes.append(f"Cameras with no confirmed location on this route: "
                     f"{', '.join(sorted(set(unlocated)))}. Distances across them are unknown.")

    db.audit("route_query", actor="routes", object_type="plate", object_id=plate,
             purpose_ref=purpose_ref,
             detail={"sightings": len(sightings), "folded_repeats": repeats})

    return Route(plate=plate.upper(), sightings=sightings, legs=legs, loop_repeats=repeats,
                 passes_folded=len(passes), window_start=since, window_end=until, notes=notes)


def format_report(route: Route) -> str:
    """The plain-text route report — the submission's 'plates with timestamps'."""
    s = route.summary()
    lines = [
        f"ROUTE REPORT — {route.plate}",
        f"generated {datetime.now().astimezone().isoformat(timespec='seconds')}",
        "",
        f"  sightings        {s['sightings']} across {s['cameras']} camera(s)",
        f"  corroborated     {s['corroborated_sightings']} of {s['sightings']}",
        f"  fuzzy matches    {s['fuzzy_matches']}",
        f"  first seen       {s['first_seen'] or '—'}",
        f"  last seen        {s['last_seen'] or '—'}",
        f"  distance         {s['distance_km']} km",
        f"  loop repeats     {s['loop_repeats_folded']} folded (of {s['passes_seen']} passes)",
        f"  evidence         {s['evidence_strength']}",
        "",
        f"{'TIME':<26}{'CAMERA':<16}{'LOCATION':<26}{'READ':<12}{'MATCH':<8}{'PX':<6}EVIDENCE",
        "-" * 108,
    ]
    for sg in route.sightings:
        place = (sg.district or "—")[:24]
        evidence = f"{sg.reads} read(s)"
        if sg.usable:
            evidence += ", corroborated"
        if sg.capability_grade:
            evidence += f", {sg.capability_grade}"
        lines.append(
            f"{sg.observed_at.strftime('%Y-%m-%d %H:%M:%S %Z'):<26}{sg.camera_code:<16}"
            f"{place:<26}{sg.plate_read:<12}{sg.match_kind:<8}"
            f"{(f'{sg.plate_width_px:.0f}' if sg.plate_width_px else '—'):<6}{evidence}")
    if route.legs:
        lines += ["", f"{'LEG':<34}{'GAP':<11}{'DISTANCE':<14}{'IMPLIED':<12}FLAGS", "-" * 108]
        for leg in route.legs:
            gap = str(timedelta(seconds=int(leg.seconds)))
            dist = f"{leg.km} km" if leg.km is not None else "unknown"
            speed = f"{leg.implied_kmh} km/h" if leg.implied_kmh is not None else "—"
            lines.append(
                f"{leg.from_camera + ' -> ' + leg.to_camera:<34}{gap:<11}{dist:<14}"
                f"{speed:<12}{', '.join(leg.flags) or '—'}")
        bases = sorted({l.distance_basis for l in route.legs})
        lines.append(f"\n  distances: {'; '.join(bases)}")
    if route.notes:
        lines += ["", "NOTES"]
        lines += [f"  - {n}" for n in route.notes]
    return "\n".join(lines)
