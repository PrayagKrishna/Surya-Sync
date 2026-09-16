"""Phase 3 exit criterion, pinned: reactive vs. threshold, standard set.

"Beats the threshold controller on grid-powered pump energy in simulation"
is judged on the aggregate and on ``cloudy``/``low_start`` individually —
Phase 2 already established that ``sunny`` draws 0.00 kWh under threshold
and is unwinnable. This file is the measured record of where Phase 3
actually lands, not just an assertion that it landed somewhere good.

**Measured** [simulated], standard set, 3 days each, default config:

===========  =============  =============  =========
scenario     threshold kWh  reactive kWh    saved kWh
===========  =============  =============  =========
cloudy       0.51           0.51            0.00
low_start    0.75           0.19            0.56
spike        0.38           0.38            0.00
sunny        0.00           0.00            0.00
aggregate    1.64           1.07            0.56
===========  =============  =============  =========

``cloudy`` and ``spike`` tie rather than improve: the reactive scheduler
only tops up on surplus that fully covers the pump's rated draw (see
``ReactiveScheduler._has_surplus`` for why a partial-surplus version
regressed both of these), and neither scenario offers a full-coverage
window before the level-triggered run would fire anyway. This is a
measured limit of a current-surplus-only baseline, not a bug — Phase 7's
predictive heuristic is where a solar *forecast* would let a run be pulled
forward through a still-building surplus. Do not tune the scenarios or the
surplus requirement to force a ``cloudy`` win; report it, as Phase 2 did
for ``sunny``.
"""

from __future__ import annotations

import pytest

from surya_sync.analytics.comparison import aggregate_grid_kwh, compare_grid_energy
from surya_sync.config.schema import Config
from surya_sync.experiments.runner import run_scenario
from surya_sync.scheduler.reactive import ReactiveScheduler
from surya_sync.scheduler.threshold import ThresholdScheduler
from surya_sync.simulator import scenarios


@pytest.fixture
def config() -> Config:
    return Config()


def _run(config: Config, scenario, scheduler_name: str):
    scheduler = (
        ThresholdScheduler(config.scheduler)
        if scheduler_name == "threshold"
        else ReactiveScheduler(config.scheduler, config.solar.surplus_threshold_kw)
    )
    return run_scenario(config, scenario, scheduler)


@pytest.mark.parametrize("name", ["sunny", "cloudy", "spike", "low_start"])
def test_reactive_never_violates_a_hard_constraint(config, name):
    run = _run(config, getattr(scenarios, name)(config), "reactive")
    assert run.violation_steps == (), (
        f"{name}: {len(run.violation_steps)} violating steps"
    )


def test_reactive_never_draws_more_grid_energy_than_threshold_per_scenario(config):
    """The opportunistic top-up must never make grid draw worse. An
    earlier, laxer surplus requirement did exactly that on ``spike`` and
    ``sunny`` — this pins the fix."""
    for name in ("sunny", "cloudy", "spike", "low_start"):
        scenario = getattr(scenarios, name)(config)
        threshold_run = _run(config, scenario, "threshold")
        reactive_run = _run(config, scenario, "reactive")
        assert reactive_run.grid_energy_kwh <= threshold_run.grid_energy_kwh + 1e-9, (
            f"{name}: reactive {reactive_run.grid_energy_kwh:.3f} kWh > "
            f"threshold {threshold_run.grid_energy_kwh:.3f} kWh"
        )


def test_reactive_beats_threshold_on_low_start_grid_energy(config):
    scenario = scenarios.low_start(config)
    threshold_run = _run(config, scenario, "threshold")
    reactive_run = _run(config, scenario, "reactive")
    assert reactive_run.grid_energy_kwh < threshold_run.grid_energy_kwh
    assert reactive_run.grid_energy_kwh == pytest.approx(0.19, abs=0.02)
    assert threshold_run.grid_energy_kwh == pytest.approx(0.75, abs=0.02)


def test_reactive_beats_threshold_on_the_aggregate(config):
    scenario_set = scenarios.standard_set(config)
    threshold_runs = tuple(_run(config, s, "threshold") for s in scenario_set)
    reactive_runs = tuple(_run(config, s, "reactive") for s in scenario_set)

    rows = compare_grid_energy(threshold_runs, reactive_runs)
    threshold_total, reactive_total = aggregate_grid_kwh(rows)

    assert reactive_total < threshold_total
    assert threshold_total == pytest.approx(1.64, abs=0.05)
    assert reactive_total == pytest.approx(1.07, abs=0.05)


def test_cloudy_and_spike_are_a_measured_tie_not_a_regression(config):
    """Documents the limit above rather than letting it drift unnoticed:
    both scenarios currently show zero improvement, not a loss."""
    for name in ("cloudy", "spike"):
        scenario = getattr(scenarios, name)(config)
        threshold_run = _run(config, scenario, "threshold")
        reactive_run = _run(config, scenario, "reactive")
        assert reactive_run.grid_energy_kwh == pytest.approx(
            threshold_run.grid_energy_kwh, abs=1e-6
        )
