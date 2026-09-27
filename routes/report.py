"""The submission's plate report: every read, with its timestamp and evidence.

    python -m routes.report --out docs/plate_report            # md + csv + json
    python -m routes.report --source anpr.worker               # grid reads only

The organiser asks for "an output report of plates with timestamps" alongside
the government-feed video. This produces it in three forms — Markdown to read,
CSV to open in a spreadsheet, JSON to machine-check — from the same rows the
platform actually stored.

It reports every read, including the ones that failed. A report that lists only
the successes invites the reader to assume the rest worked too, and on this
grid most reads do not produce a usable registration. The failures are the
finding.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime, timezone
from typing import Any

from registry import db

SQL = """
    SELECT r.id, r.plate, r.observed_at, r.pts_ms, r.confidence, r.reads,
           r.agreeing_reads, r.valid_format, r.usable, r.repaired,
           r.plate_width_px, r.char_height_px, r.vehicle_class, r.epoch,
           r.model, r.source, r.created_at,
           c.code AS camera_code, c.name AS camera_name, c.district,
           c.location_confidence,
           ST_Y(c.geom) AS lat, ST_X(c.geom) AS lon,
           cc.capability_grade
    FROM plate_read r
    JOIN camera c ON c.id = r.camera_id
    LEFT JOIN camera_current cc ON cc.id = c.id
    WHERE (%(source)s::text IS NULL OR r.source = %(source)s)
      AND (%(since)s::timestamptz IS NULL OR r.observed_at >= %(since)s)
    ORDER BY r.observed_at, c.code
"""


def rows(source: str | None, since: datetime | None) -> list[dict[str, Any]]:
    out = []
    for r in db.query(SQL, {"source": source, "since": since}):
        out.append({k: (float(v) if hasattr(v, "quantize") else v) for k, v in r.items()})
    return out


def rows_from_jsonl(path: str, catalogue: dict[str, dict[str, Any]] | None = None
                    ) -> list[dict[str, Any]]:
    """Read the worker's own JSONL instead of the database.

    The worker always writes JSONL, database or not, so the report survives the
    database being unavailable — which on a deadline matters more than elegance.
    Camera grades and coordinates are filled from a capability report when one
    is given, and left blank rather than guessed when it is not.
    """
    catalogue = catalogue or {}
    out: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            cam = catalogue.get(e["camera"], {})
            out.append({
                "id": None,
                "plate": e["plate"],
                "observed_at": datetime.fromisoformat(e["observed_at"]),
                "pts_ms": e.get("best_pts_ms"),
                "confidence": e.get("confidence"),
                "reads": e.get("reads"),
                "agreeing_reads": e.get("agreeing_reads"),
                "valid_format": e.get("valid_format"),
                "usable": e.get("usable"),
                "repaired": e.get("repaired"),
                "plate_width_px": e.get("plate_width_px"),
                "char_height_px": e.get("char_height_px"),
                "vehicle_class": e.get("vehicle_class"),
                "epoch": e.get("epoch"),
                "model": e.get("model"),
                "source": "anpr.worker",
                "created_at": None,
                "camera_code": e["camera"],
                "camera_name": cam.get("name") or e["camera"],
                "district": cam.get("district"),
                "location_confidence": None,
                "lat": None, "lon": None,
                "capability_grade": cam.get("capability_grade"),
            })
    out.sort(key=lambda r: (r["observed_at"], r["camera_code"]))
    return out


def catalogue_from_report(path: str) -> dict[str, dict[str, Any]]:
    with open(path, encoding="utf-8") as fh:
        report = json.load(fh)
    return {c["id"]: {"name": c.get("name"), "district": c.get("district"),
                      "capability_grade": c.get("capability_grade")}
            for c in report.get("cameras", [])}


def markdown(data: list[dict[str, Any]], source: str | None,
             provenance: str = "the Sentinel registry database") -> str:
    usable = [r for r in data if r["usable"]]
    valid = [r for r in data if r["valid_format"]]
    cameras = sorted({r["camera_code"] for r in data})
    synthetic = [r for r in data if (r["source"] or "").startswith("synthetic")]

    lines = [
        "# Plate reads with timestamps",
        "",
        f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} "
        f"from {provenance}.",
        "",
        "## Summary",
        "",
        f"- **{len(data)}** plate reads across **{len(cameras)}** cameras",
        f"- **{len(valid)}** parsed as a valid Indian registration",
        f"- **{len(usable)}** usable — valid format, corroborated across frames, "
        f"characters in agreement",
        f"- Source filter: `{source or 'all sources'}`",
    ]
    if synthetic:
        lines.append(f"- **{len(synthetic)}** rows are tagged synthetic demonstration data "
                     f"and are marked in the table")
    lines += [
        "",
        "Every read is listed, including the ones that failed. On this grid most "
        "cameras cannot resolve a plate well enough to read it, and a report that "
        "showed only the successes would invite the reader to assume the rest "
        "worked too. Timestamps are the media timestamp of the best frame in the "
        "track, mapped to wall clock at capture.",
        "",
        "## Reads",
        "",
        "| # | Timestamp (UTC) | Camera | Location | Plate read | Valid | Usable | "
        "Frames voted | Plate px | Camera grade |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(data, 1):
        tag = " *(synthetic)*" if (r["source"] or "").startswith("synthetic") else ""
        lines.append(
            f"| {i} | {r['observed_at']:%Y-%m-%d %H:%M:%S} | {r['camera_code']}{tag} | "
            f"{r['district'] or '—'} | `{r['plate']}` | "
            f"{'yes' if r['valid_format'] else 'no'} | "
            f"{'**yes**' if r['usable'] else 'no'} | "
            f"{r['agreeing_reads']}/{r['reads']} | "
            f"{r['plate_width_px'] and round(float(r['plate_width_px'])) or '—'} | "
            f"{r['capability_grade'] or '—'} |")

    lines += [
        "",
        "## How to read this",
        "",
        "- **Valid** means the string parses as an Indian registration mark "
        "(state code, RTO, series, number, or the BH series).",
        "- **Usable** additionally requires that more than one frame contributed "
        "and that the characters agreed. A single uncorroborated read is a lead, "
        "not a registration.",
        "- **Frames voted** is agreeing reads out of total reads for that vehicle "
        "track. The platform reads the plate on every frame and votes per "
        "character; the winning string is often one no single frame produced.",
        "- **Plate px** is the plate's width in pixels. Reliable OCR needs roughly "
        "120 px; below about 60 px the characters are not present in the image at all.",
        "- **Camera grade** is the Camera Capability Index grade from the most "
        "recent profiling run.",
    ]
    return "\n".join(lines) + "\n"


CSV_FIELDS = ["observed_at", "camera_code", "camera_name", "district", "lat", "lon",
              "plate", "valid_format", "usable", "repaired", "confidence", "reads",
              "agreeing_reads", "plate_width_px", "char_height_px", "vehicle_class",
              "capability_grade", "epoch", "pts_ms", "model", "source"]


def main() -> None:
    ap = argparse.ArgumentParser(description="Plate reads with timestamps")
    ap.add_argument("--out", default="docs/plate_report", help="path prefix")
    ap.add_argument("--source", help="filter by source, e.g. anpr.worker")
    ap.add_argument("--since", help="ISO timestamp lower bound")
    ap.add_argument("--from-jsonl", metavar="PATH",
                    help="read the worker's JSONL instead of the database")
    ap.add_argument("--capability", metavar="PATH",
                    help="capability report, to label cameras when using --from-jsonl")
    args = ap.parse_args()

    since = None
    if args.since:
        since = datetime.fromisoformat(args.since)
        since = since if since.tzinfo else since.astimezone()

    provenance = "the Sentinel registry database"
    if args.from_jsonl:
        provenance = (f"`{os.path.basename(args.from_jsonl)}`, written directly by the "
                      f"ANPR worker")
        data = rows_from_jsonl(
            args.from_jsonl,
            catalogue_from_report(args.capability) if args.capability else None)
    else:
        data = rows(args.source, since)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)

    with open(f"{args.out}.md", "w", encoding="utf-8") as fh:
        fh.write(markdown(data, args.source, provenance))
    with open(f"{args.out}.csv", "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for r in data:
            writer.writerow({k: (r[k].isoformat() if k == "observed_at" else r[k])
                             for k in CSV_FIELDS})
    with open(f"{args.out}.json", "w", encoding="utf-8") as fh:
        json.dump([{k: (v.isoformat() if isinstance(v, datetime) else v)
                    for k, v in r.items()} for r in data], fh, indent=2)

    usable = sum(1 for r in data if r["usable"])
    print(f"{len(data)} reads ({usable} usable) written to "
          f"{args.out}.md / .csv / .json")
    try:
        db.audit("plate_report", actor="routes.report",
                 detail={"rows": len(data), "usable": usable, "source": args.source})
    except Exception:
        pass        # the report is the deliverable; the audit row is not worth failing for


if __name__ == "__main__":
    main()
