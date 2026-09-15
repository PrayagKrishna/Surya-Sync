"""Safety validation: runs before the scheduler, and again on its output.

Two distinct jobs:

1. **Pre-scheduling gate** — evaluate all rules against current state. If
   a rule with priority above ``MPC_SCHEDULING`` triggers, its forced
   action is executed and the scheduler's opinion is never consulted.
2. **Post-scheduling validation** — re-check the scheduler's proposed
   first action against the same rules. A plan that would violate a hard
   constraint is rejected and replaced, with the override logged as
   ``ReasonCode.SAFETY_OVERRIDE``.

Phase 2 implements both. Phase 11 extends the rule set once hardware
produces electrical telemetry — see ``rules.UNCOVERED_PRIORITIES``.
"""

from __future__ import annotations

from dataclasses import dataclass

from surya_sync.domain import ControlAction
from surya_sync.safety.rules import SafetyPriority, SafetyRule, SafetyVerdict
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

    @property
    def covered_priorities(self) -> tuple[SafetyPriority, ...]:
        """Priorities this validator actually has a rule for, sorted.

        Exposed so that a gap in the safety layer is a queryable fact
        rather than something a reader has to infer from the constructor
        call. ``rules.UNCOVERED_PRIORITIES`` records why the gaps exist.
        """
        return tuple(sorted({rule.priority for rule in self._rules}))

    def check_state(self, state: SystemState, resource_id: str) -> SafetyDecision:
        """Pre-scheduling gate over current state.

        Every rule is evaluated, not just the ones up to the first trigger.
        A verdict that was never produced is indistinguishable from a rule
        that does not exist, and ``all_verdicts`` is the evidence that the
        layer ran — so the cost of evaluating a few cheap rules after the
        decision is already made is worth paying.

        The winning verdict is the triggered rule of highest authority that
        ranks above ``MPC_SCHEDULING``. Rules at or below that rank are
        preferences, not overrides, and never displace the scheduler.
        """
        verdicts = tuple(rule.evaluate(state, resource_id) for rule in self._rules)

        for verdict in verdicts:
            if verdict.triggered and verdict.priority < SafetyPriority.MPC_SCHEDULING:
                if verdict.forced_action is None:
                    raise ValueError(
                        f"safety rule {verdict.rule_name!r} triggered without a "
                        "forced_action; a rule that overrides the scheduler must "
                        "say what to do instead"
                    )
                return SafetyDecision(
                    allowed=False,
                    forced_action=verdict.forced_action,
                    triggering_verdict=verdict,
                    all_verdicts=verdicts,
                )

        return SafetyDecision(
            allowed=True,
            forced_action=None,
            triggering_verdict=None,
            all_verdicts=verdicts,
        )

    def validate_action(
        self, state: SystemState, resource_id: str, action: ControlAction
    ) -> SafetyDecision:
        """Post-scheduling validation of a proposed action.

        Re-runs the same rules against the same state. That is not wasted
        work: the pre-gate asks "must something happen regardless of the
        scheduler", and this asks "is what the scheduler chose allowed".
        A plan can pass the first and fail the second — the gate is silent
        when nothing is compelled, which is precisely when a scheduler is
        free to propose something a rule forbids.

        An action that matches what a rule was going to force is allowed:
        the scheduler agreed with safety, so there is nothing to override.
        Rules are asked ``evaluate_action`` rather than ``evaluate`` here,
        because a rule can forbid an action without compelling one — see
        ``EquipmentConstraintRule``.
        """
        verdicts = tuple(
            rule.evaluate_action(state, resource_id, action) for rule in self._rules
        )

        for verdict in verdicts:
            if not (verdict.triggered and verdict.priority < SafetyPriority.MPC_SCHEDULING):
                continue
            if verdict.forced_action is None:
                raise ValueError(
                    f"safety rule {verdict.rule_name!r} triggered without a "
                    "forced_action; a rule that overrides the scheduler must "
                    "say what to do instead"
                )
            # The highest-authority triggered rule settles it. A lower rule
            # that disagrees has already lost, so the scan stops here.
            agrees = verdict.forced_action is action
            return SafetyDecision(
                allowed=agrees,
                forced_action=None if agrees else verdict.forced_action,
                triggering_verdict=verdict,
                all_verdicts=verdicts,
            )

        return SafetyDecision(
            allowed=True,
            forced_action=None,
            triggering_verdict=None,
            all_verdicts=verdicts,
        )
