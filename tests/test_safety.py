"""The safety layer — Phase 2.

Two claims carry these tests:

1. The **priority order** is obeyed in practice, not just declared in an
   enum. Every ordering assertion here is written as a conflict between two
   rules that want different things, because that is the only situation in
   which an ordering can be wrong.
2. A rule that cannot evaluate says so rather than passing. Missing data is
   exactly when safety matters, so "no observation" must not read as "no
   problem".
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from surya_sync.config.schema import Config
from surya_sync.domain import ControlAction, Provenance, RunMode
from surya_sync.models.generic_resource import (
    ActuationHistory,
    ResourceObservation,
    ResourceRegistry,
)
from surya_sync.safety.rules import (
    UNCOVERED_PRIORITIES,
    ActuatorRuntimeLimitRule,
    CriticalServiceRule,
    EquipmentConstraintRule,
    ManualOverrideRule,
    OverflowProtectionRule,
    SafetyPriority,
    SensorValidityRule,
    default_rules,
    hold_action,
)
from surya_sync.safety.validator import SafetyValidator
from surya_sync.simulator.demand import ConstantDemandProfile
from surya_sync.simulator.solar import ClearSkyProfile
from surya_sync.simulator.tank import SimulatedTankResource, TankSimulator
from surya_sync.state.system_state import SystemState
from surya_sync.version import VersionStamp

NOW = datetime(2026, 3, 2, 9, 0)
RESOURCE_ID = "tank_1"


@pytest.fixture
def config() -> Config:
    return Config()


def make_resource(config: Config, level: float = 0.6) -> SimulatedTankResource:
    simulator = TankSimulator.from_config(
        config=config,
        demand_profile=ConstantDemandProfile(lpm=2.0),
        solar_profile=ClearSkyProfile.from_config(config.solar),
        start=NOW,
        initial_service_level=level,
        resource_id=RESOURCE_ID,
    )
    return SimulatedTankResource(simulator)


def make_state(
    *,
    level: float = 0.6,
    actuator_on: bool = False,
    sensor_valid: bool = True,
    observed: bool = True,
    history: ActuationHistory | None = None,
    manual_override: ControlAction | None = None,
    now: datetime = NOW,
) -> SystemState:
    observations = {}
    if observed:
        observations[RESOURCE_ID] = ResourceObservation(
            timestamp=now,
            resource_id=RESOURCE_ID,
            service_level=level,
            native_value=level * 1000.0,
            native_unit="l",
            actuator_on=actuator_on,
            provenance=Provenance.SIMULATED,
            sensor_valid=sensor_valid,
        )
    return SystemState(
        timestamp=now,
        run_id=None,
        mode=RunMode.SIMULATED,
        versions=VersionStamp(),
        observations=observations,
        actuation_history={RESOURCE_ID: history} if history else {},
        manual_override=manual_override,
    )


def settled(actuator_on: bool, minutes: float = 60.0, starts: int = 1) -> ActuationHistory:
    """History of an actuator that has held its state long enough to switch."""
    return ActuationHistory(
        resource_id=RESOURCE_ID,
        actuator_on=actuator_on,
        changed_at=NOW - timedelta(minutes=minutes),
        starts_today=starts,
    )


# --- individual rules ---------------------------------------------------


def test_hold_action_is_not_the_idle_action():
    """Holding a running actuator is RUN, not STOP. Confusing the two turns
    every failure path into an unintended shutdown."""
    assert hold_action(actuator_on=True) is ControlAction.RUN
    assert hold_action(actuator_on=False) is ControlAction.WAIT


def test_missing_observation_blocks_actuation(config):
    rule = SensorValidityRule()
    verdict = rule.evaluate(make_state(observed=False), RESOURCE_ID)
    assert verdict.triggered
    assert verdict.forced_action is ControlAction.STOP


def test_invalid_sensor_blocks_actuation(config):
    rule = SensorValidityRule()
    verdict = rule.evaluate(make_state(sensor_valid=False), RESOURCE_ID)
    assert verdict.triggered
    assert verdict.forced_action is ControlAction.STOP


def test_valid_sensor_says_nothing(config):
    assert not SensorValidityRule().evaluate(make_state(), RESOURCE_ID).triggered


def test_overflow_rule_stops_at_the_ceiling(config):
    registry = ResourceRegistry(resources=(make_resource(config),))
    rule = OverflowProtectionRule(registry)
    ceiling = config.tank.max_level

    assert rule.evaluate(make_state(level=ceiling - 0.01), RESOURCE_ID).triggered is False
    verdict = rule.evaluate(make_state(level=ceiling), RESOURCE_ID)
    assert verdict.triggered
    assert verdict.forced_action is ControlAction.STOP


def test_critical_rule_runs_at_the_floor(config):
    registry = ResourceRegistry(resources=(make_resource(config),))
    rule = CriticalServiceRule(registry)
    floor = config.tank.critical_level

    assert rule.evaluate(make_state(level=floor + 0.01), RESOURCE_ID).triggered is False
    verdict = rule.evaluate(make_state(level=floor), RESOURCE_ID)
    assert verdict.triggered
    assert verdict.forced_action is ControlAction.RUN


def test_an_untrusted_reading_does_not_trigger_the_level_rules(config):
    """A rule must not act on a number it has been told is wrong. Both level
    rules stand down and let ``SensorValidityRule``, which outranks one of
    them and is outranked by neither, decide."""
    registry = ResourceRegistry(resources=(make_resource(config),))
    state = make_state(level=0.01, sensor_valid=False)
    assert not CriticalServiceRule(registry).evaluate(state, RESOURCE_ID).triggered
    state_high = make_state(level=0.99, sensor_valid=False)
    assert not OverflowProtectionRule(registry).evaluate(state_high, RESOURCE_ID).triggered


def test_manual_override_is_passed_through_verbatim(config):
    rule = ManualOverrideRule()
    verdict = rule.evaluate(make_state(manual_override=ControlAction.RUN), RESOURCE_ID)
    assert verdict.triggered
    assert verdict.forced_action is ControlAction.RUN


def test_runtime_limit_stops_a_pump_that_has_run_too_long(config):
    rule = ActuatorRuntimeLimitRule(max_runtime_minutes=30.0)
    short = make_state(actuator_on=True, history=settled(True, minutes=29.0))
    assert not rule.evaluate(short, RESOURCE_ID).triggered

    long = make_state(actuator_on=True, history=settled(True, minutes=30.0))
    verdict = rule.evaluate(long, RESOURCE_ID)
    assert verdict.triggered
    assert verdict.forced_action is ControlAction.STOP


def test_unknown_runtime_stops_the_pump(config):
    """The deliberate inversion. ``admissible_actions`` reads unknown timing
    as "do not switch"; the deadman reads it as "you cannot prove this is
    safe". Priority 2 outranks priority 7, so the deadman wins — and that is
    the direction that costs a min-off wait instead of a pump."""
    rule = ActuatorRuntimeLimitRule(max_runtime_minutes=30.0)
    unknown = ActuationHistory.unknown(RESOURCE_ID, actuator_on=True)
    verdict = rule.evaluate(
        make_state(actuator_on=True, history=unknown), RESOURCE_ID
    )
    assert verdict.triggered
    assert verdict.forced_action is ControlAction.STOP


def test_runtime_limit_ignores_an_idle_actuator(config):
    rule = ActuatorRuntimeLimitRule(max_runtime_minutes=30.0)
    state = make_state(actuator_on=False, history=ActuationHistory.unknown(RESOURCE_ID, False))
    assert not rule.evaluate(state, RESOURCE_ID).triggered


def test_runtime_limit_rejects_a_nonsense_budget():
    with pytest.raises(ValueError):
        ActuatorRuntimeLimitRule(max_runtime_minutes=0.0)


def test_equipment_rule_is_silent_when_a_choice_exists(config):
    registry = ResourceRegistry(resources=(make_resource(config),))
    rule = EquipmentConstraintRule(registry)
    state = make_state(history=settled(False))
    assert not rule.evaluate(state, RESOURCE_ID).triggered


def test_equipment_rule_holds_during_cooldown(config):
    registry = ResourceRegistry(resources=(make_resource(config),))
    rule = EquipmentConstraintRule(registry)
    cooling = ActuationHistory(
        resource_id=RESOURCE_ID,
        actuator_on=False,
        changed_at=NOW - timedelta(minutes=1.0),
        starts_today=1,
    )
    verdict = rule.evaluate(make_state(history=cooling), RESOURCE_ID)
    assert verdict.triggered
    assert verdict.forced_action is ControlAction.WAIT


def test_equipment_rule_rejects_an_inadmissible_proposal(config):
    """``evaluate`` is silent whenever a choice exists, so without
    ``evaluate_action`` nothing would ever reject a bad proposal. STOP on an
    idle pump is meaningless and must not reach the actuator."""
    registry = ResourceRegistry(resources=(make_resource(config),))
    rule = EquipmentConstraintRule(registry)
    state = make_state(actuator_on=False, history=settled(False))

    assert not rule.evaluate(state, RESOURCE_ID).triggered
    verdict = rule.evaluate_action(state, RESOURCE_ID, ControlAction.STOP)
    assert verdict.triggered
    assert verdict.forced_action is ControlAction.WAIT


def test_rules_default_to_their_state_verdict_for_an_action(config):
    """Rules that compel an action need no separate action logic: if the
    tank is over its ceiling the pump stops, whatever was proposed."""
    registry = ResourceRegistry(resources=(make_resource(config),))
    rule = OverflowProtectionRule(registry)
    state = make_state(level=0.99)
    assert rule.evaluate_action(state, RESOURCE_ID, ControlAction.RUN).forced_action is (
        ControlAction.STOP
    )


# --- the validator ------------------------------------------------------


def make_validator(config: Config, level: float = 0.6) -> SafetyValidator:
    registry = ResourceRegistry(resources=(make_resource(config, level=level),))
    return SafetyValidator(
        default_rules(registry, max_runtime_minutes=config.pump.max_runtime_seconds / 60.0)
    )


def test_every_rule_is_evaluated_even_after_one_triggers(config):
    """A verdict that was never produced is indistinguishable from a rule
    that does not exist. ``all_verdicts`` is the evidence the layer ran."""
    validator = make_validator(config)
    decision = validator.check_state(
        make_state(level=0.01, history=settled(False)), RESOURCE_ID
    )
    assert not decision.allowed
    assert len(decision.all_verdicts) == len(validator.rules)


def test_nothing_triggered_means_the_scheduler_decides(config):
    validator = make_validator(config)
    decision = validator.check_state(make_state(history=settled(False)), RESOURCE_ID)
    assert decision.allowed
    assert decision.forced_action is None
    assert decision.triggering_verdict is None


def test_overflow_outranks_manual_override(config):
    """The user commands the system, not the physics."""
    validator = make_validator(config)
    state = make_state(
        level=0.99, manual_override=ControlAction.RUN, history=settled(False)
    )
    decision = validator.check_state(state, RESOURCE_ID)
    assert decision.forced_action is ControlAction.STOP
    assert decision.triggering_verdict.priority is SafetyPriority.OVERFLOW_PROTECTION


def test_sensor_validity_outranks_critical_service(config):
    """A tank that reads empty through a broken sensor must not start a pump.
    Overflow is unrecoverable; a dry tap is not."""
    validator = make_validator(config)
    state = make_state(level=0.01, sensor_valid=False, history=settled(False))
    decision = validator.check_state(state, RESOURCE_ID)
    assert decision.forced_action is ControlAction.STOP
    assert decision.triggering_verdict.priority is SafetyPriority.SENSOR_VALIDITY


def test_critical_service_outranks_equipment_cooldown(config):
    """A documented and deliberate consequence of the project's priority
    order: a tank at its critical floor starts the pump even though the
    min-off cooldown has not elapsed. Equipment protection is ranked below
    service availability on purpose, and the actuator's own deadman (rank 2)
    is what still bounds the damage."""
    validator = make_validator(config)
    cooling = ActuationHistory(
        resource_id=RESOURCE_ID,
        actuator_on=False,
        changed_at=NOW - timedelta(minutes=1.0),
        starts_today=1,
    )
    decision = validator.check_state(
        make_state(level=0.01, history=cooling), RESOURCE_ID
    )
    assert decision.forced_action is ControlAction.RUN
    assert (
        decision.triggering_verdict.priority
        is SafetyPriority.CRITICAL_SERVICE_AVAILABILITY
    )


def test_actuator_protection_outranks_everything_below_it(config):
    validator = make_validator(config)
    state = make_state(
        actuator_on=True,
        level=0.01,
        history=settled(True, minutes=999.0),
        manual_override=ControlAction.RUN,
    )
    decision = validator.check_state(state, RESOURCE_ID)
    assert decision.forced_action is ControlAction.STOP
    assert decision.triggering_verdict.priority is SafetyPriority.ACTUATOR_PROTECTION


def test_agreement_is_not_an_override(config):
    """When the scheduler proposes what safety was going to force, nothing
    was overridden and the decision log must not claim otherwise."""
    validator = make_validator(config)
    state = make_state(level=0.01, history=settled(False))
    decision = validator.validate_action(state, RESOURCE_ID, ControlAction.RUN)
    assert decision.allowed
    assert decision.forced_action is None
    assert decision.triggering_verdict is not None


def test_disagreement_is_an_override(config):
    validator = make_validator(config)
    state = make_state(level=0.01, history=settled(False))
    decision = validator.validate_action(state, RESOURCE_ID, ControlAction.WAIT)
    assert not decision.allowed
    assert decision.forced_action is ControlAction.RUN


def test_validate_action_catches_what_the_gate_lets_through(config):
    """The gate is silent when nothing is compelled, which is precisely when
    a scheduler is free to propose something a rule forbids. If this fails,
    post-scheduling validation is decorative."""
    validator = make_validator(config)
    state = make_state(actuator_on=False, history=settled(False))
    assert validator.check_state(state, RESOURCE_ID).allowed

    decision = validator.validate_action(state, RESOURCE_ID, ControlAction.STOP)
    assert not decision.allowed
    assert decision.forced_action is ControlAction.WAIT


def test_a_triggered_rule_without_an_action_is_a_bug(config):
    """A rule that overrides the scheduler must say what to do instead.
    Silence there would stop the cycle with no action at all."""
    from surya_sync.safety.rules import SafetyRule, SafetyVerdict

    class Broken(SafetyRule):
        name = "broken"
        priority = SafetyPriority.OVERFLOW_PROTECTION

        def evaluate(self, state, resource_id):
            return SafetyVerdict(self.name, self.priority, triggered=True)

    validator = SafetyValidator((Broken(),))
    with pytest.raises(ValueError, match="forced_action"):
        validator.check_state(make_state(), RESOURCE_ID)


def test_rules_below_the_scheduler_never_override_it(config):
    from surya_sync.safety.rules import SafetyRule, SafetyVerdict

    class Preference(SafetyRule):
        name = "solar_preference"
        priority = SafetyPriority.SOLAR_UTILIZATION

        def evaluate(self, state, resource_id):
            return SafetyVerdict(
                self.name, self.priority, triggered=True, forced_action=ControlAction.RUN
            )

    validator = SafetyValidator((Preference(),))
    assert validator.check_state(make_state(), RESOURCE_ID).allowed


def test_the_gaps_in_the_layer_are_declared(config):
    """A gap that is written down and tested for beats a rule that checks
    nothing and makes the layer look complete."""
    validator = make_validator(config)
    covered = set(validator.covered_priorities)

    assert SafetyPriority.ELECTRICAL_SAFETY in UNCOVERED_PRIORITIES
    assert SafetyPriority.ELECTRICAL_SAFETY not in covered
    assert covered == {
        SafetyPriority.ACTUATOR_PROTECTION,
        SafetyPriority.OVERFLOW_PROTECTION,
        SafetyPriority.SENSOR_VALIDITY,
        SafetyPriority.CRITICAL_SERVICE_AVAILABILITY,
        SafetyPriority.MANUAL_OVERRIDE,
        SafetyPriority.EQUIPMENT_CONSTRAINTS,
    }
    assert not set(UNCOVERED_PRIORITIES) & covered


def test_rule_order_is_not_taken_from_the_factory(config):
    """``default_rules`` returns them in construction order; the validator
    sorts. Otherwise the priority order could be changed by reordering a
    tuple, which is not a change anyone would notice in review."""
    validator = make_validator(config)
    priorities = [rule.priority for rule in validator.rules]
    assert priorities == sorted(priorities)
