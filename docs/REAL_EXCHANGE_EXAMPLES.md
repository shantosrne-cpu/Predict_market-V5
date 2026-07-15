# Real Exchange and Execution Examples

This guide documents how the project behaves with real exchange data and how a full cycle is executed in practice.

## What the repository already contains

The project already includes real runtime artifacts in the repository:

- [logs/results.json](../logs/results.json) — a real result summary from executed trading cycles
- [data](../data) — local SQLite persistence for market data and trades
- [analisis](../analisis) — historical notes and debugging traces

## Example: real profit summary

The file [logs/results.json](../logs/results.json) shows a real example of a completed demo run. Key values include:

- Initial capital: 27639.672533 USDT
- Current capital: 81281.230802 USDT
- Net profit: 0.042495
- Win rate: 66.67%
- Total trades: 6

This demonstrates that the runtime can ingest live market events, produce trade outcomes, and persist them for inspection.

## How a cycle executes

A typical execution flow is:

1. The engine loads market state from Bybit.
2. Recent candles, order book state, and ticker data are aggregated.
3. Metrics are computed from that state.
4. The decision layer evaluates whether to buy, sell, or hold.
5. A collect-only run simply records the decision and stores the outcome.
6. A live execution path can place or manage orders through the exchange client.

## Example: collect-only execution

Run a safe one-shot cycle:

```bash
curl -X POST "http://localhost:8081/execute_trade/BTCUSDT?collect_only=true"
```

This produces a database-backed snapshot without sending an aggressive order.

## Example: HFT-style demo loop

Run a short bounded loop:

```bash
curl -X POST "http://localhost:8081/hft/run/BTCUSDT?cycles=20&interval_ms=500&collect_only=true"
```

This is ideal for judges because it shows fast-cycle evaluation and persistent metrics accumulation without taking large risk.

## Example: inspect the latest trade

```bash
curl http://localhost:8081/last_trade/BTCUSDT
```

The response includes fields such as:

- entry price
- exit price
- take profit and stop loss levels
- quantity
- profit/loss
- decision and signal metrics

## Example: inspect market and orderbook state

```bash
curl http://localhost:8081/ticker/BTCUSDT
curl http://localhost:8081/orderbook/BTCUSDT
curl http://localhost:8081/metrics/BTCUSDT
```

These endpoints show the engine's view of market reality and how the signal pipeline interprets it.

## Why this matters for the repo

Sharing examples like these helps others understand:

- how the engine reacts to real market data,
- how the metrics are produced,
- how the execution loop behaves,
- and why the system is useful for demoing and experimentation.
