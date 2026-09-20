# CLAUDE.md — Sentinel / Gujarat Police Innovation Hackathon 2026

Context for any Claude session working in this repo. Read fully before writing code.

---

## 1. The mission

Build a deployment-ready platform that integrates heterogeneous CCTV cameras
across Gujarat's 26 government departments, correlates live video against
watchlist databases, and generates AI-powered real-time alerts.

**Submission deadline: 28 September 2026.** Finale 12–13 October 2026 at
i-Hub Gujarat, Gandhinagar. Results 13 October.

**Solo developer.** Scope accordingly — see §8.

Portal: https://sentinel.gujarat.gov.in · Grid: https://cctv.corp8.cloud
Organiser: Home Department / State Crime Records Bureau (SCRB), Gujarat.
Tech partner i-Hub Gujarat; knowledge partners NFSU and DA-IICT.
Helpdesk: +91 95370 89982 · sentinel.hackathon@gujarat.gov.in (Mon–Sat 10–18 IST)

---

## 2. Rules that constrain every design decision

Verified against the official portal, not reconstructed.

**Five reference models.** Model 1 is **compulsory** and must be combined with
at least one other, or with a hybrid/custom architecture.

| Model | What it is |
|---|---|
| 1 | Centralised CCTV Registry & GIS. Metadata only — no central streaming or recording. **Mandatory foundation.** |
| 2 | Unified viewing: platform connects **directly** to each departmental CCTV/VMS via RTSP/ONVIF/SDK. No middleware. |
| 3 | VMS federation: a **middleware layer** talks to departmental VMS platforms and exposes one interface downstream. |
| 4 | Central VMS: full centralisation of ingest, storage, playback and GPU analytics. |
| 5 | Hybrid / innovative — explicitly permitted. |

**We are submitting Hybrid: Model 1 spine + Model 3 federation + Model 2
direct-connect adapters. Model 4 is explicitly rejected on arithmetic** (see §6).
Do not drift from this — the form answer, deck and HLD must agree.

**Hard requirements**

- ANPR is the mandatory analytic. Face recognition is *expected in the HLD* but
  not required for the test case (which is vehicle-centric). We do not implement FR.
- Integration with VAHAN, SARTHI, eGujCop (CCTNS), AFIS, NAFIS must be
  **designed and integration-ready**, not connected. Demo against our own
  representative watchlist.
- Must support onboarding **private** public-facing cameras (societies, malls,
  commercial establishments) "wherever feasible and permitted". Rarely built by
  competitors — a cheap differentiator.
- Cameras are **analog and IP**. Model DVR/encoder as a first-class registry node.
- Open-source technologies required. Vendor-neutral, no lock-in, documented APIs.
- **Mock-ups, animations and concept videos are explicitly rejected.** Working
  backend mandatory.

**The graded test case.** A vehicle registration number is handed over on the
day. Produce its complete **timestamped, location-wise route** across the
integrated cameras, plus continuous watchlist cross-referencing with automated
real-time alerts.

**Seven evaluation areas:** Successful Test Case · Solution Presentation ·
Solution Architecture · Working Platform & Demonstration · Video Analytics
Output · Scalability & PoC Readiness · Submission Completeness.

**Bonus consideration** (the organiser's own differentiation menu): innovative
hybrid architecture · advanced cross-camera tracking · analytics beyond ANPR ·
edge processing and bandwidth optimisation · cybersecurity, privacy,
auditability, RBAC · dashboards, alerts, health monitoring, integration-ready APIs.

> Bonus features cannot compensate for a failed mandatory requirement.
> **Ship the test case first.**

**Prizes.** ₹51L pool. Phase 1 Category 1: 1st ₹4L / 2nd ₹2L / 3rd ₹1L.
Top 3 per category → 6 finalists. Phase 2 (category-blind): 1st **₹16L** /
2nd ₹8L / 3rd ₹7L. We are Category 1.

---

## 3. The camera grid — access model

**Two hosts.** This is the single most common integration mistake.

| | Endpoint | Reachable via |
|---|---|---|
| HLS | `https://cctv.corp8.cloud/<id>/index.m3u8` | CDN, behind access password. Works on any network. |
| RTSP | `rtsp://<email>:<password>@103.250.160.189:8554/stream/<id>` | **Public IP direct.** Needs 8554/TCP outbound. |
| WHEP | `http://<email>:<password>@103.250.160.189:8889/stream/<id>/whep` | Public IP direct. 8889/TCP, 8189/UDP. |

A CDN cannot proxy TCP/UDP media, so RTSP and WebRTC bypass `cctv.corp8.cloud`
entirely. `stream.corp8.cloud` is an alias for the media IP.

- **Paths are asymmetric.** HLS is `/<id>/index.m3u8` (no prefix). RTSP/WHEP are
  `/stream/<id>` (with prefix).
- **Catalogue: `https://cctv.corp8.cloud/cameras.json`.** Ids `cam01`…`cam30`.
  (The *public* resources page says `/api/ingest`; the logged-in guide says
  `cameras.json`. Try `cameras.json` first, fall back.)
- Credentials embedded in the URL; `@` in the email must be `%40`. Only emails
  on the approved access list connect.
- Web login is **session-cookie** based, and unauthenticated requests return
  **HTTP 200 serving the login page**, not 401. Never trust `resp.ok` — sniff
  the body for login markers.
- HLS off the CDN needs the session cookie passed to FFmpeg.
- **Watch-time quota per account (found 2026-09-19, not documented by the
  organiser).** After ~29 min of cumulative stream time every endpoint returns
  HTTP 403 text/plain "watch time limit reached — please wait for your
  cooldown, then watch again" (login POST included), and RTSP DESCRIBE returns
  **401** — looks like bad credentials, isn't. Cooldown length unknown.
  `sentinel_auth` raises `WatchQuotaExceeded`; `quota_exhausted()` is a free
  HTTP check. **Grid minutes are a budget:** develop on the local replica,
  spend grid time only on validation, and keep quota in hand for the test-case
  day and the government-feed demo recording.

**Credentials live in `SENTINEL_EMAIL` / `SENTINEL_PASSWORD` env vars. Never
hard-code, never commit, never log.** RTSP URLs carry them in plaintext and leak
into logs, `ps`, tracebacks and **demo screen recordings**. Use
`sentinel_auth.redact()` / `SentinelCapture.safe_url` everywhere.

**What the feeds actually are:** ~12 hours of recorded CCTV from 30+ cameras
across five departments (Health, Police, GSRTC, Panchayat, Municipal
Corporation), synchronised on a common timeline and replayed as simulated live
streams by a Python middleware. Consistent and repeatable by design.

---

## 4. Traps in the grid — all stated by the organiser, all handled in `sentinel_client.py`

| Trap | Consequence | Handling |
|---|---|---|
| UDP transport | Corrupt frames across NAT that look like model bugs | Force `rtsp_transport;tcp` before `cv2` import |
| `CAP_PROP_FPS` | Declared fps ≠ delivered; any speed/dwell metric derived from it is wrong | Ignore entirely; measure from PTS |
| **GOP replay on connect** | Gateway replays a buffered GOP, so the first 1–2 s arrive faster than real time. Trackers timestamping by arrival compute impossible velocities on every reconnect | `Frame.realtime=False` during the burst; feed Kalman/ByteTrack **PTS deltas** |
| Non-uniform frame intervals | Pipelines treat gaps as disconnects | Tolerate; motion models use elapsed PTS |
| Feed restarts | Tight reconnect loops | Exponential backoff 2 s → 30 s |
| Mixed H.264/H.265 | `Error constructing the frame RPS` on mid-stream join | Non-fatal; log and continue to first IDR |
| Non-uniform grid | Fixed-shape inference batches fail | Read per-camera properties from the catalogue |
| **Loop discontinuity** | Each feed loops; hard scene cut like a camera reboot | `Frame.epoch` increments — reset background models, re-ID gallery, track ids |
| No file download | `/stream/<id>` answers range requests, so `curl` yields a partial file that *looks* complete | Build against live capture only |
| Per-client stream copies | Opening all 30 saturates you and the gateway | Profile sequentially; close captures |
| **Don't publish to the gateway** | — | Consume only. Never call their control API. |
| **Watch-time quota** *(unstated — found on first run)* | ~29 min cumulative → 403 on web, 401 on RTSP until cooldown | `WatchQuotaExceeded`; profiler aborts the sweep instead of retrying |

**The one they don't state:** because footage loops, the designated vehicle
repeats the same journey every cycle. Naive route reconstruction reports it
traversing the identical route endlessly. **Fold repeated passes by loop epoch**
and present one canonical route, noting repeats as loop artifacts.

---

## 5. Model and library decisions — do not change without reading this

**Licensing is a design constraint, not a footnote.** This platform is pitched
at state procurement.

| Job | Chosen | Licence |
|---|---|---|
| Vehicle/object detection | RF-DETR via `open-image-models` (`rf-detr-small-512-coco`) | Apache-2.0 (nano→large only) |
| Plate detection | `open-image-models` `yolo-v9-s-608-license-plate-end2end` (mAP50 0.966) | MIT pkg, YOLOv9-derived |
| Plate OCR | `fast-plate-ocr` `cct-xs-v2-global` (~0.47 ms) | MIT |
| Wiring | `fast-alpr` | MIT |
| Tracking | ByteTrack — **from `ifzhang/ByteTrack`** | MIT |
| Cross-camera re-ID | FastReID | Apache-2.0 |
| Fallback OCR | PaddleOCR PP-OCRv6 | Apache-2.0 |
| Local sandbox | MediaMTX | MIT |
| Map-matching | OSRM (self-hosted), `Match` service | BSD-2-Clause |

**Banned:**
- **Ultralytics YOLOv8/YOLO11** — AGPL-3.0. Network clause forces disclosure of
  the entire platform. Do not `pip install ultralytics`.
- **BoxMOT** — AGPL-3.0. It is the convenient tracker wrapper; use ByteTrack directly.
- **RF-DETR XL / 2XL** — PML 1.0, paid `rfdetr_plus`. Stay within nano→large.
- **tile.openstreetmap.org** in production — their usage policy forbids this
  load. Self-host tiles; retain OSM attribution.

**Known caveats to state honestly, never hide:**
- The plate detectors are YOLOv9-architecture and upstream YOLOv9
  (`WongKinYiu/yolov9`) is GPL-3.0. No network clause, so server-side use
  triggers no obligation — but do not claim a spotless MIT stack.
- `open-image-models` plate weights appear trained on Argentine/LatAm plates
  (demo asset is from the author's `LocalizadorPatentes` repo). **Indian
  two-line plates on two-wheelers are the top validation risk. Test first.**
- Resolution matters: plate recall 0.917 at 608px → 0.797 at 256px. Select input
  size per camera capability grade.
- Vendor `.onnx` weights into the deployment artifact — don't fetch from GitHub
  at runtime. Police networks are egress-restricted.

**Multi-frame voting:** never OCR frame-by-frame and take the last answer.
Detect → track → read the plate on every frame of the track → vote across the
track with confidence weighting. Ten bad reads often resolve to one correct
string where a single frame never would. This is what pulls usable reads off
low-grade cameras.

---

## 6. The differentiation thesis

> Everyone else builds a platform that watches 80,000 cameras. We build the one
> that knows what each camera can **physically** see — and still produces
> intelligence from the ones that can't read a plate.

**Why Model 4 is rejected** (this arithmetic belongs in the deck):

| Quantity | Assumption | Result |
|---|---|---|
| Central ingest, 80,000 cameras | 2 Mbps each | ~160 Gbps sustained |
| 30-day retention | 21.6 GB/camera/day | ~1.73 PB/day → ~52 PB |
| ANPR on every stream | ~20 streams/GPU | ~4,000 GPUs |
| Metadata-only federation | ~250 B/detection, ~10 M/day | ~2.5 GB/day → ~1 TB/year |

Label these as order-of-magnitude estimates from stated assumptions. Replace
with measured figures once the grid is profiled.

**Four differentiators, in priority order:**

1. **Camera Capability Index (CCI)** — grade every camera by measured optics,
   not uptime. Model 1 already asks for "camera health monitoring" and
   "gap-analysis reports for ageing infrastructure"; everyone ships a green dot.
   Grade against IEC 62676-4 (2014 DORI: Detect 25 / Observe 63 / Recognise 125
   / Identify 250 px/m). An Indian 500×120 mm plate needing ~120 px width is
   **~240 px/m — plate reading is an Identify-grade task**, while municipal
   overview cameras are specified at 25–63 px/m. Output a costed remediation
   plan: re-aim / re-lens / replace. **This is the headline.**
2. **Vehicle Identity Integrity + plate-less tracking** — appearance-embedding
   tracking when plates are unreadable; clone/duplicate plate detection;
   plate–vehicle mismatch; impossible-journey flags (gate these on loop epoch or
   the looping sandbox fires them constantly).
3. **Department Integration Pack** — the actual questionnaire SCRB would send
   each of the 26 departments, per-archetype onboarding SOP, analog/encoder
   path, private-camera consent portal. "Department-wise Information
   Requirements" is a whole evaluation dimension almost everyone hand-waves.
4. **Evidence & audit chain** — SHA-256 sealed exports, append-only chain of
   custody, pre-filled **Section 63 Bharatiya Sakshya Adhiniyam 2023**
   certificate (requires **dual signature**: person in charge + expert, unlike
   old IEA §65B). Purpose-bound queries tied to FIR/DD number, department-scoped
   RBAC with row-level tenancy. NFSU is on the jury; digital forensics is their field.

**Supporting evidence for #1:** a competing public repo measured 30,539
plate-read attempts on the organiser's cameras → **58 reliable reads from 1 of
12 cameras**, at ~6.6 px/char. Most of the estate physically cannot do ANPR.

---

## 7. What exists in this repo

```
sentinel_auth.py      SentinelSession: login, cameras.json, credentialed URL
                      construction, transport probe (is 8554 open?), redact()
sentinel_client.py    SentinelCapture: TCP, PTS-driven timing, GOP-replay
                      detection, backoff reconnect, loop-epoch counter
profile_cameras.py    Camera Capability Index — the headline deliverable.
                      Emits capability_report.json + remediation plan
mock_catalogue.py     Serves a cameras.json-shaped catalogue for the local replica
docker-compose.yml    MediaMTX on 8554/8888/8889 — same ports as the real grid
mediamtx.yml          Server config
publish.sh            Loops ./media/*.mp4 into /stream/<n>, reproducing the
                      loop discontinuity and GOP burst
media/                Drop test clips here (mixed resolutions, one HEVC)
```

The local replica exists so the pipeline can be built and failures reproduced
without holding 30 live connections open. Use it for iteration; validate on the
real grid.

---

## 8. Build order (solo, tight)

**Ship in this order. Do not start a later item before an earlier one works.**

1. **Ingest proven** — `sentinel_auth.py` runs, catalogue parses, one stream
   decodes with sane PTS. *(blocking)*
2. **Capability profile** — full grid sweep, `capability_report.json`. Produces
   the headline finding. *(cheap, high value)*
3. **Registry + GIS** — PostgreSQL + PostGIS, bulk/manual/API onboarding,
   Leaflet map with department/type/status layers. *Model 1, compulsory.*
4. **ANPR worker** — RF-DETR → track → plate detect → OCR → **vote across
   track**. PTS-driven. Metadata to Redis Streams.
5. **Route reconstruction** — detections → OSRM map-match → timestamped
   location-wise history, loop-epoch folded. *This is the graded test case.*
6. **Watchlist + alerting** — representative DB, continuous matching,
   prioritised alerts, operator dashboard.
7. Differentiators 2–4 from §6, as time allows.
8. Deck + HLD + both demo videos + plates/timestamps output report.

**If behind:** cut §6 item 4, then 3, then the second federation adapter.
**Never cut route reconstruction** — it is evaluation area #1.

Deliverables required at submission: Solution Presentation (PPT/PDF) · Technical
Proposal/HLD · own-feed demo video (2–3 min) · government-feed demo video +
output report of plates with timestamps · hosted platform URL with test
credentials · public Git repo.

---

## 9. Working rules for this repo

- **The catalogue is the contract, the URL pattern is not.** Never hard-code
  camera ids or stream URLs.
- **All timing from PTS.** Never wall clock, never `CAP_PROP_FPS`.
- **Redact credentials in every log line and before recording any video.**
- **Never publish to the gateway** or call the organiser's control API.
- **Pace load** — open only cameras being processed; profile sequentially.
- Keep a `LIMITATIONS.md` and publish measured failures alongside successes.
  The strongest public submissions do this; it reads as credibility, not weakness.
- Put a **licence table** in the deck. Almost nobody will, and a government jury
  notices.
- Prefer honest scoping over claims. "Designed, not implemented in Phase 1" is a
  legitimate and respected position in an HLD.

---

## 10. Open questions — resolve on first real run

First real run: **2026-09-19, home network**, RTSP. cam01–03 at 20 s, then two
full sweeps (30 × 20 s; 30 × 40 s — the second cut off at cam22 by the
watch-time quota, see §3). Crops in `out/` (gitignored; real plates).

**Grid-wide finding, eyes-on (the defensible version of the CCI headline):**
across both sweeps, **legible plates appeared on only 3 of 30 cameras** —
cam06 Timbavadi gate (EV and two-wheeler plates, 71–117 px), cam07 (one
night-IR plate, 113 px), cam12 Adalaj toll plaza (a vehicle stopped at the
booth). Real but illegible plates (glare, blur, ~7–10 px characters): cam01,
02, 05, 13, 15, 16. ~15 cameras showed no plates at all in 20–40 s. cam10
never produced a decodable frame (both sweeps). **Automatic grading from the
plate detector is not trustworthy on this grid** — it fired at conf 0.7–0.84
on a vehicle's rooftop unit (cam04 → false IDENTIFY), on taillight bars
(cam13), on-screen date/timestamp text (cam16), and signage (cam01, 03, 14).
Static-rejection filters help but can't catch a moving non-plate. **Next:
grade on OCR-validated reads** (fast-plate-ocr + Indian registration regex +
multi-frame vote) — i.e. "did we actually read a plate here", which is the
literal definition of ANPR capability and reuses the step-4 stack.

- [x] **Is 8554 reachable, or HLS-only?** Reachable. Authenticated RTSP decodes
      on cam01–03; `netstat` during capture shows 1 TCP socket to :8554 and
      0 UDP, so `rtsp_transport;tcp` via `OPENCV_FFMPEG_CAPTURE_OPTIONS` works
      on Windows. :8889 also open. Re-probe on any new network (venue, police LAN).
- [x] **Camera count / `cameras.json` fields.** 30 cameras. `/cameras.json`
      works (no `/api/ingest` fallback needed): a bare JSON list of
      `{"id": "camNN", "name": "..."}` — **only those two fields.** No department,
      location, codec, resolution or fps; all must come from our own registry
      and profiling. Name numbers diverge from ids after cam20 (`cam21` =
      "23 Patan Dethali Char Rasta", `cam22` = "28 …", `cam30` has no number) —
      key on `id`, never parse the name. Names indicate sites spread across
      Gujarat (Ahmedabad, Junagadh, Gir Somnath, Rajkot, Navsari/Bilimora,
      Patan, Dehgam, Gandhidham …), i.e. geographic clusters hundreds of km
      apart — a route realistically lives inside one cluster.
- [x] **Declared vs measured fps.** Declared 30.0 on all three. Measured from
      PTS: cam01 **15.0**, cam02 30.0, cam03 **21–25 and varying between runs**
      (source drops frames). All H.264 (no HEVC among these three).
- [x] **`plates_observed`.** Wide views *do* see plates — they can't resolve them.
      cam01: 7 detections, median 34 px wide (p90 43); cam02: 18, median 49 px
      (p90 66); cam03: 0. **0 of 3 ANPR-capable** (needs ≥120 px); est. char
      height 11–13 px. Crops checked by eye: real plates (incl. yellow commercial),
      zero legible characters. Supports the CCI thesis, with a twist worth
      putting on the slide: failure is *resolution*, not *coverage* — plates are
      in frame, so re-lens/re-aim is a real remediation, not just "replace".
      **Detector caveats:** conf median 0.3–0.37 (model default threshold 0.25);
      fires on fixed signage (cam01 orange board, cam03 Gujarati wall sign) —
      without the static-rejection filter the board alone graded cam01
      RECOGNISE. `plates_observed` counts detections on sampled frames, not
      unique vehicles.
- [ ] **Loop length.** Unresolved. `epochs_seen` = 1 on every 20 s window —
      that is *no evidence*, not *no loop*. The epoch counter only sees a PTS
      step backwards inside one RTSP session. Blind to (a) publishers that keep
      PTS monotonic across loops (`ffmpeg -stream_loop` does — our own
      `publish.sh` included) and (b) loops implemented as a publisher restart,
      which surface as disconnect → reconnect. Needs a content-based detector
      (frame-fingerprint recurrence) and one long watch of a single camera.
      Sweep 2 did record PTS backsteps: cam08 2 epochs, cam11 3, cam21 3 within
      40 s — all on feeds that were stalling at the time (0.5–9 fps), so more
      likely publisher restarts/glitches than 40 s loops. Unverified. A long
      single-camera watch now costs quota — do it only once, deliberately.
- [~] **Do two-line Indian plates detect?** **Detection: yes** — a two-wheeler's
      two-line plate on cam06 was detected (conf up to 0.74) and is legible by
      eye at ~66 px; yellow commercial and green EV plates detect too. OCR
      still untested. `pixel_density_px_per_m` is a lower bound (assumes 500 mm);
      typing plates by box aspect was tried and reverted — blurred single-line
      plates get tall boxes and read as two-line, inflating grades.
- [ ] **Measured fps across the grid:** declared fps is junk on several feeds
      (cam04 declares 250, cam19 **90000** — a leaked timebase, cam26
      13.4167). Delivered fps ranges 0.5–30 and drifted down during the second
      sweep (cam21 4.7 → 0.5, cam20 7.7 → 1.9) — gateway load or pre-quota
      throttling; unknown which.
- [x] **GPS per camera?** No. Locations must be geocoded from names and
      confirmed at registry onboarding (Model 1 manual/bulk path).

**Also measured:**
- **GOP replay is real:** every connect delivers ~19 frames within ~100 ms,
  0.64–1.28 s of content ahead of live. The original skew-vs-first-frame
  detector could never clear this (lead stays constant after the burst);
  replaced with a pace latch in `sentinel_client.py`. Consumers must skip
  analysis while `realtime` is False, or their own slowness hides the burst.
- **Source stalls:** inter-frame gaps up to 2,040 ms (cam03); one 10 s FFmpeg
  read timeout (cam02); cam01/cam03 each reconnected twice in one run and zero
  times in others. PTS restarts per session, so any span/fps must be
  accumulated per (reconnect, epoch) segment.
- **Login quirks (fixed in `sentinel_auth._is_login_page`):** protected paths
  302 → `/auth/login` then 200; page is bare `text/html` with no charset
  (requests mis-decodes as ISO-8859-1, mangling "Sign in · Sentinel"); form
  markers sit ~7.7 KB into the body. Login: POST `/auth/login`, fields
  `email` + `password`, no CSRF token, session cookie `sentinel`.
- **onnxruntime-gpu** is installed but falls back to CPU (`cublas64_12.dll`
  missing). Fine for profiling; fix before the ANPR worker.
