# Sentinel — local sandbox replica + camera profiler

Day-0 scaffold for the Gujarat Police Innovation Hackathon 2026.

The organiser's grid is behind login. This lets you build and test the entire
ingest path **before** you have credentials, against a grid that behaves the
same way — same ports, same looping, same GOP-replay burst on connect.

## Why this works

The Sentinel sandbox publishes on RTSP `8554`, HLS `8888`, WebRTC/WHEP `8889`.
Those are MediaMTX defaults, so a local MediaMTX instance reproduces the
protocol behaviour their Resources page warns about:

- GOP replay on connect (first 1–2 s arrive faster than real time)
- non-uniform frame intervals
- hard scene cut at the loop point
- mixed H.264 / H.265, mixed resolutions

Write your client against this, and switching to the real host is a one-line
config change.

## Layout

```
sentinel/
  docker-compose.yml     MediaMTX + an ffmpeg publisher per media file
  mediamtx.yml           minimal server config
  publish.sh             loops every ./media/*.mp4|mkv into /stream/<n>
  mock_catalogue.py      serves /api/ingest in the organiser's shape
  sentinel_client.py     robust RTSP capture: TCP, PTS timing, backoff, loop epochs
  profile_cameras.py     Camera Capability Index — the headline deliverable
  media/                 drop your own test clips here
```

## Run it

```bash
# 1. put 3-6 short clips in ./media  (mixed resolutions; include one HEVC file)
docker compose up -d

# 2. serve the catalogue
pip install fastapi uvicorn opencv-python-headless numpy
python mock_catalogue.py            # http://localhost:8080/api/ingest

# 3. sanity check one stream
ffplay -rtsp_transport tcp rtsp://localhost:8554/stream/1

# 4. profile the whole grid
python profile_cameras.py --host http://localhost:8080 --seconds 45
```

`profile_cameras.py` writes `capability_report.json` and prints a grade table.

## The real grid

Two hosts, and the split is the thing to get right:

| | Endpoint | Reachable via |
|---|---|---|
| **HLS** | `https://cctv.corp8.cloud/<id>/index.m3u8` | CDN, behind the access password — works on any network |
| **RTSP** | `rtsp://<email>:<password>@103.250.160.189:8554/stream/<id>` | **public IP direct** — 8554/TCP must be open outbound |
| **WHEP** | `http://<email>:<password>@103.250.160.189:8889/stream/<id>/whep` | public IP direct — 8889/TCP, 8189/UDP |

A CDN cannot proxy TCP/UDP media, so RTSP and WebRTC bypass `cctv.corp8.cloud`
entirely and hit the static IP (or `stream.corp8.cloud`). Consequences:

- **Paths are asymmetric.** HLS is `/<id>/index.m3u8` with no prefix; RTSP and
  WHEP are `/stream/<id>` with one.
- **Credentials go in the URL**, and the `@` in your email must be
  percent-encoded as `%40`. Only approved emails connect.
- **Credentials therefore leak** into logs, `ps`, tracebacks and screen
  recordings. `sentinel_auth.redact()` and `SentinelCapture.safe_url` exist for
  exactly this; use them in every log line and before you record a demo video.
- Catalogue is **`https://cctv.corp8.cloud/cameras.json`**, ids `cam01`–`cam30`.

```bash
set SENTINEL_EMAIL=you@example.com
set SENTINEL_PASSWORD=XXXX-XXXX-XXXX

python sentinel_auth.py                       # login + catalogue + transport probe
python profile_cameras.py --auth --seconds 45 # profile the real grid
python profile_cameras.py --auth --transport hls --seconds 45   # force HLS
```

If 8554 is blocked on your network the probe says so and falls back to HLS.
HLS delivers in segments, so PTS granularity is coarser and latency higher —
keep every time-derived metric on PTS regardless.

## Registry + GIS (Model 1)

Metadata only — no video, and no credential, ever. Cameras carry a catalogue
key, never a URL with an embedded password.

```bash
docker compose up -d postgis                       # PostGIS on 127.0.0.1:55432
python -m registry.seed --catalogue                # 30 cameras from the grid catalogue
python -m registry.seed --capability capability_report.json
uvicorn registry.api:app --port 8090               # UI + API on http://127.0.0.1:8090
```

What it models that a camera list usually doesn't:

- **Analog cameras hang off a node.** A DVR / NVR / encoder is its own row with
  its channel count, and the database refuses an analog camera without one.
- **Private cameras need consent.** `owner_type='private'` without a
  `consent_ref` is rejected by a check constraint, not by a hopeful UI check,
  and consent has an expiry that shows up on the worklist before it lapses.
- **A location is a claim until surveyed.** `location_confidence` is one of
  surveyed / approx_area / approx_city / unknown. The grid catalogue ships no
  coordinates at all, so every point here is derived from the camera's name and
  labelled accordingly — 6 of 30 could not be placed at all.
- **A capability grade is a claim until a human looks.** The plate detector
  fires on signage, taillights and on-screen timestamps, so `evidence_verified`
  stays false until somebody checks the crops.
- **Every read is purpose-bound.** Send `X-Purpose-Ref: <FIR/DD number>`; it
  lands in `audit_log` with the rows returned.

Onboarding has three doors: `POST /api/cameras` (one, from the form),
`POST /api/cameras/bulk` (a department's CSV — see
`registry/samples/private_cameras.csv`), and `POST /api/capability`
(a `capability_report.json` from the profiler).

`GET /api/worklist` is the gap-analysis report Model 1 asks for: what is
missing, per camera, as a list somebody can work through.

The map deliberately ships **no basemap tiles**: OSM's public tile servers
forbid this kind of load and police networks are egress-restricted, so it
renders offline from a plain SVG projection. Self-hosted tiles drop in behind
the markers without touching the rest of the UI.

## Route reconstruction — the graded test case

A registration goes in, a timestamped location-wise route comes out.

```bash
python -m routes.cli GJ01DM4242 --purpose FIR/2026/0142 --json out/route.json
curl -H "X-Purpose-Ref: FIR/2026/0142" localhost:8090/api/routes/GJ01DM4242
# or trace it from the map UI
```

Four things it does that ordering sightings by time does not:

- **Matches the way OCR actually fails.** Reads off this grid differ from the
  true plate in exactly the characters OCR confuses (O/0, I/1, B/8, S/5). An
  exact-match query answers "never seen" for a vehicle that was seen four
  times, so matching is confusion-aware and every sighting carries its match
  kind and score. Fuzzy sightings are labelled in the report and drawn amber on
  the map.
- **Folds the loop.** The sandbox replays footage on a cycle, so the same
  journey recurs. Passes with the same camera sequence collapse into one route
  and the repeat count is reported as a loop artifact, not deleted.
- **Checks the route is possible.** Implied speed comes from real distance over
  real time. Over 150 km/h is flagged — unless the leg crosses a loop boundary,
  where the clock restarts and any speed is meaningless.
- **Carries its evidence.** Each sighting reports the camera's capability
  grade, plate width in pixels, how many frames voted, and whether a second
  frame corroborated it. A route built from single unconfirmed reads is
  labelled weak, because it is.

Distances are straight-line by default, which understates road distance and so
keeps the impossible-journey check conservative. Set `OSRM_URL` to a
self-hosted OSRM and the same code uses road distances instead.

`python -m routes.demo_data --insert` adds a synthetic journey for testing,
tagged `source='synthetic-demo'` and removable with `--purge`; real grid reads
are never mixed with it.

## Watchlist and alerts

```bash
python -m watchlist.cli load --csv watchlist/samples/representative.csv
python -m watchlist.cli scan          # check every unseen read
python -m watchlist.cli follow        # keep checking as reads arrive
python -m anpr.worker --camera cam06 --seconds 60 --alerts   # or inline
python -m watchlist.cli alerts
```

Three rules, each a rights decision as much as a technical one:

- **A fuzzy match is a lead, never a confirmation.** Matching has to tolerate
  OCR's confusions or it misses real hits — but a fuzzy hit on a stolen-vehicle
  circulation means a citizen with a similar plate gets stopped. Those alerts
  are raised at reduced priority, flagged `needs_verification`, and the
  operator view says so in words: *"Fuzzy plate match — a lead, not a
  confirmation. Verify the crop before acting."*
- **Alerts deduplicate.** A vehicle at a signal is read repeatedly and the
  sandbox replays the same pass every loop; repeats raise `hit_count` on the
  open alert instead of filling the queue. `SENTINEL_DEDUPE_MINUTES` widens the
  window above the loop period when demonstrating against the sandbox.
- **Watchlist entries expire.** Matching respects `valid_from`/`valid_until`,
  so a circulation nobody withdrew stops generating stops. Withdrawal
  deactivates rather than deletes: why a vehicle was circulated is audit trail.

Priority is 1–100 from severity, adjusted by evidence — exact vs fuzzy match,
corroborated vs single read, the camera's capability grade, the plate's pixel
width — and every alert stores the sentence explaining how it got its number.

`watchlist/sources.py` holds the integration adapters. Manual and CSV work.
**VAHAN and eGujCop/CCTNS are designed, not connected**: each declares its
endpoint, authentication, request and response shape, refresh cadence and what
is still needed (NIC client certificate, GSWAN placement), and raises rather
than returning fabricated or silently empty data. AFIS and NAFIS are
fingerprint systems and carry no vehicle data, so they are out of scope for
this watchlist and belong to the person side of an investigation.

## Pre-submission checklist (from the organiser's Resources page)

- [x] Every client forces RTSP over TCP
- [x] No timing logic depends on `CAP_PROP_FPS` or frame arrival time
- [x] Inter-frame gaps do not crash or stall the pipeline
- [x] Reconnect with backoff, tested by restarting a feed
- [x] Decoder warnings on join are logged, not fatal
- [x] Camera list and properties read from `/api/ingest`
- [x] Handles mixed H.264 / H.265 and mixed resolutions
- [x] Behaviour is sane across a scene discontinuity

Keep this list in the repo. It maps 1:1 to their checklist, and a reviewer who
opens the repo sees you built against it deliberately.
