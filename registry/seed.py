"""Seed the registry from the grid catalogue, and import capability reports.

    python -m registry.seed --catalogue                 # live catalogue (HTTP, no watch time)
    python -m registry.seed --catalogue-file out/cameras.json
    python -m registry.seed --capability capability_report.json

Both are idempotent: cameras upsert on their registry code, and each capability
report inserts one run per camera, keyed by the report's own timestamp.

The catalogue carries two fields, id and name. Everything else — location,
department, optics — is unknown at onboarding and is filled by the department
questionnaire. That is what registry/seed_places.json records, with an explicit
confidence per location, and it is why most cameras land UNASSIGNED.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from registry import db

log = logging.getLogger("registry.seed")

HERE = os.path.dirname(os.path.abspath(__file__))
PLACES_FILE = os.path.join(HERE, "seed_places.json")

DEPARTMENTS = [
    ("POLICE", "Gujarat Police", "government"),
    ("MUNICIPAL", "Municipal Corporation", "government"),
    ("GSRTC", "Gujarat State Road Transport Corporation", "government"),
    ("PANCHAYAT", "Panchayat (Rural Development)", "government"),
    ("HEALTH", "Health & Family Welfare", "government"),
    ("UNASSIGNED", "Unassigned — pending department questionnaire", "government"),
]


def ensure_departments() -> dict[str, int]:
    with db.connect() as conn, conn.cursor() as cur:
        for code, name, kind in DEPARTMENTS:
            cur.execute(
                """INSERT INTO department (code, name, kind) VALUES (%s, %s, %s)
                   ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name
                   RETURNING id""",
                (code, name, kind),
            )
        conn.commit()
        cur.execute("SELECT code, id FROM department")
        return {r["code"]: r["id"] for r in cur.fetchall()}


def _registry_code(external_ref: str, district: str | None) -> str:
    """GJ-<district-ish>-<ref>. Stable, readable, and independent of the
    source system's ids — the catalogue is a contract we do not control."""
    tag = (district or "UNK").upper().replace(" ", "")[:3]
    return f"GJ-{tag}-{external_ref.upper()}"


def seed_cameras(cameras: list[dict[str, Any]]) -> int:
    places = json.load(open(PLACES_FILE, encoding="utf-8"))
    by_ref: dict[str, Any] = places["places"]
    dept_overrides: dict[str, str] = {k: v for k, v in places["departments"].items()
                                      if not k.startswith("_")}
    depts = ensure_departments()

    written = 0
    with db.connect() as conn, conn.cursor() as cur:
        for cam in cameras:
            ref = str(cam.get("id") or "").strip()
            if not ref:
                continue
            place = by_ref.get(ref, {})
            dept_code = dept_overrides.get(ref, "UNASSIGNED")
            lat, lon = place.get("lat"), place.get("lon")
            cur.execute(
                """
                INSERT INTO camera (code, external_ref, name, department_id, geom,
                                    location_confidence, location_source, district,
                                    stream_path, owner_type, signal_type, status)
                VALUES (%(code)s, %(ref)s, %(name)s, %(dept)s,
                        CASE WHEN %(lon)s::double precision IS NULL THEN NULL
                             ELSE ST_SetSRID(ST_MakePoint(%(lon)s::double precision,
                                                          %(lat)s::double precision), 4326) END,
                        %(conf)s, %(src)s, %(district)s, %(ref)s, 'government', 'ip', 'unknown')
                ON CONFLICT (code) DO UPDATE SET
                    name = EXCLUDED.name,
                    department_id = EXCLUDED.department_id,
                    geom = EXCLUDED.geom,
                    location_confidence = EXCLUDED.location_confidence,
                    location_source = EXCLUDED.location_source,
                    district = EXCLUDED.district,
                    updated_at = now()
                """,
                {
                    "code": _registry_code(ref, place.get("district")),
                    "ref": ref,
                    "name": str(cam.get("name") or ref),
                    "dept": depts.get(dept_code, depts["UNASSIGNED"]),
                    "lat": lat, "lon": lon,
                    "conf": place.get("confidence", "unknown"),
                    "src": place.get("source"),
                    "district": place.get("district"),
                },
            )
            written += 1
        conn.commit()
    db.audit("seed_cameras", actor="seed.py", detail={"count": written})
    return written


CAPABILITY_FIELDS = (
    "resolution", "codec", "measured_fps", "declared_fps", "video_seconds_analysed",
    "frames_analysed", "gap_ms_p50", "gap_ms_max", "reconnects", "epochs_seen",
    "mean_luma", "lighting", "focus_laplacian_var", "plates_observed",
    "plate_width_px_median", "pixel_density_px_per_m", "char_height_px_est",
    "capability_grade", "capability_note", "anpr_capable", "error",
)


def import_capability(path: str) -> int:
    report = json.load(open(path, encoding="utf-8"))
    observed = report.get("generated_utc") or datetime.now(timezone.utc).isoformat()
    written = 0
    with db.connect() as conn, conn.cursor() as cur:
        for cam in report.get("cameras", []):
            ref = str(cam.get("id") or "")
            cur.execute("SELECT id FROM camera WHERE external_ref = %s", (ref,))
            row = cur.fetchone()
            if row is None:
                log.warning("capability report mentions %s, which is not in the registry", ref)
                continue
            values = {f: cam.get(f) for f in CAPABILITY_FIELDS}
            values.update(
                camera_id=row["id"],
                observed_at=observed,
                sample_seconds=report.get("sample_seconds"),
                reachable=bool(cam.get("reachable")),
                plates_static_rejected=cam.get("plate_detections_static_rejected"),
                source=f"profile_cameras.py · model={report.get('plate_model')}",
            )
            cols = ", ".join(values)
            cur.execute(
                f"INSERT INTO capability_run ({cols}) VALUES ({', '.join('%(' + c + ')s' for c in values)})",
                values,
            )
            # Status follows the most recent observation.
            cur.execute(
                "UPDATE camera SET status = %s, updated_at = now() WHERE id = %s",
                ("online" if cam.get("reachable") else "offline", row["id"]),
            )
            written += 1
        conn.commit()
    db.audit("import_capability", actor="seed.py",
             detail={"file": os.path.basename(path), "runs": written})
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed the Sentinel registry")
    ap.add_argument("--catalogue", action="store_true",
                    help="fetch the live catalogue (plain HTTP — costs no watch time)")
    ap.add_argument("--catalogue-file", help="seed from a saved cameras.json instead")
    ap.add_argument("--capability", help="import a capability_report.json")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    log.info("database: %s", db.safe_dsn())

    if args.catalogue_file:
        cams = json.load(open(args.catalogue_file, encoding="utf-8"))
        log.info("seeded %d camera(s) from %s", seed_cameras(cams), args.catalogue_file)
    elif args.catalogue:
        from sentinel_auth import SentinelSession

        with SentinelSession() as s:
            cams = s.catalogue()
        log.info("seeded %d camera(s) from the live catalogue", seed_cameras(cams))

    if args.capability:
        log.info("imported %d capability run(s) from %s",
                 import_capability(args.capability), args.capability)


if __name__ == "__main__":
    main()
