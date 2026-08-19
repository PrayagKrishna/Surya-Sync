"""Database schema — Phase 0 exit criterion: SQLite schema created."""

from __future__ import annotations

import sqlite3

import pytest

from surya_sync.storage.database import Database, SchemaVersionError
from surya_sync.version import DB_SCHEMA_VERSION

EXPECTED_TABLES = {
    "actuation_commands",
    "anomaly_flags",
    "component_versions",
    "experiments",
    "flexibility_estimates",
    "forecast_errors",
    "forecast_points",
    "forecasts",
    "metrics",
    "planned_actions",
    "power_readings",
    "predicted_states",
    "resource_observations",
    "resources",
    "runs",
    "safety_events",
    "schema_version",
    "scheduler_decisions",
    "sensor_readings",
}


@pytest.fixture
def db(tmp_path):
    with Database(tmp_path / "test.db") as database:
        database.initialize_schema()
        yield database


def test_schema_creates_all_tables(db):
    assert set(db.table_names()) == EXPECTED_TABLES


def test_schema_version_recorded(db):
    assert db.schema_version() == DB_SCHEMA_VERSION


def test_initialize_is_idempotent(tmp_path):
    path = tmp_path / "test.db"
    with Database(path) as database:
        assert database.initialize_schema() == DB_SCHEMA_VERSION
    with Database(path) as database:
        assert database.initialize_schema() == DB_SCHEMA_VERSION
        rows = database.connection.execute(
            "SELECT COUNT(*) AS n FROM schema_version"
        ).fetchone()
        assert rows["n"] == 1


def test_uninitialized_database_reports_no_version(tmp_path):
    with Database(tmp_path / "empty.db") as database:
        assert database.schema_version() is None


def test_mismatched_schema_version_is_fatal(tmp_path, monkeypatch):
    """Refusing to open a mismatched database is what keeps experiment
    results comparable across code versions."""
    path = tmp_path / "test.db"
    with Database(path) as database:
        database.initialize_schema()
        database.connection.execute(
            "INSERT INTO schema_version (version, applied_at, description) "
            "VALUES (?, datetime('now'), 'from the future')",
            (DB_SCHEMA_VERSION + 1,),
        )
        database.connection.commit()

    with Database(path) as database:
        with pytest.raises(SchemaVersionError, match="schema version"):
            database.initialize_schema()


def test_database_creates_parent_directories(tmp_path):
    path = tmp_path / "nested" / "deeper" / "test.db"
    with Database(path) as database:
        database.initialize_schema()
    assert path.is_file()


def test_foreign_keys_are_enforced(db):
    """The version/provenance spine is only real if the DB enforces it."""
    with pytest.raises(sqlite3.IntegrityError):
        db.connection.execute(
            "INSERT INTO runs (started_at, mode, component_version_id) "
            "VALUES (datetime('now'), 'simulated', 9999)"
        )


def test_provenance_is_constrained(db):
    """Simulated results must not be storable as measured by accident."""
    db.connection.execute(
        "INSERT INTO component_versions (recorded_at, backend_version, "
        "db_schema_version, serial_protocol_version, water_model_version, "
        "pv_model_version, config_hash, config_json) "
        "VALUES (datetime('now'), '0.1.0', 1, '1.0', '0.0.0', '0.0.0', 'h', '{}')"
    )
    db.connection.execute(
        "INSERT INTO runs (id, started_at, mode, component_version_id) "
        "VALUES (1, datetime('now'), 'simulated', 1)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.connection.execute(
            "INSERT INTO power_readings (run_id, timestamp, provenance) "
            "VALUES (1, datetime('now'), 'vibes')"
        )


def test_run_mode_is_constrained(db):
    db.connection.execute(
        "INSERT INTO component_versions (recorded_at, backend_version, "
        "db_schema_version, serial_protocol_version, water_model_version, "
        "pv_model_version, config_hash, config_json) "
        "VALUES (datetime('now'), '0.1.0', 1, '1.0', '0.0.0', '0.0.0', 'h', '{}')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.connection.execute(
            "INSERT INTO runs (started_at, mode, component_version_id) "
            "VALUES (datetime('now'), 'guessing', 1)"
        )


def test_control_action_is_constrained(db):
    """RUN / WAIT / STOP is the whole action space, at every layer."""
    db.connection.execute(
        "INSERT INTO component_versions (recorded_at, backend_version, "
        "db_schema_version, serial_protocol_version, water_model_version, "
        "pv_model_version, config_hash, config_json) "
        "VALUES (datetime('now'), '0.1.0', 1, '1.0', '0.0.0', '0.0.0', 'h', '{}')"
    )
    db.connection.execute(
        "INSERT INTO runs (id, started_at, mode, component_version_id) "
        "VALUES (1, datetime('now'), 'simulated', 1)"
    )
    db.connection.execute(
        "INSERT INTO resources (resource_id, resource_type, model_version, "
        "native_unit, rated_power_kw, service_level_critical, service_level_min, "
        "service_level_max, created_at) "
        "VALUES ('tank_1', 'water_tank', '0.0.0', 'L', 0.75, 0.2, 0.3, 0.95, "
        "datetime('now'))"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.connection.execute(
            "INSERT INTO scheduler_decisions (run_id, timestamp, resource_id, "
            "scheduler_name, algorithm_version, tier, first_action, reason_code, "
            "solver_status, constraints_satisfied, explanation_json, provenance) "
            "VALUES (1, datetime('now'), 'tank_1', 'threshold', '0.1.0', 4, "
            "'MAYBE', 'sufficient_level', 'not_applicable', 1, '{}', 'simulated')"
        )


def _check_clause(db, table: str, column: str) -> str:
    """The CREATE TABLE text for one table, as SQLite stored it."""
    row = db.connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    assert row is not None, f"missing table {table}"
    return row[0]


def test_reason_code_vocabulary_matches_the_enum(db):
    """``ReasonCode`` is documented as a closed vocabulary. If the database
    accepts a value the enum does not define — or rejects one it does — the
    two have drifted and the "Why?" screen will meet a code it cannot
    render."""
    from surya_sync.scheduler.base import ReasonCode

    sql = _check_clause(db, "scheduler_decisions", "reason_code")
    for code in ReasonCode:
        assert f"'{code.value}'" in sql, f"{code.value} missing from DB CHECK"


def test_solver_status_vocabulary_matches_the_enum(db):
    from surya_sync.scheduler.base import SolverStatus

    sql = _check_clause(db, "scheduler_decisions", "solver_status")
    for status in SolverStatus:
        assert f"'{status.value}'" in sql, f"{status.value} missing from DB CHECK"


def _seed_run(db) -> None:
    db.connection.execute(
        "INSERT INTO component_versions (recorded_at, backend_version, "
        "db_schema_version, serial_protocol_version, water_model_version, "
        "pv_model_version, config_hash, config_json) "
        "VALUES (datetime('now'), '0.1.0', 1, '1.0', '0.0.0', '0.0.0', 'h', '{}')"
    )
    db.connection.execute(
        "INSERT INTO runs (id, started_at, mode, component_version_id) "
        "VALUES (1, datetime('now'), 'simulated', 1)"
    )
    db.connection.execute(
        "INSERT INTO resources (resource_id, resource_type, model_version, "
        "native_unit, rated_power_kw, service_level_critical, "
        "service_level_min, service_level_max, created_at) "
        "VALUES ('tank_1', 'water_tank', '0.0.0', 'L', 0.75, 0.2, 0.3, 0.95, "
        "datetime('now'))"
    )


def _insert_decision(db, *, reason_code: str, solver_status: str) -> None:
    db.connection.execute(
        "INSERT INTO scheduler_decisions (run_id, timestamp, resource_id, "
        "scheduler_name, algorithm_version, tier, first_action, reason_code, "
        "solver_status, constraints_satisfied, explanation_json, provenance) "
        "VALUES (1, datetime('now'), 'tank_1', 'threshold', '0.1.0', 4, "
        "'WAIT', ?, ?, 1, '{}', 'simulated')",
        (reason_code, solver_status),
    )


def test_reason_code_is_constrained(db):
    """A typo'd or invented reason must not reach the decision log."""
    _seed_run(db)
    _insert_decision(db, reason_code="sufficient_level", solver_status="not_applicable")

    with pytest.raises(sqlite3.IntegrityError):
        _insert_decision(
            db, reason_code="because_i_said_so", solver_status="not_applicable"
        )


def test_solver_status_is_constrained(db):
    _seed_run(db)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_decision(db, reason_code="sufficient_level", solver_status="probably")


def test_fallback_is_recorded_separately_from_the_reason(db):
    """Falling back is not a rationale. The decision keeps its substantive
    reason and flags the fallback alongside, so neither is lost."""
    _seed_run(db)
    db.connection.execute(
        "INSERT INTO scheduler_decisions (run_id, timestamp, resource_id, "
        "scheduler_name, algorithm_version, tier, fallback_engaged, "
        "first_action, reason_code, solver_status, constraints_satisfied, "
        "explanation_json, provenance) "
        "VALUES (1, datetime('now'), 'tank_1', 'threshold', '0.1.0', 4, 1, "
        "'RUN', 'critical_level', 'not_applicable', 1, '{}', 'simulated')"
    )
    row = db.connection.execute(
        "SELECT fallback_engaged, reason_code FROM scheduler_decisions"
    ).fetchone()
    assert row[0] == 1
    assert row[1] == "critical_level"
