"""Phase 3 exit criterion, pinned: reactive vs. threshold, extended set.

"Beats the threshold controller on grid-powered pump energy in simulation"
is judged on ``extended_set`` (30 days, five scenarios), not
``standard_set`` (3 days, four scenarios). Phase 3 measured that 3 days was
too short a window: at 3 days, ``spike`` and ``sunny`` looked tied to the
threshold controller, and both turned out to be clear reactive wins once
run past ~7-14 days. ``standard_set`` stays frozen as the record of what
Phase 2 was actually measured against; this file uses the longer set
because a 3-day tie was proven to be a duration artifact, not a real limit.

**Measured** [simulated], extended set, 30 days each, default config:

===========  =============  =============  =========
scenario     threshold kWh  reactive kWh    saved kWh
===========  =============  =============  =========
cloudy        5.443          5.443           0.000
low_start     3.586          1.137           2.449
monsoon       5.813          5.813           0.000
spike         4.045          2.148           1.896
sunny         2.651          1.331           1.320
aggregate    21.537         15.872           5.666
===========  =============  =============  =========

``cloudy`` and ``monsoon`` tie exactly rather than improve, at every
duration tested (3 through 60 days): the reactive scheduler only tops up
on surplus that fully covers the pump's rated draw (see
``ReactiveScheduler._has_surplus``), and neither scenario ever offers a
full-coverage window before the level-triggered run would fire anyway.
This is a measured, stable limit of a current-surplus-only baseline —
``monsoon`` was added specifically to test whether *sustained* (not just
intermittent) poor solar behaves the same way, and it does. Phase 7's
predictive heuristic is where a solar *forecast* would let a run be pulled
forward through a still-building surplus. Do not tune the scenarios or the
surplus requirement to force these two scenarios to "win"; report the
limit, as Phase 2 did for the original (and since revised) ``sunny`` claim.

All ten (scenario, scheduler) runs this file needs are computed once, in
``runs``, and every test reads from that cache — a 30-day simulation run is
not free, and this file has no reason to pay for it more than once per
scenario/scheduler pair.
"""

from __future__ import annotations

import pytest

from surya_sync.analytics.comparison import aggregate_grid_kwh, compare_grid_energy
from surya_sync.config.schema import Config
from surya_sync.experiments.runner import ScenarioRun, run_scenario
from surya_sync.scheduler.reactive import ReactiveScheduler
from surya_sync.scheduler.threshold import ThresholdScheduler
from surya_sync.simulator import scenarios

EXTENDED_DAYS = 30.0


def _run(config: Config, scenario, scheduler_name: str) -> ScenarioRun:
    scheduler = (
        ThresholdScheduler(config.scheduler)
        if scheduler_name == "threshold"
        else ReactiveScheduler(config.scheduler, config.solar.surplus_threshold_kw)
    )
    return run_scenario(config, scenario, scheduler)


@pytest.fixture(scope="module")
def runs() -> dict[str, dict[str, ScenarioRun]]:
    """Every (scenario, scheduler) pair this file needs, computed once."""
    config = Config()
    scenario_set = scenarios.extended_set(config, days=EXTENDED_DAYS)
    return {
        scenario.name: {
            "threshold": _run(config, scenario, "threshold"),
            "reactive": _run(config, scenario, "reactive"),
        }
        for scenario in scenario_set
    }


ALL_SCENARIOS = ["sunny", "cloudy", "spike", "low_start", "monsoon"]


@pytest.mark.parametrize("name", ALL_SCENARIOS)
def test_reactive_never_violates_a_hard_constraint(runs, name):
    run = runs[name]["reactive"]
    assert run.violation_steps == (), (
        f"{name}: {len(run.violation_steps)} violating steps"
    )


@pytest.mark.parametrize("name", ALL_SCENARIOS)
def test_reactive_never_draws_more_grid_energy_than_threshold(runs, name):
    """The opportunistic top-up must never make grid draw worse. An
    earlier, laxer surplus requirement did exactly that on ``spike`` and
    ``sunny`` at 3 days — this pins the fix over the full extended set."""
    threshold_run = runs[name]["threshold"]
    reactive_run = runs[name]["reactive"]
    assert reactive_run.grid_energy_kwh <= threshold_run.grid_energy_kwh + 1e-9, (
        f"{name}: reactive {reactive_run.grid_energy_kwh:.3f} kWh > "
        f"threshold {threshold_run.grid_energy_kwh:.3f} kWh"
    )


@pytest.mark.parametrize(
    "name,expected_threshold,expected_reactive",
    [
        ("low_start", 3.586, 1.137),
        ("spike", 4.045, 2.148),
        ("sunny", 2.651, 1.331),
    ],
)
def test_reactive_beats_threshold_on_scenarios_that_stopped_tying_past_3_days(
    runs, name, expected_threshold, expected_reactive
):
    """At Phase 2's 3-day duration, ``spike`` and ``sunny`` looked tied to
    threshold, same as ``cloudy``/``monsoon`` still do at 30 days. Run long
    enough, they are not: pinned here so nobody shortens the duration back
    down and reads a false tie again."""
    threshold_run = runs[name]["threshold"]
    reactive_run = runs[name]["reactive"]
    assert reactive_run.grid_energy_kwh < threshold_run.grid_energy_kwh
    assert threshold_run.grid_energy_kwh == pytest.approx(expected_threshold, abs=0.01)
    assert reactive_run.grid_energy_kwh == pytest.approx(expected_reactive, abs=0.01)


def test_reactive_beats_threshold_on_the_aggregate(runs):
    threshold_runs = tuple(runs[name]["threshold"] for name in ALL_SCENARIOS)
    reactive_runs = tuple(runs[name]["reactive"] for name in ALL_SCENARIOS)

    rows = compare_grid_energy(threshold_runs, reactive_runs)
    threshold_total, reactive_total = aggregate_grid_kwh(rows)

    assert reactive_total < threshold_total
    assert threshold_total == pytest.approx(21.537, abs=0.05)
    assert reactive_total == pytest.approx(15.872, abs=0.05)


def test_cloudy_and_monsoon_are_a_measured_tie_not_a_regression(runs):
    """Documents the limit above rather than letting it drift unnoticed:
    both scenarios currently show zero improvement, not a loss. ``monsoon``
    exists specifically to check that this isn't a ``cloudy``-specific
    quirk — sustained poor solar hits the same wall."""
    for name in ("cloudy", "monsoon"):
        threshold_run = runs[name]["threshold"]
        reactive_run = runs[name]["reactive"]
        assert reactive_run.grid_energy_kwh == pytest.approx(
            threshold_run.grid_energy_kwh, abs=1e-6
        )
