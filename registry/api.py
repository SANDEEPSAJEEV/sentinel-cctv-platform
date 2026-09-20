"""Sentinel registry API — Model 1 (Centralised CCTV Registry & GIS).

Metadata only: this service never touches video. It answers where the cameras
are, who owns them, what each one can physically resolve, and what is still
unknown about them.

    uvicorn registry.api:app --port 8090

Onboarding has three doors, because departments differ:
    POST /api/cameras         one camera, from the form
    POST /api/cameras/bulk    a CSV, for a department with a spreadsheet
    POST /api/capability      a capability_report.json from profile_cameras.py

Reads are purpose-bound: send X-Purpose-Ref with the FIR/DD number the query is
being made under. It is recorded in audit_log against the rows returned.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import os

from registry import db

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "static")

app = FastAPI(title="Sentinel Registry", version="0.1",
              description="Centralised CCTV registry and GIS. Metadata only.")

GRADES = ("IDENTIFY", "RECOGNISE", "OBSERVE", "DETECT", "UNGRADED")


# ----------------------------------------------------------------- helpers
def _feature(row: dict[str, Any]) -> dict[str, Any]:
    lon, lat = row.pop("lon", None), row.pop("lat", None)
    row.pop("geom", None)
    return {
        "type": "Feature",
        "geometry": None if lon is None else {"type": "Point", "coordinates": [lon, lat]},
        "properties": row,
    }


CAMERA_COLUMNS = """
    c.code, c.external_ref, c.name, c.department_code, c.department_name,
    c.district, c.address, c.owner_type, c.signal_type, c.camera_type,
    c.node_code, c.node_kind, c.status, c.location_confidence, c.location_source,
    c.heading_deg, c.fov_deg, c.mount_height_m, c.view_kind, c.public_facing,
    c.consent_ref, c.consent_expires_on, c.stream_path,
    c.capability_grade, c.pixel_density_px_per_m, c.measured_fps,
    c.plates_observed, c.lighting, c.anpr_capable, c.evidence_verified,
    c.last_profiled_at, c.last_reachable,
    ST_X(c.geom) AS lon, ST_Y(c.geom) AS lat
"""


# -------------------------------------------------------------------- reads
@app.get("/api/stats")
def stats() -> dict[str, Any]:
    """Counts for the dashboard. Deliberately includes what is *unknown*:
    a registry that only reports what it knows is not a gap analysis."""
    return {
        "cameras": db.query("SELECT count(*) AS n FROM camera")[0]["n"],
        "by_grade": {r["k"]: r["n"] for r in db.query(
            "SELECT coalesce(capability_grade, 'UNPROFILED') AS k, count(*) AS n "
            "FROM camera_current GROUP BY 1 ORDER BY 2 DESC")},
        "by_department": {r["k"]: r["n"] for r in db.query(
            "SELECT coalesce(department_name, 'Unassigned') AS k, count(*) AS n "
            "FROM camera_current GROUP BY 1 ORDER BY 2 DESC")},
        "by_status": {r["k"]: r["n"] for r in db.query(
            "SELECT status AS k, count(*) AS n FROM camera GROUP BY 1 ORDER BY 2 DESC")},
        "by_location_confidence": {r["k"]: r["n"] for r in db.query(
            "SELECT location_confidence AS k, count(*) AS n FROM camera GROUP BY 1 ORDER BY 2 DESC")},
        "by_owner": {r["k"]: r["n"] for r in db.query(
            "SELECT owner_type AS k, count(*) AS n FROM camera GROUP BY 1")},
        "department_unassigned": db.query(
            "SELECT count(*) AS n FROM camera_current WHERE department_code = 'UNASSIGNED'")[0]["n"],
        "anpr_capable": db.query(
            "SELECT count(*) AS n FROM camera_current WHERE anpr_capable")[0]["n"],
        "evidence_verified": db.query(
            "SELECT count(*) AS n FROM camera_current WHERE evidence_verified")[0]["n"],
        "mappable": db.query(
            "SELECT count(*) AS n FROM camera WHERE geom IS NOT NULL")[0]["n"],
    }


@app.get("/api/departments")
def departments() -> list[dict[str, Any]]:
    return db.query(
        """SELECT d.code, d.name, d.kind, d.contact_name, d.contact_email, d.contact_phone,
                  count(c.id) AS cameras
           FROM department d LEFT JOIN camera c ON c.department_id = d.id
           GROUP BY d.id ORDER BY cameras DESC, d.name"""
    )


@app.get("/api/cameras")
def cameras(
    department: str | None = None,
    grade: str | None = Query(None, description="IDENTIFY / RECOGNISE / OBSERVE / DETECT / UNGRADED"),
    status: str | None = None,
    owner_type: str | None = None,
    confidence: str | None = None,
    q: str | None = Query(None, description="substring of name, code or district"),
    bbox: str | None = Query(None, description="minLon,minLat,maxLon,maxLat"),
    mappable_only: bool = False,
    x_purpose_ref: str | None = Header(None),
) -> dict[str, Any]:
    """Cameras as GeoJSON. Cameras with no location are still returned, with a
    null geometry — they are the onboarding worklist, not rows to hide."""
    where, params = ["1=1"], {}
    if department:
        where.append("c.department_code = %(department)s"); params["department"] = department
    if grade:
        where.append("c.capability_grade = %(grade)s"); params["grade"] = grade
    if status:
        where.append("c.status = %(status)s"); params["status"] = status
    if owner_type:
        where.append("c.owner_type = %(owner_type)s"); params["owner_type"] = owner_type
    if confidence:
        where.append("c.location_confidence = %(confidence)s"); params["confidence"] = confidence
    if q:
        where.append("(c.name ILIKE %(q)s OR c.code ILIKE %(q)s OR c.district ILIKE %(q)s)")
        params["q"] = f"%{q}%"
    if mappable_only:
        where.append("c.geom IS NOT NULL")
    if bbox:
        try:
            x1, y1, x2, y2 = (float(v) for v in bbox.split(","))
        except ValueError:
            raise HTTPException(400, "bbox must be minLon,minLat,maxLon,maxLat")
        where.append("c.geom && ST_MakeEnvelope(%(x1)s, %(y1)s, %(x2)s, %(y2)s, 4326)")
        params.update(x1=x1, y1=y1, x2=x2, y2=y2)

    rows = db.query(
        f"SELECT {CAMERA_COLUMNS} FROM camera_current c WHERE {' AND '.join(where)} ORDER BY c.code",
        params,
    )
    db.audit("read_cameras", actor="api", object_type="camera",
             purpose_ref=x_purpose_ref, detail={"filters": params, "returned": len(rows)})
    return {"type": "FeatureCollection", "features": [_feature(r) for r in rows]}


@app.get("/api/cameras/{code}")
def camera_detail(code: str, x_purpose_ref: str | None = Header(None)) -> dict[str, Any]:
    rows = db.query(f"SELECT {CAMERA_COLUMNS} FROM camera_current c WHERE c.code = %s", (code,))
    if not rows:
        raise HTTPException(404, f"no camera {code}")
    history = db.query(
        """SELECT r.observed_at, r.reachable, r.resolution, r.codec, r.measured_fps,
                  r.declared_fps, r.video_seconds_analysed, r.frames_analysed,
                  r.gap_ms_p50, r.gap_ms_max, r.reconnects, r.epochs_seen, r.lighting,
                  r.plates_observed, r.plates_static_rejected, r.plate_width_px_median,
                  r.pixel_density_px_per_m, r.char_height_px_est, r.capability_grade,
                  r.capability_note, r.anpr_capable, r.evidence_verified, r.error, r.source
           FROM capability_run r JOIN camera c ON c.id = r.camera_id
           WHERE c.code = %s ORDER BY r.observed_at DESC LIMIT 50""",
        (code,),
    )
    db.audit("read_camera", actor="api", object_type="camera", object_id=code,
             purpose_ref=x_purpose_ref)
    feature = _feature(rows[0])
    feature["properties"]["capability_history"] = history
    return feature


@app.get("/api/worklist")
def worklist() -> dict[str, Any]:
    """What the registry does not yet know. This is the gap-analysis report
    Model 1 asks for, and it is the department questionnaire's agenda."""
    return {
        "location_unknown": db.query(
            "SELECT code, name, district, location_source FROM camera "
            "WHERE geom IS NULL ORDER BY code"),
        "department_unassigned": db.query(
            "SELECT code, name, district FROM camera_current "
            "WHERE department_code = 'UNASSIGNED' ORDER BY code"),
        "never_profiled": db.query(
            "SELECT code, name FROM camera_current WHERE last_profiled_at IS NULL ORDER BY code"),
        "grade_unverified": db.query(
            "SELECT code, name, capability_grade FROM camera_current "
            "WHERE capability_grade IS NOT NULL AND NOT evidence_verified ORDER BY code"),
        "optics_unknown": db.query(
            "SELECT code, name FROM camera WHERE heading_deg IS NULL OR mount_height_m IS NULL "
            "ORDER BY code"),
        "consent_expiring": db.query(
            "SELECT code, name, consent_ref, consent_expires_on FROM camera "
            "WHERE owner_type = 'private' AND consent_expires_on IS NOT NULL "
            "AND consent_expires_on < current_date + 30 ORDER BY consent_expires_on"),
    }


# ------------------------------------------------------------- onboarding
CAMERA_WRITE_FIELDS = (
    "external_ref", "name", "district", "address", "owner_type", "signal_type",
    "camera_type", "make", "model", "declared_resolution", "declared_fps", "codec",
    "location_confidence", "location_source", "heading_deg", "fov_deg",
    "mount_height_m", "view_kind", "status", "commissioned_on", "retention_days",
    "public_facing", "consent_ref", "consent_expires_on", "stream_path",
)


def _insert_camera(cur: Any, rec: dict[str, Any]) -> str:
    code = (rec.get("code") or "").strip()
    if not code:
        raise HTTPException(400, "code is required")
    dept_code = rec.get("department_code") or "UNASSIGNED"
    cur.execute("SELECT id FROM department WHERE code = %s", (dept_code,))
    dept = cur.fetchone()
    if dept is None:
        raise HTTPException(400, f"unknown department_code {dept_code}")
    node_id = None
    if rec.get("node_code"):
        cur.execute("SELECT id FROM node WHERE code = %s", (rec["node_code"],))
        node = cur.fetchone()
        if node is None:
            raise HTTPException(400, f"unknown node_code {rec['node_code']}")
        node_id = node["id"]

    # Only write fields the caller actually supplied. Writing NULL for the rest
    # would override the schema's defaults (status, owner_type, signal_type are
    # NOT NULL with defaults) and fail before the real constraints are reached.
    values: dict[str, Any] = {f: rec[f] for f in CAMERA_WRITE_FIELDS
                              if rec.get(f) not in (None, "")}
    values.update(code=code, department_id=dept["id"], node_id=node_id,
                  lat=rec.get("lat"), lon=rec.get("lon"))
    cols = [c for c in values if c not in ("lat", "lon")]
    placeholders = ", ".join(f"%({c})s" for c in cols)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "code")
    cur.execute(
        f"""INSERT INTO camera ({', '.join(cols)}, geom)
            VALUES ({placeholders},
                    CASE WHEN %(lon)s::double precision IS NULL THEN NULL
                         ELSE ST_SetSRID(ST_MakePoint(%(lon)s::double precision,
                                                      %(lat)s::double precision), 4326) END)
            ON CONFLICT (code) DO UPDATE SET {updates}, geom = EXCLUDED.geom, updated_at = now()
            RETURNING code""",
        values,
    )
    return cur.fetchone()["code"]


NODE_WRITE_FIELDS = ("kind", "channels_total", "channels_used", "make", "model",
                     "firmware", "management_host", "site_name", "district", "notes")


@app.get("/api/nodes")
def nodes() -> list[dict[str, Any]]:
    return db.query(
        """SELECT n.code, n.kind, n.make, n.model, n.firmware, n.management_host,
                  n.site_name, n.district, n.channels_total, n.channels_used,
                  d.name AS department_name, count(c.id) AS cameras,
                  ST_X(n.geom) AS lon, ST_Y(n.geom) AS lat
           FROM node n
           LEFT JOIN department d ON d.id = n.department_id
           LEFT JOIN camera c ON c.node_id = n.id
           GROUP BY n.id, d.name ORDER BY n.code"""
    )


@app.post("/api/nodes", status_code=201)
def create_node(record: dict[str, Any] = Body(...),
                x_purpose_ref: str | None = Header(None)) -> dict[str, Any]:
    """Register a DVR / NVR / encoder / departmental VMS. Analog cameras reach
    the platform only through one of these, so it is registered in its own
    right — with its channel count, which is what caps an upgrade plan."""
    code = (record.get("code") or "").strip()
    if not code:
        raise HTTPException(400, "code is required")
    values = {f: record[f] for f in NODE_WRITE_FIELDS if record.get(f) not in (None, "")}
    values["code"] = code
    if not values.get("kind"):
        raise HTTPException(400, "kind is required (dvr / nvr / encoder / vms / direct)")
    dept_code = record.get("department_code")
    if dept_code:
        rows = db.query("SELECT id FROM department WHERE code = %s", (dept_code,))
        if not rows:
            raise HTTPException(400, f"unknown department_code {dept_code}")
        values["department_id"] = rows[0]["id"]
    values.update(lat=record.get("lat"), lon=record.get("lon"))
    cols = [c for c in values if c not in ("lat", "lon")]
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""INSERT INTO node ({', '.join(cols)}, geom)
                VALUES ({', '.join('%(' + c + ')s' for c in cols)},
                        CASE WHEN %(lon)s::double precision IS NULL THEN NULL
                             ELSE ST_SetSRID(ST_MakePoint(%(lon)s::double precision,
                                                          %(lat)s::double precision), 4326) END)
                ON CONFLICT (code) DO UPDATE SET
                    {', '.join(f'{c} = EXCLUDED.{c}' for c in cols if c != 'code')},
                    geom = EXCLUDED.geom
                RETURNING code""",
            values,
        )
        conn.commit()
    db.audit("create_node", actor="api", object_type="node", object_id=code,
             purpose_ref=x_purpose_ref)
    return {"code": code}


@app.post("/api/cameras", status_code=201)
def create_camera(record: dict[str, Any] = Body(...),
                  x_purpose_ref: str | None = Header(None)) -> dict[str, Any]:
    """Manual onboarding. A private camera without a consent reference is
    rejected by the database, not by a hopeful check in the UI."""
    try:
        with db.connect() as conn, conn.cursor() as cur:
            code = _insert_camera(cur, record)
            conn.commit()
    except Exception as exc:
        if "private_camera_needs_consent" in str(exc):
            raise HTTPException(400, "a private camera needs consent_ref")
        if "analog_camera_needs_node" in str(exc):
            raise HTTPException(400, "an analog camera must hang off a node (node_code)")
        raise
    db.audit("create_camera", actor="api", object_type="camera", object_id=code,
             purpose_ref=x_purpose_ref)
    return {"code": code}


@app.post("/api/cameras/bulk")
async def bulk_cameras(file: UploadFile, x_purpose_ref: str | None = Header(None)) -> dict[str, Any]:
    """CSV onboarding, for a department that keeps its estate in a spreadsheet.
    Rows are validated one by one; a bad row is reported, not silently dropped."""
    raw = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(raw))
    written, errors = [], []
    with db.connect() as conn, conn.cursor() as cur:
        for i, row in enumerate(reader, 2):        # row 1 is the header
            clean = {k.strip(): (v.strip() if isinstance(v, str) else v)
                     for k, v in row.items() if k}
            for key in ("lat", "lon", "heading_deg", "fov_deg", "mount_height_m",
                        "declared_fps", "retention_days"):
                if clean.get(key) in ("", None):
                    clean[key] = None
            try:
                with conn.transaction():
                    written.append(_insert_camera(cur, clean))
            except HTTPException as exc:
                errors.append({"row": i, "error": exc.detail})
            except Exception as exc:
                errors.append({"row": i, "error": str(exc).splitlines()[0]})
        conn.commit()
    db.audit("bulk_cameras", actor="api", purpose_ref=x_purpose_ref,
             detail={"file": file.filename, "written": len(written), "errors": len(errors)})
    return {"written": len(written), "codes": written, "errors": errors}


@app.post("/api/capability")
def ingest_capability(report: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Ingest a capability_report.json produced by profile_cameras.py."""
    from registry.seed import import_capability
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(report, fh)
        path = fh.name
    try:
        return {"runs": import_capability(path)}
    finally:
        os.unlink(path)


@app.post("/api/cameras/{code}/verify")
def verify_evidence(code: str, verified: bool = Body(True, embed=True),
                    x_purpose_ref: str | None = Header(None)) -> dict[str, Any]:
    """Mark the latest capability run as checked by a human. Detector boxes are
    a claim until somebody has looked at the crops."""
    n = db.execute(
        """UPDATE capability_run SET evidence_verified = %s
           WHERE id = (SELECT r.id FROM capability_run r JOIN camera c ON c.id = r.camera_id
                       WHERE c.code = %s ORDER BY r.observed_at DESC LIMIT 1)""",
        (verified, code),
    )
    if not n:
        raise HTTPException(404, f"no capability run for {code}")
    db.audit("verify_evidence", actor="api", object_type="camera", object_id=code,
             purpose_ref=x_purpose_ref, detail={"verified": verified})
    return {"code": code, "evidence_verified": verified}


# ------------------------------------------------------------------ health
@app.get("/healthz")
def healthz() -> JSONResponse:
    try:
        db.query("SELECT 1 AS ok")
    except Exception as exc:
        return JSONResponse({"status": "degraded", "database": str(exc)[:200]}, status_code=503)
    return JSONResponse({"status": "ok", "database": db.safe_dsn()})


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(STATIC, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC), name="static")
