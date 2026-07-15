# Devpost Submission Draft

## Project name

Nertz Metal Engine

## One-liner

A real-time crypto market intelligence engine that turns Bybit orderbook, ticker, candle, and trade flow into auditable trading signals across Bybit Demo Trading and mainnet.

## Inspiration

Fast-moving crypto markets are hard to inspect manually. The project focuses on a practical problem: giving a solo trader or builder a transparent, API-first system that can collect market microstructure data, calculate decision metrics, and expose the reasoning trail before any automated action is allowed.

## What it does

Nertz Metal Engine runs a FastAPI backend that connects to Bybit V5, ingests live market data, stores snapshots in SQLite, computes signal metrics, tracks trades/outcomes, and exposes endpoints for validation, profit review, ML dataset export, threshold calibration, and order synchronization.

For hackathon demos, it is designed to run against Bybit Demo Trading via `TRADING_ENV=demo`. Mainnet requires explicitly setting `TRADING_ENV=mainnet`.

## Build Week strategy

While the hackathon is running, the project will continue evolving in short, judge-friendly iterations:

- keep the demo experience simple and reproducible,
- improve the clarity of the signal outputs and metric explanations,
- add stronger safety checks and automated tests,
- keep the repository and Devpost materials aligned with the latest progress.

This is a practical AI-assisted builder workflow: a real backend, real exchange integration, and a polished path for judging and future extension.

## How we built it

- Python and FastAPI for the API surface.
- SQLite and SQLAlchemy for local persistence.
- aiohttp and websockets for Bybit REST/websocket integration.
- NumPy-based metric calculations and lightweight ML helpers.
- Codex was used to inspect the project state, identify reproducibility gaps, fix configuration loading, add missing runtime settings, and create judge-ready docs and setup files.

## OpenAI / Codex usage

Codex helped prepare the project for OpenAI Build Week by:

- Auditing the current repository layout and runtime entrypoints.
- Identifying missing setup assets required for a judge to run the project.
- Fixing `.env` discovery so local configuration is loaded from the repository root.
- Adding the missing `CAPITAL_USDT` configuration used by the API.
- Creating `.env.example`, `requirements.txt`, `README.md`, `PROJECT_STATUS.md`, and this Devpost draft.

If adding GPT-5.6 before final submission, the best fit is an explanation layer over the metrics: summarize why a signal is buy/sell/hold, generate a natural-language risk note, and produce a demo narration from the latest `/metrics`, `/validation`, and `/profit` responses.

## Challenges

The biggest challenge is keeping environment semantics simple. The current build chooses only between Bybit Demo Trading and mainnet.

## Accomplishments

- Real-time market data ingestion.
- Persistent market and trade telemetry.
- API endpoints for validation and observability.
- Guardrails around live trading.
- A reproducible local setup path for judges.

## What is next

- Add a small dashboard over the FastAPI endpoints.
- Add GPT-5.6 powered signal explanations and demo summaries.
- Add automated tests around configuration safety, metric calculations, and order execution gates.
- Package a short sample dataset for offline judging without exchange credentials.

## Demo checklist

- Repository is clean of secrets.
- `.env.example` is present.
- Dependencies install with `pip install -r requirements.txt`.
- API starts with `bash scripts/start_demo.sh`.
- Sweep runs with `python3 scripts/demo_sweep.py`.
- Show `http://localhost:8081/docs`.
- Show safe endpoints: `/status`, `/validation`, `/metrics/BTCUSDT`, `/profit`, `/ml/status`.
