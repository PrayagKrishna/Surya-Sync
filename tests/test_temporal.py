"""Temporal feature engineering — Phase 4 exit criterion: ``temporal/``
produces feature vectors matching the pinned feature list (``ROADMAP.md``
Phase 4; the list itself lives in
``ml.features.builder.FEATURE_NAMES`` because the original "section 12"
design doc is not in this repo — see the carried-forward note in
``CLAUDE.md``).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import pytest

from surya_sync.config.schema import Config
from surya_sync.domain import Provenance, TimedValue
from surya_sync.models.generic_resource import ResourceObservation
from surya_sync.models.pump import PumpModel
from surya_sync.models.tank import TankModel, observed_demand_lpm, observed_demand_series
from surya_sync.ml.features.builder import FEATURE_NAMES, FeatureVector, build_feature_vector
from surya_sync.scheduler.threshold import ThresholdScheduler
from surya_sync.simulator import scenarios
from surya_sync.temporal.context import (
    DayType,
    build_temporal_context,
    cyclic,
    slot_index_of,
    slots_per_day,
)
from surya_sync.temporal.history import rolling_stats, value_at, value_at_lag
from surya_sync.temporal.profiles import build_slot_profile
from surya_sync.experiments.runner import run_scenario
from surya_sync.version import FEATURE_SET_VERSION

MIDNIGHT = datetime(2026, 3, 2, 0, 0)
"""A Monday — see ``test_profiles.py``'s identical constant."""

RESOURCE_ID = "tank_1"


def _tv(at, value, provenance=Provenance.SIMULATED) -> TimedValue:
    """Shorthand for building test ``TimedValue`` fixtures — the provenance
    rarely matters to what's being asserted, but the field is required
    (no default), matching ``ResourceObservation``."""
    return TimedValue(at=at, value=value, provenance=provenance)


@pytest.fixture
def config() -> Config:
    return Config()


# --- cyclic encoding ------------------------------------------------------


def test_midnight_and_the_next_midnight_encode_identically():
    today = build_temporal_context(MIDNIGHT, slot_minutes=15.0)
    tomorrow = build_temporal_context(MIDNIGHT + timedelta(days=1), slot_minutes=15.0)
    assert today.tod_sin == pytest.approx(tomorrow.tod_sin, abs=1e-9)
    assert today.tod_cos == pytest.approx(tomorrow.tod_cos, abs=1e-9)


def test_2359_sits_next_to_0001_on_the_circle():
    """A raw hour number puts 23:59 and 00:01 twenty-three apart. The
    cyclic encoding must put them next to each other instead."""
    late = build_temporal_context(MIDNIGHT + timedelta(hours=23, minutes=59), 15.0)
    early = build_temporal_context(MIDNIGHT + timedelta(minutes=1), 15.0)
    distance = math.hypot(late.tod_sin - early.tod_sin, late.tod_cos - early.tod_cos)
    assert distance < 0.01


def test_sin_squared_plus_cos_squared_is_one():
    sin, cos = cyclic(37.0, 100.0)
    assert sin**2 + cos**2 == pytest.approx(1.0)


@pytest.mark.parametrize(
    "offset,expected_index",
    [(timedelta(0), 0), (timedelta(hours=23, minutes=45), 95)],
)
def test_slot_index_at_the_edges_of_the_day(offset, expected_index):
    assert slot_index_of(MIDNIGHT + offset, slot_minutes=15.0) == expected_index


def test_a_slot_minutes_that_does_not_divide_a_day_is_refused():
    with pytest.raises(ValueError, match="does not divide"):
        slots_per_day(7.0)


def test_feb_29_and_dec_31_encode_without_a_phase_jump():
    """Dividing by the wrong year-length would put Dec 31 and Jan 1 of a
    leap year noticeably apart on the circle instead of adjacent."""
    leap_day = build_temporal_context(datetime(2028, 2, 29, 12, 0), 15.0)
    day_after = build_temporal_context(datetime(2028, 3, 1, 12, 0), 15.0)
    distance = math.hypot(
        leap_day.doy_sin - day_after.doy_sin, leap_day.doy_cos - day_after.doy_cos
    )
    assert distance < 0.02


def test_weekday_and_weekend_use_the_same_definition_as_the_demand_profile():
    """A second weekend definition here would silently disagree with the
    profile that generated the training data — see
    ``simulator/demand.py:176``."""
    sunday = datetime(2026, 3, 1, 12, 0)
    assert sunday.weekday() >= 5
    assert build_temporal_context(sunday, 15.0).day_type is DayType.WEEKEND
    assert build_temporal_context(MIDNIGHT, 15.0).day_type is DayType.WEEKDAY


def test_context_is_pure_in_time():
    """Same property ``test_demand_is_pure_in_time`` holds the simulator
    profiles to, and for the same reason: MPC re-queries the same instant
    across replans."""
    moments = [MIDNIGHT + timedelta(minutes=17 * k) for k in range(100)]
    forward = [build_temporal_context(m, 15.0).tod_sin for m in moments]
    backward = [build_temporal_context(m, 15.0).tod_sin for m in reversed(moments)]
    assert forward == list(reversed(backward))


# --- slot profile -----------------------------------------------------------


def test_the_slot_mean_covers_only_matching_slots():
    samples = (
        _tv(MIDNIGHT + timedelta(hours=10), 10.0),
        _tv(MIDNIGHT + timedelta(hours=10, minutes=5), 20.0),
        _tv(MIDNIGHT + timedelta(hours=11), 1000.0),  # different slot
    )
    profile = build_slot_profile(samples, slot_minutes=15.0, min_samples=1)
    assert profile.mean_for(MIDNIGHT + timedelta(hours=10, minutes=2)) == pytest.approx(15.0)


def test_weekday_and_weekend_slots_are_kept_separate():
    weekday_sample = _tv(MIDNIGHT + timedelta(hours=10), 10.0)
    weekend_sample = _tv(MIDNIGHT + timedelta(days=5, hours=10), 90.0)  # the following Saturday
    profile = build_slot_profile(
        (weekday_sample, weekend_sample), slot_minutes=15.0, min_samples=1
    )
    assert profile.mean_for(MIDNIGHT + timedelta(hours=10)) == pytest.approx(10.0)
    assert profile.mean_for(
        MIDNIGHT + timedelta(days=5, hours=10)
    ) == pytest.approx(90.0)


def test_below_min_samples_reports_unknown_not_zero():
    samples = (_tv(MIDNIGHT + timedelta(hours=10), 10.0),)
    profile = build_slot_profile(samples, slot_minutes=15.0, min_samples=3)
    assert profile.mean_for(MIDNIGHT + timedelta(hours=10)) is None
    assert profile.sample_count_for(MIDNIGHT + timedelta(hours=10)) == 1


def test_a_standard_set_run_leaves_every_weekend_slot_unknown(config):
    """``scenarios.DEFAULT_START`` is a Monday and the standard set runs 3
    days, so a standard-set run supplies zero weekend samples. The profile
    must say unknown, never borrow the weekday mean."""
    scenario = scenarios.cloudy(config)
    assert scenario.start.weekday() < 5
    assert scenario.days == 3.0
    run = run_scenario(config, scenario, ThresholdScheduler(config.scheduler))

    samples = tuple(
        _tv(step.start, step.demand_lpm) for step in run.steps
    )
    profile = build_slot_profile(samples, slot_minutes=15.0, min_samples=1)
    saturday_slot = scenario.start + timedelta(days=5, hours=10)
    assert profile.mean_for(saturday_slot) is None


# --- lag / rolling ------------------------------------------------------


def test_a_gap_yields_unknown_rather_than_the_neighbouring_sample():
    """Regression-idiom: an index-based lag (``series[-1]``) would silently
    return whichever sample happens to be nearby in the list, even when
    the lag target falls in the middle of a gap neither neighbour is
    close enough to cover."""
    series = (
        _tv(MIDNIGHT, 1.0),
        _tv(MIDNIGHT + timedelta(hours=2), 99.0),
    )
    now = MIDNIGHT + timedelta(hours=3)  # both samples are in the past
    # target = now - 120min = 1:00, exactly midway between the two samples,
    # 60 minutes from each — outside a 10-minute tolerance on both sides.
    assert value_at_lag(series, now, lag_minutes=120.0, tolerance_minutes=10.0) is None


def test_nothing_at_or_after_now_can_change_a_lag_or_rolling_feature():
    """A demand sample stamped at ``now`` describes an interval only just
    beginning — using it in a feature meant to describe the past is target
    leakage. Injecting a huge spike at and after ``now`` must not move any
    value computed for ``now``."""
    now = MIDNIGHT + timedelta(hours=10)
    past = (_tv(now - timedelta(minutes=30), 5.0),)
    with_future_spike = past + (
        _tv(now, 9999.0),
        _tv(now + timedelta(minutes=10), 9999.0),
    )

    assert value_at_lag(
        past, now, lag_minutes=30.0, tolerance_minutes=7.5
    ) == value_at_lag(with_future_spike, now, lag_minutes=30.0, tolerance_minutes=7.5)
    assert rolling_stats(past, now, window_minutes=60.0).mean == rolling_stats(
        with_future_spike, now, window_minutes=60.0
    ).mean


def test_rolling_mean_and_max_cover_only_the_window():
    now = MIDNIGHT + timedelta(hours=10)
    series = (
        _tv(now - timedelta(minutes=90), 1000.0),  # outside 1h window
        _tv(now - timedelta(minutes=30), 2.0),
        _tv(now - timedelta(minutes=10), 4.0),
    )
    stats = rolling_stats(series, now, window_minutes=60.0)
    assert stats.count == 2
    assert stats.mean == pytest.approx(3.0)
    assert stats.max == pytest.approx(4.0)


def test_rolling_stats_reports_unknown_for_an_empty_window():
    assert rolling_stats((), MIDNIGHT, window_minutes=60.0) is None


def test_rolling_window_includes_the_sample_exactly_at_its_far_edge():
    """Regression. An earlier version excluded both edges of the window,
    so on the ordinary fixed-cadence control loop — samples every
    ``step_minutes``, a window that is a whole multiple of it — the oldest
    sample landed exactly on ``now - window_minutes`` and was silently
    dropped on *every single call*: a '1h rolling mean' over a 15-minute
    grid reported 3 samples instead of 4, not a rare edge case but the
    normal one."""
    now = MIDNIGHT + timedelta(hours=10)
    series = tuple(
        _tv(now - timedelta(minutes=15 * k), float(k)) for k in range(1, 5)
    )  # samples at t-15, t-30, t-45, t-60
    stats = rolling_stats(series, now, window_minutes=60.0)
    assert stats.count == 4
    assert stats.mean == pytest.approx((1.0 + 2.0 + 3.0 + 4.0) / 4.0)


def test_value_at_includes_the_sample_at_now_unlike_value_at_lag():
    """``value_at`` is for state snapshots (e.g. ``service_level``), where
    the reading taken at ``now`` is legitimately known at decision time —
    unlike an interval-start demand sample."""
    now = MIDNIGHT + timedelta(hours=10)
    series = (_tv(now, 0.42),)
    assert value_at(series, now, tolerance_minutes=1.0) == pytest.approx(0.42)
    assert value_at_lag(series, now, lag_minutes=0.0, tolerance_minutes=1.0) is None


# --- derived demand (models.tank) ---------------------------------------


@pytest.fixture
def pump(config) -> PumpModel:
    return PumpModel.from_config(config.pump)


@pytest.fixture
def tank(config) -> TankModel:
    return TankModel.from_config(config.tank)


def _observations_from_steps(steps) -> tuple[ResourceObservation, ...]:
    """Turn a scenario run's ``SimulationStep`` sequence into the raw
    tank observations a real deployment would have recorded at each
    interval boundary.

    Each boundary observation's ``actuator_on`` must reflect the interval
    *starting* there, not the one that just ended — that is what
    ``observed_demand_lpm`` reads as ``previous.actuator_on`` when this
    observation becomes ``previous`` for the next interval. Getting this
    backwards silently mixes the wrong pump state into the inverse.
    """
    observations = [_observe(steps[0].start, steps[0].volume_start_l, steps[0].actuator_on)]
    for i, step in enumerate(steps):
        upcoming_actuator_on = steps[i + 1].actuator_on if i + 1 < len(steps) else step.actuator_on
        observations.append(_observe(step.end, step.volume_end_l, upcoming_actuator_on))
    return tuple(observations)


def _observe(at, native_value, actuator_on, sensor_valid=True) -> ResourceObservation:
    return ResourceObservation(
        timestamp=at,
        resource_id=RESOURCE_ID,
        service_level=native_value / 1000.0,
        native_value=native_value,
        native_unit="L",
        actuator_on=actuator_on,
        provenance=Provenance.SIMULATED,
        sensor_valid=sensor_valid,
    )


def test_observed_demand_round_trips_tank_model_step_exactly(tank, pump):
    """Mirrors the existing ``predict_trajectory``/``advance`` agreement
    assertion: the inverse must recover exactly what ``TankModel.step``
    produced, for an interval that neither overflowed nor ran dry."""
    start_volume = 500.0
    outcome = tank.step(
        volume_l=start_volume, inflow_lpm=pump.flow_rate_lpm, demand_lpm=12.0, minutes=10.0
    )
    assert not outcome.overflowed and not outcome.ran_dry

    previous = _observe(MIDNIGHT, start_volume, actuator_on=True)
    current = _observe(MIDNIGHT + timedelta(minutes=10), outcome.volume_l, actuator_on=True)

    recovered = observed_demand_lpm(previous, current, pump)
    assert recovered == pytest.approx(12.0, rel=1e-9)


def test_an_overflowing_interval_is_unidentifiable(tank, pump):
    previous = _observe(MIDNIGHT, tank.capacity_l - 1.0, actuator_on=True)
    current = _observe(MIDNIGHT + timedelta(minutes=10), tank.capacity_l, actuator_on=True)
    assert observed_demand_lpm(previous, current, pump) is None


def test_a_run_dry_interval_is_unidentifiable(tank, pump):
    previous = _observe(MIDNIGHT, 5.0, actuator_on=False)
    current = _observe(MIDNIGHT + timedelta(minutes=10), 0.0, actuator_on=False)
    assert observed_demand_lpm(previous, current, pump) is None


def test_an_invalid_sensor_reading_is_unidentifiable(tank, pump):
    previous = _observe(MIDNIGHT, 500.0, actuator_on=False, sensor_valid=False)
    current = _observe(MIDNIGHT + timedelta(minutes=10), 490.0, actuator_on=False)
    assert observed_demand_lpm(previous, current, pump) is None


def test_two_readings_at_the_same_instant_are_unidentifiable_not_an_error(tank, pump):
    """Zero elapsed time is arithmetically degenerate, not a caller
    mistake the way a *reversed* pair is — distinct from the case below."""
    a = _observe(MIDNIGHT, 500.0, actuator_on=False)
    b = _observe(MIDNIGHT, 490.0, actuator_on=False)
    assert observed_demand_lpm(a, b, pump) is None


def test_a_reversed_pair_raises_rather_than_returns_unknown(tank, pump):
    """Regression. Passing ``current`` before ``previous`` used to fall
    through the same ``dt <= 0`` branch as a genuinely zero-duration
    interval and return ``None`` — a caller bug disguised as an
    unidentifiable interval. ``observed_demand_series`` already raised for
    the equivalent condition; the single-pair function now matches it."""
    earlier = _observe(MIDNIGHT, 500.0, actuator_on=False)
    later = _observe(MIDNIGHT + timedelta(minutes=10), 490.0, actuator_on=False)
    with pytest.raises(ValueError, match="chronological order"):
        observed_demand_lpm(later, earlier, pump)


def test_a_non_litres_reading_is_rejected(tank, pump):
    """Regression. Nothing checked ``native_unit`` before computing —
    the exact failure class ``require_demand_forecast`` exists to prevent
    on the ``Forecast`` side, just missing on the raw-observation side."""
    previous = ResourceObservation(
        MIDNIGHT, RESOURCE_ID, 0.5, 500.0, "kW", False, Provenance.SIMULATED
    )
    current = ResourceObservation(
        MIDNIGHT + timedelta(minutes=10), RESOURCE_ID, 0.49, 490.0, "kW", False, Provenance.SIMULATED
    )
    with pytest.raises(ValueError, match="native_unit"):
        observed_demand_lpm(previous, current, pump)


def test_derived_demand_is_stamped_as_derived_provenance(tank, pump):
    """The hard rule ('distinguish Measured / Simulated / Predicted /
    Estimated / Derived in all logs and reports') applies to this series
    as much as to any named result — 'mm -> litres' is CLAUDE.md's own
    example of what DERIVED means, and this is the same kind of
    deterministic transform."""
    observations = (
        _observe(MIDNIGHT, 500.0, actuator_on=False),
        _observe(MIDNIGHT + timedelta(minutes=10), 490.0, actuator_on=False),
    )
    series = observed_demand_series(observations, pump)
    assert len(series) == 1
    assert series[0].provenance is Provenance.DERIVED


def test_observed_demand_series_skips_unidentifiable_intervals_not_insert_a_guess(
    tank, pump
):
    observations = (
        _observe(MIDNIGHT, 500.0, actuator_on=False),
        _observe(MIDNIGHT + timedelta(minutes=10), 490.0, actuator_on=False),
        _observe(MIDNIGHT + timedelta(minutes=20), 0.0, actuator_on=False),  # ran dry
        _observe(MIDNIGHT + timedelta(minutes=30), 5.0, actuator_on=True),
    )
    series = observed_demand_series(observations, pump)
    assert len(series) == 2  # the run-dry interval and its follow-on are skipped
    assert series[0].at == MIDNIGHT


def test_observed_demand_matches_simulator_ground_truth_over_a_real_run(config):
    """End to end [simulated]: over a real scenario run, the demand this
    module derives from raw tank readings must match the simulator's own
    ground-truth ``SimulationStep.demand_lpm`` for every non-clamped step."""
    pump = PumpModel.from_config(config.pump)
    scenario = scenarios.cloudy(config)
    run = run_scenario(config, scenario, ThresholdScheduler(config.scheduler))

    observations = _observations_from_steps(run.steps)
    series = observed_demand_series(observations, pump)
    by_start = {tv.at: tv.value for tv in series}

    checked = 0
    for step in run.steps:
        if step.spilled_l > 0.0 or step.unmet_demand_l > 0.0:
            continue
        if step.start not in by_start:
            continue
        assert by_start[step.start] == pytest.approx(step.demand_lpm, abs=0.05)
        checked += 1
    assert checked > 0, "no non-clamped steps were available to check"


# --- exit criterion -------------------------------------------------------


def test_feature_names_match_the_pinned_spec_exactly_and_in_order():
    """**The Phase 4 exit criterion.** Section 12 of the original design
    doc is not in this repo; this pinned list is the substitute, and a
    model trained on one ordering cannot be fed another."""
    assert FEATURE_NAMES == (
        "tod_sin",
        "tod_cos",
        "dow_sin",
        "dow_cos",
        "doy_sin",
        "doy_cos",
        "is_weekend",
        "slot_index",
        "slot_mean_demand_lpm",
        "slot_sample_count",
        "demand_lag_1_slot",
        "demand_lag_1_day",
        "demand_lag_7_day",
        "demand_roll_mean_1h",
        "demand_roll_mean_24h",
        "demand_roll_max_1h",
        "service_level",
        "service_level_delta_1h",
    )


def test_a_feature_vector_carries_its_names_values_and_version_in_lockstep():
    profile = build_slot_profile((), slot_minutes=15.0, min_samples=1)
    vector = build_feature_vector(
        MIDNIGHT,
        demand_series=(),
        slot_profile=profile,
        service_level_series=(),
        provenance=Provenance.SIMULATED,
    )
    assert isinstance(vector, FeatureVector)
    assert len(vector.values) == len(FEATURE_NAMES)
    assert vector.feature_set_version == FEATURE_SET_VERSION
    assert vector.provenance is Provenance.SIMULATED


def test_mismatched_names_and_values_are_rejected():
    with pytest.raises(ValueError, match="values for"):
        FeatureVector(
            moment=MIDNIGHT,
            names=("a", "b"),
            values=(1.0,),
            feature_set_version="x",
            provenance=Provenance.SIMULATED,
        )


def test_the_slot_grid_comes_only_from_the_profile_no_separate_argument():
    """Regression: an earlier version took a separate ``slot_minutes``
    argument alongside ``slot_profile``, and nothing stopped them
    disagreeing — ``slot_index`` would be computed on one grid while
    ``slot_mean_demand_lpm`` was looked up on the profile's own, different
    one, silently, in the same vector. There is now exactly one grid: the
    profile's."""
    profile_30min = build_slot_profile(
        (_tv(MIDNIGHT + timedelta(hours=10), 7.0),), slot_minutes=30.0, min_samples=1
    )
    vector = build_feature_vector(
        MIDNIGHT + timedelta(hours=10),
        demand_series=(),
        slot_profile=profile_30min,
        service_level_series=(),
        provenance=Provenance.SIMULATED,
    )
    # slot_index must reflect the profile's 30-minute grid (20), not a
    # hardcoded or mismatched 15-minute one (40).
    assert vector.as_dict()["slot_index"] == 20.0
    assert vector.as_dict()["slot_mean_demand_lpm"] == pytest.approx(7.0)


def test_a_vector_built_in_the_first_hours_of_a_run_honestly_reports_unknowns(config):
    scenario = scenarios.extended_set(config)[1]  # cloudy, 30 days
    run = run_scenario(config, scenario, ThresholdScheduler(config.scheduler))
    pump = PumpModel.from_config(config.pump)

    observations = _observations_from_steps(run.steps)
    demand_series = observed_demand_series(observations, pump)
    level_series = tuple(_tv(o.timestamp, o.service_level) for o in observations)
    profile = build_slot_profile(demand_series, slot_minutes=15.0, min_samples=3)

    early = build_feature_vector(
        scenario.start + timedelta(minutes=30),
        demand_series=demand_series,
        slot_profile=profile,
        service_level_series=level_series,
        provenance=Provenance.SIMULATED,
    )
    assert "demand_lag_1_day" in early.missing
    assert "demand_lag_7_day" in early.missing

    late = build_feature_vector(
        scenario.start + timedelta(days=20),
        demand_series=demand_series,
        slot_profile=profile,
        service_level_series=level_series,
        provenance=Provenance.SIMULATED,
    )
    assert "demand_lag_1_day" not in late.missing
    assert "demand_lag_7_day" not in late.missing
    assert "slot_mean_demand_lpm" not in late.missing
