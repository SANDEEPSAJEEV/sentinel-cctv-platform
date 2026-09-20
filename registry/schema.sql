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
