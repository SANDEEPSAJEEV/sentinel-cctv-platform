# Limitations

Measured failures, published alongside the successes. Everything here is
reproducible from this repository.

Last updated 21 September 2026.

---

## The estate cannot mostly do ANPR

Of 30 cameras on the organiser's grid, **plates legible to the human eye
appeared on 3**. Elsewhere plates are 24–66 px wide with estimated character
heights of 7–13 px, against the 15–20 px reliable OCR needs.

This is the platform's central finding, not a defect in it. Where plates were
large enough (97–124 px) the pipeline read them correctly and repeatedly.

## Four cameras deliver nothing a computer can use

Re-measured 21 September 2026 with the integrity gate active: 29 of 30 cameras
reachable, **8 delivering corrupt frames**, and **4 — cam07, cam09, cam17,
cam22 — where every analysed frame was rejected as corrupt.** Graded capability
across the estate: 0 Identify, 1 Recognise, 5 Observe, 23 ungraded for want of
evidence in a 20-second window.

## A plate detector cannot grade a camera by itself

On real frames the detector fired at confidence 0.70–0.84 on:

- a vehicle's rooftop ventilation unit (graded a camera IDENTIFY until caught)
- taillight bars
- the camera's own on-screen date text
- shop signage in Gujarati

Mitigations implemented: reject detections that hold the same pixels across
time, reject aspect ratios no plate has, and mark every grade unverified until
a human has seen the crop. Detector-only grades remain provisional.

## Corrupt frames decode without error

An H.265 stream joined mid-GOP emits grey smears that OpenCV reports as
successful reads. One camera produced **75% corrupt frames over 60 seconds**,
and an object detector called an empty road *potted plant*, *airplane*, *boat*.

Mitigation: a frame integrity gate and rejoin-on-corruption, which took that
camera to 17% corrupt. Consequence: **capability figures measured before
2026-09-21 are contaminated** and were re-measured.

## Throughput on CPU is not real time

~1.0–1.45 s per analysed frame (vehicle detection ~580–765 ms, plate detection
~370–670 ms, OCR ~45 ms). Multi-camera real-time operation needs GPU; the
CUDA libraries are not installed on the development machine, so every figure
here is CPU-only.

## Tracking fragments on small fast objects

One motorcycle became three tracks (`GHW6709`, `GHX6709`, `GHW6799`), which
splits the multi-frame vote and weakens each fragment. Cross-track
re-identification would merge them; it is designed, not implemented.

## Distances are straight-line

OSRM is wired in behind a configuration flag but not deployed, so route legs
use great-circle distance. This understates road distance, which keeps the
impossible-journey check conservative — it will not invent a violation — but
it also cannot confirm a plausible-looking route followed a real road.

## Camera locations are inferred, not surveyed

The grid catalogue carries no coordinates. All 30 points were derived from
camera names: **8 to an identifiable junction, 16 only to a town, 6 not
placeable at all.** Each is labelled with its confidence, and the unplaceable
ones are onboarding tasks rather than hidden rows.

One camera's catalogue name (`06 Timbavadi gate-Junagadh`) does not match the
location in its own video overlay (`Madhuram Bypass Road Fix-2`). Catalogue
names are labels, not locations.

## Government integrations are not connected

VAHAN and eGujCop/CCTNS adapters declare their endpoint, authentication,
request and response shape and refresh cadence, and raise rather than return
fabricated or silently empty data. They need an NIC client certificate and
GSWAN network placement respectively. AFIS and NAFIS carry no vehicle data and
are out of scope for this watchlist.

## Face recognition is not implemented

Designed in the HLD; deliberately not built. The graded test case is
vehicle-centric, and deploying face recognition across a public estate raises
proportionality questions a hackathon submission should not answer on the
state's behalf.

## The grid meters watch time

Roughly 29 minutes of cumulative streaming exhausts an account, after which
every endpoint returns 403 and RTSP returns 401 until a cooldown — which looks
exactly like bad credentials. Undocumented by the organiser. Development
therefore runs against a local replica.

## Demonstration data is labelled

Route reconstruction and alerting are demonstrated against a synthetic journey
tagged `source='synthetic-demo'`, removable with one command, because real
reads come from one camera. It is never mixed with grid data in reports.
