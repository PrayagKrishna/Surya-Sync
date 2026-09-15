"""Pump physics and the equipment-protection gate.

``admissible_actions`` is the one answer a scheduler is never allowed to
get wrong, so most of this file is that table. The failure direction is
asserted explicitly: unknown timing must hold the actuator, never permit a
switch.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from surya_sync.config.schema import PumpConfig
from surya_sync.domain import ControlAction
from surya_sync.models.generic_resource import ActuationHistory
from surya_sync.models.pump import PumpModel

NOW = datetime(2026, 3, 1, 9, 0)


@pytest.fixture
def pump() -> PumpModel:
    return PumpModel(
        rated_power_kw=0.75,
        flow_rate_lpm=30.0,
        min_on_minutes=5.0,
        min_off_minutes=10.0,
        max_starts_per_day=12,
    )


def history(
    *, actuator_on: bool, minutes_ago: float | None, starts_today: int = 0
) -> ActuationHistory:
    changed_at = None if minutes_ago is None else NOW - timedelta(minutes=minutes_ago)
    return ActuationHistory(
        resource_id="tank_1",
        actuator_on=actuator_on,
        changed_at=changed_at,
        starts_today=starts_today,
    )


# --- construction -------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"rated_power_kw": 0.0},
        {"rated_power_kw": -1.0},
        {"flow_rate_lpm": 0.0},
        {"min_on_minutes": -1.0},
        {"min_off_minutes": -1.0},
        {"max_starts_per_day": 0},
    ],
)
def test_impossible_pump_parameters_are_rejected(kwargs):
    base = {"rated_power_kw": 0.75, "flow_rate_lpm": 30.0}
    with pytest.raises(ValueError):
        PumpModel(**{**base, **kwargs})


def test_from_config_carries_every_limit(pump):
    assert PumpModel.from_config(PumpConfig()) == pump


# --- physics ------------------------------------------------------------


def test_inflow_is_rated_flow_or_nothing(pump):
    assert pump.inflow_lpm(True) == pytest.approx(30.0)
    assert pump.inflow_lpm(False) == 0.0


def test_delivered_volume_scales_with_runtime(pump):
    assert pump.delivered_l(10.0) == pytest.approx(300.0)


def test_energy_is_rated_power_over_the_hour(pump):
    assert pump.energy_kwh(60.0) == pytest.approx(0.75)
    assert pump.energy_kwh(30.0) == pytest.approx(0.375)


def test_minutes_to_deliver_inverts_delivered_volume(pump):
    assert pump.minutes_to_deliver(pump.delivered_l(17.0)) == pytest.approx(17.0)


@pytest.mark.parametrize("method", ["delivered_l", "energy_kwh", "minutes_to_deliver"])
def test_negative_quantities_are_rejected(pump, method):
    with pytest.raises(ValueError):
        getattr(pump, method)(-1.0)


# --- the admissibility table --------------------------------------------


def test_an_idle_pump_may_always_keep_waiting(pump):
    assert ControlAction.WAIT in pump.admissible_actions(
        False, history(actuator_on=False, minutes_ago=0.0), NOW
    )


def test_a_rested_pump_may_start(pump):
    actions = pump.admissible_actions(
        False, history(actuator_on=False, minutes_ago=15.0), NOW
    )
    assert set(actions) == {ControlAction.WAIT, ControlAction.RUN}


def test_a_pump_inside_its_cooldown_may_not_start(pump):
    actions = pump.admissible_actions(
        False, history(actuator_on=False, minutes_ago=4.0), NOW
    )
    assert actions == (ControlAction.WAIT,)


def test_the_cooldown_boundary_is_inclusive(pump):
    """Exactly min_off elapsed counts as elapsed, or the limit is really
    min_off plus one control cycle."""
    actions = pump.admissible_actions(
        False, history(actuator_on=False, minutes_ago=10.0), NOW
    )
    assert ControlAction.RUN in actions


def test_the_daily_start_budget_blocks_a_rested_pump(pump):
    actions = pump.admissible_actions(
        False, history(actuator_on=False, minutes_ago=999.0, starts_today=12), NOW
    )
    assert actions == (ControlAction.WAIT,)


def test_one_start_below_the_budget_is_still_allowed(pump):
    actions = pump.admissible_actions(
        False, history(actuator_on=False, minutes_ago=999.0, starts_today=11), NOW
    )
    assert ControlAction.RUN in actions


def test_no_start_budget_means_no_limit():
    pump = PumpModel(rated_power_kw=0.75, flow_rate_lpm=30.0, max_starts_per_day=None)
    actions = pump.admissible_actions(
        False, history(actuator_on=False, minutes_ago=999.0, starts_today=9999), NOW
    )
    assert ControlAction.RUN in actions


def test_a_running_pump_may_always_keep_running(pump):
    assert ControlAction.RUN in pump.admissible_actions(
        True, history(actuator_on=True, minutes_ago=0.0), NOW
    )


def test_a_pump_past_its_minimum_run_may_stop(pump):
    actions = pump.admissible_actions(True, history(actuator_on=True, minutes_ago=6.0), NOW)
    assert set(actions) == {ControlAction.RUN, ControlAction.STOP}


def test_a_pump_inside_its_minimum_run_may_not_stop(pump):
    actions = pump.admissible_actions(True, history(actuator_on=True, minutes_ago=2.0), NOW)
    assert actions == (ControlAction.RUN,)


def test_wait_is_never_offered_to_a_running_pump(pump):
    """``WAIT`` means stay de-energized and ``STOP`` means de-energize. Only
    one of them is meaningful in each state; offering both would let a
    scheduler express the same intent two ways."""
    for minutes_ago in (0.0, 2.0, 30.0):
        actions = pump.admissible_actions(
            True, history(actuator_on=True, minutes_ago=minutes_ago), NOW
        )
        assert ControlAction.WAIT not in actions


def test_stop_is_never_offered_to_an_idle_pump(pump):
    for minutes_ago in (0.0, 2.0, 30.0):
        actions = pump.admissible_actions(
            False, history(actuator_on=False, minutes_ago=minutes_ago), NOW
        )
        assert ControlAction.STOP not in actions


def test_the_returned_set_is_never_empty(pump):
    """Holding is always legal, so there is always at least one action."""
    for on in (True, False):
        for minutes_ago in (None, 0.0, 3.0, 999.0):
            actions = pump.admissible_actions(
                on, history(actuator_on=on, minutes_ago=minutes_ago), NOW
            )
            assert actions


# --- the failure direction ----------------------------------------------


def test_a_missing_history_forbids_starting(pump):
    """The Phase 0 review's carried-forward rule: ``None`` means unknown,
    and an unknown reads as "constraint not satisfied"."""
    assert pump.admissible_actions(False, None, NOW) == (ControlAction.WAIT,)


def test_a_missing_history_forbids_stopping(pump):
    assert pump.admissible_actions(True, None, NOW) == (ControlAction.RUN,)


def test_unknown_transition_time_is_not_treated_as_long_ago(pump):
    """``changed_at=None`` must not read as a cooldown that has elapsed —
    that is exactly the mistake that short-cycles a pump."""
    unknown = ActuationHistory.unknown("tank_1", actuator_on=False)
    assert pump.admissible_actions(False, unknown, NOW) == (ControlAction.WAIT,)
    assert not pump.may_start(unknown, NOW)


def test_unknown_timing_blocks_even_a_zero_cooldown():
    """A zero limit is still evaluated against unknown timing rather than
    special-cased. Being strict here costs one held cycle; being lenient
    costs a pump."""
    pump = PumpModel(
        rated_power_kw=0.75, flow_rate_lpm=30.0, min_on_minutes=0.0, min_off_minutes=0.0
    )
    assert pump.admissible_actions(False, None, NOW) == (ControlAction.WAIT,)
