"""``ObservationRepository.history`` — the first implemented repository
method, and the Phase 4 exit criterion's data source.

No write method exists yet (every ``Observation``/``Run``/``Resource``
repository writer is still ``NotImplementedError`` — Phases 1/11), so rows
are seeded with direct SQL, mirroring ``tests/test_storage_schema.py``'s
``_seed_run`` idiom.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from surya_sync.domain import Provenance
from surya_sync.storage.database import Database
from surya_sync.storage.repositories import ObservationRepository

START = datetime(2026, 3, 2, 10, 0)


@pytest.fixture
def db():
    with Database(":memory:") as database:
        database.initialize_schema()
        yield database


def _seed_run(db) -> None:
    db.connection.execute(
        "INSERT INTO component_versions (recorded_at, backend_version, "
        "db_schema_version, serial_protocol_version, water_model_version, "
        "pv_model_version, config_hash, config_json) "
        "VALUES (datetime('now'), '0.1.0', 2, '1.0', '1.0.0', '1.0.0', 'h', '{}')"
    )
    db.connection.execute(
        "INSERT INTO runs (id, started_at, mode, component_version_id) "
        "VALUES (1, datetime('now'), 'simulated', 1)"
    )
    db.connection.execute(
        "INSERT INTO resources (resource_id, resource_type, model_version, "
        "native_unit, rated_power_kw, service_level_critical, "
        "service_level_min, service_level_max, created_at) "
        "VALUES ('tank_1', 'water_tank', '1.0.0', 'L', 0.75, 0.2, 0.3, 0.95, "
        "datetime('now'))"
    )


def _insert_observation(
    db, *, minutes_offset: int, service_level: float, provenance: str, actuator_on: int = 0
) -> None:
    timestamp = (START + timedelta(minutes=minutes_offset)).isoformat()
    db.connection.execute(
        "INSERT INTO resource_observations (run_id, timestamp, resource_id, "
        "service_level, native_value, native_unit, actuator_on, sensor_valid, "
        "provenance) VALUES (1, ?, 'tank_1', ?, ?, 'L', ?, 1, ?)",
        (timestamp, service_level, service_level * 1000.0, actuator_on, provenance),
    )


def test_history_returns_rows_in_chronological_order(db):
    """Rows are inserted out of order; the query, not insertion order, must
    produce the walk-forward sequence every downstream consumer assumes."""
    _seed_run(db)
    _insert_observation(db, minutes_offset=30, service_level=0.7, provenance="simulated")
    _insert_observation(db, minutes_offset=0, service_level=0.5, provenance="simulated")
    _insert_observation(db, minutes_offset=15, service_level=0.6, provenance="simulated")
    db.connection.commit()

    repo = ObservationRepository(db)
    history = repo.history("tank_1", START, START.replace(hour=23, minute=59))

    assert [h.timestamp for h in history] == sorted(h.timestamp for h in history)
    assert [h.service_level for h in history] == [0.5, 0.6, 0.7]


def test_history_window_is_half_open(db):
    """``since`` is inclusive, ``until`` is exclusive — a row stamped
    exactly at ``until`` belongs to the *next* window, not this one, or two
    adjacent windows would double-count it."""
    _seed_run(db)
    _insert_observation(db, minutes_offset=0, service_level=0.5, provenance="simulated")
    _insert_observation(db, minutes_offset=60, service_level=0.6, provenance="simulated")
    db.connection.commit()

    repo = ObservationRepository(db)
    history = repo.history("tank_1", START, START.replace(hour=11, minute=0))

    assert len(history) == 1
    assert history[0].service_level == 0.5


def test_history_defaults_to_every_provenance(db):
    """A caller that does not ask to filter gets everything — filtering by
    default would silently drop rows a Phase-0-era caller expects to see."""
    _seed_run(db)
    _insert_observation(db, minutes_offset=0, service_level=0.5, provenance="simulated")
    _insert_observation(db, minutes_offset=15, service_level=0.6, provenance="measured")
    db.connection.commit()

    repo = ObservationRepository(db)
    history = repo.history("tank_1", START, START.replace(hour=23, minute=59))

    assert len(history) == 2


def test_history_can_filter_to_one_provenance(db):
    """From Phase 13 on, measured and simulated rows for the same resource
    coexist. A model trained on a silent mixture cannot tell which
    instrument produced which sample, so filtering must be explicit."""
    _seed_run(db)
    _insert_observation(db, minutes_offset=0, service_level=0.5, provenance="simulated")
    _insert_observation(db, minutes_offset=15, service_level=0.6, provenance="measured")
    db.connection.commit()

    repo = ObservationRepository(db)
    history = repo.history(
        "tank_1",
        START,
        START.replace(hour=23, minute=59),
        provenance=Provenance.MEASURED,
    )

    assert len(history) == 1
    assert history[0].service_level == 0.6


def test_history_round_trips_actuator_and_validity_flags(db):
    _seed_run(db)
    _insert_observation(
        db, minutes_offset=0, service_level=0.5, provenance="simulated", actuator_on=1
    )
    db.connection.execute(
        "UPDATE resource_observations SET sensor_valid = 0 WHERE actuator_on = 1"
    )
    db.connection.commit()

    repo = ObservationRepository(db)
    history = repo.history("tank_1", START, START.replace(hour=23, minute=59))

    assert history[0].actuator_on is True
    assert history[0].sensor_valid is False
    assert history[0].provenance is Provenance.SIMULATED
