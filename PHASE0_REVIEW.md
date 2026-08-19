# Phase 0 Review Packet

Prepared 2026-08-17 for review of branch `phase-0-architecture`.

> **Status.** Reviewed 2026-08-18. The commit message was amended per
> section 1 and the branch fast-forwarded onto `main` as `2580cc1` — the
> hash reviewed here, `5a3c4ce`, no longer exists. Sections 1-3 describe
> the tree at `2580cc1`, which is byte-identical; only the message changed.
> Section 4 was worked on 2026-08-19; see section 5 for what changed. The
> verbatim listings in section 2 predate those changes.

> **Commit message discrepancy — RESOLVED 2026-08-18.** The message read
> *"architecture, interfaces, tank model, scheduler contract"* — but **there is
> no tank model in this commit**. Architecture, interfaces and the scheduler
> contract are all present. Amended before merge; the message now states that
> `models/tank.py` is a stub and the tank model is Phase 1 scope. See section 1.

---

## 1. `models/tank.py` — NOT IMPLEMENTED

`surya_sync/models/tank.py` exists as a 4-line stub. Full contents:

```python
"""Overhead water tank physical model (level <-> volume, mass balance).

Phase 1 stub. No implementation yet — see ROADMAP.md.
"""
```

**`WaterTankPumpResource` does not exist.** Verified by grep across all `.py`
files — no `WaterTankPumpResource`, and no class matching `*TankResource`
anywhere in the tree:

```
$ grep -rn "WaterTankPumpResource\|class.*TankResource" --include=*.py .
NOT FOUND
```

### Is this an open Phase 0 item?

**No — it is correctly Phase 0 scope, and the roadmap agrees.** Recording the
reasoning so tomorrow's review doesn't relitigate it:

- ROADMAP Phase 0 exit criteria are: *directory tree exists, interfaces are
  stubbed, `config/` loads, SQLite schema created, **no logic yet***. A tank
  physical model is logic.
- ROADMAP Phase 1 explicitly owns it: *"Tank simulator, pump model... Exit:
  `SimulatedTankResource` runs a multi-day trajectory deterministically; unit
  tests for tank conversion + physical model + pump model pass."*
- The abstraction it must satisfy — `FlexibleResource` — **is** delivered here
  (`models/generic_resource.py`), which is the actual Phase 0 obligation.

**The one genuine gap is the commit message, not the code.** The commit claims a
tank model it does not contain.

### Consequence for the test suite

Zero tests exercise physical modelling, because there is no physics to exercise
(see section 3). The suite validates contracts and refuses bad input; it does
not yet validate any mass balance, level↔volume conversion, or pump flow.

---

## 2. Scheduler contract — verbatim definitions

Source: [`surya_sync/scheduler/base.py`](surya_sync/scheduler/base.py)

### 2.1 `SchedulingRequest`

Everything a scheduler is allowed to look at. Passing one object rather than a
long argument list is what keeps the interface identical across all six
schedulers as inputs grow through the roadmap. A simple scheduler ignores the
fields it doesn't need — the threshold controller reads only `observation`.

```python
@dataclass(frozen=True, slots=True)
class SchedulingRequest:
    now: datetime
    horizon: Horizon
    resource: FlexibleResource
    observation: ResourceObservation
    demand_forecast: Forecast | None = None
    solar_forecast: Forecast | None = None
    base_load_forecast: Forecast | None = None
    flexibility: FlexibilityEstimate | None = None
    solar_surplus_kw: float | None = None
    """Current instantaneous surplus. Distinct from ``solar_forecast`` —
    the reactive scheduler uses only this, the predictive ones only that."""

    anomaly_flagged: bool = False
    """Set by ``ml/anomaly``. Schedulers must respond by widening safety
    margins, not by refusing to decide."""

    provenance: Provenance = Provenance.MEASURED
    """Whether the inputs came from hardware or the simulator."""

    metadata: dict[str, Any] = field(default_factory=dict)
```

### 2.2 `SchedulingPlan`

The complete, auditable output of one scheduling cycle. Every field is mandated
by the project's hard rules — a scheduler that cannot fill one in must say so
explicitly (`None`, `NOT_APPLICABLE`) rather than omit it.

```python
@dataclass(frozen=True, slots=True)
class SchedulingPlan:
    first_action: ControlAction
    """The only action that will actually be executed this cycle."""

    planned_actions: tuple[PlannedAction, ...]
    explanation: DecisionExplanation
    objective_value: float | None
    """``None`` for rule-based schedulers, which optimize nothing."""

    predicted_states: tuple[PredictedState, ...]
    constraint_status: ConstraintStatus
    solver_status: SolverStatus
    algorithm_version: str
    scheduler_name: str
    tier: SchedulerTier
    computed_at: datetime
    compute_ms: float | None = None
    """Measured runtime. Phase 12 checks this against the Pi Zero budget."""
```

### 2.3 `ReasonCode`

The backbone of the "Why?" screen. Deliberately a **closed vocabulary**: every
decision maps to exactly one code, so the UI can render plain language and
experiments can count decision types. Extend it when a genuinely new rationale
appears — never fall back to a free-text catch-all.

```python
class ReasonCode(str, Enum):
    # --- reasons to RUN ---
    CRITICAL_LEVEL = "critical_level"
    """Service level at or near the hard floor. Runs regardless of solar."""

    SOLAR_SURPLUS_AVAILABLE = "solar_surplus_available"
    """Running now is powered by surplus PV generation."""

    OPTIMAL_WINDOW_NOW = "optimal_window_now"
    """MPC's horizon says this step is the cheapest feasible one."""

    NO_BETTER_WINDOW_AHEAD = "no_better_window_ahead"
    """Deferring would only make things worse within the horizon."""

    MUST_RUN_DEADLINE = "must_run_deadline"
    """Latest feasible start time reached; deferring breaches a constraint."""

    # --- reasons to WAIT ---
    SUFFICIENT_LEVEL = "sufficient_level"
    """Service level comfortably above target; no need to act."""

    AWAITING_SOLAR = "awaiting_solar"
    """Flexibility exists and a better-lit window is forecast. The core
    value proposition of the whole system, in one reason code."""

    DEMAND_LOW = "demand_low"
    """Forecast demand does not justify actuation yet."""

    EQUIPMENT_COOLDOWN = "equipment_cooldown"
    """min-off time or daily start limit blocks actuation."""

    # --- reasons to STOP ---
    TARGET_REACHED = "target_reached"
    SOLAR_SURPLUS_ENDED = "solar_surplus_ended"

    # --- overrides and degraded operation ---
    SAFETY_OVERRIDE = "safety_override"
    """The safety layer replaced the scheduler's action. Always logged."""

    MANUAL_OVERRIDE = "manual_override"
    SENSOR_INVALID = "sensor_invalid"
    ANOMALY_DETECTED = "anomaly_detected"
    """Behaviour outside learned norms; scheduling turns conservative."""

    FALLBACK_ENGAGED = "fallback_engaged"
    """A higher tier failed and a lower tier produced this decision."""
```

16 codes: 5 RUN, 4 WAIT, 2 STOP, 5 override/degraded.

---

## 3. Test breakdown — 72 tests

```
Category                      Count    Share
─────────────────────────────────────────────
Constraint validation            18    25.0%
Config rejection                 19    26.4%
Schema versioning                 6     8.3%
Physical model                    0     0.0%   ← nothing to test yet
Serialization / plumbing         29    40.3%
─────────────────────────────────────────────
Total                            72   100.0%
```

### Constraint validation — 18

Invariants that must hold, at both the Python and SQLite layers.

| Test | n |
|---|---|
| `test_resource_constraints_accept_valid_ordering` | 1 |
| `test_resource_constraints_reject_impossible_bounds[...]` | 4 |
| `test_safety_priority_order_is_the_documented_one` | 1 |
| `test_scheduling_ranks_below_every_safety_rule` | 1 |
| `test_manual_override_outranks_scheduler_but_not_safety` | 1 |
| `test_validator_sorts_rules_by_priority` | 1 |
| `test_scheduler_tiers_degrade_downward` | 1 |
| `test_fallback_chain_orders_by_tier` | 1 |
| `test_constraint_status_defaults_to_no_violations` | 1 |
| `test_infeasible_is_a_distinct_solver_status` | 1 |
| `test_flexibility_runway_exceeds_planning_slack` | 1 |
| `test_foreign_keys_are_enforced` | 1 |
| `test_provenance_is_constrained` | 1 |
| `test_run_mode_is_constrained` | 1 |
| `test_control_action_is_constrained` | 1 |

The last four assert DB-level `CHECK`/`FOREIGN KEY` enforcement — they live in
`test_storage_schema.py` but test constraints, not schema versioning.

### Config rejection — 19

Every one asserts a `ConfigError` is raised. A typo'd physical parameter must
never silently fall back to a default.

| Test | n |
|---|---|
| `test_missing_file_is_fatal` | 1 |
| `test_unknown_section_rejected` | 1 |
| `test_unknown_key_rejected` | 1 |
| `test_invalid_run_mode_rejected` | 1 |
| `test_section_validation[...]` (tank 3, pump 2, solar 1, scheduler 3, hardware 1, storage 1, logging 1) | 12 |
| `test_ordering_of_tank_levels_is_enforced` | 1 |
| `test_cross_section_validation_pump_run_must_fit_horizon` | 1 |
| `test_cross_section_validation_reserve_must_leave_operating_band` | 1 |

`test_ordering_of_tank_levels_is_enforced` is a boundary call — it guards the
`critical < min < max` hard invariant but does so via config, so it is counted
here rather than under constraint validation.

### Schema versioning — 6

| Test | n |
|---|---|
| `test_schema_creates_all_tables` (asserts exactly 19 tables) | 1 |
| `test_schema_version_recorded` | 1 |
| `test_initialize_is_idempotent` | 1 |
| `test_uninitialized_database_reports_no_version` | 1 |
| `test_mismatched_schema_version_is_fatal` | 1 |
| `test_database_creates_parent_directories` | 1 |

### Physical model — 0

**No test in this suite exercises a physical model, because none exists.** See
section 1.

The closest thing to physical computation currently under test is arithmetic on
derived quantities, counted under serialization/plumbing:

- `test_surplus_is_generation_minus_base_load` — `pv − base_load`
- `test_surplus_never_negative` — clamp at 0
- `test_surplus_unknown_when_inputs_missing` — `None` propagation
- `test_horizon_step_count` — floor division

None of these is a mass balance, a level↔volume conversion, or a pump flow
model. **Phase 1 must close this to zero-no-longer**, and its exit criteria
already require exactly that.

### Serialization / plumbing — 29

Contract shape, enum vocabularies, dataclass field presence, abstractness,
config loading happy paths, state bookkeeping.

| Group | Tests | n |
|---|---|---|
| Config happy path | `test_packaged_defaults_load`, `test_default_toml_is_shipped_with_the_package`, `test_partial_config_uses_defaults_for_absent_sections`, `test_run_mode_parsed_to_enum`, `test_config_hash_is_stable_and_sensitive`, `test_config_is_frozen` | 6 |
| Abstractness / contract shape | `test_interfaces_cannot_be_instantiated[Scheduler\|FlexibleResource\|HardwareInterface\|SafetyRule]`, `test_scheduler_interface_is_a_single_method`, `test_flexible_resource_never_decides_anything`, `test_esp32_interface_satisfies_the_hardware_contract` | 7 |
| Mandated fields / vocabularies | `test_scheduling_plan_carries_every_mandated_field`, `test_explanation_carries_every_mandated_field`, `test_explanation_serializes_for_the_why_screen`, `test_action_space_is_exactly_run_wait_stop`, `test_provenance_labels_are_the_documented_five`, `test_scheduling_request_needs_only_an_observation`, `test_horizon_step_count`, `test_forecast_band_absence_is_detectable`, `test_resource_registry_lookup`, `test_forecast_is_stamped_as_predicted` | 10 |
| Actuation + electrical state | `test_actuation_tracks_three_independent_states`, `test_unknown_state_is_not_off`, `test_divergence_is_detectable`, `test_surplus_is_generation_minus_base_load`, `test_surplus_never_negative`, `test_surplus_unknown_when_inputs_missing` | 6 |

### Honest read on the shape of this suite

40% plumbing and 0% physics is the correct profile for a phase whose exit
criteria are *"interfaces stubbed, no logic yet"* — but it means **the suite
currently proves the architecture is coherent, not that anything works.** Its
value is as a ratchet: `test_scheduler_interface_is_a_single_method` and
`test_safety_priority_order_is_the_documented_one` will fail loudly if a later
phase quietly weakens an invariant.

---

## 4. Review checklist for tomorrow

- [x] Amend the commit message — it claims a tank model that isn't there
- [x] `SchedulingRequest` field set — two gaps found and closed, see 5.1 and 5.2
- [x] `ReasonCode` closed vocabulary — `FALLBACK_ENGAGED` removed, see 5.3
- [x] `service_level` normalization as the generalization seam — **reviewed and
      deliberately left as is**, see 5.6
- [x] Safety priority `IntEnum` ordering matches CLAUDE.md exactly — it does;
      two rule names generalized, see 5.5
- [x] SQLite schema v1 — 19 tables and provenance `CHECK`s confirmed; two
      missing `CHECK`s added, see 5.4
- [x] Decide whether `phase-0-architecture` fast-forwards onto `main` — yes, done (`2580cc1`)

---

## 5. Review outcome, 2026-08-19

Five findings were acted on and one was declined. Tests went 72 -> 81.

### 5.1 `admissible_actions` could not enforce the constraints it owns

`admissible_actions(observation, now)` is the gate for `min_on_minutes`,
`min_off_minutes` and `max_starts_per_day`. It could not evaluate any of them:
`ResourceObservation.actuator_on` is a bare boolean, carrying neither elapsed
time nor a start count, and `FlexibleResource` is contractually stateless with
respect to scheduling, so the resource could not hold the history either.

The gap would have surfaced in Phase 1, when `SimulatedTankResource` first has
to implement the method. It is also the exact boolean-collapse the project's
actuation rule warns about.

**Closed by** a new `ActuationHistory` in `models/generic_resource.py`
(`changed_at`, `starts_today`, `last_start_at`, `minutes_in_state()`), threaded
through `admissible_actions(observation, history, now)` and carried on
`SchedulingRequest.actuation_history`.

`None` means *unknown*, never *zero*: an implementation must read unknown as
"constraint not yet satisfied" and hold the actuator. Refusing to switch is
recoverable; short-cycling a pump is not.

### 5.2 A scheduler could re-read the world mid-decision

`SchedulingRequest.resource` is a live object, so a scheduler could call
`resource.observe()` and receive a state different from `request.observation`.
The plan would then be justified against a state that was never acted on, and
would not be reproducible from its logged inputs. The same field is what stops
a request being serializable, which `RunMode.REPLAY` depends on.

**Closed by** documenting `resource` as physics-only, stating the prohibition
in the `Scheduler` contract, and recording that replay rebuilds the resource
from config plus `observation.resource_id`. The rule cannot be enforced at
runtime, so a test asserts it stays in the contract docstring.

### 5.3 `FALLBACK_ENGAGED` was a reason code that gave no reason

If the MPC fails and the threshold controller runs because the tank is low, the
reason is `CRITICAL_LEVEL`. Recording `FALLBACK_ENGAGED` discarded it — and the
fallback was already recorded twice, by `SchedulingPlan.tier` and by the
`scheduler_decisions.fallback_engaged` column.

**Closed by** removing the code and adding `SchedulingPlan.fallback_engaged`.
A decision now carries both what happened and why. The vocabulary is 15 codes:
5 RUN, 4 WAIT, 2 STOP, 4 override/degraded.

### 5.4 The closed vocabulary was not closed at the database

`first_action` was `CHECK`-constrained but `reason_code` and `solver_status`
were bare `TEXT`, so the decision log would have accepted any string.

**Closed by** adding both `CHECK` lists, plus tests that compare them against
`ReasonCode` and `SolverStatus` so the two cannot drift apart.

Schema v1 was edited in place rather than bumped to v2: no deployment exists,
and the only database held a single `schema_version` row. A migration protects
real data, and there was none to protect.

### 5.5 Two safety rules were named after water

`PUMP_PROTECTION` and `CRITICAL_WATER_AVAILABILITY` were tank-specific names
inside the layer that is meant to be resource-agnostic — and it is the layer a
reviewer reads first.

**Closed by** renaming to `ACTUATOR_PROTECTION` and
`CRITICAL_SERVICE_AVAILABILITY`, with docstrings naming the tank meaning. The
priority *ordering* was verified against `CLAUDE.md` and matches exactly; only
the labels changed.

### 5.6 Declined: no `deadline` or `interruptible` on `ResourceConstraints`

`service_level` normalizes cleanly for the tank but does not carry two things
other resource types need: a user deadline ("charged by 08:00") and whether a
run can be interrupted (a wash cycle cannot; a tank fill can).

**Deliberately not added.** The water tank is the actual scope; the other
resource types exist to keep the interfaces honest, not to be delivered. Adding
fields nothing implements would cost review attention and buy nothing testable.

Recorded here so it is a known limit rather than an oversight. Both are additive
fields with safe defaults, so Phase 16 can add them without reshaping anything.

### 5.7 No change: `SchedulingRequest.provenance`

Flagged as a possible category error against `RunMode`. On inspection it is
correct: `RunMode` has three values, `scheduler_decisions.provenance` accepts
two, and a replay run has no distinct answer of its own. The field labels where
the numbers came from, which is what the column stores. Docstring clarified;
type unchanged.
