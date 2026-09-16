"""Head-to-head controller comparison under identical conditions.

Phase 3's exit criterion is not "the reactive scheduler runs" — it is that
it *beats the threshold controller on grid-powered pump energy*, and a
claim like that is worthless without the losing side's number sitting next
to it. This module pairs two ``ScenarioRun`` sets, scenario by scenario,
and reports the number the whole project is arguing about: grid energy,
never total pump energy, because total energy does not move — the tank
still needs the same litres — only *where* the energy for it comes from
does.

Every run this compares already carries ``provenance=Provenance.SIMULATED``.
This module does not repeat that per row; the caller prints it once, so
nothing here can drift into implying a physical measurement.
"""

from __future__ import annotations

from dataclasses import dataclass

from surya_sync.experiments.runner import ScenarioRun


@dataclass(frozen=True, slots=True)
class ComparisonRow:
    """Grid energy for one scenario, baseline vs. candidate."""

    scenario_name: str
    baseline_name: str
    candidate_name: str
    baseline_grid_kwh: float
    candidate_grid_kwh: float

    @property
    def grid_kwh_saved(self) -> float:
        """Positive: the candidate drew less from the grid than the baseline."""
        return self.baseline_grid_kwh - self.candidate_grid_kwh

    @property
    def beats_baseline(self) -> bool:
        return self.candidate_grid_kwh < self.baseline_grid_kwh


def compare_grid_energy(
    baseline_runs: tuple[ScenarioRun, ...],
    candidate_runs: tuple[ScenarioRun, ...],
) -> tuple[ComparisonRow, ...]:
    """Pair runs by scenario name and compare grid-powered pump energy.

    Raises if the two sets do not cover the same scenarios: a comparison
    across mismatched conditions would attribute a scenario difference to
    the candidate, which is exactly the mistake Phase 1's step-size trap
    (``Scenario.n_steps`` truncation) already warned about.
    """
    baseline_by_name = {run.scenario_name: run for run in baseline_runs}
    candidate_by_name = {run.scenario_name: run for run in candidate_runs}
    if baseline_by_name.keys() != candidate_by_name.keys():
        raise ValueError(
            "cannot compare runs over different scenario sets: "
            f"baseline={sorted(baseline_by_name)} "
            f"candidate={sorted(candidate_by_name)}"
        )
    return tuple(
        ComparisonRow(
            scenario_name=name,
            baseline_name=baseline_by_name[name].scheduler_name,
            candidate_name=candidate_by_name[name].scheduler_name,
            baseline_grid_kwh=baseline_by_name[name].grid_energy_kwh,
            candidate_grid_kwh=candidate_by_name[name].grid_energy_kwh,
        )
        for name in sorted(baseline_by_name)
    )


def aggregate_grid_kwh(rows: tuple[ComparisonRow, ...]) -> tuple[float, float]:
    """Total baseline vs. candidate grid energy across every row.

    Phase 2 recorded that ``sunny`` already draws 0.00 kWh under the
    threshold controller and cannot be beaten. The aggregate is what stops
    that one unwinnable scenario from being the only number anyone reports;
    it is what the exit criterion is actually judged on, together with
    ``cloudy`` and ``low_start`` individually.
    """
    return (
        sum(row.baseline_grid_kwh for row in rows),
        sum(row.candidate_grid_kwh for row in rows),
    )
