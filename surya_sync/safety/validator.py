"""Safety validation: runs before the scheduler, and again on its output.

Two distinct jobs:

1. **Pre-scheduling gate** — evaluate all rules against current state. If
   a rule with priority above ``MPC_SCHEDULING`` triggers, its forced
   action is executed and the scheduler's opinion is never consulted.
2. **Post-scheduling validation** — re-check the scheduler's proposed
   first action against the same rules. A plan that would violate a hard
   constraint is rejected and replaced, with the override logged as
   ``ReasonCode.SAFETY_OVERRIDE``.

Phase 0: interface only. Implemented in Phase 2, extended in Phase 11.
"""

from __future__ import annotations

from dataclasses import dataclass

from surya_sync.domain import ControlAction
from surya_sync.safety.rules import SafetyRule, SafetyVerdict
from surya_sync.state.system_state import SystemState


@dataclass(frozen=True, slots=True)
class SafetyDecision:
    """Combined result of evaluating the full rule set."""

    allowed: bool
    """False when a rule forces an action, overriding the scheduler."""

    forced_action: ControlAction | None
    triggering_verdict: SafetyVerdict | None
    all_verdicts: tuple[SafetyVerdict, ...] = ()
    """Every verdict, triggered or not — an untriggered rule that was
    evaluated is evidence; a rule that never ran is a gap."""


class SafetyValidator:
    """Evaluates ``SafetyRule`` instances in strict priority order."""

    def __init__(self, rules: tuple[SafetyRule, ...]) -> None:
        self._rules = tuple(sorted(rules, key=lambda r: r.priority))

    @property
    def rules(self) -> tuple[SafetyRule, ...]:
        """Rules in evaluation order (highest authority first)."""
        return self._rules

    def check_state(self, state: SystemState, resource_id: str) -> SafetyDecision:
        """Pre-scheduling gate over current state."""
        raise NotImplementedError("Phase 2 — see ROADMAP.md")

    def validate_action(
        self, state: SystemState, resource_id: str, action: ControlAction
    ) -> SafetyDecision:
        """Post-scheduling validation of a proposed action."""
        raise NotImplementedError("Phase 2 — see ROADMAP.md")
