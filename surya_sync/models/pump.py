"""Pump model: delivered flow, energy drawn, and the cycling limits.

This module owns the equipment-protection half of
``FlexibleResource.admissible_actions``. It is kept out of the resource
class so that the simulated tank and the real tank cannot drift apart on
the one question a scheduler is never allowed to get wrong: *is starting
the pump legal right now?*

The model is stateless. Actuator timing arrives as an ``ActuationHistory``
owned by ``state/``, never as a field here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from surya_sync.config.schema import PumpConfig
from surya_sync.domain import ControlAction
from surya_sync.models.generic_resource import ActuationHistory


@dataclass(frozen=True, slots=True)
class PumpModel:
    """A single-speed pump: it is either delivering rated flow or it is off."""

    rated_power_kw: float
    flow_rate_lpm: float
    min_on_minutes: float = 0.0
    min_off_minutes: float = 0.0
    max_starts_per_day: int | None = None
    model_version: str = "pump-1.0.0"

    def __post_init__(self) -> None:
        if self.rated_power_kw <= 0.0:
            raise ValueError("rated_power_kw must be > 0")
        if self.flow_rate_lpm <= 0.0:
            raise ValueError("flow_rate_lpm must be > 0")
        if self.min_on_minutes < 0.0 or self.min_off_minutes < 0.0:
            raise ValueError("min_on_minutes and min_off_minutes must be >= 0")
        if self.max_starts_per_day is not None and self.max_starts_per_day <= 0:
            raise ValueError("max_starts_per_day must be > 0 or None")

    @classmethod
    def from_config(cls, config: PumpConfig) -> PumpModel:
        return cls(
            rated_power_kw=config.rated_power_kw,
            flow_rate_lpm=config.flow_rate_lpm,
            min_on_minutes=config.min_on_minutes,
            min_off_minutes=config.min_off_minutes,
            max_starts_per_day=config.max_starts_per_day,
        )

    # --- physics --------------------------------------------------------

    def inflow_lpm(self, actuator_on: bool) -> float:
        return self.flow_rate_lpm if actuator_on else 0.0

    def delivered_l(self, minutes: float) -> float:
        if minutes < 0.0:
            raise ValueError("minutes must be >= 0")
        return self.flow_rate_lpm * minutes

    def energy_kwh(self, minutes: float) -> float:
        if minutes < 0.0:
            raise ValueError("minutes must be >= 0")
        return self.rated_power_kw * minutes / 60.0

    def minutes_to_deliver(self, litres: float) -> float:
        if litres < 0.0:
            raise ValueError("litres must be >= 0")
        return litres / self.flow_rate_lpm

    # --- equipment protection -------------------------------------------

    def admissible_actions(
        self,
        actuator_on: bool,
        history: ActuationHistory | None,
        now: datetime,
    ) -> tuple[ControlAction, ...]:
        """Actions the equipment limits permit at ``now``.

        The action space is asymmetric because the vocabulary is: ``WAIT``
        means stay de-energized and ``STOP`` means de-energize something
        running, so only one of them is ever meaningful.

        ==========  =========================================
        pump off    ``WAIT``, plus ``RUN`` if a start is legal
        pump on     ``RUN``, plus ``STOP`` if a stop is legal
        ==========  =========================================

        Unknown timing — ``history`` of ``None``, or a ``None``
        ``changed_at`` — reads as *constraint not yet satisfied*, never as
        a long-elapsed one. The consequence is that an unknown holds the
        pump where it is: off stays off, and running keeps running until
        ``state/`` has observed a transition. That asymmetry is deliberate.
        Refusing to switch is recoverable; short-cycling a pump is not, and
        a pump left running is independently bounded by the ESP32 deadman
        timer and the float switch, neither of which depends on this layer.
        """
        if actuator_on:
            actions = [ControlAction.RUN]
            if self.may_stop(history, now):
                actions.append(ControlAction.STOP)
        else:
            actions = [ControlAction.WAIT]
            if self.may_start(history, now):
                actions.append(ControlAction.RUN)
        return tuple(actions)

    def may_start(self, history: ActuationHistory | None, now: datetime) -> bool:
        """Whether min-off time and the daily start budget both allow a start."""
        if history is None:
            return False
        if (
            self.max_starts_per_day is not None
            and history.starts_today >= self.max_starts_per_day
        ):
            return False
        elapsed = history.minutes_in_state(now)
        if elapsed is None:
            return False
        return elapsed >= self.min_off_minutes

    def may_stop(self, history: ActuationHistory | None, now: datetime) -> bool:
        """Whether min-on time has elapsed."""
        if history is None:
            return False
        elapsed = history.minutes_in_state(now)
        if elapsed is None:
            return False
        return elapsed >= self.min_on_minutes
