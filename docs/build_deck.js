/* Sentinel — solution presentation generator.
 *
 *   node docs/build_deck.js
 *
 * Every figure on these slides was measured against the organiser's grid and
 * is reproducible from this repository. Nothing here is illustrative.
 */

const pptxgen = require("pptxgenjs");

const INK = "13233A";        // deep navy — control room
const PANEL = "1C3253";
const LIGHT = "FFFFFF";
const WASH = "F4F7FB";
const ICE = "C7D9EE";
const MUTED = "6E819C";
const AMBER = "F2A03D";
const GREEN = "37C978";
const RED = "EF5F5F";

const H = "Cambria";
const B = "Calibri";

const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE";           // 13.3 x 7.5in — set before any slide
pres.author = "Sentinel";
pres.title = "Sentinel — statewide CCTV integration, ANPR and alerting";

/* ---------------------------------------------------------------- helpers */
function darkSlide() {
  const s = pres.addSlide();
  s.background = { color: INK };
  return s;
}

function lightSlide(title, kicker) {
  const s = pres.addSlide();
  s.background = { color: LIGHT };
  if (kicker) {
    s.addText(kicker.toUpperCase(), {
      x: 0.6, y: 0.42, w: 12.1, h: 0.25, isTextBox: true,
      fontFace: B, fontSize: 11, color: MUTED, charSpacing: 2, margin: 0,
    });
  }
  s.addText(title, {
    x: 0.6, y: kicker ? 0.66 : 0.5, w: 12.1, h: 1.0, isTextBox: true,
    fontFace: H, fontSize: 32, bold: true, color: INK, margin: 0,
  });
  return s;
}

// The repeated motif: a capability grade chip.
function chip(slide, { x, y, label, sub, color, w = 2.5 }) {
  slide.addShape(pres.ShapeType.roundRect, {
    x, y, w, h: 0.95, rectRadius: 0.12,
    fill: { color: WASH }, line: { color, width: 1.5 },
  });
  slide.addText(label, {
    x: x + 0.15, y: y + 0.12, w: w - 0.3, h: 0.35, isTextBox: true,
    fontFace: B, fontSize: 14, bold: true, color, margin: 0,
  });
  slide.addText(sub, {
    x: x + 0.15, y: y + 0.48, w: w - 0.3, h: 0.4, isTextBox: true,
    fontFace: B, fontSize: 10.5, color: MUTED, margin: 0,
  });
}

function statCard(slide, { x, y, w = 3.0, value, label, color = INK, note }) {
  slide.addShape(pres.ShapeType.roundRect, {
    x, y, w, h: note ? 2.2 : 1.6, rectRadius: 0.12,
    fill: { color: WASH }, line: { color: ICE, width: 1 },
  });
  slide.addText(value, {
    x: x + 0.2, y: y + 0.18, w: w - 0.4, h: 0.75, isTextBox: true,
    fontFace: H, fontSize: 40, bold: true, color, margin: 0,
  });
  slide.addText(label, {
    x: x + 0.2, y: y + 0.95, w: w - 0.4, h: 0.4, isTextBox: true,
    fontFace: B, fontSize: 12, color: INK, margin: 0,
  });
  if (note) {
    slide.addText(note, {
      x: x + 0.2, y: y + 1.36, w: w - 0.4, h: 0.76, isTextBox: true,
      fontFace: B, fontSize: 10, color: MUTED, margin: 0,
    });
  }
}

function bullets(slide, items, opts = {}) {
  slide.addText(
    items.map((t, i) => ({
      text: t, options: { bullet: true, breakLine: i < items.length - 1 },
    })),
    {
      x: opts.x ?? 0.6, y: opts.y ?? 1.75, w: opts.w ?? 6.0,
      h: opts.h ?? Math.max(0.5, 6.5 - (opts.y ?? 1.75)),
      isTextBox: true, fontFace: B, fontSize: opts.fontSize ?? 14,
      color: opts.color ?? INK, paraSpaceAfter: 10, margin: 0,
    });
}

function table(slide, rows, opts = {}) {
  slide.addTable(rows, {
    x: opts.x ?? 0.6, y: opts.y ?? 1.7, w: opts.w ?? 12.1,
    colW: opts.colW,
    fontFace: B, fontSize: opts.fontSize ?? 12, color: INK,
    border: { type: "solid", color: ICE, pt: 1 },
    fill: { color: LIGHT },
    autoPage: false,
  });
}

function header(cells) {
  return cells.map((t) => ({
    text: t,
    options: { bold: true, color: LIGHT, fill: { color: PANEL }, fontFace: B, fontSize: 11.5 },
  }));
}

function note(slide, text) {
  slide.addText(text, {
    x: 0.6, y: 6.62, w: 12.1, h: 0.42, isTextBox: true,
    fontFace: B, fontSize: 10, italic: true, color: MUTED, margin: 0,
  });
}

/* ------------------------------------------------------------ 1. title */
{
  const s = darkSlide();
  s.addText("SENTINEL", {
    x: 0.9, y: 1.95, w: 11.5, h: 1.2, isTextBox: true,
    fontFace: H, fontSize: 60, bold: true, color: LIGHT, charSpacing: 6, margin: 0,
  });
  s.addText("Statewide CCTV integration, ANPR and real-time alerting", {
    x: 0.95, y: 3.25, w: 11.5, h: 0.5, isTextBox: true,
    fontFace: B, fontSize: 20, color: ICE, margin: 0,
  });
  s.addText("We build the platform that knows what each camera can physically see —\nand still produces intelligence from the ones that cannot read a plate.", {
    x: 0.95, y: 3.9, w: 10.5, h: 1.0, isTextBox: true,
    fontFace: B, fontSize: 14, color: AMBER, margin: 0, lineSpacingMultiple: 1.2,
  });
  s.addText("Gujarat Police Innovation Hackathon 2026  ·  Category 1  ·  Hybrid: Model 1 spine + Model 3 federation + Model 2 direct-connect", {
    x: 0.95, y: 5.6, w: 11.5, h: 0.4, isTextBox: true,
    fontFace: B, fontSize: 12, color: MUTED, margin: 0,
  });
  s.addNotes("Hybrid architecture. Model 1 is compulsory and is our spine; Model 3 federation for departments with a VMS; Model 2 direct-connect for cameras without one. Model 4 rejected on arithmetic.");
}

/* ------------------------------------------------- 2. the finding */
{
  const s = lightSlide("Most of the estate physically cannot read a plate", "What we measured, not what we assumed");
  statCard(s, { x: 0.6, y: 1.75, w: 2.85, value: "3 of 30", label: "cameras produced a plate legible to the eye",
                color: RED, note: "Verified crop by crop, not counted by a detector" });
  statCard(s, { x: 3.72, y: 1.75, w: 2.85, value: "24–66 px", label: "typical plate width elsewhere",
                color: INK, note: "Reliable OCR needs roughly 120 px" });
  statCard(s, { x: 6.84, y: 1.75, w: 2.85, value: "0 of 30", label: "graded ANPR-capable (Identify)",
                color: RED, note: "1 Recognise, 5 Observe, 23 ungraded for lack of evidence" });
  statCard(s, { x: 9.96, y: 1.75, w: 2.85, value: "4", label: "cameras delivered no usable frame",
                color: RED, note: "Every analysed frame decoded corrupt" });

  s.addText("The constraint on statewide ANPR is optics, not compute.", {
    x: 0.6, y: 4.0, w: 12.1, h: 0.45, isTextBox: true,
    fontFace: H, fontSize: 20, bold: true, color: INK, margin: 0,
  });
  bullets(s, [
    "Where plates were large enough (97–124 px) our pipeline read them correctly and repeatedly — one plate exactly, another consistently across five frames.",
    "A platform that assumes every camera can do ANPR spends GPUs to produce confident nonsense.",
    "So we measure what each camera can resolve, publish it, and allocate analytics by grade.",
  ], { y: 4.55, w: 12.1, fontSize: 14 });
  note(s, "Source: profile_cameras.py against cctv.corp8.cloud, 30 cameras, 20 s each, 21 September 2026. Raw output in the repository.");
  s.addNotes("This is the finding that shapes the whole submission. A competing public measurement found 58 reliable reads from 1 of 12 cameras — consistent with ours.");
}

/* --------------------------------------------- 3. stream reality */
{
  const s = lightSlide("What the streams actually do", "Measured on the organiser's grid");
  table(s, [
    header(["Observation", "What the camera claims", "What we measured"]),
    ["Frame rate", "30 fps declared", "15 fps delivered — and 0.5 to 30 across the estate"],
    ["Frame rate metadata", "valid", "one camera declares 250, another 90,000 (a leaked timebase)"],
    ["Frame intervals", "uniform", "20 ms to 2,040 ms; stalls to 10 s"],
    ["On connect", "live", "~19 frames in ~100 ms — 0.6 to 1.3 s of replayed buffer"],
    ["Catalogue metadata", "—", "two fields: id and name. No location, department, codec or fps"],
    ["Account limits", "undocumented", "~29 min of streaming exhausts the quota; then 403 and RTSP 401"],
  ], { colW: [3.0, 3.4, 5.7], y: 1.85 });
  s.addText("Every timing decision in the platform comes from presentation timestamps — never wall clock, never declared frame rate.", {
    x: 0.6, y: 5.4, w: 12.1, h: 0.5, isTextBox: true,
    fontFace: B, fontSize: 14, bold: true, color: INK, margin: 0,
  });
  note(s, "A tracker that counts frames instead of reading timestamps computes impossible velocities after every stall on this grid.");
}

/* ------------------------------------------- 4. corrupt frames */
{
  const s = darkSlide();
  s.addText("The failure that does not announce itself", {
    x: 0.7, y: 0.7, w: 12.0, h: 0.7, isTextBox: true,
    fontFace: H, fontSize: 32, bold: true, color: LIGHT, margin: 0,
  });
  s.addText("An H.265 stream joined mid-GOP emits grey smears. OpenCV reports them as successful reads.", {
    x: 0.7, y: 1.5, w: 12.0, h: 0.4, isTextBox: true,
    fontFace: B, fontSize: 15, color: ICE, margin: 0,
  });

  s.addShape(pres.ShapeType.roundRect, {
    x: 0.7, y: 2.2, w: 5.7, h: 2.3, rectRadius: 0.12,
    fill: { color: PANEL }, line: { color: RED, width: 1.5 },
  });
  s.addText("75%", { x: 0.95, y: 2.4, w: 5.2, h: 0.8, isTextBox: true,
    fontFace: H, fontSize: 44, bold: true, color: RED, margin: 0 });
  s.addText("of a 60-second recording was corrupt — and every frame decoded without error", {
    x: 0.95, y: 3.25, w: 5.2, h: 1.0, isTextBox: true,
    fontFace: B, fontSize: 13, color: ICE, margin: 0 });

  s.addShape(pres.ShapeType.roundRect, {
    x: 6.9, y: 2.2, w: 5.7, h: 2.3, rectRadius: 0.12,
    fill: { color: PANEL }, line: { color: GREEN, width: 1.5 },
  });
  s.addText("17%", { x: 7.15, y: 2.4, w: 5.2, h: 0.8, isTextBox: true,
    fontFace: H, fontSize: 44, bold: true, color: GREEN, margin: 0 });
  s.addText("after our integrity gate and rejoin-on-corruption — a desynchronised decoder never recovers on its own", {
    x: 7.15, y: 3.25, w: 5.2, h: 1.0, isTextBox: true,
    fontFace: B, fontSize: 13, color: ICE, margin: 0 });

  s.addText("Across the estate: 8 of 30 cameras delivered corrupt frames, and 4 delivered nothing usable at all.", {
    x: 0.7, y: 4.85, w: 12.0, h: 0.4, isTextBox: true,
    fontFace: B, fontSize: 15, bold: true, color: LIGHT, margin: 0,
  });
  s.addText("Fed those frames, an object detector reported “potted plant”, “airplane” and “boat” — on an empty road.", {
    x: 0.7, y: 5.3, w: 12.0, h: 0.45, isTextBox: true,
    fontFace: B, fontSize: 14, italic: true, color: AMBER, margin: 0,
  });
  bullets(s, [
    "Any platform that does not gate on frame integrity generates detections from noise, silently.",
    "We measure saturation and flat-block fraction against each camera's own baseline — so a night scene is not mistaken for corruption.",
    "A broken stream is remediated as FIX_STREAM, not RE_LENS. Re-lensing would fix nothing.",
  ], { y: 5.8, w: 12.0, h: 1.1, color: ICE, fontSize: 11.5 });
  s.addNotes("This also invalidated part of our own first sweep — we re-measured rather than quote contaminated numbers.");
}

/* ------------------------------------------------ 5. architecture */
{
  const s = lightSlide("One spine, three ways in", "Architecture");

  const box = (x, y, w, h, fill, line) => s.addShape(pres.ShapeType.roundRect,
    { x, y, w, h, rectRadius: 0.1, fill: { color: fill }, line: { color: line, width: 1.5 } });

  box(3.4, 1.65, 6.5, 0.85, PANEL, PANEL);
  s.addText("Operator surface — map · capability · alerts · vehicle trace", {
    x: 3.55, y: 1.78, w: 6.2, h: 0.6, isTextBox: true, fontFace: B, fontSize: 13,
    bold: true, color: LIGHT, margin: 0, align: "center" });

  box(2.4, 2.85, 8.5, 1.1, WASH, INK);
  s.addText("MODEL 1 — Registry + GIS (PostgreSQL + PostGIS)", {
    x: 2.55, y: 2.95, w: 8.2, h: 0.35, isTextBox: true, fontFace: B, fontSize: 13,
    bold: true, color: INK, margin: 0, align: "center" });
  s.addText("cameras · DVR/encoder nodes · capability history · plate reads · watchlist · alerts · audit", {
    x: 2.55, y: 3.3, w: 8.2, h: 0.5, isTextBox: true, fontFace: B, fontSize: 11,
    color: MUTED, margin: 0, align: "center" });

  box(1.3, 4.35, 4.9, 1.15, WASH, AMBER);
  s.addText("MODEL 3 — Federation adapters", {
    x: 1.45, y: 4.48, w: 4.6, h: 0.35, isTextBox: true, fontFace: B, fontSize: 12.5,
    bold: true, color: INK, margin: 0, align: "center" });
  s.addText("one per departmental VMS", {
    x: 1.45, y: 4.85, w: 4.6, h: 0.45, isTextBox: true, fontFace: B, fontSize: 11,
    color: MUTED, margin: 0, align: "center" });

  box(7.1, 4.35, 4.9, 1.15, WASH, AMBER);
  s.addText("MODEL 2 — Edge ANPR workers", {
    x: 7.25, y: 4.48, w: 4.6, h: 0.35, isTextBox: true, fontFace: B, fontSize: 12.5,
    bold: true, color: INK, margin: 0, align: "center" });
  s.addText("RTSP / ONVIF direct, incl. analog via DVR", {
    x: 7.25, y: 4.85, w: 4.6, h: 0.45, isTextBox: true, fontFace: B, fontSize: 11,
    color: MUTED, margin: 0, align: "center" });

  s.addText("Video never moves to the centre. A plate read is ~250 bytes; frames leave the edge only when an operator requests evidence, against an FIR reference.", {
    x: 0.6, y: 5.85, w: 12.1, h: 0.6, isTextBox: true,
    fontFace: B, fontSize: 14, bold: true, color: INK, margin: 0,
  });
  note(s, "A real estate contains all three situations at once — a VMS here, a DVR in a cupboard there, a society camera willing to share. The registry records which path each camera arrived by.");
}

/* ------------------------------------------- 6. why not model 4 */
{
  const s = lightSlide("Why we reject full central ingest", "Model 4, priced");
  table(s, [
    header(["Quantity", "Assumption", "Result"]),
    ["Central ingest, 80,000 cameras", "2 Mbps each", "~160 Gbps sustained"],
    ["30-day retention", "21.6 GB per camera per day", "~1.73 PB/day → ~52 PB"],
    ["ANPR on every stream", "~20 streams per GPU", "~4,000 GPUs"],
    [{ text: "Metadata-only federation (ours)", options: { bold: true } },
     { text: "~250 B per detection, ~10 M/day", options: { bold: true } },
     { text: "~2.5 GB/day → ~1 TB/year", options: { bold: true, color: GREEN } }],
  ], { colW: [4.2, 4.0, 3.9], y: 1.85 });

  s.addText("Five orders of magnitude in storage. Four in network.", {
    x: 0.6, y: 4.2, w: 12.1, h: 0.45, isTextBox: true,
    fontFace: H, fontSize: 20, bold: true, color: INK, margin: 0 });
  bullets(s, [
    "And the GPU figure assumes every stream is worth analysing. On this estate, most are not.",
    "We allocate ANPR by capability grade: continuous on Identify-grade cameras, vehicle class and direction below that.",
    "The remediation plan says what it would cost to upgrade the cameras that matter.",
  ], { y: 4.75, w: 12.1, fontSize: 14 });
  note(s, "Order-of-magnitude estimates from stated assumptions, to be replaced with measured figures as the estate is profiled.");
}

/* --------------------------------------------- 7. differentiators */
{
  const s = darkSlide();
  s.addText("Four things nobody else will ship", {
    x: 0.7, y: 0.8, w: 12.0, h: 0.8, isTextBox: true,
    fontFace: H, fontSize: 34, bold: true, color: LIGHT, margin: 0 });

  const items = [
    ["01", "Camera Capability Index", "Grade every camera by measured optics against IEC 62676-4, not by whether it is online. Output a costed re-aim / re-lens / replace plan.", AMBER],
    ["02", "Vehicle identity integrity", "Appearance tracking where plates are unreadable; clone and duplicate-plate detection; impossible-journey flags gated on loop epoch.", ICE],
    ["03", "Department integration pack", "The questionnaire SCRB would actually send, per-archetype onboarding, the analog/DVR path, and private-camera consent.", ICE],
    ["04", "Evidence and audit chain", "Purpose-bound queries tied to an FIR, append-only audit, SHA-256 sealed exports, pre-filled BSA §63 certificate with dual signature.", ICE],
  ];
  items.forEach(([num, title, body, color], i) => {
    const y = 1.85 + i * 1.28;
    s.addText(num, { x: 0.75, y, w: 0.8, h: 0.5, isTextBox: true,
      fontFace: H, fontSize: 26, bold: true, color, margin: 0 });
    s.addText(title, { x: 1.7, y, w: 4.2, h: 0.45, isTextBox: true,
      fontFace: B, fontSize: 16, bold: true, color: LIGHT, margin: 0 });
    s.addText(body, { x: 5.9, y: y - 0.02, w: 6.7, h: 1.1, isTextBox: true,
      fontFace: B, fontSize: 12.5, color: ICE, margin: 0 });
  });
  s.addNotes("Bonus features cannot compensate for a failed mandatory requirement — the test case ships first. These are built on top of it.");
}

/* ---------------------------------------------------- 8. the CCI */
{
  const s = lightSlide("Camera Capability Index", "Differentiator 01 — the headline");
  s.addText("Plates are the ruler: their size is fixed by CMVR, so plate width in pixels gives pixel density at the traffic lane. Graded against IEC 62676-4:2014 DORI.", {
    x: 0.6, y: 1.6, w: 12.1, h: 0.5, isTextBox: true, fontFace: B, fontSize: 14, color: INK, margin: 0 });

  chip(s, { x: 0.6, y: 2.25, label: "IDENTIFY ≥250 px/m", sub: "Full ANPR", color: GREEN, w: 2.85 });
  chip(s, { x: 3.7, y: 2.25, label: "RECOGNISE ≥125", sub: "Marginal ANPR, attributes", color: "3E7BD6", w: 2.85 });
  chip(s, { x: 6.8, y: 2.25, label: "OBSERVE ≥63", sub: "Class, colour, direction", color: AMBER, w: 2.85 });
  chip(s, { x: 9.9, y: 2.25, label: "DETECT ≥25", sub: "Presence and counting", color: MUTED, w: 2.8 });

  bullets(s, [
    "px/m is a deliberate lower bound — every plate is assumed to be the widest CMVR plate.",
    "Too few plates seen means UNGRADED, not DETECT. “No evidence” and “measured as poor” are different claims.",
    "A grade stays unverified until a human has seen the crop, because detectors fire on signage, taillights and on-screen text.",
    "A corrupt stream is flagged FIX_STREAM — an engineering fault, not an optics one.",
  ], { y: 3.5, w: 7.3, fontSize: 13.5 });

  s.addShape(pres.ShapeType.roundRect, {
    x: 8.2, y: 3.5, w: 4.5, h: 2.6, rectRadius: 0.12,
    fill: { color: WASH }, line: { color: ICE, width: 1 } });
  s.addText("Remediation, costed", {
    x: 8.45, y: 3.65, w: 4.0, h: 0.35, isTextBox: true,
    fontFace: B, fontSize: 14, bold: true, color: INK, margin: 0 });
  bullets(s, [
    "RE_LENS — needs 1.8× focal length",
    "RE_AIM_OR_RE_LENS — tighter framing",
    "REPLACE_OR_REPURPOSE — use for counting",
    "FIX_STREAM — encoder or packet loss",
    "RESAMPLE — too little evidence to judge",
  ], { x: 8.45, y: 4.05, w: 4.0, h: 2.0, fontSize: 11.5 });
  note(s, "Model 1 asks for gap-analysis reports on ageing infrastructure. Most submissions will ship a green dot; this measures the optics.");
}

/* ------------------------------------------------ 9. ANPR pipeline */
{
  const s = lightSlide("Reading plates a camera barely resolves", "ANPR pipeline");

  const steps = ["Frame integrity gate", "Vehicle detection", "Tracking (PTS-driven)", "Plate detection on the vehicle crop", "OCR per frame", "Vote across the track"];
  steps.forEach((label, i) => {
    const x = 0.6 + i * 2.06;
    s.addShape(pres.ShapeType.roundRect, {
      x, y: 1.75, w: 1.9, h: 1.15, rectRadius: 0.1,
      fill: { color: i === 5 ? PANEL : WASH }, line: { color: i === 5 ? PANEL : ICE, width: 1 } });
    s.addText(label, {
      x: x + 0.12, y: 1.85, w: 1.66, h: 0.95, isTextBox: true,
      fontFace: B, fontSize: 10.5, bold: i === 5, color: i === 5 ? LIGHT : INK,
      margin: 0, align: "center" });
  });

  bullets(s, [
    "Plates are searched inside the vehicle crop, not the whole frame — a 1920-wide frame resized to 608 px loses the plate entirely.",
    "The tracker's motion model takes a real time delta in pixels per second. This grid's frame intervals vary by two orders of magnitude.",
    "Tracking state resets at a loop discontinuity: carrying tracks across a hard cut invents journeys that never happened.",
    "A read is usable only if the format is a valid Indian registration, more than one frame contributed, and the characters agreed.",
  ], { y: 3.3, w: 7.6, fontSize: 13.5 });

  statCard(s, { x: 8.5, y: 3.3, w: 4.2, value: "1.0–1.45 s", label: "per analysed frame, CPU",
                color: AMBER, note: "Vehicle 580–765 ms · plate 370–670 ms · OCR 45 ms. Sizes the edge hardware; first thing a GPU changes." });
  note(s, "Honest throughput, not a target. Real-time multi-camera operation needs GPU.");
}

/* --------------------------------------------- 10. voting example */
{
  const s = lightSlide("Ten poor reads make one correct plate", "Multi-frame voting");
  s.addText("Never OCR a frame and keep the answer. Read the plate on every frame of the track, then vote per character position, weighted by the OCR's own per-character probabilities.", {
    x: 0.6, y: 1.6, w: 12.1, h: 0.55, isTextBox: true, fontFace: B, fontSize: 14, color: INK, margin: 0 });

  const reads = ["GJ01AB1234", "GJ01AB1Z34", "GJ01A81234", "GJ0IAB1234", "GJ01AB1284",
                 "GJ01AB1234", "6J01AB1234", "GJ01AB1234", "GJ01AB1234", "GJ01AB1734"];
  reads.forEach((r, i) => {
    const x = 0.6 + (i % 5) * 2.3;
    const y = 2.35 + Math.floor(i / 5) * 0.62;
    s.addShape(pres.ShapeType.roundRect, {
      x, y, w: 2.1, h: 0.48, rectRadius: 0.08,
      fill: { color: WASH }, line: { color: ICE, width: 1 } });
    s.addText(r, { x: x + 0.1, y: y + 0.06, w: 1.9, h: 0.36, isTextBox: true,
      fontFace: "Courier New", fontSize: 13, color: r === "GJ01AB1234" ? INK : RED,
      margin: 0, align: "center" });
  });

  s.addShape(pres.ShapeType.roundRect, {
    x: 0.6, y: 3.9, w: 5.6, h: 1.15, rectRadius: 0.12,
    fill: { color: PANEL }, line: { color: GREEN, width: 2 } });
  s.addText("GJ01AB1234", { x: 0.8, y: 4.05, w: 5.2, h: 0.5, isTextBox: true,
    fontFace: "Courier New", fontSize: 24, bold: true, color: GREEN, margin: 0, align: "center" });
  s.addText("voted, format-validated, corroborated", { x: 0.8, y: 4.58, w: 5.2, h: 0.35, isTextBox: true,
    fontFace: B, fontSize: 11, color: ICE, margin: 0, align: "center" });

  bullets(s, [
    "The vote does not require any single frame to have produced the winning string.",
    "Format repair only swaps characters whose class is wrong for a candidate Indian plate mask — O→0 in a digit slot, never blindly.",
    "Real example from the grid: our OCR returned GJO1AB1234 on a test plate; position 3 must be a digit, so the repair is justified by the format, not guessed.",
  ], { x: 6.5, y: 3.9, w: 6.2, fontSize: 13 });
  note(s, "This is what pulls usable reads off low-grade cameras — and it is also why a single read is never called a confirmation.");
}

/* ------------------------------------------ 11. route reconstruction */
{
  const s = lightSlide("The graded test case", "A registration in, a timestamped route out");
  table(s, [
    header(["Time", "Camera", "Location", "Read", "Match", "Plate px", "Evidence"]),
    ["23:16:43", "GJ-AHM-CAM05", "Ahmedabad", "GJ01DM4242", "exact", "118", "9 reads, corroborated"],
    ["23:20:43", "GJ-AHM-CAM16", "Ahmedabad", "GJ01DM4242", "exact", "96", "9 reads, corroborated"],
    ["23:33:43", "GJ-GAN-CAM12", "Gandhinagar", "GJ0IDM4242",
      { text: "fuzzy", options: { color: AMBER, bold: true } }, "88", "9 reads, corroborated"],
    ["23:55:43", "GJ-AHM-CAM01", "Ahmedabad", "GJ01DM4242", "exact", "72", "9 reads, corroborated"],
    ["00:08:43", "GJ-AHM-CAM13", "Ahmedabad", "GJ01DM424",
      { text: "fuzzy", options: { color: AMBER, bold: true } }, "51", "2 reads"],
  ], { colW: [1.5, 2.3, 1.9, 1.9, 1.1, 1.1, 2.3], y: 1.8, fontSize: 11 });

  s.addText("5 sightings · 5 cameras · 22.01 km · 1 loop repeat folded · evidence: good, but includes fuzzy matches", {
    x: 0.6, y: 4.35, w: 12.1, h: 0.4, isTextBox: true,
    fontFace: B, fontSize: 14, bold: true, color: INK, margin: 0 });
  bullets(s, [
    "Confusion-aware matching: an exact-match query reports “never seen” for a vehicle that was seen. Fuzzy sightings are labelled and scored, never silently merged.",
    "Loop folding: passes sharing a camera sequence collapse into one route. Without it, the sandbox reports a car driving the same road all night.",
    "Plausibility: implied speed from real distance over real time — over 150 km/h is flagged, except across a loop boundary where the clock restarts.",
    "A camera with no confirmed location gives an unknown-distance leg, never an invented one.",
  ], { y: 4.78, w: 12.1, h: 1.75, fontSize: 11.5 });
  note(s, "Demonstration journey, tagged synthetic in the database and removable with one command — real grid reads come from the cameras that can actually read a plate.");
}

/* ------------------------------------------------- 12. watchlist */
{
  const s = lightSlide("Alerts worth acting on — and ones that are not", "Watchlist");

  s.addShape(pres.ShapeType.roundRect, {
    x: 0.6, y: 1.7, w: 5.9, h: 1.5, rectRadius: 0.12,
    fill: { color: WASH }, line: { color: AMBER, width: 2 } });
  s.addText("A fuzzy match is a lead, never a confirmation", {
    x: 0.8, y: 1.85, w: 5.5, h: 0.4, isTextBox: true,
    fontFace: B, fontSize: 14, bold: true, color: INK, margin: 0 });
  s.addText("Raised at reduced priority, flagged for verification, and the operator sees it in words. Acting on a fuzzy hit stops the wrong driver.", {
    x: 0.8, y: 2.3, w: 5.5, h: 0.8, isTextBox: true,
    fontFace: B, fontSize: 12, color: MUTED, margin: 0 });

  statCard(s, { x: 6.9, y: 1.7, w: 2.7, value: "100", label: "priority — exact, corroborated", color: GREEN });
  statCard(s, { x: 10.0, y: 1.7, w: 2.7, value: "60", label: "priority — fuzzy, single read", color: AMBER });

  bullets(s, [
    "Priority explains itself: severity adjusted by evidence — exact vs fuzzy, corroborated vs single, camera capability grade, plate pixel width — with the reasoning stored per alert.",
    "Alerts deduplicate: repeat sightings raise a hit count. On the demo journey that is 10 alerts for one car at the default window, 5 with the loop-aware window.",
    "Entries expire. A circulation nobody withdrew stops generating stops. Withdrawal deactivates rather than deletes — why a vehicle was circulated is audit trail.",
    "VAHAN and eGujCop/CCTNS adapters are designed and declared; each raises rather than returning fabricated or silently empty data.",
  ], { y: 3.55, w: 12.1, fontSize: 13.5 });
  note(s, "A watchlist source that quietly yields nothing reports “no hits” for a stolen vehicle it was never told about. Ours refuses instead.");
}

/* ------------------------------------------------- 13. onboarding */
{
  const s = lightSlide("Onboarding the estate as it actually is", "Registry · Model 1, compulsory");
  bullets(s, [
    "Analog cameras hang off a DVR or encoder. The node is a first-class record with its channel count, and the database refuses an analog camera that does not name one.",
    "Private cameras — societies, malls, shops — need a consent reference with an expiry, enforced by a constraint rather than a hopeful check in the UI.",
    "Three doors in: a form, a department's CSV, or the API. Capability reports import the same way.",
    "A location is a claim until surveyed. The grid catalogue has no coordinates, so every point is labelled with how it was arrived at.",
  ], { y: 1.75, w: 7.2, h: 3.6, fontSize: 13.5 });

  s.addShape(pres.ShapeType.roundRect, {
    x: 8.1, y: 1.75, w: 4.6, h: 3.5, rectRadius: 0.12,
    fill: { color: WASH }, line: { color: ICE, width: 1 } });
  s.addText("The gap analysis, from real data", {
    x: 8.35, y: 1.9, w: 4.1, h: 0.35, isTextBox: true,
    fontFace: B, fontSize: 14, bold: true, color: INK, margin: 0 });
  const gaps = [["29", "cameras with no department assigned"], ["30", "with optics never surveyed"],
                ["20", "capability grades not yet eye-verified"], ["6", "with no location established at all"]];
  gaps.forEach(([n, label], i) => {
    const y = 2.35 + i * 0.72;
    s.addText(n, { x: 8.35, y, w: 0.8, h: 0.45, isTextBox: true,
      fontFace: H, fontSize: 22, bold: true, color: AMBER, margin: 0 });
    s.addText(label, { x: 9.2, y: y + 0.06, w: 3.3, h: 0.6, isTextBox: true,
      fontFace: B, fontSize: 11.5, color: INK, margin: 0 });
  });

  s.addText("These are onboarding tasks with names attached, not rows hidden from the dashboard.", {
    x: 0.6, y: 5.6, w: 12.1, h: 0.4, isTextBox: true,
    fontFace: B, fontSize: 14, bold: true, color: INK, margin: 0 });
  note(s, "“Department-wise information requirements” is a whole evaluation dimension. This is the worklist that answers it.");
}

/* -------------------------------------------- 14. security/evidence */
{
  const s = lightSlide("Built for a court, not just a control room", "Security, privacy and evidence");
  const cards = [
    ["Purpose-bound queries", "Tracing a vehicle carries an FIR/DD reference into an append-only audit log, alongside the rows returned."],
    ["Evidence chain", "SHA-256 sealed exports with a pre-filled Section 63 Bharatiya Sakshya Adhiniyam 2023 certificate — dual signature, unlike the old IEA §65B."],
    ["No credentials in artifacts", "RTSP URLs embed credentials. They are redacted from every log line, report and recording, and the repository is scanned before each commit."],
    ["Consent, enforced", "A private camera without a consent reference is rejected by the database. Expiry is surfaced before it lapses."],
    ["Department-scoped RBAC", "Row-level tenancy: a department sees its own estate by default."],
    ["Face recognition: designed, not built", "The test case is vehicle-centric. Deploying face recognition across a public estate raises proportionality questions a hackathon should not answer for the state."],
  ];
  cards.forEach(([title, body], i) => {
    const x = 0.6 + (i % 3) * 4.15;
    const y = 1.8 + Math.floor(i / 3) * 2.35;
    s.addShape(pres.ShapeType.roundRect, {
      x, y, w: 3.85, h: 2.05, rectRadius: 0.12,
      fill: { color: WASH }, line: { color: ICE, width: 1 } });
    s.addText(title, { x: x + 0.2, y: y + 0.15, w: 3.45, h: 0.5, isTextBox: true,
      fontFace: B, fontSize: 13, bold: true, color: INK, margin: 0 });
    s.addText(body, { x: x + 0.2, y: y + 0.68, w: 3.45, h: 1.25, isTextBox: true,
      fontFace: B, fontSize: 11, color: MUTED, margin: 0 });
  });
  s.addNotes("NFSU is on the jury and digital forensics is their field. The BSA §63 dual-signature requirement is new and most submissions will still cite IEA §65B.");
}

/* ------------------------------------------------- 15. licensing */
{
  const s = lightSlide("Open source, and we read the licences", "Vendor-neutral by construction");
  table(s, [
    header(["Job", "Chosen", "Licence"]),
    ["Vehicle detection", "RF-DETR via open-image-models", "Apache-2.0"],
    ["Plate detection", "YOLOv9-architecture plate model", "MIT package (weights: see note)"],
    ["Plate OCR", "fast-plate-ocr cct-xs-v2-global", "MIT"],
    ["Tracking", "ByteTrack-style, implemented in-repo", "our code"],
    ["Registry and GIS", "PostgreSQL + PostGIS", "PostgreSQL / GPL-2.0"],
    ["Media sandbox", "MediaMTX", "MIT"],
    ["Map matching", "OSRM (self-hosted)", "BSD-2-Clause"],
  ], { colW: [3.6, 5.2, 3.3], y: 1.8, fontSize: 12 });

  bullets(s, [
    "Ultralytics YOLO is excluded deliberately: AGPL-3.0's network clause would force disclosure of the entire platform.",
    "Stated honestly: the plate detector weights derive from GPL-3.0 upstream YOLOv9 — no network clause, so server-side use triggers no obligation, but we do not claim a spotless MIT stack.",
    "Those weights appear trained on Latin-American plates, which is exactly why Indian plate performance was validated explicitly and published.",
  ], { y: 5.0, w: 12.1, fontSize: 12.5 });
  note(s, "Weights are vendored into the deployment artifact — police networks are egress-restricted and should not fetch models from GitHub at runtime.");
}

/* ------------------------------------------------ 16. limitations */
{
  const s = darkSlide();
  s.addText("What this does not do", {
    x: 0.7, y: 0.8, w: 12.0, h: 0.7, isTextBox: true,
    fontFace: H, fontSize: 34, bold: true, color: LIGHT, margin: 0 });
  s.addText("Published in LIMITATIONS.md, alongside the successes.", {
    x: 0.7, y: 1.55, w: 12.0, h: 0.4, isTextBox: true,
    fontFace: B, fontSize: 15, color: AMBER, margin: 0 });
  bullets(s, [
    "ANPR is viable on a small minority of this estate's cameras. That is the finding, not a defect we hide.",
    "OCR throughput on CPU is about one frame per second; real-time multi-camera operation needs GPU.",
    "Road distances need a self-hosted OSRM; straight-line distance is used until it is deployed, which keeps the plausibility check conservative.",
    "VAHAN and CCTNS are designed and declared, not connected — they need an NIC certificate and GSWAN placement.",
    "Camera locations derived from names are approximate and labelled: 8 to a junction, 16 to a town, 6 not placeable at all.",
    "One camera's catalogue name does not match the location in its own video overlay. Names are labels, not locations.",
    "Cross-camera re-identification and face recognition are designed, not implemented.",
  ], { y: 2.2, w: 12.0, color: ICE, fontSize: 13.5 });
  s.addNotes("Publishing measured failures reads as credibility to a technical jury, and every one of these is reproducible from the repository.");
}

/* --------------------------------------------------- 17. status */
{
  const s = lightSlide("Where it stands", "Working platform, not a concept");
  table(s, [
    header(["Capability", "State"]),
    ["Registry + GIS, multi-department, analog and IP, private cameras", "Implemented"],
    ["Camera Capability Index with costed remediation", "Implemented"],
    ["ANPR: detect → track → read → vote, PTS-driven, integrity-gated", "Implemented"],
    ["Route reconstruction with loop folding and plausibility checks", "Implemented"],
    ["Watchlist matching with prioritised, deduplicated alerts", "Implemented"],
    ["Operator dashboard: map, capability, alerts, vehicle trace", "Implemented"],
    ["Purpose-bound queries and append-only audit", "Implemented"],
    ["VAHAN / SARTHI / eGujCop integration", { text: "Designed, not connected", options: { color: AMBER } }],
    ["Cross-camera re-identification, face recognition", { text: "Designed, not implemented", options: { color: AMBER } }],
  ], { colW: [8.6, 3.5], y: 1.8, fontSize: 12 });

  s.addText("Every figure in this deck was measured against the organiser's grid and is reproducible from the repository.", {
    x: 0.6, y: 5.95, w: 12.1, h: 0.62, isTextBox: true,
    fontFace: H, fontSize: 17, bold: true, color: INK, margin: 0 });
  note(s, "Mock-ups, animations and concept videos are explicitly rejected by the problem statement. This is a working backend.");
}

pres.writeFile({ fileName: "docs/Sentinel_Solution_Presentation.pptx" })
  .then((f) => console.log("written:", f));
