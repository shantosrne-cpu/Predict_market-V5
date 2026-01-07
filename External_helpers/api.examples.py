# Configuración de la API con FastAPI
app = FastAPI()
bot = NertzMetalEngine()

run_mode = os.getenv("NERTZ_RUN_MODE", "full").strip().lower()
if run_mode in {"api", "api_only", "api-only"}:
    bot.running = False
    bot.paused = True


@app.get("/settings")
async def get_settings(db: Session = Depends(get_db)) -> Dict[str, Dict[str, Any]]:
    settings = {
        symbol: {
            "symbol": symbol,
            "capital": bot.capital,
            "risk_factor": config.RISK_FACTOR,
            "min_trade_size": config.MIN_TRADE_SIZE,
            "max_trade_size": config.MAX_TRADE_SIZE,
            "metrics": await get_metrics(symbol, db)
        } for symbol in bot.symbols
    }
    return settings


@app.get("/market_data/{symbol}")
async def get_market_data(symbol: str, db: Session = Depends(get_db)) -> Dict[
    str, Union[str, List[Dict[str, Union[str, float]]]]]:
    candles = db.query(MarketData).filter(MarketData.symbol == symbol).order_by(MarketData.timestamp.desc()).limit(
        5).all()
    return {
        "symbol": symbol,
        "candles": [{"timestamp": c.timestamp.isoformat(), "open": float(c.open), "high": float(c.high),
                     "low": float(c.low), "close": float(c.close), "volume": float(c.volume)} for c in candles],
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/ticker/{symbol}")
async def get_ticker(symbol: str, db: Session = Depends(get_db)) -> Dict[str, Union[str, float]]:
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
async def get_metrics(symbol: str, db: Session = Depends(get_db)) -> Dict[str, Union[str, Dict[str, float]]]:
    await bot._refresh_market_state_if_stale(symbol)
    candles = db.query(MarketData).filter(MarketData.symbol == symbol).order_by(MarketData.timestamp.desc()).limit(
        5).all()
    candle_data = [{"open": float(c.open), "high": float(c.high), "low": float(c.low), "close": float(c.close),
                    "volume": float(c.volume)} for c in candles]
    orderbook = bot.orderbook_data.get(symbol, {"bids": [], "asks": []})
    ticker = bot.ticker_data.get(symbol, {"last_price": 0.0})
    metrics = calculate_metrics(candle_data, orderbook, ticker)
    return {
        "symbol": symbol,
        "metrics": metrics,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/signals/{symbol}")
async def get_signals(symbol: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    await bot._refresh_market_state_if_stale(symbol)
    candles = db.query(MarketData).filter(MarketData.symbol == symbol).order_by(MarketData.timestamp.desc()).limit(5).all()
    candle_data = [
        {"open": float(c.open), "high": float(c.high), "low": float(c.low), "close": float(c.close), "volume": float(c.volume)}
        for c in candles
    ]
    orderbook = bot.orderbook_data.get(symbol, {"bids": [], "asks": []})
    ticker = bot.ticker_data.get(symbol, {"last_price": 0.0})
    metrics = calculate_metrics(candle_data, orderbook, ticker)
    decision = bot._determine_decision(symbol, metrics)
    scores = bot._compute_signal_scores(metrics)

    active_position = None
    for p in bot.positions.get(symbol, []) or []:
        if p.get("status") in ["pending", "open", "closing"]:
            active_position = p
            break

    return {
        "symbol": symbol,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "decision": decision,
        "scores": {
            **scores,
            "egm_buy_threshold": float(config.EGM_BUY_THRESHOLD),
            "egm_sell_threshold": float(config.EGM_SELL_THRESHOLD),
        },
        "metrics": metrics,
        "market": {
            "last_price": float(ticker.get("last_price", 0.0) or 0.0),
            "orderbook_bids": len(orderbook.get("bids") or []),
            "orderbook_asks": len(orderbook.get("asks") or []),
            "candles_used": len(candle_data),
        },
        "position": active_position,
    }


@app.get("/metrics/{symbol}/{metric_name}")
async def get_metric_value(symbol: str, metric_name: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    await bot._refresh_market_state_if_stale(symbol)
    candles = db.query(MarketData).filter(MarketData.symbol == symbol).order_by(MarketData.timestamp.desc()).limit(5).all()
    candle_data = [
        {"open": float(c.open), "high": float(c.high), "low": float(c.low), "close": float(c.close), "volume": float(c.volume)}
        for c in candles
    ]
    orderbook = bot.orderbook_data.get(symbol, {"bids": [], "asks": []})
    ticker = bot.ticker_data.get(symbol, {"last_price": 0.0})
    metrics = calculate_metrics(candle_data, orderbook, ticker)
    key = str(metric_name or "").strip().lower()
    value = metrics.get(key)
    if value is None:
        return {
            "symbol": symbol,
            "metric": key,
            "value": None,
            "available": sorted(list(metrics.keys())),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    return {
        "symbol": symbol,
        "metric": key,
        "value": float(value),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/config/update_thresholds")
async def update_thresholds(egm_buy_threshold: float, egm_sell_threshold: float) -> Dict[str, str]:
    config.EGM_BUY_THRESHOLD = egm_buy_threshold
    config.EGM_SELL_THRESHOLD = egm_sell_threshold
    logger.info(f"✅ Umbrales actualizados: buy={egm_buy_threshold}, sell={egm_sell_threshold}")
    return {"message": "Umbrales actualizados"}


@app.get("/formula/templates")
async def get_formula_templates() -> Dict[str, Any]:
    score_threshold = float(os.getenv("NERTZ_SCORE_THRESHOLD", "0.5") or 0.5)
    combined_threshold = float(os.getenv("NERTZ_COMBINED_THRESHOLD", "0.75") or 0.75)
    strong_score_threshold = float(os.getenv("NERTZ_STRONG_SIGNAL_SCORE", "0.75") or 0.75)
    def _as_finite_float(value: Any) -> float:
        try:
            v = float(value)
        except Exception:
            return 0.0
        if not math.isfinite(v):
            return 0.0
        return v

    weights = {
        "egm": 0.3,
        "combined": 0.3,
        "ild": 0.2,
        "rol": 0.1,
        "pio": 0.05,
        "ogm": 0.05,
    }
    for k in list(weights.keys()):
        env_key = f"NERTZ_WEIGHT_{k.upper()}"
        if env_key in os.environ:
            weights[k] = _as_finite_float(os.getenv(env_key))

    metrics = [
        "egm",
        "combined",
        "ild",
        "rol",
        "pio",
        "ogm",
        "volatility",
        "rsi",
        "macd",
        "macd_signal",
        "macd_diff",
        "bb_high",
        "bb_mid",
        "bb_low",
    ]
    return {
        "weights": weights,
        "thresholds": {
            "score_threshold": score_threshold,
            "combined_threshold": combined_threshold,
            "egm_buy_threshold": float(config.EGM_BUY_THRESHOLD),
            "egm_sell_threshold": float(config.EGM_SELL_THRESHOLD),
            "strong_score_threshold": strong_score_threshold,
            "require_strong": str(os.getenv("NERTZ_REQUIRE_STRONG", "") or "").strip().lower() in {"1", "true", "yes", "y"},
        },
        "metrics": metrics,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/formula/eval")
async def eval_formula(request: Request) -> Dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    metrics_in = payload.get("metrics") if isinstance(payload, dict) else None
    weights_in = payload.get("weights") if isinstance(payload, dict) else None
    thresholds_in = payload.get("thresholds") if isinstance(payload, dict) else None

    metrics = metrics_in if isinstance(metrics_in, dict) else {}
    weights = weights_in if isinstance(weights_in, dict) else {}
    thresholds = thresholds_in if isinstance(thresholds_in, dict) else {}

    def _as_finite_float(value: Any) -> float:
        try:
            v = float(value)
        except Exception:
            return 0.0
        if not math.isfinite(v):
            return 0.0
        return v

    template = (await get_formula_templates())
    default_weights = dict(template.get("weights") or {})

    used_weights: Dict[str, float] = {}
    for k, default_v in default_weights.items():
        used_weights[str(k)] = _as_finite_float(weights.get(k, default_v))
    for k, v in (weights or {}).items():
        ks = str(k)
        if ks in used_weights:
            continue
        used_weights[ks] = _as_finite_float(v)

    score_threshold = _as_finite_float(thresholds.get("score_threshold", os.getenv("NERTZ_SCORE_THRESHOLD", "0.5")))
    combined_threshold = _as_finite_float(thresholds.get("combined_threshold", os.getenv("NERTZ_COMBINED_THRESHOLD", "0.75")))
    strong_score_threshold = _as_finite_float(thresholds.get("strong_score_threshold", os.getenv("NERTZ_STRONG_SIGNAL_SCORE", "0.75")))
    egm_buy_threshold = _as_finite_float(thresholds.get("egm_buy_threshold", config.EGM_BUY_THRESHOLD))
    egm_sell_threshold = _as_finite_float(thresholds.get("egm_sell_threshold", config.EGM_SELL_THRESHOLD))

    egm = _as_finite_float(metrics.get("egm"))
    combined = _as_finite_float(metrics.get("combined"))
    ild = _as_finite_float(metrics.get("ild"))
    rol = _as_finite_float(metrics.get("rol"))
    pio = _as_finite_float(metrics.get("pio"))
    ogm = _as_finite_float(metrics.get("ogm"))

    buy_score = 0.0
    for k, w in used_weights.items():
        buy_score += _as_finite_float(metrics.get(k)) * float(w)
    sell_score = -buy_score

    if buy_score >= score_threshold or combined >= combined_threshold or (egm >= egm_buy_threshold and combined >= combined_threshold):
        decision = "buy"
    elif sell_score >= score_threshold or combined <= -combined_threshold or (egm <= egm_sell_threshold and combined <= -combined_threshold):
        decision = "sell"
    else:
        decision = "hold"

    strong = float(max(buy_score, sell_score)) >= float(strong_score_threshold)

    return {
        "decision": decision,
        "strong": bool(strong),
        "scores": {
            "buy_score": float(buy_score),
            "sell_score": float(sell_score),
        },
        "weights": used_weights,
        "thresholds": {
            "score_threshold": float(score_threshold),
            "combined_threshold": float(combined_threshold),
            "egm_buy_threshold": float(egm_buy_threshold),
            "egm_sell_threshold": float(egm_sell_threshold),
            "strong_score_threshold": float(strong_score_threshold),
        },
        "inputs": {
            "egm": float(egm),
            "combined": float(combined),
            "ild": float(ild),
            "rol": float(rol),
            "pio": float(pio),
            "ogm": float(ogm),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/orderbook/{symbol}")
async def get_orderbook(symbol: str, db: Session = Depends(get_db)) -> Dict[str, Union[str, List[List[str]]]]:
    orderbook = bot.orderbook_data.get(symbol, {"bids": [], "asks": []})
    return {
        "symbol": symbol,
        "bids": orderbook["bids"],
        "asks": orderbook["asks"],
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/candles/{symbol}/{limit}")
async def get_candles(symbol: str, limit: int = 5, db: Session = Depends(get_db)) -> Dict[
    str, Union[str, List[Dict[str, Union[str, float]]]]]:
    candles = db.query(MarketData).filter(MarketData.symbol == symbol).order_by(MarketData.timestamp.desc()).limit(
        limit).all()
    return {
        "symbol": symbol,
        "candles": [{"timestamp": c.timestamp.isoformat(), "open": float(c.open), "high": float(c.high),
                     "low": float(c.low), "close": float(c.close), "volume": float(c.volume)} for c in candles],
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/trades/{symbol}")
async def get_trades(symbol: str, db: Session = Depends(get_db)) -> Response:
    def _make_json_safe(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {str(k): _make_json_safe(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [_make_json_safe(v) for v in obj]
        if isinstance(obj, float):
            if not math.isfinite(obj):
                return None
            return obj
        if obj is None or isinstance(obj, (str, int, bool)):
            return obj
        try:
            as_float = float(obj)
            if not math.isfinite(as_float):
                return None
            return as_float
        except Exception:
            pass
        try:
            json.dumps(obj)
            return obj
        except Exception:
            return str(obj)

    try:
        raw_trades = bot.positions.get(symbol, [])
        encoded = jsonable_encoder(raw_trades)
        trades = _make_json_safe(encoded)
    except Exception:
        trades = []

    payload = {
        "symbol": symbol,
        "trades": trades,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    except Exception:
        body = json.dumps(_make_json_safe(payload), ensure_ascii=False, allow_nan=False, default=str)
    return Response(content=body, media_type="application/json")


@app.get("/export/db/trades")
async def export_db_trades(
    symbol: Optional[str] = None,
    limit: int = 500,
    include_zero_pnl: bool = True,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    q = db.query(Trade)
    if symbol:
        q = q.filter(Trade.symbol == symbol)
    q = q.order_by(Trade.timestamp.desc()).limit(max(1, int(limit)))
    trades: List[Trade] = list(q.all())

    order_ids: List[str] = []
    for t in trades:
        oid = getattr(t, "order_id", None)
        if oid:
            order_ids.append(str(oid))

    pos_by_order: Dict[str, Position] = {}
    if order_ids:
        for p in (
            db.query(Position)
            .filter(Position.order_id.in_(order_ids))
            .order_by(Position.id.desc())
            .all()
        ):
            key = str(getattr(p, "order_id", "") or "")
            if key and key not in pos_by_order:
                pos_by_order[key] = p

    rows: List[Dict[str, Any]] = []
    for t in trades:
        oid = str(getattr(t, "order_id", "") or "")
        p = pos_by_order.get(oid) if oid else None
        pnl = None
        if p is not None:
            pnl = getattr(p, "pnl_net_usdt", None)
            if pnl is None:
                pnl = getattr(p, "profit_loss", None)
        if pnl is None:
            pnl = getattr(t, "profit_loss", None)

        try:
            pnl_f = float(pnl) if pnl is not None else 0.0
        except Exception:
            pnl_f = 0.0
        if not include_zero_pnl and pnl_f == 0.0:
            continue

        rows.append({
            "trade_id": int(getattr(t, "trade_id")),
            "timestamp": getattr(t, "timestamp").isoformat() if getattr(t, "timestamp", None) else None,
            "symbol": str(getattr(t, "symbol", "")),
            "action": str(getattr(t, "action", "")),
            "decision": str(getattr(t, "decision", "")),
            "entry_price": float(getattr(t, "entry_price", 0.0) or 0.0),
            "exit_price": float(getattr(t, "exit_price", 0.0) or 0.0) if getattr(t, "exit_price", None) is not None else None,
            "quantity": float(getattr(t, "quantity", 0.0) or 0.0),
            "profit_loss": pnl_f,
            "metrics": {
                "combined": float(getattr(t, "combined", 0.0) or 0.0),
                "ild": float(getattr(t, "ild", 0.0) or 0.0),
                "egm": float(getattr(t, "egm", 0.0) or 0.0),
                "rol": float(getattr(t, "rol", 0.0) or 0.0),
                "pio": float(getattr(t, "pio", 0.0) or 0.0),
                "ogm": float(getattr(t, "ogm", 0.0) or 0.0),
            },
            "order_id": oid or None,
            "order_link_id": str(getattr(t, "order_link_id", "") or "") or None,
            "position": None if p is None else {
                "status": str(getattr(p, "status", "")),
                "entry_price": float(getattr(p, "entry_price", 0.0) or 0.0),
                "exit_price": float(getattr(p, "exit_price", 0.0) or 0.0) if getattr(p, "exit_price", None) is not None else None,
                "quantity": float(getattr(p, "quantity", 0.0) or 0.0),
                "profit_loss": float(getattr(p, "profit_loss", 0.0) or 0.0) if getattr(p, "profit_loss", None) is not None else None,
                "pnl_net_usdt": float(getattr(p, "pnl_net_usdt", 0.0) or 0.0) if getattr(p, "pnl_net_usdt", None) is not None else None,
                "fees_usdt": float(getattr(p, "fees_usdt", 0.0) or 0.0) if getattr(p, "fees_usdt", None) is not None else None,
            },
        })

    return {
        "symbol": symbol,
        "count": int(len(rows)),
        "rows": rows,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/admin/backfill/positions_pnl")
async def backfill_positions_pnl(
    symbol: Optional[str] = None,
    limit: int = 250,
    dry_run: bool = True,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    def _needs_backfill(p: Position) -> bool:
        for field in ("entry_value_usdt", "exit_value_usdt", "fees_usdt", "pnl_gross_usdt", "pnl_net_usdt"):
            if getattr(p, field, None) is None:
                return True
        return False

    def _as_float(v: Any) -> float:
        try:
            f = float(v)
        except Exception:
            return 0.0
        if not math.isfinite(f):
            return 0.0
        return f

    q = db.query(Position).filter(Position.status == "closed")
    if symbol:
        q = q.filter(Position.symbol == symbol)
    q = q.order_by(Position.timestamp.desc()).limit(max(1, int(limit)))
    positions: List[Position] = list(q.all())

    can_use_api = bool(config.BYBIT_API_KEY and config.BYBIT_API_SECRET)
    session: Optional[HTTP] = None
    if can_use_api:
        try:
            session = await bot._get_bybit_http()
        except Exception:
            session = None
            can_use_api = False

    updated: List[Dict[str, Any]] = []
    skipped: int = 0
    errors: List[Dict[str, Any]] = []

    for p in positions:
        try:
            if not _needs_backfill(p):
                skipped += 1
                continue

            sym = str(getattr(p, "symbol", "") or "")
            entry_order_id = str(getattr(p, "order_id", "") or "")
            if not sym or not entry_order_id:
                skipped += 1
                continue

            trade = (
                db.query(Trade)
                .filter(Trade.order_id == entry_order_id)
                .order_by(Trade.id.desc())
                .first()
            )
            entry_action = str(getattr(trade, "action", None) or getattr(p, "action", "buy") or "buy").lower()
            entry_link_id = (
                getattr(p, "order_link_id", None)
                or (getattr(trade, "order_link_id", None) if trade is not None else None)
            )

            exit_order_id = getattr(p, "exit_order_id", None)
            if not exit_order_id and can_use_api and session is not None:
                try:
                    discovered = await bot._discover_exit_order(sym, parent_order_id=entry_order_id, action=entry_action)
                    discovered_id = (discovered or {}).get("orderId")
                    if discovered_id:
                        exit_order_id = str(discovered_id)
                except Exception:
                    exit_order_id = None

            realized: Optional[dict] = None
            if can_use_api and session is not None and exit_order_id:
                entry_info = await bot._fetch_spot_order_history_info(session, sym, entry_order_id, entry_link_id)
                exit_info = await bot._fetch_spot_order_history_info(session, sym, str(exit_order_id), entry_link_id)
                if entry_info and exit_info:
                    realized = bot._compute_spot_realized_pnl_usdt(sym, entry_info, exit_info, entry_action)

            entry_price = _as_float(getattr(p, "entry_price", 0.0) or (getattr(trade, "entry_price", 0.0) if trade else 0.0))
            exit_price = _as_float(getattr(p, "exit_price", 0.0) or (getattr(trade, "exit_price", 0.0) if trade else 0.0))
            qty = _as_float(getattr(p, "quantity", 0.0) or (getattr(trade, "quantity", 0.0) if trade else 0.0))

            if realized:
                entry_value = _as_float(realized.get("entry_value_usdt"))
                exit_value = _as_float(realized.get("exit_value_usdt"))
                fees_usdt = _as_float(realized.get("fees_usdt"))
                pnl_gross = _as_float(realized.get("pnl_gross_usdt"))
                pnl_net = _as_float(realized.get("pnl_net_usdt"))
                rp_entry = _as_float(realized.get("entry_avg_price"))
                rp_exit = _as_float(realized.get("exit_avg_price"))
                rp_qty = _as_float(realized.get("qty_executed"))
                if rp_entry > 0:
                    entry_price = rp_entry
                if rp_exit > 0:
                    exit_price = rp_exit
                if rp_qty > 0:
                    qty = rp_qty
            else:
                if entry_price <= 0 or exit_price <= 0 or qty <= 0:
                    skipped += 1
                    continue
                entry_value = entry_price * qty
                exit_value = exit_price * qty
                pnl_gross = (exit_value - entry_value) if entry_action == "buy" else (entry_value - exit_value)
                fee_rate = float(getattr(config, "FEE_RATE", 0.0) or 0.0)
                fees_usdt = max(0.0, fee_rate) * (entry_value + exit_value)
                pnl_net = pnl_gross - fees_usdt

            if not dry_run:
                p.entry_value_usdt = float(entry_value)
                p.exit_value_usdt = float(exit_value)
                p.fees_usdt = float(fees_usdt)
                p.pnl_gross_usdt = float(pnl_gross)
                p.pnl_net_usdt = float(pnl_net)
                p.exit_order_id = str(exit_order_id) if exit_order_id else getattr(p, "exit_order_id", None)
                if entry_price > 0:
                    p.entry_price = float(entry_price)
                if exit_price > 0:
                    p.exit_price = float(exit_price)
                if qty > 0:
                    p.quantity = float(qty)
                p.profit_loss = float(pnl_net)
                if trade is not None:
                    if entry_price > 0:
                        trade.entry_price = float(entry_price)
                    if exit_price > 0:
                        trade.exit_price = float(exit_price)
                    if qty > 0:
                        trade.quantity = float(qty)
                    trade.profit_loss = float(pnl_net)

            updated.append({
                "order_id": entry_order_id,
                "exit_order_id": str(exit_order_id) if exit_order_id else None,
                "symbol": sym,
                "pnl_net_usdt": float(pnl_net),
                "fees_usdt": float(fees_usdt),
                "source": "bybit" if realized else "approx",
            })
        except Exception as e:
            errors.append({
                "order_id": str(getattr(p, "order_id", "") or ""),
                "symbol": str(getattr(p, "symbol", "") or ""),
                "error": str(e),
            })

    if not dry_run and updated:
        db.commit()

    return {
        "symbol": symbol,
        "limit": int(limit),
        "dry_run": bool(dry_run),
        "can_use_api": bool(can_use_api),
        "scanned": int(len(positions)),
        "updated": int(len(updated)),
        "skipped": int(skipped),
        "errors": int(len(errors)),
        "sample": updated[:50],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/admin/reconcile/positions_inconsistent")
async def reconcile_positions_inconsistent(
    symbol: Optional[str] = None,
    limit: int = 250,
    dry_run: bool = True,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    q = (
        db.query(Position)
        .filter(Position.status == "closed")
        .filter(or_(Position.exit_price.is_(None), Position.exit_price <= 0))
    )
    if symbol:
        q = q.filter(Position.symbol == symbol)
    q = q.order_by(Position.timestamp.desc()).limit(max(1, int(limit)))
    positions: List[Position] = list(q.all())

    can_use_api = bool(config.BYBIT_API_KEY and config.BYBIT_API_SECRET)
    session: Optional[HTTP] = None
    if can_use_api:
        try:
            session = await bot._get_bybit_http()
        except Exception:
            session = None
            can_use_api = False

    filled_statuses = {"filled", "partiallyfilled", "partiallyfilledcanceled"}
    cancelled_statuses = {"cancelled", "canceled", "rejected", "deactivated"}

    fixed: List[Dict[str, Any]] = []
    skipped: int = 0
    errors: List[Dict[str, Any]] = []

    for p in positions:
        try:
            sym = str(getattr(p, "symbol", "") or "")
            entry_order_id = str(getattr(p, "order_id", "") or "")
            if not sym or not entry_order_id:
                skipped += 1
                continue

            trade = (
                db.query(Trade)
                .filter(Trade.order_id == entry_order_id)
                .order_by(Trade.id.desc())
                .first()
            )
            entry_action = str(getattr(trade, "action", None) or getattr(p, "action", "buy") or "buy").lower()
            entry_link_id = (
                getattr(p, "order_link_id", None)
                or (getattr(trade, "order_link_id", None) if trade is not None else None)
            )

            if not can_use_api or session is None:
                skipped += 1
                continue

            entry_info = await bot._fetch_spot_order_history_info(session, sym, entry_order_id, entry_link_id)
            if not entry_info:
                skipped += 1
                continue

            entry_status = str(entry_info.get("orderStatus", "") or "").lower()
            entry_exec_qty = float(entry_info.get("cumExecQty", "0.0") or 0.0)

            if entry_status in cancelled_statuses and entry_exec_qty <= 0:  
                if not dry_run:
                    p.status = "cancelled"
                    p.exit_order_id = None
                    p.exit_price = None
                    p.profit_loss = 0.0
                    p.pnl_net_usdt = 0.0
                    if trade is not None:
                        trade.exit_price = None
                        trade.profit_loss = 0.0

                fixed.append({
                    "order_id": entry_order_id,
                    "symbol": sym,
                    "action": entry_action,
                    "result": "cancelled",
                })
                continue

            expected_exit_link_id = f"nertz-exit-{entry_order_id}"
            timestamp = await get_synced_time()
            base_params = {
                "category": "spot",
                "symbol": sym,
                "timestamp": str(timestamp),
                "recvWindow": str(int(config.RECV_WINDOW)),
            }
            try:
                exit_resp = session.get_order_history(**{**base_params, "orderLinkId": expected_exit_link_id})
            except TypeError:
                exit_resp = {"retCode": -1}

            exit_info = None
            if exit_resp.get("retCode") == 0:
                items = exit_resp.get("result", {}).get("list", []) or []
                if items:
                    exit_info = items[0]

            if exit_info:
                exit_status = str(exit_info.get("orderStatus", "") or "").lower()
                if exit_status in filled_statuses:
                    realized = bot._compute_spot_realized_pnl_usdt(sym, entry_info, exit_info, entry_action)
                    if not dry_run:
                        p.status = "closed"
                        p.exit_order_id = str(exit_info.get("orderId") or "") or None
                        p.exit_price = float(realized.get("exit_avg_price") or 0.0) or None
                        p.entry_price = float(realized.get("entry_avg_price") or getattr(p, "entry_price", 0.0) or 0.0)
                        p.quantity = float(realized.get("qty_executed") or getattr(p, "quantity", 0.0) or 0.0)
                        p.entry_value_usdt = float(realized.get("entry_value_usdt") or 0.0)
                        p.exit_value_usdt = float(realized.get("exit_value_usdt") or 0.0)
                        p.fees_usdt = float(realized.get("fees_usdt") or 0.0)
                        p.pnl_gross_usdt = float(realized.get("pnl_gross_usdt") or 0.0)
                        p.pnl_net_usdt = float(realized.get("pnl_net_usdt") or 0.0)
                        p.profit_loss = float(realized.get("pnl_net_usdt") or 0.0)
                        if trade is not None:
                            trade.entry_price = float(p.entry_price or trade.entry_price or 0.0)
                            trade.exit_price = float(p.exit_price or 0.0) if p.exit_price is not None else None
                            trade.quantity = float(p.quantity or trade.quantity or 0.0)
                            trade.profit_loss = float(p.profit_loss or 0.0)

                    fixed.append({
                        "order_id": entry_order_id,
                        "exit_order_id": str(exit_info.get("orderId") or "") or None,
                        "symbol": sym,
                        "action": entry_action,
                        "result": "closed",
                    })
                    continue

            if not dry_run:
                p.status = "open"
                p.exit_order_id = None
                p.exit_price = None
                p.profit_loss = 0.0
                p.pnl_net_usdt = 0.0
                if trade is not None:
                    trade.exit_price = None
                    trade.profit_loss = 0.0

            fixed.append({
                "order_id": entry_order_id,
                "symbol": sym,
                "action": entry_action,
                "result": "open",
            })
        except Exception as e:
            errors.append({
                "order_id": str(getattr(p, "order_id", "") or ""),
                "symbol": str(getattr(p, "symbol", "") or ""),
                "error": str(e),
            })

    if not dry_run and fixed:
        db.commit()

    return {
        "symbol": symbol,
        "limit": int(limit),
        "dry_run": bool(dry_run),
        "can_use_api": bool(can_use_api),
        "scanned": int(len(positions)),
        "fixed": int(len(fixed)),
        "skipped": int(skipped),
        "errors": int(len(errors)),
        "sample": fixed[:50],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

@app.get("/discovery/metrics/{symbol}")
async def get_discovery_metrics(symbol: str) -> Dict[str, Any]:
    metrics = bot.metrics.get(symbol, {})
    return {
        "symbol": symbol,
        "metrics": metrics,
        "timestamp": datetime.now(timezone.utc).isoformat(),

    }


@app.get("/exchange/open_orders/{symbol}")
async def exchange_open_orders(symbol: str) -> Dict[str, Any]:
    timestamp = await get_synced_time()
    resp = await bot._bybit_call(
        "get_open_orders",
        category="spot",
        symbol=symbol,
        timestamp=str(timestamp),
        recvWindow=str(int(config.RECV_WINDOW)),
        limit=50,
    )
    return {"symbol": symbol, "response": resp, "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/exchange/order_history/{symbol}")
async def exchange_order_history(
    symbol: str,
    limit: int = 50,
    order_id: Optional[str] = None,
    order_link_id: Optional[str] = None,
) -> Dict[str, Any]:
    timestamp = await get_synced_time()
    params: Dict[str, Any] = {
        "category": "spot",
        "symbol": symbol,
        "timestamp": str(timestamp),
        "recvWindow": str(int(config.RECV_WINDOW)),
    }
    if order_id:
        params["orderId"] = order_id
    elif order_link_id:
        params["orderLinkId"] = order_link_id
    resp = await bot._bybit_call("get_order_history", **params)
    if resp.get("retCode") == 0:
        orders = resp.get("result", {}).get("list", []) or []
        resp = {**resp, "result": {**(resp.get("result") or {}), "list": orders[: max(1, int(limit))]}}
    return {"symbol": symbol, "response": resp, "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/exchange/wallet_balances")
async def exchange_wallet_balances() -> Dict[str, Any]:
    coins_to_show = {"USDT"}
    for sym in bot.symbols:
        base, _quote = bot._split_symbol_base_quote(sym)
        if base:
            coins_to_show.add(base.upper())

    timestamp = await get_synced_time()
    resp = await bot._bybit_call(
        "get_wallet_balance",
        accountType="UNIFIED",
        timestamp=str(timestamp),
        recvWindow=str(int(config.RECV_WINDOW)),
    )
    out: Dict[str, Any] = {"retCode": resp.get("retCode"), "retMsg": resp.get("retMsg"), "coins": {}}
    if resp.get("retCode") == 0:
        wallet_list = (resp.get("result", {}).get("list", []) or [])
        coins = (wallet_list[0].get("coin", []) or []) if wallet_list else []
        for c in coins:
            coin = str(c.get("coin", "")).upper()
            if coin in coins_to_show:
                wallet_balance = float(c.get("walletBalance", 0.0) or 0.0)
                locked = float(c.get("locked", 0.0) or 0.0)
                available_to_withdraw = c.get("availableToWithdraw")
                try:
                    available_to_withdraw_f = float(available_to_withdraw) if available_to_withdraw is not None else None
                except (TypeError, ValueError):
                    available_to_withdraw_f = None
                available = available_to_withdraw_f if available_to_withdraw_f is not None else (wallet_balance - locked)
                if available < 0:
                    available = 0.0
                out["coins"][coin] = {
                    "walletBalance": wallet_balance,
                    "locked": locked,
                    "availableToWithdraw": available_to_withdraw_f,
                    "available": available,
                }
    return {"response": out, "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/exchange/executions/{symbol}")
async def exchange_executions(
    symbol: str,
    limit: int = 200,
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
    today_utc: bool = True,
) -> Dict[str, Any]:
    now_ms = await get_synced_time()
    now = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
    if today_utc:
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_ms = int(start_dt.timestamp() * 1000)
        end_ms = int(now_ms)
    else:
        if start_ms is None:
            start_ms = int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
        if end_ms is None:
            end_ms = int(now_ms)

    overall_limit = max(1, int(limit))
    page_limit = min(50, overall_limit)
    cursor: Optional[str] = None
    all_execs: List[Dict[str, Any]] = []
    last_resp: Dict[str, Any] = {}

    for _ in range(50):
        params: Dict[str, Any] = {
            "category": "spot",
            "symbol": symbol,
            "timestamp": str(await get_synced_time()),
            "recvWindow": str(int(config.RECV_WINDOW)),
            "limit": page_limit,
            "startTime": int(start_ms),
            "endTime": int(end_ms),
        }
        if cursor:
            params["cursor"] = cursor

        resp = await bot._bybit_call("get_executions", **params)

        last_resp = resp
        if resp.get("retCode") != 0:
            break

        execs = (resp.get("result", {}) or {}).get("list", []) or []
        all_execs.extend(execs)

        if len(all_execs) >= overall_limit:
            all_execs = all_execs[:overall_limit]
            break

        cursor = (resp.get("result", {}) or {}).get("nextPageCursor")
        if not cursor or not execs:
            break

    seen: set[str] = set()
    deduped: List[Dict[str, Any]] = []
    for e in all_execs:
        key = e.get("execId") or e.get("execID") or e.get("id")
        if key is None:
            deduped.append(e)
            continue
        key_str = str(key)
        if key_str in seen:
            continue
        seen.add(key_str)
        deduped.append(e)

    return {
        "symbol": symbol,
        "use_testnet": config.USE_TESTNET,
        "window": {"start_ms": start_ms, "end_ms": end_ms},
        "response": {
            "retCode": last_resp.get("retCode"),
            "retMsg": last_resp.get("retMsg"),
            "count": len(deduped),
            "list": deduped,
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/analysis/bybit_today")
async def bybit_today_report(
    symbols: Optional[str] = None,
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
    day_utc: Optional[str] = None,
    include_raw: bool = False,
    raw_limit: int = 200,
) -> Dict[str, Any]:
    now_ms = await get_synced_time()
    now = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)

    if day_utc:
        try:
            d = datetime.strptime(day_utc.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            start_ms = int(d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)
            end_ms = int(d.replace(hour=23, minute=59, second=59, microsecond=999000).timestamp() * 1000)
        except Exception:
            start_ms = None
            end_ms = None

    if start_ms is None or end_ms is None:
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        start_ms = int(start_dt.timestamp() * 1000)
        end_ms = int(now_ms)
    else:
        start_ms = int(start_ms)
        end_ms = int(end_ms)
        start_dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
        now = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)

    if symbols:
        symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        symbol_list = [s.strip().upper() for s in config.SYMBOL.split(",") if s.strip()]

    session: HTTP = await bot._get_bybit_http()

    def _as_float(x: Any) -> float:
        try:
            return float(x)
        except Exception:
            return 0.0

    def _ms_to_iso(ms: Any) -> Optional[str]:
        try:
            return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()
        except Exception:
            return None

    async def _fetch_all(getter, symbol: str, *, limit: int = 500) -> Dict[str, Any]:
        overall_limit = max(1, int(limit))
        page_limit = min(50, overall_limit)
        cursor: Optional[str] = None
        out: List[Dict[str, Any]] = []
        last_resp: Dict[str, Any] = {}

        for _ in range(50):
            params: Dict[str, Any] = {
                "category": "spot",
                "symbol": symbol,
                "timestamp": str(await get_synced_time()),
                "recvWindow": str(int(config.RECV_WINDOW)),
                "limit": page_limit,
                "startTime": start_ms,
                "endTime": end_ms,
            }
            if cursor:
                params["cursor"] = cursor

            try:
                resp = getter(**params)
            except TypeError:
                params.pop("limit", None)
                resp = getter(**params)

            last_resp = resp
            if resp.get("retCode") != 0:
                break

            lst = (resp.get("result", {}) or {}).get("list", []) or []
            out.extend(lst)

            if len(out) >= overall_limit:
                out = out[:overall_limit]
                break

            cursor = (resp.get("result", {}) or {}).get("nextPageCursor")
            if not cursor or not lst:
                break

        return {"retCode": last_resp.get("retCode"), "retMsg": last_resp.get("retMsg"), "list": out}

    def _analyze_symbol(symbol: str, orders: List[Dict[str, Any]], execs: List[Dict[str, Any]]) -> Dict[str, Any]:
        orders_by_status: Dict[str, int] = defaultdict(int)
        orders_by_side: Dict[str, int] = defaultdict(int)
        for o in orders:
            orders_by_status[str(o.get("orderStatus", ""))] += 1
            orders_by_side[str(o.get("side", ""))] += 1

        execs_filtered: List[Dict[str, Any]] = []
        for e in execs:
            exec_type = str(e.get("execType") or "").strip().upper()
            if exec_type and exec_type != "TRADE":
                continue
            execs_filtered.append(e)

        execs_sorted = sorted(execs_filtered, key=lambda e: int(e.get("execTime") or 0))

        base, quote = bot._split_symbol_base_quote(symbol)
        base = base.upper() if base else ""
        quote = quote.upper() if quote else ""
        base_ccy = base or None
        quote_ccy = quote or None

        lots = deque()
        fees: Dict[str, float] = defaultdict(float)
        buy_value = 0.0
        sell_value = 0.0
        realized = 0.0
        exec_buy_qty = 0.0
        exec_sell_qty = 0.0
        unmatched_sell_qty = 0.0
        unmatched_sell_notional = 0.0

        for e in execs_sorted:
            side = str(e.get("side", "")).upper()
            px = _as_float(e.get("execPrice"))
            qty = _as_float(e.get("execQty"))
            fee = _as_float(e.get("execFee"))
            fee_ccy = str(e.get("feeCurrency") or quote_ccy or "").upper()
            if fee_ccy:
                fees[fee_ccy] += fee

            if side == "BUY":
                exec_buy_qty += qty
                cost = px * qty
                if quote_ccy and fee_ccy == quote_ccy:
                    cost += fee
                qty_in = qty
                if base_ccy and fee_ccy == base_ccy:
                    qty_in = max(0.0, qty - fee)
                buy_value += px * qty
                lots.append([qty_in, cost])
            elif side == "SELL":
                exec_sell_qty += qty
                sell_value += px * qty

                qty_to_match = qty
                if base_ccy and fee_ccy == base_ccy:
                    qty_to_match = max(0.0, qty - fee)

                proceeds_per_unit = px
                if quote_ccy and fee_ccy == quote_ccy and qty > 0:
                    proceeds_per_unit = (px * qty - fee) / qty

                while qty_to_match > 1e-12 and lots:
                    lot_qty, lot_cost = lots[0]
                    take = min(lot_qty, qty_to_match)
                    avg_cost = lot_cost / lot_qty if lot_qty > 0 else 0.0
                    cost_part = avg_cost * take
                    realized += (proceeds_per_unit * take) - cost_part
                    lot_qty -= take
                    lot_cost -= cost_part
                    qty_to_match -= take
                    if lot_qty <= 1e-12:
                        lots.popleft()
                    else:
                        lots[0] = [lot_qty, lot_cost]

                if qty_to_match > 1e-9:
                    unmatched_sell_qty += qty_to_match
                    unmatched_sell_notional += proceeds_per_unit * qty_to_match

        open_qty = sum(q for q, _c in lots)
        open_cost = sum(_c for _q, _c in lots)
        open_avg = (open_cost / open_qty) if open_qty > 0 else 0.0

        first_exec = execs_sorted[0] if execs_sorted else None
        last_exec = execs_sorted[-1] if execs_sorted else None

        return {
            "orders_total": len(orders),
            "orders_by_status": dict(orders_by_status),
            "orders_by_side": dict(orders_by_side),
            "executions_total": len(execs_sorted),
            "executions_window": {
                "first": _ms_to_iso(first_exec.get("execTime")) if first_exec else None,
                "last": _ms_to_iso(last_exec.get("execTime")) if last_exec else None,
            },
            "executed_qty": {"buy": exec_buy_qty, "sell": exec_sell_qty},
            "notional_quote": {"buy": buy_value, "sell": sell_value},
            "fees": dict(fees),
            "realized_pnl_est_quote": realized,
            "unmatched_sell": {"qty_base": unmatched_sell_qty, "notional_quote": unmatched_sell_notional},
            "open_position": {"qty_base": open_qty, "avg_cost_quote": open_avg},
        }

    by_symbol: Dict[str, Any] = {}
    totals_realized = 0.0
    totals_unmatched_sell_notional = 0.0
    totals_fees: Dict[str, float] = defaultdict(float)
    for sym in symbol_list:
        orders_resp = await _fetch_all(session.get_order_history, sym, limit=500)
        execs_resp = await _fetch_all(session.get_executions, sym, limit=500)
        orders = orders_resp.get("list") or []
        execs = execs_resp.get("list") or []

        seen_exec: set[str] = set()
        deduped_execs: List[Dict[str, Any]] = []
        for e in execs:
            key = e.get("execId") or e.get("execID") or e.get("id")
            if key is None:
                deduped_execs.append(e)
                continue
            key_str = str(key)
            if key_str in seen_exec:
                continue
            seen_exec.add(key_str)
            deduped_execs.append(e)

        seen_orders: set[str] = set()
        deduped_orders: List[Dict[str, Any]] = []
        for o in orders:
            key = o.get("orderId") or o.get("orderID") or o.get("id")
            if key is None:
                deduped_orders.append(o)
                continue
            key_str = str(key)
            if key_str in seen_orders:
                continue
            seen_orders.add(key_str)
            deduped_orders.append(o)

        analysis = _analyze_symbol(sym, deduped_orders, deduped_execs)
        totals_realized += float(analysis.get("realized_pnl_est_quote") or 0.0)
        totals_unmatched_sell_notional += float((analysis.get("unmatched_sell") or {}).get("notional_quote") or 0.0)
        for k, v in (analysis.get("fees") or {}).items():
            try:
                totals_fees[str(k).upper()] += float(v)
            except Exception:
                pass

        symbol_payload: Dict[str, Any] = {
            "order_history_ret": {"retCode": orders_resp.get("retCode"), "retMsg": orders_resp.get("retMsg")},
            "executions_ret": {"retCode": execs_resp.get("retCode"), "retMsg": execs_resp.get("retMsg")},
            "analysis": analysis,
        }

        if include_raw:
            lim = max(1, int(raw_limit))
            symbol_payload["orders"] = deduped_orders[:lim]
            symbol_payload["executions"] = deduped_execs[:lim]

        by_symbol[sym] = symbol_payload

    return {
        "use_testnet": config.USE_TESTNET,
        "utc_window": {"start": start_dt.isoformat(), "end": now.isoformat(), "start_ms": start_ms, "end_ms": end_ms},
        "symbols": symbol_list,
        "totals": {
            "realized_pnl_est_quote": totals_realized,
            "unmatched_sell_notional_quote": totals_unmatched_sell_notional,
            "fees": dict(totals_fees),
        },
        "by_symbol": by_symbol,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/analysis/bybit_extract")
async def bybit_extract(
    symbols: Optional[str] = None,
    hours: float = 24.0,
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
    limit: int = 2000,
    include_raw: bool = False,
    raw_limit: int = 200,
) -> Dict[str, Any]:
    if not config.BYBIT_API_KEY or not config.BYBIT_API_SECRET:
        return {
            "ok": False,
            "error": "missing_bybit_credentials",
            "use_testnet": config.USE_TESTNET,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    now_ms = await get_synced_time()
    if end_ms is None:
        end_ms = int(now_ms)
    else:
        end_ms = int(end_ms)

    if start_ms is None:
        h = float(hours)
        if h <= 0:
            h = 24.0
        start_ms = int((datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc) - timedelta(hours=h)).timestamp() * 1000)
    else:
        start_ms = int(start_ms)

    if symbols:
        symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        symbol_list = [s.strip().upper() for s in config.SYMBOL.split(",") if s.strip()]

    lim = max(1, min(20000, int(limit)))
    raw_lim = max(1, min(5000, int(raw_limit)))

    session: HTTP = await bot._get_bybit_http()

    async def _fetch_all(getter, symbol: str, *, limit: int) -> Dict[str, Any]:
        overall_limit = max(1, int(limit))
        page_limit = min(50, overall_limit)
        max_pages = min(200, max(1, int(math.ceil(overall_limit / page_limit)) + 5))
        cursor: Optional[str] = None
        out: List[Dict[str, Any]] = []
        last_resp: Dict[str, Any] = {}

        for _ in range(max_pages):
            params: Dict[str, Any] = {
                "category": "spot",
                "symbol": symbol,
                "timestamp": str(await get_synced_time()),
                "recvWindow": str(int(config.RECV_WINDOW)),
                "limit": page_limit,
                "startTime": start_ms,
                "endTime": end_ms,
            }
            if cursor:
                params["cursor"] = cursor

            try:
                resp = getter(**params)
            except TypeError:
                params.pop("limit", None)
                resp = getter(**params)

            last_resp = resp
            if resp.get("retCode") != 0:
                break

            lst = (resp.get("result", {}) or {}).get("list", []) or []
            out.extend(lst)

            if len(out) >= overall_limit:
                out = out[:overall_limit]
                break

            cursor = (resp.get("result", {}) or {}).get("nextPageCursor")
            if not cursor or not lst:
                break

        return {"retCode": last_resp.get("retCode"), "retMsg": last_resp.get("retMsg"), "list": out}

    by_symbol: Dict[str, Any] = {}
    for sym in symbol_list:
        orders_resp = await _fetch_all(session.get_order_history, sym, limit=lim)
        execs_resp = await _fetch_all(session.get_executions, sym, limit=lim)

        orders = orders_resp.get("list") or []
        execs = execs_resp.get("list") or []

        seen_order_ids: set[str] = set()
        for o in orders:
            oid = o.get("orderId") or o.get("orderID") or o.get("id")
            if oid is None:
                continue
            seen_order_ids.add(str(oid))

        nertz_orders = 0
        external_orders = 0
        for o in orders:
            link = o.get("orderLinkId")
            if link and str(link).startswith("nertz-"):
                nertz_orders += 1
            else:
                external_orders += 1

        payload: Dict[str, Any] = {
            "window": {
                "start_ms": start_ms,
                "end_ms": end_ms,
                "start": datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).isoformat(),
                "end": datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).isoformat(),
            },
            "orders": {
                "retCode": orders_resp.get("retCode"),
                "retMsg": orders_resp.get("retMsg"),
                "count": len(orders),
                "nertz": nertz_orders,
                "external": external_orders,
            },
            "executions": {
                "retCode": execs_resp.get("retCode"),
                "retMsg": execs_resp.get("retMsg"),
                "count": len(execs),
            },
        }

        if include_raw:
            payload["raw"] = {
                "orders": orders[:raw_lim],
                "executions": execs[:raw_lim],
            }

        by_symbol[sym] = payload

    return {
        "ok": True,
        "use_testnet": config.USE_TESTNET,
        "symbols": symbol_list,
        "by_symbol": by_symbol,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/analysis/bybit_compare_db")
async def bybit_compare_db(
    symbols: Optional[str] = None,
    hours: float = 24.0,
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
    limit: int = 2000,
    only_nertz: bool = True,
    sample: int = 200,
) -> Dict[str, Any]:
    if not config.BYBIT_API_KEY or not config.BYBIT_API_SECRET:
        return {
            "ok": False,
            "error": "missing_bybit_credentials",
            "use_testnet": config.USE_TESTNET,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    now_ms = await get_synced_time()
    if end_ms is None:
        end_ms = int(now_ms)
    else:
        end_ms = int(end_ms)

    if start_ms is None:
        h = float(hours)
        if h <= 0:
            h = 24.0
        start_ms = int((datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc) - timedelta(hours=h)).timestamp() * 1000)
    else:
        start_ms = int(start_ms)

    if symbols:
        symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        symbol_list = [s.strip().upper() for s in config.SYMBOL.split(",") if s.strip()]

    lim = max(1, min(20000, int(limit)))
    sample_lim = max(1, min(2000, int(sample)))

    start_dt = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    end_dt = datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc)

    session: HTTP = await bot._get_bybit_http()

    async def _fetch_all(getter, symbol: str, *, limit: int) -> Dict[str, Any]:
        overall_limit = max(1, int(limit))
        page_limit = min(50, overall_limit)
        max_pages = min(200, max(1, int(math.ceil(overall_limit / page_limit)) + 5))
        cursor: Optional[str] = None
        out: List[Dict[str, Any]] = []
        last_resp: Dict[str, Any] = {}

        for _ in range(max_pages):
            params: Dict[str, Any] = {
                "category": "spot",
                "symbol": symbol,
                "timestamp": str(await get_synced_time()),
                "recvWindow": str(int(config.RECV_WINDOW)),
                "limit": page_limit,
                "startTime": start_ms,
                "endTime": end_ms,
            }
            if cursor:
                params["cursor"] = cursor

            try:
                resp = getter(**params)
            except TypeError:
                params.pop("limit", None)
                resp = getter(**params)

            last_resp = resp
            if resp.get("retCode") != 0:
                break

            lst = (resp.get("result", {}) or {}).get("list", []) or []
            out.extend(lst)

            if len(out) >= overall_limit:
                out = out[:overall_limit]
                break

            cursor = (resp.get("result", {}) or {}).get("nextPageCursor")
            if not cursor or not lst:
                break

        return {"retCode": last_resp.get("retCode"), "retMsg": last_resp.get("retMsg"), "list": out}

    by_symbol: Dict[str, Any] = {}
    totals = {
        "bybit_orders": 0,
        "bybit_executions": 0,
        "bybit_orders_nertz": 0,
        "db_trades": 0,
        "missing_in_db": 0,
        "missing_in_bybit": 0,
    }

    with SessionLocal() as db:
        db_rows: List[Trade] = (
            db.query(Trade)
            .filter(Trade.symbol.in_(symbol_list))
            .order_by(Trade.timestamp.asc())
            .all()
        )
        db_by_symbol: Dict[str, List[Trade]] = defaultdict(list)
        db_order_ids_all: set[str] = set()
        db_link_ids_all: set[str] = set()
        for t in db_rows:
            ts = getattr(t, "timestamp", None)
            if isinstance(ts, datetime):
                ts_utc = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
                if ts_utc < start_dt or ts_utc > end_dt:
                    continue
            sym = str(getattr(t, "symbol", "") or "").upper()
            db_by_symbol[sym].append(t)
            oid = getattr(t, "order_id", None)
            if oid:
                db_order_ids_all.add(str(oid))
            lid = getattr(t, "order_link_id", None)
            if lid:
                db_link_ids_all.add(str(lid))

    for sym in symbol_list:
        orders_resp = await _fetch_all(session.get_order_history, sym, limit=lim)
        execs_resp = await _fetch_all(session.get_executions, sym, limit=lim)
        orders = orders_resp.get("list") or []
        execs = execs_resp.get("list") or []

        bybit_order_ids: set[str] = set()
        bybit_link_ids: set[str] = set()
        bybit_nertz_order_ids: set[str] = set()
        bybit_nertz_entry_order_ids: set[str] = set()
        bybit_nertz_exit_order_ids: set[str] = set()
        bybit_nertz_entry_link_ids: set[str] = set()
        bybit_nertz_exit_link_ids: set[str] = set()

        bybit_exec_order_ids: set[str] = set()
        bybit_nertz_entry_exec_order_ids: set[str] = set()
        bybit_nertz_exit_exec_order_ids: set[str] = set()

        for o in orders:
            oid = o.get("orderId") or o.get("orderID") or o.get("id")
            oid_str = str(oid) if oid is not None else None
            if oid_str:
                bybit_order_ids.add(oid_str)
            lid = o.get("orderLinkId") or o.get("orderLinkID") or o.get("order_link_id")
            lid_str = str(lid) if lid is not None else None
            if lid_str:
                bybit_link_ids.add(lid_str)
            if oid_str and lid_str and lid_str.startswith("nertz-"):
                bybit_nertz_order_ids.add(oid_str)
                if lid_str.startswith("nertz-exit-"):
                    bybit_nertz_exit_order_ids.add(oid_str)
                    bybit_nertz_exit_link_ids.add(lid_str)
                else:
                    bybit_nertz_entry_order_ids.add(oid_str)
                    bybit_nertz_entry_link_ids.add(lid_str)

        for e in execs:
            oid = e.get("orderId") or e.get("orderID") or e.get("id")
            oid_str = str(oid) if oid is not None else None
            if not oid_str:
                continue
            bybit_exec_order_ids.add(oid_str)
            lid = e.get("orderLinkId") or e.get("orderLinkID") or e.get("order_link_id")
            lid_str = str(lid) if lid is not None else ""
            if not lid_str.startswith("nertz-"):
                continue
            if lid_str.startswith("nertz-exit-"):
                bybit_nertz_exit_exec_order_ids.add(oid_str)
                bybit_nertz_exit_link_ids.add(lid_str)
            else:
                bybit_nertz_entry_exec_order_ids.add(oid_str)
                bybit_nertz_entry_link_ids.add(lid_str)

        order_by_id: Dict[str, Dict[str, Any]] = {}
        for o in orders:
            oid = o.get("orderId") or o.get("orderID") or o.get("id")
            oid_str = str(oid) if oid is not None else None
            if oid_str:
                order_by_id[oid_str] = o

        db_trades_sym = db_by_symbol.get(sym, [])
        if only_nertz:
            filtered: List[Trade] = []
            for t in db_trades_sym:
                lid = getattr(t, "order_link_id", None)
                if lid is None:
                    filtered.append(t)
                    continue
                if str(lid).startswith("nertz-"):
                    filtered.append(t)
            db_trades_sym = filtered
        db_order_ids_sym = {str(getattr(t, "order_id", "") or "") for t in db_trades_sym if getattr(t, "order_id", None)}
        db_link_ids_sym = {str(getattr(t, "order_link_id", "") or "") for t in db_trades_sym if getattr(t, "order_link_id", None)}

        target_bybit_ids = bybit_nertz_entry_exec_order_ids if only_nertz else bybit_exec_order_ids
        missing_in_db = sorted([oid for oid in target_bybit_ids if oid not in db_order_ids_sym])
        missing_in_bybit = sorted([oid for oid in db_order_ids_sym if oid and (oid not in bybit_order_ids)])

        missing_in_db_details: List[Dict[str, Any]] = []
        for oid in missing_in_db[:sample_lim]:
            o = order_by_id.get(oid)
            if not isinstance(o, dict):
                continue
            missing_in_db_details.append(
                {
                    "orderId": oid,
                    "orderLinkId": o.get("orderLinkId"),
                    "orderStatus": o.get("orderStatus") or o.get("status"),
                    "side": o.get("side"),
                    "orderType": o.get("orderType"),
                    "qty": o.get("qty") or o.get("orderQty"),
                    "price": o.get("price") or o.get("orderPrice"),
                    "avgPrice": o.get("avgPrice"),
                    "cumExecQty": o.get("cumExecQty"),
                    "cumExecValue": o.get("cumExecValue"),
                    "createdTime": o.get("createdTime"),
                    "updatedTime": o.get("updatedTime"),
                }
            )

        missing_entry_link_in_db = sorted([lid for lid in bybit_nertz_entry_link_ids if lid not in db_link_ids_sym])
        missing_entry_link_in_bybit = sorted([lid for lid in db_link_ids_sym if lid and (lid not in bybit_nertz_entry_link_ids)])

        closed_order_ids = {
            str(getattr(t, "order_id", "") or "")
            for t in db_trades_sym
            if getattr(t, "order_id", None)
            and str(getattr(t, "decision", "") or "").lower() in {"closed"}
        }
        expected_exit_link_ids = {f"nertz-exit-{oid}" for oid in closed_order_ids if oid}
        missing_exit_link_in_bybit = sorted([lid for lid in expected_exit_link_ids if lid not in bybit_nertz_exit_link_ids])
        unexpected_exit_link_in_bybit = sorted([lid for lid in bybit_nertz_exit_link_ids if lid not in expected_exit_link_ids])

        by_symbol[sym] = {
            "window": {
                "start_ms": start_ms,
                "end_ms": end_ms,
                "start": datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).isoformat(),
                "end": datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc).isoformat(),
            },
            "bybit": {
                "orders": {"retCode": orders_resp.get("retCode"), "retMsg": orders_resp.get("retMsg"), "count": len(orders)},
                "executions": {"retCode": execs_resp.get("retCode"), "retMsg": execs_resp.get("retMsg"), "count": len(execs)},
                "order_ids": len(bybit_order_ids),
                "order_link_ids": len(bybit_link_ids),
                "nertz_order_ids": len(bybit_nertz_order_ids),
                "nertz_entry_order_ids": len(bybit_nertz_entry_order_ids),
                "nertz_exit_order_ids": len(bybit_nertz_exit_order_ids),
                "nertz_entry_link_ids": len(bybit_nertz_entry_link_ids),
                "nertz_exit_link_ids": len(bybit_nertz_exit_link_ids),
                "exec_order_ids": len(bybit_exec_order_ids),
                "nertz_entry_exec_order_ids": len(bybit_nertz_entry_exec_order_ids),
                "nertz_exit_exec_order_ids": len(bybit_nertz_exit_exec_order_ids),
            },
            "db": {
                "trades": len(db_trades_sym),
                "order_ids": len(db_order_ids_sym),
                "order_link_ids": len(db_link_ids_sym),
            },
            "diff": {
                "only_nertz": bool(only_nertz),
                "missing_in_db": missing_in_db[:sample_lim],
                "missing_in_db_count": len(missing_in_db),
                "missing_in_bybit": missing_in_bybit[:sample_lim],
                "missing_in_bybit_count": len(missing_in_bybit),
                "missing_in_db_details": missing_in_db_details,
                "entry_link_missing_in_db": missing_entry_link_in_db[:sample_lim],
                "entry_link_missing_in_db_count": len(missing_entry_link_in_db),
                "entry_link_missing_in_bybit": missing_entry_link_in_bybit[:sample_lim],
                "entry_link_missing_in_bybit_count": len(missing_entry_link_in_bybit),
                "expected_exit_link_missing_in_bybit": missing_exit_link_in_bybit[:sample_lim],
                "expected_exit_link_missing_in_bybit_count": len(missing_exit_link_in_bybit),
                "unexpected_exit_link_in_bybit": unexpected_exit_link_in_bybit[:sample_lim],
                "unexpected_exit_link_in_bybit_count": len(unexpected_exit_link_in_bybit),
            },
        }

        totals["bybit_orders"] += len(orders)
        totals["bybit_executions"] += len(execs)
        totals["bybit_orders_nertz"] += len(bybit_nertz_order_ids)
        totals["db_trades"] += len(db_trades_sym)
        totals["missing_in_db"] += len(missing_in_db)
        totals["missing_in_bybit"] += len(missing_in_bybit)

    return {
        "ok": True,
        "use_testnet": config.USE_TESTNET,
        "symbols": symbol_list,
        "totals": totals,
        "by_symbol": by_symbol,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/analysis/db_trades")
async def analysis_db_trades(
    symbol: Optional[str] = None,
    limit: int = 500,
    offset: int = 0,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    lim = max(1, min(5000, int(limit)))
    off = max(0, int(offset))
    sym = str(symbol).upper().strip() if symbol else None
    status_wanted = str(status).lower().strip() if status else None

    def _status_from_decision(decision: Any) -> Optional[str]:
        d = str(decision or "").lower()
        if d in {"closed", "closed_external"}:
            return "closed"
        if d == "cancelled":
            return "cancelled"
        if d == "failed":
            return "failed"
        return None

    with SessionLocal() as db:
        q = db.query(Trade)
        if sym:
            q = q.filter(Trade.symbol == sym)
        total = q.count()
        rows_all: List[Trade] = q.order_by(Trade.timestamp.asc()).all()

        order_ids = [r.order_id for r in rows_all if getattr(r, "order_id", None)]
        pos_by_order_id: Dict[str, Position] = {}
        if order_ids:
            for p in db.query(Position).filter(Position.order_id.in_(order_ids)).all():
                if p.order_id:
                    pos_by_order_id[str(p.order_id)] = p

    out_rows: List[Dict[str, Any]] = []
    for r in rows_all:
        oid = getattr(r, "order_id", None)
        oid_str = str(oid) if oid is not None else None
        pos = pos_by_order_id.get(oid_str) if oid_str else None

        derived_status = _status_from_decision(getattr(r, "decision", None))
        if derived_status is None and pos is not None:
            derived_status = str(pos.status or "open")
        if derived_status is None:
            derived_status = "open"

        if status_wanted and derived_status != status_wanted:
            continue

        ts = getattr(r, "timestamp", None)
        ts_iso = ts.isoformat() if ts is not None else None

        out_rows.append(
            {
                "trade_id": getattr(r, "trade_id", None),
                "timestamp": ts_iso,
                "symbol": getattr(r, "symbol", None),
                "action": getattr(r, "action", None),
                "entry_price": getattr(r, "entry_price", None),
                "exit_price": getattr(r, "exit_price", None),
                "quantity": getattr(r, "quantity", None),
                "profit_loss": getattr(r, "profit_loss", None),
                "decision": getattr(r, "decision", None),
                "order_id": oid_str,
                "order_link_id": getattr(r, "order_link_id", None),
                "status": derived_status,
                "tp": (getattr(pos, "tp", None) if pos is not None else None),
                "sl": (getattr(pos, "sl", None) if pos is not None else None),
            }
        )

    total_filtered = len(out_rows)
    paged = out_rows[off : off + lim]

    return {
        "ok": True,
        "use_testnet": config.USE_TESTNET,
        "query": {"symbol": sym, "limit": lim, "offset": off, "status": status_wanted},
        "total_db_rows": total,
        "total_filtered_rows": total_filtered,
        "returned": len(paged),
        "rows": paged,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/analysis/rebuild_session_results")
async def rebuild_session_results() -> Dict[str, Any]:
    prev_force = os.environ.get("NERTZ_SINGLE_SESSION_LOG")
    os.environ["NERTZ_SINGLE_SESSION_LOG"] = "1"
    backfilled = 0
    try:
        if config.BYBIT_API_KEY and config.BYBIT_API_SECRET:
            with SessionLocal() as db:
                rows = (
                    db.query(Position)
                    .filter((Position.exit_order_id.is_(None)) | (Position.exit_order_id == ""))
                    .filter(Position.status.in_(["closing", "closed"]))
                    .all()
                )

                by_symbol: Dict[str, List[Position]] = defaultdict(list)
                for p in rows:
                    sym = str(getattr(p, "symbol", "") or "").upper()
                    if not sym:
                        continue
                    by_symbol[sym].append(p)

            session: HTTP = await bot._get_bybit_http()

            async def _fetch_orders(sym: str, start_ms: int, end_ms: int, max_rows: int) -> List[Dict[str, Any]]:
                overall_limit = max(1, int(max_rows))
                page_limit = min(50, overall_limit)
                max_pages = min(200, max(1, int(math.ceil(overall_limit / page_limit)) + 5))
                cursor: Optional[str] = None
                out: List[Dict[str, Any]] = []

                for _ in range(max_pages):
                    params: Dict[str, Any] = {
                        "category": "spot",
                        "symbol": sym,
                        "timestamp": str(await get_synced_time()),
                        "recvWindow": str(int(config.RECV_WINDOW)),
                        "limit": page_limit,
                        "startTime": int(start_ms),
                        "endTime": int(end_ms),
                    }
                    if cursor:
                        params["cursor"] = cursor
                    try:
                        resp = session.get_order_history(**params)
                    except TypeError:
                        params.pop("limit", None)
                        resp = session.get_order_history(**params)
                    if resp.get("retCode") != 0:
                        break
                    lst = (resp.get("result", {}) or {}).get("list", []) or []
                    out.extend(lst)
                    if len(out) >= overall_limit:
                        out = out[:overall_limit]
                        break
                    cursor = (resp.get("result", {}) or {}).get("nextPageCursor")
                    if not cursor or not lst:
                        break

                return out

            for sym, positions in by_symbol.items():
                if not positions:
                    continue
                min_ts = None
                max_ts = None
                expected: Dict[str, Position] = {}
                for p in positions:
                    oid = str(getattr(p, "order_id", "") or "")
                    if not oid:
                        continue
                    expected[f"nertz-exit-{oid}"] = p
                    ts = getattr(p, "timestamp", None)
                    if isinstance(ts, datetime):
                        ts_utc = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
                        if min_ts is None or ts_utc < min_ts:
                            min_ts = ts_utc
                        if max_ts is None or ts_utc > max_ts:
                            max_ts = ts_utc

                if not expected:
                    continue

                if min_ts is None:
                    start_ms = int((datetime.now(timezone.utc) - timedelta(hours=24)).timestamp() * 1000)
                else:
                    start_ms = int((min_ts - timedelta(hours=24)).timestamp() * 1000)
                if max_ts is None:
                    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                else:
                    end_ms = int((max_ts + timedelta(hours=24)).timestamp() * 1000)

                orders = await _fetch_orders(sym, start_ms, end_ms, max_rows=5000)
                link_to_oid: Dict[str, str] = {}
                for o in orders:
                    lid = o.get("orderLinkId") or o.get("orderLinkID") or o.get("order_link_id")
                    if not lid:
                        continue
                    lid_str = str(lid)
                    if not lid_str.startswith("nertz-exit-"):
                        continue
                    oid_val = o.get("orderId") or o.get("orderID") or o.get("id")
                    if oid_val is None:
                        continue
                    link_to_oid[lid_str] = str(oid_val)

                to_update: List[tuple[int, str]] = []
                for lid_str, p in expected.items():
                    exit_oid = link_to_oid.get(lid_str)
                    if exit_oid:
                        to_update.append((int(p.id), exit_oid))

                if to_update:
                    with SessionLocal() as db:
                        for pid, exit_oid in to_update:
                            db.query(Position).filter(Position.id == pid).update({"exit_order_id": exit_oid})
                        db.commit()
                    backfilled += len(to_update)

        await bot._save_results("", None, include_wallet=False)
    finally:
        if prev_force is None:
            os.environ.pop("NERTZ_SINGLE_SESSION_LOG", None)
        else:
            os.environ["NERTZ_SINGLE_SESSION_LOG"] = prev_force

    file_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "logs", "session_results.json"))
    written_trades = None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        trades_obj = data.get("trades") or {}
        if isinstance(trades_obj, dict):
            written_trades = sum(
                len(v) for v in trades_obj.values() if isinstance(v, list)
            )
    except Exception:
        written_trades = None

    total_db_trades = None
    try:
        with SessionLocal() as db:
            total_db_trades = int(db.query(Trade).count())
    except Exception:
        total_db_trades = None

    return {
        "ok": True,
        "written": True,
        "file": file_path,
        "backfilled_exit_order_ids": backfilled,
        "total_db_trades": total_db_trades,
        "written_trades": written_trades,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/analysis/verify_trade_pnl")
async def verify_trade_pnl(
    trade_id: Optional[int] = None,
    symbol: Optional[str] = None,
    order_id: Optional[str] = None,
    exit_order_id: Optional[str] = None,
    order_link_id: Optional[str] = None,
    hours: float = 12.0,
    limit: int = 500,
) -> Dict[str, Any]:
    bot._load_positions()

    wanted_trade: Optional[Dict[str, Any]] = None
    if trade_id is not None:
        tid = int(trade_id)
        for sym in bot.symbols:
            for t in (bot.positions.get(sym) or []):
                if int(t.get("trade_id") or -1) == tid:
                    wanted_trade = t
                    break
            if wanted_trade:
                break

    if wanted_trade is None and symbol and order_id:
        sym = str(symbol).upper()
        for t in (bot.positions.get(sym) or []):
            if str(t.get("order_id") or "") == str(order_id):
                wanted_trade = t
                break

    if wanted_trade is None and trade_id is not None:
        tid = int(trade_id)
        try:
            with SessionLocal() as db:
                row = db.query(Trade).filter(Trade.trade_id == tid).first()
                pos = None
                if row is not None and getattr(row, "order_id", None):
                    pos = (
                        db.query(Position)
                        .filter(Position.order_id == str(row.order_id))
                        .order_by(Position.id.desc())
                        .first()
                    )
        except Exception:
            row = None
            pos = None

        if row is not None:
            ts = getattr(row, "timestamp", None)
            try:
                if isinstance(ts, datetime):
                    ts_utc = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
                    ts_iso = ts_utc.isoformat()
                else:
                    ts_iso = None
            except Exception:
                ts_iso = None

            wanted_trade = {
                "trade_id": getattr(row, "trade_id", None),
                "symbol": getattr(row, "symbol", None),
                "timestamp": ts_iso,
                "order_id": getattr(row, "order_id", None),
                "exit_order_id": (getattr(pos, "exit_order_id", None) if pos is not None else None),
                "order_link_id": getattr(row, "order_link_id", None),
                "profit_loss": getattr(row, "profit_loss", None),
            }

    if wanted_trade is None and symbol and order_id:
        sym = str(symbol).upper()
        oid = str(order_id)
        try:
            with SessionLocal() as db:
                row = db.query(Trade).filter(Trade.symbol == sym).filter(Trade.order_id == oid).first()
                pos = (
                    db.query(Position)
                    .filter(Position.order_id == oid)
                    .order_by(Position.id.desc())
                    .first()
                )
        except Exception:
            row = None
            pos = None

        if row is not None:
            ts = getattr(row, "timestamp", None)
            try:
                if isinstance(ts, datetime):
                    ts_utc = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
                    ts_iso = ts_utc.isoformat()
                else:
                    ts_iso = None
            except Exception:
                ts_iso = None

            wanted_trade = {
                "trade_id": getattr(row, "trade_id", None),
                "symbol": getattr(row, "symbol", None),
                "timestamp": ts_iso,
                "order_id": getattr(row, "order_id", None),
                "exit_order_id": (getattr(pos, "exit_order_id", None) if pos is not None else None),
                "order_link_id": getattr(row, "order_link_id", None),
                "profit_loss": getattr(row, "profit_loss", None),
            }

    if wanted_trade is None:
        latest_ts: Optional[datetime] = None
        for sym in bot.symbols:
            for t in (bot.positions.get(sym) or []):
                ts = t.get("timestamp")
                if not isinstance(ts, str):
                    continue
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except Exception:
                    continue
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if latest_ts is None or dt > latest_ts:
                    latest_ts = dt
                    wanted_trade = t

    if wanted_trade is None:
        return {
            "ok": False,
            "error": "no_trade_found",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    symbol = (symbol or wanted_trade.get("symbol") or "").upper()
    order_id = str(order_id or wanted_trade.get("order_id") or "") or None
    exit_order_id = str(exit_order_id or wanted_trade.get("exit_order_id") or "") or None
    order_link_id = str(order_link_id or wanted_trade.get("order_link_id") or "") or None

    ts_str = wanted_trade.get("timestamp")
    trade_dt = datetime.now(timezone.utc)
    if isinstance(ts_str, str) and ts_str:
        try:
            trade_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            if trade_dt.tzinfo is None:
                trade_dt = trade_dt.replace(tzinfo=timezone.utc)
        except Exception:
            trade_dt = datetime.now(timezone.utc)

    window_hours = max(0.25, float(hours))
    start_ms = int((trade_dt - timedelta(hours=window_hours)).timestamp() * 1000)
    end_ms = int((trade_dt + timedelta(hours=window_hours)).timestamp() * 1000)

    if not config.BYBIT_API_KEY or not config.BYBIT_API_SECRET:
        return {
            "ok": False,
            "error": "missing_bybit_credentials",
            "trade": {
                "trade_id": wanted_trade.get("trade_id"),
                "symbol": symbol,
                "timestamp": wanted_trade.get("timestamp"),
                "order_id": order_id,
                "exit_order_id": exit_order_id,
                "order_link_id": order_link_id,
            },
            "utc_window": {"start_ms": start_ms, "end_ms": end_ms},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    session: HTTP = await bot._get_bybit_http()

    async def _fetch_execs(sym: str, start: int, end: int, max_rows: int) -> Dict[str, Any]:
        overall_limit = max(1, int(max_rows))
        page_limit = min(50, overall_limit)
        max_pages = min(200, max(1, int(math.ceil(overall_limit / page_limit)) + 5))
        cursor: Optional[str] = None
        out: List[Dict[str, Any]] = []
        last_resp: Dict[str, Any] = {}

        for _ in range(max_pages):
            params: Dict[str, Any] = {
                "category": "spot",
                "symbol": sym,
                "timestamp": str(await get_synced_time()),
                "recvWindow": str(int(config.RECV_WINDOW)),
                "limit": page_limit,
                "startTime": start,
                "endTime": end,
            }
            if cursor:
                params["cursor"] = cursor
            try:
                resp = session.get_executions(**params)
            except TypeError:
                params.pop("limit", None)
                resp = session.get_executions(**params)

            last_resp = resp
            if resp.get("retCode") != 0:
                break

            lst = (resp.get("result", {}) or {}).get("list", []) or []
            out.extend(lst)

            if len(out) >= overall_limit:
                out = out[:overall_limit]
                break

            cursor = (resp.get("result", {}) or {}).get("nextPageCursor")
            if not cursor or not lst:
                break

        return {"retCode": last_resp.get("retCode"), "retMsg": last_resp.get("retMsg"), "list": out}

    def _analyze_execs(sym: str, execs: List[Dict[str, Any]]) -> Dict[str, Any]:
        def _as_float(x: Any) -> float:
            try:
                return float(x)
            except Exception:
                return 0.0

        def _ms_to_iso(ms: Any) -> Optional[str]:
            try:
                return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()
            except Exception:
                return None

        base_ccy, quote_ccy = bot._split_symbol_base_quote(sym)
        base_ccy = (base_ccy or "").upper()
        quote_ccy = (quote_ccy or "").upper()

        execs_sorted = sorted(execs, key=lambda e: int(e.get("execTime") or e.get("tradeTime") or 0))
        fees: Dict[str, float] = defaultdict(float)
        realized = 0.0
        lots: deque[list[float]] = deque()
        exec_buy_qty = 0.0
        exec_sell_qty = 0.0
        buy_value = 0.0
        sell_value = 0.0
        unmatched_sell_qty = 0.0
        unmatched_sell_notional = 0.0

        for e in execs_sorted:
            side = str(e.get("side") or "").upper()
            px = _as_float(e.get("execPrice") or e.get("price"))
            qty = _as_float(e.get("execQty") or e.get("qty"))
            fee = _as_float(e.get("execFee") or e.get("fee"))
            fee_ccy = str(e.get("feeCurrency") or e.get("execFeeCurrency") or "").upper()

            if fee_ccy:
                fees[fee_ccy] += fee

            if side == "BUY":
                exec_buy_qty += qty
                cost = px * qty
                if quote_ccy and fee_ccy == quote_ccy:
                    cost += fee
                qty_in = qty
                if base_ccy and fee_ccy == base_ccy:
                    qty_in = max(0.0, qty - fee)
                buy_value += px * qty
                lots.append([qty_in, cost])
            elif side == "SELL":
                exec_sell_qty += qty
                sell_value += px * qty

                qty_to_match = qty
                if base_ccy and fee_ccy == base_ccy:
                    qty_to_match = max(0.0, qty - fee)

                proceeds_per_unit = px
                if quote_ccy and fee_ccy == quote_ccy and qty > 0:
                    proceeds_per_unit = (px * qty - fee) / qty

                while qty_to_match > 1e-12 and lots:
                    lot_qty, lot_cost = lots[0]
                    take = min(lot_qty, qty_to_match)
                    avg_cost = lot_cost / lot_qty if lot_qty > 0 else 0.0
                    cost_part = avg_cost * take
                    realized += (proceeds_per_unit * take) - cost_part
                    lot_qty -= take
                    lot_cost -= cost_part
                    qty_to_match -= take
                    if lot_qty <= 1e-12:
                        lots.popleft()
                    else:
                        lots[0] = [lot_qty, lot_cost]

                if qty_to_match > 1e-9:
                    unmatched_sell_qty += qty_to_match
                    unmatched_sell_notional += proceeds_per_unit * qty_to_match

        open_qty = sum(q for q, _c in lots)
        open_cost = sum(_c for _q, _c in lots)
        open_avg = (open_cost / open_qty) if open_qty > 0 else 0.0

        first_exec = execs_sorted[0] if execs_sorted else None
        last_exec = execs_sorted[-1] if execs_sorted else None

        return {
            "executions_total": len(execs_sorted),
            "executions_window": {
                "first": _ms_to_iso(first_exec.get("execTime")) if first_exec else None,
                "last": _ms_to_iso(last_exec.get("execTime")) if last_exec else None,
            },
            "executed_qty": {"buy": exec_buy_qty, "sell": exec_sell_qty},
            "notional_quote": {"buy": buy_value, "sell": sell_value},
            "fees": dict(fees),
            "realized_pnl_est_quote": realized,
            "unmatched_sell": {"qty_base": unmatched_sell_qty, "notional_quote": unmatched_sell_notional},
            "open_position": {"qty_base": open_qty, "avg_cost_quote": open_avg},
        }

    execs_resp = await _fetch_execs(symbol, start_ms, end_ms, max_rows=limit)
    execs = execs_resp.get("list") or []

    wanted_order_ids = {str(x) for x in [order_id, exit_order_id] if x}
    wanted_link_ids = {str(x) for x in [order_link_id] if x}
    if order_id:
        wanted_link_ids.add(f"nertz-exit-{order_id}")

    filtered: List[Dict[str, Any]] = []
    for e in execs:
        oid = str(e.get("orderId") or e.get("orderID") or "")
        olid = str(e.get("orderLinkId") or e.get("orderLinkID") or "")
        if (oid and oid in wanted_order_ids) or (olid and olid in wanted_link_ids):
            filtered.append(e)
            continue
        if order_link_id and olid and olid.startswith(order_link_id):
            filtered.append(e)

    seen_exec: set[str] = set()
    deduped: List[Dict[str, Any]] = []
    for e in filtered:
        key = e.get("execId") or e.get("execID") or e.get("id")
        if key is None:
            deduped.append(e)
            continue
        key_str = str(key)
        if key_str in seen_exec:
            continue
        seen_exec.add(key_str)
        deduped.append(e)

    analysis = _analyze_execs(symbol, deduped)
    bot_pnl = None
    try:
        bot_pnl = float(wanted_trade.get("profit_loss"))
    except Exception:
        bot_pnl = None

    return {
        "ok": True,
        "use_testnet": config.USE_TESTNET,
        "trade": {
            "trade_id": wanted_trade.get("trade_id"),
            "symbol": symbol,
            "timestamp": wanted_trade.get("timestamp"),
            "order_id": order_id,
            "exit_order_id": exit_order_id,
            "order_link_id": order_link_id,
            "bot_profit_loss": bot_pnl,
        },
        "utc_window": {"start_ms": start_ms, "end_ms": end_ms},
        "executions_ret": {"retCode": execs_resp.get("retCode"), "retMsg": execs_resp.get("retMsg")},
        "executions": {"total": len(deduped), "list": deduped[: min(200, len(deduped))]},
        "analysis": analysis,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/analysis/simulate_trades")
async def analysis_simulate_trades(
    request: Request,
    symbols: Optional[str] = None,
    hours: float = 72.0,
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
    limit_trades: int = 500,
    action: str = "buy",
    only_closed: bool = True,
    use_bybit_pnl: bool = True,
    bybit_exec_limit: int = 20000,
    include_trade_rows: bool = True,
    max_trade_rows: int = 250,
) -> Dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    action_wanted = str(action or "").strip().lower() or "buy"
    only_closed = bool(only_closed)
    include_trade_rows = bool(include_trade_rows)
    max_trade_rows = max(0, min(5000, int(max_trade_rows)))
    max_rows = max(1, min(5000, int(limit_trades)))

    now_ms = await get_synced_time()
    if end_ms is None:
        end_ms = int(now_ms)
    else:
        end_ms = int(end_ms)
    if start_ms is None:
        h = float(hours)
        if h <= 0:
            h = 72.0
        start_ms = int((datetime.fromtimestamp(end_ms / 1000, tz=timezone.utc) - timedelta(hours=h)).timestamp() * 1000)
    else:
        start_ms = int(start_ms)

    start_dt = datetime.fromtimestamp(int(start_ms) / 1000, tz=timezone.utc)
    end_dt = datetime.fromtimestamp(int(end_ms) / 1000, tz=timezone.utc)
    start_dt_db = start_dt.replace(tzinfo=None)
    end_dt_db = end_dt.replace(tzinfo=None)

    if symbols:
        symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        symbol_list = [s.strip().upper() for s in config.SYMBOL.split(",") if s.strip()]

    requested_run_id = payload.get("run_id") if isinstance(payload, dict) else None
    run_id = str(requested_run_id).strip() if requested_run_id else str(uuid.uuid4())

    def _as_finite_float(value: Any) -> float:
        try:
            v = float(value)
        except Exception:
            return 0.0
        if not math.isfinite(v):
            return 0.0
        return v

    def _pearson_corr(xs: List[float], ys: List[float]) -> float:
        n = min(len(xs), len(ys))
        if n < 2:
            return 0.0
        mx = sum(xs[:n]) / n
        my = sum(ys[:n]) / n
        cov = 0.0
        vx = 0.0
        vy = 0.0
        for i in range(n):
            dx = xs[i] - mx
            dy = ys[i] - my
            cov += dx * dy
            vx += dx * dx
            vy += dy * dy
        if vx <= 1e-18 or vy <= 1e-18:
            return 0.0
        return cov / math.sqrt(vx * vy)

    def _normalize_weights(raw: Dict[str, float]) -> Dict[str, float]:
        cleaned: Dict[str, float] = {str(k): _as_finite_float(v) for k, v in (raw or {}).items()}
        total = sum(abs(v) for v in cleaned.values())
        if total <= 1e-18:
            return {str(k): 0.0 for k in cleaned.keys()}
        return {k: (v / total) for k, v in cleaned.items()}

    metrics_keys = ["egm", "combined", "ild", "rol", "pio", "ogm"]
    closed_decisions = {"closed", "closed_external"}

    with SessionLocal() as db:
        q = db.query(Trade)
        if symbol_list:
            q = q.filter(Trade.symbol.in_(symbol_list))
        q = q.filter(Trade.timestamp >= start_dt_db).filter(Trade.timestamp <= end_dt_db)
        if action_wanted:
            q = q.filter(Trade.action == action_wanted)
        if only_closed:
            q = q.filter(Trade.decision.in_(list(closed_decisions)))
        trades: List[Trade] = q.order_by(Trade.timestamp.asc()).limit(max_rows).all()

    if use_bybit_pnl and (not config.BYBIT_API_KEY or not config.BYBIT_API_SECRET):
        use_bybit_pnl = False

    session: Optional[HTTP] = None
    if use_bybit_pnl:
        session = await bot._get_bybit_http()

    async def _fetch_execs(sym: str, start: int, end: int, max_rows: int) -> Dict[str, Any]:
        overall_limit = max(1, int(max_rows))
        page_limit = min(50, overall_limit)
        max_pages = min(200, max(1, int(math.ceil(overall_limit / page_limit)) + 5))
        cursor: Optional[str] = None
        out: List[Dict[str, Any]] = []
        last_resp: Dict[str, Any] = {}

        if session is None:
            return {"retCode": -1, "retMsg": "no_session", "list": []}

        for _ in range(max_pages):
            params: Dict[str, Any] = {
                "category": "spot",
                "symbol": sym,
                "timestamp": str(await get_synced_time()),
                "recvWindow": str(int(config.RECV_WINDOW)),
                "limit": page_limit,
                "startTime": int(start),
                "endTime": int(end),
            }
            if cursor:
                params["cursor"] = cursor
            try:
                resp = session.get_executions(**params)
            except TypeError:
                params.pop("limit", None)
                resp = session.get_executions(**params)

            last_resp = resp
            if resp.get("retCode") != 0:
                break
            lst = (resp.get("result", {}) or {}).get("list", []) or []
            out.extend(lst)
            if len(out) >= overall_limit:
                out = out[:overall_limit]
                break
            cursor = (resp.get("result", {}) or {}).get("nextPageCursor")
            if not cursor or not lst:
                break

        return {"retCode": last_resp.get("retCode"), "retMsg": last_resp.get("retMsg"), "list": out}

    def _analyze_execs(sym: str, execs: List[Dict[str, Any]]) -> Dict[str, Any]:
        def _ms_to_iso(ms: Any) -> Optional[str]:
            try:
                return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()
            except Exception:
                return None

        base_ccy, quote_ccy = bot._split_symbol_base_quote(sym)
        base_ccy = (base_ccy or "").upper() or None
        quote_ccy = (quote_ccy or "").upper() or None

        execs_filtered: List[Dict[str, Any]] = []
        for e in execs:
            exec_type = str(e.get("execType") or "").strip().upper()
            if exec_type and exec_type != "TRADE":
                continue
            execs_filtered.append(e)

        execs_sorted = sorted(execs_filtered, key=lambda e: int(e.get("execTime") or e.get("tradeTime") or 0))
        fees: Dict[str, float] = defaultdict(float)
        realized = 0.0
        lots: deque[list[float]] = deque()
        exec_buy_qty = 0.0
        exec_sell_qty = 0.0
        buy_value = 0.0
        sell_value = 0.0
        unmatched_sell_qty = 0.0
        unmatched_sell_notional = 0.0

        for e in execs_sorted:
            side = str(e.get("side") or "").upper()
            px = _as_finite_float(e.get("execPrice") or e.get("price"))
            qty = _as_finite_float(e.get("execQty") or e.get("qty"))
            fee = _as_finite_float(e.get("execFee") or e.get("fee"))
            fee_ccy = str(e.get("feeCurrency") or e.get("execFeeCurrency") or "").upper()

            if fee_ccy:
                fees[fee_ccy] += fee

            if side == "BUY":
                exec_buy_qty += qty
                cost = px * qty
                if quote_ccy and fee_ccy == quote_ccy:
                    cost += fee
                qty_in = qty
                if base_ccy and fee_ccy == base_ccy:
                    qty_in = max(0.0, qty - fee)
                buy_value += px * qty
                lots.append([qty_in, cost])
            elif side == "SELL":
                exec_sell_qty += qty
                sell_value += px * qty

                qty_to_match = qty
                if base_ccy and fee_ccy == base_ccy:
                    qty_to_match = max(0.0, qty - fee)

                proceeds_per_unit = px
                if quote_ccy and fee_ccy == quote_ccy and qty > 0:
                    proceeds_per_unit = (px * qty - fee) / qty

                while qty_to_match > 1e-12 and lots:
                    lot_qty, lot_cost = lots[0]
                    take = min(lot_qty, qty_to_match)
                    avg_cost = lot_cost / lot_qty if lot_qty > 0 else 0.0
                    cost_part = avg_cost * take
                    realized += (proceeds_per_unit * take) - cost_part
                    lot_qty -= take
                    lot_cost -= cost_part
                    qty_to_match -= take
                    if lot_qty <= 1e-12:
                        lots.popleft()
                    else:
                        lots[0] = [lot_qty, lot_cost]

                if qty_to_match > 1e-9:
                    unmatched_sell_qty += qty_to_match
                    unmatched_sell_notional += proceeds_per_unit * qty_to_match

        open_qty = sum(q for q, _c in lots)
        open_cost = sum(_c for _q, _c in lots)
        open_avg = (open_cost / open_qty) if open_qty > 0 else 0.0
        first_exec = execs_sorted[0] if execs_sorted else None
        last_exec = execs_sorted[-1] if execs_sorted else None

        return {
            "executions_total": len(execs_sorted),
            "executions_window": {
                "first": _ms_to_iso(first_exec.get("execTime") or first_exec.get("tradeTime")) if first_exec else None,
                "last": _ms_to_iso(last_exec.get("execTime") or last_exec.get("tradeTime")) if last_exec else None,
            },
            "executed_qty": {"buy": exec_buy_qty, "sell": exec_sell_qty},
            "notional_quote": {"buy": buy_value, "sell": sell_value},
            "fees": dict(fees),
            "realized_pnl_est_quote": realized,
            "unmatched_sell": {"qty_base": unmatched_sell_qty, "notional_quote": unmatched_sell_notional},
            "open_position": {"qty_base": open_qty, "avg_cost_quote": open_avg},
        }

    execs_by_symbol: Dict[str, Dict[str, Any]] = {}
    if use_bybit_pnl and trades:
        symbols_needed = sorted({str(getattr(t, "symbol", "") or "").upper() for t in trades if getattr(t, "symbol", None)})
        for sym in symbols_needed:
            execs_resp = await _fetch_execs(sym, start_ms, end_ms, max_rows=min(20000, max(1, int(bybit_exec_limit))))
            raw_execs = execs_resp.get("list") or []

            seen_exec: set[str] = set()
            deduped: List[Dict[str, Any]] = []
            for e in raw_execs:
                key = e.get("execId") or e.get("execID") or e.get("id")
                if key is None:
                    deduped.append(e)
                    continue
                key_str = str(key)
                if key_str in seen_exec:
                    continue
                seen_exec.add(key_str)
                deduped.append(e)

            by_order_id: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
            by_link_id: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
            for e in deduped:
                oid = e.get("orderId") or e.get("orderID")
                olid = e.get("orderLinkId") or e.get("orderLinkID")
                if oid is not None:
                    by_order_id[str(oid)].append(e)
                if olid is not None:
                    by_link_id[str(olid)].append(e)

            execs_by_symbol[sym] = {
                "ret": {"retCode": execs_resp.get("retCode"), "retMsg": execs_resp.get("retMsg")},
                "all": deduped,
                "by_order_id": by_order_id,
                "by_link_id": by_link_id,
            }

    async def _get_defaults() -> Dict[str, Any]:
        try:
            tpl = await get_formula_templates()
        except Exception:
            tpl = {"weights": {}, "thresholds": {}, "metrics": []}
        return tpl

    defaults = await _get_defaults()
    default_weights = dict(defaults.get("weights") or {})
    default_thresholds = dict(defaults.get("thresholds") or {})

    weights_in = payload.get("weights") if isinstance(payload, dict) else None
    thresholds_in = payload.get("thresholds") if isinstance(payload, dict) else None

    provided_weights = weights_in if isinstance(weights_in, dict) else {}
    provided_thresholds = thresholds_in if isinstance(thresholds_in, dict) else {}

    used_thresholds = {
        "score_threshold": _as_finite_float(provided_thresholds.get("score_threshold", default_thresholds.get("score_threshold", 0.5))),
        "combined_threshold": _as_finite_float(provided_thresholds.get("combined_threshold", default_thresholds.get("combined_threshold", 0.75))),
        "egm_buy_threshold": _as_finite_float(provided_thresholds.get("egm_buy_threshold", default_thresholds.get("egm_buy_threshold", config.EGM_BUY_THRESHOLD))),
        "egm_sell_threshold": _as_finite_float(provided_thresholds.get("egm_sell_threshold", default_thresholds.get("egm_sell_threshold", config.EGM_SELL_THRESHOLD))),
        "strong_score_threshold": _as_finite_float(provided_thresholds.get("strong_score_threshold", default_thresholds.get("strong_score_threshold", 0.75))),
    }

    def _eval_decision(metrics: Dict[str, float], weights: Dict[str, float], thresholds: Dict[str, float]) -> Dict[str, Any]:
        buy_score = 0.0
        for k, w in (weights or {}).items():
            buy_score += _as_finite_float(metrics.get(k)) * _as_finite_float(w)
        sell_score = -buy_score

        egm = _as_finite_float(metrics.get("egm"))
        combined = _as_finite_float(metrics.get("combined"))

        if buy_score >= thresholds["score_threshold"] or combined >= thresholds["combined_threshold"] or (
            egm >= thresholds["egm_buy_threshold"] and combined >= thresholds["combined_threshold"]
        ):
            decision = "buy"
        elif sell_score >= thresholds["score_threshold"] or combined <= -thresholds["combined_threshold"] or (
            egm <= thresholds["egm_sell_threshold"] and combined <= -thresholds["combined_threshold"]
        ):
            decision = "sell"
        else:
            decision = "hold"

        strong = float(max(buy_score, sell_score)) >= float(thresholds["strong_score_threshold"])

        return {
            "decision": decision,
            "strong": bool(strong),
            "scores": {"buy_score": float(buy_score), "sell_score": float(sell_score)},
        }

    def _trade_metrics_row(t: Trade) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for k in metrics_keys:
            out[k] = _as_finite_float(getattr(t, k, 0.0))
        return out

    def _trade_return_from_db(t: Trade) -> Dict[str, Optional[float]]:
        pnl = None
        try:
            pnl = float(getattr(t, "profit_loss", None))
        except Exception:
            pnl = None
        notional = _as_finite_float(getattr(t, "entry_price", 0.0)) * _as_finite_float(getattr(t, "quantity", 0.0))
        ret = (pnl / notional) if pnl is not None and notional > 1e-18 else None
        return {"pnl_quote": pnl, "notional_quote": notional if notional > 0 else None, "return_pct": ret}

    def _trade_return_from_bybit(t: Trade) -> Dict[str, Any]:
        sym = str(getattr(t, "symbol", "") or "").upper()
        sym_execs = execs_by_symbol.get(sym) or {}
        by_order_id = sym_execs.get("by_order_id") or {}
        by_link_id = sym_execs.get("by_link_id") or {}

        oid = getattr(t, "order_id", None)
        oid_str = str(oid) if oid is not None else ""
        olink = getattr(t, "order_link_id", None)
        olink_str = str(olink) if olink else ""

        selected: List[Dict[str, Any]] = []
        if oid_str:
            selected.extend(by_order_id.get(oid_str) or [])
            selected.extend(by_link_id.get(f"nertz-exit-{oid_str}") or [])
        if olink_str:
            selected.extend(by_link_id.get(olink_str) or [])

        seen_exec: set[str] = set()
        deduped: List[Dict[str, Any]] = []
        for e in selected:
            key = e.get("execId") or e.get("execID") or e.get("id")
            if key is None:
                deduped.append(e)
                continue
            key_str = str(key)
            if key_str in seen_exec:
                continue
            seen_exec.add(key_str)
            deduped.append(e)

        analysis = _analyze_execs(sym, deduped)
        pnl = _as_finite_float(analysis.get("realized_pnl_est_quote"))
        buy_notional = _as_finite_float((analysis.get("notional_quote") or {}).get("buy"))
        notional = buy_notional
        if notional <= 1e-18:
            notional = _as_finite_float(getattr(t, "entry_price", 0.0)) * _as_finite_float(getattr(t, "quantity", 0.0))
        ret = (pnl / notional) if notional > 1e-18 else None

        return {
            "pnl_quote": float(pnl),
            "notional_quote": float(notional) if notional > 0 else None,
            "return_pct": float(ret) if ret is not None else None,
            "analysis": analysis,
            "executions": {"total": int(analysis.get("executions_total") or 0)},
        }

    per_trade: List[Dict[str, Any]] = []
    returns_for_fit: List[float] = []
    metrics_for_fit: Dict[str, List[float]] = {k: [] for k in metrics_keys}

    for t in trades:
        sym = str(getattr(t, "symbol", "") or "").upper()
        ts = getattr(t, "timestamp", None)
        ts_iso = ts.isoformat() if ts is not None else None
        row_metrics = _trade_metrics_row(t)

        pnl_src = payload.get("pnl_source") if isinstance(payload, dict) else None
        pnl_source = str(pnl_src).strip().lower() if pnl_src else ("bybit" if use_bybit_pnl else "db")

        if pnl_source == "bybit" and use_bybit_pnl:
            ret_info = _trade_return_from_bybit(t)
        else:
            ret_info = _trade_return_from_db(t)

        r_pct = ret_info.get("return_pct")
        if r_pct is not None and math.isfinite(float(r_pct)):
            returns_for_fit.append(float(r_pct))
            for k in metrics_keys:
                metrics_for_fit[k].append(_as_finite_float(row_metrics.get(k)))

        if include_trade_rows:
            per_trade.append(
                {
                    "trade_id": getattr(t, "trade_id", None),
                    "timestamp": ts_iso,
                    "symbol": sym,
                    "action": getattr(t, "action", None),
                    "decision": getattr(t, "decision", None),
                    "entry_price": _as_finite_float(getattr(t, "entry_price", 0.0)),
                    "exit_price": getattr(t, "exit_price", None),
                    "quantity": _as_finite_float(getattr(t, "quantity", 0.0)),
                    "profit_loss": getattr(t, "profit_loss", None),
                    "order_id": getattr(t, "order_id", None),
                    "order_link_id": getattr(t, "order_link_id", None),
                    "metrics": row_metrics,
                    "pnl": {k: v for k, v in ret_info.items() if k != "analysis"},
                }
            )

    corrs: Dict[str, float] = {}
    for k in metrics_keys:
        corrs[k] = float(_pearson_corr(metrics_for_fit.get(k) or [], returns_for_fit))

    weights_suggested_signed = {k: corrs.get(k, 0.0) for k in metrics_keys}
    weights_suggested_pos = {k: max(0.0, float(corrs.get(k, 0.0))) for k in metrics_keys}
    weights_suggested_abs = {k: abs(float(corrs.get(k, 0.0))) for k in metrics_keys}

    weights_suggested = _normalize_weights(weights_suggested_pos)
    weights_suggested_abs_norm = _normalize_weights(weights_suggested_abs)

    used_default_weights: Dict[str, float] = {}
    for k in metrics_keys:
        used_default_weights[k] = _as_finite_float(provided_weights.get(k, default_weights.get(k, 0.0)))
    used_default_weights = _normalize_weights(used_default_weights)

    def _run_sim(weights: Dict[str, float]) -> Dict[str, Any]:
        taken = 0
        skipped = 0
        pnl_sum = 0.0
        returns: List[float] = []
        wins = 0
        losses = 0

        per_row: List[Dict[str, Any]] = []
        src = payload.get("pnl_source") if isinstance(payload, dict) else None
        pnl_source = str(src).strip().lower() if src else ("bybit" if use_bybit_pnl else "db")

        for t in trades:
            row_metrics = _trade_metrics_row(t)
            eval_out = _eval_decision(row_metrics, weights, used_thresholds)
            decision = eval_out["decision"]
            would_take = (decision == action_wanted)
            ret_info = _trade_return_from_bybit(t) if pnl_source == "bybit" and use_bybit_pnl else _trade_return_from_db(t)
            pnl = ret_info.get("pnl_quote")
            r_pct = ret_info.get("return_pct")

            if would_take and pnl is not None:
                taken += 1
                pnl_sum += float(pnl)
                if r_pct is not None and math.isfinite(float(r_pct)):
                    returns.append(float(r_pct))
                if float(pnl) >= 0:
                    wins += 1
                else:
                    losses += 1
            else:
                skipped += 1

            if include_trade_rows and len(per_row) < max_trade_rows:
                ts = getattr(t, "timestamp", None)
                ts_iso = ts.isoformat() if ts is not None else None
                per_row.append(
                    {
                        "trade_id": getattr(t, "trade_id", None),
                        "timestamp": ts_iso,
                        "symbol": str(getattr(t, "symbol", "") or "").upper(),
                        "action": getattr(t, "action", None),
                        "decision": decision,
                        "strong": bool(eval_out.get("strong")),
                        "scores": eval_out.get("scores"),
                        "metrics": row_metrics,
                        "pnl": ret_info,
                        "would_take": bool(would_take),
                    }
                )

        avg_return = (sum(returns) / len(returns)) if returns else None
        win_rate = (wins / taken) if taken > 0 else None
        return {
            "weights": weights,
            "thresholds": used_thresholds,
            "counts": {"trades": len(trades), "taken": taken, "skipped": skipped},
            "pnl": {"sum_quote": float(pnl_sum), "avg_return_pct": float(avg_return) if avg_return is not None else None},
            "win_loss": {"wins": wins, "losses": losses, "win_rate": float(win_rate) if win_rate is not None else None},
            "trades": per_row if include_trade_rows else [],
        }

    sim_default = _run_sim(used_default_weights)
    sim_suggested = _run_sim(weights_suggested)

    log_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "logs"))
    os.makedirs(log_dir, exist_ok=True)
    file_path = os.path.join(log_dir, "simulation_results.json")
    lock_path = os.path.join(log_dir, "simulation_results.lock")
    temp_path = os.path.join(log_dir, f"simulation_results_{run_id}_{os.getpid()}.tmp")

    run_payload: Dict[str, Any] = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "use_testnet": config.USE_TESTNET,
        "query": {
            "symbols": symbol_list,
            "utc_window": {"start_ms": int(start_ms), "end_ms": int(end_ms), "start": start_dt.isoformat(), "end": end_dt.isoformat()},
            "limit_trades": max_rows,
            "action": action_wanted,
            "only_closed": only_closed,
            "use_bybit_pnl": bool(use_bybit_pnl),
            "bybit_exec_limit": min(20000, max(1, int(bybit_exec_limit))),
            "pnl_source": (payload.get("pnl_source") if isinstance(payload, dict) else None) or ("bybit" if use_bybit_pnl else "db"),
        },
        "fit": {
            "trades_with_return": len(returns_for_fit),
            "correlations": corrs,
            "weights_suggested_signed": weights_suggested_signed,
            "weights_suggested_positive_norm": weights_suggested,
            "weights_suggested_abs_norm": weights_suggested_abs_norm,
        },
        "simulation": {
            "default": sim_default,
            "suggested": sim_suggested,
        },
        "bybit": {
            "executions": {
                sym: {
                    "ret": (execs_by_symbol.get(sym) or {}).get("ret"),
                    "count": len((execs_by_symbol.get(sym) or {}).get("all") or []),
                }
                for sym in sorted(execs_by_symbol.keys())
            }
        },
    }

    if include_trade_rows:
        run_payload["trades_input"] = per_trade[:max_trade_rows]

    lock_file = None
    written = False
    try:
        try:
            lock_file = open(lock_path, "a+")
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write("0")
                lock_file.flush()
            lock_file.seek(0)
            if msvcrt is not None:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        except Exception:
            lock_file = None

        def _sanitize_json_obj(x: Any) -> Any:
            if isinstance(x, float):
                return x if math.isfinite(x) else None
            if isinstance(x, dict):
                return {str(k): _sanitize_json_obj(v) for k, v in x.items()}
            if isinstance(x, list):
                return [_sanitize_json_obj(v) for v in x]
            return x

        existing: Dict[str, Any] = {}
        try:
            if os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                existing = obj if isinstance(obj, dict) else {}
        except Exception:
            existing = {}

        existing = _sanitize_json_obj(existing)
        run_payload = _sanitize_json_obj(run_payload)

        runs = existing.get("runs")
        if not isinstance(runs, list):
            runs = []
        runs.append(run_payload)
        if len(runs) > 500:
            runs = runs[-500:]
        existing["runs"] = runs
        existing["updated_at"] = datetime.now(timezone.utc).isoformat()

        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False, allow_nan=False, default=str)
        os.replace(temp_path, file_path)
        written = True
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        if lock_file is not None:
            try:
                lock_file.seek(0)
                if msvcrt is not None:
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
            try:
                lock_file.close()
            except Exception:
                pass

    return {
        "ok": True,
        "written": bool(written),
        "file": os.path.abspath(file_path),
        "run": run_payload,
    }


@app.post("/execute_trade/{symbol}")
async def execute_trade(symbol: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    if not bool(getattr(bot, "trading_enabled", True)):
        return {
            "message": "Trading disabled",
            "symbol": symbol,
            "skipped": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    await bot._execute_trade(symbol, db)
    return {"message": f"✅ Trade ejecutado para {symbol}", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/config")
async def get_config() -> Dict[str, Union[str, float, int, bool]]:
    return {
        "symbol": config.SYMBOL,
        "timeframe": config.TIMEFRAME,
        "order_type": config.ORDER_TYPE,
        "time_in_force": config.TIME_IN_FORCE,
        "orderbook_depth": config.ORDERBOOK_DEPTH,
        "use_testnet": config.USE_TESTNET,
        "capital_usdt": bot.capital,
        "risk_factor": config.RISK_FACTOR,
        "min_trade_size": config.MIN_TRADE_SIZE,
        "max_trade_size": config.MAX_TRADE_SIZE,
        "fee_rate": config.FEE_RATE,
        "tp_percentage": config.TP_PERCENTAGE,
        "sl_percentage": config.SL_PERCENTAGE,
        "egm_buy_threshold": config.EGM_BUY_THRESHOLD,
        "egm_sell_threshold": config.EGM_SELL_THRESHOLD,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.post("/config/update_all")
async def update_all_config(config_data: Dict[str, Union[str, float, int]]) -> Dict[str, str]:
    if "capital_usdt" in config_data:
        bot.capital = float(config_data["capital_usdt"]) if float(config_data["capital_usdt"]) > 0 else bot.capital
        bot.initial_capital = bot.capital
    if "risk_factor" in config_data:
        config.RISK_FACTOR = max(0.0, min(1.0, float(config_data["risk_factor"])))
    if "egm_buy_threshold" in config_data:
        config.EGM_BUY_THRESHOLD = float(config_data["egm_buy_threshold"])
    if "egm_sell_threshold" in config_data:
        config.EGM_SELL_THRESHOLD = float(config_data["egm_sell_threshold"])
    logger.info(f"✅ Configuración actualizada: {config_data}")
    return {"message": "Configuración actualizada", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.get("/technical_analysis/{symbol}")
def get_technical_analysis(symbol: str, db: Session = Depends(get_db), limit: int = 200):
    """Calcula y devuelve indicadores técnicos básicos."""
    
    # 1. Obtener datos históricos de la base de datos
    query = db.query(MarketData).filter(MarketData.symbol == symbol).order_by(MarketData.timestamp.desc()).limit(limit)
    data = pd.read_sql(query.statement, query.session.bind)
    
    if data.empty or len(data) < 20: # Se necesita un mínimo de datos para los indicadores
        return {"error": "No hay suficientes datos históricos para calcular el análisis técnico."}
        
    # Asegurarse de que los datos están en orden cronológico ascendente para los cálculos
    data = data.sort_values(by='timestamp', ascending=True)

    # 2. Calcular indicadores técnicos
    # RSI (Relative Strength Index)
    data['rsi'] = momentum.RSIIndicator(close=data['close'], window=14).rsi()
    
    # MACD (Moving Average Convergence Divergence)
    macd = trend.MACD(close=data['close'], window_slow=26, window_fast=12, window_sign=9)
    data['macd'] = macd.macd()
    data['macd_signal'] = macd.macd_signal()
    data['macd_diff'] = macd.macd_diff()
    
    # Bollinger Bands
    bollinger = volatility.BollingerBands(close=data['close'], window=20, window_dev=2)
    data['bb_high'] = bollinger.bollinger_hband()
    data['bb_low'] = bollinger.bollinger_lband()
    data['bb_mid'] = bollinger.bollinger_mavg()

    # 3. Devolver los valores más recientes
    latest_data = data.iloc[-1]

    ts = latest_data["timestamp"]
    try:
        if hasattr(ts, "to_pydatetime"):
            ts = ts.to_pydatetime()
    except Exception:
        pass
    try:
        if isinstance(ts, datetime):
            ts = ts.astimezone(timezone.utc).isoformat()
        else:
            ts = str(ts)
    except Exception:
        ts = datetime.now(timezone.utc).isoformat()

    return {
        "symbol": symbol,
        "timestamp": ts,
        "close": latest_data['close'],
        "rsi": round(latest_data['rsi'], 2) if pd.notna(latest_data['rsi']) else None,
        "macd": {
            "value": round(latest_data['macd'], 2) if pd.notna(latest_data['macd']) else None,
            "signal": round(latest_data['macd_signal'], 2) if pd.notna(latest_data['macd_signal']) else None,
            "histogram": round(latest_data['macd_diff'], 2) if pd.notna(latest_data['macd_diff']) else None,
        },
        "bollinger_bands": {
            "high": round(latest_data['bb_high'], 2) if pd.notna(latest_data['bb_high']) else None,
            "middle": round(latest_data['bb_mid'], 2) if pd.notna(latest_data['bb_mid']) else None,
            "low": round(latest_data['bb_low'], 2) if pd.notna(latest_data['bb_low']) else None,
        }
    }    


@app.post("/start")
async def start_bot() -> Dict[str, str]:
    if not bot.running:
        bot.running = True
        bot.paused = False
        disable_trading = os.getenv("NERTZ_DISABLE_TRADING", "").strip().lower() in {"1", "true", "yes", "y", "on"}
        if disable_trading:
            bot.trading_enabled = False
        asyncio.create_task(bot.start_async())
        return {"message": "✅ Bot iniciado", "timestamp": datetime.now(timezone.utc).isoformat()}
    return {"message": "⚠️ Bot ya está corriendo", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/stop")
async def stop_bot() -> Dict[str, str]:
    if bot.running:
        bot.stop()
        return {"message": "🛑 Bot detenido", "timestamp": datetime.now(timezone.utc).isoformat()}
    return {"message": "⚠️ Bot ya está detenido", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/trading")
async def trading_status() -> Dict[str, Any]:
    return {
        "trading_enabled": bool(getattr(bot, "trading_enabled", True)),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/trading/disable")
async def trading_disable() -> Dict[str, Any]:
    bot.trading_enabled = False
    return {
        "trading_enabled": bool(getattr(bot, "trading_enabled", True)),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/trading/enable")
async def trading_enable() -> Dict[str, Any]:
    bot.trading_enabled = True
    return {
        "trading_enabled": bool(getattr(bot, "trading_enabled", True)),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/force_reconcile")
async def force_reconcile() -> Dict[str, Any]:
    min_interval = float(getattr(config, "FORCE_RECONCILE_MIN_INTERVAL_SECONDS", 30.0) or 30.0)
    result = await bot.force_reconcile(min_interval_seconds=min_interval, ignore_running=True)
    message = "✅ Reconciliación disparada" if result.get("started") else "⚠️ Reconciliación no iniciada"
    return {
        "message": message,
        "result": result,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/snapshot")
async def get_snapshot(
    db: Session = Depends(get_db),
    symbols: Optional[str] = None,
    ta_limit: int = 200,
) -> Dict[str, Any]:
    started = time.time()
    status = await get_status()
    health = await health_check()

    if symbols:
        symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        symbol_list = list(status.get("symbols") or [])
    if not symbol_list:
        symbol_list = [s.strip().upper() for s in str(config.SYMBOL or "").split(",") if s.strip()]

    by_symbol = status.get("by_symbol") if isinstance(status, dict) else {}
    strong_score_threshold = float(os.getenv("NERTZ_STRONG_SIGNAL_SCORE", "0.75") or 0.75)
    strong_count = 0
    active_positions = 0
    rows: List[Dict[str, Any]] = []

    for sym in symbol_list:
        try:
            sig = await get_signals(sym, db)
        except Exception:
            sig = {"symbol": sym}
        try:
            ta = get_technical_analysis(sym, db, limit=int(ta_limit))
        except Exception:
            ta = {}

        scores = (sig.get("scores") or {}) if isinstance(sig, dict) else {}
        try:
            buy_score = float(scores.get("buy_score") or 0.0)
        except Exception:
            buy_score = 0.0
        try:
            sell_score = float(scores.get("sell_score") or 0.0)
        except Exception:
            sell_score = 0.0
        strong = max(buy_score, sell_score) >= strong_score_threshold
        if strong:
            strong_count += 1

        ops = (by_symbol.get(sym) or {}) if isinstance(by_symbol, dict) else {}
        act = int(float(ops.get("active_positions") or 0))
        active_positions += act
        open_orders = (ops.get("exchange_open_orders") or {}) if isinstance(ops, dict) else {}

        rows.append(
            {
                "symbol": sym,
                "signal": {
                    "decision": sig.get("decision") if isinstance(sig, dict) else None,
                    "scores": {"buy_score": buy_score, "sell_score": sell_score},
                    "strong": bool(strong),
                    "metrics": sig.get("metrics") if isinstance(sig, dict) else {},
                },
                "market": sig.get("market") if isinstance(sig, dict) else {},
                "ta": ta if isinstance(ta, dict) else {},
                "ops": {
                    "active_positions": act,
                    "exchange_open_orders": int(float(open_orders.get("count") or 0)) if isinstance(open_orders, dict) else 0,
                },
            }
        )

    return {
        "health": health,
        "status": status,
        "bot": {
            "running": bool(status.get("running")) if isinstance(status, dict) else False,
            "paused": bool(status.get("paused")) if isinstance(status, dict) else False,
            "iterations": int(status.get("iterations") or 0) if isinstance(status, dict) else 0,
            "cycles": int(status.get("cycles") or 0) if isinstance(status, dict) else 0,
        },
        "summary": {
            "strong_signals": int(strong_count),
            "active_positions": int(active_positions),
            "elapsed_ms": int((time.time() - started) * 1000),
        },
        "symbols": rows,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/status")
async def get_status() -> Dict[str, Any]:
    now_ts = time.time()
    bot._load_positions()
    snapshot = await bot._get_wallet_snapshot(max_age_seconds=float(getattr(config, "WALLET_CACHE_TTL_SECONDS", 5.0) or 5.0))
    coins = snapshot.get("coins") or {}

    coins_to_show = {"USDT"}
    for sym in bot.symbols:
        base, _quote = bot._split_symbol_base_quote(sym)
        if base:
            coins_to_show.add(str(base).upper())

    wallet_view: Dict[str, Any] = {}
    for coin in sorted(coins_to_show):
        info = coins.get(coin) or {}
        if not info:
            continue
        wallet_view[coin] = {
            "walletBalance": float(info.get("walletBalance") or 0.0),
            "locked": float(info.get("locked") or 0.0),
            "availableToWithdraw": info.get("availableToWithdraw"),
            "available": float(info.get("available") or 0.0),
        }

    by_symbol: Dict[str, Any] = {}
    for sym in bot.symbols:
        open_orders = await bot._get_exchange_open_orders_count(
            sym,
            max_age_seconds=float(getattr(config, "OPEN_ORDERS_CACHE_TTL_SECONDS", 5.0) or 5.0),
        )
        active = 0
        for p in (bot.positions.get(sym) or []):
            if (p or {}).get("status") in ["pending", "open", "closing"]:
                active += 1
        by_symbol[sym] = {
            "last_price": float((bot.ticker_data.get(sym, {}) or {}).get("last_price") or 0.0),
            "age_ticker_s": float(now_ts - float(bot.last_ticker_update.get(sym, 0.0) or 0.0)),
            "age_orderbook_s": float(now_ts - float(bot.last_orderbook_update.get(sym, 0.0) or 0.0)),
            "active_positions": active,
            "exchange_open_orders": {
                "retCode": open_orders.get("retCode"),
                "retMsg": open_orders.get("retMsg"),
                "count": int(open_orders.get("count") or 0),
            },
        }

    return {
        "running": bool(bot.running),
        "paused": bool(bot.paused),
        "trading_enabled": bool(getattr(bot, "trading_enabled", True)),
        "iterations": int(bot.iterations),
        "cycles": int(getattr(bot, "cycle_counter", 0) or 0),
        "symbols": bot.symbols,
        "wallet": {
            "retCode": snapshot.get("retCode"),
            "retMsg": snapshot.get("retMsg"),
            "accountType": snapshot.get("accountType"),
            "coins": wallet_view,
        },
        "by_symbol": by_symbol,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/check_reset")
async def check_reset(db: Session = Depends(get_db)):
    results_file = os.path.join(os.path.dirname(__file__), '..', 'logs', 'results.json')
    try:
        with open(results_file, "r", encoding="utf-8") as f:
            results_data = json.load(f)
    except FileNotFoundError:
        results_data = {"metadata": {"total_trades": 0}, "summary": {"total_profit": 0.0}}

    trades = db.query(Trade).all()
    positions = db.query(Position).all()
    results_reset = results_data["metadata"]["total_trades"] == 0 and results_data["summary"]["total_profit"] == 0.0
    trades_reset = len(trades) == 0 and len(positions) == 0
    return {"results_reset": results_reset, "trades_reset": trades_reset}


@app.get("/health")
async def health_check() -> Dict[str, Any]:
    api_only = run_mode in {"api", "api_only", "api-only"}
    status = "healthy" if api_only else ("healthy" if bot.running and not bot.paused else "unhealthy")
    return {
        "status": status,
        "run_mode": run_mode,
        "bot_running": bool(bot.running),
        "bot_paused": bool(bot.paused),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


api_port = int(os.getenv("NERTZ_API_PORT", "8084"))
server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=api_port))