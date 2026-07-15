# API Reference

This document describes the main FastAPI endpoints exposed by Nertz Metal Engine and provides example requests for common workflows.

## Base URL

```text
http://localhost:8081
```

## Health and status

### GET /health
Returns the current health status of the API process.

```bash
curl http://localhost:8081/health
```

Example response:

```json
{
  "status": "healthy",
  "timestamp": "2026-07-15T02:41:01.114183+00:00"
}
```

### GET /status
Returns the bot runtime state and loop status.

```bash
curl http://localhost:8081/status
```

### GET /validation
Validates the health of the runtime pipeline: bot loop, websocket connection, DB freshness, and open orders tracking.

```bash
curl http://localhost:8081/validation
```

## Market data

### GET /market_data/{symbol}
Returns the most recent candle snapshots stored in the local database.

```bash
curl http://localhost:8081/market_data/BTCUSDT
```

### GET /ticker/{symbol}
Returns the latest ticker snapshot.

```bash
curl http://localhost:8081/ticker/BTCUSDT
```

### GET /candles/{symbol}/{limit}
Returns the latest N candles for a symbol.

```bash
curl http://localhost:8081/candles/BTCUSDT/10
```

### GET /orderbook/{symbol}
Returns the latest persisted order book snapshot.

```bash
curl http://localhost:8081/orderbook/BTCUSDT
```

### GET /trades/{symbol}
Returns trades tracked in the current runtime state.

```bash
curl http://localhost:8081/trades/BTCUSDT
```

## Metrics and decision signals

### GET /metrics/{symbol}
Computes the current metric bundle from recent candles, order book state, ticker context, and history.

```bash
curl http://localhost:8081/metrics/BTCUSDT
```

Example response snippet:

```json
{
  "symbol": "BTCUSDT",
  "metrics": {
    "combined": 4.5,
    "ild": 0.11,
    "egm": 0.42,
    "rol": 0.08,
    "pio": 0.3,
    "ogm": 0.22,
    "volatility": 0.005
  },
  "timestamp": "2026-07-15T02:41:01.114183+00:00"
}
```

### GET /combined/{symbol}
Returns a richer payload including candles, order book, ticker, recent trades, and timestamp.

```bash
curl http://localhost:8081/combined/BTCUSDT
```

### GET /ild/{symbol}
Returns the ILD estimate and associated component context.

```bash
curl http://localhost:8081/ild/BTCUSDT
```

### GET /rol/{symbol}
Returns the ROL metric estimate.

```bash
curl http://localhost:8081/rol/BTCUSDT
```

### GET /discovery/metrics/{symbol}
Returns the discovery-oriented metric bundle used for deeper analysis.

```bash
curl http://localhost:8081/discovery/metrics/BTCUSDT
```

## Trading execution

### POST /execute_trade/{symbol}
Runs a single core cycle for the given symbol. Use `collect_only=true` for safe demo execution.

```bash
curl -X POST "http://localhost:8081/execute_trade/BTCUSDT?collect_only=true"
```

### POST /hft/run/{symbol}
Schedules a bounded HFT-style loop for a symbol.

```bash
curl -X POST "http://localhost:8081/hft/run/BTCUSDT?cycles=20&interval_ms=500&collect_only=true"
```

### POST /hft/start/{symbol}
Starts an HFT loop.

```bash
curl -X POST "http://localhost:8081/hft/start/BTCUSDT?interval_ms=250&collect_only=true"
```

### POST /hft/stop/{symbol}
Stops the HFT loop.

```bash
curl -X POST "http://localhost:8081/hft/stop/BTCUSDT"
```

## Profit and balance

### GET /profit
Returns profit, loss, net profit, win rate, and per-symbol summary.

```bash
curl http://localhost:8081/profit
```

Example response snippet:

```json
{
  "capital_inicial": 27639.672533,
  "capital_actual": 81281.230802,
  "capital_source": "bybit_wallet_balance",
  "capital_pnl": 53641.558269,
  "total_profit": 0.058084,
  "total_loss": -0.015589,
  "net_profit": 0.042495,
  "win_rate": 66.67,
  "by_symbol": {
    "BTCUSDT": {
      "trade_count": 6,
      "net_profit": 0.042495
    }
  }
}
```

### GET /balance
Records and returns the current wallet balance snapshot.

```bash
curl http://localhost:8081/balance
```

## Configuration

### GET /config
Returns runtime configuration values and connectivity state.

```bash
curl http://localhost:8081/config
```

### POST /config/update_thresholds
Updates basic EGM thresholds.

```bash
curl -X POST "http://localhost:8081/config/update_thresholds?egm_buy_threshold=0.01&egm_sell_threshold=-0.01"
```

### POST /config/update_all
Updates a set of runtime config values.

```bash
curl -X POST http://localhost:8081/config/update_all -H "Content-Type: application/json" -d '{"capital_usdt": 5000, "risk_factor": 0.25}'
```

## Orders and exchange state

### GET /orders/status
Returns open order state from Bybit and correlates it with local DB records.

```bash
curl http://localhost:8081/orders/status
```

### POST /orders/sync
Synchronizes local DB order tracking with the exchange.

```bash
curl -X POST http://localhost:8081/orders/sync
```

### GET /exchange/open_orders/{symbol}
Returns raw open order payloads from the exchange.

```bash
curl http://localhost:8081/exchange/open_orders/BTCUSDT
```

## Machine learning and data export

### GET /ml/status
Returns the ML status and recent agent actions.

```bash
curl http://localhost:8081/ml/status
```

### GET /ml/dataset/trades
Exports finalized trade rows as JSON or CSV for downstream ML use.

```bash
curl "http://localhost:8081/ml/dataset/trades?limit=20&output=json"
```

### POST /ml/train
Triggers the ML training flow from the stored trades.

```bash
curl -X POST "http://localhost:8081/ml/train?min_samples=50"
```
