"""Continuous watchlist matching and alert raising.

Every plate read is checked against the active watchlist and, when it matches,
an alert is raised with a priority an operator can act on and a sentence saying
how that priority was arrived at.

Three decisions shape this, and each is a rights decision as much as a
technical one.

**A fuzzy match is a lead, never a confirmation.** Reads off this grid differ
from the true plate in exactly the characters OCR confuses, so fuzzy matching
is necessary or the system misses real hits. But a fuzzy hit on a stolen-
vehicle circulation means a citizen with a similar plate gets stopped. Those
alerts are raised at reduced priority, flagged needs_verification, and the UI
says so in words rather than a subtle colour.

**Alerts deduplicate.** A vehicle waiting at a signal is read repeatedly, and
the sandbox's looping footage replays the same pass forever. Without
suppression the queue fills with one car. Repeat sightings at the same camera
within a window raise hit_count on the open alert instead.

**Watchlist entries expire.** Matching ignores anything outside valid_from..
valid_until, so a circulation that was never withdrawn stops generating stops.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from registry import db
from routes.matching import candidates, match

log = logging.getLogger("watchlist.engine")

# Repeat sightings of the same vehicle on the same camera inside this window
# are the same event, and raise hit_count instead of a new alert.
#
# Ten minutes covers a vehicle waiting at a signal. It does NOT cover the
# sandbox's looping footage, which replays the same pass hours later and is not
# a second journey — set SENTINEL_DEDUPE_MINUTES above the loop period when
# demonstrating against the sandbox, or the queue fills with one car.
DEDUPE_MINUTES = float(os.environ.get("SENTINEL_DEDUPE_MINUTES", "10"))

SEVERITY_BASE = {"critical": 80, "high": 60, "medium": 40, "low": 20}
STRONG_GRADES = {"IDENTIFY", "RECOGNISE"}


@dataclass
class AlertRaised:
    alert_id: int
    plate_wanted: str
    plate_read: str
    camera_code: str
    severity: str
    priority: int
    match_kind: str
    needs_verification: bool
    deduplicated: bool
    seen_at: datetime

    def as_dict(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d["seen_at"] = self.seen_at.isoformat()
        return d


def score_priority(*, severity: str, match_kind: str, usable: bool,
                   capability_grade: str | None, plate_width_px: float | None
                   ) -> tuple[int, str]:
    """Priority 1-100, with the reasoning that produced it.

    An operator should never have to guess why one alert sits above another,
    and a supervisor should be able to argue with the weighting.
    """
    score = SEVERITY_BASE.get(severity, 40)
    why = [f"{severity} severity ({score})"]

    if match_kind == "exact":
        score += 10
        why.append("exact plate match (+10)")
    else:
        score -= 15
        why.append("fuzzy match, needs verification (-15)")

    if usable:
        score += 5
        why.append("corroborated across frames (+5)")
    else:
        score -= 10
        why.append("single uncorroborated read (-10)")

    if capability_grade in STRONG_GRADES:
        score += 5
        why.append(f"{capability_grade}-grade camera (+5)")
    elif capability_grade:
        score -= 5
        why.append(f"{capability_grade}-grade camera (-5)")

    if plate_width_px and plate_width_px < 40:
        score -= 5
        why.append(f"plate only {plate_width_px:.0f} px wide (-5)")

    score = max(1, min(100, score))
    return score, "; ".join(why)


def active_watchlist(at: datetime | None = None) -> list[dict[str, Any]]:
    return db.query(
        """SELECT id, plate, category, severity, reason, source_system, source_ref
           FROM watchlist
           WHERE active
             AND valid_from <= %(when)s::date
             AND (valid_until IS NULL OR valid_until >= %(when)s::date)""",
        {"when": at or datetime.now(timezone.utc)},
    )


UNCHECKED_READS_SQL = """
    SELECT r.id, r.plate, r.observed_at, r.usable, r.plate_width_px, r.camera_id,
           c.code AS camera_code, cc.capability_grade
    FROM plate_read r
    JOIN camera c ON c.id = r.camera_id
    LEFT JOIN camera_current cc ON cc.id = c.id
    WHERE r.id > %(after_id)s
    ORDER BY r.id
    LIMIT %(limit)s
"""


def scan(after_id: int = 0, *, limit: int = 500, tolerance: float = 1.0,
         min_confidence_for_fuzzy: float = 0.0) -> tuple[list[AlertRaised], int]:
    """Check unseen plate reads against the watchlist. Returns (alerts, last id)."""
    entries = active_watchlist()
    reads = db.query(UNCHECKED_READS_SQL, {"after_id": after_id, "limit": limit})
    if not reads:
        return [], after_id
    if not entries:
        return [], max(r["id"] for r in reads)

    raised: list[AlertRaised] = []
    last_id = after_id
    for read in reads:
        last_id = max(last_id, read["id"])
        for entry in entries:
            m = match(entry["plate"], read["plate"], tolerance=tolerance)
            if m.kind == "rejected":
                continue
            if m.kind == "fuzzy" and m.score < min_confidence_for_fuzzy:
                continue
            alert = _raise(entry, read, m.kind, m.score)
            if alert:
                raised.append(alert)
    return raised, last_id


def _raise(entry: dict[str, Any], read: dict[str, Any], match_kind: str,
           score: float) -> AlertRaised | None:
    priority, reason = score_priority(
        severity=entry["severity"], match_kind=match_kind,
        usable=bool(read["usable"]), capability_grade=read["capability_grade"],
        plate_width_px=float(read["plate_width_px"]) if read["plate_width_px"] else None,
    )
    seen_at = read["observed_at"]
    window_start = seen_at - timedelta(minutes=DEDUPE_MINUTES)

    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT id FROM alert
               WHERE watchlist_id = %s AND camera_id = %s AND status IN ('new', 'acknowledged')
                 AND last_seen_at >= %s
               ORDER BY last_seen_at DESC LIMIT 1""",
            (entry["id"], read["camera_id"], window_start),
        )
        existing = cur.fetchone()
        if existing:
            cur.execute(
                """UPDATE alert
                   SET hit_count = hit_count + 1,
                       last_seen_at = GREATEST(last_seen_at, %s),
                       plate_read_id = %s,
                       priority = GREATEST(priority, %s)
                   WHERE id = %s""",
                (seen_at, read["id"], priority, existing["id"]),
            )
            conn.commit()
            return AlertRaised(existing["id"], entry["plate"], read["plate"],
                               read["camera_code"], entry["severity"], priority,
                               match_kind, match_kind == "fuzzy", True, seen_at)

        cur.execute(
            """INSERT INTO alert (watchlist_id, plate_read_id, camera_id, plate_wanted,
                                  plate_read, match_kind, match_score, severity, priority,
                                  priority_reason, needs_verification, first_seen_at,
                                  last_seen_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (entry["id"], read["id"], read["camera_id"], entry["plate"], read["plate"],
             match_kind, score, entry["severity"], priority, reason,
             match_kind == "fuzzy", seen_at, seen_at),
        )
        alert_id = cur.fetchone()["id"]
        conn.commit()

    db.audit("alert_raised", actor="watchlist.engine", object_type="alert",
             object_id=str(alert_id), purpose_ref=entry.get("source_ref"),
             detail={"plate_wanted": entry["plate"], "plate_read": read["plate"],
                     "match": match_kind, "priority": priority})
    return AlertRaised(alert_id, entry["plate"], read["plate"], read["camera_code"],
                       entry["severity"], priority, match_kind,
                       match_kind == "fuzzy", False, seen_at)


def open_alerts(limit: int = 100, include_closed: bool = False) -> list[dict[str, Any]]:
    return db.query(
        f"""SELECT a.*, w.category, w.reason, w.source_system, w.source_ref,
                   c.code AS camera_code, c.name AS camera_name, c.district
            FROM alert a
            JOIN watchlist w ON w.id = a.watchlist_id
            LEFT JOIN camera c ON c.id = a.camera_id
            {'' if include_closed else "WHERE a.status IN ('new', 'acknowledged')"}
            ORDER BY a.priority DESC, a.last_seen_at DESC
            LIMIT %s""",
        (min(limit, 500),),
    )


def set_status(alert_id: int, status: str, *, by: str | None = None,
               note: str | None = None) -> bool:
    if status not in ("new", "acknowledged", "dismissed", "escalated"):
        raise ValueError(f"unknown status {status!r}")
    n = db.execute(
        """UPDATE alert SET status = %s, acknowledged_at = now(),
                            acknowledged_by = %s, note = coalesce(%s, note)
           WHERE id = %s""",
        (status, by, note, alert_id),
    )
    if n:
        db.audit("alert_status", actor=by or "api", object_type="alert",
                 object_id=str(alert_id), detail={"status": status, "note": note})
    return bool(n)
