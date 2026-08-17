"""Interfaces — Phase 0 exit criterion: interfaces are stubbed.

These tests guard the *contracts*, not behaviour. They exist so that a
later phase cannot quietly weaken an invariant the project depends on:
one shared scheduler interface, hard constraints that stay hard, and a
fixed safety priority order.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta

import pytest

from surya_sync.domain import ControlAction, Forecast, Horizon, Provenance
from surya_sync.hardware.esp32_serial import ESP32SerialInterface
from surya_sync.hardware.interfaces import HardwareInterface
from surya_sync.models.generic_resource import (
    FlexibilityEstimate,
    FlexibleResource,
    ResourceConstraints,
    ResourceObservation,
    ResourceRegistry,
)
from surya_sync.safety.rules import SafetyPriority, SafetyRule
from surya_sync.safety.validator import SafetyValidator
from surya_sync.scheduler.base import (
    ConstraintStatus,
    DecisionExplanation,
    FallbackChain,
    ReasonCode,
    Scheduler,
    SchedulerTier,
    SchedulingPlan,
    SchedulingRequest,
    SolverStatus,
)


# --- abstractness -------------------------------------------------------


@pytest.mark.parametrize(
    "cls", [Scheduler, FlexibleResource, HardwareInterface, SafetyRule]
)
def test_interfaces_cannot_be_instantiated(cls):
    with pytest.raises(TypeError):
        cls()  # type: ignore[abstract]


def test_scheduler_interface_is_a_single_method():
    """All six schedulers share one entry point. If this grows, the
    threshold controller and the MPC stop being comparable."""
    abstract = {
        name
        for name, member in inspect.getmembers(Scheduler)
        if getattr(member, "__isabstractmethod__", False)
    }
    assert abstract == {"generate_plan"}


def test_flexible_resource_never_decides_anything():
    """A resource answers questions about physics and constraints. If it
    ever grows a decide/schedule method, the layer boundary has leaked."""
    abstract = {
        name
        for name, member in inspect.getmembers(FlexibleResource)
        if getattr(member, "__isabstractmethod__", False)
    }
    assert abstract == {
        "constraints",
        "observe",
        "rated_power_kw",
        "admissible_actions",
        "predict_trajectory",
        "estimate_flexibility",
    }


def test_esp32_interface_satisfies_the_hardware_contract():
    """The real implementation is a stub, but it must already satisfy the
    interface so Phases 1-10 develop against a shape that will not move."""
    interface = ESP32SerialInterface(port="/dev/null", baud_rate=115200, timeout_s=1.0)
    assert isinstance(interface, HardwareInterface)
    assert interface.is_connected is False
    with pytest.raises(NotImplementedError):
        interface.connect()


# --- hard constraints ---------------------------------------------------


def test_resource_constraints_accept_valid_ordering():
    constraints = ResourceConstraints(
        service_level_critical=0.2, service_level_min=0.3, service_level_max=0.95
    )
    assert constraints.service_level_critical < constraints.service_level_min


@pytest.mark.parametrize(
    "critical,minimum,maximum",
    [
        (0.4, 0.3, 0.95),  # critical above min
        (0.2, 0.95, 0.9),  # min above max
        (-0.1, 0.3, 0.95),  # negative
        (0.2, 0.3, 1.5),  # max above 1
    ],
)
def test_resource_constraints_reject_impossible_bounds(critical, minimum, maximum):
    with pytest.raises(ValueError):
        ResourceConstraints(
            service_level_critical=critical,
            service_level_min=minimum,
            service_level_max=maximum,
        )


# --- safety priority ----------------------------------------------------


def test_safety_priority_order_is_the_documented_one():
    """This ordering is a project invariant. It must not be reordered to
    make a benchmark look better."""
    assert [p.name for p in sorted(SafetyPriority)] == [
        "ELECTRICAL_SAFETY",
        "PUMP_PROTECTION",
        "OVERFLOW_PROTECTION",
        "SENSOR_VALIDITY",
        "CRITICAL_WATER_AVAILABILITY",
        "MANUAL_OVERRIDE",
        "EQUIPMENT_CONSTRAINTS",
        "MPC_SCHEDULING",
        "SOLAR_UTILIZATION",
        "GRID_OPTIMIZATION",
    ]


def test_scheduling_ranks_below_every_safety_rule():
    for priority in SafetyPriority:
        if priority.name in {"SOLAR_UTILIZATION", "GRID_OPTIMIZATION"}:
            continue
        if priority is SafetyPriority.MPC_SCHEDULING:
            continue
        assert priority < SafetyPriority.MPC_SCHEDULING


def test_manual_override_outranks_scheduler_but_not_safety():
    assert SafetyPriority.MANUAL_OVERRIDE < SafetyPriority.MPC_SCHEDULING
    assert SafetyPriority.ELECTRICAL_SAFETY < SafetyPriority.MANUAL_OVERRIDE
    assert SafetyPriority.OVERFLOW_PROTECTION < SafetyPriority.MANUAL_OVERRIDE


def test_validator_sorts_rules_by_priority():
    class Rule(SafetyRule):
        def __init__(self, name, priority):
            self.name = name
            self.priority = priority

        def evaluate(self, state, resource_id):  # pragma: no cover - not called
            raise NotImplementedError

    validator = SafetyValidator(
        (
            Rule("grid", SafetyPriority.GRID_OPTIMIZATION),
            Rule("electrical", SafetyPriority.ELECTRICAL_SAFETY),
            Rule("overflow", SafetyPriority.OVERFLOW_PROTECTION),
        )
    )
    assert [rule.name for rule in validator.rules] == [
        "electrical",
        "overflow",
        "grid",
    ]


# --- fallback chain -----------------------------------------------------


def test_scheduler_tiers_degrade_downward():
    assert SchedulerTier.RISK_AWARE_MPC < SchedulerTier.PREDICTIVE_HEURISTIC
    assert SchedulerTier.PREDICTIVE_HEURISTIC < SchedulerTier.REACTIVE_SOLAR
    assert SchedulerTier.REACTIVE_SOLAR < SchedulerTier.THRESHOLD
    assert SchedulerTier.THRESHOLD < SchedulerTier.LOCAL_SAFE_MODE


def test_fallback_chain_orders_by_tier():
    class Dummy(Scheduler):
        def __init__(self, name, tier):
            self.name = name
            self.algorithm_version = "0.0.0"
            self.tier = tier

        def generate_plan(self, request):  # pragma: no cover - not called
            raise NotImplementedError

    chain = FallbackChain(
        (
            Dummy("threshold", SchedulerTier.THRESHOLD),
            Dummy("mpc", SchedulerTier.RISK_AWARE_MPC),
            Dummy("reactive", SchedulerTier.REACTIVE_SOLAR),
        )
    )
    assert [s.name for s in chain.schedulers] == ["mpc", "reactive", "threshold"]


# --- result types -------------------------------------------------------


def test_scheduling_plan_carries_every_mandated_field():
    """The hard rule lists exactly what a plan must return. A plan missing
    any of these is not auditable."""
    required = {
        "first_action",
        "planned_actions",
        "explanation",
        "objective_value",
        "predicted_states",
        "constraint_status",
        "solver_status",
        "algorithm_version",
    }
    assert required <= set(SchedulingPlan.__dataclass_fields__)


def test_explanation_carries_every_mandated_field():
    required = {
        "decision",
        "reason_code",
        "service_level",
        "solar_surplus_kw",
        "expected_best_time",
        "flexibility_minutes",
    }
    assert required <= set(DecisionExplanation.__dataclass_fields__)


def test_explanation_serializes_for_the_why_screen():
    best = datetime(2026, 8, 17, 11, 30)
    explanation = DecisionExplanation(
        decision=ControlAction.WAIT,
        reason_code=ReasonCode.AWAITING_SOLAR,
        service_level=0.47,
        flexibility_minutes=180.0,
        solar_surplus_kw=0.2,
        expected_best_time=best,
    )
    payload = explanation.to_dict()
    assert payload["decision"] == "WAIT"
    assert payload["reason_code"] == "awaiting_solar"
    assert payload["expected_best_time"] == best.isoformat()


def test_constraint_status_defaults_to_no_violations():
    status = ConstraintStatus(satisfied=True)
    assert status.violations == ()


def test_infeasible_is_a_distinct_solver_status():
    """Infeasibility must trigger fallback, never a relaxed re-solve."""
    assert SolverStatus.INFEASIBLE is not SolverStatus.ERROR
    assert SolverStatus.NOT_APPLICABLE.value == "not_applicable"


def test_action_space_is_exactly_run_wait_stop():
    assert {a.value for a in ControlAction} == {"RUN", "WAIT", "STOP"}


def test_provenance_labels_are_the_documented_five():
    assert {p.value for p in Provenance} == {
        "measured",
        "simulated",
        "predicted",
        "estimated",
        "derived",
    }


# --- request assembly ---------------------------------------------------


def test_scheduling_request_needs_only_an_observation():
    """The threshold controller reads nothing but the observation. If
    forecasts became required, the tiers would stop being interchangeable."""
    now = datetime(2026, 8, 17, 9, 0)

    class Tank(FlexibleResource):
        resource_id = "tank_1"
        resource_type = None  # type: ignore[assignment]
        model_version = "0.0.0"

        def constraints(self):
            raise NotImplementedError

        def observe(self):
            raise NotImplementedError

        def rated_power_kw(self):
            return 0.75

        def admissible_actions(self, observation, now):
            raise NotImplementedError

        def predict_trajectory(self, observation, actions, demand_forecast, step_minutes):
            raise NotImplementedError

        def estimate_flexibility(self, observation, demand_forecast):
            raise NotImplementedError

    request = SchedulingRequest(
        now=now,
        horizon=Horizon(start=now, length_minutes=720.0, step_minutes=15.0),
        resource=Tank(),
        observation=ResourceObservation(
            timestamp=now,
            resource_id="tank_1",
            service_level=0.47,
            native_value=470.0,
            native_unit="L",
            actuator_on=False,
            provenance=Provenance.SIMULATED,
        ),
    )
    assert request.demand_forecast is None
    assert request.anomaly_flagged is False
    assert request.horizon.n_steps == 48


def test_horizon_step_count():
    now = datetime(2026, 8, 17, 6, 0)
    assert Horizon(start=now, length_minutes=60.0, step_minutes=15.0).n_steps == 4


def test_flexibility_runway_exceeds_planning_slack():
    """time_to_critical is always the longer runway — it measures distance
    to service failure, not to the planning floor."""
    estimate = FlexibilityEstimate(
        timestamp=datetime(2026, 8, 17, 9, 0),
        resource_id="tank_1",
        flexibility_minutes=120.0,
        time_to_critical_minutes=210.0,
        must_run_by=datetime(2026, 8, 17, 11, 0),
    )
    assert estimate.time_to_critical_minutes >= estimate.flexibility_minutes
    assert estimate.provenance is Provenance.ESTIMATED


def test_forecast_band_absence_is_detectable():
    """Schedulers must not treat a bandless forecast as certain."""
    from surya_sync.domain import ForecastPoint

    now = datetime(2026, 8, 17, 9, 0)
    point = ForecastPoint(target_time=now + timedelta(minutes=15), p50=1.2)
    assert point.has_band is False

    banded = ForecastPoint(
        target_time=now + timedelta(minutes=15), p50=1.2, p10=0.8, p90=1.9
    )
    assert banded.has_band is True


def test_resource_registry_lookup():
    registry = ResourceRegistry()
    with pytest.raises(KeyError):
        registry.get("tank_1")


def test_forecast_is_stamped_as_predicted():
    forecast = Forecast(
        target="pv_generation_kw",
        issued_at=datetime(2026, 8, 17, 9, 0),
        points=(),
        model_name="persistence",
        model_version="0.1.0",
    )
    assert forecast.provenance is Provenance.PREDICTED
