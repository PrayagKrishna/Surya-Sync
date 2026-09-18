"""Household water-demand forecasting."""

from surya_sync.ml.demand.models import LinearDemandModel

SELECTED_MODEL = LinearDemandModel
"""Phase 5's chosen demand model, confirmed by the author.

Measured [simulated], ``realistic_household``, 30 days: random forest
edges this out on validation MAE by ~3% (0.0177 vs. 0.0183 L/min), but
linear regression is a single dot product versus traversing 100 trees on
a Raspberry Pi Zero, and a 3% accuracy difference is not worth that cost
without Phase 12 hardware numbers saying otherwise. See ``ROADMAP.md``'s
Phase 5 entry and ``docs/PROJECT_JOURNEY.md``'s "Phase 5 hardened" entry
for the full comparison. Phase 12 may overturn this once Pi inference
cost is actually measured rather than reasoned about."""
