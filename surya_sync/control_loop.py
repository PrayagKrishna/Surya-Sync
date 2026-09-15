"""One control cycle: safety, then scheduling, then safety again.

This is the module that makes the hard rule *"the safety layer runs before
the scheduler"* structural instead of a convention. Nothing else is
supposed to call ``Scheduler.generate_plan`` in production; everything goes
through ``ControlCycle.decide``, which cannot be made to skip the gate.

It lives at the top level, above both ``simulator/`` and ``hardware/``,
because the simulated and the real loop must be the same loop. If the
simulation driver had its own copy, the thing Phase 13 validates on the
roof would not be the thing Phase 14 benchmarked.

**It decides; it does not act.** ``decide`` returns the action to execute
and never executes it. Actuation belongs to the caller — ``TankSimulator``
in simulation, the ESP32 serial link in Phase 11 — because that is the step
where *command sent != command executed*, and a function that both decided
and actuated would have nowhere to record the difference.

The cycle this sits inside::

    observe -> build context -> forecast -> predict -> DECIDE -> execute
    first action only -> observe again
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from surya_sync.domain import ControlAction
from surya_sync.safety.rules import SafetyPriority
from surya_sync.safety.validator import SafetyDecision, SafetyValidator
from surya_sync.scheduler.base import (
    ConstraintStatus,
    DecisionExplanation,
    FallbackChain,
    PlannedAction,
    ReasonCode,
    SchedulerTier,
    SchedulingPlan,
    SchedulingRequest,
    SolverStatus,
)
from surya_sync.state.system_state import SystemState

SAFETY_REASON_CODES: dict[SafetyPriority, ReasonCode] = {
    SafetyPriority.MANUAL_OVERRIDE: ReasonCode.MANUAL_OVERRIDE,
    SafetyPriority.SENSOR_VALIDITY: ReasonCode.SENSOR_INVALID,
    SafetyPriority.EQUIPMENT_CONSTRAINTS: ReasonCode.EQUIPMENT_COOLDOWN,
    SafetyPriority.CRITICAL_SERVICE_AVAILABILITY: ReasonCode.CRITICAL_LEVEL,
}
"""Substantive reason behind a forced action, where one exists.

A safety override is a *fact about the decision path*, not a rationale, and
the "Why?" screen needs the rationale. "The tank is below its critical
floor" is what the household needs to read; "a safety rule fired" is not.
Priorities without an entry here fall back to ``SAFETY_OVERRIDE``, which is
honest for a rule whose whole content is "stop".
"""


@dataclass(frozen=True, slots=True)
class CycleDecision:
    """What one cycle concluded, and how it got there.

    Both safety evaluations are carried, triggered or not. A cycle where
    nothing fired is evidence that the rules ran; a cycle with no verdicts
    at all is evidence of nothing.
    """

    action: ControlAction
    """The action to execute. This and only this — never the rest of the plan."""

    plan: SchedulingPlan
    pre_gate: SafetyDecision
    post_validation: SafetyDecision | None
    """``None`` when the pre-gate forced an action and the scheduler was
    never consulted — there was no proposal to validate."""

    @property
    def scheduler_consulted(self) -> bool:
        return self.post_validation is not None

    @property
    def safety_overridden(self) -> bool:
        """Whether safety replaced what the scheduler wanted.

        A pre-gate trigger is not an override in this sense: nothing was
        overridden, because nothing was proposed.
        """
        return self.post_validation is not None and not self.post_validation.allowed


class ControlCycle:
    """Runs the safety layer around one scheduling decision."""

    def __init__(self, validator: SafetyValidator, chain: FallbackChain) -> None:
        self._validator = validator
        self._chain = chain

    def decide(self, state: SystemState, request: SchedulingRequest) -> CycleDecision:
        """Decide what to do with one resource, this cycle.

        ``state`` and ``request`` must describe the same instant. They are
        separate arguments because they answer to different owners:
        ``state/`` writes the first, and the caller assembles the second
        from forecasts the scheduler is allowed to see.
        """
        resource_id = request.observation.resource_id

        pre_gate = self._validator.check_state(state, resource_id)
        if not pre_gate.allowed:
            assert pre_gate.forced_action is not None  # check_state guarantees it
            return CycleDecision(
                action=pre_gate.forced_action,
                plan=self._forced_plan(request, pre_gate),
                pre_gate=pre_gate,
                post_validation=None,
            )

        plan = self._chain.generate_plan(request)
        post = self._validator.validate_action(state, resource_id, plan.first_action)
        if post.allowed:
            return CycleDecision(
                action=plan.first_action,
                plan=plan,
                pre_gate=pre_gate,
                post_validation=post,
            )

        assert post.forced_action is not None  # validate_action guarantees it
        return CycleDecision(
            action=post.forced_action,
            plan=self._overridden_plan(plan, post),
            pre_gate=pre_gate,
            post_validation=post,
        )

    # --- plans for decisions the scheduler did not make -------------------

    def _forced_plan(
        self, request: SchedulingRequest, decision: SafetyDecision
    ) -> SchedulingPlan:
        """A plan for an action the safety layer compelled outright.

        Synthesized rather than skipped: *every* decision produces a
        structured explanation, including the ones the scheduler never saw.
        A cycle with no explanation is a cycle the "Why?" screen has to show
        a blank for, and those are exactly the cycles a user asks about.
        """
        verdict = decision.triggering_verdict
        assert verdict is not None
        action = decision.forced_action
        assert action is not None

        reason = SAFETY_REASON_CODES.get(verdict.priority, ReasonCode.SAFETY_OVERRIDE)
        return SchedulingPlan(
            first_action=action,
            planned_actions=(
                PlannedAction(step_index=0, target_time=request.now, action=action),
            ),
            explanation=DecisionExplanation(
                decision=action,
                reason_code=reason,
                service_level=request.observation.service_level,
                flexibility_minutes=(
                    request.flexibility.flexibility_minutes
                    if request.flexibility is not None
                    else 0.0
                ),
                solar_surplus_kw=request.solar_surplus_kw,
                notes=(
                    f"safety rule {verdict.rule_name!r} "
                    f"(priority {verdict.priority.name}) forced this action",
                    verdict.message,
                    "the scheduler was not consulted",
                ),
            ),
            objective_value=None,
            predicted_states=(),
            constraint_status=ConstraintStatus(
                satisfied=True, violations=(), checked_constraints=()
            ),
            solver_status=SolverStatus.NOT_APPLICABLE,
            algorithm_version="safety-1.0.0",
            scheduler_name=f"safety:{verdict.rule_name}",
            tier=SchedulerTier.SAFETY_LAYER,
            computed_at=request.now,
        )

    def _overridden_plan(
        self, plan: SchedulingPlan, decision: SafetyDecision
    ) -> SchedulingPlan:
        """The scheduler's plan, with safety's action substituted.

        The original reason is kept in the notes rather than discarded. An
        override is only diagnosable if you can still see what was wanted
        and why — "safety said no" on its own tells nobody which scheduler
        needs fixing.
        """
        verdict = decision.triggering_verdict
        assert verdict is not None
        action = decision.forced_action
        assert action is not None

        return replace(
            plan,
            first_action=action,
            planned_actions=(
                PlannedAction(
                    step_index=0, target_time=plan.computed_at, action=action
                ),
            ),
            predicted_states=(),
            explanation=replace(
                plan.explanation,
                decision=action,
                reason_code=ReasonCode.SAFETY_OVERRIDE,
                notes=plan.explanation.notes
                + (
                    f"scheduler proposed {plan.first_action.value} because "
                    f"{plan.explanation.reason_code.value}",
                    f"overridden by {verdict.rule_name!r} "
                    f"(priority {verdict.priority.name})",
                    verdict.message,
                ),
            ),
            constraint_status=ConstraintStatus(
                satisfied=True, violations=(), checked_constraints=()
            ),
        )
