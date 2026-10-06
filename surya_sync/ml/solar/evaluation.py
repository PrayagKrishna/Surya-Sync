"""Score solar forecasters per lead, and decide whether ML earned its place.

Exit criterion (``ROADMAP.md`` Phase 6): ML is kept only if it beats both
baselines on held-out data. Spelled out, so it cannot be argued with after
the numbers are in:

1. **Validation, every headline lead.** Its MAE must be strictly below the
   best baseline's at each of ``HEADLINE_LEADS``. A model that wins on the
   1-hour average by being excellent at 1 hour and useless at 12 is not a
   better forecast for a scheduler that looks 12 hours ahead.
2. **Test, overall.** Its MAE across all leads must be below the best
   baseline's. Test is only a confirmation of a choice made on validation.

"Best baseline" is taken per lead, not overall, so a baseline is judged at
the lead it is actually good at.
"""

from __future__ import annotations

from dataclasses import dataclass

from surya_sync.ml.evaluation import mean_absolute_error, root_mean_squared_error
from surya_sync.ml.solar.dataset import HEADLINE_LEADS, SolarDataset, SolarSplit


@dataclass(frozen=True, slots=True)
class MethodScore:
    name: str
    val_mae: float
    val_rmse: float
    test_mae: float
    test_rmse: float
    val_mae_by_lead: dict[int, float]
    test_mae_by_lead: dict[int, float]


def _by_lead(split: SolarSplit, predictions: tuple[float, ...]) -> dict[int, float]:
    grouped: dict[int, tuple[list[float], list[float]]] = {}
    for index, (truth, guess) in enumerate(zip(split.targets, predictions)):
        true_values, guesses = grouped.setdefault(split.lead_of(index), ([], []))
        true_values.append(truth)
        guesses.append(guess)
    return {lead: mean_absolute_error(t, g) for lead, (t, g) in sorted(grouped.items())}


def score_method(model, dataset: SolarDataset) -> MethodScore:
    """``model`` must already be fitted (or need no fitting)."""
    val_pred = model.predict(dataset.val.features)
    test_pred = model.predict(dataset.test.features)
    return MethodScore(
        name=model.model_name,
        val_mae=mean_absolute_error(dataset.val.targets, val_pred),
        val_rmse=root_mean_squared_error(dataset.val.targets, val_pred),
        test_mae=mean_absolute_error(dataset.test.targets, test_pred),
        test_rmse=root_mean_squared_error(dataset.test.targets, test_pred),
        val_mae_by_lead=_by_lead(dataset.val, val_pred),
        test_mae_by_lead=_by_lead(dataset.test, test_pred),
    )


def earns_its_place(
    candidate: MethodScore, baselines: list[MethodScore]
) -> tuple[bool, list[str]]:
    """Apply the two-part rule above. Returns ``(verdict, reasons it failed)``."""
    failures: list[str] = []
    for lead in HEADLINE_LEADS:
        best = min(b.val_mae_by_lead[lead] for b in baselines)
        mine = candidate.val_mae_by_lead[lead]
        if mine >= best:
            failures.append(f"val lead {lead}h: {mine:.4f} >= best baseline {best:.4f} kW")
    best_test = min(b.test_mae for b in baselines)
    if candidate.test_mae >= best_test:
        failures.append(f"test overall: {candidate.test_mae:.4f} >= best baseline {best_test:.4f} kW")
    return (not failures, failures)
