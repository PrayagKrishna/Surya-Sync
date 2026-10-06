# Graph Report - Surya-Sync  (2026-10-06)

## Corpus Check
- 109 files · ~75,065 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 7 file(s) not represented in the graph (top: (none) 4, .csv 1, .toml 1)

## Summary
- 1799 nodes · 4966 edges · 123 communities (75 shown, 48 thin omitted)
- Extraction: 81% EXTRACTED · 19% INFERRED · 0% AMBIGUOUS · INFERRED: 964 edges (avg confidence: 0.95)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `bd44a003`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- ConfigError
- test_profiles.py
- ControlAction
- Config
- Forecast
- ThresholdScheduler
- test_demand_models.py
- test_reactive_vs_threshold_scenarios.py
- test_control_loop.py
- test_threshold_scheduler.py
- StateManager
- test_reactive_scheduler.py
- ClearSkyProfile
- PumpModel
- test_safety.py
- flexibility.py
- train_demand_model
- TankSimulator
- SimulatedTankResource
- context.py
- main.py
- generic_resource.py
- ResourceRegistry
- ResourceObservation
- Constraint validation — 18
- SystemState
- TimedValue
- run_standard_set
- test_temporal.py
- SafetyValidator
- demand.py
- test_tank_model.py
- FlexibleResource
- ScenarioRun
- datetime
- test_storage_schema.py
- HardwareInterface
- TankModel
- SlotProfile
- DiurnalBaseLoadProfile
- test_interfaces.py
- ActuationHistory
- Provenance
- test_solar_forecast.py
- RunMode
- test_repositories.py
- ConstantDemandProfile
- build_slot_profile
- ReasonCode
- test_scheduling_request_needs_only_an_observation
- SuryaSync — Project Memory
- system_state.py
- SuryaSync
- test_simulator.py
- ClearSkyModel
- domain.py
- EnergySplit
- _imported_packages
- config
- run_scenario
- 3. Test breakdown — 72 tests
- TankConfig
- test_demand_dataset.py
- CycleDecision
- demand/dataset.py
- ActuationState
- ControlCycle
- rules.py
- test_the_threshold_controller_never_violates_a_hard_constraint
- analytics/__init__.py
- metrics.py
- app.py
- api/__init__.py
- config/__init__.py
- ablation.py
- experiments/comparison.py
- experiments/__init__.py
- hardware/__init__.py
- detector.py
- anomaly/__init__.py
- base_load/__init__.py
- base_load/models.py
- features/__init__.py
- ml/__init__.py
- FeatureVector
- solar/__init__.py
- solar/models.py
- uncertainty/__init__.py
- quantiles.py
- residuals.py
- models/__init__.py
- safety/__init__.py
- fixed_time.py
- heuristic.py
- scheduler/__init__.py
- mpc.py
- optimizer.py
- simulator/__init__.py
- state/__init__.py
- adaptation.py
- temporal/__init__.py
- SimulationStep
- earns_its_place
- ComparisonRow
- test_demand_is_served_before_the_ceiling_is_applied
- my-agent
- surya-sync
- ResourceType
- runs
- .to_dict
- test_the_run_is_deterministic
- test_the_controller_actually_ran_the_pump
- test_the_pump_stays_within_its_daily_start_budget
- test_the_energy_split_accounts_for_every_kwh
- test_results_are_labelled_simulated
- test_the_daily_start_budget_yields_to_critical_service
- test_a_full_tank_overflows_and_reports_the_spill
- test_volume_never_leaves_the_physical_range
- test_sensor_reads_the_offset_when_full

## God Nodes (most connected - your core abstractions)
1. `ControlAction` - 180 edges
2. `Provenance` - 99 edges
3. `Config` - 71 edges
4. `Current phase` - 68 edges
5. `SimulatedTankResource` - 67 edges
6. `ThresholdScheduler` - 58 edges
7. `ResourceObservation` - 55 edges
8. `ActuationHistory` - 55 edges
9. `ReasonCode` - 55 edges
10. `SchedulingRequest` - 52 edges

## Surprising Connections (you probably didn't know these)
- `5.7 No change: `SchedulingRequest.provenance`` --references--> `RunMode`  [INFERRED]
  PHASE0_REVIEW.md → surya_sync/domain.py
- `2.1 `SchedulingRequest`` --references--> `SchedulingRequest`  [INFERRED]
  PHASE0_REVIEW.md → surya_sync/scheduler/base.py
- `2.2 `SchedulingPlan`` --references--> `SchedulingPlan`  [INFERRED]
  PHASE0_REVIEW.md → surya_sync/scheduler/base.py
- `5.2 A scheduler could re-read the world mid-decision` --references--> `Scheduler`  [INFERRED]
  PHASE0_REVIEW.md → surya_sync/scheduler/base.py
- `Constraint validation — 18` --references--> `test_run_mode_is_constrained()`  [INFERRED]
  PHASE0_REVIEW.md → tests/test_storage_schema.py

## Import Cycles
- None detected.

## Communities (123 total, 48 thin omitted)

### Community 0 - "ConfigError"
Cohesion: 0.05
Nodes (61): hashlib, json, Namespace, Config rejection — 19, build_config(), _build_section(), config_hash(), load_config() (+53 more)

### Community 1 - "test_profiles.py"
Cohesion: 0.04
Nodes (45): _split(), DiurnalDemandProfile, A two-peak household day. The hourly weights are interpolated linearly between…, A deterministic value in ``[0, 1)`` derived from two integers. Mixing is the…, A deterministic value in ``[-1, 1)``., signed_noise(), unit_noise(), clear_sky() (+37 more)

### Community 2 - "ControlAction"
Cohesion: 0.11
Nodes (31): ControlAction, The action space exposed to every scheduler. Deliberately minimal — on/off…, build(), Admissibility is a gate in ``safety/`` and the scheduler, not here. A simulator…, No actuation log means no known timing, so no start is permitted — and nothing…, ``min_level`` is where the plan went wrong; ``critical_level`` is where the…, Assuming no demand makes every trajectory look safe. That is the one optimistic…, Regression. ``Forecast`` is a general container, so a PV series is structurally… (+23 more)

### Community 3 - "Config"
Cohesion: 0.11
Nodes (46): Current phase, 2026-08-19 — Phase 0 review: packet recorded and merged — commit `550aed0`, 2026-09-15 — Phase 2: Conventional control and the safety layer — commit `0c8b3c0`, 2026-09-16 — Phase 3 closed: the 3-day benchmark was too short — commit `09fef37`, 2026-09-16 — Phase 3, in progress: reactive solar scheduler — commit `618683c`, 2026-09-17 — Phase 4 closed: temporal feature engineering, 2026-09-18 — Phase 5 closed: demand ML baselines through gradient boosting, 2026-09-18 — Phase 5 hardened: an actuator-timing bug, not an accuracy problem (+38 more)

### Community 4 - "Forecast"
Cohesion: 0.13
Nodes (21): dataclasses, Typed configuration schema. Plain dataclasses, validated at load time. Every…, Forecast, forecast_value_at(), A forecast series for one target, stamped with its producer. Conservative…, The forecast value governing ``moment``, by zero-order hold. Each point governs…, Pump model: delivered flow, energy drawn, and the cycling limits. This module…, predict_tank_trajectory() (+13 more)

### Community 5 - "ThresholdScheduler"
Cohesion: 0.08
Nodes (34): A plan for an action the safety layer compelled outright. Synthesized rather…, PredictedState, One step of a predicted resource trajectory. Returned by ``predict_trajectory``…, ConstraintStatus, DecisionExplanation, PlannedAction, Whether the returned plan respects the hard constraints. A plan with…, Structured rationale for a single decision. Produced by *every* scheduler on… (+26 more)

### Community 6 - "test_demand_models.py"
Cohesion: 0.17
Nodes (16): MeanBaseline, Predicts the training-set mean demand for every input, unconditionally., parametrize, Demand baseline and models — unit level, synthetic feature vectors. Uses hand-…, None entries must not reach sklearn as None/NaN-intolerant input., Pinned: the author's choice, made on measured validation MAE (random forest…, test_each_model_declares_a_distinct_name_and_version(), test_mean_baseline_predicts_the_training_mean_regardless_of_input() (+8 more)

### Community 7 - "test_reactive_vs_threshold_scenarios.py"
Cohesion: 0.17
Nodes (13): aggregate_grid_kwh(), Head-to-head controller comparison under identical conditions. Phase 3's exit…, Total baseline vs. candidate grid energy across every row. Phase 2 recorded…, parametrize, Phase 3 exit criterion, pinned: reactive vs. threshold, extended set. "Beats…, At Phase 2's 3-day duration, ``spike`` and ``sunny`` looked tied to threshold,…, Documents the limit above rather than letting it drift unnoticed: both…, The opportunistic top-up must never make grid draw worse. An earlier, laxer… (+5 more)

### Community 8 - "test_control_loop.py"
Cohesion: 0.12
Nodes (45): FallbackChain, IntEnum, Ordered scheduler tiers with automatic degradation. Tries each scheduler in…, Position in the fallback chain. Lower value = more sophisticated. The household…, SchedulerTier, config(), make_cycle(), make_resource() (+37 more)

### Community 9 - "test_threshold_scheduler.py"
Cohesion: 0.06
Nodes (49): ForecastPoint, Horizon, A single forecast value with its uncertainty band. ``p10``/``p90`` are…, A discretized planning horizon. ``step_minutes`` is the control resolution;…, config(), make_request(), make_resource(), fixture (+41 more)

### Community 10 - "StateManager"
Cohesion: 0.13
Nodes (12): datetime, Record what the scheduler decided. Does not imply execution. Only the…, Record what the hardware says it is doing., Record independently corroborated actuator state., Mark a simulated command as reported and confirmed. In simulation the relay…, Set or clear the user's override. Outranks the scheduler only., Move the state clock forward, never backward. Out-of-order arrivals are normal…, Maintains the current ``SystemState`` and its history. (+4 more)

### Community 11 - "test_reactive_scheduler.py"
Cohesion: 0.11
Nodes (29): config(), make_request(), make_resource(), fixture, The reactive scheduler — Phase 3, Baseline C. What is being asserted is that…, Same as the threshold controller: nothing needs doing, sun or not., The behaviour this tier exists for: free energy is on the table, and the tank…, The ceiling check for the opportunistic top-up must not rely solely on the one-… (+21 more)

### Community 12 - "ClearSkyProfile"
Cohesion: 0.10
Nodes (20): 2026-10-05 — Phase 6 started: solar forecast, real data chosen over simulated clouds — uncommitted, ClearSkyProfile, IntermittentProfile, OvercastProfile, ABC, datetime, Passing clouds: clear sky scaled by a value that changes per slot. This is the…, A PV generation series expressed as a pure function of time. (+12 more)

### Community 13 - "PumpModel"
Cohesion: 0.06
Nodes (37): PumpConfig, PumpModel, datetime, Whether min-off time and the daily start budget both allow a start., Whether min-on time has elapsed., A single-speed pump: it is either delivering rated flow or it is off., Actions the equipment limits permit at ``now``. The action space is asymmetric…, test_too_little_history_raises_rather_than_returning_a_tiny_dataset() (+29 more)

### Community 14 - "test_safety.py"
Cohesion: 0.13
Nodes (33): config(), make_state(), make_validator(), datetime, fixture, The safety layer — Phase 2. Two claims carry these tests: 1. The **priority…, History of an actuator that has held its state long enough to switch., A verdict that was never produced is indistinguishable from a rule that does… (+25 more)

### Community 15 - "flexibility.py"
Cohesion: 0.25
Nodes (9): estimate_tank_flexibility(), latest_safe_start(), minutes_until_below(), datetime, Quantify how long actuation can be deferred without breaching a hard floor.…, Minutes until the level first drops below ``floor_level``. ``None`` when it…, Last moment at which starting the pump still avoids the hard floor. Three…, How long the pump can stay off before a floor is breached. Conservative by… (+1 more)

### Community 16 - "train_demand_model"
Cohesion: 0.12
Nodes (24): collections_abc, Phase 5 exit criterion in executable form. Builds a demand dataset from…, train_demand_model(), chronological_split(), mean_absolute_error(), Chronological validation and error metrics, shared across ML targets. Hard…, Split a chronological sequence into train/val/test by position. Never shuffles:…, root_mean_squared_error() (+16 more)

### Community 17 - "TankSimulator"
Cohesion: 0.09
Nodes (17): 2026-09-15 — Phase 1 audited against `CLAUDE.md` — commit `288ceaf`, 2026-09-15 — Phase 1: Simulator and physical models — commit `1df68b2`, Advance the stored volume by ``minutes``. Inflow and draw are applied over the…, The outcome of advancing the tank by one interval. ``spilled_l`` and…, TankStep, ConstantBaseLoadProfile, GridModel, Attributes generation to loads under the surplus-only rule. (+9 more)

### Community 18 - "SimulatedTankResource"
Cohesion: 0.09
Nodes (19): ``FlexibleResource`` over a ``TankSimulator``. Holds no scheduling state of its…, Delegate to the shared tank physics. See…, SimulatedTankResource, hysteresis_run(), The load-bearing test of the phase. ``predict_trajectory`` is what the…, Conservative by construction: a band must shorten the estimate, never leave it…, A trajectory in which the pump never starts would pass a determinism check…, A control-resolution limit, recorded so it is not later mistaken for a physics… (+11 more)

### Community 19 - "context.py"
Cohesion: 0.10
Nodes (22): math, build_temporal_context(), _days_in_year(), DayType, datetime, Enum, str, Build a temporal context vector for a timestamp. Cyclic encoding only — no… (+14 more)

### Community 20 - "main.py"
Cohesion: 0.09
Nodes (24): argparse, logging, pathlib, re, SolarConfig, SuryaSync entry point. Loads and validates config, initializes the database…, Phase 6 exit criterion in executable form. Scores three baselines (persistence,…, train_solar_model() (+16 more)

### Community 21 - "generic_resource.py"
Cohesion: 0.08
Nodes (21): 2026-08-19 — Phase 0 review: interface fixes — commit `c1567c3`, SchedulerConfig, FlexibilityEstimate, The ``FlexibleResource`` interface. This is the abstraction that makes…, Operating limits of a resource. The service-level bounds are **hard…, How much temporal slack the resource currently has. ``flexibility_minutes`` is…, ResourceConstraints, ABC (+13 more)

### Community 22 - "ResourceRegistry"
Cohesion: 0.16
Nodes (20): The set of resources under SuryaSync's control. Phase 0 ships single-resource…, ResourceRegistry, CriticalServiceRule, default_rules(), EquipmentConstraintRule, OverflowProtectionRule, The service level may not exceed the resource's ceiling. Forces a stop, never a…, Below the hard floor, the resource is replenished regardless of solar. This is… (+12 more)

### Community 23 - "ResourceObservation"
Cohesion: 0.10
Nodes (14): The state of a resource at one instant. ``service_level`` is the normalized…, ResourceObservation, ForecastRepository, MetricsRepository, _observation_from_row(), datetime, Repository interfaces over the SQLite schema. One repository per bounded…, Forecasts and their realized errors. (+6 more)

### Community 24 - "Constraint validation — 18"
Cohesion: 0.15
Nodes (11): Constraint validation — 18, Infeasibility must trigger fallback, never a relaxed re-solve., time_to_critical is always the longer runway — it measures distance to service…, test_constraint_status_defaults_to_no_violations(), test_fallback_chain_orders_by_tier(), test_flexibility_runway_exceeds_planning_slack(), test_infeasible_is_a_distinct_solver_status(), test_manual_override_outranks_scheduler_but_not_safety() (+3 more)

### Community 25 - "SystemState"
Cohesion: 0.12
Nodes (19): _history(), hold_action(), _observation(), The action that changes nothing. ``WAIT`` and ``RUN`` are both "carry on as you…, Reject a proposed action the equipment limits do not permit. ``evaluate`` is…, The outcome of evaluating one rule., Return this rule's verdict for the given resource., Verdict on an action the scheduler has *proposed*. Defaults to the state… (+11 more)

### Community 26 - "TimedValue"
Cohesion: 0.15
Nodes (23): 2026-09-17 — Phase 4 hardened: five latent bugs — commit `98f2992`, A single measured/derived value at an instant. The measured twin of…, TimedValue, Mean demand baseline. MANDATORY before any ML — nothing in ``models.py`` is…, build_feature_vector(), datetime, Assemble model-ready feature vectors from temporal context. The single point…, Build the fixed-order feature vector for ``moment``. ``demand_series`` and… (+15 more)

### Community 27 - "run_standard_set"
Cohesion: 0.22
Nodes (10): Run one controller across several scenarios. ``scheduler_factory`` is a zero-…, run_standard_set(), _build_schedulers(), compare_schedulers(), The fallback chain for one configured active tier. Returns every tier from…, Run the standard set and print a summary. Every number printed here is…, Run threshold and reactive across the extended set and compare. This is the…, run_scenarios() (+2 more)

### Community 28 - "test_temporal.py"
Cohesion: 0.12
Nodes (29): observed_demand_lpm(), observed_demand_series(), Recover the household draw between two consecutive readings. The inverse of…, Derive a chronological demand series from raw tank observations. Walks…, config(), _observe(), pump(), fixture (+21 more)

### Community 29 - "SafetyValidator"
Cohesion: 0.10
Nodes (16): IntEnum, Evaluation order. Lower value = higher authority. This ordering is a project…, SafetyPriority, Safety validation: runs before the scheduler, and again on its output. Two…, Post-scheduling validation of a proposed action. Re-runs the same rules against…, Combined result of evaluating the full rule set., Evaluates ``SafetyRule`` instances in strict priority order., Rules in evaluation order (highest authority first). (+8 more)

### Community 30 - "demand.py"
Cohesion: 0.12
Nodes (15): 2026-09-15 — Phase 1 hardening: five latent bugs — commit `ecd5ba6`, DemandProfile, ABC, datetime, Synthetic household water-demand profiles. Demand is the driver the whole…, The interpolated hourly weight, before any scaling., An unusual draw laid over the base profile — guests, washing, a leak., A base profile plus explicit spikes. The scenario that breaks naive schedulers:… (+7 more)

### Community 31 - "test_tank_model.py"
Cohesion: 0.09
Nodes (15): parametrize, Tank geometry and mass balance — Phase 1 exit criterion. These are the first…, Ripple and mounting error must not flag a healthy sensor as broken., Reproducibility of every experiment rests on this., Everything in must end up stored, drawn or spilled., The conversion the UI and the ESP32 both depend on must be exact in both…, A wild reading must not produce a negative or over-full volume that would…, test_impossible_geometry_is_rejected() (+7 more)

### Community 32 - "FlexibleResource"
Cohesion: 0.11
Nodes (13): 2026-08-17 — Project charter — commit `0e11b79`, FlexibleResource, ABC, A controllable load the scheduler can shift in time. Implementations must be…, Return the resource's hard and equipment constraints., Return the current state. For real resources this reads the latest validated…, Electrical power drawn while the actuator is energized., Simulate forward under a candidate action sequence. This is the physical model… (+5 more)

### Community 33 - "ScenarioRun"
Cohesion: 0.14
Nodes (7): Simulator quickstart, Pump starts: transitions from off to on across the run., Cycles where the pre-scheduling gate decided outright., Everything one controller did under one scenario. Steps and decisions are…, Pump energy met by surplus PV. With ``grid_energy_kwh`` this sums to…, Pump energy drawn from the grid. Phase 3's headline number., ScenarioRun

### Community 34 - "datetime"
Cohesion: 0.18
Nodes (12): datetime, Physical model — 0, ElectricalState, Instantaneous household electrical picture. ``surplus_kw`` is what the…, System state — in particular the desired/reported/confirmed invariant., Hard rule: command sent != command executed. Collapsing these into one boolean…, Unmeasured is not zero — a scheduler must be able to tell them apart., test_actuation_tracks_three_independent_states() (+4 more)

### Community 35 - "test_storage_schema.py"
Cohesion: 0.05
Nodes (48): BaseException, Connection, Schema versioning — 6, RuntimeError, sqlite3, Database, Path, SQLite connection management and schema initialization. SQLite only — no… (+40 more)

### Community 36 - "HardwareInterface"
Cohesion: 0.07
Nodes (30): Ack, AckStatus, Command, Handshake, MessageType, Enum, str, Wire protocol between the Raspberry Pi Zero and the ESP32. Newline-delimited… (+22 more)

### Community 37 - "TankModel"
Cohesion: 0.12
Nodes (11): _clamp(), Cross-sectional area, expressed in the units that matter here., Sensor distance -> depth of water, clamped to the tank., Depth of water -> the distance a healthy sensor would report., The full sensor path, which is how real telemetry arrives., Normalize to the 0..1 quantity ``scheduler/`` reasons about. Fraction of…, Whether a reading could have come from this tank at all. An ultrasonic sensor…, Geometry and mass balance for one overhead tank. (+3 more)

### Community 38 - "SlotProfile"
Cohesion: 0.17
Nodes (14): How many equal slots divide a day, or raise if they don't divide evenly. An…, Which slot of the day ``moment`` falls in, ``0`` at midnight. Not the noise…, slot_index_of(), slots_per_day(), datetime, Historical per-slot behavioural profiles. A slot profile answers "what does…, Per-(day-type, slot) mean of a quantity, built from history. ``min_samples``…, Reports the raw count even below ``min_samples`` — this is what lets a caller… (+6 more)

### Community 39 - "DiurnalBaseLoadProfile"
Cohesion: 0.15
Nodes (10): BaseLoadProfile, DiurnalBaseLoadProfile, ABC, datetime, Non-controllable household electrical load, as a function of time., Household draw excluding every load SuryaSync controls., A typical household day, interpolated between hourly values., test_base_load_has_an_evening_peak() (+2 more)

### Community 40 - "test_interfaces.py"
Cohesion: 0.10
Nodes (25): ast, inspect, Serialization / plumbing — 29, test_default_toml_is_shipped_with_the_package(), Interfaces — Phase 0 exit criterion: interfaces are stubbed. These tests guard…, The hard rule lists exactly what a plan must return. A plan missing any of…, Schedulers must not treat a bandless forecast as certain., Gap 2: a second ``observe()`` mid-cycle would justify the plan against a state… (+17 more)

### Community 41 - "ActuationHistory"
Cohesion: 0.10
Nodes (20): 5.1 `admissible_actions` could not enforce the constraints it owns, ActuationHistory, datetime, History for a resource whose timing has not been observed yet. Every equipment…, Actions permitted by equipment constraints right now. Enforces min-on / min-off…, Actuator timing facts that equipment constraints are evaluated against.…, How long the actuator has held its current state. ``None`` when unknown. A…, ActuatorRuntimeLimitRule (+12 more)

### Community 42 - "Provenance"
Cohesion: 0.15
Nodes (18): Provenance, Where a number came from. Hard rule: never present simulated results as…, build_solar_features(), PvHistory, datetime, Turn an hourly PV series into a lead-time solar forecasting dataset. One…, Exact-timestamp lookup over an hourly series, plus clear-sky index., Clear-sky index at ``at``; ``None`` at night or with no reading. (+10 more)

### Community 43 - "test_solar_forecast.py"
Cohesion: 0.15
Nodes (28): HistoricalProfileBaseline, Mean output at the target's hour of day over the training period. Takes the…, build_solar_dataset(), Chronologically split dataset from an hourly PV series. ``series`` must be…, LinearSolarModel, _fv(), datetime, Phase 6: PVGIS loader, solar features, walk-forward split, baselines, models.… (+20 more)

### Community 44 - "RunMode"
Cohesion: 0.15
Nodes (15): How the control loop is sourcing its observations., RunMode, Record a new run and return its id. Writes the ``component_versions`` row…, Any, The full set of versions in play for a single run. Persisted to…, VersionStamp, An empty snapshot looks exactly like a healthy system with nothing connected,…, The hard rule: command sent != command executed. (+7 more)

### Community 45 - "test_repositories.py"
Cohesion: 0.28
Nodes (14): ObservationRepository, Sensor readings, resource state, and the electrical picture., _insert_observation(), ``ObservationRepository.history`` — the first implemented repository method,…, From Phase 13 on, measured and simulated rows for the same resource coexist. A…, Rows are inserted out of order; the query, not insertion order, must produce…, ``since`` is inclusive, ``until`` is exclusive — a row stamped exactly at…, A caller that does not ask to filter gets everything — filtering by default… (+6 more)

### Community 46 - "ConstantDemandProfile"
Cohesion: 0.12
Nodes (17): ConstantDemandProfile, A flat draw. For unit tests and for isolating a single variable., Schedulers are contractually forbidden from re-observing mid-cycle, so the…, Rewriting a proposed sequence would hide an optimizer bug behind a trajectory…, 600 L, a 300 L planning floor and 10 lpm draw leaves 30 minutes., Regression. ``must_run_by=None`` means *unconstrained*. A tank that cannot be…, test_a_doomed_tank_reports_a_deadline_of_now_not_no_deadline(), test_a_draining_tank_has_a_deadline_inside_the_horizon() (+9 more)

### Community 47 - "build_slot_profile"
Cohesion: 0.14
Nodes (18): build_slot_profile(), Aggregate ``samples`` into per-(day-type, slot) means. ``samples`` need not be…, ``scenarios.DEFAULT_START`` is a Monday and the standard set runs 3 days, so a…, Regression-idiom: an index-based lag (``series[-1]``) would silently return…, Regression. An earlier version excluded both edges of the window, so on the…, Shorthand for building test ``TimedValue`` fixtures — the provenance rarely…, Regression: an earlier version took a separate ``slot_minutes`` argument…, test_a_feature_vector_carries_its_names_values_and_version_in_lockstep() (+10 more)

### Community 48 - "ReasonCode"
Cohesion: 0.09
Nodes (25): 2026-08-17 — Phase 0: Architecture — commit `d076167`, Highlights, Maintaining this file, SuryaSync — Development Journey, 2.1 `SchedulingRequest`, 2.2 `SchedulingPlan`, 2.3 `ReasonCode`, 2. Scheduler contract — verbatim definitions (+17 more)

### Community 50 - "SuryaSync — Project Memory"
Cohesion: 0.18
Nodes (10): Core algorithm loop, Directory structure (extend, don't restructure without reason), graphify, Hard rules, Hardware split — strict separation, Non-goals — do not add unless explicitly requested, Priority order — do not reorder, SuryaSync — Project Memory (+2 more)

### Community 51 - "system_state.py"
Cohesion: 0.21
Nodes (9): SuryaSync — solar-aware residential flexible-load scheduling. Learns temporal…, actuator_state_from_action(), Ownership and lifecycle of ``SystemState``. The only writer of system state.…, Map a control action onto the actuator state it commands. ``WAIT`` and ``STOP``…, ActuatorState, Enum, str, System state representation. The single source of truth about what SuryaSync… (+1 more)

### Community 52 - "SuryaSync"
Cohesion: 0.20
Nodes (8): Algorithm, Data Integrity, License, Repository Structure, Status, SuryaSync, System Architecture, The Core Idea

### Community 53 - "test_simulator.py"
Cohesion: 0.12
Nodes (24): forecast_step_minutes(), Spacing between forecast points, or the default if it cannot be told., _banded_forecast(), conservative_demand_at(), demand_at(), ``TankSimulator`` and ``SimulatedTankResource`` — Phase 1 exit criteria. Two…, Regression. ``Forecast`` does not promise sorted points, and one rebuilt from…, One run must never be contaminated by a previous controller's state. (+16 more)

### Community 54 - "ClearSkyModel"
Cohesion: 0.24
Nodes (7): ClearSkyModel, datetime, Clear-sky PV output from solar geometry: the physics half of…, Cloudless generation for a fixed, horizontal-equivalent array. Shared physics,…, Cooper's equation. Accurate to well under a degree., Angle of the sun above the horizon. Negative before sunrise., Output as a fraction of nameplate, before the performance ratio. Proportional…

### Community 55 - "domain.py"
Cohesion: 0.21
Nodes (10): datetime, Enum, Cross-cutting vocabulary shared by every layer. Kept deliberately dependency-…, One slot of a discretized planning horizon., TimeStep, _electrical_at(), _perfect_demand_forecast(), datetime (+2 more)

### Community 56 - "EnergySplit"
Cohesion: 0.18
Nodes (4): EnergySplit, Where one interval's energy came from and where it went. Power fields are…, Share of the controllable load met by surplus PV. ``1.0`` when nothing…, PV available to controllable loads after the base load is served.

### Community 57 - "_imported_packages"
Cohesion: 0.29
Nodes (8): _imported_packages(), parametrize, Path, The hard rule is that simulated and real resources run the *exact same* code.…, test_interfaces_cannot_be_instantiated(), test_production_layers_never_import_the_simulator(), test_production_modules_never_import_the_simulator(), test_resource_constraints_reject_impossible_bounds()

### Community 58 - "config"
Cohesion: 0.25
Nodes (8): config(), fixture, The premise of the entire project, asserted once: identical pump work draws…, Phase 1 exit criterion. Every later comparison depends on it., test_a_three_day_trajectory_is_reproducible(), run(), test_the_same_run_costs_differently_by_time_of_day(), grid_kwh()

### Community 59 - "run_scenario"
Cohesion: 0.29
Nodes (10): compare_grid_energy(), Pair runs by scenario name and compare grid-powered pump energy. Raises if the…, Run one controller through one scenario, start to finish. ``scheduler`` accepts…, run_scenario(), config(), fixture, ``analytics/comparison.py`` — pairing runs, never conflating them., test_aggregate_sums_every_row() (+2 more)

### Community 60 - "3. Test breakdown — 72 tests"
Cohesion: 0.22
Nodes (8): 1. `models/tank.py` — NOT IMPLEMENTED, 3. Test breakdown — 72 tests, Consequence for the test suite, Honest read on the shape of this suite, Is this an open Phase 0 item?, Phase 0 Review Packet, This ordering is a project invariant. It must not be reordered to make a…, test_safety_priority_order_is_the_documented_one()

### Community 61 - "TankConfig"
Cohesion: 0.50
Nodes (3): Physical geometry and hard service levels of the tank. The levels are fractions…, TankConfig, test_from_config_carries_the_geometry()

### Community 62 - "test_demand_dataset.py"
Cohesion: 0.22
Nodes (5): pytest, surya_sync_simulator, Phase 5 dataset assembly: chronological split, no leakage into the profile.…, Total samples the profile counted across all its buckets must not exceed the…, test_slot_profile_is_fit_from_train_only()

### Community 63 - "CycleDecision"
Cohesion: 0.40
Nodes (3): CycleDecision, What one cycle concluded, and how it got there. Both safety evaluations are…, Whether safety replaced what the scheduler wanted. A pre-gate trigger is not an…

### Community 64 - "demand/dataset.py"
Cohesion: 0.28
Nodes (6): build_demand_dataset(), _split(), DemandDataset, DemandSplit, Turn a resource's observation history into a model-ready demand dataset. The…, Build a chronologically split demand dataset from raw tank readings.…

### Community 65 - "ActuationState"
Cohesion: 0.31
Nodes (5): ActuationState, Three-way tracking of a single actuator. Hard rule: ``command sent != command…, True when all three agree. Divergence is a first-class signal: it means a…, ActuationRepository, Desired / reported / confirmed actuator state.

### Community 66 - "ControlCycle"
Cohesion: 0.29
Nodes (6): ControlCycle, Decide what to do with one resource, this cycle. ``state`` and ``request`` must…, The scheduler's plan, with safety's action substituted. The original reason is…, Runs the safety layer around one scheduling decision., build_control_cycle(), Assemble the safety layer and the fallback chain for a run. One place, so a…

### Community 67 - "rules.py"
Cohesion: 0.21
Nodes (9): enum, ManualOverrideRule, ABC, Safety rules and their strict priority order. The safety layer runs **before**…, A resource with no trustworthy reading may not be actuated. Ranked above…, The user's explicit instruction outranks the scheduler. It does not outrank…, One deterministic check over system state. Rules must be cheap and total: no…, SafetyRule (+1 more)

### Community 68 - "test_the_threshold_controller_never_violates_a_hard_constraint"
Cohesion: 0.67
Nodes (3): parametrize, **The Phase 2 exit criterion.** Hard constraints are hard: ``critical <= level…, test_the_threshold_controller_never_violates_a_hard_constraint()

### Community 85 - "FeatureVector"
Cohesion: 0.14
Nodes (9): FeatureVector, _Baseline, PersistenceBaseline, Persistence and historical-profile solar baselines. ML must beat all three.…, The output right now, held for the whole lead. Raises without a reading; the…, Hold the clear-sky *index* instead of the output: the cloud state is assumed to…, SmartPersistenceBaseline, _PerLeadKsiModel (+1 more)

### Community 87 - "solar/models.py"
Cohesion: 0.12
Nodes (17): collections, ndarray, numpy, sklearn_ensemble, sklearn_impute, sklearn_linear_model, sklearn_pipeline, Household water-demand forecasting. (+9 more)

### Community 102 - "SimulationStep"
Cohesion: 0.33
Nodes (3): Steps that ended outside the hard constraint band. The Phase 2 exit criterion…, One interval of a simulation run. ``start`` and ``minutes`` delimit the…, SimulationStep

### Community 103 - "earns_its_place"
Cohesion: 0.47
Nodes (6): earns_its_place(), MethodScore, Apply the two-part rule above. Returns ``(verdict, reasons it failed)``., _score(), test_best_baseline_is_chosen_per_lead_not_overall(), test_ml_is_kept_only_if_it_beats_the_best_baseline_at_every_headline_lead_and_on_test()

### Community 104 - "ComparisonRow"
Cohesion: 0.40
Nodes (3): ComparisonRow, Grid energy for one scenario, baseline vs. candidate., Positive: the candidate drew less from the grid than the baseline.

### Community 111 - "ResourceType"
Cohesion: 0.50
Nodes (4): str, Known flexible-resource classes. Only ``WATER_TANK`` is implemented. The rest…, ResourceType, test_the_resource_satisfies_the_flexible_resource_contract()

### Community 112 - "runs"
Cohesion: 0.67
Nodes (3): fixture, Every (scenario, scheduler) pair this file needs, computed once., runs()

## Knowledge Gaps
- **23 isolated node(s):** `my-agent`, `surya-sync`, `Priority order — do not reorder`, `Hardware split — strict separation`, `Core algorithm loop` (+18 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 735 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **48 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Provenance` connect `Provenance` to `test_profiles.py`, `ControlAction`, `Config`, `Forecast`, `ThresholdScheduler`, `test_demand_models.py`, `test_control_loop.py`, `test_threshold_scheduler.py`, `test_reactive_scheduler.py`, `ClearSkyProfile`, `test_safety.py`, `flexibility.py`, `TankSimulator`, `SimulatedTankResource`, `main.py`, `generic_resource.py`, `ResourceObservation`, `Constraint validation — 18`, `TimedValue`, `test_temporal.py`, `demand.py`, `ScenarioRun`, `datetime`, `test_interfaces.py`, `ActuationHistory`, `test_solar_forecast.py`, `test_repositories.py`, `ConstantDemandProfile`, `build_slot_profile`, `ReasonCode`, `test_scheduling_request_needs_only_an_observation`, `system_state.py`, `test_simulator.py`, `domain.py`, `run_scenario`, `FeatureVector`, `SimulationStep`, `ResourceType`, `test_results_are_labelled_simulated`?**
  _High betweenness centrality (0.108) - this node is a cross-community bridge._
- **Why does `ControlAction` connect `ControlAction` to `Config`, `Forecast`, `ThresholdScheduler`, `test_control_loop.py`, `test_threshold_scheduler.py`, `StateManager`, `test_reactive_scheduler.py`, `PumpModel`, `test_safety.py`, `TankSimulator`, `SimulatedTankResource`, `generic_resource.py`, `ResourceRegistry`, `SystemState`, `SafetyValidator`, `FlexibleResource`, `test_interfaces.py`, `ActuationHistory`, `RunMode`, `ConstantDemandProfile`, `ReasonCode`, `system_state.py`, `test_simulator.py`, `domain.py`, `config`, `CycleDecision`, `rules.py`, `SimulationStep`, `ResourceType`?**
  _High betweenness centrality (0.101) - this node is a cross-community bridge._
- **Why does `Current phase` connect `Config` to `test_profiles.py`, `ControlAction`, `Forecast`, `ThresholdScheduler`, `test_control_loop.py`, `test_threshold_scheduler.py`, `test_reactive_scheduler.py`, `ClearSkyProfile`, `train_demand_model`, `TankSimulator`, `SimulatedTankResource`, `generic_resource.py`, `ResourceObservation`, `TimedValue`, `test_temporal.py`, `FlexibleResource`, `SlotProfile`, `ActuationHistory`, `Provenance`, `RunMode`, `ConstantDemandProfile`, `build_slot_profile`, `ReasonCode`, `SuryaSync — Project Memory`, `test_simulator.py`, `run_scenario`, `FeatureVector`?**
  _High betweenness centrality (0.064) - this node is a cross-community bridge._
- **Are the 136 inferred relationships involving `ControlAction` (e.g. with `Current phase` and `2026-08-17 — Phase 0: Architecture — commit `d076167``) actually correct?**
  _`ControlAction` has 136 INFERRED edges - model-reasoned connections that need verification._
- **Are the 65 inferred relationships involving `Provenance` (e.g. with `Current phase` and `2026-08-17 — Phase 0: Architecture — commit `d076167``) actually correct?**
  _`Provenance` has 65 INFERRED edges - model-reasoned connections that need verification._
- **Are the 46 inferred relationships involving `Config` (e.g. with `config_hash()` and `load_config()`) actually correct?**
  _`Config` has 46 INFERRED edges - model-reasoned connections that need verification._
- **Are the 67 inferred relationships involving `Current phase` (e.g. with `ControlAction` and `Forecast`) actually correct?**
  _`Current phase` has 67 INFERRED edges - model-reasoned connections that need verification._