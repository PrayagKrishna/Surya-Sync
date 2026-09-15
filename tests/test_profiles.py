"""Demand, solar and grid profiles.

The property every profile must have is **purity in time**: ``f(t)``
depends only on ``t``. The MPC queries the same future instant repeatedly
across successive replans, and replay re-queries the whole series in a
different order. A profile that remembers how often it has been called
would silently break both.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from surya_sync.config.schema import SolarConfig
from surya_sync.domain import Provenance
from surya_sync.simulator.demand import (
    ConstantDemandProfile,
    DiurnalDemandProfile,
    SpikeDemandProfile,
    SpikeWindow,
)
from surya_sync.simulator.grid import (
    ConstantBaseLoadProfile,
    DiurnalBaseLoadProfile,
    GridModel,
)
from surya_sync.simulator.noise import signed_noise, unit_noise
from surya_sync.simulator.solar import (
    ClearSkyProfile,
    IntermittentProfile,
    OvercastProfile,
)

MIDNIGHT = datetime(2026, 3, 2, 0, 0)
"""A Monday. Weekday and weekend days scale differently, so a test that
asserts a daily volume has to say which kind of day it means."""

NOON = datetime(2026, 3, 2, 12, 0)


# --- deterministic noise ------------------------------------------------


def test_noise_is_reproducible():
    assert unit_noise(7, 99) == unit_noise(7, 99)


def test_noise_stays_in_the_unit_interval():
    values = [unit_noise(20260915, index) for index in range(2000)]
    assert all(0.0 <= value < 1.0 for value in values)


def test_noise_is_not_a_constant():
    values = {unit_noise(1, index) for index in range(500)}
    assert len(values) > 400


def test_neighbouring_indices_decorrelate():
    """Consecutive time slots must not walk smoothly, or "random" cloud
    cover would really be a slow ramp."""
    diffs = [abs(unit_noise(3, i + 1) - unit_noise(3, i)) for i in range(500)]
    assert sum(diffs) / len(diffs) > 0.2


def test_signed_noise_is_centred_on_zero():
    values = [signed_noise(5, index) for index in range(5000)]
    assert all(-1.0 <= value < 1.0 for value in values)
    assert abs(sum(values) / len(values)) < 0.05


# --- demand -------------------------------------------------------------


def test_constant_demand_is_constant():
    profile = ConstantDemandProfile(lpm=0.5)
    assert profile.lpm_at(MIDNIGHT) == 0.5
    assert profile.lpm_at(NOON) == 0.5


def test_negative_demand_is_rejected():
    with pytest.raises(ValueError):
        ConstantDemandProfile(lpm=-1.0)


def test_a_weekday_integrates_to_the_stated_volume():
    """Scenarios are stated in litres per day, so that number has to be
    real rather than approximate. Exact only because the interpolation is
    periodic and piecewise-linear — its mean over a full day equals the
    mean of the hourly weights."""
    profile = DiurnalDemandProfile(daily_volume_l=450.0)
    assert MIDNIGHT.weekday() < 5
    total = profile.volume_l(MIDNIGHT, minutes=1440.0, step_minutes=1.0)
    assert total == pytest.approx(450.0, rel=1e-6)


def test_the_daily_volume_holds_on_any_start_hour():
    profile = DiurnalDemandProfile(daily_volume_l=450.0)
    total = profile.volume_l(MIDNIGHT.replace(hour=13), minutes=1440.0, step_minutes=1.0)
    assert total == pytest.approx(450.0, rel=1e-6)


def test_a_weekend_day_integrates_to_the_scaled_volume():
    profile = DiurnalDemandProfile(daily_volume_l=450.0, weekend_scale=1.15)
    sunday = datetime(2026, 3, 1, 0, 0)
    assert sunday.weekday() == 6
    total = profile.volume_l(sunday, minutes=1440.0, step_minutes=1.0)
    assert total == pytest.approx(450.0 * 1.15, rel=1e-6)


def test_demand_is_pure_in_time():
    profile = DiurnalDemandProfile(daily_volume_l=450.0, jitter=0.2)
    moments = [MIDNIGHT + timedelta(minutes=17 * k) for k in range(200)]
    forward = [profile.lpm_at(m) for m in moments]
    backward = [profile.lpm_at(m) for m in reversed(moments)]
    assert forward == list(reversed(backward))


def test_the_household_has_a_morning_and_an_evening_peak():
    profile = DiurnalDemandProfile(daily_volume_l=450.0)
    night = profile.lpm_at(MIDNIGHT.replace(hour=3, minute=30))
    morning = profile.lpm_at(MIDNIGHT.replace(hour=7, minute=30))
    midday = profile.lpm_at(MIDNIGHT.replace(hour=14, minute=30))
    evening = profile.lpm_at(MIDNIGHT.replace(hour=19, minute=30))
    assert morning > midday > night
    assert evening > midday
    assert morning > night * 10


def test_demand_is_never_negative_even_with_heavy_jitter():
    profile = DiurnalDemandProfile(daily_volume_l=450.0, jitter=0.9)
    for k in range(2000):
        assert profile.lpm_at(MIDNIGHT + timedelta(minutes=k)) >= 0.0


def test_weekends_draw_more():
    profile = DiurnalDemandProfile(daily_volume_l=450.0, weekend_scale=1.15)
    friday = datetime(2026, 3, 6, 8, 30)
    saturday = datetime(2026, 3, 7, 8, 30)
    assert saturday.weekday() == 5
    assert profile.lpm_at(saturday) == pytest.approx(profile.lpm_at(friday) * 1.15)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"daily_volume_l": -1.0},
        {"daily_volume_l": 450.0, "weights": (1.0,) * 23},
        {"daily_volume_l": 450.0, "weights": (0.0,) * 24},
        {"daily_volume_l": 450.0, "jitter": 1.0},
        {"daily_volume_l": 450.0, "jitter": -0.1},
    ],
)
def test_impossible_demand_parameters_are_rejected(kwargs):
    with pytest.raises(ValueError):
        DiurnalDemandProfile(**kwargs)


def test_a_spike_adds_only_inside_its_window():
    base = ConstantDemandProfile(lpm=1.0)
    start = MIDNIGHT.replace(hour=11)
    profile = SpikeDemandProfile(
        base=base, spikes=(SpikeWindow(start=start, minutes=30.0, extra_lpm=8.0),)
    )
    assert profile.lpm_at(start - timedelta(minutes=1)) == pytest.approx(1.0)
    assert profile.lpm_at(start) == pytest.approx(9.0)
    assert profile.lpm_at(start + timedelta(minutes=29)) == pytest.approx(9.0)
    assert profile.lpm_at(start + timedelta(minutes=30)) == pytest.approx(1.0)


def test_a_perfect_forecast_is_labelled_simulated():
    """The simulator handing over its own ground truth is an upper bound,
    not forecast skill, and the provenance has to say so."""
    profile = DiurnalDemandProfile(daily_volume_l=450.0)
    forecast = profile.as_forecast(MIDNIGHT, n_steps=8, step_minutes=15.0)
    assert forecast.provenance is Provenance.SIMULATED
    assert forecast.target == "water_demand_lpm"
    assert len(forecast.points) == 8
    assert forecast.points[1].target_time == MIDNIGHT + timedelta(minutes=15)
    assert forecast.points[3].p50 == pytest.approx(
        profile.lpm_at(MIDNIGHT + timedelta(minutes=45))
    )


# --- solar --------------------------------------------------------------


@pytest.fixture
def clear_sky() -> ClearSkyProfile:
    return ClearSkyProfile.from_config(SolarConfig())


def test_the_sun_is_down_at_night(clear_sky):
    for hour in (0, 1, 2, 3, 22, 23):
        assert clear_sky.kw_at(MIDNIGHT.replace(hour=hour)) == 0.0


def test_generation_peaks_around_midday(clear_sky):
    midday = clear_sky.kw_at(NOON)
    assert midday > clear_sky.kw_at(NOON.replace(hour=8))
    assert midday > clear_sky.kw_at(NOON.replace(hour=16))
    assert midday > 0.0


def test_output_never_exceeds_the_derated_array(clear_sky):
    ceiling = clear_sky.pv_capacity_kw * clear_sky.performance_ratio
    for k in range(0, 1440, 7):
        assert 0.0 <= clear_sky.kw_at(MIDNIGHT + timedelta(minutes=k)) <= ceiling + 1e-9


def test_summer_days_are_longer_than_winter_days(clear_sky):
    """The reason the geometry is here rather than a fixed bell curve:
    "wait for the sun" has to mean something different in December."""

    def daylight_minutes(day: datetime) -> int:
        return sum(
            1 for k in range(1440) if clear_sky.kw_at(day + timedelta(minutes=k)) > 0.0
        )

    june = daylight_minutes(datetime(2026, 6, 21))
    december = daylight_minutes(datetime(2026, 12, 21))
    assert june > december


def test_declination_swings_across_the_year(clear_sky):
    assert clear_sky.declination_deg(datetime(2026, 6, 21)) > 20.0
    assert clear_sky.declination_deg(datetime(2026, 12, 21)) < -20.0


def test_solar_altitude_is_negative_before_sunrise(clear_sky):
    assert clear_sky.solar_altitude_deg(MIDNIGHT.replace(hour=2)) < 0.0
    assert clear_sky.solar_altitude_deg(NOON) > 0.0


@pytest.mark.parametrize(
    "kwargs",
    [
        {"pv_capacity_kw": 0.0},
        {"pv_capacity_kw": 3.0, "latitude": 100.0},
        {"pv_capacity_kw": 3.0, "longitude": 200.0},
        {"pv_capacity_kw": 3.0, "performance_ratio": 0.0},
        {"pv_capacity_kw": 3.0, "performance_ratio": 1.5},
    ],
)
def test_impossible_array_parameters_are_rejected(kwargs):
    with pytest.raises(ValueError):
        ClearSkyProfile(**kwargs)


def test_cloud_only_ever_reduces_generation(clear_sky):
    overcast = OvercastProfile(clear_sky=clear_sky, cloud_factor=0.3)
    for k in range(0, 1440, 11):
        moment = MIDNIGHT + timedelta(minutes=k)
        assert overcast.kw_at(moment) <= clear_sky.kw_at(moment) + 1e-12


def test_overcast_scales_by_exactly_the_cloud_factor(clear_sky):
    overcast = OvercastProfile(clear_sky=clear_sky, cloud_factor=0.25)
    assert overcast.kw_at(NOON) == pytest.approx(clear_sky.kw_at(NOON) * 0.25)


def test_intermittent_cloud_is_reproducible(clear_sky):
    """Queried forwards and backwards, the same instants give the same
    values — which a call-ordered random stream would not."""
    profile = IntermittentProfile(clear_sky=clear_sky, seed=42)
    moments = [NOON + timedelta(minutes=k) for k in range(300)]
    forward = [profile.kw_at(m) for m in moments]
    backward = [profile.kw_at(m) for m in reversed(moments)]
    assert forward == list(reversed(backward))


def test_intermittent_cloud_actually_varies(clear_sky):
    profile = IntermittentProfile(clear_sky=clear_sky, seed=42, slot_minutes=20.0)
    factors = {
        round(profile.cloud_factor_at(NOON + timedelta(minutes=20 * k)), 6)
        for k in range(12)
    }
    assert len(factors) > 8


def test_intermittent_cloud_stays_within_its_bounds(clear_sky):
    profile = IntermittentProfile(
        clear_sky=clear_sky, seed=42, min_factor=0.15, max_factor=0.9
    )
    for k in range(0, 2000, 3):
        factor = profile.cloud_factor_at(MIDNIGHT + timedelta(minutes=k))
        assert 0.15 <= factor <= 0.9


def test_solar_energy_integrates_over_a_day(clear_sky):
    energy = clear_sky.energy_kwh(MIDNIGHT, minutes=1440.0, step_minutes=5.0)
    assert 0.0 < energy < clear_sky.pv_capacity_kw * 24.0


# --- grid ---------------------------------------------------------------


@pytest.fixture
def grid() -> GridModel:
    return GridModel(surplus_threshold_kw=0.1)


def test_the_base_load_is_served_by_solar_first(grid):
    """The attribution rule in one test: the pump only gets the surplus."""
    split = grid.split(pv_kw=2.0, base_load_kw=0.5, controllable_kw=0.75, minutes=60.0)
    assert split.surplus_kw == pytest.approx(1.5)
    assert split.controllable_solar_kw == pytest.approx(0.75)
    assert split.controllable_grid_kw == pytest.approx(0.0)


def test_a_pump_larger_than_the_surplus_draws_the_rest_from_grid(grid):
    split = grid.split(pv_kw=1.0, base_load_kw=0.8, controllable_kw=0.75, minutes=60.0)
    assert split.surplus_kw == pytest.approx(0.2)
    assert split.controllable_solar_kw == pytest.approx(0.2)
    assert split.controllable_grid_kw == pytest.approx(0.55)


def test_the_pump_cannot_claim_solar_the_fridge_was_using(grid):
    """Without this rule a scheduler could report a solar win for running
    at night against a house that was importing anyway."""
    split = grid.split(pv_kw=0.4, base_load_kw=0.9, controllable_kw=0.75, minutes=60.0)
    assert split.surplus_kw == 0.0
    assert split.controllable_solar_kw == 0.0
    assert split.controllable_grid_kw == pytest.approx(0.75)
    assert split.grid_import_kw == pytest.approx(0.5 + 0.75)


def test_surplus_below_the_threshold_is_treated_as_noise(grid):
    assert grid.surplus_kw(pv_kw=1.05, base_load_kw=1.0) == 0.0
    assert grid.surplus_kw(pv_kw=1.20, base_load_kw=1.0) == pytest.approx(0.2)


def test_unused_surplus_is_exported(grid):
    split = grid.split(pv_kw=3.0, base_load_kw=0.5, controllable_kw=0.0, minutes=60.0)
    assert split.export_kw == pytest.approx(2.5)
    assert split.controllable_grid_kw == 0.0


def test_energy_totals_follow_the_interval(grid):
    split = grid.split(pv_kw=0.0, base_load_kw=0.0, controllable_kw=0.75, minutes=30.0)
    assert split.controllable_grid_kwh == pytest.approx(0.375)
    assert split.grid_import_kwh == pytest.approx(0.375)


def test_an_idle_pump_scores_a_full_solar_fraction(grid):
    """Reporting zero here would punish a scheduler for correctly holding."""
    split = grid.split(pv_kw=0.0, base_load_kw=0.5, controllable_kw=0.0, minutes=60.0)
    assert split.solar_fraction == 1.0


def test_solar_fraction_is_the_share_met_by_surplus(grid):
    split = grid.split(pv_kw=1.0, base_load_kw=0.5, controllable_kw=1.0, minutes=60.0)
    assert split.solar_fraction == pytest.approx(0.5)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"pv_kw": -1.0, "base_load_kw": 0.0, "controllable_kw": 0.0, "minutes": 60.0},
        {"pv_kw": 0.0, "base_load_kw": -1.0, "controllable_kw": 0.0, "minutes": 60.0},
        {"pv_kw": 0.0, "base_load_kw": 0.0, "controllable_kw": -1.0, "minutes": 60.0},
        {"pv_kw": 0.0, "base_load_kw": 0.0, "controllable_kw": 0.0, "minutes": -1.0},
    ],
)
def test_negative_power_is_rejected(grid, kwargs):
    with pytest.raises(ValueError):
        grid.split(**kwargs)


def test_base_load_has_an_evening_peak():
    profile = DiurnalBaseLoadProfile()
    night = profile.kw_at(MIDNIGHT.replace(hour=3, minute=30))
    evening = profile.kw_at(MIDNIGHT.replace(hour=19, minute=30))
    assert evening > night * 3


def test_base_load_is_pure_in_time():
    profile = DiurnalBaseLoadProfile(jitter=0.2)
    moments = [MIDNIGHT + timedelta(minutes=13 * k) for k in range(200)]
    first = [profile.kw_at(m) for m in moments]
    second = [profile.kw_at(m) for m in moments]
    assert first == second


def test_constant_base_load_is_constant():
    assert ConstantBaseLoadProfile(kw=0.3).kw_at(NOON) == 0.3
