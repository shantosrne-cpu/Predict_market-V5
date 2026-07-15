import asyncio
import csv
import io
import json
import logging
import os
import time
import uuid
import re
from collections import deque
from contextlib import asynccontextmanager
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, ROUND_UP
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

import aiohttp
import numpy as np
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import create_engine, Integer, String, Float, DateTime, JSON, text
from sqlalchemy.orm import sessionmaker, Session, declarative_base, Mapped, mapped_column

# Importaciones corregidas
from settings import ConfigSettings
from utils import (
    calculate_metrics,
    calculate_discovery_metrics,
    save_results,
    append_results_event,
    load_results_json,
    timestamp_to_datetime,
    calculate_tp_sl,
)
from bybit_v5 import BybitV5Client

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

# Cargar variables desde el archivo .env
load_dotenv(dotenv_path=os.path.join(PROJECT_ROOT, ".env"), override=False)

# Instanciar ConfigSettings
config = ConfigSettings()

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("NertzMetalEngine")

# URL y base de datos
BASE_URL = config.REST_BASE_URL
WS_URL = config.WS_PUBLIC_URL

DATABASE_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
os.makedirs(DATABASE_DIR, exist_ok=True)
DATABASE_URL = os.path.join(DATABASE_DIR, 'trading.db')

engine = create_engine(f"sqlite:///{DATABASE_URL}", connect_args={"check_same_thread": False})
Base = declarative_base()


# Modelos de la base de datos
class MarketData(Base):
    __tablename__ = "market_data"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    high: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    low: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    close: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    volume: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


class Orderbook(Base):
    __tablename__ = "orderbook"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    bids: Mapped[list[Any]] = mapped_column(JSON, nullable=False)
    asks: Mapped[list[Any]] = mapped_column(JSON, nullable=False)


class MarketTicker(Base):
    __tablename__ = "market_ticker"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    last_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    volume_24h: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    high_24h: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    low_24h: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


class Trade(Base):
    __tablename__ = "trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    trade_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String, nullable=False)
    order_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True, index=True)
    bybit_raw: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    exit_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    tp_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sl_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    profit_loss: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    outcome_status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    outcome_timestamp: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    decision: Mapped[str] = mapped_column(String, nullable=False)
    combined: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    ild: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    egm: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    rol: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    pio: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    ogm: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    risk_reward_ratio: Mapped[float] = mapped_column(Float, nullable=False, default=1.5)


class MetricSnapshot(Base):
    __tablename__ = "metric_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    last_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    decision: Mapped[str] = mapped_column(String, nullable=False, default="hold")
    combined: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    ild: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    egm: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    rol: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    pio: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    ogm: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    volatility: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    thresholds: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class BalanceSnapshot(Base):
    __tablename__ = "balance_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    account_type: Mapped[str] = mapped_column(String(20), nullable=False, default="UNIFIED")
    coin: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    total_equity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    available_balance: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class ThresholdSnapshot(Base):
    __tablename__ = "threshold_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    egm_buy_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    egm_sell_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    combined_buy_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    combined_sell_threshold: Mapped[float] = mapped_column(Float, nullable=False)
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


Base.metadata.create_all(bind=engine)

def _persist_thresholds_to_env(env_path: str) -> Dict[str, Any]:
    before = {
        "EGM_BUY_THRESHOLD": float(config.EGM_BUY_THRESHOLD),
        "EGM_SELL_THRESHOLD": float(config.EGM_SELL_THRESHOLD),
        "COMBINED_BUY_THRESHOLD": float(getattr(config, "COMBINED_BUY_THRESHOLD", 2.0)),
        "COMBINED_SELL_THRESHOLD": float(getattr(config, "COMBINED_SELL_THRESHOLD", -2.0)),
    }
    try:
        env_path = os.path.abspath(env_path)
        if not os.path.exists(env_path):
            return {"success": False, "message": "env_not_found", "path": env_path, "values": before}

        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()

        values = {
            "EGM_BUY_THRESHOLD": str(before["EGM_BUY_THRESHOLD"]),
            "EGM_SELL_THRESHOLD": str(before["EGM_SELL_THRESHOLD"]),
            "COMBINED_BUY_THRESHOLD": str(before["COMBINED_BUY_THRESHOLD"]),
            "COMBINED_SELL_THRESHOLD": str(before["COMBINED_SELL_THRESHOLD"]),
        }

        keys = list(values.keys())
        patterns = {k: re.compile(rf"^\s*{re.escape(k)}\s*=") for k in keys}

        found = {k: False for k in keys}
        new_lines: list[str] = []
        for line in lines:
            replaced = False
            for k in keys:
                if patterns[k].match(line):
                    new_lines.append(f"{k}={values[k]}")
                    found[k] = True
                    replaced = True
                    break
            if not replaced:
                new_lines.append(line)

        for k in keys:
            if not found[k]:
                new_lines.append(f"{k}={values[k]}")

        with open(env_path, "w", encoding="utf-8") as f:
            f.write("\n".join(new_lines) + "\n")

        return {"success": True, "path": env_path, "values": before}
    except Exception as e:
        return {"success": False, "message": str(e), "path": env_path, "values": before}


def _ensure_sqlite_columns(table: str, desired: Dict[str, str]) -> None:
    with engine.begin() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
        existing = {row[1] for row in rows} if rows else set()
        for name, type_sql in desired.items():
            if name in existing:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {type_sql}"))


_ensure_sqlite_columns(
    "trades",
    {
        "order_id": "TEXT",
        "bybit_raw": "TEXT",
        "tp_price": "REAL",
        "sl_price": "REAL",
        "outcome_status": "TEXT DEFAULT 'pending'",
        "outcome_timestamp": "DATETIME",
    },
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# Dependencia para la base de datos
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Función para obtener datos de la API
async def fetch_data(session, url, params=None):
    async with session.get(url, params=params) as response:
        if response.status == 200:
            return await response.json()
        logger.error(f"❌ Error en {url}: {response.status}")
        return None


def timeframe_to_bybit_interval(timeframe: str) -> str:
    mapping = {
        "1m": "1",
        "3m": "3",
        "5m": "5",
        "15m": "15",
        "30m": "30",
        "1h": "60",
        "2h": "120",
        "4h": "240",
        "6h": "360",
        "12h": "720",
        "1d": "D",
    }
    return mapping.get(timeframe, timeframe.replace("m", ""))


def _resolve_capital_inicial(prev_initial: Any, capital_source: str, capital_actual: float) -> float:
    if isinstance(prev_initial, (int, float)) and float(prev_initial) > 0:
        return float(prev_initial)
    if capital_source == "bybit_wallet_balance":
        return float(capital_actual)
    return float(config.CAPITAL_USDT)
 

# Función para actualizar orderbook
def _update_orderbook(bid_dict, ask_dict, data):
    for price, qty in data["data"]["b"]:
        price = float(price)
        qty = float(qty)
        if qty > 0:
            bid_dict[price] = qty
        elif price in bid_dict:
            del bid_dict[price]
    for price, qty in data["data"]["a"]:
        price = float(price)
        qty = float(qty)
        if qty > 0:
            ask_dict[price] = qty
        elif price in ask_dict:
            del ask_dict[price]


class NertzMetalEngine:
    def __init__(self) -> None:
        self.timeframe = config.TIMEFRAME
        self.symbols = config.SYMBOL.split(",")
        self.capital = 0.0
        self.positions = {symbol: [] for symbol in self.symbols}
        self.iterations = 0
        self.ws = None
        self.running = True
        self.orderbook_data = {symbol: {"bids": [], "asks": []} for symbol in self.symbols}
        self.ticker_data = {symbol: {"last_price": 0.0, "volume_24h": 0.0, "high_24h": 0.0, "low_24h": 0.0} for symbol
                            in self.symbols}
        self.candles = {symbol: [] for symbol in self.symbols}
        self.trade_id_counter = self._load_initial_trade_id()
        self._load_positions()
        self.last_orderbook_log = 0
        self.last_trade_time = {symbol: datetime.min.replace(tzinfo=timezone.utc) for symbol in self.symbols}
        self.hft_tasks: Dict[str, asyncio.Task] = {}
        self._last_tune_ts = 0.0
        self._last_metrics_json_ts: Dict[str, float] = {symbol: 0.0 for symbol in self.symbols}
        self._last_balance_sync_ts = 0.0
        self.instrument_rules: Dict[str, Dict[str, float]] = {}
        self._instrument_rules_ts: Dict[str, float] = {}
        self._start_task: Optional[asyncio.Task] = None
        self._core_cycle_locks: Dict[str, asyncio.Lock] = {}
        self.order_status: Dict[str, Dict[str, Any]] = {}
        self._support_task: Optional[asyncio.Task] = None
        self._support_interval_s = 1.0
        self._last_orders_sync_ts = 0.0
        self._orders_sync_lock = asyncio.Lock()
        self._metrics_raw_history: Dict[str, Any] = {symbol: deque() for symbol in self.symbols}
        self._last_weighted_liquidity: Dict[str, Any] = {symbol: None for symbol in self.symbols}
        self.recent_trades: Dict[str, Any] = {symbol: deque(maxlen=500) for symbol in self.symbols}
        self._bybit: Optional[BybitV5Client] = None
        self._ml_models: Dict[str, Dict[str, Any]] = {}
        self._ml_last_train_ts: Dict[str, float] = {}
        self._ml_lock = asyncio.Lock()
        self._agent_last_tick_ts = 0.0
        self._agent_events: Dict[str, Any] = {"actions": deque(maxlen=250)}

    async def initialize_capital(self):
        try:
            balance_result = await self.record_balance()
            if balance_result.get("success"):
                self.capital = balance_result["balance"]["total_equity"]
                logger.info(f"💰 Capital inicial obtenido de Bybit: {self.capital:.2f} USDT")
            else:
                logger.error("❌ No se pudo obtener el capital inicial de Bybit. Usando 0.0.")
                self.capital = 0.0
        except Exception as e:
            logger.error(f"❌ Error al obtener el capital inicial de Bybit: {e}")
            self.capital = 0.0

    def _outcome_horizon_seconds(self) -> int:
        try:
            return max(10, int(config.DEFAULT_SLEEP_TIME))
        except Exception:
            return 10

    def _normalize_outcome_status(self, value: Any) -> str:
        if isinstance(value, str) and value.strip():
            return value
        return "legacy"

    @staticmethod
    def _ml_sigmoid(z: np.ndarray) -> np.ndarray:
        zc = np.clip(z, -50.0, 50.0)
        return 1.0 / (1.0 + np.exp(-zc))

    @staticmethod
    def _ml_action_sign(action: str) -> float:
        a = (action or "").lower()
        if a == "buy":
            return 1.0
        if a == "sell":
            return -1.0
        return 0.0

    def _ml_feature_names(self) -> list[str]:
        return ["action_sign", "combined", "ild", "egm", "rol", "pio", "ogm", "risk_reward_ratio"]

    def _ml_extract_features(self, action: str, metrics: Dict[str, Any]) -> np.ndarray:
        rr = float(config.TP_PERCENTAGE) / float(config.SL_PERCENTAGE) if float(config.SL_PERCENTAGE or 0.0) > 0 else 0.0
        v = np.array(
            [
                self._ml_action_sign(action),
                float(metrics.get("combined", 0.0) or 0.0),
                float(metrics.get("ild", 0.0) or 0.0),
                float(metrics.get("egm", 0.0) or 0.0),
                float(metrics.get("rol", 0.0) or 0.0),
                float(metrics.get("pio", 0.0) or 0.0),
                float(metrics.get("ogm", 0.0) or 0.0),
                float(metrics.get("risk_reward_ratio", rr) or rr),
            ],
            dtype=np.float64,
        )
        return v

    def train_ml_model_from_trades(
        self,
        db: Session,
        *,
        symbol: Optional[str] = None,
        min_samples: Optional[int] = None,
        epochs: int = 250,
        lr: float = 0.15,
        l2: float = 0.02,
    ) -> Dict[str, Any]:
        ms = int(min_samples) if min_samples is not None else int(getattr(config, "ML_MIN_SAMPLES", 150) or 150)
        q = db.query(Trade).filter(Trade.outcome_status == "final")
        if isinstance(symbol, str) and symbol:
            q = q.filter(Trade.symbol == symbol)
        trades = q.order_by(Trade.timestamp.desc()).limit(max(ms * 50, 500)).all()
        if not trades or len(trades) < ms:
            return {"success": False, "message": "insufficient_samples", "samples": len(trades or [])}

        feats: list[np.ndarray] = []
        labels: list[float] = []
        for t in trades:
            pl = float(getattr(t, "profit_loss", 0.0) or 0.0)
            y = 1.0 if pl > 0 else 0.0
            x = np.array(
                [
                    self._ml_action_sign(getattr(t, "action", "")),
                    float(getattr(t, "combined", 0.0) or 0.0),
                    float(getattr(t, "ild", 0.0) or 0.0),
                    float(getattr(t, "egm", 0.0) or 0.0),
                    float(getattr(t, "rol", 0.0) or 0.0),
                    float(getattr(t, "pio", 0.0) or 0.0),
                    float(getattr(t, "ogm", 0.0) or 0.0),
                    float(getattr(t, "risk_reward_ratio", 0.0) or 0.0),
                ],
                dtype=np.float64,
            )
            if not np.all(np.isfinite(x)):
                continue
            feats.append(x)
            labels.append(y)

        if len(feats) < ms:
            return {"success": False, "message": "insufficient_clean_samples", "samples": len(feats)}

        X = np.vstack(feats)
        yv = np.array(labels, dtype=np.float64)
        mu = X.mean(axis=0)
        sigma = X.std(axis=0)
        sigma = np.where(sigma > 1e-9, sigma, 1.0)
        Xn = (X - mu) / sigma
        Xb = np.concatenate([np.ones((Xn.shape[0], 1), dtype=np.float64), Xn], axis=1)

        w = np.zeros((Xb.shape[1],), dtype=np.float64)
        n = float(Xb.shape[0])
        for _ in range(int(max(10, epochs))):
            p = self._ml_sigmoid(Xb @ w)
            grad = (Xb.T @ (p - yv)) / n
            grad[1:] = grad[1:] + float(l2) * w[1:]
            w = w - float(lr) * grad

        p_final = self._ml_sigmoid(Xb @ w)
        pred = (p_final >= 0.5).astype(np.float64)
        acc = float((pred == yv).mean()) if yv.size else 0.0

        key = symbol or "__all__"
        self._ml_models[key] = {
            "features": self._ml_feature_names(),
            "mu": mu.tolist(),
            "sigma": sigma.tolist(),
            "w": w.tolist(),
            "samples": int(Xb.shape[0]),
            "accuracy_train": acc,
            "trained_at": datetime.now(timezone.utc).isoformat(),
        }
        self._ml_last_train_ts[key] = time.time()
        return {"success": True, "key": key, "model": self._ml_models[key]}

    def ml_predict_proba(self, *, symbol: str, action: str, metrics: Dict[str, Any]) -> Optional[float]:
        key = symbol if symbol in self._ml_models else "__all__"
        model = self._ml_models.get(key)
        if not isinstance(model, dict):
            return None
        try:
            mu = np.array(model.get("mu") or [], dtype=np.float64)
            sigma = np.array(model.get("sigma") or [], dtype=np.float64)
            w = np.array(model.get("w") or [], dtype=np.float64)
            if mu.size == 0 or sigma.size == 0 or w.size == 0:
                return None
            x = self._ml_extract_features(action, metrics)
            if x.size != mu.size:
                return None
            xn = (x - mu) / np.where(sigma > 1e-9, sigma, 1.0)
            xb = np.concatenate([np.ones((1,), dtype=np.float64), xn], axis=0)
            if xb.size != w.size:
                return None
            p = float(self._ml_sigmoid(xb @ w))
            if not np.isfinite(p):
                return None
            return p
        except Exception:
            return None

    async def _agent_tick(self, db: Session) -> None:
        now_ts = time.time()
        if now_ts - float(self._agent_last_tick_ts or 0.0) < 0.5:
            return
        self._agent_last_tick_ts = now_ts

        actions = self._agent_events.get("actions")
        if not isinstance(actions, deque):
            actions = deque(maxlen=250)
            self._agent_events["actions"] = actions

        start_task = getattr(self, "_start_task", None)
        if self.running and (start_task is None or getattr(start_task, "done", lambda: True)()):
            ok = self.schedule_start()
            if ok:
                actions.append({"type": "restart_start_task", "ts": datetime.now(timezone.utc).isoformat()})

        if bool(getattr(config, "ML_ENABLED", False)):
            interval_s = float(getattr(config, "AUTO_AGENT_TRAIN_INTERVAL_MIN", 30.0) or 30.0) * 60.0
            last_train = float(self._ml_last_train_ts.get("__all__", 0.0) or 0.0)
            if now_ts - last_train >= interval_s:
                res = self.train_ml_model_from_trades(db, symbol=None)
                actions.append(
                    {
                        "type": "ml_train",
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "success": bool(res.get("success")),
                        "samples": ((res.get("model") or {}).get("samples") if isinstance(res.get("model"), dict) else None),
                    }
                )

    async def _finalize_due_outcomes(self, db: Session, symbol: str, exit_price: float) -> Optional[Trade]:
        if exit_price <= 0:
            return None
        horizon = self._outcome_horizon_seconds()
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=horizon)
        pending = (
            db.query(Trade)
            .filter(Trade.symbol == symbol)
            .filter(Trade.timestamp <= cutoff)
            .filter(~Trade.outcome_status.in_(["final", "cancelled", "invalid_entry"]))
            .order_by(Trade.timestamp.asc())
            .limit(50)
            .all()
        )
        if not pending:
            return None

        last_finalized: Optional[Trade] = None
        fee_factor = 1 - float(config.FEE_RATE)
        now = datetime.now(timezone.utc)
        for t in pending:
            status = self._normalize_outcome_status(getattr(t, "outcome_status", None))
            if status == "final":
                continue
            entry = float(getattr(t, "entry_price", 0.0) or 0.0)
            qty = float(getattr(t, "quantity", 0.0) or 0.0)
            raw = getattr(t, "bybit_raw", None)
            if isinstance(raw, dict):
                order_info = raw.get("order_realtime") or raw.get("order_history") or {}
                if isinstance(order_info, dict):
                    try:
                        avg_price = float(order_info.get("avgPrice") or 0.0)
                    except Exception:
                        avg_price = 0.0
                    try:
                        cum_exec_qty = float(order_info.get("cumExecQty") or 0.0)
                    except Exception:
                        cum_exec_qty = 0.0
                    if avg_price > 0:
                        entry = avg_price
                    if cum_exec_qty > 0:
                        qty = cum_exec_qty
            if entry <= 0 or qty <= 0:
                t.outcome_status = "invalid_entry"
                t.outcome_timestamp = now
                continue
            if t.action == "buy":
                pnl = (exit_price - entry) * qty * fee_factor
            else:
                pnl = (entry - exit_price) * qty * fee_factor
            t.exit_price = float(exit_price)
            t.profit_loss = float(pnl)
            t.outcome_status = "final"
            t.outcome_timestamp = now
            last_finalized = t

        if last_finalized is not None:
            db.commit()
        return last_finalized

    def schedule_start(self) -> bool:
        if self._start_task and not self._start_task.done():
            return False
        self.running = True
        self._start_task = asyncio.create_task(self.start_async())
        self.start_support_loop(interval_s=self._support_interval_s)
        return True

    def _load_initial_trade_id(self):
        with SessionLocal() as db:
            last_trade = db.query(Trade.trade_id).order_by(Trade.trade_id.desc()).first()
            return last_trade[0] + 1 if last_trade else 1

    def _load_positions(self):
        with SessionLocal() as db:
            for symbol in self.symbols:
                trades = db.query(Trade).filter_by(symbol=symbol).order_by(Trade.timestamp.desc()).all()
                self.positions[symbol] = [{
                    "trade_id": t.trade_id,
                    "timestamp": t.timestamp.isoformat(),
                    "symbol": t.symbol,
                    "action": t.action,
                    "order_id": getattr(t, "order_id", None),
                    "entry_price": t.entry_price,
                    "exit_price": (t.exit_price if (getattr(t, "outcome_status", None) == "final") else None),
                    "tp_price": getattr(t, "tp_price", None),
                    "sl_price": getattr(t, "sl_price", None),
                    "quantity": t.quantity,
                    "profit_loss": (t.profit_loss if (getattr(t, "outcome_status", None) == "final") else None),
                    "outcome_status": getattr(t, "outcome_status", None) or "legacy",
                    "outcome_timestamp": t.outcome_timestamp.isoformat() if isinstance(t.outcome_timestamp, datetime) else None,
                    "decision": t.decision,
                    "combined": t.combined,
                    "ild": t.ild,
                    "egm": t.egm,
                    "rol": t.rol,
                    "pio": t.pio,
                    "ogm": t.ogm,
                    "risk_reward_ratio": t.risk_reward_ratio
                } for t in trades]

    async def fetch_initial_data(self):
        async with aiohttp.ClientSession() as session:
            tasks = [self._fetch_symbol_data(session, symbol) for symbol in self.symbols]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"❌ Error al obtener datos iniciales: {result}")

    async def _fetch_symbol_data(self, session, symbol):
        try:
            kline_url = f"{BASE_URL}/v5/market/kline"
            interval = timeframe_to_bybit_interval(self.timeframe)
            params = {"category": "spot", "symbol": symbol, "interval": interval, "limit": 50}
            kline_response = await fetch_data(session, kline_url, params)
            if kline_response and "result" in kline_response and "list" in kline_response["result"]:
                candles = [
                    MarketData(
                        timestamp=timestamp_to_datetime(int(k[0])),
                        symbol=symbol,
                        open=float(k[1]),
                        high=float(k[2]),
                        low=float(k[3]),
                        close=float(k[4]),
                        volume=float(k[5])
                    ) for k in kline_response["result"]["list"]
                ]
                with SessionLocal() as db:
                    for candle in candles:
                        if not db.query(MarketData).filter_by(timestamp=candle.timestamp, symbol=symbol).first():
                            db.add(candle)
                    db.commit()
                logger.info(f"📈 Velas iniciales para {symbol}: {len(candles)}")
            else:
                logger.error(f"❌ Kline inesperado para {symbol}: {kline_response}")

            orderbook_url = f"{BASE_URL}/v5/market/orderbook"
            params = {"category": "spot", "symbol": symbol, "limit": 100}
            orderbook_response = await fetch_data(session, orderbook_url, params)
            if orderbook_response and "result" in orderbook_response:
                depth = int(getattr(config, "ORDERBOOK_DEPTH", 50) or 50)
                depth = max(1, min(depth, 50))
                self.orderbook_data[symbol] = {
                    "bids": (orderbook_response["result"].get("b") or [])[:depth],
                    "asks": (orderbook_response["result"].get("a") or [])[:depth],
                }
                logger.info(
                    f"📊 Orderbook inicial para {symbol}: Bids={len(self.orderbook_data[symbol]['bids'])}, Asks={len(self.orderbook_data[symbol]['asks'])}")
            else:
                logger.error(f"❌ Orderbook inesperado para {symbol}: {orderbook_response}")

            ticker_url = f"{BASE_URL}/v5/market/tickers"
            params = {"category": "spot", "symbol": symbol}
            ticker_response = await fetch_data(session, ticker_url, params)
            if ticker_response and "result" in ticker_response and "list" in ticker_response["result"]:
                ticker_data = ticker_response["result"]["list"][0]
                self.ticker_data[symbol] = {
                    "last_price": float(ticker_data["lastPrice"]),
                    "volume_24h": float(ticker_data["volume24h"]),
                    "high_24h": float(ticker_data["highPrice24h"]),
                    "low_24h": float(ticker_data["lowPrice24h"])
                }
                logger.info(f"⚡ Ticker inicial para {symbol}: {self.ticker_data[symbol]['last_price']}")
            else:
                logger.error(f"❌ Ticker inesperado para {symbol}: {ticker_response}")
        except Exception as e:
            logger.error(f"❌ Fetch inicial falló para {symbol}: {e}")

    async def start_async(self):
        logger.info(f"🔥 Iniciando bot para {self.symbols}")
        try:
            preflight = await self.preflight()
            if not preflight.get("success"):
                logger.error(f"❌ Preflight falló: {preflight.get('message') or 'error'}")
                return
        except Exception as e:
            logger.error(f"❌ Preflight falló: {e}")
            return
        max_initial_attempts = 3
        for attempt in range(max_initial_attempts):
            if not self.running:
                logger.info("🛑 Bot detenido antes de iniciar.")
                return
            try:
                await self.fetch_initial_data()
                break
            except Exception as e:
                logger.error(f"❌ Error al obtener datos iniciales (intento {attempt + 1}/{max_initial_attempts}): {e}")
                await asyncio.sleep(min(10, 2 ** attempt))

        if not self.running:
            return
        await self._connect_websocket_async()

    async def preflight(self) -> Dict[str, Any]:
        return {
            "success": True,
            "trading_env": config.TRADING_ENV,
            "rest_base_url": config.REST_BASE_URL,
            "ws_public_url": config.WS_PUBLIC_URL,
            "credentials_configured": bool(config.BYBIT_API_KEY and config.BYBIT_API_SECRET),
        }

    async def _connect_websocket_async(self):
        import websockets
        while self.running:
            try:
                async with websockets.connect(WS_URL) as ws:
                    self.ws = ws
                    logger.info("🌐 WebSocket abierto")
                    await self._resubscribe_async()
                    async for message in ws:
                        await self._on_message(ws, message)
            except websockets.ConnectionClosed as e:
                logger.warning(f"⚠️ WebSocket cerrado: {e}, intentando reconectar en 5s...")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"❌ Error en WebSocket: {e}")
                await asyncio.sleep(5)

    async def _resubscribe_async(self):
        interval = timeframe_to_bybit_interval(self.timeframe)
        for symbol in self.symbols:
            subscription = {"op": "subscribe",
                            "args": [f"kline.{interval}.{symbol}", f"orderbook.50.{symbol}", f"tickers.{symbol}", f"publicTrade.{symbol}"]}
            if self.ws:
                await self.ws.send(json.dumps(subscription))
                logger.info(f"📡 Suscrito a {symbol}")

    async def _on_message(self, ws, message):
        with SessionLocal() as db:
            try:
                if isinstance(message, bytes):
                    message = message.decode('utf-8')
                elif isinstance(message, tuple):
                    message = message[0]
                elif message is None:
                    logger.warning("⚠️ Mensaje recibido es None, ignorando.")
                    return

                if isinstance(message, str):
                    data = json.loads(message)
                    logger.debug(f"📨 Mensaje procesado: {json.dumps(data, indent=2)}")

                    if "topic" not in data:
                        logger.debug("⚠️ Mensaje sin tema ('topic'), posiblemente ping/pong.")
                        if data.get("op") == "ping" and ws is not None:
                            await ws.send(json.dumps({"op": "pong", "ts": data.get("ts", int(time.time() * 1000))}))
                        return

                    symbol = data["topic"].split(".")[-1]
                    if symbol not in self.symbols:
                        logger.warning(f"⚠️ Símbolo desconocido: {symbol}")
                        return

                    if "kline" in data["topic"] and data.get("data") and len(data["data"]) > 0:
                        await self._handle_kline(symbol, data["data"][0], db)
                    elif "orderbook" in data["topic"] and data.get("data"):
                        await self._handle_orderbook(symbol, data, db)
                    elif "tickers" in data["topic"] and data.get("data"):
                        await self._handle_ticker(symbol, data["data"], db)
                    elif "publicTrade" in data["topic"] and data.get("data"):
                        await self._handle_public_trade(symbol, data.get("data"), db)
                    else:
                        logger.warning(f"⚠️ Tema no manejado o datos inválidos: {data.get('topic', 'desconocido')}")
                else:
                    logger.error(f"❌ Mensaje no procesable. Tipo recibido: {type(message)}")
            except json.JSONDecodeError as e:
                logger.error(f"❌ Error de decodificación JSON: {e}")
            except Exception as e:
                logger.error(f"❌ Error inesperado en mensaje: {e}")

    async def _handle_kline(self, symbol: str, kline: Dict, db: Session):
        try:
            timestamp_value = kline.get("start")
            if not timestamp_value or not str(timestamp_value).isdigit():
                logger.warning(f"⚠️ Timestamp inválido '{timestamp_value}' para {symbol}. Saltando.")
                return
            timestamp = timestamp_to_datetime(int(timestamp_value))

            try:
                volume = float(kline.get("volume", 0))
                open_price = float(kline.get("open", 0))
                high_price = float(kline.get("high", 0))
                low_price = float(kline.get("low", 0))
                close_price = float(kline.get("close", 0))
            except (ValueError, TypeError) as e:
                logger.warning(f"⚠️ Valores inválidos en Kline ('{kline}') para {symbol}: {e}")
                return

            logger.debug(f"📥 Kline recibido para {symbol}: timestamp={timestamp}, close={close_price}, volume={volume}")

            if not db.query(MarketData).filter_by(timestamp=timestamp, symbol=symbol).first():
                candle = MarketData(
                    timestamp=timestamp, symbol=symbol, open=open_price, high=high_price,
                    low=low_price, close=close_price, volume=volume
                )
                db.add(candle)
                db.commit()
                logger.debug(f"⚡ Kline para {symbol}: Close={candle.close}, Volume={candle.volume}")

                self.candles[symbol] = db.query(MarketData).filter_by(symbol=symbol).order_by(
                    MarketData.timestamp.desc()).limit(50).all()
                logger.debug(f"📈 Acumulados {len(self.candles[symbol])} velas para {symbol}")

                await self._execute_trade(symbol, db)

        except Exception as e:
            logger.error(f"❌ Error inesperado en _handle_kline para {symbol}: {e}")

    async def _handle_orderbook(self, symbol: str, data: Dict, db: Session):
        try:
            if data.get("type") == "snapshot":
                depth = int(getattr(config, "ORDERBOOK_DEPTH", 50) or 50)
                depth = max(1, min(depth, 50))
                self.orderbook_data[symbol] = {
                    "bids": (data["data"].get("b") or [])[:depth],
                    "asks": (data["data"].get("a") or [])[:depth],
                }
                await self._store_orderbook(symbol, db)
                logger.debug(
                    f"📊 Snapshot para {symbol}: Bids={len(self.orderbook_data[symbol]['bids'])}, Asks={len(self.orderbook_data[symbol]['asks'])}")
            elif data.get("type") == "delta":
                if symbol not in self.orderbook_data or not self.orderbook_data[symbol]["bids"]:
                    logger.warning(f"⚠️ No hay orderbook previo para {symbol}, esperando snapshot")
                    return
                current = self.orderbook_data[symbol]
                bid_dict = {float(b[0]): float(b[1]) for b in current["bids"]}
                ask_dict = {float(a[0]): float(a[1]) for a in current["asks"]}
                _update_orderbook(bid_dict, ask_dict, data)
                depth = int(getattr(config, "ORDERBOOK_DEPTH", 50) or 50)
                depth = max(1, min(depth, 50))
                self.orderbook_data[symbol] = {
                    "bids": [[str(p), str(q)] for p, q in sorted(bid_dict.items(), reverse=True) if q > 0][:depth],
                    "asks": [[str(p), str(q)] for p, q in sorted(ask_dict.items()) if q > 0][:depth],
                }
                await self._store_orderbook(symbol, db)
                logger.debug(
                    f"📊 Delta para {symbol}: Bids={len(self.orderbook_data[symbol]['bids'])}, Asks={len(self.orderbook_data[symbol]['asks'])}")
        except Exception as e:
            logger.error(f"❌ Error en _handle_orderbook para {symbol}: {e}")

    async def _store_orderbook(self, symbol: str, db: Session):
        try:
            orderbook = Orderbook(
                timestamp=datetime.now(timezone.utc), symbol=symbol,
                bids=self.orderbook_data[symbol]["bids"],
                asks=self.orderbook_data[symbol]["asks"]
            )
            db.add(orderbook)
            db.commit()
            if time.time() - self.last_orderbook_log >= 5:
                logger.info(
                    f"🤘 Orderbook guardado para {symbol}: Bids={len(self.orderbook_data[symbol]['bids'])}, Asks={len(self.orderbook_data[symbol]['asks'])}")
                self.last_orderbook_log = time.time()
        except Exception as e:
            logger.error(f"❌ Error al guardar orderbook para {symbol}: {e}")

    async def _handle_public_trade(self, symbol: str, trades: Any, db: Session) -> None:
        try:
            if symbol not in self.recent_trades:
                self.recent_trades[symbol] = deque(maxlen=500)
            q = self.recent_trades[symbol]
            if not isinstance(trades, list):
                return
            now_s = time.time()
            for t in trades[-50:]:
                if not isinstance(t, dict):
                    continue
                qty = 0.0
                price = 0.0
                ts_s = None
                for k in ("v", "size", "qty", "q"):
                    if k in t:
                        try:
                            qty = float(t.get(k) or 0.0)
                        except Exception:
                            qty = 0.0
                        break
                for k in ("p", "price", "px"):
                    if k in t:
                        try:
                            price = float(t.get(k) or 0.0)
                        except Exception:
                            price = 0.0
                        break
                for k in ("T", "ts", "time", "timestamp"):
                    if k in t:
                        try:
                            raw = t.get(k)
                            if raw is None:
                                ts_s = None
                            else:
                                val = float(raw)
                                ts_s = (val / 1000.0) if val > 10_000_000_000 else val
                        except Exception:
                            ts_s = None
                        break
                if ts_s is None:
                    ts_s = now_s
                if qty > 0:
                    q.append({"ts": float(ts_s), "qty": float(qty), "price": float(price)})
        except Exception as e:
            logger.error(f"❌ Error en _handle_public_trade para {symbol}: {e}")

    async def _handle_ticker(self, symbol: str, ticker: Dict, db: Session):
        try:
            if not isinstance(ticker, dict) or not ticker:
                logger.warning(f"⚠️ Ticker inválido para {symbol}: {ticker}. Saltando.")
                return

            required = ["lastPrice", "volume24h", "highPrice24h", "lowPrice24h"]
            optional = ["usdIndexPrice"]
            if not all(key in ticker for key in required):
                logger.warning(f"⚠️ Faltan claves requeridas en ticker para {symbol}: {ticker}. Saltando.")
                return

            ticker_values = {}
            for key in required + optional:
                value = ticker.get(key, 0.0)
                try:
                    ticker_values[key] = float(value) if value else 0.0
                except (ValueError, TypeError) as ve:
                    logger.warning(f"⚠️ Valor inválido en {key} para {symbol}: {value} - {ve}. Usando 0.0")
                    ticker_values[key] = 0.0

            self.ticker_data[symbol] = {
                "last_price": ticker_values["lastPrice"],
                "volume_24h": ticker_values["volume24h"],
                "high_24h": ticker_values["highPrice24h"],
                "low_24h": ticker_values["lowPrice24h"],
                "usd_index_price": ticker_values["usdIndexPrice"]
            }

            market_ticker = MarketTicker(
                timestamp=datetime.now(timezone.utc),
                symbol=symbol,
                last_price=self.ticker_data[symbol]["last_price"],
                volume_24h=self.ticker_data[symbol]["volume_24h"],
                high_24h=self.ticker_data[symbol]["high_24h"],
                low_24h=self.ticker_data[symbol]["low_24h"]
            )
            db.add(market_ticker)
            db.commit()

            logger.debug(
                f"⚡ Ticker actualizado para {symbol}: Last={self.ticker_data[symbol]['last_price']}, USDIndex={self.ticker_data[symbol]['usd_index_price']}")
        except Exception as e:
            logger.error(f"❌ Error inesperado en _handle_ticker para {symbol}: {type(e).__name__} - {str(e)}")

    @staticmethod
    def _classify_market_regime(metrics: Dict[str, Any], history: Optional[list[Dict[str, Any]]] = None) -> str:
        combined = float(metrics.get("combined", 0.0) or 0.0)
        volatility = float(metrics.get("volatility", 0.0) or 0.0)
        pio = float(metrics.get("pio", 0.0) or 0.0)
        egm = float(metrics.get("egm", 0.0) or 0.0)

        hist = history or []
        if isinstance(hist, list) and hist:
            try:
                recent_vol = float(max(abs(float(item.get("volatility", 0.0) or 0.0)) for item in hist if isinstance(item, dict)))
                volatility = max(volatility, recent_vol)
            except Exception:
                pass

        if abs(combined) >= 10.0 or volatility >= 0.01 or abs(pio) >= 1.5 or abs(egm) >= 1.5:
            return "volatile"
        if abs(combined) >= 3.0 or volatility >= 0.003 or abs(pio) >= 0.5 or abs(egm) >= 0.5:
            return "normal"
        return "calm"

    @staticmethod
    def _determine_decision(symbol: str, metrics: Dict) -> str:
        egm = float(metrics.get("egm", 0.0) or 0.0)
        pio = float(metrics.get("pio", 0.0) or 0.0)
        combined = float(metrics.get("combined", 0.0) or 0.0)
        buy_th = float(getattr(config, "COMBINED_BUY_THRESHOLD", 25.0) or 25.0)
        sell_th = float(getattr(config, "COMBINED_SELL_THRESHOLD", -25.0) or -25.0)
        hold_band = float(getattr(config, "COMBINED_HOLD_BAND", 12.0) or 12.0)

        volatility = float(metrics.get("volatility", 0.0) or 0.0)
        regime = NertzMetalEngine._classify_market_regime(metrics, metrics.get("history") if isinstance(metrics, dict) else None)

        if regime == "calm":
            buy_th = max(buy_th * 0.7, 2.0)
            sell_th = min(sell_th * 0.7, -2.0)
            hold_band = max(hold_band * 0.6, 1.0)
        elif regime == "volatile":
            buy_th = max(buy_th * 1.15, 3.0)
            sell_th = min(sell_th * 1.15, -3.0)
            hold_band = max(hold_band * 1.2, 1.5)
        else:
            buy_th = max(buy_th * 0.9, 2.5)
            sell_th = min(sell_th * 0.9, -2.5)
            hold_band = max(hold_band * 0.8, 1.2)

        if volatility > 0:
            base_vol = 0.01
            if volatility < base_vol:
                scale = (base_vol / volatility) ** 0.5
                if scale > 2.5:
                    scale = 2.5
                if scale < 1.0:
                    scale = 1.0
                buy_th = buy_th * scale
                sell_th = -abs(sell_th) * scale
                hold_band = hold_band * min(2.0, scale)

        if abs(combined) < hold_band:
            return "hold"

        if combined >= buy_th:
            if pio > 0 and egm > 0:
                return "buy"
            return "hold"

        if combined <= sell_th:
            if pio < 0 and egm < 0:
                return "sell"
            return "hold"

        return "hold"

    def _default_metrics(self) -> Dict[str, float]:
        return {"combined": 0.0, "ild": 0.0, "egm": 0.0, "rol": 0.0, "pio": 0.0, "ogm": 0.0, "volatility": 0.0}

    @staticmethod
    def _d(value: Any) -> Decimal:
        try:
            return Decimal(str(value))
        except Exception:
            return Decimal("0")

    @staticmethod
    def _format_decimal(value: Decimal) -> str:
        s = format(value, "f")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s if s else "0"

    def _quantize_to_step(self, value: float, step: float, rounding) -> Decimal:
        if step is None or step <= 0:
            return self._d(value)
        dv = self._d(value)
        ds = self._d(step)
        if ds == 0:
            return dv
        units = (dv / ds).to_integral_value(rounding=rounding)
        return units * ds

    async def _get_instrument_rules(self, symbol: str) -> Dict[str, float]:
        now = time.time()
        if symbol in self.instrument_rules and (now - self._instrument_rules_ts.get(symbol, 0.0) < 3600.0):
            return self.instrument_rules[symbol]

        url = f"{BASE_URL}/v5/market/instruments-info"
        params = {"category": "spot", "symbol": symbol}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                data = await resp.json()

        rules = {
            "tick_size": 0.01,
            "qty_step": float(config.MIN_TRADE_SIZE),
            "min_qty": float(config.MIN_TRADE_SIZE),
            "min_notional": 1.0,
        }
        try:
            if isinstance(data, dict) and data.get("retCode") == 0:
                lst = ((data.get("result") or {}).get("list") or [])
                row = lst[0] if isinstance(lst, list) and lst else {}
                price_filter = row.get("priceFilter") or {}
                lot_filter = row.get("lotSizeFilter") or {}

                tick = price_filter.get("tickSize")
                qty_step = lot_filter.get("qtyStep")
                if qty_step is None:
                    qty_step = lot_filter.get("basePrecision")
                min_qty = lot_filter.get("minOrderQty")
                min_amt = lot_filter.get("minNotionalValue")
                if min_amt is None:
                    min_amt = lot_filter.get("minOrderAmt")

                if tick is not None:
                    rules["tick_size"] = float(tick)
                if qty_step is not None:
                    rules["qty_step"] = float(qty_step)
                if min_qty is not None:
                    rules["min_qty"] = float(min_qty)
                if min_amt is not None:
                    rules["min_notional"] = float(min_amt)
        except Exception:
            pass

        self.instrument_rules[symbol] = rules
        self._instrument_rules_ts[symbol] = now
        return rules

    def _thresholds_payload(self) -> Dict[str, float]:
        return {
            "egm_buy_threshold": float(config.EGM_BUY_THRESHOLD),
            "egm_sell_threshold": float(config.EGM_SELL_THRESHOLD),
            "combined_buy_threshold": float(getattr(config, "COMBINED_BUY_THRESHOLD", 2.0)),
            "combined_sell_threshold": float(getattr(config, "COMBINED_SELL_THRESHOLD", -2.0)),
        }

    async def _record_metrics_snapshot(self, db: Session, symbol: str, last_price: float, metrics: Dict[str, float], decision: str) -> None:
        snapshot = MetricSnapshot(
            timestamp=datetime.now(timezone.utc),
            symbol=symbol,
            last_price=last_price,
            decision=decision,
            combined=float(metrics.get("combined", 0.0)),
            ild=float(metrics.get("ild", 0.0)),
            egm=float(metrics.get("egm", 0.0)),
            rol=float(metrics.get("rol", 0.0)),
            pio=float(metrics.get("pio", 0.0)),
            ogm=float(metrics.get("ogm", 0.0)),
            volatility=float(metrics.get("volatility", 0.0)),
            thresholds=self._thresholds_payload(),
        )
        db.add(snapshot)
        db.commit()

        now_ts = time.time()
        if now_ts - self._last_metrics_json_ts.get(symbol, 0.0) >= 2.0:
            append_results_event(
                {
                    "type": "metrics",
                    "symbol": symbol,
                    "last_price": last_price,
                    "decision": decision,
                    "metrics": {
                        "combined": snapshot.combined,
                        "ild": snapshot.ild,
                        "egm": snapshot.egm,
                        "rol": snapshot.rol,
                        "pio": snapshot.pio,
                        "ogm": snapshot.ogm,
                        "volatility": snapshot.volatility,
                    },
                    "thresholds": snapshot.thresholds,
                },
                log_dir=os.path.join(os.path.dirname(__file__), '..', 'logs'),
            )
            self._last_metrics_json_ts[symbol] = now_ts

    def _compute_threshold_targets(self, trades: list[Trade]) -> Dict[str, float]:
        buys = [t for t in trades if t.action == "buy" and t.egm is not None]
        sells = [t for t in trades if t.action == "sell" and t.egm is not None]

        win_buys = [t for t in buys if (t.profit_loss or 0.0) > 0]
        win_sells = [t for t in sells if (t.profit_loss or 0.0) > 0]

        def _median(values: list[float]) -> Optional[float]:
            if not values:
                return None
            values_sorted = sorted(values)
            mid = len(values_sorted) // 2
            if len(values_sorted) % 2 == 1:
                return float(values_sorted[mid])
            return float((values_sorted[mid - 1] + values_sorted[mid]) / 2)

        buy_egm_target = _median([float(t.egm) for t in win_buys]) if win_buys else None
        sell_egm_target = _median([float(t.egm) for t in win_sells]) if win_sells else None

        buy_comb_target = _median([float(t.combined) for t in win_buys]) if win_buys else None
        sell_comb_target = _median([float(t.combined) for t in win_sells]) if win_sells else None

        targets: Dict[str, float] = {}
        if buy_egm_target is not None:
            targets["egm_buy_threshold"] = max(0.0, min(1.0, buy_egm_target * 0.8))
        if sell_egm_target is not None:
            targets["egm_sell_threshold"] = min(0.0, max(-1.0, sell_egm_target * 0.8))

        if buy_comb_target is not None:
            targets["combined_buy_threshold"] = max(5.0, min(50.0, buy_comb_target * 0.9))
        if sell_comb_target is not None:
            targets["combined_sell_threshold"] = min(-5.0, max(-50.0, sell_comb_target * 0.9))

        return targets

    def _apply_threshold_update(self, targets: Dict[str, float], alpha: float = 0.1) -> Dict[str, Dict[str, float]]:
        before = self._thresholds_payload()
        if "egm_buy_threshold" in targets:
            config.EGM_BUY_THRESHOLD = (1 - alpha) * float(config.EGM_BUY_THRESHOLD) + alpha * float(targets["egm_buy_threshold"])
        if "egm_sell_threshold" in targets:
            config.EGM_SELL_THRESHOLD = (1 - alpha) * float(config.EGM_SELL_THRESHOLD) + alpha * float(targets["egm_sell_threshold"])
        if "combined_buy_threshold" in targets:
            config.COMBINED_BUY_THRESHOLD = (1 - alpha) * float(getattr(config, "COMBINED_BUY_THRESHOLD", 2.0)) + alpha * float(targets["combined_buy_threshold"])
        if "combined_sell_threshold" in targets:
            config.COMBINED_SELL_THRESHOLD = (1 - alpha) * float(getattr(config, "COMBINED_SELL_THRESHOLD", -2.0)) + alpha * float(targets["combined_sell_threshold"])
        after = self._thresholds_payload()
        return {"before": before, "after": after}

    def force_calibrate_thresholds(
        self,
        db: Session,
        sample_size: int = 500,
        alpha: float = 1.0,
        min_trades: int = 20,
    ) -> Dict[str, Any]:
        try:
            before = self._thresholds_payload()
            trades = (
                db.query(Trade)
                .filter(Trade.outcome_status == "final")
                .order_by(Trade.timestamp.desc())
                .limit(max(1, int(sample_size)))
                .all()
            )
            total = len(trades)
            wins = sum(1 for t in trades if (t.profit_loss or 0.0) > 0)
            losses = sum(1 for t in trades if (t.profit_loss or 0.0) < 0)
            win_rate = (wins / total) * 100 if total > 0 else 0.0

            if total < int(min_trades):
                return {
                    "success": False,
                    "message": "not_enough_final_trades",
                    "sample_size": total,
                    "wins": wins,
                    "losses": losses,
                    "win_rate": win_rate,
                    "thresholds": {"before": before, "after": before},
                }

            targets = self._compute_threshold_targets(trades)
            if not targets:
                return {
                    "success": False,
                    "message": "no_targets",
                    "sample_size": total,
                    "wins": wins,
                    "losses": losses,
                    "win_rate": win_rate,
                    "thresholds": {"before": before, "after": before},
                }

            update = self._apply_threshold_update(targets, alpha=float(alpha))
            snapshot = ThresholdSnapshot(
                timestamp=datetime.now(timezone.utc),
                egm_buy_threshold=float(config.EGM_BUY_THRESHOLD),
                egm_sell_threshold=float(config.EGM_SELL_THRESHOLD),
                combined_buy_threshold=float(getattr(config, "COMBINED_BUY_THRESHOLD", 2.0)),
                combined_sell_threshold=float(getattr(config, "COMBINED_SELL_THRESHOLD", -2.0)),
                stats={
                    "targets": targets,
                    "sample_size": total,
                    "wins": wins,
                    "losses": losses,
                    "win_rate": win_rate,
                    **update,
                },
            )
            db.add(snapshot)
            db.commit()

            log_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
            append_results_event({"type": "thresholds", "update": update, "targets": targets}, log_dir=log_dir)
            payload = load_results_json(log_dir=log_dir)
            payload["thresholds"] = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "values": self._thresholds_payload(),
                "update": update,
                "targets": targets,
            }
            save_results(payload, log_dir=log_dir)
            return {
                "success": True,
                "sample_size": total,
                "wins": wins,
                "losses": losses,
                "win_rate": win_rate,
                "targets": targets,
                "thresholds": update,
            }
        except Exception as e:
            return {"success": False, "message": str(e)}

    async def cancel_all_open_orders(self, symbol: Optional[str] = None, limit: int = 200) -> Dict[str, Any]:
        client = self._bybit_client()
        if client is None:
            return {"success": False, "message": "Credenciales BYBIT_API_KEY/BYBIT_API_SECRET no configuradas"}

        try:
            payload = await client.get_open_orders_merged(category="spot", symbol=symbol, limit=int(limit))
            if payload.get("retCode") != 0:
                return {"success": False, "message": payload.get("retMsg") or "get_open_orders_failed", "raw": payload}
            orders = (payload.get("result", {}) or {}).get("list", []) or []
        except Exception as e:
            return {"success": False, "message": str(e)}

        cancelled = 0
        failed = 0
        failures: list[dict] = []
        for o in orders:
            if not isinstance(o, dict):
                continue
            link = o.get("orderLinkId")
            link_str = str(link) if isinstance(link, str) and link else ""
            if not link_str.startswith("nertzh-"):
                continue
            oid = o.get("orderId")
            sym = o.get("symbol")
            if not isinstance(oid, str) or not oid or not isinstance(sym, str) or not sym:
                continue
            try:
                res = await client.cancel_order({"category": "spot", "symbol": sym, "orderId": oid})
                if res.get("retCode") == 0:
                    cancelled += 1
                else:
                    failed += 1
                    failures.append({"orderId": oid, "symbol": sym, "retCode": res.get("retCode"), "retMsg": res.get("retMsg")})
            except Exception as e:
                failed += 1
                failures.append({"orderId": oid, "symbol": sym, "error": str(e)})

        return {
            "success": True,
            "seen": len([o for o in orders if isinstance(o, dict)]),
            "cancelled": cancelled,
            "failed": failed,
            "failures": failures[:50],
        }

    def reset_runtime_state(self) -> None:
        self.positions = {symbol: [] for symbol in self.symbols}
        self.iterations = 0
        self.order_status = {}
        self._metrics_raw_history = {symbol: deque() for symbol in self.symbols}
        self._last_weighted_liquidity = {symbol: None for symbol in self.symbols}
        self.recent_trades = {symbol: deque(maxlen=500) for symbol in self.symbols}
        self.last_trade_time = {symbol: datetime.min.replace(tzinfo=timezone.utc) for symbol in self.symbols}
        self._last_metrics_json_ts = {symbol: 0.0 for symbol in self.symbols}
        self._last_balance_sync_ts = 0.0

    def wipe_database(self, db: Session) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for model, name in [
            (Trade, "trades"),
            (MetricSnapshot, "metric_snapshots"),
            (BalanceSnapshot, "balance_snapshots"),
            (ThresholdSnapshot, "threshold_snapshots"),
            (MarketTicker, "market_ticker"),
            (Orderbook, "orderbook"),
            (MarketData, "market_data"),
        ]:
            try:
                n = db.query(model).delete()
                counts[name] = int(n or 0)
            except Exception:
                counts[name] = -1
        db.commit()
        try:
            db.execute(text("VACUUM"))
            db.commit()
        except Exception:
            pass
        return counts

    def reset_results_json(self) -> str:
        log_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
        payload = {"events": [], "metadata": {"timestamp": datetime.now(timezone.utc).isoformat(), "reset": True}}
        save_results(payload, log_dir=log_dir)
        return os.path.join(os.path.abspath(log_dir), "results.json")

    async def _auto_tune_thresholds_if_due(self) -> None:
        if not bool(getattr(config, "AUTO_TUNE_THRESHOLDS", False)):
            return
        now_ts = time.time()
        if now_ts - self._last_tune_ts < 60.0:
            return

        with SessionLocal() as db:
            recent_trades = (
                db.query(Trade)
                .filter(Trade.outcome_status == "final")
                .order_by(Trade.timestamp.desc())
                .limit(200)
                .all()
            )
            if len(recent_trades) < 20:
                return

            targets = self._compute_threshold_targets(recent_trades)
            if not targets:
                return

            update = self._apply_threshold_update(targets, alpha=0.1)
            snapshot = ThresholdSnapshot(
                timestamp=datetime.now(timezone.utc),
                egm_buy_threshold=float(config.EGM_BUY_THRESHOLD),
                egm_sell_threshold=float(config.EGM_SELL_THRESHOLD),
                combined_buy_threshold=float(getattr(config, "COMBINED_BUY_THRESHOLD", 2.0)),
                combined_sell_threshold=float(getattr(config, "COMBINED_SELL_THRESHOLD", -2.0)),
                stats={
                    "targets": targets,
                    "sample_size": len(recent_trades),
                    "wins": sum(1 for t in recent_trades if (t.profit_loss or 0.0) > 0),
                    "losses": sum(1 for t in recent_trades if (t.profit_loss or 0.0) < 0),
                    **update,
                },
            )
            db.add(snapshot)
            db.commit()

        append_results_event(
            {"type": "thresholds", "update": update, "targets": targets},
            log_dir=os.path.join(os.path.dirname(__file__), '..', 'logs'),
        )
        log_dir = os.path.join(os.path.dirname(__file__), '..', 'logs')
        payload = load_results_json(log_dir=log_dir)
        payload["thresholds"] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "values": self._thresholds_payload(),
            "update": update,
            "targets": targets,
        }
        save_results(payload, log_dir=log_dir)
        self._last_tune_ts = now_ts

    async def _core_cycle(self, symbol: str, db: Session, collect_only: bool = False, force_trade: bool = False) -> None:
        lock = self._core_cycle_locks.setdefault(symbol, asyncio.Lock())
        async with lock:
            try:
                current_time = datetime.now(timezone.utc)
                now_ts = time.time()
                if now_ts - self._last_balance_sync_ts >= 60.0:
                    balance = await self.record_balance(account_type="UNIFIED", coin="USDT")
                    if balance.get("success") and isinstance(balance.get("balance"), dict):
                        available = float(balance["balance"].get("available_balance") or 0.0)
                        total_equity = float(balance["balance"].get("total_equity") or 0.0)
                        if total_equity > 0:
                            self.capital = total_equity
                        elif available > 0:
                            self.capital = available
                    self._last_balance_sync_ts = now_ts

                candles = (
                    db.query(MarketData)
                    .filter(MarketData.symbol == symbol)
                    .order_by(MarketData.timestamp.desc())
                    .limit(50)
                    .all()
                )
                cooldown = timedelta(seconds=config.DEFAULT_SLEEP_TIME)
                last_trade_time = self.last_trade_time.get(symbol, datetime.min.replace(tzinfo=timezone.utc))
                in_cooldown = current_time <= last_trade_time + cooldown

                candle_data = [
                    {"open": c.open, "high": c.high, "low": c.low, "close": c.close, "volume": c.volume}
                    for c in candles
                ]
                orderbook = self.orderbook_data.get(symbol, {"bids": [], "asks": []})
                ticker = self.ticker_data.get(symbol, {"last_price": 0.0})

                if len(candle_data) >= 2 and orderbook.get("bids") and orderbook.get("asks") and ticker.get("last_price"):
                    window_min = float(getattr(config, "METRICS_WINDOW_MINUTES", 15.0) or 15.0)
                    window_s = max(60.0, window_min * 60.0)
                    history_q = self._metrics_raw_history.setdefault(symbol, deque())
                    cutoff = now_ts - window_s
                    while history_q:
                        head = history_q[0]
                        ts = head.get("ts") if isinstance(head, dict) else None
                        if ts is None or float(ts) >= cutoff:
                            break
                        history_q.popleft()

                    history_payload = []
                    for h in history_q:
                        if not isinstance(h, dict):
                            continue
                        history_payload.append({k: v for k, v in h.items() if k != "ts"})

                    prev_entry = self._last_weighted_liquidity.get(symbol)
                    prev_liq = None
                    prev_ts = None
                    if isinstance(prev_entry, tuple) and len(prev_entry) == 2:
                        prev_liq = prev_entry[0]
                        prev_ts = prev_entry[1]

                    ticker_payload: dict[str, Any] = dict(ticker)
                    ticker_payload["orderbook_lambda"] = float(getattr(config, "ORDERBOOK_LAMBDA", 0.03) or 0.03)
                    ticker_payload["orderbook_pct_band"] = float(getattr(config, "ORDERBOOK_PCT_BAND", 0.015) or 0.015)
                    ticker_payload["ild_target_move"] = float(getattr(config, "ILD_TARGET_MOVE", 0.002) or 0.002)
                    ticker_payload["metric_history"] = history_payload
                    ticker_payload["prev_weighted_liquidity"] = prev_liq
                    ticker_payload["rol_dt_s"] = (now_ts - float(prev_ts)) if prev_ts else None
                    ticker_payload["formulas"] = getattr(config, "FORMULAS", {}) or {}

                    metrics = calculate_metrics(candle_data, orderbook, ticker_payload, depth=int(getattr(config, "ORDERBOOK_DEPTH", 50) or 50))

                    try:
                        wl = metrics.get("weighted_liquidity")
                        if wl is not None:
                            self._last_weighted_liquidity[symbol] = (float(wl), now_ts)
                    except Exception:
                        pass

                    try:
                        raw_sample = {
                            "ts": now_ts,
                            "pio": float(metrics.get("pio_raw", 0.0) or 0.0),
                            "ild": float(metrics.get("ild_raw", 0.0) or 0.0),
                            "egm": float(metrics.get("egm_raw", 0.0) or 0.0),
                            "rol": float(metrics.get("rol_raw", 0.0) or 0.0),
                            "ogm": float(metrics.get("ogm_raw", 0.0) or 0.0),
                        }
                        history_q.append(raw_sample)
                    except Exception:
                        pass
                else:
                    metrics = self._default_metrics()
                logger.debug(
                    f"📊 Métricas calculadas para {symbol}: pio={metrics.get('pio', 0)}, ild={metrics.get('ild', 0)}, egm={metrics.get('egm', 0)}, rol={metrics.get('rol', 0)}, combined={metrics.get('combined', 0)}")

                decision = self._determine_decision(symbol, metrics)
                last_price = float(ticker.get("last_price", 0.0) or 0.0)
                if last_price <= 0 and candles:
                    try:
                        last_price = float(candles[0].close)
                    except Exception:
                        last_price = 0.0

                finalized = await self._finalize_due_outcomes(db, symbol, last_price)
                if finalized is not None:
                    await self._save_results(symbol, finalized)
                await self._record_metrics_snapshot(db, symbol, last_price, metrics, decision)

                await self._auto_tune_thresholds_if_due()

                if (
                    decision == "hold"
                    and force_trade
                    and not collect_only
                    and not in_cooldown
                ):
                    last = (self.positions.get(symbol) or [])
                    last_action = (last[-1].get("action") if last else None)
                    decision = "sell" if last_action == "buy" else "buy"

                if decision in {"buy", "sell"} and bool(getattr(config, "ML_ENABLED", False)):
                    p = self.ml_predict_proba(symbol=symbol, action=decision, metrics=metrics)
                    th = float(getattr(config, "ML_PROB_THRESHOLD", 0.6) or 0.6)
                    if p is not None and p < th:
                        decision = "hold"

                if decision == "hold" or collect_only or in_cooldown:
                    return

                active_trade = (
                    db.query(Trade)
                    .filter(Trade.symbol == symbol)
                    .filter(Trade.outcome_status.in_(["pending", "partial", "filled"]))
                    .order_by(Trade.timestamp.desc())
                    .first()
                )
                if active_trade is not None:
                    return

                rules = await self._get_instrument_rules(symbol)
                tick_size = float(rules.get("tick_size") or 0.01)
                qty_step = float(rules.get("qty_step") or float(config.MIN_TRADE_SIZE))
                min_qty = float(rules.get("min_qty") or float(config.MIN_TRADE_SIZE))
                min_notional = float(rules.get("min_notional") or 1.0)

                risk_per_trade = self.capital * config.RISK_FACTOR
                volatility = metrics.get("volatility", 0.01)
                if volatility <= 0:
                    logger.warning(f"⚠️ Volatilidad inválida ({volatility}) para {symbol}, usando 0.01")
                    volatility = 0.01

                min_risk_notional = max(min_notional * 1.1, 1.0)
                if risk_per_trade < min_risk_notional:
                    risk_per_trade = min_risk_notional

                if last_price <= 0:
                    logger.error(f"❌ Precio inválido ({last_price}) para {symbol}")
                    return

                quantity = risk_per_trade / (volatility * last_price)
                quantity = max(min(quantity, config.MAX_TRADE_SIZE), config.MIN_TRADE_SIZE)

                order_type_raw = config.ORDER_TYPE or "Limit"
                order_type = {
                    "limit": "Limit",
                    "Limit": "Limit",
                    "market": "Market",
                    "Market": "Market",
                }.get(order_type_raw, "Limit")

                entry_price = last_price
                if order_type == "Limit":
                    book = self.orderbook_data.get(symbol, {"bids": [], "asks": []})
                    try:
                        best_bid = float(book.get("bids", [])[0][0]) if book.get("bids") else last_price
                        best_ask = float(book.get("asks", [])[0][0]) if book.get("asks") else last_price
                    except Exception:
                        best_bid = last_price
                        best_ask = last_price
                    entry_price = best_bid if decision == "buy" else best_ask
                    entry_price = float(self._quantize_to_step(entry_price, tick_size, ROUND_HALF_UP))

                qty_dec = self._quantize_to_step(quantity, qty_step, ROUND_DOWN)
                min_qty_dec = self._quantize_to_step(min_qty, qty_step, ROUND_UP)
                if qty_dec < min_qty_dec:
                    qty_dec = min_qty_dec

                notional = float(qty_dec) * float(entry_price)
                if min_notional > 0 and notional < min_notional:
                    target_qty = (self._d(min_notional) / self._d(entry_price)) if entry_price > 0 else self._d(0)
                    qty_dec = self._quantize_to_step(float(target_qty), qty_step, ROUND_UP)

                quantity = float(qty_dec)
                trade_value = quantity * entry_price
                if trade_value > self.capital:
                    logger.warning(f"⚠️ Cantidad excesiva ({trade_value:.2f}) para {symbol}. Ajustando...")
                    quantity = (self.capital * 0.1) / max(entry_price, 1e-9)
                    qty_dec = self._quantize_to_step(quantity, qty_step, ROUND_DOWN)
                    if qty_dec < min_qty_dec:
                        qty_dec = min_qty_dec
                    notional = float(qty_dec) * float(entry_price)
                    if min_notional > 0 and notional < min_notional:
                        target_qty = (self._d(min_notional) / self._d(entry_price)) if entry_price > 0 else self._d(0)
                        qty_dec = self._quantize_to_step(float(target_qty), qty_step, ROUND_UP)
                        notional = float(qty_dec) * float(entry_price)
                    if notional > self.capital:
                        logger.warning(f"⚠️ No alcanza capital para mínimo del exchange ({min_notional}). Saltando trade.")
                        return
                    quantity = float(qty_dec)
                    if quantity < float(min_qty_dec):
                        logger.warning(f"⚠️ Cantidad ajustada ({quantity}) por debajo del mínimo. Saltando trade.")
                        return

                tp, sl = calculate_tp_sl(entry_price, volatility, decision, config.TP_PERCENTAGE, config.SL_PERCENTAGE)
                tp_dec = self._quantize_to_step(tp, tick_size, ROUND_HALF_UP)
                sl_dec = self._quantize_to_step(sl, tick_size, ROUND_HALF_UP)
                entry_dec = self._d(entry_price)
                tick_dec = self._d(tick_size)
                if decision == "buy":
                    if tp_dec <= entry_dec:
                        tp_dec = entry_dec + tick_dec
                    if sl_dec >= entry_dec:
                        sl_dec = entry_dec - tick_dec
                else:
                    if tp_dec >= entry_dec:
                        tp_dec = entry_dec - tick_dec
                    if sl_dec <= entry_dec:
                        sl_dec = entry_dec + tick_dec
                tp = float(tp_dec)
                sl = float(sl_dec)

                order_result = await self._place_order(symbol, decision, quantity, entry_price, tp, sl)
                if not order_result.get("success", False):
                    logger.error(
                        f"❌ Fallo al colocar orden para {symbol}: {order_result.get('message', 'Error desconocido')}")
                    return

                self.trade_id_counter += 1
                order_id = str(order_result.get("order_id") or "")
                bybit_raw = order_result.get("raw")
                order_link_id = str(order_result.get("order_link_id") or "")
                if isinstance(bybit_raw, dict) and order_link_id:
                    merged_raw = dict(bybit_raw)
                    merged_raw["order_link_id"] = order_link_id
                    bybit_raw = merged_raw

                trade = Trade(
                    trade_id=self.trade_id_counter - 1,
                    timestamp=current_time,
                    symbol=symbol,
                    action=decision,
                    order_id=order_id,
                    bybit_raw=bybit_raw,
                    entry_price=entry_price,
                    exit_price=0.0,
                    tp_price=float(tp),
                    sl_price=float(sl),
                    quantity=quantity,
                    profit_loss=0.0,
                    outcome_status="pending",
                    decision=decision,
                    combined=metrics.get("combined", 0),
                    ild=metrics.get("ild", 0),
                    egm=metrics.get("egm", 0),
                    rol=metrics.get("rol", 0),
                    pio=metrics.get("pio", 0),
                    ogm=metrics.get("ogm", 0),
                    risk_reward_ratio=config.TP_PERCENTAGE / config.SL_PERCENTAGE
                )
                db.add(trade)
                db.commit()

                self.positions.setdefault(symbol, []).append({
                    "trade_id": trade.trade_id,
                    "timestamp": trade.timestamp.isoformat(),
                    "symbol": trade.symbol,
                    "action": trade.action,
                    "order_id": trade.order_id,
                    "entry_price": trade.entry_price,
                    "exit_price": None,
                    "tp_price": trade.tp_price,
                    "sl_price": trade.sl_price,
                    "quantity": trade.quantity,
                    "profit_loss": None,
                    "outcome_status": trade.outcome_status,
                    "outcome_timestamp": trade.outcome_timestamp.isoformat() if trade.outcome_timestamp else None,
                    "decision": trade.decision,
                    "combined": trade.combined,
                    "ild": trade.ild,
                    "egm": trade.egm,
                    "rol": trade.rol,
                    "pio": trade.pio,
                    "ogm": trade.ogm,
                    "risk_reward_ratio": trade.risk_reward_ratio
                })
                self.last_trade_time[symbol] = current_time
                if order_id:
                    self.order_status[order_id] = {
                        "order_id": order_id,
                        "trade_id": int(trade.trade_id),
                        "symbol": symbol,
                        "status": "pending",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }

                logger.info(
                    f"💰 Orden colocada: {decision.upper()} {quantity:.4f} {symbol} @ {entry_price:.2f}, TP={tp:.2f}, SL={sl:.2f}, OrderID={order_id}")

                self.iterations += 1
                if self.iterations >= config.MAX_ITERATIONS > 0:
                    logger.info("🏁 Máximo de iteraciones alcanzado. Deteniendo bot.")
                    self.stop()
                await self._save_results(symbol, trade)

            except Exception as e:
                logger.error(f"❌ Error en _execute_trade para {symbol}: {e}")

    async def _execute_trade(self, symbol: str, db: Session):
        await self._core_cycle(symbol, db, collect_only=False)

    async def run_cycles(self, symbol: str, cycles: int, interval_ms: int, collect_only: bool) -> None:
        if cycles < 0:
            return
        remaining = cycles
        while self.running and (remaining > 0 or cycles == 0):
            with SessionLocal() as db:
                await self._core_cycle(symbol, db, collect_only=collect_only)
            if cycles != 0:
                remaining -= 1
            if interval_ms > 0:
                await asyncio.sleep(interval_ms / 1000)
            else:
                await asyncio.sleep(0)

    def start_hft(self, symbol: str, interval_ms: int = 250, collect_only: bool = False) -> bool:
        if symbol in self.hft_tasks and not self.hft_tasks[symbol].done():
            return False
        task = asyncio.create_task(self.run_cycles(symbol, cycles=0, interval_ms=interval_ms, collect_only=collect_only))
        self.hft_tasks[symbol] = task
        return True

    def stop_hft(self, symbol: str) -> bool:
        task = self.hft_tasks.get(symbol)
        if not task:
            return False
        task.cancel()
        return True

    def start_support_loop(self, interval_s: float = 2.0) -> bool:
        if self._support_task and not self._support_task.done():
            return False
        self._support_interval_s = float(max(0.25, min(30.0, float(interval_s))))
        self._support_task = asyncio.create_task(self._support_loop())
        return True

    async def _support_loop(self) -> None:
        while self.running:
            try:
                with SessionLocal() as db:
                    await self.sync_open_orders(db)
                    if bool(getattr(config, "AUTO_AGENT_ENABLED", False)):
                        await self._agent_tick(db)
            except Exception as e:
                logger.error(f"❌ Error en support loop: {e}")
            await asyncio.sleep(self._support_interval_s)

    async def sync_open_orders(
        self,
        db: Session,
        symbol: Optional[str] = None,
        timeout_seconds: float = 30.0,
        update_after_seconds: float = 20.0,
        limit: int = 100,
    ) -> Dict[str, Any]:
        client = self._bybit_client()
        if client is None:
            return {"success": False, "message": "Credenciales BYBIT_API_KEY/BYBIT_API_SECRET no configuradas"}

        now = datetime.now(timezone.utc)
        now_ts = time.time()
        if now_ts - float(self._last_orders_sync_ts or 0.0) < 0.5:
            return {"success": True, "results": {"skipped": 1}}

        async with self._orders_sync_lock:
            if now_ts - float(self._last_orders_sync_ts or 0.0) < 0.5:
                return {"success": True, "results": {"skipped": 1}}
            self._last_orders_sync_ts = now_ts

            symbols = [symbol] if symbol else list(self.symbols)
            results: Dict[str, Any] = {
                "checked": 0,
                "updated": 0,
                "amended": 0,
                "cancelled": 0,
                "replaced": 0,
                "orphan_open": 0,
                "no_action": 0,
                "errors": 0,
            }

            changed = False
            for sym in symbols:
                bybit_open: list[dict] = []
                try:
                    payload = await client.get_open_orders_merged(category="spot", symbol=sym, limit=int(limit))
                    if payload.get("retCode") == 0:
                        bybit_open = list(((payload.get("result", {}) or {}).get("list", []) or []))
                except Exception:
                    bybit_open = []

                open_by_id: Dict[str, Dict[str, Any]] = {}
                for o in bybit_open:
                    if not isinstance(o, dict):
                        continue
                    oid = o.get("orderId")
                    if isinstance(oid, str) and oid:
                        open_by_id[oid] = o
                        self.order_status[oid] = {
                            "order_id": oid,
                            "symbol": sym,
                            "status": str(o.get("orderStatus") or "").lower(),
                            "timestamp": now.isoformat(),
                            "raw": o,
                        }

                trades = (
                    db.query(Trade)
                    .filter(Trade.symbol == sym)
                    .filter(Trade.order_id.isnot(None))
                    .filter(Trade.order_id != "")
                    .filter(~Trade.outcome_status.in_(["final", "cancelled"]))
                    .order_by(Trade.timestamp.desc())
                    .limit(300)
                    .all()
                )

                tracked_order_ids: set[str] = set()
                tracked_link_ids: set[str] = set()
                for trade in trades:
                    order_id = str(getattr(trade, "order_id", "") or "")
                    if not order_id:
                        continue
                    tracked_order_ids.add(order_id)
                    raw = getattr(trade, "bybit_raw", None)
                    if isinstance(raw, dict):
                        link = raw.get("order_link_id") or raw.get("orderLinkId")
                        if isinstance(link, str) and link:
                            tracked_link_ids.add(link)

                    order = open_by_id.get(order_id)
                    if order is None:
                        try:
                            payload = await client.order_realtime(category="spot", symbol=sym, order_id=order_id)
                            if payload.get("retCode") == 0:
                                lst = (payload.get("result", {}) or {}).get("list", []) or []
                                if isinstance(lst, list) and lst:
                                    first = lst[0]
                                    if isinstance(first, dict):
                                        order = first
                        except Exception:
                            order = None
                    if order is None:
                        try:
                            order_link_id = None
                            raw = getattr(trade, "bybit_raw", None)
                            if isinstance(raw, dict):
                                order_link_id = raw.get("order_link_id")
                            payload = await client.order_history(
                                category="spot",
                                symbol=sym,
                                order_id=order_id,
                                order_link_id=order_link_id if isinstance(order_link_id, str) and order_link_id else None,
                                limit=1,
                            )
                            if payload.get("retCode") == 0:
                                lst = (payload.get("result", {}) or {}).get("list", []) or []
                                if isinstance(lst, list) and lst:
                                    first = lst[0]
                                    if isinstance(first, dict):
                                        order = first
                                        current_raw: Any = getattr(trade, "bybit_raw", None)
                                        merged = dict(current_raw) if isinstance(current_raw, dict) else {}
                                        merged["order_history"] = first
                                        trade.bybit_raw = merged
                                        changed = True
                        except Exception:
                            order = None

                    if order is None:
                        results["no_action"] += 1
                        continue

                    results["checked"] += 1
                    order_status = str(order.get("orderStatus") or "").lower()
                    order_link_id = order.get("orderLinkId")
                    if isinstance(order_link_id, str) and order_link_id:
                        tracked_link_ids.add(order_link_id)
                    order_link_id_str = order_link_id if isinstance(order_link_id, str) else ""
                    trade_link_id_str = ""
                    trade_raw = getattr(trade, "bybit_raw", None)
                    if isinstance(trade_raw, dict):
                        tl = trade_raw.get("order_link_id") or trade_raw.get("orderLinkId")
                        if isinstance(tl, str):
                            trade_link_id_str = tl
                    is_bot_order = bool(order_link_id_str.startswith("nertzh-") or trade_link_id_str.startswith("nertzh-"))
                    self.order_status[order_id] = {
                        "order_id": order_id,
                        "symbol": sym,
                        "status": order_status,
                        "timestamp": now.isoformat(),
                        "raw": order,
                    }

                    ts = getattr(trade, "timestamp", None)
                    if isinstance(ts, datetime) and ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    seconds_elapsed = (now - ts).total_seconds() if isinstance(ts, datetime) else 0.0

                    if is_bot_order and seconds_elapsed >= float(update_after_seconds) and order_status in {"new", "partially_filled"}:
                        try:
                            order_type = str(order.get("orderType") or "")
                            if order_type.lower() == "limit":
                                side = str(order.get("side") or "").lower()
                                book = self.orderbook_data.get(sym, {"bids": [], "asks": []})
                                best_bid = float(book.get("bids", [])[0][0]) if book.get("bids") else 0.0
                                best_ask = float(book.get("asks", [])[0][0]) if book.get("asks") else 0.0
                                target_price = best_bid if side == "buy" else best_ask
                                if target_price > 0:
                                    rules = await self._get_instrument_rules(sym)
                                    tick_size = float(rules.get("tick_size") or 0.01)
                                    target_price = float(self._quantize_to_step(target_price, tick_size, ROUND_HALF_UP))
                                    amend_body = {
                                        "category": "spot",
                                        "symbol": sym,
                                        "orderId": order_id,
                                        "price": self._format_decimal(self._d(target_price)),
                                    }
                                    amend_res = await client.amend_order(amend_body)
                                    if amend_res.get("retCode") == 0:
                                        current_raw: Any = getattr(trade, "bybit_raw", None)
                                        merged = dict(current_raw) if isinstance(current_raw, dict) else {}
                                        merged["amend"] = amend_res
                                        trade.bybit_raw = merged
                                        results["amended"] += 1
                                        changed = True
                                        continue
                        except Exception:
                            pass

                    if is_bot_order and seconds_elapsed >= float(timeout_seconds):
                        if order_status in {"filled", "cancelled", "rejected", "deactivated"}:
                            if await self._update_trade_from_bybit(trade, order):
                                results["updated"] += 1
                                changed = True
                            continue

                        try:
                            cancel_body = {"category": "spot", "symbol": sym, "orderId": order_id}
                            cancel_result = await client.cancel_order(cancel_body)
                            if cancel_result.get("retCode") == 0:
                                current_raw: Any = getattr(trade, "bybit_raw", None)
                                merged: Dict[str, Any] = dict(current_raw) if isinstance(current_raw, dict) else {}
                                merged["cancel"] = cancel_result
                                merged["order_realtime"] = order
                                trade.bybit_raw = merged
                                trade.outcome_status = "cancelled"
                                trade.outcome_timestamp = now
                                trade.exit_price = 0.0
                                trade.profit_loss = 0.0
                                self.order_status[order_id] = {
                                    "order_id": order_id,
                                    "symbol": sym,
                                    "status": "cancelled",
                                    "timestamp": now.isoformat(),
                                    "raw": cancel_result,
                                }
                                results["cancelled"] += 1
                                changed = True
                            else:
                                results["errors"] += 1
                        except Exception:
                            results["errors"] += 1
                        continue

                    if is_bot_order and seconds_elapsed >= float(update_after_seconds) and order_status in {"new", "partially_filled"}:
                        rep_res = await self._replace_order_with_market(sym, order_id, trade, bybit_order=order)
                        if rep_res.get("success"):
                            results["replaced"] += 1
                            changed = True
                        else:
                            results["errors"] += 1
                        continue

                    if await self._update_trade_from_bybit(trade, order):
                        results["updated"] += 1
                        changed = True

                for oid, orphan in open_by_id.items():
                    link = orphan.get("orderLinkId")
                    link_str = str(link) if isinstance(link, str) and link else ""
                    if oid in tracked_order_ids or (link_str and link_str in tracked_link_ids):
                        continue
                    results["orphan_open"] += 1
                    try:
                        if not link_str.startswith("nertzh-"):
                            continue
                        cancel_res = await client.cancel_order({"category": "spot", "symbol": sym, "orderId": oid})
                        if cancel_res.get("retCode") == 0:
                            results["cancelled"] += 1
                            self.order_status[oid] = {
                                "order_id": oid,
                                "symbol": sym,
                                "status": "cancelled",
                                "timestamp": now.isoformat(),
                                "raw": {"order": orphan, "cancel": cancel_res},
                            }
                        else:
                            results["errors"] += 1
                    except Exception:
                        results["errors"] += 1

            if changed:
                db.commit()

            return {"success": True, "results": results}

    async def _update_trade_from_bybit(self, trade: Trade, bybit_order: Dict[str, Any]) -> bool:
        try:
            order_status = str(bybit_order.get("orderStatus") or "").lower()
            avg_price = float(bybit_order.get("avgPrice") or 0.0)
            cum_exec_qty = float(bybit_order.get("cumExecQty") or 0.0)
            cum_fee = float(bybit_order.get("cumExecFee") or 0.0)
            now = datetime.now(timezone.utc)

            current_raw: Any = getattr(trade, "bybit_raw", None)
            if isinstance(current_raw, dict):
                merged = dict(current_raw)
                merged["order_realtime"] = bybit_order
                trade.bybit_raw = merged
            else:
                trade.bybit_raw = {"order_realtime": bybit_order}

            prev_status = str(getattr(trade, "outcome_status", "") or "")
            prev_entry = float(getattr(trade, "entry_price", 0.0) or 0.0)
            prev_qty = float(getattr(trade, "quantity", 0.0) or 0.0)
            if order_status in {"new"}:
                trade.outcome_status = "pending"
            elif order_status in {"partially_filled"}:
                trade.outcome_status = "partial"
                if avg_price > 0:
                    trade.entry_price = float(avg_price)
                if cum_exec_qty > 0:
                    trade.exit_price = 0.0
                    trade.profit_loss = float(-cum_fee) if cum_fee > 0 else 0.0
            elif order_status in {"filled"}:
                trade.outcome_status = "filled"
                trade.outcome_timestamp = now
                if avg_price > 0:
                    trade.entry_price = float(avg_price)
                if cum_exec_qty > 0:
                    trade.quantity = float(cum_exec_qty)
                trade.exit_price = 0.0
                trade.profit_loss = float(-cum_fee) if cum_fee > 0 else 0.0
            elif order_status in {"cancelled", "rejected", "deactivated"}:
                trade.outcome_status = order_status
                trade.outcome_timestamp = now
                trade.exit_price = 0.0
                trade.profit_loss = 0.0
            else:
                trade.outcome_status = order_status or prev_status or "pending"

            return (
                prev_status != str(trade.outcome_status or "")
                or prev_entry != float(getattr(trade, "entry_price", 0.0) or 0.0)
                or prev_qty != float(getattr(trade, "quantity", 0.0) or 0.0)
            )
        except Exception as e:
            logger.error(f"❌ Error actualizando trade {getattr(trade, 'trade_id', None)}: {e}")
            return False

    async def _replace_order_with_market(
        self,
        symbol: str,
        order_id: str,
        trade: Trade,
        bybit_order: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        try:
            client = self._bybit_client()
            if client is None:
                return {"success": False, "message": "Credenciales BYBIT_API_KEY/BYBIT_API_SECRET no configuradas"}

            cancel_body = {"category": "spot", "symbol": symbol, "orderId": order_id}
            cancel_result = await client.cancel_order(cancel_body)
            if cancel_result.get("retCode") != 0:
                return {"success": False, "message": cancel_result.get("retMsg") or "cancel_failed", "raw": cancel_result}

            executed = 0.0
            if isinstance(bybit_order, dict):
                try:
                    executed = float(bybit_order.get("cumExecQty") or 0.0)
                except Exception:
                    executed = 0.0
            if executed <= 0 and isinstance(getattr(trade, "bybit_raw", None), dict):
                try:
                    rt = (trade.bybit_raw or {}).get("order_realtime") or {}
                    executed = float((rt or {}).get("cumExecQty") or 0.0)
                except Exception:
                    executed = 0.0

            rules = await self._get_instrument_rules(symbol)
            qty_step = float(rules.get("qty_step") or float(config.MIN_TRADE_SIZE))
            min_qty = float(rules.get("min_qty") or float(config.MIN_TRADE_SIZE))
            min_notional = float(rules.get("min_notional") or 0.0)
            original_qty = float(trade.quantity or 0.0)
            remaining = max(0.0, original_qty - float(executed or 0.0)) if original_qty > 0 else 0.0
            if remaining <= 0:
                current_raw: Any = getattr(trade, "bybit_raw", None)
                merged: Dict[str, Any] = dict(current_raw) if isinstance(current_raw, dict) else {}
                merged["replace"] = {"cancel": cancel_result, "create": None}
                trade.bybit_raw = merged
                return {"success": True, "old_order_id": order_id, "new_order_id": "", "raw": merged.get("replace")}

            qty_dec = self._quantize_to_step(float(remaining), qty_step, ROUND_DOWN)
            min_qty_dec = self._quantize_to_step(min_qty, qty_step, ROUND_UP)
            if qty_dec < min_qty_dec:
                current_raw: Any = getattr(trade, "bybit_raw", None)
                merged: Dict[str, Any] = dict(current_raw) if isinstance(current_raw, dict) else {}
                merged["replace"] = {"cancel": cancel_result, "create": None, "skipped": "remaining_below_min_qty"}
                trade.bybit_raw = merged
                return {"success": True, "old_order_id": order_id, "new_order_id": "", "raw": merged.get("replace")}

            approx_price = float(getattr(trade, "entry_price", 0.0) or 0.0)
            if min_notional > 0 and approx_price > 0 and (float(qty_dec) * approx_price) < min_notional:
                current_raw: Any = getattr(trade, "bybit_raw", None)
                merged: Dict[str, Any] = dict(current_raw) if isinstance(current_raw, dict) else {}
                merged["replace"] = {"cancel": cancel_result, "create": None, "skipped": "remaining_below_min_notional"}
                trade.bybit_raw = merged
                return {"success": True, "old_order_id": order_id, "new_order_id": "", "raw": merged.get("replace")}

            qty_str = self._format_decimal(qty_dec)
            side = "Buy" if str(getattr(trade, "action", "")).lower() == "buy" else "Sell"
            create_body = {
                "category": "spot",
                "symbol": symbol,
                "side": side,
                "orderType": "Market",
                "qty": qty_str,
                "timeInForce": "IOC",
                "marketUnit": "baseCoin",
            }
            create_result = await client.create_order(create_body)
            if create_result.get("retCode") != 0:
                return {"success": False, "message": create_result.get("retMsg") or "create_failed", "raw": create_result}

            new_order_id = str(((create_result.get("result") or {}).get("orderId")) or "")
            now = datetime.now(timezone.utc)

            current_raw: Any = getattr(trade, "bybit_raw", None)
            merged: Dict[str, Any] = dict(current_raw) if isinstance(current_raw, dict) else {}
            merged["replace"] = {"cancel": cancel_result, "create": create_result}
            trade.bybit_raw = merged
            trade.order_id = new_order_id or trade.order_id
            trade.outcome_status = "pending"
            trade.outcome_timestamp = None

            if new_order_id:
                self.order_status[new_order_id] = {
                    "order_id": new_order_id,
                    "symbol": symbol,
                    "status": "pending",
                    "timestamp": now.isoformat(),
                    "raw": create_result,
                }

            return {"success": True, "old_order_id": order_id, "new_order_id": new_order_id, "raw": merged.get("replace")}
        except Exception as e:
            logger.error(f"❌ Error reemplazando {order_id}: {e}")
            return {"success": False, "message": str(e)}

    def _bybit_client(self) -> Optional[BybitV5Client]:
        if not config.BYBIT_API_KEY or not config.BYBIT_API_SECRET:
            return None
        if self._bybit is not None:
            return self._bybit
        self._bybit = BybitV5Client(config.BYBIT_API_KEY, config.BYBIT_API_SECRET, base_url=config.REST_BASE_URL)
        return self._bybit

    async def record_balance(self, account_type: str = "UNIFIED", coin: Optional[str] = "USDT") -> Dict[str, Any]:
        client = self._bybit_client()
        if client is None:
            return {"success": False, "message": "Credenciales BYBIT_API_KEY/BYBIT_API_SECRET no configuradas"}

        payload = await client.wallet_balance(account_type=account_type, coin=coin)
        result = payload.get("result") or {}
        lst = result.get("list") or []
        row = lst[0] if isinstance(lst, list) and lst else {}

        def _to_float(value: Any) -> float:
            try:
                return float(value)
            except Exception:
                return 0.0

        total_equity = _to_float(row.get("totalEquity") or row.get("totalWalletBalance") or 0.0)
        available_balance = _to_float(row.get("totalAvailableBalance") or row.get("totalAvailableToWithdraw") or 0.0)

        with SessionLocal() as db:
            snap = BalanceSnapshot(
                timestamp=datetime.now(timezone.utc),
                account_type=account_type,
                coin=coin,
                total_equity=total_equity,
                available_balance=available_balance,
                raw=payload,
            )
            db.add(snap)
            db.commit()

        append_results_event(
            {
                "type": "balance",
                "account_type": account_type,
                "coin": coin,
                "total_equity": total_equity,
                "available_balance": available_balance,
                "http_status": payload.get("http_status"),
                "retCode": payload.get("retCode"),
                "retMsg": payload.get("retMsg"),
            },
            log_dir=os.path.join(os.path.dirname(__file__), '..', 'logs'),
        )

        return {"success": True, "balance": {"total_equity": total_equity, "available_balance": available_balance}, "raw": payload}

    async def _place_order(self, symbol: str, action: str, quantity: float, price: float, tp: float,
                           sl: float) -> Dict:
        max_retries = 3
        for attempt in range(max_retries):
            try:
                client = self._bybit_client()
                if client is None:
                    logger.error("❌ Credenciales de API no configuradas. No se puede colocar la orden.")
                    return {"success": False, "message": "Credenciales BYBIT_API_KEY/BYBIT_API_SECRET no configuradas"}

                rules = await self._get_instrument_rules(symbol)
                tick_size = float(rules.get("tick_size") or 0.01)
                qty_step = float(rules.get("qty_step") or float(config.MIN_TRADE_SIZE))

                side = "Buy" if action.lower() == "buy" else "Sell"

                order_type_raw = config.ORDER_TYPE or "Limit"
                order_type = {
                    "limit": "Limit",
                    "Limit": "Limit",
                    "market": "Market",
                    "Market": "Market",
                }.get(order_type_raw, "Limit")

                tif_raw = config.TIME_IN_FORCE or "GTC"
                time_in_force = {
                    "GoodTillCancel": "GTC",
                    "GTC": "GTC",
                    "ImmediateOrCancel": "IOC",
                    "IOC": "IOC",
                    "FillOrKill": "FOK",
                    "FOK": "FOK",
                    "PostOnly": "PostOnly",
                }.get(tif_raw, "GTC")
                if order_type == "Market":
                    time_in_force = "IOC"

                qty_str = self._format_decimal(self._quantize_to_step(quantity, qty_step, ROUND_DOWN))
                tp_str = self._format_decimal(self._quantize_to_step(tp, tick_size, ROUND_HALF_UP))
                sl_str = self._format_decimal(self._quantize_to_step(sl, tick_size, ROUND_HALF_UP))
                order_link_id = f"nertzh-{uuid.uuid4().hex[:20]}"

                body_params = {
                    "category": "spot",
                    "symbol": symbol,
                    "side": side,
                    "orderType": order_type,
                    "qty": qty_str,
                    "timeInForce": time_in_force,
                    "orderLinkId": order_link_id,
                }
                if order_type == "Limit":
                    price_str = self._format_decimal(self._quantize_to_step(price, tick_size, ROUND_HALF_UP))
                    body_params["price"] = price_str
                    body_params["takeProfit"] = tp_str
                    body_params["stopLoss"] = sl_str
                    body_params["tpOrderType"] = "Market"
                    body_params["slOrderType"] = "Market"
                if order_type == "Market":
                    body_params["marketUnit"] = "baseCoin"
                result = await client.create_order(body_params)
                http_status = result.get("http_status")
                ret_code = result.get("retCode")
                if http_status == 200 and ret_code == 0:
                    order_id = ((result.get("result") or {}).get("orderId")) or ""
                    logger.info(
                        f"✅ Orden colocada: {symbol} {side} {quantity:.6f} @ {price if order_type == 'Limit' else 'Market'}, TP={tp:.2f}, SL={sl:.2f}, OrderID={order_id}"
                    )
                    return {"success": True, "order_id": order_id, "order_link_id": order_link_id, "raw": result}

                if http_status == 429:
                    logger.warning(f"⚠️ Rate limit alcanzado. Reintentando en {2 ** attempt}s...")
                    await asyncio.sleep(2 ** attempt)
                    continue

                if order_type == "Limit" and http_status == 200 and ret_code in {170193, 170194}:
                    msg = str(result.get("retMsg") or "")
                    nums = []
                    cur = ""
                    for ch in msg:
                        if ch.isdigit() or ch == ".":
                            cur += ch
                        else:
                            if cur:
                                nums.append(cur)
                                cur = ""
                    if cur:
                        nums.append(cur)

                    if nums:
                        try:
                            limit_price = float(nums[-1])
                            if limit_price > 0:
                                current_price = float(body_params.get("price") or 0.0)
                                if ret_code == 170193:
                                    new_price = min(current_price, limit_price)
                                else:
                                    new_price = max(current_price, limit_price)
                                new_price = float(self._quantize_to_step(new_price, tick_size, ROUND_HALF_UP))
                                price = new_price
                                body_params["price"] = self._format_decimal(self._d(new_price))
                                await asyncio.sleep(0)
                                continue
                        except Exception:
                            pass

                error_msg = result.get("retMsg", "Error desconocido")
                logger.error(
                    f"❌ Error al colocar orden (HTTP {http_status}): retCode={ret_code}, retMsg={error_msg}"
                )
                return {"success": False, "message": error_msg, "raw": result}
            except Exception as e:
                logger.error(f"❌ Error en intento {attempt + 1}/{max_retries}: {str(e)}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
                else:
                    return {"success": False, "message": f"Error tras {max_retries} intentos: {str(e)}"}
        return {"success": False, "message": f"Falló tras {max_retries} intentos"}

    def reset_trades(self):
        self.positions = {symbol: [] for symbol in self.symbols}
        self.trade_id_counter = self._load_initial_trade_id()
        with SessionLocal() as db:
            db.query(Trade).delete()
            db.commit()
        logger.info("🧹 Trades reseteados")

    async def _save_results(self, symbol, trade_result):
        precision = 6
        with SessionLocal() as db:
            trades_all = (
                db.query(Trade)
                .order_by(Trade.timestamp.asc())
                .all()
            )
            latest_balance = (
                db.query(BalanceSnapshot)
                .order_by(BalanceSnapshot.timestamp.desc())
                .first()
            )

        trades_by_symbol: Dict[str, list[dict]] = {s: [] for s in self.symbols}
        for t in trades_all:
            outcome_status = self._normalize_outcome_status(getattr(t, "outcome_status", None))
            is_final = outcome_status == "final"
            trades_by_symbol.setdefault(t.symbol, []).append(
                {
                    "trade_id": t.trade_id,
                    "timestamp": t.timestamp.isoformat(),
                    "symbol": t.symbol,
                    "action": t.action,
                    "order_id": getattr(t, "order_id", None),
                    "entry_price": float(t.entry_price),
                    "exit_price": float(t.exit_price) if is_final else None,
                    "tp_price": float(getattr(t, "tp_price", 0.0) or 0.0) if getattr(t, "tp_price", None) is not None else None,
                    "sl_price": float(getattr(t, "sl_price", 0.0) or 0.0) if getattr(t, "sl_price", None) is not None else None,
                    "quantity": float(t.quantity),
                    "profit_loss": float(t.profit_loss) if is_final else None,
                    "outcome_status": outcome_status,
                    "outcome_timestamp": t.outcome_timestamp.isoformat() if isinstance(t.outcome_timestamp, datetime) else None,
                    "bybit_raw": getattr(t, "bybit_raw", None),
                    "decision": t.decision,
                    "combined": float(t.combined),
                    "ild": float(t.ild),
                    "egm": float(t.egm),
                    "rol": float(t.rol),
                    "pio": float(t.pio),
                    "ogm": float(t.ogm),
                    "risk_reward_ratio": float(t.risk_reward_ratio),
                }
            )

        finalized = [t for t in trades_all if self._normalize_outcome_status(getattr(t, "outcome_status", None)) == "final"]
        total_profit = sum((t.profit_loss or 0.0) for t in finalized if (t.profit_loss or 0.0) > 0)
        total_loss = sum((t.profit_loss or 0.0) for t in finalized if (t.profit_loss or 0.0) < 0)
        net_profit = total_profit + total_loss
        total_trades = len(finalized)
        wins = sum(1 for t in finalized if (t.profit_loss or 0.0) > 0)
        win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0.0
        avg_profit_per_trade = (net_profit / total_trades) if total_trades > 0 else 0.0

        by_symbol: Dict[str, dict] = {}
        for s in self.symbols:
            s_trades = trades_by_symbol.get(s, [])
            s_profit = sum((x["profit_loss"] or 0.0) for x in s_trades if (x.get("outcome_status") == "final" and (x["profit_loss"] or 0.0) > 0))
            s_loss = sum((x["profit_loss"] or 0.0) for x in s_trades if (x.get("outcome_status") == "final" and (x["profit_loss"] or 0.0) < 0))
            by_symbol[s] = {
                "profit": round(s_profit, precision),
                "loss": round(s_loss, precision),
                "net_profit": round(s_profit + s_loss, precision),
                "trade_count": sum(1 for x in s_trades if x.get("outcome_status") == "final"),
            }

        log_dir = os.path.join(os.path.dirname(__file__), '..', 'logs')
        previous = load_results_json(log_dir=log_dir)
        prev_initial = (previous.get("metadata") or {}).get("capital_inicial")

        capital_source = "configured_capital"
        capital_actual = float(self.capital)
        balance_meta: Dict[str, Any] = {}
        if latest_balance and (latest_balance.total_equity or 0.0) > 0:
            capital_source = "bybit_wallet_balance"
            capital_actual = float(latest_balance.total_equity)
            balance_meta = {
                "balance_timestamp": latest_balance.timestamp.isoformat(),
                "balance_total_equity": float(latest_balance.total_equity),
                "balance_available_balance": float(latest_balance.available_balance),
                "balance_account_type": latest_balance.account_type,
                "balance_coin": latest_balance.coin,
            }

        capital_inicial = _resolve_capital_inicial(prev_initial, capital_source, capital_actual)

        capital_pnl = capital_actual - capital_inicial

        results = {
            "metadata": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "capital_inicial": round(capital_inicial, precision),
                "capital_actual": round(capital_actual, precision),
                "capital_final": round(capital_actual, precision),
                "capital_source": capital_source,
                "capital_pnl": round(capital_pnl, precision),
                "total_pnl": round(net_profit, precision),
                "total_trades": total_trades,
                "iterations": self.iterations,
                "running": self.running,
                **balance_meta,
            },
            "summary": {
                "total_profit": round(total_profit, precision),
                "total_loss": round(total_loss, precision),
                "net_profit": round(net_profit, precision),
                "win_rate": round(win_rate, 2),
                "avg_profit_per_trade": round(avg_profit_per_trade, precision)
            },
            "by_symbol": by_symbol,
            "trades": trades_by_symbol,
        }
        if trade_result:
            results["metadata"]["last_trade_timestamp"] = trade_result.timestamp.isoformat()
            outcome_status = self._normalize_outcome_status(getattr(trade_result, "outcome_status", None))
            is_final = outcome_status == "final"
            results["last_trade"] = {
                "trade_id": trade_result.trade_id,
                "timestamp": trade_result.timestamp.isoformat(),
                "symbol": trade_result.symbol,
                "action": trade_result.action,
                "order_id": getattr(trade_result, "order_id", None),
                "entry_price": trade_result.entry_price,
                "exit_price": trade_result.exit_price if is_final else None,
                "tp_price": getattr(trade_result, "tp_price", None),
                "sl_price": getattr(trade_result, "sl_price", None),
                "quantity": trade_result.quantity,
                "profit_loss": trade_result.profit_loss if is_final else None,
                "outcome_status": outcome_status,
                "outcome_timestamp": trade_result.outcome_timestamp.isoformat() if isinstance(trade_result.outcome_timestamp, datetime) else None,
                "bybit_raw": getattr(trade_result, "bybit_raw", None),
                "decision": trade_result.decision,
                "combined": trade_result.combined,
                "ild": trade_result.ild,
                "egm": trade_result.egm,
                "rol": trade_result.rol,
                "pio": trade_result.pio,
                "ogm": trade_result.ogm,
                "risk_reward_ratio": trade_result.risk_reward_ratio
            }

        save_results(results, log_dir=log_dir)
        logger.info(f"📊 Resultados guardados: Total PNL={round(net_profit, precision)} USDT, Capital={round(capital_actual, precision)} USDT")

    def stop(self):
        self.running = False
        if self.ws:
            asyncio.create_task(self.ws.close())
        client = getattr(self, "_bybit", None)
        if client is not None:
            try:
                asyncio.create_task(client.aclose())
            except Exception:
                pass
        self._bybit = None
        task = self._start_task
        if task and not task.done():
            task.cancel()
        self._start_task = None
        support = getattr(self, "_support_task", None)
        if support is not None and not support.done():
            try:
                support.cancel()
            except Exception:
                pass
        self._support_task = None
        logger.info("🛑 Bot detenido.")


# FastAPI
bot = NertzMetalEngine()

@asynccontextmanager
async def lifespan(_: FastAPI):
    await bot._save_results(symbol=(bot.symbols[0] if bot.symbols else "BTCUSDT"), trade_result=None)
    try:
        preflight = await bot.preflight()
    except Exception as e:
        preflight = {"success": False, "message": str(e)}

    if preflight.get("success"):
        bot.schedule_start()
        bot.start_support_loop(interval_s=bot._support_interval_s)
    else:
        logger.error(f"❌ Preflight falló en startup: {preflight.get('message') or 'error'}")
    try:
        yield
    finally:
        bot.stop()

app = FastAPI(lifespan=lifespan)


@app.get("/settings")
async def get_settings():
    settings = {
        symbol: {
            "symbol": symbol,
            "capital": bot.capital,
            "risk_factor": config.RISK_FACTOR,
            "min_trade_size": config.MIN_TRADE_SIZE,
            "max_trade_size": config.MAX_TRADE_SIZE,
            "metrics": await get_metrics(symbol, next(get_db()))
        } for symbol in bot.symbols
    }
    return settings


@app.get("/ml/dataset/trades")
async def ml_dataset_trades(
    symbol: Optional[str] = None,
    limit: int = Query(default=5000, ge=1, le=200000),
    include_pending: bool = False,
    output: str = Query(default="json", pattern="^(json|csv)$"),
    db: Session = Depends(get_db),
):
    q = db.query(Trade)
    if isinstance(symbol, str) and symbol:
        q = q.filter(Trade.symbol == symbol)
    if not bool(include_pending):
        q = q.filter(Trade.outcome_status == "final")
    trades = q.order_by(Trade.timestamp.desc()).limit(int(limit)).all()

    rows: list[dict] = []
    for t in trades:
        pl = float(getattr(t, "profit_loss", 0.0) or 0.0)
        rows.append(
            {
                "timestamp": t.timestamp.isoformat() if getattr(t, "timestamp", None) else None,
                "symbol": t.symbol,
                "action": t.action,
                "decision": t.decision,
                "order_id": getattr(t, "order_id", None),
                "entry_price": float(t.entry_price or 0.0),
                "exit_price": float(getattr(t, "exit_price", 0.0) or 0.0),
                "tp_price": float(getattr(t, "tp_price", 0.0) or 0.0) if getattr(t, "tp_price", None) is not None else None,
                "sl_price": float(getattr(t, "sl_price", 0.0) or 0.0) if getattr(t, "sl_price", None) is not None else None,
                "quantity": float(t.quantity or 0.0),
                "profit_loss": pl,
                "win": 1 if pl > 0 else 0,
                "combined": float(t.combined or 0.0),
                "ild": float(t.ild or 0.0),
                "egm": float(t.egm or 0.0),
                "rol": float(t.rol or 0.0),
                "pio": float(t.pio or 0.0),
                "ogm": float(t.ogm or 0.0),
                "risk_reward_ratio": float(getattr(t, "risk_reward_ratio", 0.0) or 0.0),
                "outcome_status": getattr(t, "outcome_status", None),
                "outcome_timestamp": t.outcome_timestamp.isoformat() if isinstance(t.outcome_timestamp, datetime) else None,
            }
        )

    if output == "csv":
        buf = io.StringIO()
        fieldnames = list(rows[0].keys()) if rows else [
            "timestamp",
            "symbol",
            "action",
            "decision",
            "order_id",
            "entry_price",
            "exit_price",
            "tp_price",
            "sl_price",
            "quantity",
            "profit_loss",
            "win",
            "combined",
            "ild",
            "egm",
            "rol",
            "pio",
            "ogm",
            "risk_reward_ratio",
            "outcome_status",
            "outcome_timestamp",
        ]
        w = csv.DictWriter(buf, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)
        return PlainTextResponse(content=buf.getvalue(), media_type="text/csv")

    return {"count": len(rows), "rows": rows, "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/ml/status")
async def ml_status():
    return {
        "enabled": bool(getattr(config, "ML_ENABLED", False)),
        "models": bot._ml_models,
        "auto_agent_enabled": bool(getattr(config, "AUTO_AGENT_ENABLED", False)),
        "auto_agent": {
            "last_tick_ts": bot._agent_last_tick_ts,
            "recent_actions": list(bot._agent_events.get("actions") or []),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/ml/train")
async def ml_train(
    symbol: Optional[str] = None,
    min_samples: Optional[int] = Query(default=None, ge=10, le=50000),
    db: Session = Depends(get_db),
):
    return bot.train_ml_model_from_trades(db, symbol=symbol, min_samples=min_samples)


@app.get("/market_data/{symbol}")
async def get_market_data(symbol: str, db: Session = Depends(get_db)):
    candles = db.query(MarketData).filter(MarketData.symbol == symbol).order_by(MarketData.timestamp.desc()).limit(
        5).all()
    return {
        "symbol": symbol,
        "candles": [
            {"timestamp": c.timestamp.isoformat(), "open": c.open, "high": c.high, "low": c.low,
             "close": c.close, "volume": c.volume} for c in candles
        ]
    }


@app.get("/ticker/{symbol}")
async def get_ticker(symbol: str, db: Session = Depends(get_db)):
    ticker = db.query(MarketTicker).filter(MarketTicker.symbol == symbol).order_by(
        MarketTicker.timestamp.desc()).first()
    return {
        "symbol": symbol,
        "last_price": ticker.last_price if ticker else 0.0,
        "volume_24h": ticker.volume_24h if ticker else 0.0,
        "high_24h": ticker.high_24h if ticker else 0.0,
        "low_24h": ticker.low_24h if ticker else 0.0,
        "timestamp": ticker.timestamp.isoformat() if ticker else datetime.now(timezone.utc).isoformat()
    }


@app.get("/metrics/{symbol}")
async def get_metrics(symbol: str, db: Session = Depends(get_db)):
    candles = db.query(MarketData).filter(MarketData.symbol == symbol).order_by(MarketData.timestamp.desc()).limit(
        5).all()
    candle_data = [{"open": c.open, "high": c.high, "low": c.low, "close": c.close, "volume": c.volume} for c in
                   candles]
    orderbook = bot.orderbook_data.get(symbol, {"bids": [], "asks": []})
    ticker = bot.ticker_data.get(symbol, {"last_price": 0.0})
    now_ts = time.time()
    window_min = float(getattr(config, "METRICS_WINDOW_MINUTES", 15.0) or 15.0)
    window_s = max(60.0, window_min * 60.0)
    history_q = bot._metrics_raw_history.setdefault(symbol, deque())
    cutoff = now_ts - window_s
    while history_q:
        head = history_q[0]
        ts = head.get("ts") if isinstance(head, dict) else None
        if ts is None or float(ts) >= cutoff:
            break
        history_q.popleft()

    history_payload = []
    for h in history_q:
        if not isinstance(h, dict):
            continue
        history_payload.append({k: v for k, v in h.items() if k != "ts"})

    prev_entry = bot._last_weighted_liquidity.get(symbol)
    prev_liq = None
    prev_ts = None
    if isinstance(prev_entry, tuple) and len(prev_entry) == 2:
        prev_liq = prev_entry[0]
        prev_ts = prev_entry[1]

    ticker_payload: dict[str, Any] = dict(ticker)
    ticker_payload["orderbook_lambda"] = float(getattr(config, "ORDERBOOK_LAMBDA", 0.03) or 0.03)
    ticker_payload["orderbook_pct_band"] = float(getattr(config, "ORDERBOOK_PCT_BAND", 0.015) or 0.015)
    ticker_payload["ild_target_move"] = float(getattr(config, "ILD_TARGET_MOVE", 0.002) or 0.002)
    ticker_payload["metric_history"] = history_payload
    ticker_payload["prev_weighted_liquidity"] = prev_liq
    ticker_payload["rol_dt_s"] = (now_ts - float(prev_ts)) if prev_ts else None
    ticker_payload["formulas"] = getattr(config, "FORMULAS", {}) or {}

    metrics = calculate_metrics(candle_data, orderbook, ticker_payload, depth=int(getattr(config, "ORDERBOOK_DEPTH", 50) or 50))
    return {
        "symbol": symbol,
        "metrics": metrics,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.get("/combined/{symbol}")
async def get_combined(symbol: str, db: Session = Depends(get_db)):
    candles = (
        db.query(MarketData)
        .filter(MarketData.symbol == symbol)
        .order_by(MarketData.timestamp.desc())
        .limit(5)
        .all()
    )
    orderbook_row = (
        db.query(Orderbook)
        .filter(Orderbook.symbol == symbol)
        .order_by(Orderbook.timestamp.desc())
        .first()
    )
    ticker_row = (
        db.query(MarketTicker)
        .filter(MarketTicker.symbol == symbol)
        .order_by(MarketTicker.timestamp.desc())
        .first()
    )
    recent = list(bot.recent_trades.get(symbol) or [])[-10:]
    return {
        "symbol": symbol,
        "candles": [
            {
                "timestamp": c.timestamp.isoformat() if c.timestamp else None,
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "volume": float(c.volume),
            }
            for c in candles
        ],
        "orderbook": {
            "timestamp": orderbook_row.timestamp.isoformat() if orderbook_row else None,
            "bids": (orderbook_row.bids if orderbook_row else []),
            "asks": (orderbook_row.asks if orderbook_row else []),
        },
        "ticker": {
            "timestamp": ticker_row.timestamp.isoformat() if ticker_row else None,
            "last_price": float(ticker_row.last_price) if ticker_row else 0.0,
            "volume_24h": float(ticker_row.volume_24h) if ticker_row else 0.0,
            "high_24h": float(ticker_row.high_24h) if ticker_row else 0.0,
            "low_24h": float(ticker_row.low_24h) if ticker_row else 0.0,
        },
        "recent_trades": recent,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/ild/{symbol}")
async def get_ild(symbol: str, db: Session = Depends(get_db)):
    candles = (
        db.query(MarketData)
        .filter(MarketData.symbol == symbol)
        .order_by(MarketData.timestamp.desc())
        .limit(50)
        .all()
    )
    candle_data = [{"open": c.open, "high": c.high, "low": c.low, "close": c.close, "volume": c.volume} for c in candles]
    orderbook = bot.orderbook_data.get(symbol, {"bids": [], "asks": []})
    ticker = bot.ticker_data.get(symbol, {"last_price": 0.0})
    recent = list(bot.recent_trades.get(symbol) or [])
    metrics = calculate_discovery_metrics(candle_data, orderbook, ticker, recent)
    return {
        "symbol": symbol,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ild": float(metrics.get("ild") or 0.0),
        "components": metrics.get("combined") or {},
    }


@app.get("/rol/{symbol}")
async def get_rol(symbol: str, db: Session = Depends(get_db)):
    candles = (
        db.query(MarketData)
        .filter(MarketData.symbol == symbol)
        .order_by(MarketData.timestamp.desc())
        .limit(50)
        .all()
    )
    candle_data = [{"open": c.open, "high": c.high, "low": c.low, "close": c.close, "volume": c.volume} for c in candles]
    orderbook = bot.orderbook_data.get(symbol, {"bids": [], "asks": []})
    ticker = bot.ticker_data.get(symbol, {"last_price": 0.0})
    recent = list(bot.recent_trades.get(symbol) or [])
    metrics = calculate_discovery_metrics(candle_data, orderbook, ticker, recent)
    return {
        "symbol": symbol,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "rol": float(metrics.get("rol") or 0.0),
        "components": metrics.get("combined") or {},
    }


@app.get("/discovery/metrics/{symbol}")
async def get_discovery_metrics(symbol: str, db: Session = Depends(get_db)):
    candles = (
        db.query(MarketData)
        .filter(MarketData.symbol == symbol)
        .order_by(MarketData.timestamp.desc())
        .limit(500)
        .all()
    )
    candle_data = [{"open": c.open, "high": c.high, "low": c.low, "close": c.close, "volume": c.volume} for c in candles]
    orderbook = bot.orderbook_data.get(symbol, {"bids": [], "asks": []})
    ticker = bot.ticker_data.get(symbol, {"last_price": 0.0})
    recent = list(bot.recent_trades.get(symbol) or [])
    metrics = calculate_discovery_metrics(candle_data, orderbook, ticker, recent)
    return {
        "symbol": symbol,
        "metrics": metrics,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/profit")
async def get_profit(db: Session = Depends(get_db)):
    precision = 6
    trades_all = db.query(Trade).order_by(Trade.timestamp.asc()).all()
    latest_balance = db.query(BalanceSnapshot).order_by(BalanceSnapshot.timestamp.desc()).first()

    finalized = [t for t in trades_all if (getattr(t, "outcome_status", None) == "final")]
    total_profit = sum((t.profit_loss or 0.0) for t in finalized if (t.profit_loss or 0.0) > 0)
    total_loss = sum((t.profit_loss or 0.0) for t in finalized if (t.profit_loss or 0.0) < 0)
    total_trades = len(finalized)
    wins = sum(1 for t in finalized if (t.profit_loss or 0.0) > 0)
    win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0.0

    log_dir = os.path.join(os.path.dirname(__file__), '..', 'logs')
    previous = load_results_json(log_dir=log_dir)
    prev_initial = (previous.get("metadata") or {}).get("capital_inicial")

    capital_source = "configured_capital"
    capital_actual = float(bot.capital)
    if latest_balance and (latest_balance.total_equity or 0.0) > 0:
        capital_source = "bybit_wallet_balance"
        capital_actual = float(latest_balance.total_equity)

    capital_inicial = _resolve_capital_inicial(prev_initial, capital_source, capital_actual)

    capital_pnl = capital_actual - capital_inicial
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "capital_inicial": round(capital_inicial, precision),
        "capital_actual": round(capital_actual, precision),
        "capital_source": capital_source,
        "capital_pnl": round(capital_pnl, precision),
        "total_pnl": round(total_profit + total_loss, precision),
        "total_profit": round(total_profit, precision),
        "total_loss": round(total_loss, precision),
        "net_profit": round(total_profit + total_loss, precision),
        "win_rate": round(win_rate, 2),
        "by_symbol": {
            symbol: {
                "profit": round(sum(t.profit_loss for t in trades_all if t.symbol == symbol and (t.profit_loss or 0.0) > 0), precision),
                "loss": round(sum(t.profit_loss for t in trades_all if t.symbol == symbol and (t.profit_loss or 0.0) < 0), precision),
                "net_profit": round(sum(t.profit_loss for t in trades_all if t.symbol == symbol), precision),
                "trade_count": len([t for t in trades_all if t.symbol == symbol]),
            } for symbol in bot.symbols
        }
    }


@app.post("/config/update_thresholds")
async def update_thresholds(egm_buy_threshold: float, egm_sell_threshold: float):
    config.EGM_BUY_THRESHOLD = egm_buy_threshold
    config.EGM_SELL_THRESHOLD = egm_sell_threshold
    logger.info(f"✅ Umbrales actualizados: buy={egm_buy_threshold}, sell={egm_sell_threshold}")
    return {"message": "Umbrales actualizados"}


@app.get("/orderbook/{symbol}")
async def get_orderbook(symbol: str, db: Session = Depends(get_db)):
    orderbook = bot.orderbook_data.get(symbol, {"bids": [], "asks": []})
    return {
        "symbol": symbol,
        "bids": orderbook["bids"],
        "asks": orderbook["asks"],
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/candles/{symbol}/{limit}")
async def get_candles(symbol: str, limit: int = 5, db: Session = Depends(get_db)):
    candles = db.query(MarketData).filter(MarketData.symbol == symbol).order_by(MarketData.timestamp.desc()).limit(
        limit).all()
    return {
        "symbol": symbol,
        "candles": [
            {"timestamp": c.timestamp.isoformat(), "open": c.open, "high": c.high, "low": c.low,
             "close": c.close, "volume": c.volume} for c in candles
        ],
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/trades/{symbol}")
async def get_trades(symbol: str, db: Session = Depends(get_db)):
    trades = bot.positions.get(symbol, [])
    return {
        "symbol": symbol,
        "trades": trades,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/last_trade/{symbol}")
async def get_last_trade(symbol: str, db: Session = Depends(get_db)):
    trades = bot.positions.get(symbol, [])
    if not trades:
        last = db.query(Trade).filter_by(symbol=symbol).order_by(Trade.timestamp.desc()).first()
        if last:
            outcome_status = getattr(last, "outcome_status", None) or "legacy"
            is_final = outcome_status == "final"
            trades = [{
                "trade_id": last.trade_id,
                "timestamp": last.timestamp.isoformat(),
                "symbol": last.symbol,
                "action": last.action,
                "entry_price": float(last.entry_price),
                "exit_price": float(last.exit_price) if is_final else None,
                "tp_price": float(getattr(last, "tp_price", 0.0) or 0.0) if getattr(last, "tp_price", None) is not None else None,
                "sl_price": float(getattr(last, "sl_price", 0.0) or 0.0) if getattr(last, "sl_price", None) is not None else None,
                "quantity": float(last.quantity),
                "profit_loss": float(last.profit_loss) if is_final else None,
                "order_id": getattr(last, "order_id", None),
                "outcome_status": outcome_status,
                "outcome_timestamp": last.outcome_timestamp.isoformat() if isinstance(last.outcome_timestamp, datetime) else None,
                "decision": last.decision,
                "combined": float(last.combined),
                "ild": float(last.ild),
                "egm": float(last.egm),
                "rol": float(last.rol),
                "pio": float(last.pio),
                "ogm": float(last.ogm),
                "risk_reward_ratio": float(last.risk_reward_ratio),
            }]
    return {
        "symbol": symbol,
        "last_trade": trades[-1] if trades else None,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.post("/execute_trade/{symbol}")
async def execute_trade(symbol: str, collect_only: bool = False, force_trade: bool = False, db: Session = Depends(get_db)):
    if symbol not in bot.symbols:
        return {"message": f"⚠️ Símbolo no soportado: {symbol}", "timestamp": datetime.now(timezone.utc).isoformat()}
    await bot._core_cycle(symbol, db, collect_only=collect_only, force_trade=force_trade)
    return {
        "message": f"✅ Ciclo ejecutado para {symbol}",
        "collect_only": collect_only,
        "force_trade": force_trade,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/hft/start/{symbol}")
async def start_hft(symbol: str, interval_ms: int = 250, collect_only: bool = True):
    if symbol not in bot.symbols:
        return {"message": f"⚠️ Símbolo no soportado: {symbol}", "timestamp": datetime.now(timezone.utc).isoformat()}
    started = bot.start_hft(symbol, interval_ms=max(0, int(interval_ms)), collect_only=bool(collect_only))
    return {
        "message": "✅ HFT iniciado" if started else "⚠️ HFT ya estaba corriendo",
        "symbol": symbol,
        "interval_ms": max(0, int(interval_ms)),
        "collect_only": bool(collect_only),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/hft/stop/{symbol}")
async def stop_hft(symbol: str):
    if symbol not in bot.symbols:
        return {"message": f"⚠️ Símbolo no soportado: {symbol}", "timestamp": datetime.now(timezone.utc).isoformat()}
    stopped = bot.stop_hft(symbol)
    return {
        "message": "🛑 HFT detenido" if stopped else "⚠️ HFT no estaba corriendo",
        "symbol": symbol,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/hft/run/{symbol}")
async def run_hft(symbol: str, cycles: int = 100, interval_ms: int = 250, collect_only: bool = True):
    if symbol not in bot.symbols:
        return {"message": f"⚠️ Símbolo no soportado: {symbol}", "timestamp": datetime.now(timezone.utc).isoformat()}
    asyncio.create_task(bot.run_cycles(symbol, cycles=int(cycles), interval_ms=max(0, int(interval_ms)), collect_only=bool(collect_only)))
    return {
        "message": "✅ HFT run programado",
        "symbol": symbol,
        "cycles": int(cycles),
        "interval_ms": max(0, int(interval_ms)),
        "collect_only": bool(collect_only),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/balance")
async def get_balance(account_type: str = "UNIFIED", coin: str = "USDT"):
    return await bot.record_balance(account_type=account_type, coin=coin)


@app.get("/config")
async def get_config():
    return {
        "symbol": config.SYMBOL,
        "timeframe": config.TIMEFRAME,
        "order_type": config.ORDER_TYPE,
        "time_in_force": config.TIME_IN_FORCE,
        "orderbook_depth": config.ORDERBOOK_DEPTH,
        "trading_env": config.TRADING_ENV,
        "rest_base_url": config.REST_BASE_URL,
        "ws_public_url": config.WS_PUBLIC_URL,
        "credentials_configured": bool(config.BYBIT_API_KEY and config.BYBIT_API_SECRET),
        "capital_usdt": config.CAPITAL_USDT,
        "risk_factor": config.RISK_FACTOR,
        "min_trade_size": config.MIN_TRADE_SIZE,
        "max_trade_size": config.MAX_TRADE_SIZE,
        "fee_rate": config.FEE_RATE,
        "tp_percentage": config.TP_PERCENTAGE,
        "sl_percentage": config.SL_PERCENTAGE,
        "egm_buy_threshold": config.EGM_BUY_THRESHOLD,
        "egm_sell_threshold": config.EGM_SELL_THRESHOLD,
        "combined_buy_threshold": float(getattr(config, "COMBINED_BUY_THRESHOLD", 2.0)),
        "combined_sell_threshold": float(getattr(config, "COMBINED_SELL_THRESHOLD", -2.0)),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.post("/config/update_all")
async def update_all_config(config_data: dict):
    if "capital_usdt" in config_data:
        config.CAPITAL_USDT = float(config_data["capital_usdt"]) if float(
            config_data["capital_usdt"]) > 0 else config.CAPITAL_USDT
    if "risk_factor" in config_data:
        config.RISK_FACTOR = max(0.0, min(1.0, float(config_data["risk_factor"])))
    if "egm_buy_threshold" in config_data:
        config.EGM_BUY_THRESHOLD = float(config_data["egm_buy_threshold"])
    if "egm_sell_threshold" in config_data:
        config.EGM_SELL_THRESHOLD = float(config_data["egm_sell_threshold"])
    if "combined_buy_threshold" in config_data:
        config.COMBINED_BUY_THRESHOLD = float(config_data["combined_buy_threshold"])
    if "combined_sell_threshold" in config_data:
        config.COMBINED_SELL_THRESHOLD = float(config_data["combined_sell_threshold"])
    logger.info(f"✅ Configuración actualizada: {config_data}")
    return {"message": "Configuración actualizada", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/admin/full_reset")
async def admin_full_reset(
    sample_size: int = 500,
    alpha: float = 1.0,
    cancel_bybit_orders: bool = True,
    db: Session = Depends(get_db),
):
    calibrate = bot.force_calibrate_thresholds(db, sample_size=int(sample_size), alpha=float(alpha))
    env_update = _persist_thresholds_to_env(os.path.join(PROJECT_ROOT, ".env"))

    cancel_result = None
    if bool(cancel_bybit_orders):
        cancel_result = await bot.cancel_all_open_orders(symbol=None, limit=200)

    bot.stop()
    support_task = getattr(bot, "_support_task", None)
    if support_task is not None and not support_task.done():
        try:
            support_task.cancel()
        except Exception:
            pass

    wiped = bot.wipe_database(db)
    bot.reset_runtime_state()
    results_path = bot.reset_results_json()

    bot.schedule_start()
    bot.start_support_loop(interval_s=2.0)

    return {
        "success": True,
        "thresholds": bot._thresholds_payload(),
        "calibration": calibrate,
        "persist_env": env_update,
        "cancel_bybit_orders": cancel_result,
        "wiped": wiped,
        "results_json": results_path,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/start")
async def start_bot():
    if not bot.running:
        bot.schedule_start()
        bot.start_support_loop(interval_s=2.0)
        return {"message": "✅ Bot iniciado", "timestamp": datetime.now(timezone.utc).isoformat()}
    bot.start_support_loop(interval_s=2.0)
    return {"message": "⚠️ Bot ya está corriendo", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/stop")
async def stop_bot():
    if bot.running:
        bot.stop()
        return {"message": "🛑 Bot detenido", "timestamp": datetime.now(timezone.utc).isoformat()}
    return {"message": "⚠️ Bot ya está detenido", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/status")
async def get_status():
    support_task = getattr(bot, "_support_task", None)
    return {
        "running": bot.running,
        "iterations": bot.iterations,
        "symbols": bot.symbols,
        "support_loop_running": bool(support_task is not None and not support_task.done()),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.get("/validation")
async def get_validation(db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    now_s = time.time()

    def _to_utc_aware(dt: Optional[datetime]) -> Optional[datetime]:
        if not isinstance(dt, datetime):
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        try:
            return dt.astimezone(timezone.utc)
        except Exception:
            return dt

    start_task = getattr(bot, "_start_task", None)
    support_task = getattr(bot, "_support_task", None)
    ws = getattr(bot, "ws", None)
    ws_open = bool(ws is not None and not getattr(ws, "closed", False))

    layer1 = {
        "ok": bool(bot.running and (start_task is not None and not start_task.done()) and (support_task is not None and not support_task.done()) and ws_open),
        "running_flag": bool(bot.running),
        "start_task_running": bool(start_task is not None and not start_task.done()),
        "support_task_running": bool(support_task is not None and not support_task.done()),
        "websocket_open": bool(ws_open),
    }

    market: Dict[str, Any] = {}
    market_ok = True
    for sym in bot.symbols:
        last_ob = db.query(Orderbook).filter(Orderbook.symbol == sym).order_by(Orderbook.timestamp.desc()).first()
        last_tk = db.query(MarketTicker).filter(MarketTicker.symbol == sym).order_by(MarketTicker.timestamp.desc()).first()
        last_kl = db.query(MarketData).filter(MarketData.symbol == sym).order_by(MarketData.timestamp.desc()).first()

        ob_ts = _to_utc_aware(getattr(last_ob, "timestamp", None))
        tk_ts = _to_utc_aware(getattr(last_tk, "timestamp", None))
        kl_ts = _to_utc_aware(getattr(last_kl, "timestamp", None))

        ob_age = (now - ob_ts).total_seconds() if ob_ts else None
        tk_age = (now - tk_ts).total_seconds() if tk_ts else None
        kl_age = (now - kl_ts).total_seconds() if kl_ts else None

        entry = {"orderbook_age_s": ob_age, "ticker_age_s": tk_age, "kline_age_s": kl_age}
        market[sym] = entry

        for age in (ob_age, tk_age):
            if age is None or age > 15.0:
                market_ok = False

    layer2 = {"ok": bool(market_ok), "by_symbol": market}

    db_pending_trades = (
        db.query(Trade)
        .filter(Trade.outcome_status.in_(["pending", "partial", "filled"]))
        .order_by(Trade.timestamp.desc())
        .limit(500)
        .all()
    )
    tracked_order_ids: set[str] = {
        str(t.order_id)
        for t in db_pending_trades
        if isinstance(getattr(t, "order_id", None), str) and str(t.order_id).strip()
    }
    tracked_link_ids: set[str] = set()
    for t in db_pending_trades:
        raw = getattr(t, "bybit_raw", None)
        if isinstance(raw, dict):
            link = raw.get("order_link_id") or raw.get("orderLinkId")
            if isinstance(link, str) and link.strip():
                tracked_link_ids.add(link.strip())

    layer3 = {
        "ok": True,
        "db_pending_trades": len(db_pending_trades),
        "tracked_order_ids": len(tracked_order_ids),
        "tracked_link_ids": len(tracked_link_ids),
        "now": now.isoformat(),
        "now_s": now_s,
    }

    client = bot._bybit_client()
    bybit_orders: list[dict] = []
    if client:
        merged: Dict[str, Dict[str, Any]] = {}
        for sym in bot.symbols:
            try:
                payload = await client.get_open_orders_merged(category="spot", symbol=sym, limit=200)
                if payload.get("retCode") != 0:
                    continue
                rows = list(((payload.get("result", {}) or {}).get("list", []) or []))
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    oid = row.get("orderId")
                    if isinstance(oid, str) and oid:
                        merged[oid] = row
            except Exception:
                continue
        bybit_orders = list(merged.values())

    bybit_open_ids: set[str] = set()
    orphan_count = 0
    orphan_bot_candidates = 0
    for o in bybit_orders:
        if not isinstance(o, dict):
            continue
        oid = o.get("orderId")
        if not isinstance(oid, str) or not oid:
            continue
        bybit_open_ids.add(oid)
        link = o.get("orderLinkId")
        link_str = str(link) if isinstance(link, str) and link else ""
        if oid not in tracked_order_ids and not (link_str and link_str in tracked_link_ids):
            orphan_count += 1
            if link_str.startswith("nertzh-"):
                orphan_bot_candidates += 1

    layer4_ok = (orphan_bot_candidates == 0)
    layer4 = {
        "ok": bool(layer4_ok),
        "bybit_open_orders": len(bybit_open_ids),
        "orphan_open_orders": orphan_count,
        "orphan_bot_candidates": orphan_bot_candidates,
        "linked_open_orders": sum(
            1
            for o in bybit_orders
            if isinstance(o, dict)
            and isinstance(o.get("orderId"), str)
            and (
                (o.get("orderId") in tracked_order_ids)
                or (
                    isinstance(o.get("orderLinkId"), str)
                    and str(o.get("orderLinkId")).strip()
                    and str(o.get("orderLinkId")).strip() in tracked_link_ids
                )
            )
        ),
    }

    overall = bool(layer1["ok"] and layer2["ok"] and layer3["ok"] and layer4["ok"])
    return {"ok": overall, "layer1_process": layer1, "layer2_market_data": layer2, "layer3_db": layer3, "layer4_orders": layer4, "timestamp": now.isoformat()}

@app.get("/orders/status")
async def get_orders_status(db: Session = Depends(get_db)):
    client = bot._bybit_client()
    bybit_orders: list[dict] = []
    if client:
        merged: Dict[str, Dict[str, Any]] = {}
        for sym in bot.symbols:
            try:
                payload = await client.get_open_orders_merged(category="spot", symbol=sym, limit=200)
                if payload.get("retCode") != 0:
                    continue
                rows = list(((payload.get("result", {}) or {}).get("list", []) or []))
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    oid = row.get("orderId")
                    if isinstance(oid, str) and oid:
                        merged[oid] = row
            except Exception:
                continue
        bybit_orders = list(merged.values())

    pending_trades = (
        db.query(Trade)
        .filter(Trade.outcome_status.in_(["pending", "partial", "filled"]))
        .order_by(Trade.timestamp.desc())
        .limit(200)
        .all()
    )

    tracked_order_ids: set[str] = {
        str(t.order_id)
        for t in pending_trades
        if isinstance(getattr(t, "order_id", None), str) and str(t.order_id).strip()
    }
    tracked_link_ids: set[str] = set()
    for t in pending_trades:
        raw = getattr(t, "bybit_raw", None)
        if isinstance(raw, dict):
            link = raw.get("order_link_id") or raw.get("orderLinkId")
            if isinstance(link, str) and link.strip():
                tracked_link_ids.add(link.strip())

    bybit_open_ids: set[str] = set()
    bybit_open_link_ids: set[str] = set()
    bybit_orders_payload: list[dict] = []
    orphan_orders_payload: list[dict] = []
    for o in bybit_orders:
        if not isinstance(o, dict):
            continue
        oid = o.get("orderId")
        if not isinstance(oid, str) or not oid:
            continue
        bybit_open_ids.add(oid)
        symbol = o.get("symbol")
        sym = str(symbol) if isinstance(symbol, str) and symbol else ""
        status_raw = o.get("orderStatus")
        status = str(status_raw) if status_raw is not None else ""
        side_raw = o.get("side")
        side = str(side_raw) if side_raw is not None else ""
        link_raw = o.get("orderLinkId")
        link = str(link_raw) if link_raw is not None else ""
        if link.strip():
            bybit_open_link_ids.add(link.strip())
        stop_type_raw = o.get("stopOrderType")
        stop_type = str(stop_type_raw) if stop_type_raw is not None else ""
        tracked_in_db = (oid in tracked_order_ids) or (link.strip() and link.strip() in tracked_link_ids)
        order_payload = {
            "orderId": oid,
            "symbol": sym,
            "status": status,
            "side": side,
            "orderLinkId": link,
            "orderFilter": o.get("orderFilter"),
            "orderType": o.get("orderType"),
            "timeInForce": o.get("timeInForce"),
            "stopOrderType": stop_type,
            "triggerPrice": o.get("triggerPrice"),
            "takeProfit": o.get("takeProfit"),
            "stopLoss": o.get("stopLoss"),
            "qty": o.get("qty"),
            "price": o.get("price"),
            "avgPrice": o.get("avgPrice"),
            "cumExecQty": o.get("cumExecQty"),
            "createdTime": o.get("createdTime"),
            "updatedTime": o.get("updatedTime"),
            "tracked_in_db": tracked_in_db,
        }
        bybit_orders_payload.append(order_payload)
        if not tracked_in_db:
            orphan_orders_payload.append(order_payload)
        if sym:
            bot.order_status[oid] = {
                "order_id": oid,
                "symbol": sym,
                "status": status.lower(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "raw": o,
            }

    return {
        "bybit_open_orders": len(bybit_orders),
        "db_pending_trades": len(pending_trades),
        "linked_open_orders": sum(1 for row in bybit_orders_payload if bool(row.get("tracked_in_db"))),
        "orphan_open_orders": len(orphan_orders_payload),
        "bybit_orders": bybit_orders_payload,
        "orphan_bybit_orders": orphan_orders_payload[:50],
        "db_pending": [
            {
                "trade_id": t.trade_id,
                "order_id": t.order_id,
                "symbol": t.symbol,
                "action": t.action,
                "status": t.outcome_status,
                "timestamp": t.timestamp.isoformat(),
                "seconds_elapsed": (datetime.now(timezone.utc) - (t.timestamp.replace(tzinfo=timezone.utc) if t.timestamp.tzinfo is None else t.timestamp)).total_seconds(),
                "present_in_bybit_open_orders": (
                    (str(t.order_id) in bybit_open_ids)
                    if isinstance(getattr(t, "order_id", None), str)
                    else False
                )
                or (
                    isinstance(getattr(t, "bybit_raw", None), dict)
                    and isinstance((t.bybit_raw or {}).get("order_link_id"), str)
                    and str((t.bybit_raw or {}).get("order_link_id") or "").strip() in bybit_open_link_ids
                )
                or (
                    isinstance(getattr(t, "bybit_raw", None), dict)
                    and isinstance((t.bybit_raw or {}).get("orderLinkId"), str)
                    and str((t.bybit_raw or {}).get("orderLinkId") or "").strip() in bybit_open_link_ids
                ),
            }
            for t in pending_trades
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/orders/sync")
async def sync_orders(db: Session = Depends(get_db)):
    try:
        result = await bot.sync_open_orders(db)
        if result.get("success"):
            return {
                "success": True,
                "message": "Órdenes sincronizadas correctamente",
                "details": result.get("results", {}),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        return {
            "success": False,
            "message": result.get("message", "Error desconocido"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"❌ Error en sync_orders: {e}")
        return {
            "success": False,
            "message": f"Error interno: {str(e)}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


@app.get("/order_status/{order_id}")
async def get_order_status(order_id: str):
    order_status = bot.order_status.get(order_id)
    if order_status:
        return order_status
    return {"message": "Orden no encontrada", "order_id": order_id, "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/health")
async def health_check():
    return {"status": "healthy" if bot.running else "unhealthy", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.get("/exchange/open_orders/{symbol}")
async def exchange_open_orders(symbol: str, limit: int = 200):
    client = bot._bybit_client()
    if client is None:
        return {"success": False, "message": "Credenciales BYBIT_API_KEY/BYBIT_API_SECRET no configuradas"}
    try:
        payload = await client.get_open_orders_merged(category="spot", symbol=symbol, limit=int(limit))
        return {"success": True, "symbol": symbol, "payload": payload, "timestamp": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        return {"success": False, "symbol": symbol, "message": str(e), "timestamp": datetime.now(timezone.utc).isoformat()}


# Ejecución principal
server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=8081))


async def main():
    try:
        logger.info("🚀 Iniciando bot y servidor API...")
        bot.schedule_start()
        await server.serve()
    except Exception as e:
        logger.error(f"❌ Error crítico en main(): {e}")
        await server.shutdown()
    except KeyboardInterrupt:
        logger.info("🛑 Interrupción del usuario detectada.")
        await server.shutdown()
        bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
