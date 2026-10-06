"""Rooftop PV generation forecasting."""

from surya_sync.ml.solar.models import LinearSolarModel

SELECTED_MODEL = LinearSolarModel
"""Phase 6's chosen solar model, confirmed by the author.

Measured [estimated, PVGIS-ERA5], 2020-2022 hourly, all leads pooled, test
MAE: linear 0.1754, random forest 0.1744, gradient boosting 0.1756 kW — a
tie, with the best baseline (historical profile) at 0.2411. Validation
favours the tree tiers by ~4%, which does not survive to test. Linear is
14 dot products per forecast against hundreds of tree traversals per lead
on a Raspberry Pi Zero, so it is chosen on cost with accuracy equal. Same
call as ``ml.demand.SELECTED_MODEL``, and like it provisional until Phase
12 measures actual Pi inference cost. See ``ROADMAP.md``'s Phase 6 entry."""
