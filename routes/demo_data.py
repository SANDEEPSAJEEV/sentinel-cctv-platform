"""Insert a synthetic journey, for exercising route reconstruction.

    python -m routes.demo_data --insert
    python -m routes.demo_data --purge

The real grid has given us plate reads from one camera so far, because most of
its cameras cannot resolve a plate. That is the finding, not a gap to fill with
invented data — so everything written here is tagged `source='synthetic-demo'`,
uses a plate in a series reserved for the demo, and can be removed in one
command. Route reconstruction reports the source of every sighting, so a
synthetic one is never mistaken for evidence.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from registry import db

SOURCE = "synthetic-demo"
PLATE = "GJ01DM4242"

# A plausible run through the Ahmedabad cluster, then the same run again two
# hours later — which is what the sandbox's looping footage produces.
JOURNEY = [
    ("cam05", 0, "GJ01DM4242", 118.0, True),    # Visat teen rasta
    ("cam16", 4, "GJ01DM4242", 96.0, True),     # Visat P2, same junction
    ("cam12", 17, "GJ0IDM4242", 88.0, True),    # Adalaj toll — OCR reads I for 1
    ("cam01", 39, "GJ01DM4242", 72.0, True),    # Chimanbhai bridge
    ("cam13", 52, "GJ01DM424", 51.0, False),    # CN Vidyalaya — last char lost
]
LOOP_OFFSET_MINUTES = 120


def insert() -> int:
    start = datetime.now(timezone.utc) - timedelta(hours=6)
    written = 0
    with db.connect() as conn, conn.cursor() as cur:
        for loop in range(2):
            for cam_ref, minute, read, width, usable in JOURNEY:
                cur.execute("SELECT id FROM camera WHERE external_ref = %s", (cam_ref,))
                row = cur.fetchone()
                if row is None:
                    raise SystemExit(f"camera {cam_ref} is not in the registry — seed it first")
                at = start + timedelta(minutes=minute + loop * LOOP_OFFSET_MINUTES)
                cur.execute(
                    """INSERT INTO vehicle_track
                           (camera_id, track_key, epoch, vehicle_class, frames,
                            first_seen_at, last_seen_at)
                       VALUES (%s, %s, %s, 'car', 12, %s, %s)
                       ON CONFLICT (camera_id, track_key) DO UPDATE SET frames = 12
                       RETURNING id""",
                    (row["id"], f"{SOURCE}:{PLATE}:{loop}:{cam_ref}", loop, at, at),
                )
                track_id = cur.fetchone()["id"]
                cur.execute(
                    """INSERT INTO plate_read
                           (camera_id, track_id, plate, confidence, reads, agreeing_reads,
                            valid_format, repaired, usable, vehicle_class, plate_width_px,
                            char_height_px, epoch, pts_ms, observed_at, model, source)
                       VALUES (%s, %s, %s, %s, %s, %s, true, false, %s, 'car', %s, %s, %s,
                               %s, %s, 'synthetic', %s)""",
                    (row["id"], track_id, read, 0.86 if usable else 0.55,
                     9 if usable else 2, 7 if usable else 1, usable, width,
                     round(width * 0.22, 1), loop, minute * 1000.0, at, SOURCE),
                )
                written += 1
        conn.commit()
    db.audit("insert_demo_data", actor="routes.demo_data", detail={"rows": written})
    return written


def purge() -> int:
    n = db.execute("DELETE FROM plate_read WHERE source = %s", (SOURCE,))
    db.execute("DELETE FROM vehicle_track WHERE track_key LIKE %s", (f"{SOURCE}:%",))
    db.audit("purge_demo_data", actor="routes.demo_data", detail={"rows": n})
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description="Synthetic journey for route testing")
    ap.add_argument("--insert", action="store_true")
    ap.add_argument("--purge", action="store_true")
    args = ap.parse_args()
    if args.purge:
        print(f"removed {purge()} synthetic plate read(s)")
    if args.insert:
        print(f"inserted {insert()} synthetic plate read(s) for {PLATE} "
              f"(tagged source={SOURCE})")
    if not (args.insert or args.purge):
        ap.error("give --insert or --purge")


if __name__ == "__main__":
    main()
