# Sentinel — Technical Proposal / High-Level Design

**Gujarat Police Innovation Hackathon 2026 · Category 1**
Statewide CCTV integration, ANPR and real-time alerting

Version 0.9 (draft) · 21 September 2026

---

## 1. Summary

Sentinel integrates heterogeneous CCTV estates across Gujarat's departments
into one registry, correlates the video that is worth correlating against
watchlist databases, and raises alerts an operator can act on.

The submission is a **hybrid architecture**: a compulsory Model 1 registry and
GIS spine, a Model 3 federation layer that talks to departmental VMS platforms,
and Model 2 direct-connect adapters for cameras that have no VMS in front of
them. Model 4 — full central ingest — is rejected on arithmetic set out in §4.

The design rests on one finding, measured on the organiser's own grid rather
than assumed: **most cameras in a general-purpose CCTV estate physically cannot
read a number plate.** Of 30 cameras profiled, plates legible to the human eye
appeared on 3. A platform that assumes otherwise produces confident nonsense at
scale. Sentinel measures what each camera can actually resolve, says so, and
still extracts usable intelligence from the cameras that cannot read a plate.

---

## 2. What the platform does

| Capability | State |
|---|---|
| Central camera registry with GIS, multi-department, analog and IP | Implemented |
| Camera Capability Index — grades cameras by measured optics | Implemented |
| Onboarding: manual, CSV bulk, API, private cameras under consent | Implemented |
| ANPR: vehicle detection → tracking → plate detection → OCR → multi-frame vote | Implemented |
| Frame integrity gate (rejects corrupt frames before analysis) | Implemented |
| Route reconstruction with loop folding and plausibility checks | Implemented |
| Watchlist matching with prioritised, deduplicated alerts | Implemented |
| Operator dashboard: map, capability, alerts, vehicle trace | Implemented |
| Purpose-bound queries and append-only audit log | Implemented |
| VAHAN / SARTHI / eGujCop (CCTNS) integration | **Designed, not connected** |
| Face recognition | **Designed, not implemented** — see §11 |
| Cross-camera appearance re-identification | **Designed, not implemented** |
| Evidence export with BSA §63 certificate | **Designed, partially implemented** |

Nothing in the "implemented" column is a mock-up. Every figure quoted in this
document was measured against the organiser's grid and is reproducible from the
repository.

---

## 3. Architecture

```
                    ┌────────────────────────────────────────────┐
                    │  Operator surface                          │
                    │  map · capability · alerts · vehicle trace │
                    └───────────────────┬────────────────────────┘
                                        │ purpose-bound, RBAC
                    ┌───────────────────▼────────────────────────┐
   MODEL 1 SPINE    │  Registry + GIS (PostgreSQL + PostGIS)     │
   metadata only    │  cameras · nodes · capability · audit      │
                    │  plate reads · watchlist · alerts          │
                    └───────▲───────────────────────▲────────────┘
                            │ metadata                │ metadata
                ┌───────────┴──────────┐   ┌──────────┴───────────┐
   MODEL 3      │ Federation adapters  │   │ Edge ANPR workers    │  MODEL 2
   middleware   │ per departmental VMS │   │ RTSP/ONVIF direct    │  direct
                └───────────▲──────────┘   └──────────▲───────────┘
                            │                          │
                 departmental VMS platforms      cameras, DVRs,
                 (Milestone, Genetec, CP Plus)   encoders, private
```

**Video does not move to the centre.** Workers sit close to the cameras, read
the streams, and emit metadata: a plate read is ~250 bytes. Frames leave the
edge only when an operator requests evidence for a specific case, and that
request is logged against an FIR/DD reference.

### 3.1 Why hybrid, and not one model

- **Model 1 alone** is a map and an asset list. It cannot answer the test case.
- **Model 2 alone** requires the platform to speak every camera vendor's
  protocol and to reach every camera directly. It does not survive a department
  that already runs a VMS and will not open its cameras.
- **Model 3 alone** requires every department to have a VMS. Many have a DVR in
  a cupboard.
- **Model 4** is rejected on arithmetic (§4).

A real estate contains all of these situations at once, so the platform
supports all of them and records, per camera, which path it arrived by.

---

## 4. Why not central ingest (Model 4)

Order-of-magnitude figures from stated assumptions, for an 80,000-camera estate:

| Quantity | Assumption | Result |
|---|---|---|
| Central ingest | 2 Mbps per camera | ~160 Gbps sustained |
| 30-day retention | 21.6 GB per camera per day | ~1.73 PB/day → ~52 PB |
| ANPR on every stream | ~20 streams per GPU | ~4,000 GPUs |
| **Metadata-only federation** | ~250 B per detection, ~10 M/day | **~2.5 GB/day → ~1 TB/year** |

The difference is five orders of magnitude in storage and roughly four in
network. Measured on this grid, the case is stronger still: **the ANPR figure
assumes every stream is worth analysing, and on this estate most are not.**
Spending 4,000 GPUs to read plates from cameras that cannot resolve a plate is
the expensive way to produce nothing.

Sentinel's ANPR capacity is therefore allocated by capability grade, not
uniformly: Identify-grade cameras get continuous ANPR, lower grades get
vehicle-class and direction analytics, and the remediation plan (§6) says what
it would cost to upgrade a camera that matters.

---

## 5. What we measured on the grid

Profiled 2026-09-19 and 2026-09-21 against `cctv.corp8.cloud`, 30 cameras,
20–40 s per camera. Method and raw output: `profile_cameras.py`,
`out/capability_clean.json`.

### 5.1 Access and transport

- RTSP over TCP on `103.250.160.189:8554`, reachable; UDP transport corrupts
  frames across NAT and is never used.
- HLS via the CDN is available as a fallback where 8554 is blocked.
- The catalogue carries **two fields**, `id` and `name`. No coordinates, no
  department, no codec, no resolution, no frame rate. Every other attribute in
  the registry comes from our own profiling or from department onboarding.
- **A watch-time quota applies per account.** After roughly 29 minutes of
  cumulative streaming the grid returns HTTP 403 on every endpoint and 401 on
  RTSP until a cooldown expires. This is not documented by the organiser. It
  shapes operations: development runs against a local replica, and grid time is
  spent deliberately.

### 5.2 Stream reality

| Observation | Measured |
|---|---|
| Declared frame rate vs delivered | Declared 30 on feeds delivering 15; one camera declares 250, another 90,000 |
| Delivered frame rate range | 0.5 – 30 fps |
| Inter-frame gaps | 20 ms to 2,040 ms; stalls to 10 s |
| GOP replay on connect | ~19 frames arriving in ~100 ms, 0.64–1.28 s of content ahead of live |
| **Frame corruption (H.265, mid-GOP join)** | **8 of 30 cameras**; 4 delivered no usable frame at all |

The corruption finding matters beyond stream health. Corrupt frames decode
"successfully" — OpenCV reports them as good reads — and an object detector
answers them confidently: on an empty road it reported *potted plant*,
*airplane* and *boat*. Any platform that does not gate on frame integrity is
generating detections from noise. Sentinel measures saturation and flat-block
fraction against each camera's own rolling baseline, rejects the frame, and
rejoins the stream when corruption persists, because a desynchronised decoder
never recovers on its own. That took one camera from 75% corrupt to 17%.

Measured across the estate on 21 September 2026, with the gate active: 29 of 30
cameras reachable, **8 delivering corrupt frames**, and **4 (cam07, cam09,
cam17, cam22) delivering nothing usable at all** — every analysed frame
rejected. Those four are engineering faults, not optics faults, and the
remediation plan says so.

### 5.3 What the cameras can actually read

Eyes-on verification of every plate crop, not detector counts:

- **Graded capability, 30 cameras, 20 s each: 0 Identify, 1 Recognise,
  5 Observe, 23 ungraded** for want of evidence in the window. Nothing on this
  estate is ANPR-capable by the DORI standard.
- **Legible plates: 3 of 30 cameras.** Elsewhere plates are 24–66 px wide with
  estimated character heights of 7–13 px. Reliable OCR needs roughly 15–20 px.
- Where plates were large enough (97–124 px), OCR read them correctly and
  repeatedly — `GJ32K9870` exactly, and a second plate consistently across five
  frames.
- **Plate detectors alone cannot grade a camera.** The detector fired at
  confidence 0.70–0.84 on a vehicle's rooftop unit, on taillight bars, on
  on-screen timestamp text and on shop signage. Sentinel rejects detections
  that hold the same pixels over time and those whose aspect ratio is not a
  plate, and marks every grade unverified until a human has seen the crop.

This is the evidence behind §1: the constraint on statewide ANPR is optics, not
compute or software.

---

## 6. Camera Capability Index

Every camera is graded on pixel density at the plate against **IEC 62676-4:2014
DORI**: Detect 25, Observe 63, Recognise 125, Identify 250 px/m. Plates are the
ruler because their physical size is fixed by CMVR.

- px/m is a **lower bound**: every plate is assumed to be the widest CMVR plate
  (500 mm single-line). Typing plates by aspect ratio was implemented, tested
  and withdrawn — at low resolution a blurred single-line plate gets a tall box,
  reads as two-line, and inflates the grade.
- Cameras with too few plates observed are **UNGRADED**, not DETECT. "No
  evidence" and "measured as poor" are different claims.
- A camera whose stream is corrupt is flagged **FIX_STREAM**, not RE_LENS:
  re-lensing would fix nothing.

Output is a costed remediation list — re-aim, re-lens, replace, fix stream —
with the multiplier each camera needs to reach Identify grade. This answers
Model 1's "gap-analysis reports for ageing infrastructure" with measurements
instead of a green dot.

---

## 7. ANPR pipeline

```
frame → integrity gate → vehicle detection → tracking → plate detection
      → OCR per frame → vote across the track → validated read
```

- **All timing from presentation timestamps.** Never wall clock, never declared
  frame rate. The tracker's motion model takes a real time delta, because this
  grid's frame intervals vary by two orders of magnitude and a frame-counting
  tracker computes impossible velocities after every stall.
- **Multi-frame voting** is per character position, weighted by the OCR's own
  per-character probabilities, followed by a format repair that only swaps
  characters whose class is wrong for a candidate Indian plate mask. Ten poor
  reads that disagree resolve to one correct string; the vote does not require
  any single frame to have produced it.
- **A read is "usable" only if** the format is a valid Indian registration,
  more than one frame contributed, and the characters agreed. Everything else
  is stored, flagged, and kept out of route reconstruction.
- **Loop discontinuities reset tracking state.** The sandbox replays footage on
  a cycle; carrying tracks across a hard cut invents journeys.

Measured throughput on CPU: ~1.0–1.45 s per analysed frame (vehicle detection
~580–765 ms, plate detection ~370–670 ms, OCR ~45 ms). This is an honest
figure, not a target: it sizes the edge hardware in §10 and is the first thing
a GPU changes.

---

## 8. Route reconstruction — the graded test case

A registration goes in; a timestamped, location-wise route comes out, with the
evidence behind every sighting.

1. **Confusion-aware matching.** Reads differ from the true plate in exactly
   the characters OCR confuses (O/0, I/1, B/8, S/5). An exact-match query
   reports "never seen" for a vehicle that was seen. Fuzzy sightings are
   labelled and scored, never silently merged.
2. **Loop folding.** Passes sharing a camera sequence collapse into one
   canonical route; repeats are reported as loop artifacts rather than deleted.
   Without this the sandbox reports a vehicle driving the same road all night.
3. **Plausibility.** Implied speed from real distance over real time; over
   150 km/h is flagged — except across a loop boundary, where the clock
   restarts and the number is meaningless. Cameras with no confirmed location
   produce an unknown-distance leg rather than an invented one.
4. **Evidence travels with the route.** Capability grade, plate pixel width,
   frames voted, corroboration, and a blunt summary: a route built from single
   unconfirmed reads is labelled weak.

Distances are straight-line by default, which understates road distance and so
keeps the plausibility check conservative. A self-hosted OSRM Match service
gives road distances through the same interface.

---

## 9. Watchlist and alerting

- **A fuzzy match is a lead, never a confirmation.** Fuzzy hits are raised at
  reduced priority, flagged for verification, and the operator surface says so
  in words. Acting on a fuzzy hit stops the wrong driver.
- **Alerts deduplicate.** Repeat sightings raise a hit count rather than
  filling the queue — a vehicle at a signal, or the sandbox replaying a pass.
- **Entries expire.** A circulation nobody withdrew stops generating stops.
  Withdrawal deactivates rather than deletes; why a vehicle was circulated is
  audit trail.
- **Priority explains itself.** 1–100 from severity, adjusted by the evidence
  behind the read, with the reasoning stored per alert so a supervisor can
  argue with the weighting.

---

## 10. Deployment and scale

### 10.1 Placement

| Tier | Runs | Sizing driver |
|---|---|---|
| Edge worker | ANPR for a cluster of cameras | GPU throughput; one worker per site or per DVR |
| Federation adapter | One per departmental VMS | API rate limits of that VMS |
| Core | Registry, GIS, routes, watchlist, alerts, audit | Metadata volume — ~1 TB/year at 10 M detections/day |

Edge workers hold no permanent state; they emit metadata and buffer locally if
the core is unreachable. Camera credentials never leave the tier that needs
them and are never written to logs, exports or recordings.

### 10.2 Phased rollout

1. **Registry first.** Onboard cameras, establish locations, run the capability
   sweep. This alone produces the estate's first honest gap analysis.
2. **ANPR where it can work.** Deploy workers against Identify-grade cameras.
3. **Remediate.** Execute the costed re-aim/re-lens/replace list for corridors
   that matter.
4. **Extend.** Federation adapters per department; private-camera onboarding
   under consent.

### 10.3 Open source and licensing

Every component is open source and vendor-neutral, and the licence of each
model and library is recorded in the repository's licence table. Two honest
caveats are stated there rather than hidden: the plate detector weights are
YOLOv9-architecture (upstream GPL-3.0; no network clause, so server-side use
triggers no obligation), and those weights appear trained on Latin-American
plates, which is why Indian plate validation was done explicitly and reported.

---

## 11. Security, privacy and evidence

- **Purpose-bound queries.** Tracing a vehicle carries an FIR/DD reference into
  an append-only audit log alongside the rows returned.
- **RBAC with department scoping** and row-level tenancy: a department sees its
  own cameras by default.
- **No credentials in artifacts.** RTSP URLs embed credentials; they are
  redacted in every log line, report and recording, and the repository is
  scanned before each commit.
- **Consent for private cameras.** A private camera without a consent reference
  is rejected by a database constraint, and consent expiry is surfaced before it
  lapses.
- **Evidence export** seals a clip and its metadata with SHA-256 and a
  pre-filled certificate under **Section 63 of the Bharatiya Sakshya Adhiniyam
  2023**, which requires dual signature — the person in charge and an expert —
  unlike the old IEA §65B.
- **Face recognition is designed, not implemented.** The test case is
  vehicle-centric, and deploying face recognition across a public estate raises
  proportionality questions that a hackathon submission should not quietly
  answer for the state. The HLD sets out where it would attach and what
  safeguards would gate it.

---

## 12. Limitations

Stated plainly, and kept current in `LIMITATIONS.md`:

- ANPR is viable on a small minority of this estate's cameras. That is the
  finding, not a defect to be hidden.
- OCR throughput on CPU is ~1 frame/second; real-time multi-camera operation
  needs GPU.
- Road distances need a self-hosted OSRM; straight-line distance is used until
  it is deployed.
- Government system integrations are designed and declared, not connected.
- Camera locations derived from names are approximate and labelled as such:
  8 of 30 to an identifiable junction, 16 to a town, 6 not placeable at all.
- One camera's catalogue name does not match the location shown in its own
  video overlay. Names are labels, not locations.

---

## 13. Repository

`github.com/<org>/sentinel` — client, profiler, registry, ANPR, routes,
watchlist, HLD, limitations, licence table, and the measured outputs behind
every figure in this document.
