"""System state — in particular the desired/reported/confirmed invariant."""

from __future__ import annotations

from datetime import datetime

from surya_sync.domain import Provenance
from surya_sync.state.system_state import (
    ActuationState,
    ActuatorState,
    ElectricalState,
)


def test_actuation_tracks_three_independent_states():
    """Hard rule: command sent != command executed. Collapsing these into
    one boolean is how a control system starts lying about reality."""
    fields = set(ActuationState.__dataclass_fields__)
    assert {"desired", "reported", "confirmed"} <= fields
    assert {"desired_at", "reported_at", "confirmed_at"} <= fields


def test_unknown_state_is_not_off():
    """A stale actuator must never be assumed de-energized."""
    state = ActuationState.unknown()
    assert state.desired is ActuatorState.UNKNOWN
    assert state.desired is not ActuatorState.OFF
    assert state.is_consistent is True  # consistently unknown


def test_divergence_is_detectable():
    now = datetime(2026, 8, 17, 9, 0)
    diverged = ActuationState(
        desired=ActuatorState.ON,
        desired_at=now,
        reported=ActuatorState.ON,
        reported_at=now,
        confirmed=ActuatorState.OFF,  # relay failed, or pump is dry
        confirmed_at=now,
    )
    assert diverged.is_consistent is False


def test_surplus_is_generation_minus_base_load():
    electrical = ElectricalState(
        timestamp=datetime(2026, 8, 17, 12, 0),
        pv_generation_kw=2.4,
        base_load_kw=0.9,
        grid_import_kw=0.0,
        provenance=Provenance.MEASURED,
    )
    assert electrical.surplus_kw == 1.5


def test_surplus_never_negative():
    electrical = ElectricalState(
        timestamp=datetime(2026, 8, 17, 19, 0),
        pv_generation_kw=0.0,
        base_load_kw=1.2,
        grid_import_kw=1.2,
        provenance=Provenance.MEASURED,
    )
    assert electrical.surplus_kw == 0.0


def test_surplus_unknown_when_inputs_missing():
    """Unmeasured is not zero — a scheduler must be able to tell them apart."""
    electrical = ElectricalState(
        timestamp=datetime(2026, 8, 17, 12, 0),
        pv_generation_kw=None,
        base_load_kw=0.9,
        grid_import_kw=None,
        provenance=Provenance.MEASURED,
    )
    assert electrical.surplus_kw is None
