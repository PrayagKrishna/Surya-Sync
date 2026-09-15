# SuryaSync — Development Journey

Development log. Records what happened, in what order, and why.

Distinct from `CLAUDE.md` (session context for the next working session) and
`ROADMAP.md` (forward plan). This file is backward-looking only.

**Standing status caveat.** As of `5873f7b` the system has run **zero** physical
hardware trials. Physical *models* now exist (`5873f7b`), so trajectories can be
produced, but they are simulated and are tagged `[simulated]` wherever they
appear. No number in this file is a measured hardware result, and none will be
until Phase 13. Every other figure is a test count, a commit, or a file
measurement.

---

## Highlights

*Curated. Rewritten periodically, not append-only. Rewrites require review of
the diff before they land.*

- **Architecture-first sequencing, with the cost paid deliberately.** Phase 0
  shipped 68 package modules and a 19-table schema with **0 lines of
  scheduling logic and 0 physical models** — an explicit choice recorded in
  `ROADMAP.md` Phase 0 exit criteria ("no logic yet"). The 72-test suite at
  `2580cc1` broke down as 18 constraint validation / 19 config rejection /
  6 schema versioning / 0 physical model / 29 serialization-and-contract. The
  0 is reported, not hidden: at that commit the suite proved the architecture
  was self-consistent, not that anything worked.

- **A self-review of the scaffold caught two interface defects before any code
  depended on them.** `FlexibleResource.admissible_actions(observation, now)`
  was the designated gate for `min_on_minutes` / `min_off_minutes` /
  `max_starts_per_day`, but could evaluate none of them — its only state input
  was a boolean `actuator_on`, carrying no elapsed time and no start count.
  Found while reviewing Phase 0, fixed in `2504c2f` before Phase 1 built on it.

- **Data-integrity invariants enforced by the database, not by convention.**
  Schema v1 (`storage/schema.sql`, 19 tables) `CHECK`-constrains `provenance`
  on all 9 tables that store numbers, and constrains the `ControlAction` (3),
  `ReasonCode` (15) and `SolverStatus` (6) vocabularies. Two tests read the
  constraints back out of SQLite and compare them against the Python enums, so
  the two cannot drift apart silently.

- **Zero third-party runtime dependencies** (`pyproject.toml`:
  `dependencies = []`), driven by the Raspberry Pi Zero deployment target.
  Config parsing uses stdlib `tomllib`; storage is stdlib `sqlite3`. ML,
  solver, serial and API libraries are declared as optional extras, gated to
  the phases that need them.

- **Fail-safe direction is specified, not assumed.** Unknown actuator timing is
  represented as `None` and is contractually required to read as
  "constraint not yet satisfied", so an unknown holds the pump rather than
  permitting a start (`ActuationHistory`, `2504c2f`). Rationale recorded in the
  contract: refusing to switch is recoverable, short-cycling a pump is not.

---

## Timeline

*Append-only, oldest first. Entries are never rewritten. A reversed decision
gets a new entry that links back to the original.*

### 2026-08-17 — Project charter — commit `63c0fda`

- **Built:** `README.md`, `CLAUDE.md`, `ROADMAP.md`. Fixed the 13-item priority
  order (scheduling algorithm first, UI polish last), the hard rules, the
  ESP32-vs-Pi-Zero split, the 17-phase roadmap with per-phase exit criteria,
  and an explicit non-goals list.
- **Key decisions:**
  - Scheduling decision is the contribution; the relay is infrastructure.
    Written into `CLAUDE.md` to stop later effort drifting toward provable-but-
    uninteresting hardware demos.
  - Deep learning ruled out until simpler models plateau with measured
    evidence. Rejected alternative: start with an LSTM. Rejected on Pi Zero
    inference cost and on the absence of any baseline to beat.
  - Tank constraints declared hard, never soft penalty terms. Rejected
    alternative: penalty terms in the objective, which is easier to make
    feasible and would have let the optimizer trade away service failure.
- **Results:** No code, no tests.
- **AI assistance:** **None recorded.** This commit carries no
  `Co-Authored-By` trailer, unlike every commit after it. The priority order,
  hard rules, hardware split and non-goals are the author's own.
- **Open questions carried forward:** Whether the `FlexibleResource`
  abstraction could hold a second resource type without forking the scheduler.

### 2026-08-17 — Phase 0: Architecture — commit `2580cc1`

- **Built:** 68 modules under `surya_sync/` across the directory tree in
  `CLAUDE.md`, plus 4 test modules; SQLite schema v1 with 19 tables (73 `.py`
  files in total at this commit). Only `config/loader.py` and
  `storage/database.py` carry real implementation — required by the phase's own
  exit criteria. Everything else is interface or stub.
  - `Scheduler` / `FlexibleResource` interfaces (`scheduler/base.py`,
    `models/generic_resource.py`).
  - Dependency-free shared vocabulary in `domain.py` (`ControlAction` 3,
    `Provenance` 5, plus `Forecast` / `Horizon`) — imports nothing from the
    package, so it cannot participate in an import cycle.
  - `SafetyPriority` `IntEnum`, 10 levels, scheduling at rank 8 below every
    safety rule.
  - TOML config, validated and hashed; unknown keys are fatal.
- **Key decisions:**
  - Normalize every resource to a `service_level` in 0..1 so `scheduler/` never
    learns which resource it schedules. Rejected alternative: native units
    (litres) throughout, which is more readable but forks the scheduler per
    resource type.
  - One `SchedulingRequest` object rather than a growing argument list, so the
    interface stays identical across all planned scheduler tiers.
  - Three-way actuation state (desired / reported / confirmed) as three columns
    with three timestamps. Rejected alternative: a single boolean, which hides
    lost commands and failed relays.
  - Unknown config keys are fatal rather than ignored, so a typo'd physical
    parameter cannot silently fall back to a default.
- **Problems hit:**
  - The commit message claimed a "tank model" that did not exist —
    `models/tank.py` was a 4-line stub. **Root cause:** message written from the
    phase's intent rather than its contents. **Fix:** amended before merge
    (see next entry). The tank model is Phase 1 scope per `ROADMAP.md`, so the
    code was correct and only the message was wrong.
- **Results:** 72 tests passing. Breakdown: 18 constraint validation, 19 config
  rejection, 6 schema versioning, **0 physical model**, 29 serialization and
  contract shape. The 0 is expected at this phase and is the reason the suite
  cannot yet claim anything works.
- **AI assistance:** Implementation AI-assisted (Claude Code; commit carries a
  `Co-Authored-By: Claude Opus 5` trailer). The specification it implemented —
  directory structure, hard rules, priority order, phase exit criteria — was
  authored beforehand in `63c0fda`. Finer attribution within this session was
  not recorded at the time and is not reconstructed here.
- **Open questions carried forward:** `SchedulingRequest` field set;
  `service_level` as the generalization seam; whether `ReasonCode` was complete.

### 2026-08-19 — Phase 0 review: packet recorded and merged — commit `e3bea49`

- **Built:** `PHASE0_REVIEW.md` committed to the repo (previously untracked).
  Phase 0 fast-forwarded onto `main` as `2580cc1`; `main` pushed to `origin`.
- **Key decisions:**
  - Hold Phase 1 until the review checklist was worked. Rationale: the
    `SchedulingRequest` field set and the `service_level` seam are near-free to
    change while only interfaces exist and expensive once a simulator and
    schedulers sit on them. Rejected alternative: start Phase 1 and revisit
    later.
  - Commit the review packet rather than keep it a local note, so the reasoning
    behind the Phase 0 shape — in particular why `models/tank.py` is
    deliberately empty — survives the session.
  - Amend the Phase 0 commit message rather than merge it as-is. The commit was
    unpushed, so amending was safe.
- **Problems hit:**
  - `git push origin main` failed with `Permission denied (publickey)`.
    **Root cause:** `~/.ssh/id_ed25519` is passphrase-protected, no `ssh-agent`
    running, and no `ssh-askpass` binary available. **Fix:** pushed over HTTPS
    using the `gh` CLI credential helper, without modifying the remote config.
  - Amending changed the commit hash, leaving `PHASE0_REVIEW.md` citing a hash
    (`5a3c4ce`) that no longer existed. **Fix:** header updated to cite
    `2580cc1` and note the trees are identical.
- **Results:** 72 tests passing, unchanged — this commit moved documents only.
- **AI assistance:** Options and recommendations from Claude Code; the decision
  to hold Phase 1, and the choice to commit the review packet, were the
  author's, taken against those recommendations as input.
- **Open questions carried forward:** Five design questions in
  `PHASE0_REVIEW.md` section 4.

### 2026-08-19 — Phase 0 review: interface fixes — commit `2504c2f`

- **Built:** `ActuationHistory` (`models/generic_resource.py`) with
  `changed_at`, `starts_today`, `last_start_at`, `minutes_in_state()`; threaded
  through `admissible_actions(observation, history, now)` and
  `SchedulingRequest.actuation_history`. `SchedulingPlan.fallback_engaged`.
  `CHECK` constraints on `reason_code` and `solver_status`. Two safety
  priorities renamed. Outcomes written up as `PHASE0_REVIEW.md` section 5.
- **Key decisions:**
  - Represent unknown actuator timing as `None` and require implementations to
    read it as "constraint not yet satisfied". Rejected alternative: default to
    0 minutes elapsed, which reads as "cooldown long over" and is exactly the
    failure that short-cycles a pump.
  - Remove `ReasonCode.FALLBACK_ENGAGED`; carry the fact on
    `SchedulingPlan.fallback_engaged` instead. Rationale: falling back is not a
    rationale — the lower tier still decided for a substantive reason, and the
    code displaced it. The fallback was already recorded by
    `SchedulingPlan.tier` and the `scheduler_decisions.fallback_engaged` column.
    Vocabulary went 16 codes to 15.
  - Edit schema v1 in place rather than bump to v2. Rationale: no deployment
    exists and the only database held a single `schema_version` row; a
    migration protects real data and there was none. Rejected alternative: a v2
    migration, judged ceremony at this stage.
  - **Declined:** adding `deadline` and `interruptible` to
    `ResourceConstraints`. These are needed by EV chargers and washing machines
    but not by the water tank, which is the actual scope; the other resource
    types exist to keep the interfaces honest, not to be delivered. Recorded as
    a known limit rather than an oversight — both are additive fields with safe
    defaults. **Author's call, against the recommendation to add them.**
  - Renamed `PUMP_PROTECTION` to `ACTUATOR_PROTECTION` and
    `CRITICAL_WATER_AVAILABILITY` to `CRITICAL_SERVICE_AVAILABILITY`. Accepted
    because generic naming in a shared layer costs nothing; distinct from the
    declined item above, which would have added unused fields.
  - Left `SchedulingRequest.provenance` as `Provenance` rather than `RunMode`.
    `RunMode` has 3 values, the DB column accepts 2, and a replay run has no
    distinct answer of its own. A raised concern that was checked and dropped.
- **Problems hit:**
  - `admissible_actions` could not evaluate the equipment constraints it owned
    (see Highlights). **Root cause:** `ResourceObservation` was designed to
    describe instantaneous state, while equipment constraints are defined over
    elapsed time and counts; `FlexibleResource` is contractually stateless with
    respect to scheduling, so nothing else held the history either.
    **Fix:** `ActuationHistory`, passed in rather than stored.
  - A scheduler could call `request.resource.observe()` and receive a state
    different from `request.observation`. **Root cause:** the request holds a
    live object, not a snapshot. **Fix:** prohibition stated in the `Scheduler`
    contract, with a test asserting the rule stays in the docstring; the rule
    is not enforceable at runtime. This is a documented convention, not a
    guarantee.
  - A first draft of that test contained an `assert ... or True` clause, which
    can never fail. Caught on read-back and replaced before commit.
- **Results:** 81 tests passing, up from 72. Per file: `test_config.py` 25,
  `test_interfaces.py` 35, `test_state.py` 6, `test_storage_schema.py` 15.
  Physical-model tests remain at **0**. Dev database rebuilt to pick up the new
  constraints; it held 1 row and is gitignored.
- **AI assistance:** The five findings were identified by Claude Code during a
  code review of the Phase 0 tree, and it wrote the fixes and tests. Which
  findings to act on was the author's call: fixes 1, 2, 3 and the two small
  vocabulary items were approved; the `deadline` / `interruptible` proposal was
  rejected on scope grounds with rationale recorded above.
- **Open questions carried forward:** Phase 1 must take physical-model test
  coverage off 0. `ROADMAP.md` Phase 1 exit criteria already require
  deterministic multi-day trajectories plus tank-conversion, physical-model and
  pump-model unit tests.

### 2026-09-15 — Phase 1: Simulator and physical models — commit `5873f7b`

- **Built:** 1,749 lines across `models/tank.py` (geometry + mass balance),
  `models/pump.py` (flow, energy, the cycling gate), `simulator/tank.py`
  (`TankSimulator` + `SimulatedTankResource`), `simulator/demand.py`,
  `simulator/solar.py`, `simulator/grid.py` (base load + energy attribution),
  `simulator/noise.py`, `simulator/scenarios.py`. Four test modules added.
  Still zero third-party runtime dependencies.
- **Key decisions:**
  - **Physics lives in `models/`, not in the simulator.**
    `predict_trajectory` and `TankSimulator.advance` both call
    `TankModel.step`, and a test asserts the two agree to floating point.
    Rationale: if the optimizer could search a world other than the one it
    is scored in, the divergence would surface as "the scheduler is bad"
    rather than as the modelling bug it is. Rejected alternative: a fast
    approximate model for the optimizer and an accurate one for the
    simulator — the standard reason MPC results fail to reproduce.
  - **Profiles are pure functions of time, not seeded random streams.** A
    `random.Random` stream's output depends on how many times it has been
    called, so querying out of order changes the answer; MPC re-queries the
    same future instant across replans and replay re-queries the series out
    of order. `simulator/noise.py` uses an integer hash instead.
  - **Grid attribution is marginal: the pump may claim only the surplus PV
    left after the base household load.** The fridge's consumption is not
    the scheduler's achievement. Rejected alternative: split PV pro rata
    across all loads, which would have let a scheduler book a solar win for
    running at night against a house that was importing anyway.
  - Overflow and unmet demand are reported as `spilled_l` /
    `unmet_demand_l` rather than absorbed by clamping the volume. Clamping
    alone would render a failing trajectory as one that merely stopped
    changing.
  - `min_level` is excluded from the hard-constraint check; only
    `critical_level`, `max_level` and spill count. A plan dipping below its
    planning floor went wrong, but the household did not lose service.
    Conflating them would make every cautious scheduler look unsafe.
  - Clear-sky output uses real solar geometry (Cooper's declination, solar
    altitude) rather than a fixed bell curve, so day length varies with
    season and `latitude` / `longitude` become live config rather than
    decoration. Explicitly **not** an irradiance model: no atmosphere, no
    diffuse component, no temperature derating. Phase 6 forecasts against
    real generation.
  - `DEFAULT_START` moved to a Monday so a multi-day comparison is not
    perturbed by weekend demand scaling landing on different days.
- **Problems hit:**
  - **A cold start deadlocks the pump.** First end-to-end run: 288 steps,
    pump never once started, 817.5 L `[simulated]` of demand unmet.
    **Root cause:** `admissible_actions` treats unknown actuator timing as
    "constraint not satisfied" (the rule carried out of the Phase 0 review),
    `changed_at` is only written by a transition, and the unknown forbids
    the only transition that would write it. **Fix in the simulator:** a
    simulated world provably begins at `clock`, so `changed_at` is seeded
    there; `TankSimulator(cold_start=True)` exercises the genuine unknown.
    **Not fixed generally, and recorded as carried forward:** on real
    hardware `state/` must seed the timestamp from the actuation log or
    from boot time, or a Pi that boots with no log never starts the pump.
    The contract was right; the initialisation was missing.
  - **A 15-minute control step cannot hold the target band.** At 30 lpm the
    pump fills the 1000 L tank in 33 minutes, so one
    `scheduler.step_minutes` moves the level 45 percentage points; the
    naive driver overshot to 0.891 against a 0.80 stop threshold and spilled
    991.7 L `[simulated]`. **Root cause:** control resolution, not the
    physics — at a 1-minute step the same driver spills nothing and peaks at
    0.80. **Not fixed:** Phase 2's controller must predict the fill and stop
    early. Recorded as a test so the cause is not later mistaken for a
    model bug.
  - Two of the first test failures were the test's arithmetic, not the
    model's: a daily-volume assertion landed on a Sunday and silently
    collected the 1.15 weekend scale, and two step assertions asked for
    volumes above the tank's capacity. Both were rewritten to state the day
    type and stay inside the tank.
- **Results `[simulated]`:** 262 tests passing, up from 81. Per file:
  `test_config.py` 25, `test_interfaces.py` 35, `test_state.py` 6,
  `test_storage_schema.py` 15, `test_tank_model.py` 39,
  `test_pump_model.py` 31, `test_profiles.py` 53, `test_simulator.py` 58.
  **Physical-model tests: 181, up from 0** — the open question carried out
  of `2504c2f` is closed. `SimulatedTankResource` runs all four standard
  scenarios over three simulated days; a three-day trajectory reproduces
  exactly on re-run, and no scenario leaves demand unmet at a 5-minute step.
- **AI assistance:** Design and implementation AI-assisted (Claude Code;
  commit carries a `Co-Authored-By: Claude Opus 5` trailer). The two
  findings above were surfaced by running the simulator rather than by
  inspection. The decisions to keep the physics in one place, to make
  profiles pure in time, and to attribute solar marginally were taken
  during implementation and are recorded here with their rejected
  alternatives; they were not pre-specified in `63c0fda`.
- **Open questions carried forward:**
  - Phase 2 must supply a controller that predicts fill rather than
    reacting to a threshold, or decide that `scheduler.step_minutes` is
    wrong for this hardware. Do not resolve it by changing the physics.
  - Phase 11's `state/` owes a `changed_at` at boot.
  - `estimate_flexibility` currently scans O(n²) to find `must_run_by`.
    Correct, and cheap at a 48-step horizon, but it has not been
    benchmarked on a Pi Zero; Phase 12 should measure it.

---

## Maintaining this file

1. Add an entry at the end of every phase, and at the end of any session that
   produces a committed change. Not "when convenient." If a session is ending
   without an entry, say so before it closes.
2. Timeline entries are append-only. Never edit a past entry. A reversed
   decision gets a new entry that links back to the one it reverses.
3. Highlights may be rewritten, but only after showing the diff and getting
   confirmation.
4. Every claim carries a referent: a commit hash, a test count, a file
   measurement, or an explicit `[simulated]` / `[measured]` tag. No claim
   without one.
5. Record AI assistance per entry, and separate it from the author's design
   calls. Do not blanket-state it once. Where attribution was not recorded at
   the time, say that rather than reconstructing it.
6. Factual and dense. This is a log, not marketing copy. Narrative framing
   belongs in portfolio drafts written from this file, not in it.
