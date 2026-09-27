# Demo runbook

Two videos are required:

1. **Own-feed demo, 2–3 minutes** — the platform running against our own feed.
2. **Government-feed demo** — the platform running against the organiser's
   cameras, delivered with the plate/timestamp report.

Record with the Xbox Game Bar (`Win + G` → record) or OBS. 1080p. Speak over it;
a silent screen recording is harder to follow and the jury is watching many.

**Before either recording, read this once:** credentials appear in RTSP URLs. The
platform redacts them everywhere, but do not open a terminal with
`SENTINEL_PASSWORD` echoed, and do not show the `.env` or environment variables
on screen. Check the frame before you hit record.

---

## Setup, once, before recording

```bash
# 1. database and replica
docker compose up -d postgis mediamtx publisher

# 2. seed the registry from the real catalogue, plus the capability sweep
python -m registry.seed --catalogue --capability docs/capability_report_2026-09-21.json

# 3. import the grid plate reads from the recorded run
python -m registry.import_reads out/grid_reads.jsonl      # if available
#    (otherwise the reads are already in the database from the worker run)

# 4. watchlist
python -m watchlist.cli load --csv watchlist/samples/representative.csv
set SENTINEL_DEDUPE_MINUTES=180
python -m watchlist.cli scan --from-start

# 5. the platform
python -m uvicorn registry.api:app --host 127.0.0.1 --port 8090
```

Open `http://127.0.0.1:8090` and check the map, the alert queue and a vehicle
trace all render before you start recording.

---

## Video 1 — own-feed demo (2–3 minutes)

Our own feed is the local replica: MediaMTX serving clips we control on the
same ports and with the same behaviour as the grid. This is the honest reading
of "own feed", and it lets the pipeline be shown end to end without spending
the organiser's metered watch time.

| Time | Show | Say |
|---|---|---|
| 0:00–0:20 | `docker compose ps`, then the replica's catalogue at `/api/ingest` | "Three cameras, mixed resolutions, one H.265, published on the same ports as the grid. The catalogue is the contract — nothing hard-codes a URL." |
| 0:20–0:50 | `python profile_cameras.py --host http://127.0.0.1:8080 --seconds 20` | "Every camera is profiled before it is trusted. Frame rate measured from presentation timestamps, never from what the camera claims — one grid camera claims ninety thousand frames per second." |
| 0:50–1:20 | The capability table output | "Graded against IEC 62676-4. Too few plates seen means UNGRADED, not DETECT — no evidence and measured-as-poor are different claims." |
| 1:20–2:00 | `python -m anpr.worker --file <a clip> --camera 1 --sample-ms 200` | "Detect the vehicle, track it with a motion model driven by real timestamps, find the plate inside the vehicle crop, read it on every frame, then vote per character." |
| 2:00–2:40 | The registry UI: map, filters, a camera's capability history | "Model 1 is the spine. Analog cameras hang off a DVR record; private cameras need a consent reference the database enforces." |
| 2:40–3:00 | `python -m routes.cli GJ01DM4242` | "A registration in, a timestamped route out." |

---

## Video 2 — government-feed demo

| Time | Show | Say |
|---|---|---|
| 0:00–0:25 | `python sentinel_auth.py` (output is redacted) | "Thirty cameras from the organiser's catalogue. RTSP over TCP on the public media IP — the CDN cannot proxy it. Credentials never reach a log line." |
| 0:25–1:10 | `out/capability_clean.json` summary, or the capability table | "Twenty-nine of thirty reachable. Zero ANPR-capable by the DORI standard. Eight cameras delivering corrupt frames, four delivering nothing usable at all. This is measured, not assumed." |
| 1:10–1:50 | A corrupt frame next to a clean one from cam06 | "These decode without error. OpenCV reports them as good reads, and an object detector called an empty road a potted plant. We gate on frame integrity and rejoin the stream, which took that camera from 75% corrupt to 17%." |
| 1:50–2:40 | `python -m anpr.worker --file out/grid/cam06.mp4 --camera cam06 --alerts` | "Real grid footage. Every read is listed, and most are rejected — the plates are 30 to 60 pixels wide and the characters are not in the image. Where they were large enough, it read them correctly." |
| 2:40–3:20 | The alert queue in the UI; acknowledge one; point at a fuzzy alert | "A fuzzy match is a lead, never a confirmation. Acting on one stops the wrong driver, so it is flagged and its priority is reduced." |
| 3:20–4:00 | Trace a plate; the route on the map; the plain-text report | "Timestamped, location-wise, with the evidence behind every sighting and an honest note where the evidence is thin." |
| 4:00–4:20 | `docs/plate_report.md` | "And the full plate report with timestamps, as submitted." |

---

## If Docker will not start

The registry, routes and alerts need PostGIS. If Docker Desktop is stuck:

- Open the Docker Desktop window and clear whatever prompt it is showing
  (sign-in, update, WSL update). It will not start headless with a prompt open.
- `wsl --update` then restart Docker Desktop, if it complains about WSL.

The ANPR pipeline, the profiler and the plate report do **not** need the
database — `python -m routes.report --from-jsonl out/grid_reads.jsonl
--capability docs/capability_report_2026-09-21.json` produces the report
regardless. Record video 1's pipeline sections and video 2's capability and
ANPR sections while the database is down, and add the UI sections after.

---

## Hosted URL

```bash
python -m uvicorn registry.api:app --host 127.0.0.1 --port 8090
cloudflared tunnel --url http://127.0.0.1:8090
```

`cloudflared` prints a `https://<random>.trycloudflare.com` URL. Put that in the
submission with the test credentials, and say plainly that it is a development
tunnel available during judging rather than production hosting — a jury
respects that more than a dead link.

**Before exposing anything publicly, confirm:** the API has no authentication
in front of it yet. It exposes camera locations and plate reads. Either put the
platform behind the login you add, or keep the tunnel up only while judging and
say so in the submission.
