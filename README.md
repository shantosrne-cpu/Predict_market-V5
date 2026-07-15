# Nertz Metal Engine

Nertz Metal Engine is a professional-grade crypto market intelligence and guarded trading automation platform for Bybit V5. It ingests live market data, stores telemetry in SQLite, computes signal metrics, and exposes a FastAPI interface for validation, observability, and controlled execution workflows.

The project was designed for real-world experimentation, hackathon demos, and future AI-assisted decision support. It combines low-latency market ingestion with a transparent decision trail so that every signal can be inspected, audited, and improved.

## Why this project exists

Crypto markets move quickly and are often too noisy to interpret manually. This system turns raw exchange activity into structured signals and persistent telemetry, making it easier to explore market behavior, evaluate decision logic, and run safe demo workflows without losing traceability.

## What it does

- Connects to Bybit V5 through REST and WebSocket interfaces.
- Ingests candles, order book snapshots, tickers, trades, balances, and threshold state.
- Computes decision metrics such as combined signal, ILD, EGM, ROL, PIO, OGM, volatility, and regime-aware confidence indicators.
- Stores market and trading history locally in SQLite for inspection and replay.
- Exposes FastAPI endpoints for status checks, validation, metrics, profit review, balance tracking, ML export, and collect-only execution.
- Supports both Bybit Demo Trading and mainnet environments through explicit configuration.

## Highlights

- Real-time market data ingestion and persistence
- Auditable signal pipeline and metrics layer
- Guardrails for demo-safe execution
- API-first architecture designed for judges, demos, and future automation
- Reproducible local setup with clear documentation

## Tech stack

- Python
- FastAPI
- SQLAlchemy
- SQLite
- aiohttp / WebSockets
- NumPy

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
bash scripts/start_demo.sh
```

The API will be available at:

```text
http://localhost:8081
```

Interactive API docs:

```text
http://localhost:8081/docs
```

## Configuration

Copy the example environment file and set the runtime mode:

```bash
cp .env.example .env
```

Example:

```env
TRADING_ENV=demo
```

Use `TRADING_ENV=mainnet` only when you intend to connect to the live Bybit network.

## Demo flow for judges

After starting the API, these endpoints are useful for inspection:

```text
GET /status
GET /validation
GET /metrics/BTCUSDT
GET /discovery/metrics/BTCUSDT
GET /profit
GET /ml/status
```

Run one safe collect-only cycle:

```bash
curl -X POST "http://localhost:8081/execute_trade/BTCUSDT?collect_only=true"
```

Run a bounded HFT collect-only sample:

```bash
curl -X POST "http://localhost:8081/hft/run/BTCUSDT?cycles=20&interval_ms=500&collect_only=true"
```

Or use the prepared helpers:

```bash
bash scripts/start_demo.sh
python3 scripts/demo_sweep.py
```

See [DEMO_RUNBOOK.md](DEMO_RUNBOOK.md) for the full recording flow.

## Project structure

- [src](src) — engine, settings, utilities, and model logic
- [scripts](scripts) — demo helpers and startup scripts
- [data](data) — runtime and persistence artifacts
- [logs](logs) — execution and result logs
- [analisis](analisis) — historical notes and debugging material

## Related materials

- Project status: [PROJECT_STATUS.md](PROJECT_STATUS.md)
- DevPost submission draft: [DEVPOST.md](DEVPOST.md)
- Demo runbook: [DEMO_RUNBOOK.md](DEMO_RUNBOOK.md)
- API reference: [docs/API_REFERENCE.md](docs/API_REFERENCE.md)
- Real exchange examples: [docs/REAL_EXCHANGE_EXAMPLES.md](docs/REAL_EXCHANGE_EXAMPLES.md)
- Environment template: [.env.example](.env.example)

## Notes

This is experimental software for hackathon and demo use. It is not financial advice. For judging and demonstrations, use Bybit Demo Trading credentials and keep trading activity limited to safe, collect-only flows.
