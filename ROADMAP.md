# SuryaSync Roadmap

One phase = one focused working session (or a tight cluster of them).
Do not start phase N+1 until phase N's exit criteria are met and tested.
Update the checkbox and the "Current phase" line in CLAUDE.md when a phase
closes.

- [x] **Phase 0 — Architecture** ✅
  Interfaces (`Scheduler`, `FlexibleResource`), config schema, DB schema.
  Exit: directory tree exists, interfaces are stubbed, `config/` loads,
  SQLite schema created, no logic yet.
  *Met: tree + stubs in place; `load_config()` validates and hashes;
  `python -m surya_sync.main --init-db` creates schema v1 idempotently;
  72 tests pass. Only `config/loader.py` and `storage/database.py` carry
  implementation — required by the exit criteria themselves.*

- [x] **Phase 1 — Simulator** ✅
  Tank simulator, pump model, demand profiles, solar profiles, grid model.
  Exit: `SimulatedTankResource` runs a multi-day trajectory deterministically;
  unit tests for tank conversion + physical model + pump model pass.
  *Met: `SimulatedTankResource` runs the four standard scenarios over three
  simulated days and reproduces them exactly; `predict_trajectory` and
  `TankSimulator.advance` are asserted to agree to floating point because
  both call `TankModel.step`; 262 tests pass, of which 181 are physical —
  up from 0.*

- [x] **Phase 2 — Conventional control** ✅
  Threshold controller implementing `Scheduler`.
  Exit: runs against the simulator, never violates hard tank constraints
  across the standard scenario set (sunny/cloudy/spike/low-start).
  *Met: `ThresholdScheduler` runs all four scenarios over three simulated
  days with **0 violating steps, 0 L spilled and 0 L unmet demand**, within
  the daily start budget (`--run-scenarios`, and asserted per scenario in
  `tests/test_control_loop.py`). The safety layer, the fallback chain and the
  shared `control_loop.ControlCycle` landed with it; 379 tests pass, up from
  277. The exit criterion holds at the configured 15-minute step and is not a
  claim about any step — 30 minutes and above provably cannot hold the band,
  and a test pins that.*

- [x] **Phase 3 — Solar-reactive control** ✅
  Simple current-surplus scheduler.
  Exit: beats threshold controller on grid-powered pump energy in
  simulation; comparison logged via `analytics/comparison.py`.
  *Met: `ReactiveScheduler` beats the threshold controller on the
  extended-set aggregate (21.537 -> 15.872 kWh grid, `[simulated]`, 30
  days) and on 3 of 5 individual scenarios (`low_start`, `spike`, `sunny`),
  with the other 2 (`cloudy`, `monsoon`) tying — never losing — for a
  measured, structural reason recorded below. Comparison logged via
  `analytics/comparison.py` and reproducible with `--compare-schedulers`.
  Zero hard-constraint violations at every duration checked (3-60 days).
  412 tests pass. Closed 2026-09-16 on the extended-set evidence, after an
  earlier close-or-not-yet decision was made and then revisited once the
  3-day benchmark was found to be too short a window (see below).*
  *Note from Phase 2: the threshold controller already draws 0.00 kWh from
  the grid in `sunny`, so that scenario cannot be beaten. Judge on `cloudy`
  (0.51 kWh), `low_start` (0.75 kWh) and the aggregate.*
  *Measured [simulated] at the original 3-day standard set: `low_start`
  0.75 -> 0.19 kWh, aggregate 1.64 -> 1.07 kWh, `sunny`/`cloudy`/`spike` all
  tied. **3 days turned out to be too short to judge this fairly** — see
  below.*
  *Re-measured [simulated] on `extended_set` (30 days, five scenarios —
  `sunny`/`cloudy`/`spike`/`low_start` plus `monsoon`, sustained heavy
  overcast) via `--compare-schedulers`:*
  | scenario | threshold kWh | reactive kWh | saved |
  |---|---|---|---|
  | cloudy | 5.443 | 5.443 | 0.000 (tied) |
  | low_start | 3.586 | 1.137 | 2.449 |
  | monsoon | 5.813 | 5.813 | 0.000 (tied) |
  | spike | 4.045 | 2.148 | 1.896 |
  | sunny | 2.651 | 1.331 | 1.320 |
  | **aggregate** | **21.537** | **15.872** | **5.666** |
  *`spike` and `sunny` — tied at 3 days — are clear wins once run long
  enough (confirmed from 7 days onward across 3/7/14/30/60-day checks); the
  3-day tie was a duration artifact, not a real limit. `cloudy` and
  `monsoon` (a second, harsher poor-solar scenario added specifically to
  test this) tie exactly at every duration checked, 3 through 60 days — a
  stable, structural limit: the reactive scheduler only tops up on surplus
  that fully covers the pump's rated draw (0.75 kW), and neither scenario
  ever offers such a window before the level-triggered run would fire
  anyway. Zero hard-constraint violations across the extended set at any
  tested duration. **Decision (2026-09-16): closed.** The first close-or-
  not-yet call was made against the weaker 3-day numbers and came down
  "not yet"; re-measured on the 30-day extended set, the case is a clean
  win (3 of 5 scenarios improve, 2 tie for a stated structural reason,
  none regress), and the author confirmed closing on this evidence. See
  `PROJECT_JOURNEY.md`.*

- [x] **Phase 4 — Temporal behaviour** ✅
  Cyclic time encoding, historical slot means, lag/rolling features.
  Exit: `temporal/` produces feature vectors matching section 12's feature
  list; tests for temporal encoding pass.
  *Met: "section 12" is not in this repo (greps clean), so the 18-feature
  list is pinned as code — `ml.features.builder.FEATURE_NAMES` — and
  `test_feature_names_match_the_pinned_spec_exactly_and_in_order` is the
  exit-criterion test. `temporal/context.py` (cyclic time-of-day/
  day-of-week/day-of-year encoding), `temporal/profiles.py` (per-slot
  historical means, weekday/weekend kept separate), `temporal/history.py`
  (time-based, gap-aware lag and rolling features) and
  `ml/features/builder.py` (the 18-value vector) are all implemented.
  `ObservationRepository.history()` — the one `NotImplementedError` in the
  repo explicitly tagged Phase 4 — is also implemented, with an added
  `provenance` filter. Asked directly whether Phase 4 had been thoroughly
  debugged, the honest answer was no; an adversarial probing pass then
  found and fixed five latent bugs (a rolling-window boundary that
  systematically dropped a sample at normal cadence, a silent
  slot-grid-mismatch footgun, a caller error swallowed as "unidentifiable"
  instead of raised, a missing unit check, and a missing provenance tag —
  see `CLAUDE.md`'s Phase 4 hardening notes). 455 tests pass, up from 412
  (449 before hardening). `--run-scenarios` reproduces Phase 2's exact
  figures unchanged, confirming nothing leaked into the decision path.
  `temporal/adaptation.py` stays a stub — deliberately out of scope; see
  the carried-forward note below.*

- [x] **Phase 5 — Demand ML** ✅
  Mean baseline → linear regression → random forest → gradient boosting.
  Exit: chronological train/val/test split; MAE/RMSE reported for every
  model; final model chosen by measured performance + Pi inference cost,
  not by default.
  *Met, after a hardening pass — see `PROJECT_JOURNEY.md`'s "Phase 5
  hardened" entry for the full debugging trail. `ml.evaluation.
  chronological_split` (index-based, never shuffles — shared with Phase 6
  solar) splits `models.tank.observed_demand_series` 70/15/15;
  `SlotProfile` is fit on the train slice only
  (`ml.demand.dataset.build_demand_dataset`). `ml.demand.baselines.
  MeanBaseline` and `ml.demand.models.{Linear,RandomForest,
  GradientBoosting}DemandModel` (scikit-learn, median-imputed) share one
  `fit`/`predict` shape. `main.py --train-demand-model` runs the pipeline
  over the new `simulator.scenarios.realistic_household` (30 days, real
  day-to-day demand jitter and passing-cloud solar — not `extended_set`,
  whose scenarios each hold demand fixed for their whole duration by
  design, see that function's docstring) and prints validation *and*
  held-out test MAE/RMSE, flagging any tier that fails to beat the
  previous one's validation MAE rather than reporting it uncommented.

  **A real bug was found and fixed while checking Phase 5's first-pass
  numbers, not treated as data to work around:**
  `models.tank.observed_demand_lpm` read `previous.actuator_on` to infer
  the pump's inflow during an interval, but a real observation (and the
  simulator's, identically) reports the actuator's state *before* the
  decision for the upcoming interval is made — it cannot know that
  decision yet. `current.actuator_on`, recorded after that decision was
  applied, is the one that actually governed the interval.
  `tests/test_temporal.py` had already worked around this by hand-shifting
  `actuator_on` forward by one step in a synthetic fixture built
  specifically for testing, with a comment saying so — proof the mismatch
  was known but never fixed at the source, because no production code
  path exercised the raw stream until this phase's `ScenarioRun.
  observations` field did. Measured effect: recovered demand ranged
  -29.7 to +30.6 L/min before the fix (against a true profile maximum
  under 1 L/min) and 0.01 to 0.96 L/min after — the fabricated values
  were the entire reason random forest's first-pass MAE (0.003-0.13
  depending on scenario) looked implausibly good; its dominant feature
  was `service_level` (51%), an artifact of the bug, not a real signal.
  Fixed by reading `current.actuator_on`; the fixture workaround was
  deleted in favor of testing directly against `ScenarioRun.observations`.

  **Measured** [simulated], `realistic_household`, 30 days, default
  config, MAE/RMSE (L/min), validation then held-out test:

  ```
  model                   val_MAE  val_RMSE  test_MAE  test_RMSE
  mean_baseline            0.2039    0.2391    0.2019    0.2464
  linear_regression        0.0183    0.0276    0.0214    0.0325
  random_forest            0.0177    0.0266    0.0227    0.0350
  gradient_boosting        0.0192    0.0282    0.0228    0.0353  <- flagged
  ```

  Every tier beats the mean baseline by roughly 10x, as it should on a
  target this close to a smooth, mildly-jittered diurnal curve. Random
  forest edges out linear regression on validation MAE (0.0177 vs.
  0.0183, ~3% relative); gradient boosting does not beat random forest and
  `--train-demand-model` prints a warning for it rather than silently
  listing it as a peer. Random forest's feature importance is now
  dominated by `slot_mean_demand_lpm` (98%), the historical per-slot mean
  — the sane result, given the target is a mostly-deterministic curve plus
  jitter. Given random forest's edge over linear regression is small and
  linear regression is far cheaper to run (one dot product vs. traversing
  100 trees), **linear regression is Phase 5's chosen model, confirmed by
  the author** (`ml.demand.SELECTED_MODEL`) — "final model chosen by
  measured performance + Pi inference cost" is genuinely both-conditions
  now, rather than accuracy alone standing in for a cost nobody measured.
  Phase 12's actual Pi Zero benchmark can still overturn this if random
  forest turns out cheap enough on-device that its small accuracy edge is
  worth taking.*

- [x] **Phase 6 — Solar forecast**
  Persistence, historical profile, ML if it earns its place.
  Exit: ML only kept if it beats both baselines on held-out data.
  *Met: all three ML tiers beat the best baseline; linear regression is the
  chosen model (`ml.solar.SELECTED_MODEL`), confirmed by the author.*

  *Phase 6 [estimated]: scored on real weather, not on the simulator.*

  **Why not the simulator's solar.** `IntermittentProfile`'s cloud factor is
  independent from one 20-minute slot to the next, so a forecaster has
  nothing to learn beyond the clear-sky curve and every method would tie for
  an artifact reason (the same trap as Phase 5's demand data). The author
  chose real data over a new simulated scenario, because a scenario we build
  hands the models the pattern we put in it.

  **Data.** PVGIS-ERA5 hourly output for a 3 kWp, 13° tilt, 20% loss array at
  12.97N 77.59E, 2020-2022 (`data/reference/pvgis_bangalore_2020_2022.csv`,
  26,304 rows, (c) European Union). A reanalysis through a PV model, **not**
  a rooftop meter: `Provenance.ESTIMATED`, never `MEASURED`. Hourly, one
  site, one nominal system. The clear-sky curve correlates best with it at
  zero time shift (r=0.913, vs 0.896 at -30 min, 0.905 at +30 min). Phase
  13's inverter data is the real check.

  **Built.** `ml/solar/data.py` (loader, UTC -> local +5:30, header parsed;
  `require_matches_config` refuses data for a different site or kWp),
  `dataset.py` (13 pinned features, leads 1-12/18/24 h, daylight-only
  targets, split by *target* time, historical profile fit on train only),
  `baselines.py` (persistence, clear-sky-index "smart" persistence,
  historical hour-of-day profile), `models.py` (per-lead clear-sky-index
  linear / random forest / gradient boosting), `evaluation.py` (per-lead
  scoring and `earns_its_place`), `main.py --train-solar-model`.
  `models/solar.py` (new) holds the clear-sky geometry moved out of
  `simulator/solar.py`; `simulator.solar.ClearSkyProfile` is now
  `ClearSkyModel` + `SolarProfile` and behaves identically.
  `ml.features.builder.to_matrix` is now shared by demand and solar.
  518 tests pass, up from 484.

  **The keep-ML rule, fixed before the final numbers:** a tier is kept only
  if its validation MAE beats the best baseline *at each of the 1/3/6/12/24 h
  leads*, and its test MAE beats the best baseline overall. Daylight targets
  only: below 0.05 kW of clear-sky output the forecast is exactly zero by
  geometry, so scoring night would add the same free zero to every method.

  **Results** `[estimated]`, `--train-solar-model`, train 2020-01-01..
  2022-02-06 (125,520 examples), val ..2022-07-20 (27,636), test 2022-07-21..
  2022-12-31 (26,712); MAE/RMSE in kW, all leads pooled; mean test daylight
  output 0.875 kW:
  ```
  method              val_MAE val_RMSE test_MAE test_RMSE
  persistence          0.8398   1.0469   0.7460    0.9291
  smart_persistence    0.3944   0.5397   0.4036    0.5455
  historical_profile   0.2495   0.3360   0.2411    0.3386
  linear_regression    0.1277   0.2055   0.1754    0.2536
  random_forest        0.1226   0.2027   0.1744    0.2547
  gradient_boosting    0.1223   0.2019   0.1756    0.2554

  test MAE by lead        1h     3h     6h    12h    24h
  smart_persistence    0.143  0.294  0.427  0.568  0.199
  historical_profile   0.241  0.241  0.241  0.241  0.241
  linear_regression    0.115  0.167  0.182  0.186  0.188
  random_forest        0.098  0.158  0.184  0.190  0.193
  gradient_boosting    0.100  0.159  0.185  0.190  0.197
  ```
  Linear is 49% below the best baseline on validation and 27% below on
  test. All three tiers are KEPT.

  **Model shape was chosen on validation only, after a first design failed.**
  The first pass was one pooled model predicting kW (test seen: 0.1937 linear,
  0.1817 gradient boosting). It lost to smart persistence at the 1 h lead on
  test (0.147 vs 0.143), which prompted the redesign — so test was *seen*
  before the redesign, and that is stated here rather than hidden. Four
  shapes were then compared on **validation** MAE (linear / gradient
  boosting): pooled kW 0.167/0.131; per-lead kW 0.152/0.125; pooled
  clear-sky-index 0.133/0.126; **per-lead clear-sky-index 0.128/0.122**
  (chosen). Test was not consulted for the choice, and per-lead
  clear-sky-index fits were scored on test once, by the CLI above. Weighting
  the fit toward high-sun hours was tried and rejected (no gain). Two ideas
  carry it: predict the clear-sky index so the model learns only weather (the
  sun's own motion is exact geometry), and one model per lead, since how far
  to trust "cloudy now" differs between 1 h and 12 h and a pooled linear
  model has one coefficient for all of them (1 h error 0.090 per-lead vs
  0.133 pooled, validation).

  **Caveats, stated not buried.**
  (1) The tiers tie on test: random forest vs linear -0.6% test MAE, gradient
  boosting vs random forest +0.7% (worse). The ~4% validation edge for trees
  does not survive to test, so linear is chosen on Pi Zero cost, not
  accuracy. Phase 12's benchmark can still overturn it.
  (2) Test is harder than validation: every ML tier is ~37-44% worse on test
  while the historical profile is not (0.2495 -> 0.2411). Validation covers
  Feb-Jul 2022 and test Jul-Dec 2022 (monsoon and after), so the margin over
  the best baseline shrinks from 49% to 27%. One test window, not a
  confidence interval.
  (3) At the 24 h lead the ML tiers barely beat persistence on test (0.188 vs
  0.199, 5%): day-ahead skill is thin here.
  (4) Reanalysis, hourly, one site. Not rooftop data.
  (5) The full command takes about 76 s (random forest and boosting fit 14
  models each); the tests do not run it.

  **Leak checks.** Target-time spans of the three splits are disjoint and
  chronological (`test_split_is_chronological_by_target_time_and_daylight_only`);
  rewriting every reading after the issue time leaves the feature vector
  unchanged at leads 1/6/12/24
  (`test_no_feature_ever_reads_the_future`); the historical profile is
  identical when val/test days are altered
  (`test_the_historical_profile_never_sees_validation_or_test_days`).

  **Carried into Phase 7:**
  - **The simulator cannot show forecast skill.** Its clouds are independent
    per slot, so in simulation a forecaster can only know "clear-sky times the
    average"; the persistence skill measured above does not exist there. Phase
    7's exit ("beats reactive on solar fraction in simulation") would
    understate a good forecast. Candidate fix: a simulator `SolarProfile` that
    replays the PVGIS series as a pure function of time. Needs the author's
    call.
  - Hourly leads 1-12 come from one fitted object; no `Forecast` producer
    exists yet (Phase 7 builds it; `forecast_value_at` already holds hourly
    points across 15-minute steps).
  - A live Pi needs at least 24 h of PV readings (`ksi_mean_24h`,
    `ksi_yesterday_at_target`); Phase 11/13 must supply them.

- [ ] **Phase 7 — Predictive heuristic**
  Combines demand + solar forecasts without full optimization.
  Exit: beats reactive scheduler on solar fraction in simulation.

- [ ] **Phase 8 — Optimization**
  MILP (OR-Tools/PuLP) with hard constraints + objective from section 30.
  Exit: solver runtime benchmarked; deterministic heuristic fallback exists
  if MILP proves too heavy.

- [ ] **Phase 9 — MPC**
  Receding-horizon replanning, execute-first-action-only loop.
  Exit: re-optimizes every cycle; event-driven re-optimization triggers
  (section 37) implemented and tested.

- [ ] **Phase 10 — Uncertainty**
  P10/P50/P90 forecasts, conservative scheduling (`Q_P90`, `PV_P10`),
  adaptive residual correction, anomaly detection.
  Exit: dynamic safety reserve scales with demand uncertainty; anomaly
  flag reduces scheduling aggressiveness in a test scenario.

- [ ] **Phase 11 — ESP32 integration**
  Newline-delimited JSON over USB serial; telemetry/command/ack protocol.
  Exit: command sent vs. executed tracked as separate states; integration
  tests for serial telemetry + ack pass.

- [ ] **Phase 12 — Raspberry Pi Zero deployment**
  Full inference + scheduler running locally.
  Exit: CPU/RAM/latency measured and within budget for the chosen model
  from Phase 5/8.

- [ ] **Phase 13 — Real tank validation**
  Controlled physical experiments.
  Exit: zero unintentional hard-constraint violations across a defined
  test window; measured vs. predicted tank trajectory logged.

- [ ] **Phase 14 — Experiment comparison**
  All controllers (A–F, section 58) run under identical conditions.
  Exit: comparison report distinguishes Measured/Simulated/Predicted;
  ablation study (section 59) completed.

- [ ] **Phase 15 — Minimal frontend**
  Live Home, Why?, Forecast, History, Experiments, Settings screens.
  Exit: every scheduler decision renders as a structured explanation
  (section 53 JSON → plain language), not raw parameters.

- [ ] **Phase 16 — Generalization**
  Second `FlexibleResource` implementation (e.g. washing machine or geyser
  as a stub/simulated resource) proves the interface generalizes.
  Exit: new resource type runs through the same scheduler code with zero
  changes to `scheduler/`.

---

## Before starting any phase, ask

Does this improve scheduling intelligence, model accuracy, system
robustness, experimental validity, hardware integration, observability, or
usability required to validate the core algorithm? If no — don't add it yet.
