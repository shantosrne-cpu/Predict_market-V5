# Project Status

Updated for DevPost submission preparation: 2026-07-14

## Current state

Nertz Metal Engine is a production-style FastAPI service for real-time crypto market analysis and guarded trading automation on Bybit V5. The current build ingests live spot market data, stores candles, order book snapshots, ticker and trade telemetry in SQLite, computes decision metrics, exposes API endpoints for inspection, and can run in safe collect-only mode or execute trades when explicitly configured.

## What works now

- FastAPI server with health, status, validation, market data, metrics, order book, profit, balance, ML, and order sync endpoints.
- Bybit V5 REST client with signed requests, retry handling, backoff, and order management helpers.
- WebSocket market ingestion for spot data.
- SQLite persistence for market data, order book snapshots, tickers, trades, metric snapshots, balances, and thresholds.
- Explicit environment selection: `TRADING_ENV=demo` for Bybit Demo Trading and `TRADING_ENV=mainnet` for live network use.
- Collect-only HFT runs for demo and testing without placing live orders.
- Optional ML training path based on finalized trade outcomes.

## Submission readiness

- Added a reproducible dependency list in `requirements.txt`.
- Added `.env.example` with Bybit Demo Trading defaults.
- Added polished setup instructions in `README.md`.
- Added a DevPost-ready narrative in `DEVPOST.md`.
- Added demo startup and sweep helpers in `scripts/` and `DEMO_RUNBOOK.md`.
- Fixed `.env` loading so runtime configuration resolves from the project root.
- Added the missing `CAPITAL_USDT` setting used by configuration and API responses.
- Replaced placeholder ignore rules with a cleaner Python/runtime/secret-aware `.gitignore`.

## Known limitations

- This is an API-first backend rather than a polished front-end dashboard.
- A complete demo depends on Bybit network access and valid Demo Trading credentials.
- The local SQLite database is runtime state and should not be treated as canonical training data.
- Trading functionality is experimental and should be judged as a decision-support and automation prototype, not as financial advice.

## Recommended demo path

1. Start with safe settings from `.env.example`.
2. Run the API locally.
3. Start the demo with `bash scripts/start_demo.sh`.
4. Run `python3 scripts/demo_sweep.py`.
5. Show `/status`, `/validation`, `/metrics/BTCUSDT`, `/discovery/metrics/BTCUSDT`, `/profit`, and `/ml/status`.
6. Trigger a collect-only cycle with `/execute_trade/BTCUSDT?collect_only=true`.
7. Explain how Codex was used to audit the repository state, fix reproducibility issues, and prepare judge-facing documentation.
