# SuryaSync

**A solar-aware residential flexible-load scheduling system.**

SuryaSync learns temporal household behaviour, forecasts future service demand and rooftop solar availability, quantifies the flexibility of controllable loads, and schedules those loads using uncertainty-aware Model Predictive Control (MPC).

> **The relay is not the innovation. The decision about when that relay should operate is the innovation.**

The current physical validation platform is an ordinary household overhead water tank + pump — used as the first testbed for a load-flexibility scheduling framework intended to generalize to washing machines, geysers, EV chargers, battery charging, and HVAC.

---

## The Core Idea

A household's real requirement is *"maintain sufficient water availability,"* not *"run the pump the instant level drops below X."* That gap between the two is **temporal flexibility** — and it can be exploited to shift electrical consumption toward periods of solar surplus without ever compromising the household service.

```text
Tank level: 47%      Expected demand before noon: low
Current solar: low   Expected solar at 11:30: high
Tank remains safe until 14:00

Decision: WAIT
→ At 11:30, solar surplus becomes sufficient → Decision: RUN
```

## Algorithm

```text
Observe
  → Build temporal context
  → Forecast household demand
  → Forecast solar generation
  → Forecast base electrical load
  → Estimate uncertainty
  → Predict future physical (tank) state
  → Quantify flexibility
  → Optimize schedule (MPC, receding horizon)
  → Validate hard safety constraints
  → Execute first action only
  → Observe again, re-optimize
```

**ML answers:** *what is likely to happen* (demand, solar, uncertainty).
**MPC/optimizer answers:** *what should happen now* (RUN / WAIT / STOP).
These layers are kept strictly separate.

## System Architecture

```mermaid
flowchart TD
    A[Rooftop Solar / Inverter] --> B[Raspberry Pi Zero]
    B --> C[Temporal Engine]
    B --> D[ML Forecasting]
    B --> E[State Model]
    C --> F[Flexibility Engine]
    D --> F
    E --> F
    F --> G[MPC / Optimizer]
    G --> H[Safety Validator]
    H --> I[Control Decision: RUN / WAIT / STOP]
    I --> J[ESP32]
    J --> K[Tank Sensor]
    J --> L[Safety Inputs]
    J --> M[Pump Output]
    M --> N[Isolated Driver] --> O[Contactor] --> P[Pump]
```

**Raspberry Pi Zero** — intelligence & scheduling: forecasting, uncertainty, flexibility estimation, MPC, logging, API.
**ESP32** — real-time hardware interface & local safety node: sensor sampling/filtering, pump control, heartbeat, local failsafe. No scheduling logic runs here by design.

## Status

Actively in development, following a 16-phase roadmap (see [`ROADMAP.md`](ROADMAP.md)).

| Phase | Description | Status |
|---|---|---|
| 0 | Architecture, interfaces, config, DB schema | ✅ Complete |
| 1 | Simulator (tank, pump, demand, solar) | 🚧 Next |
| 2 | Conventional threshold control | ⬜ Not started |
| 3 | Solar-reactive control | ⬜ Not started |
| 4–16 | Temporal learning → ML → MPC → hardware → frontend | ⬜ Not started |

## Repository Structure

```text
surya_sync/
├── hardware/     # ESP32 serial interface, command protocol
├── state/        # System state management
├── temporal/     # Time-aware feature engineering
├── ml/           # Demand, solar, base-load forecasting + uncertainty
├── models/       # Physical models: tank, pump, flexibility
├── scheduler/    # Threshold → reactive → heuristic → MPC (shared interface)
├── safety/       # Rule-based safety layer (runs before scheduler)
├── storage/      # SQLite persistence
├── analytics/    # Metrics, controller comparison
├── simulator/    # Simulated tank/solar/demand for algorithm dev
├── experiments/  # Reproducible experiment runner
├── api/          # FastAPI backend
├── frontend/     # Minimal monitoring/explainability UI
└── tests/
```

## Data Integrity

Results in this repo are explicitly labeled as **Measured**, **Simulated**, **Predicted**, or **Estimated**. Simulation results are never presented as physical results.

## Running Locally

Requires Python 3.11+ (for `tomllib`). Phase 0 has **no third-party runtime
dependencies** — deliberately, since everything eventually runs on a Pi Zero.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

.venv/bin/python -m surya_sync.main --show-config   # resolved config + hash
.venv/bin/python -m surya_sync.main --init-db       # create SQLite schema
.venv/bin/python -m pytest                          # test suite
```

Configuration lives in [`surya_sync/config/default.toml`](surya_sync/config/default.toml).
Copy it and pass `--config path/to/your.toml` for a real deployment rather than
editing the packaged defaults. Every resolved config is hashed, and the hash is
recorded with each run so any result traces back to the exact parameters that
produced it.

*(Simulator quickstart lands with Phase 1.)*

## License

TBD — add a `LICENSE` file (MIT recommended for portfolio visibility).
