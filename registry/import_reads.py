"""Import plate reads the worker wrote to JSONL into the registry database.

    python -m registry.import_reads out/grid_reads.jsonl

The worker always writes JSONL and writes to the database only when it is
reachable. This closes the gap: reads captured while the database was down
still land, keyed so that re-running the import does not duplicate them.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from typing import Any

from registry import db


def import_file(path: str) -> tuple[int, int]:
    written = skipped = 0
    with db.connect() as conn, conn.cursor() as cur, open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            e: dict[str, Any] = json.loads(line)
            cur.execute("SELECT id FROM camera WHERE external_ref = %s OR code = %s",
                        (e["camera"], e["camera"]))
            cam = cur.fetchone()
            if cam is None:
                skipped += 1
                continue
            observed = datetime.fromisoformat(e["observed_at"])
            cur.execute(
                """INSERT INTO vehicle_track
                       (camera_id, track_key, epoch, vehicle_class, frames,
                        first_pts_ms, last_pts_ms, first_seen_at, last_seen_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (camera_id, track_key) DO UPDATE
                       SET frames = EXCLUDED.frames
                   RETURNING id""",
                (cam["id"], e["track_key"], e.get("epoch", 0), e.get("vehicle_class"),
                 e.get("frames"), e.get("first_pts_ms"), e.get("last_pts_ms"),
                 observed, observed))
            track_id = cur.fetchone()["id"]

            # One voted read per track; re-importing the same file is a no-op.
            cur.execute("SELECT 1 FROM plate_read WHERE track_id = %s AND plate = %s",
                        (track_id, e["plate"]))
            if cur.fetchone():
                skipped += 1
                continue
            cur.execute(
                """INSERT INTO plate_read
                       (camera_id, track_id, plate, confidence, reads, agreeing_reads,
                        valid_format, repaired, usable, vehicle_class, plate_width_px,
                        char_height_px, epoch, pts_ms, observed_at, model, source)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                           'anpr.worker')""",
                (cam["id"], track_id, e["plate"], e.get("confidence"), e.get("reads"),
                 e.get("agreeing_reads"), e.get("valid_format"), e.get("repaired"),
                 e.get("usable"), e.get("vehicle_class"), e.get("plate_width_px"),
                 e.get("char_height_px"), e.get("epoch", 0), e.get("best_pts_ms"),
                 observed, e.get("model")))
            written += 1
        conn.commit()
    db.audit("import_reads", actor="registry.import_reads",
             detail={"file": path, "written": written, "skipped": skipped})
    return written, skipped


def main() -> None:
    ap = argparse.ArgumentParser(description="Import worker JSONL into the registry")
    ap.add_argument("path")
    args = ap.parse_args()
    written, skipped = import_file(args.path)
    print(f"{written} read(s) imported, {skipped} skipped (already present or "
          f"camera not in the registry)")


if __name__ == "__main__":
    main()
