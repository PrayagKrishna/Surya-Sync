"""Quantify how long actuation can be deferred without breaching a hard floor.

Flexibility is the quantity the whole system spends. The scheduler's claim
is never "the pump should run now" in isolation — it is "the pump need not
run now, because there is *this much* slack and a better-lit window inside
it." ``flexibility_minutes`` is the number the "Why?" screen renders.

This lives in ``models/`` rather than in ``simulator/`` deliberately. The
real resource in Phase 11 needs exactly this arithmetic, and if it had to
reach into the simulator to get it, someone would eventually copy it
instead — which is how "never fork the algorithm" quietly stops being true.

Everything here is a pure function of its arguments: no clock, no state, no
randomness.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from surya_sync.domain import Forecast, Provenance, forecast_step_minutes, forecast_value_at
from surya_sync.models.generic_resource import (
    FlexibilityEstimate,
    ResourceConstraints,
    ResourceObservation,
)
from surya_sync.models.pump import PumpModel
from surya_sync.models.tank import TankModel, require_demand_forecast


def estimate_tank_flexibility(
    tank: TankModel,
    pump: PumpModel,
    constraints: ResourceConstraints,
    observation: ResourceObservation,
    demand_forecast: Forecast,
    resource_id: str,
) -> FlexibilityEstimate:
    """How long the pump can stay off before a floor is breached.

    Conservative by construction: uses ``p90`` demand where the forecast
    carries a band, so flexibility is understated rather than overstated.
    Until Phase 10 populates bands this falls back to ``p50``, and the
    estimate is only as cautious as the forecast is.

    Note that ``flexibility_minutes`` reports the *horizon length* when no
    floor is breached within it. That is a lower bound presented as a value.
    It errs conservative, and ``FlexibilityEstimate`` has no field to say so
    — do not read it as exact.
    """
    require_demand_forecast(demand_forecast)

    step_minutes = forecast_step_minutes(demand_forecast)
    n_steps = max(1, len(demand_forecast.points))
    demands = [
        forecast_value_at(
            demand_forecast,
            observation.timestamp + timedelta(minutes=index * step_minutes),
            conservative=True,
        )
        for index in range(n_steps)
    ]
    horizon_minutes = n_steps * step_minutes

    flexibility_minutes = minutes_until_below(
        tank,
        observation.native_value,
        demands,
        step_minutes,
        constraints.service_level_min,
        pump_lpm=0.0,
    )
    time_to_critical_minutes = minutes_until_below(
        tank,
        observation.native_value,
        demands,
        step_minutes,
        constraints.service_level_critical,
        pump_lpm=0.0,
    )

    if flexibility_minutes is None:
        flexibility_minutes = horizon_minutes
    if time_to_critical_minutes is None:
        time_to_critical_minutes = horizon_minutes

    return FlexibilityEstimate(
        timestamp=observation.timestamp,
        resource_id=resource_id,
        flexibility_minutes=flexibility_minutes,
        time_to_critical_minutes=max(flexibility_minutes, time_to_critical_minutes),
        must_run_by=latest_safe_start(
            tank, pump, observation, demands, step_minutes, constraints
        ),
        provenance=Provenance.ESTIMATED,
    )


def minutes_until_below(
    tank: TankModel,
    volume_l: float,
    demands: list[float],
    step_minutes: float,
    floor_level: float,
    pump_lpm: float,
) -> float | None:
    """Minutes until the level first drops below ``floor_level``.

    ``None`` when it never does within the supplied demand series. The
    caller decides what to report for that — the honest answer is "at least
    the horizon", not "infinite".
    """
    volume = volume_l
    for index, demand_lpm in enumerate(demands):
        outcome = tank.step(volume, pump_lpm, demand_lpm, step_minutes)
        volume = outcome.volume_l
        if tank.service_level_from_volume_l(volume) < floor_level:
            return (index + 1) * step_minutes
    return None


def latest_safe_start(
    tank: TankModel,
    pump: PumpModel,
    observation: ResourceObservation,
    demands: list[float],
    step_minutes: float,
    constraints: ResourceConstraints,
) -> datetime | None:
    """Last moment at which starting the pump still avoids the hard floor.

    Three outcomes, and they must stay distinguishable:

    ``None``
        Unconstrained. Idling through the whole horizon never approaches
        ``critical``, so there is no deadline to report.
    a time in the future
        The genuine deadline.
    ``observation.timestamp``
        Already too late — not even starting immediately holds the level
        above ``critical``. Reporting ``None`` here would read as "no
        deadline" and invite a scheduler to keep deferring at exactly the
        moment it should be running flat out, so the doomed case is
        reported as a deadline of *now* instead.

    Scanning every start time, rather than simply reporting when the level
    hits the floor, is what makes the answer correct when demand outpaces
    the pump — there, starting at the floor is already too late.
    """
    unconstrained = (
        minutes_until_below(
            tank,
            observation.native_value,
            demands,
            step_minutes,
            constraints.service_level_critical,
            pump_lpm=0.0,
        )
        is None
    )
    if unconstrained:
        return None

    latest: datetime | None = None
    for start_index in range(len(demands)):
        volume = observation.native_value
        safe = True
        for index, demand_lpm in enumerate(demands):
            inflow = pump.flow_rate_lpm if index >= start_index else 0.0
            volume = tank.step(volume, inflow, demand_lpm, step_minutes).volume_l
            if tank.service_level_from_volume_l(volume) < constraints.service_level_critical:
                safe = False
                break
        if safe:
            latest = observation.timestamp + timedelta(minutes=start_index * step_minutes)

    return latest if latest is not None else observation.timestamp
