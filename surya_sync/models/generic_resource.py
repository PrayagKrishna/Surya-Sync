"""The ``FlexibleResource`` interface.

This is the abstraction that makes SuryaSync a *load-flexibility framework*
rather than a water-pump controller. The tank is the first implementation,
not the scope.

The central idea: a household does not want "the pump to run", it wants a
**service level** maintained. The gap between the service level required
now and the service level the household will actually need later is
temporal flexibility, and that is what the scheduler spends.

Every resource type expresses itself in the same terms:

===================  ==============================  ====================
Resource             service_level means             actuation means
===================  ==============================  ====================
Water tank           fraction of usable volume       pump ON
Water heater         normalized stored heat          element ON
EV charger           state of charge                 charging
Washing machine      fraction of cycle complete      cycle running
===================  ==============================  ====================

Because they all speak this language, ``scheduler/`` never needs to know
which resource it is scheduling. Phase 16's exit criterion is exactly
this: a second resource type runs through the scheduler with zero changes
under ``scheduler/``.

Phase 0: interface only. Implementations land in Phase 1 (simulated) and
Phase 11 (real).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from surya_sync.domain import (
    ControlAction,
    Forecast,
    Provenance,
    ResourceType,
)


@dataclass(frozen=True, slots=True)
class ResourceObservation:
    """The state of a resource at one instant.

    ``service_level`` is the normalized (0..1) quantity the scheduler
    reasons about. ``native_value`` carries the same state in physical
    units so that logs, the UI and the physical model stay interpretable.
    """

    timestamp: datetime
    resource_id: str
    service_level: float
    native_value: float
    native_unit: str
    actuator_on: bool
    provenance: Provenance
    sensor_valid: bool = True
    """False when the sensor reading failed validation. A resource with an
    invalid sensor must not be scheduled optimistically — the safety layer
    catches this before the scheduler runs."""


@dataclass(frozen=True, slots=True)
class ResourceConstraints:
    """Operating limits of a resource.

    The service-level bounds are **hard constraints**. They are never
    relaxed into penalty terms in the objective function — an optimizer
    that cannot satisfy them must report infeasibility and hand over to
    the fallback chain.
    """

    service_level_critical: float
    """Absolute floor. Below this the household service has failed."""

    service_level_min: float
    """Target floor. The scheduler plans to stay above this; the margin
    between it and ``service_level_critical`` absorbs forecast error."""

    service_level_max: float
    """Ceiling (overflow / overcharge protection)."""

    min_on_minutes: float = 0.0
    min_off_minutes: float = 0.0
    max_starts_per_day: int | None = None
    """Equipment protection: limits actuator cycling."""

    def __post_init__(self) -> None:
        if not 0.0 <= self.service_level_critical <= self.service_level_min:
            raise ValueError(
                "require 0 <= service_level_critical <= service_level_min, got "
                f"{self.service_level_critical} / {self.service_level_min}"
            )
        if not self.service_level_min < self.service_level_max <= 1.0:
            raise ValueError(
                "require service_level_min < service_level_max <= 1.0, got "
                f"{self.service_level_min} / {self.service_level_max}"
            )


@dataclass(frozen=True, slots=True)
class PredictedState:
    """One step of a predicted resource trajectory.

    Returned by ``predict_trajectory`` and surfaced in the scheduling plan
    so that a decision can be audited against what the model expected —
    and, in Phase 13, against what physically happened.
    """

    timestamp: datetime
    service_level: float
    native_value: float
    actuator_on: bool
    violates_hard_constraint: bool = False
    provenance: Provenance = Provenance.PREDICTED


@dataclass(frozen=True, slots=True)
class FlexibilityEstimate:
    """How much temporal slack the resource currently has.

    ``flexibility_minutes`` is the headline number the "Why?" screen
    renders: *how long actuation can be deferred before a hard constraint
    would be violated, given the demand forecast*.
    """

    timestamp: datetime
    resource_id: str
    flexibility_minutes: float
    """Time until ``service_level_min`` would be breached with no actuation."""

    time_to_critical_minutes: float
    """Time until ``service_level_critical`` would be breached. Always
    >= ``flexibility_minutes``. This is the true safety runway."""

    must_run_by: datetime | None
    """Latest start time that still satisfies the constraint, accounting
    for how long actuation takes to have effect. ``None`` if unconstrained
    within the horizon."""

    provenance: Provenance = Provenance.ESTIMATED


class FlexibleResource(ABC):
    """A controllable load the scheduler can shift in time.

    Implementations must be **stateless with respect to scheduling**: all
    scheduling state lives in ``state/``. A resource answers questions
    about physics and constraints; it never decides anything.

    Simulated and real implementations of the same resource type share
    this interface exactly, so the scheduling code path is identical in
    simulation and on hardware. Never fork the algorithm per environment.
    """

    resource_id: str
    resource_type: ResourceType
    model_version: str

    @abstractmethod
    def constraints(self) -> ResourceConstraints:
        """Return the resource's hard and equipment constraints."""

    @abstractmethod
    def observe(self) -> ResourceObservation:
        """Return the current state.

        For real resources this reads the latest validated telemetry; for
        simulated ones it reads the simulator's internal state. Neither
        blocks on hardware I/O — the control loop owns timing.
        """

    @abstractmethod
    def rated_power_kw(self) -> float:
        """Electrical power drawn while the actuator is energized."""

    @abstractmethod
    def admissible_actions(
        self, observation: ResourceObservation, now: datetime
    ) -> tuple[ControlAction, ...]:
        """Actions permitted by equipment constraints right now.

        Enforces min-on / min-off / max-starts. The safety layer and the
        optimizer both consult this; neither may propose an action outside
        the returned set.
        """

    @abstractmethod
    def predict_trajectory(
        self,
        observation: ResourceObservation,
        actions: tuple[ControlAction, ...],
        demand_forecast: Forecast,
        step_minutes: float,
    ) -> tuple[PredictedState, ...]:
        """Simulate forward under a candidate action sequence.

        This is the physical model the optimizer searches over. It must be
        deterministic: identical inputs produce identical trajectories, or
        experiments stop being reproducible.
        """

    @abstractmethod
    def estimate_flexibility(
        self,
        observation: ResourceObservation,
        demand_forecast: Forecast,
    ) -> FlexibilityEstimate:
        """Quantify how long actuation can be deferred.

        Conservative by construction: uses the P90 demand band when the
        forecast carries one, so flexibility is under- rather than
        over-stated.
        """


@dataclass(frozen=True, slots=True)
class ResourceRegistry:
    """The set of resources under SuryaSync's control.

    Phase 0 ships single-resource operation. The registry exists now so
    that multi-resource coordination in Phase 16 is an extension rather
    than a rewrite of the scheduler's inputs.
    """

    resources: tuple[FlexibleResource, ...] = field(default_factory=tuple)

    def get(self, resource_id: str) -> FlexibleResource:
        for resource in self.resources:
            if resource.resource_id == resource_id:
                return resource
        raise KeyError(f"unknown resource_id: {resource_id!r}")
