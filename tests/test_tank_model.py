"""Tank geometry and mass balance — Phase 1 exit criterion.

These are the first tests in the project that assert something *physical*
rather than something structural. Phase 0 closed with zero of them, which
is why its suite could only claim the architecture was self-consistent.
"""

from __future__ import annotations

import pytest

from surya_sync.config.schema import TankConfig
from surya_sync.models.tank import TankModel

CAPACITY_L = 1000.0
HEIGHT_MM = 1200.0
OFFSET_MM = 100.0


@pytest.fixture
def tank() -> TankModel:
    return TankModel(
        capacity_l=CAPACITY_L, height_mm=HEIGHT_MM, sensor_offset_mm=OFFSET_MM
    )


# --- construction -------------------------------------------------------


@pytest.mark.parametrize(
    "capacity_l, height_mm, offset_mm",
    [(0.0, 1200.0, 100.0), (-1.0, 1200.0, 100.0), (1000.0, 0.0, 100.0), (1000.0, 1200.0, -1.0)],
)
def test_impossible_geometry_is_rejected(capacity_l, height_mm, offset_mm):
    with pytest.raises(ValueError):
        TankModel(capacity_l=capacity_l, height_mm=height_mm, sensor_offset_mm=offset_mm)


def test_from_config_carries_the_geometry(tank):
    assert TankModel.from_config(TankConfig()) == tank


# --- geometry -----------------------------------------------------------


def test_cross_section_is_capacity_over_height(tank):
    assert tank.litres_per_mm == pytest.approx(CAPACITY_L / HEIGHT_MM)


def test_sensor_reads_the_offset_when_full(tank):
    """A downward ultrasonic sensor sees its *smallest* distance at full."""
    assert tank.distance_mm_full == OFFSET_MM
    assert tank.volume_l_from_distance_mm(OFFSET_MM) == pytest.approx(CAPACITY_L)


def test_sensor_reads_offset_plus_height_when_empty(tank):
    assert tank.distance_mm_empty == OFFSET_MM + HEIGHT_MM
    assert tank.volume_l_from_distance_mm(OFFSET_MM + HEIGHT_MM) == pytest.approx(0.0)


def test_half_full_is_halfway_down_the_column(tank):
    distance = OFFSET_MM + HEIGHT_MM / 2.0
    assert tank.volume_l_from_distance_mm(distance) == pytest.approx(CAPACITY_L / 2.0)


@pytest.mark.parametrize("volume_l", [0.0, 1.0, 250.0, 500.0, 999.0, 1000.0])
def test_volume_to_distance_round_trips(tank, volume_l):
    """The conversion the UI and the ESP32 both depend on must be exact in
    both directions, or a logged level cannot be compared to a sensed one."""
    distance = tank.distance_mm_from_volume_l(volume_l)
    assert tank.volume_l_from_distance_mm(distance) == pytest.approx(volume_l)


@pytest.mark.parametrize("service_level", [0.0, 0.2, 0.5, 0.95, 1.0])
def test_service_level_round_trips(tank, service_level):
    volume = tank.volume_l_from_service_level(service_level)
    assert tank.service_level_from_volume_l(volume) == pytest.approx(service_level)


def test_service_level_is_a_linear_fraction_of_capacity(tank):
    assert tank.service_level_from_volume_l(250.0) == pytest.approx(0.25)


@pytest.mark.parametrize(
    "distance_mm, expected_l",
    [(0.0, CAPACITY_L), (-500.0, CAPACITY_L), (5000.0, 0.0)],
)
def test_out_of_range_distances_clamp_rather_than_extrapolate(tank, distance_mm, expected_l):
    """A wild reading must not produce a negative or over-full volume that
    would propagate into the scheduler as a plausible number."""
    assert tank.volume_l_from_distance_mm(distance_mm) == pytest.approx(expected_l)


def test_plausibility_admits_real_readings_and_rejects_nonsense(tank):
    assert tank.is_plausible_distance_mm(OFFSET_MM)
    assert tank.is_plausible_distance_mm(OFFSET_MM + HEIGHT_MM)
    assert tank.is_plausible_distance_mm(OFFSET_MM + HEIGHT_MM / 2)
    assert not tank.is_plausible_distance_mm(0.0)
    assert not tank.is_plausible_distance_mm(4000.0)


def test_plausibility_tolerates_mounting_slop(tank):
    """Ripple and mounting error must not flag a healthy sensor as broken."""
    assert tank.is_plausible_distance_mm(OFFSET_MM - 20.0)
    assert tank.is_plausible_distance_mm(OFFSET_MM + HEIGHT_MM + 20.0)


# --- mass balance -------------------------------------------------------


def test_inflow_only_raises_the_level(tank):
    step = tank.step(volume_l=500.0, inflow_lpm=30.0, demand_lpm=0.0, minutes=10.0)
    assert step.volume_l == pytest.approx(800.0)
    assert step.inflow_l == pytest.approx(300.0)
    assert step.spilled_l == 0.0


def test_demand_only_lowers_the_level(tank):
    step = tank.step(volume_l=500.0, inflow_lpm=0.0, demand_lpm=10.0, minutes=10.0)
    assert step.volume_l == pytest.approx(400.0)
    assert step.drawn_l == pytest.approx(100.0)
    assert step.unmet_demand_l == 0.0


def test_inflow_and_demand_net_out(tank):
    step = tank.step(volume_l=500.0, inflow_lpm=30.0, demand_lpm=10.0, minutes=10.0)
    assert step.volume_l == pytest.approx(700.0)


def test_a_full_tank_overflows_and_reports_the_spill(tank):
    """Clamping alone would hide the overflow; the spill is the evidence."""
    step = tank.step(volume_l=900.0, inflow_lpm=30.0, demand_lpm=0.0, minutes=10.0)
    assert step.volume_l == pytest.approx(CAPACITY_L)
    assert step.spilled_l == pytest.approx(200.0)
    assert step.overflowed
    assert step.inflow_l == pytest.approx(100.0)


def test_an_empty_tank_reports_unmet_demand(tank):
    step = tank.step(volume_l=50.0, inflow_lpm=0.0, demand_lpm=10.0, minutes=10.0)
    assert step.volume_l == pytest.approx(0.0)
    assert step.drawn_l == pytest.approx(50.0)
    assert step.unmet_demand_l == pytest.approx(50.0)
    assert step.ran_dry


def test_demand_is_served_before_the_ceiling_is_applied(tank):
    """A nearly full tank being drawn while it fills must not report a spill
    for water the household actually received."""
    step = tank.step(volume_l=990.0, inflow_lpm=30.0, demand_lpm=30.0, minutes=10.0)
    assert step.volume_l == pytest.approx(990.0)
    assert step.spilled_l == 0.0
    assert step.drawn_l == pytest.approx(300.0)


def test_a_zero_length_step_changes_nothing(tank):
    step = tank.step(volume_l=500.0, inflow_lpm=30.0, demand_lpm=10.0, minutes=0.0)
    assert step.volume_l == pytest.approx(500.0)


@pytest.mark.parametrize(
    "inflow_lpm, demand_lpm, minutes",
    [(-1.0, 0.0, 10.0), (0.0, -1.0, 10.0), (0.0, 0.0, -10.0)],
)
def test_negative_physical_quantities_are_rejected(tank, inflow_lpm, demand_lpm, minutes):
    with pytest.raises(ValueError):
        tank.step(500.0, inflow_lpm, demand_lpm, minutes)


def test_the_step_is_deterministic(tank):
    """Reproducibility of every experiment rests on this."""
    first = tank.step(437.5, 30.0, 12.3, 7.0)
    second = tank.step(437.5, 30.0, 12.3, 7.0)
    assert first == second


def test_volume_never_leaves_the_physical_range(tank):
    """Property sweep: no combination of inputs produces an impossible tank."""
    for volume in (0.0, 1.0, 500.0, 999.0, 1000.0):
        for inflow in (0.0, 30.0, 500.0):
            for demand in (0.0, 5.0, 500.0):
                step = tank.step(volume, inflow, demand, 15.0)
                assert 0.0 <= step.volume_l <= CAPACITY_L
                assert step.spilled_l >= 0.0
                assert step.unmet_demand_l >= 0.0


def test_mass_is_conserved(tank):
    """Everything in must end up stored, drawn or spilled."""
    for volume in (0.0, 250.0, 900.0, 1000.0):
        for inflow in (0.0, 30.0, 200.0):
            for demand in (0.0, 10.0, 300.0):
                step = tank.step(volume, inflow, demand, 12.0)
                supplied = volume + inflow * 12.0
                accounted = step.volume_l + step.drawn_l + step.spilled_l
                assert accounted == pytest.approx(supplied)
