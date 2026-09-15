"""Interfaces — Phase 0 exit criterion: interfaces are stubbed.

These tests guard the *contracts*, not behaviour. They exist so that a
later phase cannot quietly weaken an invariant the project depends on:
one shared scheduler interface, hard constraints that stay hard, and a
fixed safety priority order.
"""

from __future__ import annotations

import ast
import inspect
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from surya_sync.domain import ControlAction, Forecast, Horizon, Provenance
from surya_sync.hardware.esp32_serial import ESP32SerialInterface
from surya_sync.hardware.interfaces import HardwareInterface
from surya_sync.models.generic_resource import (
    ActuationHistory,
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
        "ACTUATOR_PROTECTION",
        "OVERFLOW_PROTECTION",
        "SENSOR_VALIDITY",
        "CRITICAL_SERVICE_AVAILABILITY",
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
    assert SchedulerTier.SAFETY_LAYER < SchedulerTier.RISK_AWARE_MPC
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

        def admissible_actions(self, observation, history, now):
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
    assert request.actuation_history is None
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


def test_equipment_constraints_need_more_than_a_boolean():
    """``observation.actuator_on`` cannot answer min-off or max-starts, so
    ``admissible_actions`` takes the timing separately. Without this the
    resource would have to keep scheduling state, which its contract
    forbids."""
    params = inspect.signature(FlexibleResource.admissible_actions).parameters
    assert list(params) == ["self", "observation", "history", "now"]


def test_unknown_actuation_timing_is_not_elapsed_time():
    """The failure direction matters: unknown timing must read as "constraint
    not yet satisfied", never as a large elapsed time that would let a pump
    short-cycle."""
    now = datetime(2026, 8, 17, 9, 0)
    unknown = ActuationHistory.unknown("tank_1", actuator_on=True)

    assert unknown.minutes_in_state(now) is None
    assert unknown.starts_today == 0
    assert unknown.last_start_at is None

    known = ActuationHistory(
        resource_id="tank_1",
        actuator_on=True,
        changed_at=datetime(2026, 8, 17, 8, 45),
        starts_today=3,
    )
    assert known.minutes_in_state(now) == 15.0


def test_actuation_history_is_derived_not_measured():
    """It is computed from the actuation log, not read off a sensor."""
    history = ActuationHistory.unknown("tank_1", actuator_on=False)
    assert history.provenance is Provenance.DERIVED


def test_schedulers_are_told_not_to_re_read_the_resource():
    """Gap 2: a second ``observe()`` mid-cycle would justify the plan against
    a state that was never acted on, and would break replay. The prohibition
    is unenforceable at runtime, so it lives in the contract docstring — this
    test stops it being silently deleted."""
    contract = Scheduler.__doc__ or ""
    assert "observe()" in contract
    assert "only" in contract


# --- architectural direction --------------------------------------------

REAL_LAYERS = ("models", "scheduler", "safety", "state", "temporal", "ml", "storage")
"""Packages that will run on the Pi against real hardware."""


def _imported_packages(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


@pytest.mark.parametrize("layer", REAL_LAYERS)
def test_production_layers_never_import_the_simulator(layer):
    """The hard rule is that simulated and real resources run the *exact
    same* code. That survives only while the shared physics sits in
    ``models/``: the moment a production layer has to reach into
    ``simulator/`` for it, the fix someone reaches for is a copy — and the
    algorithm has forked without anyone deciding to.

    Phase 1 got this wrong at first. The flexibility maths and the
    trajectory rollout lived in ``simulator/tank.py``, so Phase 11's
    ``RealTankResource`` would have had to import them from there.
    """
    package = Path(__file__).resolve().parent.parent / "surya_sync" / layer
    if not package.exists():
        pytest.skip(f"{layer}/ does not exist yet")

    offenders = {
        source.relative_to(package.parent.parent).as_posix(): sorted(
            module
            for module in _imported_packages(source)
            if module.startswith("surya_sync.simulator")
        )
        for source in package.rglob("*.py")
    }
    offenders = {path: mods for path, mods in offenders.items() if mods}
    assert not offenders, f"production code importing the simulator: {offenders}"


REAL_MODULES = ("control_loop.py", "domain.py", "version.py")
"""Top-level modules that run on the Pi. ``control_loop.py`` matters most:
it is the one loop both the simulator and the ESP32 drive, and a simulator
import there would mean the roof runs different code from the benchmark.

``main.py`` is deliberately absent: it is the composition root, and choosing
which world to run is the one job that belongs there. It gets its own,
stricter rule below."""


@pytest.mark.parametrize("module", REAL_MODULES)
def test_production_modules_never_import_the_simulator(module):
    source = Path(__file__).resolve().parent.parent / "surya_sync" / module
    if not source.exists():
        pytest.skip(f"{module} does not exist yet")
    offenders = sorted(
        name
        for name in _imported_packages(source)
        if name.startswith("surya_sync.simulator")
    )
    assert not offenders, f"{module} imports the simulator: {offenders}"


def test_the_entry_point_imports_the_simulator_only_inside_a_command():
    """``main.py`` may build a simulated run — it is the composition root,
    the one place that picks a concrete world. What it may not do is pull the
    simulator in at module level, because then a Pi in ``mode = real`` loads
    it on every boot and the separation is only nominal.

    So the rule is placement, not absence: every ``surya_sync.simulator``
    import must sit inside a function body, where a real deployment never
    executes it.
    """
    source = Path(__file__).resolve().parent.parent / "surya_sync" / "main.py"
    tree = ast.parse(source.read_text())

    inside_functions = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for inner in ast.walk(node):
                if isinstance(inner, (ast.Import, ast.ImportFrom)):
                    inside_functions.add(id(inner))

    module_level = sorted(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.startswith("surya_sync.simulator")
        and id(node) not in inside_functions
    )
    assert not module_level, (
        "main.py imports the simulator at module level: "
        f"{module_level}. Move it inside the command that needs it."
    )


def test_the_shared_tank_physics_lives_in_models():
    """Both resource adapters must be able to reach these without touching
    ``simulator/``. Named explicitly so a later move is a deliberate act."""
    from surya_sync.models import flexibility, tank

    for name in (
        "tank_constraints",
        "violates_hard_constraint",
        "require_demand_forecast",
        "predict_tank_trajectory",
    ):
        assert callable(getattr(tank, name)), name
    for name in ("estimate_tank_flexibility", "minutes_until_below", "latest_safe_start"):
        assert callable(getattr(flexibility, name)), name
