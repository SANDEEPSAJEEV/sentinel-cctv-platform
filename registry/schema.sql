-- Sentinel registry — Model 1 (Centralised CCTV Registry & GIS).
--
-- Metadata only. No stream ever lands here, and no credential: cameras carry a
-- catalogue key (stream_path), never a URL with an embedded email/password.
--
-- Three things this schema takes seriously that most registries skip:
--   1. Analog cameras are not IP cameras. They hang off a DVR/encoder, so the
--      node is a first-class row and the camera points at it.
--   2. Private cameras (societies, malls, shops) are onboarded under a consent
--      reference with an expiry, not silently absorbed.
--   3. A location is a claim until somebody surveys it. location_confidence
--      says how much to trust the point, and drives the onboarding worklist.

CREATE EXTENSION IF NOT EXISTS postgis;

-- ---------------------------------------------------------------- department
CREATE TABLE IF NOT EXISTS department (
    id              serial PRIMARY KEY,
    code            text UNIQUE NOT NULL,
    name            text NOT NULL,
    kind            text NOT NULL DEFAULT 'government'
                    CHECK (kind IN ('government', 'private')),
    contact_name    text,
    contact_email   text,
    contact_phone   text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------- node
-- DVR / NVR / encoder / departmental VMS. Analog cameras reach us only through
-- one of these, so capacity and firmware live here, not on the camera.
CREATE TABLE IF NOT EXISTS node (
    id              serial PRIMARY KEY,
    code            text UNIQUE NOT NULL,
    kind            text NOT NULL
                    CHECK (kind IN ('dvr', 'nvr', 'encoder', 'vms', 'direct')),
    department_id   integer REFERENCES department(id) ON DELETE SET NULL,
    channels_total  integer,
    channels_used   integer,
    make            text,
    model           text,
    firmware        text,
    management_host text,          -- hostname or IP, no credentials
    site_name       text,
    district        text,
    geom            geometry(Point, 4326),
    notes           text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- -------------------------------------------------------------------- camera
CREATE TABLE IF NOT EXISTS camera (
    id                  serial PRIMARY KEY,
    code                text UNIQUE NOT NULL,        -- registry id
    external_ref        text,                        -- id in the source system
    name                text NOT NULL,
    department_id       integer REFERENCES department(id) ON DELETE SET NULL,
    node_id             integer REFERENCES node(id) ON DELETE SET NULL,

    owner_type          text NOT NULL DEFAULT 'government'
                        CHECK (owner_type IN ('government', 'private')),
    signal_type         text NOT NULL DEFAULT 'ip'
                        CHECK (signal_type IN ('ip', 'analog')),
    camera_type         text NOT NULL DEFAULT 'unknown'
                        CHECK (camera_type IN ('fixed', 'ptz', 'dome', 'bullet',
                                               'anpr', 'thermal', 'unknown')),
    make                text,
    model               text,
    declared_resolution text,
    declared_fps        numeric,
    codec               text,

    geom                geometry(Point, 4326),
    location_confidence text NOT NULL DEFAULT 'unknown'
                        CHECK (location_confidence IN ('surveyed', 'approx_area',
                                                       'approx_city', 'unknown')),
    location_source     text,
    district            text,
    address             text,

    -- optics: what the camera is pointed at, which is what capability depends on
    heading_deg         numeric CHECK (heading_deg BETWEEN 0 AND 360),
    fov_deg             numeric CHECK (fov_deg > 0 AND fov_deg <= 360),
    mount_height_m      numeric,
    view_kind           text CHECK (view_kind IN ('lane', 'junction', 'overview',
                                                  'entry', 'corridor', 'unknown')),

    status              text NOT NULL DEFAULT 'unknown'
                        CHECK (status IN ('online', 'offline', 'degraded',
                                          'unknown', 'decommissioned')),
    commissioned_on     date,
    retention_days      integer,

    -- private-camera onboarding: consent is mandatory and it expires
    public_facing       boolean,
    consent_ref         text,
    consent_expires_on  date,

    stream_path         text,        -- catalogue key only. NEVER a credentialed URL.
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT private_camera_needs_consent
        CHECK (owner_type <> 'private' OR consent_ref IS NOT NULL),
    CONSTRAINT analog_camera_needs_node
        CHECK (signal_type <> 'analog' OR node_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS camera_geom_idx ON camera USING gist (geom);
CREATE INDEX IF NOT EXISTS camera_department_idx ON camera (department_id);

-- ------------------------------------------------------------ capability_run
-- One row per profiling pass (profile_cameras.py). Kept as history, not
-- overwritten: re-lensing a camera should show up as a grade that improves.
CREATE TABLE IF NOT EXISTS capability_run (
    id                      bigserial PRIMARY KEY,
    camera_id               integer NOT NULL REFERENCES camera(id) ON DELETE CASCADE,
    observed_at             timestamptz NOT NULL,
    sample_seconds          numeric,
    reachable               boolean NOT NULL DEFAULT false,
    error                   text,

    resolution              text,
    codec                   text,
    measured_fps            numeric,
    declared_fps            numeric,
    video_seconds_analysed  numeric,
    frames_analysed         integer,
    gap_ms_p50              numeric,
    gap_ms_max              numeric,
    reconnects              integer,
    epochs_seen             integer,

    mean_luma               numeric,
    lighting                text,
    focus_laplacian_var     numeric,

    plates_observed         integer,
    plates_static_rejected  integer,
    plate_width_px_median   numeric,
    pixel_density_px_per_m  numeric,
    char_height_px_est      numeric,

    capability_grade        text,
    capability_note         text,
    anpr_capable            boolean,
    -- true only once a human has looked at the crops. The detector fires on
    -- signage, taillights and on-screen text; an unverified grade is a claim.
    evidence_verified       boolean NOT NULL DEFAULT false,
    source                  text
);

CREATE INDEX IF NOT EXISTS capability_run_camera_idx
    ON capability_run (camera_id, observed_at DESC);

-- -------------------------------------------------------- vehicle_track
-- One row per tracked vehicle on one camera, from the ANPR worker. This is
-- metadata: a box path and a time span, never a frame of video.
CREATE TABLE IF NOT EXISTS vehicle_track (
    id              bigserial PRIMARY KEY,
    camera_id       integer NOT NULL REFERENCES camera(id) ON DELETE CASCADE,
    track_key       text NOT NULL,     -- run id + tracker id: unique per worker run
    epoch           integer NOT NULL DEFAULT 0,
    vehicle_class   text,
    frames          integer,
    first_pts_ms    numeric,
    last_pts_ms     numeric,
    first_seen_at   timestamptz,
    last_seen_at    timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (camera_id, track_key)
);

-- ------------------------------------------------------------ plate_read
-- One row per *voted* plate, not per frame: the worker reads the plate on
-- every frame of a track and votes across them, so a row here is already the
-- combined answer. reads/agreeing_reads keep the evidence behind it.
CREATE TABLE IF NOT EXISTS plate_read (
    id              bigserial PRIMARY KEY,
    camera_id       integer NOT NULL REFERENCES camera(id) ON DELETE CASCADE,
    track_id        bigint REFERENCES vehicle_track(id) ON DELETE CASCADE,
    plate           text NOT NULL,
    confidence      numeric,
    reads           integer,
    agreeing_reads  integer,
    valid_format    boolean,
    repaired        boolean,
    -- usable = valid Indian format AND corroborated by more than one frame.
    -- Everything else is kept, flagged, and left out of route reconstruction.
    usable          boolean,
    vehicle_class   text,
    plate_width_px  numeric,
    char_height_px  numeric,
    epoch           integer,
    pts_ms          numeric,        -- media timestamp of the best read
    observed_at     timestamptz NOT NULL,   -- media time mapped to wall clock
    model           text,
    source          text,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS plate_read_plate_idx ON plate_read (plate, observed_at DESC);
CREATE INDEX IF NOT EXISTS plate_read_camera_idx ON plate_read (camera_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS plate_read_usable_idx ON plate_read (usable, observed_at DESC);

-- ------------------------------------------------------------- watchlist
-- The representative watchlist. In deployment the rows arrive from VAHAN,
-- SARTHI, eGujCop/CCTNS and the like through the adapters in watchlist/
-- sources.py; source_system records which, so an operator can see where a
-- claim came from and an integration can be swapped without touching alerts.
--
-- Entries expire. A stolen-vehicle circulation that is never withdrawn becomes
-- a standing order to stop a citizen, so valid_until is part of the record and
-- matching ignores anything outside its window.
CREATE TABLE IF NOT EXISTS watchlist (
    id              serial PRIMARY KEY,
    plate           text NOT NULL,
    category        text NOT NULL DEFAULT 'bolo'
                    CHECK (category IN ('stolen', 'wanted', 'suspect', 'bolo',
                                        'permit', 'test')),
    severity        text NOT NULL DEFAULT 'medium'
                    CHECK (severity IN ('critical', 'high', 'medium', 'low')),
    reason          text,
    source_system   text NOT NULL DEFAULT 'manual',
    source_ref      text,                    -- FIR / DD / circulation number
    added_by        text,
    valid_from      date NOT NULL DEFAULT current_date,
    valid_until     date,
    active          boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (plate, source_ref)
);

CREATE INDEX IF NOT EXISTS watchlist_plate_idx ON watchlist (plate) WHERE active;

-- ----------------------------------------------------------------- alert
-- One row per (watchlist entry, camera) sighting burst. hit_count grows rather
-- than the table: a looping sandbox — or a vehicle waiting at a signal — would
-- otherwise raise the same alert dozens of times and bury the operator.
CREATE TABLE IF NOT EXISTS alert (
    id              bigserial PRIMARY KEY,
    watchlist_id    integer NOT NULL REFERENCES watchlist(id) ON DELETE CASCADE,
    plate_read_id   bigint REFERENCES plate_read(id) ON DELETE SET NULL,
    camera_id       integer REFERENCES camera(id) ON DELETE SET NULL,
    plate_wanted    text NOT NULL,
    plate_read      text NOT NULL,
    match_kind      text NOT NULL CHECK (match_kind IN ('exact', 'fuzzy')),
    match_score     numeric,
    severity        text NOT NULL,
    priority        integer NOT NULL,
    priority_reason text,
    -- A fuzzy match means a plate that is one OCR-confusable character away.
    -- It is a lead, never a confirmation: acting on it stops the wrong driver.
    needs_verification boolean NOT NULL DEFAULT false,
    status          text NOT NULL DEFAULT 'new'
                    CHECK (status IN ('new', 'acknowledged', 'dismissed', 'escalated')),
    hit_count       integer NOT NULL DEFAULT 1,
    first_seen_at   timestamptz NOT NULL,
    last_seen_at    timestamptz NOT NULL,
    raised_at       timestamptz NOT NULL DEFAULT now(),
    acknowledged_at timestamptz,
    acknowledged_by text,
    note            text
);

CREATE INDEX IF NOT EXISTS alert_open_idx ON alert (status, priority DESC, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS alert_watch_camera_idx ON alert (watchlist_id, camera_id, last_seen_at DESC);

-- ------------------------------------------------------------------ audit_log
-- Every read of camera data is purpose-bound: queries carry an FIR/DD
-- reference. Append-only by convention here; enforced by grants in deployment.
CREATE TABLE IF NOT EXISTS audit_log (
    id          bigserial PRIMARY KEY,
    at          timestamptz NOT NULL DEFAULT now(),
    actor       text,
    action      text NOT NULL,
    object_type text,
    object_id   text,
    purpose_ref text,
    detail      jsonb
);

CREATE INDEX IF NOT EXISTS audit_log_at_idx ON audit_log (at DESC);

-- ------------------------------------------------------------------- views
-- Latest capability per camera, for the map and the dashboard.
CREATE OR REPLACE VIEW camera_current AS
SELECT DISTINCT ON (c.id)
       c.*,
       d.code  AS department_code,
       d.name  AS department_name,
       n.code  AS node_code,
       n.kind  AS node_kind,
       r.observed_at            AS last_profiled_at,
       r.capability_grade,
       r.pixel_density_px_per_m,
       r.measured_fps,
       r.plates_observed,
       r.lighting,
       r.anpr_capable,
       r.evidence_verified,
       r.reachable              AS last_reachable
FROM camera c
LEFT JOIN department d ON d.id = c.department_id
LEFT JOIN node n ON n.id = c.node_id
LEFT JOIN capability_run r ON r.camera_id = c.id
ORDER BY c.id, r.observed_at DESC NULLS LAST;
