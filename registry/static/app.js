/* Sentinel registry UI.
 *
 * No third-party JS, and no basemap tiles: CLAUDE.md rules out loading OSM's
 * public tile servers, and police networks are egress-restricted anyway. The
 * map is a plain SVG projection with a graticule and a scale bar. When a
 * self-hosted tile server exists, a raster layer drops in behind the markers
 * without touching anything else here.
 */

const GRADES = ["IDENTIFY", "RECOGNISE", "OBSERVE", "DETECT", "UNGRADED", "UNPROFILED"];
const GRADE_HELP = {
  IDENTIFY: "≥250 px/m — full ANPR",
  RECOGNISE: "≥125 px/m — marginal ANPR, attributes, re-ID",
  OBSERVE: "≥63 px/m — class, colour, direction",
  DETECT: "≥25 px/m — presence and counting only",
  UNGRADED: "too few plates seen to judge — resample",
  UNPROFILED: "never profiled",
};

// Gujarat bounding box, with a small margin.
const BBOX = { minLon: 68.0, maxLon: 74.8, minLat: 20.0, maxLat: 24.9 };

const state = { features: [], selected: null, view: null, size: { w: 800, h: 600 }, route: null };

const $ = (sel) => document.querySelector(sel);
const map = $("#map");

/* ------------------------------------------------------------- projection */
// Equirectangular with a cos(lat) correction, which is accurate enough across
// a single state and keeps the maths inspectable.
const LAT0 = (BBOX.minLat + BBOX.maxLat) / 2;
const kx = Math.cos((LAT0 * Math.PI) / 180);

function baseProject(lon, lat) {
  const x = (lon - BBOX.minLon) * kx;
  const y = BBOX.maxLat - lat;
  return [x, y];
}

function fitView() {
  const [x0, y0] = baseProject(BBOX.minLon, BBOX.maxLat);
  const [x1, y1] = baseProject(BBOX.maxLon, BBOX.minLat);
  const { w, h } = state.size;
  const scale = Math.min(w / (x1 - x0), h / (y1 - y0)) * 0.94;
  state.view = {
    scale,
    tx: (w - (x1 - x0) * scale) / 2 - x0 * scale,
    ty: (h - (y1 - y0) * scale) / 2 - y0 * scale,
  };
}

function project(lon, lat) {
  const [bx, by] = baseProject(lon, lat);
  const { scale, tx, ty } = state.view;
  return [bx * scale + tx, by * scale + ty];
}

/* ------------------------------------------------------------------ data */
async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`);
  return res.json();
}

function gradeOf(props) {
  return props.capability_grade || "UNPROFILED";
}

async function loadStats() {
  const s = await api("/api/stats");
  const unknown = s.by_location_confidence?.unknown || 0;
  const tiles = [
    ["cameras", s.cameras, ""],
    ["on the map", s.mappable, ""],
    ["ANPR capable", s.anpr_capable, s.anpr_capable ? "good" : "warn"],
    ["grades verified", s.evidence_verified, s.evidence_verified ? "good" : "warn"],
    ["location unknown", unknown, unknown ? "warn" : ""],
    ["unassigned dept", s.department_unassigned, s.department_unassigned ? "warn" : ""],
  ];
  $("#stats").innerHTML = tiles
    .map(([label, value, cls]) => `<div class="stat ${cls}"><b>${value ?? 0}</b><span>${label}</span></div>`)
    .join("");
}

async function loadFilters() {
  const depts = await api("/api/departments");
  fill("#f-department", [["", "All departments"]].concat(
    depts.map((d) => [d.code, `${d.name} (${d.cameras})`])));
  fill("#f-grade", [["", "Any capability"]].concat(GRADES.filter((g) => g !== "UNPROFILED").map((g) => [g, g])));
  fill("#f-status", [["", "Any status"], ["online", "online"], ["offline", "offline"],
                     ["degraded", "degraded"], ["unknown", "unknown"]]);
  fill("#f-confidence", [["", "Any location quality"], ["surveyed", "surveyed"],
                         ["approx_area", "approx area"], ["approx_city", "approx city"],
                         ["unknown", "unknown"]]);
  fill("#f-owner", [["", "Any owner"], ["government", "government"], ["private", "private"]]);

  $("#legend").innerHTML = GRADES
    .map((g) => `<span title="${GRADE_HELP[g]}"><i class="dot ${g}"></i>${g}</span>`)
    .join("");
  $("#map-note").textContent =
    "No basemap: public OSM tiles are not permitted for this load, so the map " +
    "renders offline. Point it at a self-hosted tile server for production.";
}

function fill(sel, pairs) {
  $(sel).innerHTML = pairs.map(([v, t]) => `<option value="${v}">${t}</option>`).join("");
}

function filterParams() {
  const p = new URLSearchParams();
  const add = (k, sel) => { const v = $(sel).value.trim(); if (v) p.set(k, v); };
  add("q", "#f-q");
  add("department", "#f-department");
  add("grade", "#f-grade");
  add("status", "#f-status");
  add("confidence", "#f-confidence");
  add("owner_type", "#f-owner");
  return p;
}

async function loadCameras() {
  const data = await api(`/api/cameras?${filterParams()}`);
  state.features = data.features;
  renderList();
  renderMap();
  const unmapped = state.features.filter((f) => !f.geometry).length;
  $("#unmapped-count").textContent = unmapped;
  $("#count").textContent = `(${state.features.length})`;
}

/* ------------------------------------------------------------------- list */
function renderList() {
  $("#list").innerHTML = state.features
    .map((f) => {
      const p = f.properties;
      const g = gradeOf(p);
      return `<li data-code="${p.code}" class="${p.code === state.selected ? "active" : ""}">
        <i class="dot ${g}" title="${g}"></i>
        <span class="nm">${escapeHtml(p.name)}</span>
        <span class="cd">${p.external_ref ?? ""}</span></li>`;
    })
    .join("");
  $("#list").querySelectorAll("li").forEach((li) =>
    li.addEventListener("click", () => select(li.dataset.code)));
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* -------------------------------------------------------------------- map */
function renderMap() {
  const { w, h } = state.size;
  map.setAttribute("viewBox", `0 0 ${w} ${h}`);
  const parts = [graticule(), scalebar()];

  // Cameras at the same site overlap exactly (three at Bilimora, two at Visat),
  // so fan out duplicates rather than hiding them under one another.
  const seen = new Map();
  for (const f of state.features) {
    if (!f.geometry) continue;
    const [lon, lat] = f.geometry.coordinates;
    const key = `${lon.toFixed(4)},${lat.toFixed(4)}`;
    const n = seen.get(key) ?? 0;
    seen.set(key, n + 1);
    let [x, y] = project(lon, lat);
    if (n) {
      const a = (n * 2 * Math.PI) / 6;
      x += Math.cos(a) * 9;
      y += Math.sin(a) * 9;
    }
    const p = f.properties;
    const g = gradeOf(p);
    parts.push(
      `<g class="cam ${p.code === state.selected ? "sel" : ""}" data-code="${p.code}"
          transform="translate(${x.toFixed(1)} ${y.toFixed(1)})">
         <circle class="halo" r="10"></circle>
         <circle r="5" fill="${colorFor(g)}" stroke="#0b1016" stroke-width="1.5">
           <title>${escapeHtml(p.name)} — ${g}${p.district ? " — " + escapeHtml(p.district) : ""}</title>
         </circle>
       </g>`);
  }
  if (state.route) parts.push(routeOverlay());
  map.innerHTML = parts.join("");
  map.querySelectorAll(".cam").forEach((el) =>
    el.addEventListener("click", () => select(el.dataset.code)));
}

/* The route drawn over the camera layer: dashed line between consecutive
 * sightings, numbered stops, amber where the plate matched only fuzzily.
 * Sightings at cameras with no confirmed location cannot be drawn — they stay
 * in the timeline, and the gap in the line is the honest picture. */
function routeOverlay() {
  const stops = state.route.sightings.filter((s) => s.lat != null && s.lon != null);
  if (!stops.length) return "";
  const pts = stops.map((s) => project(s.lon, s.lat));
  const path = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  const marks = stops.map((s, i) => {
    const [x, y] = pts[i];
    return `<g transform="translate(${x.toFixed(1)} ${y.toFixed(1)})">
      <circle class="route-stop ${s.match_kind}" r="9"></circle>
      <text class="route-num" y="3">${i + 1}</text>
      <title>${escapeHtml(s.camera_name)} — ${new Date(s.observed_at).toLocaleString()}
${escapeHtml(s.plate_read)} (${s.match_kind})</title></g>`;
  }).join("");
  return `<path class="route-line" d="${path}"></path>${marks}`;
}

function colorFor(grade) {
  const v = getComputedStyle(document.documentElement)
    .getPropertyValue(`--${grade.toLowerCase()}`).trim();
  return v || "#8e9aa6";
}

function graticule() {
  const out = [];
  for (let lon = Math.ceil(BBOX.minLon); lon <= BBOX.maxLon; lon++) {
    const [x1, y1] = project(lon, BBOX.maxLat);
    const [x2, y2] = project(lon, BBOX.minLat);
    out.push(`<line class="graticule" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"></line>`);
    out.push(`<text class="grat-label" x="${x1 + 3}" y="${y2 - 4}">${lon}°E</text>`);
  }
  for (let lat = Math.ceil(BBOX.minLat); lat <= BBOX.maxLat; lat++) {
    const [x1, y1] = project(BBOX.minLon, lat);
    const [x2, y2] = project(BBOX.maxLon, lat);
    out.push(`<line class="graticule" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}"></line>`);
    out.push(`<text class="grat-label" x="${x1 + 3}" y="${y1 - 3}">${lat}°N</text>`);
  }
  return out.join("");
}

function scalebar() {
  // 50 km at this latitude, in projected units.
  const kmPerDegLon = 111.32 * kx;
  const [x0, y0] = project(BBOX.minLon + 0.3, BBOX.minLat + 0.25);
  const [x1] = project(BBOX.minLon + 0.3 + 50 / kmPerDegLon, BBOX.minLat + 0.25);
  return `<g class="scalebar">
    <path d="M${x0} ${y0 - 5} L${x0} ${y0} L${x1} ${y0} L${x1} ${y0 - 5}"></path>
    <text x="${(x0 + x1) / 2}" y="${y0 - 8}" text-anchor="middle">50 km</text>
  </g>`;
}

/* ----------------------------------------------------------------- detail */
async function select(code) {
  state.selected = code;
  renderList();
  renderMap();
  const f = await api(`/api/cameras/${encodeURIComponent(code)}`, {
    headers: { "X-Purpose-Ref": "REGISTRY-UI" },
  });
  const p = f.properties;
  const g = gradeOf(p);
  const rows = [
    ["Registry code", p.code],
    ["Source id", p.external_ref],
    ["Department", p.department_name ?? "—"],
    ["District", p.district ?? "—"],
    ["Owner", p.owner_type],
    ["Signal", p.signal_type + (p.node_code ? ` via ${p.node_code} (${p.node_kind})` : "")],
    ["Status", p.status],
    ["Location", f.geometry
      ? `${f.geometry.coordinates[1].toFixed(4)}, ${f.geometry.coordinates[0].toFixed(4)}`
      : "not established"],
    ["Location quality", p.location_confidence],
    ["Derived from", p.location_source ?? "—"],
    ["Optics", [p.heading_deg && `${p.heading_deg}°`, p.fov_deg && `${p.fov_deg}° FOV`,
                p.mount_height_m && `${p.mount_height_m} m`].filter(Boolean).join(" · ") || "not surveyed"],
    ["Measured fps", p.measured_fps ?? "—"],
    ["Plate density", p.pixel_density_px_per_m ? `${p.pixel_density_px_per_m} px/m` : "—"],
    ["Plates seen", p.plates_observed ?? "—"],
    ["Lighting", p.lighting ?? "—"],
    ["Last profiled", p.last_profiled_at ? new Date(p.last_profiled_at).toLocaleString() : "never"],
  ];

  const unverified = p.capability_grade && !p.evidence_verified;
  $("#detail").innerHTML = `
    <h2>${escapeHtml(p.name)}</h2>
    <p><span class="pill ${g}">${g}</span>
       <span class="muted">${GRADE_HELP[g] ?? ""}</span></p>
    ${unverified ? `<p class="warnline">Grade not verified by eye. The plate
       detector also fires on signage, taillights and on-screen text — check the
       crops before quoting this grade.</p>
       <button id="verify-btn" type="button">Mark evidence verified</button>` : ""}
    <dl>${rows.map(([k, v]) => `<dt>${k}</dt><dd>${escapeHtml(v)}</dd>`).join("")}</dl>
    <h3>Capability history</h3>
    ${p.capability_history?.length ? runsTable(p.capability_history)
      : '<p class="muted">Never profiled.</p>'}`;

  const btn = $("#verify-btn");
  if (btn) {
    btn.addEventListener("click", async () => {
      await api(`/api/cameras/${encodeURIComponent(code)}/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Purpose-Ref": "REGISTRY-UI" },
        body: JSON.stringify({ verified: true }),
      });
      await Promise.all([loadStats(), loadCameras()]);
      select(code);
    });
  }
}

function runsTable(runs) {
  return `<table class="runs">
    <tr><th>When</th><th>Grade</th><th>px/m</th><th>fps</th><th>Plates</th><th>Video s</th></tr>
    ${runs.map((r) => `<tr>
      <td>${new Date(r.observed_at).toLocaleDateString()}</td>
      <td>${r.capability_grade ?? (r.reachable ? "—" : "unreachable")}</td>
      <td>${r.pixel_density_px_per_m ?? "—"}</td>
      <td>${r.measured_fps ?? "—"}</td>
      <td>${r.plates_observed ?? "—"}</td>
      <td>${r.video_seconds_analysed ?? "—"}</td></tr>`).join("")}
  </table>`;
}

/* ----------------------------------------------------------------- alerts */
async function loadAlerts() {
  let alerts;
  try {
    alerts = await api("/api/alerts?limit=12");
  } catch {
    $("#alerts").innerHTML = '<li class="empty">alerts unavailable</li>';
    return;
  }
  $("#alert-count").textContent = alerts.length ? `(${alerts.length} open)` : "";
  if (!alerts.length) {
    $("#alerts").innerHTML = '<li class="empty">No open alerts.</li>';
    return;
  }
  $("#alerts").innerHTML = alerts.map((a) => `
    <li class="${a.severity}" data-id="${a.id}">
      <div class="top">
        <span class="plate" data-plate="${escapeHtml(a.plate_wanted)}"
              title="Trace this registration">${escapeHtml(a.plate_wanted)}</span>
        <span class="pri">${a.severity} · priority ${a.priority}</span>
      </div>
      <div class="where">read ${escapeHtml(a.plate_read)} at
        ${escapeHtml(a.camera_name || a.camera_code || "unknown camera")}
        · ${new Date(a.last_seen_at).toLocaleString()}
        ${a.hit_count > 1 ? ` · ${a.hit_count} sightings` : ""}</div>
      ${a.needs_verification
        ? '<span class="verify">Fuzzy plate match — a lead, not a confirmation. Verify the crop before acting.</span>'
        : ""}
      <div class="acts">
        <button data-act="acknowledged">Acknowledge</button>
        <button data-act="dismissed">Dismiss</button>
      </div>
    </li>`).join("");

  $("#alerts").querySelectorAll(".plate").forEach((el) =>
    el.addEventListener("click", () => {
      $("#r-plate").value = el.dataset.plate;
      traceRoute();
    }));
  $("#alerts").querySelectorAll(".acts button").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const id = btn.closest("li").dataset.id;
      await api(`/api/alerts/${id}/status`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: btn.dataset.act, by: "operator (demo)" }),
      });
      loadAlerts();
    }));
}

/* ------------------------------------------------------------------ route */
async function traceRoute() {
  const plate = $("#r-plate").value.trim().toUpperCase();
  if (!plate) return;
  const purpose = $("#r-purpose").value.trim();
  $("#r-summary").textContent = "tracing…";
  let route;
  try {
    route = await api(`/api/routes/${encodeURIComponent(plate)}`, {
      headers: purpose ? { "X-Purpose-Ref": purpose } : {},
    });
  } catch (err) {
    $("#r-summary").textContent = `trace failed: ${err.message}`;
    return;
  }
  state.route = route;
  $("#r-clear").hidden = false;
  const s = route.summary;
  const strength = (s.evidence_strength || "").split(" ")[0];
  $("#r-summary").innerHTML = s.sightings
    ? `<b>${s.sightings}</b> sighting(s) on <b>${s.cameras}</b> camera(s),
       <b>${s.distance_km}</b> km${s.loop_repeats_folded
        ? `, <b>${s.loop_repeats_folded}</b> loop repeat(s) folded` : ""}
       <span class="strength ${strength}">${escapeHtml(s.evidence_strength)}</span>`
    : "no sighting of this registration in the registry";
  renderMap();
  renderRoutePanel(route);
}

function renderRoutePanel(route) {
  const s = route.summary;
  const rows = route.sightings.map((sg, i) => `<tr>
      <td>${i + 1}</td>
      <td>${new Date(sg.observed_at).toLocaleString()}</td>
      <td>${escapeHtml(sg.camera_name)}<br><span class="muted">${escapeHtml(sg.camera_code)}</span></td>
      <td class="${sg.match_kind}">${escapeHtml(sg.plate_read)}<br>
          <span class="muted">${sg.match_kind}</span></td>
      <td>${sg.reads} read${sg.reads === 1 ? "" : "s"}${sg.usable ? "<br>corroborated" : ""}
          ${sg.plate_width_px ? `<br><span class="muted">${Math.round(sg.plate_width_px)} px</span>` : ""}</td>
    </tr>`).join("");

  const legs = route.legs.map((l) => `<tr>
      <td>${escapeHtml(l.from_camera.slice(-5))} → ${escapeHtml(l.to_camera.slice(-5))}</td>
      <td>${Math.round(l.seconds / 60)} min</td>
      <td>${l.km == null ? "—" : l.km + " km"}</td>
      <td>${l.implied_kmh == null ? "—" : l.implied_kmh + " km/h"}</td>
      <td class="leg-flag">${l.flags.join(", ") || ""}</td>
    </tr>`).join("");

  $("#detail").innerHTML = `
    <h2>Route — ${escapeHtml(route.plate)}</h2>
    <p class="muted">${s.first_seen ? `${new Date(s.first_seen).toLocaleString()} →
       ${new Date(s.last_seen).toLocaleString()}` : "no sightings"}</p>
    ${s.sightings ? `
      <h3>Sightings</h3>
      <table class="route"><tr><th>#</th><th>When</th><th>Camera</th><th>Read</th><th>Evidence</th></tr>
        ${rows}</table>
      ${legs ? `<h3>Legs</h3><table class="route">
        <tr><th>Leg</th><th>Gap</th><th>Distance</th><th>Implied</th><th>Flags</th></tr>
        ${legs}</table>` : ""}
      <p class="muted" style="margin-top:8px">
        <a href="/api/routes/${encodeURIComponent(route.plate)}/report" target="_blank">
          plain-text report</a></p>` : ""}
    ${route.notes?.length ? `<h3>Notes</h3><ul class="notes">${
      route.notes.map((n) => `<li>${escapeHtml(n)}</li>`).join("")}</ul>` : ""}`;
}

function clearRoute() {
  state.route = null;
  $("#r-clear").hidden = true;
  $("#r-summary").textContent = "";
  $("#detail").innerHTML = '<h2>Camera</h2><p class="muted">Select a camera on the map or in the list.</p>';
  renderMap();
}

/* --------------------------------------------------------------- worklist */
async function showWorklist() {
  const wl = await api("/api/worklist");
  const titles = {
    location_unknown: "Location not established",
    department_unassigned: "Department not assigned",
    never_profiled: "Never profiled",
    grade_unverified: "Capability grade not verified by eye",
    optics_unknown: "Optics not surveyed (heading / mount height)",
    consent_expiring: "Private-camera consent expiring within 30 days",
  };
  $("#worklist-body").innerHTML = Object.entries(wl).map(([key, rows]) => `
    <div class="wl-group">
      <h3>${titles[key] ?? key} — ${rows.length}</h3>
      ${rows.length ? `<ul>${rows.slice(0, 40).map((r) =>
        `<li>${escapeHtml(r.code)} · ${escapeHtml(r.name ?? "")}${
          r.location_source ? ` — ${escapeHtml(r.location_source)}` : ""}${
          r.capability_grade ? ` — ${escapeHtml(r.capability_grade)}` : ""}</li>`).join("")}</ul>`
        : '<p class="muted">Nothing outstanding.</p>'}
    </div>`).join("");
  $("#worklist-dialog").showModal();
}

/* ------------------------------------------------------------- pan & zoom */
function installMapControls() {
  let dragging = false, last = null;
  map.addEventListener("pointerdown", (e) => {
    dragging = true; last = [e.clientX, e.clientY];
    map.classList.add("dragging"); map.setPointerCapture(e.pointerId);
  });
  map.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    state.view.tx += e.clientX - last[0];
    state.view.ty += e.clientY - last[1];
    last = [e.clientX, e.clientY];
    renderMap();
  });
  const stop = (e) => { dragging = false; map.classList.remove("dragging");
                        if (e.pointerId !== undefined) map.releasePointerCapture?.(e.pointerId); };
  map.addEventListener("pointerup", stop);
  map.addEventListener("pointercancel", stop);
  map.addEventListener("wheel", (e) => {
    e.preventDefault();
    const rect = map.getBoundingClientRect();
    const mx = ((e.clientX - rect.left) / rect.width) * state.size.w;
    const my = ((e.clientY - rect.top) / rect.height) * state.size.h;
    const k = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    state.view.scale *= k;
    state.view.tx = mx - (mx - state.view.tx) * k;
    state.view.ty = my - (my - state.view.ty) * k;
    renderMap();
  }, { passive: false });
}

function resize() {
  const rect = map.getBoundingClientRect();
  state.size = { w: Math.max(320, rect.width), h: Math.max(320, rect.height) };
  fitView();
  renderMap();
}

/* ------------------------------------------------------------------- boot */
(async function boot() {
  await loadFilters();
  resize();
  installMapControls();
  await Promise.all([loadStats(), loadCameras(), loadAlerts()]);
  // An operator's queue should not need a page refresh to be current.
  setInterval(loadAlerts, 15000);

  ["#f-q", "#f-department", "#f-grade", "#f-status", "#f-confidence", "#f-owner"]
    .forEach((sel) => $(sel).addEventListener("input", () => loadCameras()));
  $("#f-reset").addEventListener("click", () => {
    ["#f-q", "#f-department", "#f-grade", "#f-status", "#f-confidence", "#f-owner"]
      .forEach((sel) => ($(sel).value = ""));
    loadCameras();
  });
  $("#show-worklist").addEventListener("click", showWorklist);
  $("#r-go").addEventListener("click", traceRoute);
  $("#r-clear").addEventListener("click", clearRoute);
  $("#r-plate").addEventListener("keydown", (e) => { if (e.key === "Enter") traceRoute(); });
  window.addEventListener("resize", resize);
})();
