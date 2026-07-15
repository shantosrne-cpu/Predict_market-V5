#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -f ".env" ]; then
  cp ".env.example" ".env"
  echo "Created .env from .env.example. Add Demo Trading BYBIT_API_KEY and BYBIT_API_SECRET before a full exchange demo."
fi

export TRADING_ENV=demo
export SYMBOL="${SYMBOL:-BTCUSDT}"

echo "Starting demo deployment:"
echo "  TRADING_ENV=$TRADING_ENV"
echo "  SYMBOL=$SYMBOL"
echo "  Using environment file: $ROOT_DIR/.env"

echo "Launching Nertz Metal Engine in demo mode..."
if [ -x ".venv/bin/python" ]; then
  .venv/bin/python src/Nertzh.py
else
  python3 src/Nertzh.py
fi
