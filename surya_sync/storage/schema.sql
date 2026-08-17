-- SuryaSync database schema, version 1.
--
-- Three principles are enforced structurally rather than by convention:
--
--   1. PROVENANCE. Every table holding a number that could be mistaken
--      for physical evidence carries a `provenance` column constrained to
--      measured/simulated/predicted/estimated/derived. A query cannot
--      accidentally mix simulated and measured results.
--
--   2. VERSIONING. Every row traces to a `runs` row, which traces to a
--      `component_versions` row and a config hash. No result is orphaned
--      from the code and parameters that produced it.
--
--   3. RESOURCE-AGNOSTIC. Tables speak of `resources` and `service_level`,
--      not tanks and litres. Native units travel alongside as data. Adding
--      a water heater in Phase 16 requires no schema migration.
--
-- Keep this file and `version.DB_SCHEMA_VERSION` in lockstep.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- Versioning and provenance spine
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS component_versions (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at              TEXT    NOT NULL,
    backend_version          TEXT    NOT NULL,
    db_schema_version        INTEGER NOT NULL,
    serial_protocol_version  TEXT    NOT NULL,
    water_model_version      TEXT    NOT NULL,
    pv_model_version         TEXT    NOT NULL,
    scheduler_version        TEXT,
    demand_model_version     TEXT,
    solar_model_version      TEXT,
    firmware_version         TEXT,
    config_hash              TEXT    NOT NULL,
    config_json              TEXT    NOT NULL
);

-- One execution session of the control loop.
CREATE TABLE IF NOT EXISTS runs (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at           TEXT NOT NULL,
    ended_at             TEXT,
    mode                 TEXT NOT NULL
        CHECK (mode IN ('simulated', 'real', 'replay')),
    component_version_id INTEGER NOT NULL REFERENCES component_versions(id),
    experiment_id        INTEGER REFERENCES experiments(id),
    scenario             TEXT,
    notes                TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs(started_at);

-- ---------------------------------------------------------------------
-- Resources
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS resources (
    resource_id     TEXT PRIMARY KEY,
    resource_type   TEXT NOT NULL,
    model_version   TEXT NOT NULL,
    native_unit     TEXT NOT NULL,
    rated_power_kw  REAL NOT NULL,
    -- Hard constraints, denormalized here so a historical row can be
    -- interpreted against the limits that were in force at the time.
    service_level_critical REAL NOT NULL,
    service_level_min      REAL NOT NULL,
    service_level_max      REAL NOT NULL,
    created_at      TEXT NOT NULL,
    config_json     TEXT
);

-- ---------------------------------------------------------------------
-- Observations
-- ---------------------------------------------------------------------

-- Raw per-channel hardware readings, before any model is applied.
-- Kept separately from derived state so the versioned conversion can be
-- re-run against history when the water model changes.
CREATE TABLE IF NOT EXISTS sensor_readings (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       INTEGER NOT NULL REFERENCES runs(id),
    timestamp    TEXT    NOT NULL,
    resource_id  TEXT    REFERENCES resources(resource_id),
    channel      TEXT    NOT NULL,   -- e.g. 'distance_mm', 'current_a'
    value        REAL,
    unit         TEXT    NOT NULL,
    valid        INTEGER NOT NULL DEFAULT 1,
    sequence     INTEGER,
    provenance   TEXT    NOT NULL
        CHECK (provenance IN ('measured', 'simulated'))
);

CREATE INDEX IF NOT EXISTS idx_sensor_readings_time
    ON sensor_readings(timestamp);
CREATE INDEX IF NOT EXISTS idx_sensor_readings_run_channel
    ON sensor_readings(run_id, channel, timestamp);

-- Resource state after applying the physical model.
CREATE TABLE IF NOT EXISTS resource_observations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL REFERENCES runs(id),
    timestamp     TEXT    NOT NULL,
    resource_id   TEXT    NOT NULL REFERENCES resources(resource_id),
    service_level REAL    NOT NULL,
    native_value  REAL    NOT NULL,
    native_unit   TEXT    NOT NULL,
    actuator_on   INTEGER NOT NULL,
    sensor_valid  INTEGER NOT NULL DEFAULT 1,
    provenance    TEXT    NOT NULL
        CHECK (provenance IN ('measured', 'simulated', 'derived'))
);

CREATE INDEX IF NOT EXISTS idx_resource_observations_time
    ON resource_observations(resource_id, timestamp);

-- Household electrical picture.
CREATE TABLE IF NOT EXISTS power_readings (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           INTEGER NOT NULL REFERENCES runs(id),
    timestamp        TEXT    NOT NULL,
    pv_generation_kw REAL,
    base_load_kw     REAL,
    grid_import_kw   REAL,
    surplus_kw       REAL,
    provenance       TEXT    NOT NULL
        CHECK (provenance IN ('measured', 'simulated', 'derived', 'estimated'))
);

CREATE INDEX IF NOT EXISTS idx_power_readings_time ON power_readings(timestamp);

-- ---------------------------------------------------------------------
-- Actuation: command sent != command executed
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS actuation_commands (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL REFERENCES runs(id),
    resource_id   TEXT    NOT NULL REFERENCES resources(resource_id),
    command_id    TEXT    NOT NULL,
    decision_id   INTEGER REFERENCES scheduler_decisions(id),

    -- The three states are deliberately separate columns with separate
    -- timestamps. Collapsing them would hide lost commands and failed
    -- relays behind an optimistic boolean.
    desired_state   TEXT NOT NULL CHECK (desired_state IN ('on', 'off')),
    desired_at      TEXT NOT NULL,
    reported_state  TEXT CHECK (reported_state IN ('on', 'off', 'unknown')),
    reported_at     TEXT,
    confirmed_state TEXT CHECK (confirmed_state IN ('on', 'off', 'unknown')),
    confirmed_at    TEXT,

    ack_status    TEXT,
    ack_message   TEXT,
    provenance    TEXT NOT NULL
        CHECK (provenance IN ('measured', 'simulated'))
);

CREATE INDEX IF NOT EXISTS idx_actuation_commands_time
    ON actuation_commands(resource_id, desired_at);

-- ---------------------------------------------------------------------
-- Forecasts
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS forecasts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL REFERENCES runs(id),
    issued_at     TEXT    NOT NULL,
    target        TEXT    NOT NULL,   -- 'water_demand_lpm', 'pv_generation_kw', ...
    model_name    TEXT    NOT NULL,
    model_version TEXT    NOT NULL,
    horizon_minutes REAL  NOT NULL
);

CREATE TABLE IF NOT EXISTS forecast_points (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    forecast_id INTEGER NOT NULL REFERENCES forecasts(id) ON DELETE CASCADE,
    target_time TEXT    NOT NULL,
    p50         REAL    NOT NULL,
    p10         REAL,             -- populated from Phase 10
    p90         REAL,
    unit        TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_forecast_points_target_time
    ON forecast_points(forecast_id, target_time);

-- Realized values joined back to forecasts, for walk-forward evaluation.
CREATE TABLE IF NOT EXISTS forecast_errors (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    forecast_point_id INTEGER NOT NULL
        REFERENCES forecast_points(id) ON DELETE CASCADE,
    actual_value      REAL NOT NULL,
    error             REAL NOT NULL,
    absolute_error    REAL NOT NULL,
    evaluated_at      TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- Scheduling decisions
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS scheduler_decisions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            INTEGER NOT NULL REFERENCES runs(id),
    timestamp         TEXT    NOT NULL,
    resource_id       TEXT    NOT NULL REFERENCES resources(resource_id),

    scheduler_name    TEXT    NOT NULL,
    algorithm_version TEXT    NOT NULL,
    tier              INTEGER NOT NULL,
    fallback_engaged  INTEGER NOT NULL DEFAULT 0,

    first_action      TEXT    NOT NULL
        CHECK (first_action IN ('RUN', 'WAIT', 'STOP')),
    reason_code       TEXT    NOT NULL,
    objective_value   REAL,             -- NULL for rule-based schedulers
    solver_status     TEXT    NOT NULL,
    constraints_satisfied INTEGER NOT NULL,
    constraint_violations TEXT,         -- JSON array, NULL when satisfied
    compute_ms        REAL,             -- Phase 12 budget check

    -- The structured explanation the "Why?" screen renders. Never NULL:
    -- a decision without a rationale is not a decision we ship.
    explanation_json  TEXT    NOT NULL,
    provenance        TEXT    NOT NULL
        CHECK (provenance IN ('measured', 'simulated'))
);

CREATE INDEX IF NOT EXISTS idx_scheduler_decisions_time
    ON scheduler_decisions(timestamp);
CREATE INDEX IF NOT EXISTS idx_scheduler_decisions_run
    ON scheduler_decisions(run_id, timestamp);

-- The future plan. Only step 0 is executed; the rest is retained so plan
-- drift can be measured against what actually happened.
CREATE TABLE IF NOT EXISTS planned_actions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id INTEGER NOT NULL
        REFERENCES scheduler_decisions(id) ON DELETE CASCADE,
    step_index  INTEGER NOT NULL,
    target_time TEXT    NOT NULL,
    action      TEXT    NOT NULL CHECK (action IN ('RUN', 'WAIT', 'STOP')),
    predicted_service_level REAL,
    predicted_solar_kw      REAL
);

CREATE INDEX IF NOT EXISTS idx_planned_actions_decision
    ON planned_actions(decision_id, step_index);

-- Predicted trajectory behind a decision. Compared against measured
-- reality in Phase 13.
CREATE TABLE IF NOT EXISTS predicted_states (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id   INTEGER NOT NULL
        REFERENCES scheduler_decisions(id) ON DELETE CASCADE,
    timestamp     TEXT    NOT NULL,
    service_level REAL    NOT NULL,
    native_value  REAL    NOT NULL,
    actuator_on   INTEGER NOT NULL,
    violates_hard_constraint INTEGER NOT NULL DEFAULT 0,
    provenance    TEXT    NOT NULL DEFAULT 'predicted'
        CHECK (provenance = 'predicted')
);

CREATE TABLE IF NOT EXISTS flexibility_estimates (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                   INTEGER NOT NULL REFERENCES runs(id),
    timestamp                TEXT    NOT NULL,
    resource_id              TEXT    NOT NULL REFERENCES resources(resource_id),
    flexibility_minutes      REAL    NOT NULL,
    time_to_critical_minutes REAL    NOT NULL,
    must_run_by              TEXT,
    provenance               TEXT    NOT NULL DEFAULT 'estimated'
        CHECK (provenance = 'estimated')
);

-- ---------------------------------------------------------------------
-- Safety and anomalies
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS safety_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         INTEGER NOT NULL REFERENCES runs(id),
    timestamp      TEXT    NOT NULL,
    resource_id    TEXT    REFERENCES resources(resource_id),
    rule_name      TEXT    NOT NULL,
    priority       INTEGER NOT NULL,
    triggered      INTEGER NOT NULL,
    forced_action  TEXT CHECK (forced_action IN ('RUN', 'WAIT', 'STOP')),
    overridden_action TEXT CHECK (overridden_action IN ('RUN', 'WAIT', 'STOP')),
    decision_id    INTEGER REFERENCES scheduler_decisions(id),
    message        TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_safety_events_time ON safety_events(timestamp);

CREATE TABLE IF NOT EXISTS anomaly_flags (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES runs(id),
    timestamp   TEXT    NOT NULL,
    resource_id TEXT    REFERENCES resources(resource_id),
    detector    TEXT    NOT NULL,
    detector_version TEXT NOT NULL,
    severity    REAL    NOT NULL,
    description TEXT    NOT NULL,
    provenance  TEXT    NOT NULL DEFAULT 'estimated'
        CHECK (provenance = 'estimated')
);

-- ---------------------------------------------------------------------
-- Experiments
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS experiments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    created_at   TEXT    NOT NULL,
    description  TEXT    NOT NULL DEFAULT '',
    config_hash  TEXT    NOT NULL,
    config_json  TEXT    NOT NULL,
    scenario_set TEXT
);

CREATE TABLE IF NOT EXISTS metrics (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL REFERENCES runs(id),
    experiment_id INTEGER REFERENCES experiments(id),
    computed_at   TEXT    NOT NULL,
    name          TEXT    NOT NULL,   -- 'solar_fraction', 'grid_kwh', ...
    value         REAL    NOT NULL,
    unit          TEXT    NOT NULL DEFAULT '',
    -- A metric is only as trustworthy as its inputs: a solar fraction
    -- computed over simulated data is never reported as a measured result.
    provenance    TEXT    NOT NULL
        CHECK (provenance IN ('measured', 'simulated', 'derived', 'estimated'))
);

CREATE INDEX IF NOT EXISTS idx_metrics_run ON metrics(run_id, name);
