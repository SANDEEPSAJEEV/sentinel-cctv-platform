"""ANPR worker: read one camera, emit voted plate reads.

    # local replica
    python -m anpr.worker --host http://127.0.0.1:8080 --camera 1 --seconds 60

    # the real grid (spends watch-time quota — see CLAUDE.md §3)
    python -m anpr.worker --auth --camera cam06 --seconds 60

    # a file, for validating against your own footage
    python -m anpr.worker --file media/my_clip.mp4

Sinks: JSONL always (out/plate_reads.jsonl), PostgreSQL when the registry
database is reachable, Redis Streams when REDIS_URL is set. The database is
the one route reconstruction reads from.

Timing is PTS throughout. Frames are sampled on media time, not frame count,
so a 5 fps feed and a 30 fps feed get the same analysis density.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Iterator

from anpr.pipeline import AnprPipeline, PlateEvent

log = logging.getLogger("anpr.worker")


# ------------------------------------------------------------------- sinks
class JsonlSink:
    def __init__(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path
        self.count = 0

    def write(self, event: PlateEvent) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.as_dict()) + "\n")
        self.count += 1

    def close(self) -> None:
        pass


class PostgresSink:
    """Writes tracks and voted reads into the registry database."""

    def __init__(self, camera_ref: str) -> None:
        from registry import db

        self.db = db
        rows = db.query("SELECT id, code FROM camera WHERE external_ref = %s OR code = %s",
                        (camera_ref, camera_ref))
        if not rows:
            raise LookupError(f"camera {camera_ref!r} is not in the registry — "
                              "seed it first (python -m registry.seed --catalogue)")
        self.camera_id = rows[0]["id"]
        self.camera_code = rows[0]["code"]
        self.count = 0

    def write(self, event: PlateEvent) -> None:
        with self.db.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO vehicle_track
                       (camera_id, track_key, epoch, vehicle_class, frames,
                        first_pts_ms, last_pts_ms, first_seen_at, last_seen_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (camera_id, track_key) DO UPDATE SET
                       frames = EXCLUDED.frames, last_pts_ms = EXCLUDED.last_pts_ms,
                       last_seen_at = EXCLUDED.last_seen_at
                   RETURNING id""",
                (self.camera_id, event.track_key, event.epoch, event.vehicle_class,
                 event.frames, event.first_pts_ms, event.last_pts_ms,
                 event.observed_at, event.observed_at),
            )
            track_id = cur.fetchone()["id"]
            cur.execute(
                """INSERT INTO plate_read
                       (camera_id, track_id, plate, confidence, reads, agreeing_reads,
                        valid_format, repaired, usable, vehicle_class, plate_width_px,
                        char_height_px, epoch, pts_ms, observed_at, model, source)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (self.camera_id, track_id, event.plate, event.confidence, event.reads,
                 event.agreeing_reads, event.valid_format, event.repaired, event.usable,
                 event.vehicle_class, event.plate_width_px, event.char_height_px,
                 event.epoch, event.best_pts_ms, event.observed_at, event.model,
                 "anpr.worker"),
            )
            conn.commit()
        self.count += 1

    def close(self) -> None:
        self.db.audit("anpr_run", actor="anpr.worker", object_type="camera",
                      object_id=self.camera_code, detail={"plate_reads": self.count})


class RedisSink:
    """Optional. The architecture puts metadata on a stream so that many camera
    workers can run at the edge and one consumer persists centrally."""

    def __init__(self, url: str, stream: str = "sentinel.plate_reads") -> None:
        import redis

        self.client = redis.Redis.from_url(url)
        self.stream = stream
        self.count = 0

    def write(self, event: PlateEvent) -> None:
        self.client.xadd(self.stream, {k: str(v) for k, v in event.as_dict().items()})
        self.count += 1

    def close(self) -> None:
        self.client.close()


# ------------------------------------------------------------------ source
class GridSource:
    """Live capture the worker can talk back to.

    The pipeline can tell it that frames have gone bad — decoder desync emits
    plausible-looking frames indefinitely — and the only cure is to rejoin the
    stream and wait for a keyframe.
    """

    def __init__(self, capture: Any) -> None:
        self.capture = capture

    def frames(self, seconds: float) -> Iterator[Any]:
        yield from self.capture.frames(max_seconds=seconds)

    def request_reconnect(self, reason: str) -> None:
        self.capture.request_reconnect(reason)


def open_grid(args: argparse.Namespace) -> Any:
    from sentinel_client import SentinelCapture, catalogue, rtsp_url_of

    url, cap_opts = None, None
    if args.auth:
        from sentinel_auth import SentinelSession, camera_id_of

        session = SentinelSession()
        session.login()
        cams = session.catalogue()
        match = [c for c in cams if camera_id_of(c) == args.camera]
        if not match:
            raise SystemExit(f"camera {args.camera} is not in the catalogue")
        transport = args.transport or session.choose_transport()
        url = session.stream_url(camera_id_of(match[0]), transport)
        cap_opts = session.capture_options(transport)
        log.info("grid camera %s over %s", args.camera, transport)
    else:
        cams = catalogue(args.host)
        match = [c for c in cams if str(c.get("id")) == args.camera]
        if not match:
            raise SystemExit(f"camera {args.camera} is not in {args.host}")
        url = rtsp_url_of(match[0])

    capture = SentinelCapture(url, capture_options=cap_opts)
    capture.__enter__()
    return capture


def frames_from_file(path: str) -> Iterator[Any]:
    """Replay a local file with the same Frame shape the grid client yields.

    If anpr.record wrote a PTS sidecar next to the clip, replay on those
    timestamps: a recorded container has a fixed nominal fps, which would hide
    exactly the non-uniform intervals the tracker has to cope with.
    """
    import cv2

    from sentinel_client import Frame

    sidecar_path = path + ".pts.json"
    sidecar: list[dict[str, Any]] = []
    if os.path.exists(sidecar_path):
        with open(sidecar_path, encoding="utf-8") as fh:
            sidecar = json.load(fh).get("pts", [])
        log.info("replaying %s on %d recorded timestamps", os.path.basename(path), len(sidecar))

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise SystemExit(f"cannot open {path}")
    index, last = 0, None
    while True:
        ok, image = cap.read()
        if not ok:
            break
        if index < len(sidecar):
            pts = float(sidecar[index]["pts_ms"])
            # A rejoin is a scene change as far as tracking is concerned: PTS
            # restarts and whatever was mid-frame is gone. Fold the recorder's
            # session counter into the epoch so the tracker resets there.
            epoch = int(sidecar[index].get("epoch", 0)) + int(sidecar[index].get("session", 0))
        else:
            pts, epoch = float(cap.get(cv2.CAP_PROP_POS_MSEC) or 0.0), 0
        gap = 0.0 if last is None else pts - last
        last = pts
        yield Frame(image=image, pts_ms=pts, epoch=epoch, index=index, realtime=True, gap_ms=gap)
        index += 1
    cap.release()


# -------------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser(description="Sentinel ANPR worker")
    src = ap.add_argument_group("source")
    src.add_argument("--camera", help="camera id in the catalogue")
    src.add_argument("--file", help="a local video file instead of a stream")
    src.add_argument("--host", default="http://127.0.0.1:8080", help="catalogue host")
    src.add_argument("--auth", action="store_true", help="use the real grid (spends quota)")
    src.add_argument("--transport", choices=["rtsp", "hls"], default=None)
    src.add_argument("--seconds", type=float, default=60.0)

    proc = ap.add_argument_group("processing")
    proc.add_argument("--sample-ms", type=float, default=100.0,
                      help="analyse one frame per this much media time")
    proc.add_argument("--vehicle-model", default="rf-detr-small-512-coco")
    proc.add_argument("--plate-model", default="yolo-v9-s-608-license-plate-end2end")
    proc.add_argument("--ocr-model", default="cct-xs-v2-global-model")
    proc.add_argument("--plates-only", action="store_true",
                      help="skip vehicle detection and track plates directly")
    proc.add_argument("--min-plate-px", type=float, default=20.0)
    proc.add_argument("--corrupt-limit", type=int, default=20,
                      help="analysed frames of corruption before rejoining the stream")

    out = ap.add_argument_group("output")
    out.add_argument("--jsonl", default="out/plate_reads.jsonl")
    out.add_argument("--no-db", action="store_true", help="skip the PostgreSQL sink")
    out.add_argument("--redis", default=os.environ.get("REDIS_URL"))
    out.add_argument("--alerts", action="store_true",
                     help="check each read against the watchlist and raise alerts inline")
    out.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    if not args.file and not args.camera:
        ap.error("give --camera or --file")

    camera_ref = args.camera or os.path.basename(args.file)
    sinks: list[Any] = [JsonlSink(args.jsonl)]
    if not args.no_db and args.camera:
        try:
            sinks.append(PostgresSink(camera_ref))
        except Exception as exc:
            log.warning("database sink unavailable (%s) — JSONL only", exc)
    if args.redis:
        try:
            sinks.append(RedisSink(args.redis))
        except Exception as exc:
            log.warning("redis sink unavailable (%s)", exc)

    pipeline = AnprPipeline(
        camera=camera_ref,
        vehicle_model=None if args.plates_only else args.vehicle_model,
        plate_model=args.plate_model,
        ocr_model=args.ocr_model,
        min_plate_px=args.min_plate_px,
    )

    capture = None if args.file else open_grid(args)
    frames = frames_from_file(args.file) if args.file else capture.frames(max_seconds=args.seconds)
    started = time.monotonic()
    emitted = 0
    last_sample = float("-inf")
    last_epoch: int | None = None
    skipped = 0

    alert_cursor = 0

    def emit(event: PlateEvent) -> None:
        nonlocal emitted, alert_cursor
        emitted += 1
        for sink in sinks:
            try:
                sink.write(event)
            except Exception as exc:
                log.error("sink %s failed: %r", type(sink).__name__, exc)

        # Watchlist matching runs against the stored read, so it sees the same
        # row an analyst would later — no second code path for "live" hits.
        if args.alerts:
            try:
                from watchlist.engine import scan

                raised, alert_cursor = scan(alert_cursor)
                for a in raised:
                    log.warning("ALERT %s: %s seen as %s on %s — %s, priority %d%s",
                                a.alert_id, a.plate_wanted, a.plate_read, a.camera_code,
                                a.severity, a.priority,
                                " (needs verification)" if a.needs_verification else "")
            except Exception as exc:
                log.error("watchlist scan failed: %r", exc)
        mark = "OK " if event.usable else "?? "
        log.info("%s%-12s conf=%.2f reads=%d/%d %s width=%.0fpx %s",
                 mark, event.plate, event.confidence, event.agreeing_reads, event.reads,
                 "valid" if event.valid_format else "INVALID-FORMAT",
                 event.plate_width_px, event.observed_at.strftime("%H:%M:%S"))

    try:
        for frame in frames:
            if not frame.realtime:          # inside the GOP-replay burst
                continue
            # PTS restarts at every rejoin and every loop, so the sampling
            # clock has to restart with it — otherwise the first session's
            # timestamps sit in the future and everything after is skipped.
            if frame.epoch != last_epoch:
                last_epoch, last_sample = frame.epoch, float("-inf")
            if frame.pts_ms - last_sample < args.sample_ms:
                skipped += 1
                continue
            last_sample = frame.pts_ms
            for event in pipeline.process(frame.image, pts_ms=frame.pts_ms, epoch=frame.epoch):
                emit(event)

            # Frames still arriving, but they are no longer pictures of the
            # scene. Waiting does not fix a desynced decoder; rejoining does.
            if capture is not None and pipeline.gate.corrupt_streak >= args.corrupt_limit:
                capture.request_reconnect(
                    f"{pipeline.gate.corrupt_streak} corrupt frames in a row")
                pipeline.gate.reset()
    except KeyboardInterrupt:
        log.warning("interrupted")
    finally:
        for event in pipeline.flush():
            emit(event)
        if capture is not None:
            capture.close()
        for sink in sinks:
            try:
                sink.close()
            except Exception:
                pass

    wall = time.monotonic() - started
    summary = pipeline.summary()
    usable = "—"
    print(json.dumps({"camera": camera_ref, "wall_seconds": round(wall, 1),
                      "events": emitted, "frames_skipped_by_sampling": skipped,
                      **summary}, indent=2))
    if emitted == 0:
        print("\nNo plate read. That is a result, not a failure: on this grid most "
              "cameras cannot resolve a plate at all (CLAUDE.md §10).", file=sys.stderr)


if __name__ == "__main__":
    main()
