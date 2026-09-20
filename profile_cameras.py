"""
Camera Capability Index (CCI) — profile every camera in the grid and grade it
by what it can *physically* do, not by whether it is online.

This is the headline deliverable. Model 1 of the problem statement already asks
for "camera health and maintenance-status monitoring" and "gap-analysis reports
for uncovered zones and ageing infrastructure"; most teams will ship a green
dot. This measures the optics.

What it measures per camera
---------------------------
  resolution, codec, measured fps (from PTS, not CAP_PROP_FPS)
  inter-frame gap p50/p95/max, loop epochs seen, reconnects, decode warnings
  mean luminance  -> day / low-light / night
  Laplacian variance -> focus and motion blur
  plate pixel geometry (if a detector is installed):
      median plate width in px, and the implied pixel density in px/m

Grading
-------
Graded on pixel density at the plate, against the IEC 62676-4:2014 DORI
thresholds. Plates are the ruler: their physical size is fixed by CMVR, so
plate width in px / plate width in m = px/m at the traffic lane.

px/m is a LOWER BOUND: every plate is assumed to be the widest CMVR plate,
500 mm single-line. Typing plates by box aspect (single-line ~4.2, two-line
1.7-2.0) was tried and dropped: at low resolution a blurred single-line plate
gets a loose, tall box, reads as "two-line", and inflates the grade — on the
real grid it lifted cameras with ~7 px characters to RECOGNISE. Two-wheeler
plates (200 mm) are therefore under-graded; say so rather than over-claim.
Aspect-based type counts are still reported, for information only.

Detector boxes alone are NOT proof of a plate: on the real grid the detector
fired at conf 0.7-0.84 on a vehicle's rooftop unit, and on taillight bars,
on-screen date text and shop signs. Treat grades as provisional until a
camera's plates are confirmed by OCR (fast-plate-ocr) or by eye (--save-crops).

  IDENTIFY   >= 250 px/m   -> full ANPR  (~120 px across a 500 mm plate)
  RECOGNISE  >= 125 px/m   -> marginal ANPR; vehicle attributes and re-ID
  OBSERVE    >= 63 px/m    -> class / colour / direction / counting
  DETECT     >= 25 px/m    -> presence, motion, counting only
  UNGRADED   fewer than MIN_PLATES_FOR_GRADE plates in the sample — no evidence
             either way; resample (longer window, busier hour), don't replace

Detections that do not move (signage, on-screen timestamp text) are rejected
before grading — on the real grid they otherwise set several grades alone.

Usage
-----
    python profile_cameras.py --host http://localhost:8080 --seconds 45
    python profile_cameras.py --host https://<sentinel-host> --seconds 60 \\
                              --out capability_report.json

Plate measurement is optional. Without a detector installed the script still
produces the stream-health half of the report:

    pip install open-image-models[onnx]     # or [onnx-gpu]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
from collections import deque
from datetime import datetime, timezone
from typing import Any

import cv2
import numpy as np

from sentinel_auth import camera_id_of, redact
from sentinel_client import SentinelCapture, catalogue, rtsp_url_of

log = logging.getLogger("profile")

# Indian plate geometry (Central Motor Vehicles Rules)
SINGLE_LINE_MIN_ASPECT = 3.0      # 500x120 mm is 4.2; two-line plates are 1.7-2.0
PLATE_WIDTH_M = {"single": 0.50, "two_line": 0.34}
CHAR_HEIGHT_FRACTION = {"single": 0.54, "two_line": 0.35}   # per text row

# Static-detection rejection. (a) same pixels (IoU) held across this much PTS —
# the detector fires intermittently on fixed signage; (b) the box region is
# pixel-for-pixel unchanged against a frame ~1 s earlier — catches signs and
# on-screen timestamp text detected only once.
STATIC_IOU = 0.7
STATIC_MS = 1_000.0
STATIC_LOOKBACK_MS = 800.0
STATIC_PIXEL_DIFF = 6.0          # median |grey delta| inside the box
# Indian plates run from 1.7 (340x200 / 200x100 mm two-line) to 4.2 (500x120
# mm single-line). Anything taller than wide is not a plate.
PLATE_ASPECT_RANGE = (1.2, 6.0)

MIN_PLATES_FOR_GRADE = 3

# IEC 62676-4:2014 DORI, px/m
GRADES = [
    ("IDENTIFY", 250.0, "Full ANPR"),
    ("RECOGNISE", 125.0, "Marginal ANPR; vehicle attributes and re-ID"),
    ("OBSERVE", 63.0, "Vehicle class, colour, direction, counting"),
    ("DETECT", 25.0, "Presence, motion, counting only"),
]


def _load_plate_detector(model: str) -> Any | None:
    try:
        from open_image_models import create_detector  # type: ignore
    except Exception as exc:
        log.warning("plate detector unavailable (%s) — stream health only", exc)
        return None
    try:
        return create_detector(model)
    except Exception as exc:
        log.warning("could not load %s (%s) — stream health only", model, exc)
        return None


_detector_errors = 0


def _xyxy(det: Any) -> tuple[float, float, float, float]:
    box = getattr(det, "bounding_box", None) or getattr(det, "bbox", None) or det
    if all(hasattr(box, a) for a in ("x1", "y1", "x2", "y2")):
        return float(box.x1), float(box.y1), float(box.x2), float(box.y2)
    # open-image-models' BoundingBox is iterable but NOT subscriptable, so
    # never index it; iterate.
    x1, y1, x2, y2 = (float(v) for v in list(box)[:4])
    return x1, y1, x2, y2


def _iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _static_mask(dets: list[tuple[float, tuple[float, float, float, float]]]) -> list[bool]:
    """Flag detections that hold the same pixels for >= STATIC_MS of PTS.

    A plate on a moving vehicle cannot. On the real grid the detector fires
    intermittently on fixed signage (cam01: an orange board, ~77x52 px, conf
    0.26-0.53) and that one object otherwise sets the camera's grade on its
    own. A vehicle stopped in frame is dropped too — conservative for grading.
    """
    flags = []
    for pts_a, box_a in dets:
        same = [pts_b for pts_b, box_b in dets if _iou(box_a, box_b) >= STATIC_IOU]
        flags.append(max(same) - min(same) >= STATIC_MS)
    return flags


def _plate_boxes(detector: Any, image: np.ndarray) -> list[tuple[float, float, float, float, float]]:
    """Return [(x1, y1, x2, y2, confidence), ...] for detected plates.

    Failures are logged, never swallowed silently: a detector that errors on
    every frame reports plates_observed=0, which is indistinguishable from a
    camera that genuinely cannot resolve a plate — and that would fake the CCI
    finding.
    """
    global _detector_errors
    try:
        detections = detector.predict(image)
    except Exception as exc:
        _detector_errors += 1
        if _detector_errors <= 3:
            log.warning("plate detector failed on a frame: %r", exc)
        return []
    out: list[tuple[float, float, float, float, float]] = []
    for det in detections or []:
        try:
            x1, y1, x2, y2 = _xyxy(det)
        except Exception as exc:
            _detector_errors += 1
            if _detector_errors <= 3:
                log.warning("unreadable detection %r: %r", det, exc)
            continue
        w, h = abs(x2 - x1), abs(y2 - y1)
        if w > 1 and h > 1 and PLATE_ASPECT_RANGE[0] <= w / h <= PLATE_ASPECT_RANGE[1]:
            conf = float(getattr(det, "confidence", float("nan")))
            out.append((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2), conf))
    return out


def _plate_type(w: float, h: float) -> str:
    return "single" if w / h >= SINGLE_LINE_MIN_ASPECT else "two_line"


def _region_static(grey: np.ndarray, ref: np.ndarray | None,
                   box: tuple[float, float, float, float]) -> bool:
    """True if the box region is unchanged against a frame ~1 s earlier."""
    if ref is None or ref.shape != grey.shape:
        return False
    x1, y1, x2, y2 = (int(v) for v in box)
    a, b = grey[y1:y2, x1:x2], ref[y1:y2, x1:x2]
    return a.size > 0 and float(np.median(cv2.absdiff(a, b))) < STATIC_PIXEL_DIFF


def _grade(px_per_m: float | None, n_plates: int) -> tuple[str, str]:
    if n_plates < MIN_PLATES_FOR_GRADE or px_per_m is None:
        seen = "no plates in view" if n_plates == 0 else f"only {n_plates} plate(s) seen"
        return "UNGRADED", f"{seen} during the sample window — no evidence either way"
    for name, threshold, note in GRADES:
        if px_per_m >= threshold:
            return name, note
    return "DETECT", GRADES[-1][2]


def profile_camera(
    cam: dict[str, Any],
    *,
    seconds: float,
    detector: Any | None,
    sample_ms: float,
    url: str | None = None,
    capture_options: str | None = None,
    crops_dir: str | None = None,
) -> dict[str, Any]:
    try:
        cam_id = camera_id_of(cam)
    except ValueError:
        cam_id = "?"
    url = url or rtsp_url_of(cam)
    result: dict[str, Any] = {
        "id": cam_id,
        "name": cam.get("name"),
        "department": cam.get("department"),
        "location": cam.get("location"),
        "codec_declared": cam.get("codec"),
        "reachable": False,
    }
    if not url:
        result["error"] = "no stream URL for this camera"
        return result
    result["stream_url"] = redact(url)   # never write credentials to the report

    gaps: list[float] = []
    lumas: list[float] = []
    blurs: list[float] = []
    # (t_ms on a clock continuous across reconnects, box, confidence, pixel-static)
    dets: list[tuple[float, tuple[float, float, float, float], float, bool]] = []
    crops: dict[int, np.ndarray] = {}
    past: deque[tuple[float, np.ndarray]] = deque(maxlen=8)   # recent sampled grey frames
    width = height = 0
    frames = realtime_frames = frames_analysed = 0
    last_sample_t = float("-inf")
    first_frame_pts: float | None = None
    first_realtime_pts: float | None = None
    info: dict[str, Any] = {}

    # PTS restarts near zero on every reconnect (and jumps at a loop), so span
    # and fps are accumulated per (reconnect, epoch) segment — never last - first.
    seg_key: tuple[int, int] | None = None
    seg_first = seg_last = 0.0
    seg_frames = 0
    span_ms = 0.0
    intervals = 0

    with SentinelCapture(url, capture_options=capture_options) as cap:
        for f in cap.frames(max_seconds=seconds):
            frames += 1
            if first_frame_pts is None:
                first_frame_pts = f.pts_ms
                info = cap.stream_info()
            # Skip all analysis inside the GOP-replay burst. Besides being stale
            # content, analysing it slows the reader to live pace and hides the
            # burst from the pace detector.
            if not f.realtime:
                continue
            realtime_frames += 1
            if first_realtime_pts is None:
                first_realtime_pts = f.pts_ms

            key = (cap.reconnects, f.epoch)
            if key != seg_key:
                if seg_key is not None:
                    span_ms += seg_last - seg_first
                    intervals += seg_frames - 1
                seg_key, seg_first, seg_frames = key, f.pts_ms, 0
                past.clear()
            else:
                if f.gap_ms > 0:
                    gaps.append(f.gap_ms)
            seg_last = f.pts_ms
            seg_frames += 1
            t_ms = span_ms + (f.pts_ms - seg_first)

            # Sample on video time, not frame count: feeds run 5-30 fps, and
            # every-Nth-frame starves the slow ones of samples.
            if t_ms - last_sample_t < sample_ms:
                continue
            last_sample_t = t_ms
            frames_analysed += 1

            height, width = f.image.shape[:2]
            grey = cv2.cvtColor(f.image, cv2.COLOR_BGR2GRAY)
            lumas.append(float(grey.mean()))
            blurs.append(float(cv2.Laplacian(grey, cv2.CV_64F).var()))

            if detector is not None:
                ref = next((g for t, g in reversed(past) if t_ms - t >= STATIC_LOOKBACK_MS), None)
                for x1, y1, x2, y2, conf in _plate_boxes(detector, f.image):
                    box = (x1, y1, x2, y2)
                    if crops_dir and len(dets) < 80:
                        pad = int(0.6 * (x2 - x1))
                        crops[len(dets)] = f.image[max(0, int(y1) - pad):int(y2) + pad,
                                                   max(0, int(x1) - pad):int(x2) + pad].copy()
                    dets.append((t_ms, box, conf, _region_static(grey, ref, box)))
            past.append((t_ms, grey))

        result["reachable"] = frames > 0
        result["epochs_seen"] = cap.epoch + 1
        result["reconnects"] = cap.reconnects
        result["decode_warnings"] = cap.decode_warnings
        result["codec"] = info.get("codec")
        result["declared_fps"] = info.get("declared_fps")

    if frames == 0:
        result["error"] = "no frames received"
        return result

    if seg_key is not None:
        span_ms += seg_last - seg_first
        intervals += seg_frames - 1
    span_s = span_ms / 1000.0
    measured_fps = intervals / span_s if span_s > 0.5 and intervals > 0 else None

    held = _static_mask([(t, box) for t, box, _, _ in dets])
    static = [h or px for h, (_, _, _, px) in zip(held, dets)]
    moving = [d for d, s in zip(dets, static) if not s]
    plate_widths = [box[2] - box[0] for _, box, _, _ in moving]
    plate_heights = [box[3] - box[1] for _, box, _, _ in moving]
    plate_types = [_plate_type(w, h) for w, h in zip(plate_widths, plate_heights)]
    densities = [w / PLATE_WIDTH_M["single"] for w in plate_widths]   # lower bound
    char_heights = [h * CHAR_HEIGHT_FRACTION[t] for h, t in zip(plate_heights, plate_types)]
    if crops_dir:
        os.makedirs(crops_dir, exist_ok=True)
        saved = {True: 0, False: 0}
        for i, crop in crops.items():
            is_static = static[i]
            if saved[is_static] >= (3 if is_static else 12) or crop.size == 0:
                continue
            x1, y1, x2, y2 = dets[i][1]
            tag = "STATIC" if is_static else "moving"
            cv2.imwrite(os.path.join(crops_dir, f"{cam_id}_{tag}_{i:02d}_{int(x2 - x1)}x{int(y2 - y1)}"
                                                f"_c{dets[i][2]:.2f}.jpg"), crop)
            saved[is_static] += 1

    result.update(
        {
            "resolution": f"{width}x{height}",
            "pixels": width * height,
            "frames_seen": frames,
            "frames_realtime": realtime_frames,
            "gop_replay_frames": frames - realtime_frames,
            # upper bound: includes the pace detector's few-frame latch lag
            "gop_replay_ms": (round(first_realtime_pts - first_frame_pts, 1)
                              if first_realtime_pts is not None and first_frame_pts is not None
                              else None),
            "video_seconds_analysed": round(span_s, 2),
            "frames_analysed": frames_analysed,
            "measured_fps": round(measured_fps, 2) if measured_fps else None,
            "gap_ms_p50": round(statistics.median(gaps), 1) if gaps else None,
            "gap_ms_p95": round(float(np.percentile(gaps, 95)), 1) if len(gaps) > 5 else None,
            "gap_ms_max": round(max(gaps), 1) if gaps else None,
            "mean_luma": round(statistics.mean(lumas), 1) if lumas else None,
            "lighting": (
                None if not lumas
                else "night" if statistics.mean(lumas) < 50
                else "low-light" if statistics.mean(lumas) < 90
                else "day"
            ),
            "focus_laplacian_var": round(statistics.mean(blurs), 1) if blurs else None,
            # detections on sampled frames, not unique vehicles
            "plates_observed": len(plate_widths),
            "plate_detections_static_rejected": sum(static),
            "plate_conf_median": (round(statistics.median(c for _, _, c, _ in moving), 2)
                                  if moving else None),
        }
    )

    density = statistics.median(densities) if densities else None
    if density:
        result.update(
            {
                "plate_width_px_median": round(statistics.median(plate_widths), 1),
                "plate_width_px_p90": round(float(np.percentile(plate_widths, 90)), 1),
                "plate_aspect_median": round(float(np.median(
                    np.array(plate_widths) / np.array(plate_heights))), 2),
                "plate_types": {t: plate_types.count(t) for t in set(plate_types)},
                "char_height_px_est": round(statistics.median(char_heights), 1),
                "pixel_density_px_per_m": round(density, 1),
                "pixel_density_px_per_m_p90": round(float(np.percentile(densities, 90)), 1),
            }
        )

    grade, note = _grade(density, len(plate_widths))
    result["capability_grade"] = grade
    result["capability_note"] = note
    result["anpr_capable"] = grade == "IDENTIFY"
    return result


def remediation(report: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn grades into an action list an engineering budget can be built on."""
    actions = []
    for cam in report:
        if not cam.get("reachable"):
            actions.append({"id": cam["id"], "action": "INVESTIGATE", "reason": cam.get("error", "unreachable")})
            continue
        grade = cam.get("capability_grade")
        d = cam.get("pixel_density_px_per_m")
        if grade == "IDENTIFY":
            continue
        if grade == "UNGRADED":
            actions.append({"id": cam["id"], "action": "RESAMPLE",
                            "reason": f"{cam.get('capability_note')} — longer window or busier hour"})
        elif grade == "RECOGNISE":
            actions.append({"id": cam["id"], "action": "RE_LENS",
                            "reason": f"{d} px/m at the plate — ~{250 / d:.1f}x longer focal length "
                                      "reaches the 250 px/m Identify grade"})
        elif grade == "OBSERVE":
            actions.append({"id": cam["id"], "action": "RE_AIM_OR_RE_LENS",
                            "reason": f"{d} px/m at the plate — needs ~{250 / d:.1f}x; tighter framing "
                                      "on the traffic lane"})
        else:
            actions.append({"id": cam["id"], "action": "REPLACE_OR_REPURPOSE",
                            "reason": f"{d} px/m at the plate — below Observe; use for presence/counting"})
    return actions


def main() -> None:
    ap = argparse.ArgumentParser(description="Sentinel Camera Capability Index")
    ap.add_argument("--host", default="https://cctv.corp8.cloud",
                    help="catalogue host — https://cctv.corp8.cloud (real grid, with --auth) "
                         "or http://localhost:8080 (local replica)")
    ap.add_argument("--auth", action="store_true",
                    help="log in first (real grid); reads SENTINEL_EMAIL / SENTINEL_PASSWORD")
    ap.add_argument("--transport", choices=["rtsp", "hls"], default=None,
                    help="force a transport; default is probed (RTSP if 8554 is open, else HLS)")
    ap.add_argument("--seconds", type=float, default=45.0, help="sample window per camera")
    ap.add_argument("--sample-ms", type=float, default=250.0,
                    help="analyse one frame per this much video time (PTS)")
    ap.add_argument("--model", default="yolo-v9-s-608-license-plate-end2end")
    ap.add_argument("--no-plates", action="store_true", help="skip plate measurement")
    ap.add_argument("--only", nargs="*", help="limit to these camera ids")
    ap.add_argument("--out", default="capability_report.json")
    ap.add_argument("--save-crops", metavar="DIR", default=None,
                    help="save up to 12 detected plate crops per camera, to verify detections by eye")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    session = None
    transport = None
    if args.auth:
        from sentinel_auth import SentinelSession

        session = SentinelSession(args.host)
        session.login()
        cams = session.catalogue()
        probe = session.transport_report()
        transport = args.transport or probe["recommended_transport"]
        log.info("transport probe: %s", probe["ports"])
        if probe.get("note"):
            log.warning("%s", probe["note"])
        log.info("using transport: %s", transport)
    else:
        cams = catalogue(args.host)

    if args.only:
        wanted = set(args.only)
        cams = [c for c in cams if camera_id_of(c) in wanted]
    log.info("profiling %d camera(s) at %.0fs each", len(cams), args.seconds)

    detector = None if args.no_plates else _load_plate_detector(args.model)

    # Sequential on purpose: "each connected client receives its own copy of the
    # stream ... open only the cameras you are actively processing."
    report = []
    for i, cam in enumerate(cams, 1):
        # The grid meters watch time per account. Once spent, RTSP answers 401
        # for every camera — stop instead of burning retries through the rest.
        if (session is not None and report and not report[-1].get("reachable")
                and session.quota_exhausted()):
            log.error("watch-time quota spent — skipping the remaining %d camera(s)",
                      len(cams) - i + 1)
            report.extend({"id": camera_id_of(c), "name": c.get("name"), "reachable": False,
                           "error": "skipped: watch-time quota spent"} for c in cams[i - 1:])
            break
        log.info("[%d/%d] camera %s", i, len(cams), cam.get("id"))
        url = cap_opts = None
        if session is not None:
            cid = camera_id_of(cam)
            url = session.stream_url(cid, transport)
            cap_opts = session.capture_options(transport)  # type: ignore[arg-type]
        try:
            report.append(profile_camera(cam, seconds=args.seconds,
                                         detector=detector, sample_ms=args.sample_ms,
                                         url=url, capture_options=cap_opts,
                                         crops_dir=args.save_crops))
        except Exception as exc:
            log.exception("camera %s failed", cam.get("id"))
            report.append({"id": str(cam.get("id")), "reachable": False, "error": repr(exc)})

    graded = [c for c in report if c.get("reachable")]
    counts: dict[str, int] = {}
    for c in graded:
        counts[c.get("capability_grade", "?")] = counts.get(c.get("capability_grade", "?"), 0) + 1

    payload = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "host": args.host,
        "sample_seconds": args.seconds,
        "plate_model": None if args.no_plates else args.model,
        "cameras_total": len(report),
        "cameras_reachable": len(graded),
        "grade_counts": counts,
        "anpr_capable": sum(1 for c in graded if c.get("anpr_capable")),
        "cameras": report,
        "remediation": remediation(report),
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

    print(f"\n{'ID':<6}{'GRADE':<11}{'RES':<11}{'FPS':<7}{'DECL':<8}{'VID s':<7}{'PLATES':<8}"
          f"{'PLATE px':<10}{'px/m':<8}{'LIGHT':<11}EPOCHS")
    print("-" * 95)
    for c in report:
        if not c.get("reachable"):
            print(f"{c['id']:<6}{'UNREACHABLE':<11}{c.get('error','')}")
            continue
        decl = c.get("declared_fps")
        print(
            f"{c['id']:<6}{c.get('capability_grade',''):<11}{c.get('resolution',''):<11}"
            f"{str(c.get('measured_fps') or '-'):<7}"
            f"{(f'{decl:g}' if decl else '-')[:7]:<8}"
            f"{str(c.get('video_seconds_analysed') or '-'):<7}"
            f"{str(c.get('plates_observed', '-')):<8}"
            f"{str(c.get('plate_width_px_median') or '-'):<10}"
            f"{str(c.get('pixel_density_px_per_m') or '-'):<8}"
            f"{str(c.get('lighting') or '-'):<11}{c.get('epochs_seen','-')}"
        )
    print("-" * 95)
    print(f"{len(graded)}/{len(report)} reachable · ANPR-capable: {payload['anpr_capable']} · {counts}")
    print(f"written: {args.out}")


if __name__ == "__main__":
    main()
