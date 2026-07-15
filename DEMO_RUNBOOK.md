# Demo Runbook

Use this runbook after adding Bybit Demo Trading credentials to `.env`.

## 1. Configure credentials

Keep the environment explicit:

```env
TRADING_ENV=demo
SYMBOL=BTCUSDT
```

Then add:

```env
BYBIT_API_KEY=your_demo_trading_key
BYBIT_API_SECRET=your_demo_trading_secret
```

## 2. Start the demo API

```bash
bash scripts/start_demo.sh
```

This script forces `TRADING_ENV=demo` and uses `.env` for credentials.

Open:

```text
http://localhost:8081/docs
```

## 3. Run the sweep

In a second terminal:

```bash
python3 scripts/demo_sweep.py
```

The sweep checks health, status, config, validation, market data, metrics, profit, ML status, and one collect-only cycle.

## 4. Devpost demo recording path

1. Show `.env.example` safety defaults.
2. Start the API with `bash scripts/start_demo.sh`.
3. Open `/docs`.
4. Run `python3 scripts/demo_sweep.py`.
5. Show `/metrics/BTCUSDT`, `/validation`, and `/profit`.
6. Explain that Codex helped prepare the repo, fix configuration, and create reproducible judging docs.

## Environment note

There are only two active environments: `demo` for Bybit Demo Trading and `mainnet` for the live Bybit network.
