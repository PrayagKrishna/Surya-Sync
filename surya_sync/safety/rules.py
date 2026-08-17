"""Safety rules and their strict priority order.

The safety layer runs **before** the scheduler, every cycle, without
exception. It is rule-based and deterministic — no model, no forecast, no
optimization. If the sophisticated stack disagrees with a safety rule, the
safety rule wins.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import IntEnum

from surya_sync.domain import ControlAction
from surya_sync.state.system_state import SystemState


class SafetyPriority(IntEnum):
    """Evaluation order. Lower value = higher authority.

    This ordering is a project invariant, not a tuning parameter. It
    encodes the claim that equipment and people matter more than solar
    optimization, and it must not be reordered to make a benchmark look
    better.
    """

    ELECTRICAL_SAFETY = 1
    PUMP_PROTECTION = 2
    OVERFLOW_PROTECTION = 3
    SENSOR_VALIDITY = 4
    CRITICAL_WATER_AVAILABILITY = 5
    MANUAL_OVERRIDE = 6
    EQUIPMENT_CONSTRAINTS = 7
    MPC_SCHEDULING = 8
    """The scheduler's own preference sits here — below every rule above."""

    SOLAR_UTILIZATION = 9
    GRID_OPTIMIZATION = 10


@dataclass(frozen=True, slots=True)
class SafetyVerdict:
    """The outcome of evaluating one rule."""

    rule_name: str
    priority: SafetyPriority
    triggered: bool
    forced_action: ControlAction | None = None
    """Non-``None`` only when ``triggered``. The rule is *compelling* this
    action; lower-priority rules and the scheduler cannot overturn it."""

    message: str = ""
    """Recorded verbatim in ``safety_events`` and shown to the user."""


class SafetyRule(ABC):
    """One deterministic check over system state.

    Rules must be cheap and total: no I/O, no exceptions, and a verdict
    for every possible state — including states with missing or invalid
    data, which are exactly when safety matters most.
    """

    name: str
    priority: SafetyPriority

    @abstractmethod
    def evaluate(self, state: SystemState, resource_id: str) -> SafetyVerdict:
        """Return this rule's verdict for the given resource."""
