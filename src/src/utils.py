import hmac
import hashlib
import time
import json
import logging
import os
import math
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Dict, Tuple, Any, Optional, Callable

import numpy as np

logger = logging.getLogger("NertzMetalEngine")
_RESULTS_JSON_LOCK = threading.Lock()

# NOTA: Este ajuste es previo al análisis anterior (trades 1-68 y trades 77-90) y corrige problemas como el sesgo en 'egm' y la ineficiencia de operaciones cortas, optimizando para tiempo real.

@dataclass(frozen=True)
class _TSMToken:
    t: str
    v: Any


class _TSMFormulaError(Exception):
    pass


def _tsm_is_valid_number(x: Any) -> bool:
    if x is None:
        return False
    try:
        v = float(x)
    except Exception:
        return False
    return math.isfinite(v)


def _tsm_to_number(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
    except Exception:
        return None
    if not math.isfinite(v):
        return None
    return v


def _tsm_round_half_away_from_zero(x: float) -> float:
    if x >= 0:
        return float(math.floor(x + 0.5))
    return float(-math.floor(abs(x) + 0.5))


_TSM_NUMBER_RE = re.compile(r"(?:(?:\d+\.\d+)|(?:\d+\.?)|(?:\.\d+))(?:[eE][+-]?\d+)?")
_TSM_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_:\.]*")


def _tsm_tokenize(s: str) -> List[_TSMToken]:
    if not isinstance(s, str):
        raise _TSMFormulaError("formula_not_string")
    i = 0
    n = len(s)
    out: List[_TSMToken] = []
    while i < n:
        ch = s[i]
        if ch.isspace():
            i += 1
            continue
        if ch in "+-*/":
            out.append(_TSMToken("op", ch))
            i += 1
            continue
        if ch == "(":
            out.append(_TSMToken("lparen", ch))
            i += 1
            continue
        if ch == ")":
            out.append(_TSMToken("rparen", ch))
            i += 1
            continue
        if ch == ",":
            out.append(_TSMToken("comma", ch))
            i += 1
            continue
        mnum = _TSM_NUMBER_RE.match(s, i)
        if mnum:
            out.append(_TSMToken("num", float(mnum.group(0))))
            i = mnum.end()
            continue
        mid = _TSM_IDENT_RE.match(s, i)
        if mid:
            out.append(_TSMToken("ident", mid.group(0)))
            i = mid.end()
            continue
        raise _TSMFormulaError(f"unexpected_char:{ch}")
    out.append(_TSMToken("eof", None))
    return out


@dataclass(frozen=True)
class _TSMAst:
    k: str
    v: Any = None
    a: Any = None
    b: Any = None


class _TSMParser:
    def __init__(self, tokens: List[_TSMToken]):
        self._toks = tokens
        self._i = 0

    def _peek(self) -> _TSMToken:
        return self._toks[self._i]

    def _pop(self) -> _TSMToken:
        t = self._toks[self._i]
        self._i += 1
        return t

    def _expect(self, ttype: str) -> _TSMToken:
        t = self._peek()
        if t.t != ttype:
            raise _TSMFormulaError(f"expected:{ttype}")
        return self._pop()

    def parse(self) -> _TSMAst:
        expr = self._expr(0)
        if self._peek().t != "eof":
            raise _TSMFormulaError("trailing_tokens")
        return expr

    def _lbp(self, tok: _TSMToken) -> int:
        if tok.t != "op":
            return 0
        if tok.v in ("+", "-"):
            return 10
        if tok.v in ("*", "/"):
            return 20
        return 0

    def _expr(self, rbp: int) -> _TSMAst:
        t = self._pop()
        left = self._nud(t)
        while rbp < self._lbp(self._peek()):
            t2 = self._pop()
            left = self._led(t2, left)
        return left

    def _nud(self, tok: _TSMToken) -> _TSMAst:
        if tok.t == "num":
            return _TSMAst("num", tok.v)
        if tok.t == "ident":
            name = str(tok.v)
            if self._peek().t == "lparen":
                self._pop()
                args: List[_TSMAst] = []
                if self._peek().t != "rparen":
                    while True:
                        args.append(self._expr(0))
                        if self._peek().t == "comma":
                            self._pop()
                            continue
                        break
                self._expect("rparen")
                return _TSMAst("call", name, args)
            return _TSMAst("var", name)
        if tok.t == "op" and tok.v == "-":
            right = self._expr(30)
            return _TSMAst("neg", None, right)
        if tok.t == "lparen":
            e = self._expr(0)
            self._expect("rparen")
            return e
        raise _TSMFormulaError("unexpected_token")

    def _led(self, tok: _TSMToken, left: _TSMAst) -> _TSMAst:
        if tok.t != "op":
            raise _TSMFormulaError("unexpected_led")
        op = str(tok.v)
        rbp = 10 if op in ("+", "-") else 20
        right = self._expr(rbp)
        return _TSMAst("bin", op, left, right)


def compile_tsm_formula(formula: str) -> _TSMAst:
    toks = _tsm_tokenize(formula)
    return _TSMParser(toks).parse()


def _tsm_collect(ast: _TSMAst) -> Tuple[List[str], List[str]]:
    vars_used: List[str] = []
    funcs_used: List[str] = []

    def _walk(n: _TSMAst) -> None:
        if n.k == "var":
            vars_used.append(str(n.v))
            return
        if n.k == "call":
            funcs_used.append(str(n.v).lower())
            for x in (n.a or []):
                _walk(x)
            return
        if n.k in {"neg"}:
            _walk(n.a)
            return
        if n.k == "bin":
            _walk(n.a)
            _walk(n.b)
            return

    _walk(ast)
    return sorted(set(vars_used)), sorted(set(funcs_used))


def tsm_formula_features(formula: str) -> Dict[str, Any]:
    ast = compile_tsm_formula(formula)
    vars_used, funcs_used = _tsm_collect(ast)
    return {"variables": vars_used, "functions": funcs_used}


def eval_tsm_formula(
    formula: str,
    context: Dict[str, Any],
    *,
    functions: Optional[Dict[str, Callable[..., Any]]] = None,
) -> Optional[float]:
    ast = compile_tsm_formula(formula)
    return eval_tsm_ast(ast, context, functions=functions)


def eval_tsm_ast(
    ast: _TSMAst,
    context: Dict[str, Any],
    *,
    functions: Optional[Dict[str, Callable[..., Any]]] = None,
) -> Optional[float]:
    ctx = context if isinstance(context, dict) else {}
    fn = functions if isinstance(functions, dict) else {}

    def _fn(name: str) -> Optional[Callable[..., Any]]:
        if name in fn:
            return fn[name]
        key = name.lower()
        if key in fn:
            return fn[key]
        return None

    def _as_args(xs: List[_TSMAst]) -> List[Optional[float]]:
        return [ _eval(x) for x in xs ]

    def _minmax(xs: List[Optional[float]], is_min: bool) -> Optional[float]:
        vals = [x for x in xs if _tsm_is_valid_number(x)]
        if not vals:
            return None
        return float(min(vals) if is_min else max(vals))

    def _first(xs: List[Optional[float]]) -> Optional[float]:
        for x in xs:
            if _tsm_is_valid_number(x):
                return float(x)
        return None

    def _avg(xs: List[Optional[float]]) -> Optional[float]:
        vals = [float(x) for x in xs if _tsm_is_valid_number(x)]
        if not vals:
            return None
        return float(sum(vals) / float(len(vals)))

    def _round_std(x: Optional[float], y: Optional[float]) -> Optional[float]:
        if not _tsm_is_valid_number(x):
            return None
        xv = float(x)
        if not _tsm_is_valid_number(y):
            return float(_tsm_round_half_away_from_zero(xv))
        yv = float(y)
        if yv == 0:
            return None
        scaled = xv / yv
        return float(_tsm_round_half_away_from_zero(scaled) * yv)

    def _round_updown(x: Optional[float], y: Optional[float], up: bool) -> Optional[float]:
        if not _tsm_is_valid_number(x):
            return None
        xv = float(x)
        if not _tsm_is_valid_number(y):
            return float(math.ceil(xv) if up else math.floor(xv))
        yv = float(y)
        if yv == 0:
            return None
        scaled = xv / yv
        return float((math.ceil(scaled) if up else math.floor(scaled)) * yv)

    def _ifcmp(a: Optional[float], b: Optional[float], x: Optional[float], y: Optional[float], op: str) -> Optional[float]:
        if not _tsm_is_valid_number(a) or not _tsm_is_valid_number(b):
            return y if _tsm_is_valid_number(y) else None
        av = float(a)
        bv = float(b)
        ok = False
        if op == "gt":
            ok = av > bv
        elif op == "gte":
            ok = av >= bv
        elif op == "lt":
            ok = av < bv
        elif op == "lte":
            ok = av <= bv
        elif op == "eq":
            ok = av == bv
        return x if ok else y

    def _check(a: Optional[float], b: Optional[float], c: Optional[float]) -> Optional[float]:
        if _tsm_is_valid_number(a) and float(a) > 0:
            return b if _tsm_is_valid_number(b) else None
        return c if _tsm_is_valid_number(c) else None

    def _convert(p: Optional[float]) -> Optional[float]:
        if not _tsm_is_valid_number(p):
            return None
        conv = ctx.get("_convert")
        if callable(conv):
            try:
                return _tsm_to_number(conv(float(p)))
            except Exception:
                return float(p)
        return float(p)

    def _eval(n: _TSMAst) -> Optional[float]:
        if n.k == "num":
            return _tsm_to_number(n.v)
        if n.k == "var":
            if str(n.v) in ctx:
                return _tsm_to_number(ctx.get(str(n.v)))
            return None
        if n.k == "neg":
            x = _eval(n.a)
            return None if not _tsm_is_valid_number(x) else float(-float(x))
        if n.k == "bin":
            op = str(n.v)
            left = _eval(n.a)
            right = _eval(n.b)
            if not _tsm_is_valid_number(left) or not _tsm_is_valid_number(right):
                return None
            a = float(left)
            b = float(right)
            if op == "+":
                return a + b
            if op == "-":
                return a - b
            if op == "*":
                return a * b
            if op == "/":
                if b == 0:
                    return None
                return a / b
            return None
        if n.k == "call":
            name = str(n.v)
            f = _fn(name)
            if f is not None:
                try:
                    return _tsm_to_number(f(*_as_args(n.a or [])))
                except Exception:
                    return None
            lname = name.lower()
            args = _as_args(n.a or [])
            if lname == "min":
                return _minmax(args, True)
            if lname == "max":
                return _minmax(args, False)
            if lname == "first":
                return _first(args)
            if lname == "avg":
                return _avg(args)
            if lname == "check":
                a = args[0] if len(args) > 0 else None
                b = args[1] if len(args) > 1 else None
                c = args[2] if len(args) > 2 else None
                return _check(a, b, c)
            if lname in {"ifgt", "ifgte", "iflt", "iflte", "ifeq"}:
                a = args[0] if len(args) > 0 else None
                b = args[1] if len(args) > 1 else None
                x = args[2] if len(args) > 2 else None
                y = args[3] if len(args) > 3 else None
                op = lname.replace("if", "")
                return _ifcmp(a, b, x, y, op)
            if lname == "round":
                x = args[0] if len(args) > 0 else None
                y = args[1] if len(args) > 1 else None
                return _round_std(x, y)
            if lname == "roundup":
                x = args[0] if len(args) > 0 else None
                y = args[1] if len(args) > 1 else None
                return _round_updown(x, y, True)
            if lname == "rounddown":
                x = args[0] if len(args) > 0 else None
                y = args[1] if len(args) > 1 else None
                return _round_updown(x, y, False)
            if lname == "convert":
                p = args[0] if len(args) > 0 else None
                return _convert(p)
            return None
        return None

    return _eval(ast)


def eval_tsm_formulas(formulas: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, float]:
    if not isinstance(formulas, dict):
        return {}
    out: Dict[str, float] = {}
    for k, v in formulas.items():
        if not isinstance(k, str) or not k.strip():
            continue
        if not isinstance(v, str) or not v.strip():
            continue
        try:
            res = eval_tsm_formula(v, context)
        except Exception:
            res = None
        if _tsm_is_valid_number(res):
            out[k] = float(res)
    return out

def _safe_float(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
    except Exception:
        return float(default)
    return v if math.isfinite(v) else float(default)


def _parse_book_side(rows: Any, limit: int) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    if not isinstance(rows, list):
        return out
    for r in rows[: max(1, int(limit))]:
        if not isinstance(r, (list, tuple)) or len(r) < 2:
            continue
        p = _safe_float(r[0], 0.0)
        q = _safe_float(r[1], 0.0)
        if p > 0 and q > 0:
            out.append((p, q))
    return out


def _pivot_levels_from_candles(
    candle_data: List[Dict[str, float]],
    *,
    lookback: int,
    window: int,
    tol_pct: float,
    max_levels: int,
) -> Tuple[List[Dict[str, float]], List[Dict[str, float]]]:
    if not candle_data:
        return [], []
    series = list(reversed(candle_data[: max(1, int(lookback))]))
    highs = [ _safe_float(c.get("high"), 0.0) for c in series ]
    lows = [ _safe_float(c.get("low"), 0.0) for c in series ]
    vols = [ _safe_float(c.get("volume"), 0.0) for c in series ]
    n = len(series)
    w = max(1, int(window))
    sup_raw: List[Tuple[float, float]] = []
    res_raw: List[Tuple[float, float]] = []
    for i in range(w, n - w):
        lo = lows[i]
        hi = highs[i]
        if lo > 0 and lo == min(lows[i - w : i + w + 1]):
            sup_raw.append((lo, vols[i]))
        if hi > 0 and hi == max(highs[i - w : i + w + 1]):
            res_raw.append((hi, vols[i]))

    def _cluster(xs: List[Tuple[float, float]], side: str) -> List[Dict[str, float]]:
        if not xs:
            return []
        xs_sorted = sorted(xs, key=lambda t: t[0])
        clusters: List[Dict[str, float]] = []
        for price, weight in xs_sorted:
            if price <= 0:
                continue
            if not clusters:
                clusters.append({"price": float(price), "strength": float(weight), "touches": 1.0})
                continue
            last = clusters[-1]
            base = float(last["price"]) if float(last["price"]) > 0 else float(price)
            if abs(price - base) / base <= float(tol_pct):
                s = float(last["strength"]) + float(weight)
                t = float(last["touches"]) + 1.0
                last["price"] = float((float(last["price"]) * float(last["touches"]) + float(price)) / t)
                last["strength"] = s
                last["touches"] = t
            else:
                clusters.append({"price": float(price), "strength": float(weight), "touches": 1.0})
        clusters_sorted = sorted(clusters, key=lambda d: float(d.get("strength", 0.0)), reverse=True)
        keep = clusters_sorted[: max(1, int(max_levels))]
        if side == "support":
            keep = sorted(keep, key=lambda d: float(d["price"]), reverse=True)
        else:
            keep = sorted(keep, key=lambda d: float(d["price"]))
        return keep

    return _cluster(sup_raw, "support"), _cluster(res_raw, "resistance")


def _orderbook_walls(
    orderbook_data: Dict[str, Any],
    *,
    last_price: float,
    band_pct: float,
    depth: int,
    max_levels: int,
) -> Tuple[List[Dict[str, float]], List[Dict[str, float]]]:
    bids = _parse_book_side((orderbook_data or {}).get("bids"), depth)
    asks = _parse_book_side((orderbook_data or {}).get("asks"), depth)
    if last_price <= 0:
        last_price = bids[0][0] if bids else (asks[0][0] if asks else 0.0)
    if last_price <= 0:
        return [], []
    band = max(0.0, float(band_pct))
    lo = last_price * (1.0 - band)
    hi = last_price * (1.0 + band)
    bids_band = [b for b in bids if lo <= b[0] <= hi]
    asks_band = [a for a in asks if lo <= a[0] <= hi]
    top_bids = sorted(bids_band or bids, key=lambda t: t[1], reverse=True)[: max(1, int(max_levels))]
    top_asks = sorted(asks_band or asks, key=lambda t: t[1], reverse=True)[: max(1, int(max_levels))]
    supports = [{"price": float(p), "qty": float(q), "notional": float(p * q)} for p, q in top_bids]
    resistances = [{"price": float(p), "qty": float(q), "notional": float(p * q)} for p, q in top_asks]
    supports = sorted(supports, key=lambda d: float(d["price"]), reverse=True)
    resistances = sorted(resistances, key=lambda d: float(d["price"]))
    return supports, resistances


def calculate_discovery_metrics(
    candle_data: List[Dict[str, float]],
    orderbook_data: Dict[str, Any],
    ticker_data: Dict[str, Any],
    recent_trades: Optional[List[Dict[str, Any]]] = None,
    *,
    candles_n: int = 5,
    book_levels_n: int = 10,
    pe_band_pct: float = 0.001,
    ar_trades_n: int = 10,
    sr_lookback: int = 200,
    sr_window: int = 2,
    sr_tol_pct: float = 0.0015,
    sr_levels_n: int = 6,
) -> Dict[str, Any]:
    last_price = _safe_float((ticker_data or {}).get("last_price"), 0.0)
    candles_used = candle_data[: max(1, int(candles_n))] if isinstance(candle_data, list) else []
    if not last_price and candles_used:
        last_price = _safe_float(candles_used[0].get("close"), 0.0)

    vols = [ _safe_float(c.get("volume"), 0.0) for c in candles_used ]
    cv = float(sum(vols) / float(len(vols))) if vols else 0.0
    ranges = [ max(0.0, _safe_float(c.get("high"), 0.0) - _safe_float(c.get("low"), 0.0)) for c in candles_used ]
    avg_range = float(sum(ranges) / float(len(ranges))) if ranges else 0.0
    cvo = float(avg_range / last_price) if last_price > 0 else 0.0

    bids = _parse_book_side((orderbook_data or {}).get("bids"), book_levels_n)
    asks = _parse_book_side((orderbook_data or {}).get("asks"), book_levels_n)
    cp = float(sum(q for _, q in bids) + sum(q for _, q in asks))

    ild = float((cv * 0.4 + cp * 0.4 + cvo * 0.2) * 100.0)

    band = max(0.0, float(pe_band_pct))
    pe = 0.0
    if last_price > 0 and (bids or asks):
        lo = last_price * (1.0 - band)
        hi = last_price * (1.0 + band)
        pe = float(sum(q for p, q in bids if lo <= p <= hi) + sum(q for p, q in asks if lo <= p <= hi))

    ar = 0.0
    rt = recent_trades if isinstance(recent_trades, list) else []
    if rt:
        qtys: List[float] = []
        for t in rt[-max(1, int(ar_trades_n)) :]:
            if not isinstance(t, dict):
                continue
            qtys.append(_safe_float(t.get("qty") or t.get("size") or t.get("q"), 0.0))
        ar = float(sum(qtys))

    vr = float(cv)
    rol = float((vr * 0.3 + pe * 0.4 + ar * 0.3) * 100.0)
    if last_price > 0:
        if float(avg_range / last_price) > 0.005:
            rol *= 0.8

    rop_min = None
    rop_max = None
    if last_price > 0 and pe > 0 and (bids or asks):
        lo = last_price * (1.0 - band)
        hi = last_price * (1.0 + band)
        levels = [ (p, q) for p, q in (bids + asks) if lo <= p <= hi ]
        if levels:
            levels_sorted = sorted(levels, key=lambda t: abs(t[0] - last_price))
            acc = 0.0
            thr = pe * 0.5
            for p, q in levels_sorted:
                acc += q
                if rop_min is None or p < rop_min:
                    rop_min = float(p)
                if rop_max is None or p > rop_max:
                    rop_max = float(p)
                if acc >= thr:
                    break

    pivot_supports, pivot_resistances = _pivot_levels_from_candles(
        candle_data,
        lookback=int(sr_lookback),
        window=int(sr_window),
        tol_pct=float(sr_tol_pct),
        max_levels=int(sr_levels_n),
    )
    wall_supports, wall_resistances = _orderbook_walls(
        orderbook_data,
        last_price=float(last_price),
        band_pct=float(max(0.005, band)),
        depth=max(20, int(book_levels_n) * 5),
        max_levels=int(sr_levels_n),
    )

    def _merge_levels(a: List[Dict[str, float]], b: List[Dict[str, float]], tol: float) -> List[Dict[str, float]]:
        out: List[Dict[str, float]] = []
        for src in (a or []):
            if not isinstance(src, dict) or _safe_float(src.get("price"), 0.0) <= 0:
                continue
            out.append(dict(src))
        for src in (b or []):
            p = _safe_float((src or {}).get("price"), 0.0)
            if p <= 0:
                continue
            merged = False
            for d in out:
                base = _safe_float(d.get("price"), 0.0)
                if base > 0 and abs(p - base) / base <= tol:
                    d["price"] = float((base + p) / 2.0)
                    d["strength"] = float(_safe_float(d.get("strength"), 0.0) + _safe_float(src.get("qty") or src.get("notional") or src.get("strength"), 0.0))
                    d["touches"] = float(_safe_float(d.get("touches"), 1.0) + 1.0)
                    merged = True
                    break
            if not merged:
                out.append(
                    {
                        "price": float(p),
                        "strength": float(_safe_float(src.get("qty") or src.get("notional") or src.get("strength"), 0.0)),
                        "touches": float(_safe_float(src.get("touches"), 1.0)),
                    }
                )
        return out

    supports_all = _merge_levels(pivot_supports, wall_supports, float(sr_tol_pct))
    resistances_all = _merge_levels(pivot_resistances, wall_resistances, float(sr_tol_pct))
    supports_all = sorted(supports_all, key=lambda d: float(d.get("price", 0.0)), reverse=True)[: max(1, int(sr_levels_n))]
    resistances_all = sorted(resistances_all, key=lambda d: float(d.get("price", 0.0)))[: max(1, int(sr_levels_n))]

    nearest_support = None
    nearest_resistance = None
    if last_price > 0:
        below = [d for d in supports_all if float(d.get("price", 0.0)) < last_price]
        above = [d for d in resistances_all if float(d.get("price", 0.0)) > last_price]
        if below:
            nearest_support = float(max(below, key=lambda d: float(d["price"]))["price"])
        if above:
            nearest_resistance = float(min(above, key=lambda d: float(d["price"]))["price"])

    support_dist_pct = float((last_price - nearest_support) / last_price) if last_price > 0 and nearest_support else None
    resistance_dist_pct = float((nearest_resistance - last_price) / last_price) if last_price > 0 and nearest_resistance else None

    return {
        "combined": {
            "last_price": float(last_price),
            "candles_n": int(len(candles_used)),
            "cv": float(cv),
            "cp": float(cp),
            "cvo": float(cvo),
            "vr": float(vr),
            "pe": float(pe),
            "ar": float(ar),
            "rop_min": float(rop_min) if rop_min is not None else None,
            "rop_max": float(rop_max) if rop_max is not None else None,
        },
        "ild": float(ild),
        "rol": float(rol),
        "supports": supports_all,
        "resistances": resistances_all,
        "nearest_support": float(nearest_support) if nearest_support is not None else None,
        "nearest_resistance": float(nearest_resistance) if nearest_resistance is not None else None,
        "support_dist_pct": float(support_dist_pct) if support_dist_pct is not None else None,
        "resistance_dist_pct": float(resistance_dist_pct) if resistance_dist_pct is not None else None,
    }

def calculate_metrics(candle_data: List[Dict[str, float]], orderbook_data: Dict[str, List[List[str]]], ticker_data: Dict[str, float], depth: int = 5) -> Dict[str, float]:
    if not all([candle_data, len(candle_data) >= 2, orderbook_data.get("bids"), orderbook_data.get("asks"), ticker_data.get('last_price')]):
        logger.warning("Datos insuficientes para métricas, devolviendo valores por defecto")
        return {"combined": 0.0, "ild": 0.0, "egm": 0.0, "rol": 0.0, "pio": 0.0, "ogm": 0.0, "volatility": 0.0}

    try:
        last_price: float = float(ticker_data["last_price"])

        highs: np.ndarray = np.array(
            [float(c.get("high", 0)) for c in candle_data[:20] if c.get("high") is not None],
            dtype=np.float64,
        )
        lows: np.ndarray = np.array(
            [float(c.get("low", 0)) for c in candle_data[:20] if c.get("low") is not None],
            dtype=np.float64,
        )
        if len(highs) == 0 or len(lows) == 0 or last_price <= 0:
            return {"combined": 0.0, "ild": 0.0, "egm": 0.0, "rol": 0.0, "pio": 0.0, "ogm": 0.0, "volatility": 0.0}

        volatility: float = float((highs.max() - lows.min()) / last_price)

        depth_i = int(depth) if isinstance(depth, int) and depth > 0 else 50
        bids_raw = orderbook_data.get("bids") or []
        asks_raw = orderbook_data.get("asks") or []
        bids_in: List[Tuple[float, float]] = []
        asks_in: List[Tuple[float, float]] = []
        for row in bids_raw[:depth_i]:
            try:
                p = float(row[0])
                q = float(row[1])
                if p > 0 and q > 0:
                    bids_in.append((p, q))
            except Exception:
                continue
        for row in asks_raw[:depth_i]:
            try:
                p = float(row[0])
                q = float(row[1])
                if p > 0 and q > 0:
                    asks_in.append((p, q))
            except Exception:
                continue

        if not bids_in or not asks_in:
            return {"combined": 0.0, "ild": 0.0, "egm": 0.0, "rol": 0.0, "pio": 0.0, "ogm": 0.0, "volatility": float(volatility)}

        best_bid = float(max(p for p, _ in bids_in))
        best_ask = float(min(p for p, _ in asks_in))
        if best_bid <= 0 or best_ask <= 0:
            return {"combined": 0.0, "ild": 0.0, "egm": 0.0, "rol": 0.0, "pio": 0.0, "ogm": 0.0, "volatility": float(volatility)}

        mid = (best_bid + best_ask) / 2.0
        if mid <= 0:
            return {"combined": 0.0, "ild": 0.0, "egm": 0.0, "rol": 0.0, "pio": 0.0, "ogm": 0.0, "volatility": float(volatility)}

        lambda_ = float(ticker_data.get("orderbook_lambda", 0.03) or 0.03)
        pct_band = float(ticker_data.get("orderbook_pct_band", 0.015) or 0.015)
        target_move = float(ticker_data.get("ild_target_move", 0.002) or 0.002)

        bids: List[Tuple[float, float]] = []
        asks: List[Tuple[float, float]] = []
        for p, q in bids_in:
            if abs(p - mid) / mid <= pct_band:
                bids.append((p, q))
        for p, q in asks_in:
            if abs(p - mid) / mid <= pct_band:
                asks.append((p, q))

        if not bids or not asks:
            bids = bids_in
            asks = asks_in

        bid_w_sum = 0.0
        ask_w_sum = 0.0
        for p, q in bids:
            dist = max(0.0, mid - p)
            bid_w_sum += q * float(np.exp(-lambda_ * dist))
        for p, q in asks:
            dist = max(0.0, p - mid)
            ask_w_sum += q * float(np.exp(-lambda_ * dist))

        pio_raw = bid_w_sum - ask_w_sum
        weighted_liquidity = bid_w_sum + ask_w_sum
        asymmetry = (bid_w_sum - ask_w_sum) / (weighted_liquidity + 1e-12)

        up_target = mid * (1.0 + target_move)
        down_target = mid * (1.0 - target_move)

        asks_sorted = sorted(asks, key=lambda x: x[0])
        bids_sorted = sorted(bids, key=lambda x: x[0], reverse=True)

        up_notional = 0.0
        for p, q in asks_sorted:
            up_notional += p * q
            if p >= up_target:
                break

        down_notional = 0.0
        for p, q in bids_sorted:
            down_notional += p * q
            if p <= down_target:
                break

        ild_raw = (up_notional + down_notional) / 2.0

        def _gap_stats(levels: List[Tuple[float, float]], ascending: bool) -> Tuple[float, float]:
            if len(levels) < 3:
                return 0.0, 0.0
            levels_sorted = sorted(levels, key=lambda x: x[0], reverse=not ascending)
            prices = np.array([p for p, _ in levels_sorted], dtype=np.float64)
            qtys = np.array([q for _, q in levels_sorted], dtype=np.float64)
            if len(prices) < 3:
                return 0.0, 0.0
            gaps = np.abs(np.diff(prices))
            if len(gaps) == 0:
                return 0.0, 0.0
            med_gap = float(np.median(gaps))
            q_thr = float(np.quantile(qtys, 0.9)) if len(qtys) > 0 else 0.0
            large_idx = np.where(qtys[:-1] >= q_thr)[0]
            if len(large_idx) == 0:
                return med_gap, med_gap
            large_gaps = gaps[large_idx]
            return med_gap, float(np.mean(large_gaps)) if len(large_gaps) else med_gap

        ask_med_gap, ask_large_gap = _gap_stats(asks, ascending=True)
        bid_med_gap, bid_large_gap = _gap_stats(bids, ascending=False)
        ogm_raw = (ask_large_gap - ask_med_gap) - (bid_large_gap - bid_med_gap)

        prev_weighted_liq = ticker_data.get("prev_weighted_liquidity")
        dt_s = ticker_data.get("rol_dt_s")
        rol_raw = 0.0
        try:
            if prev_weighted_liq is not None and dt_s is not None:
                dt_s_f = float(dt_s)
                prev_liq_f = float(prev_weighted_liq)
                if dt_s_f > 0:
                    rol_raw = (weighted_liquidity - prev_liq_f) / dt_s_f
        except Exception:
            rol_raw = 0.0

        history = ticker_data.get("metric_history") or []
        if not isinstance(history, list):
            history = []

        def _z(current: float, key: str) -> float:
            xs: List[float] = []
            for h in history:
                if not isinstance(h, dict):
                    continue
                v = h.get(key)
                if v is None:
                    continue
                try:
                    xs.append(float(v))
                except Exception:
                    continue
            xs.append(float(current))
            if len(xs) < 5:
                return 0.0
            arr = np.array(xs, dtype=np.float64)
            mu = float(np.mean(arr))
            sd = float(np.std(arr))
            if sd <= 1e-12:
                return 0.0
            return (float(current) - mu) / sd

        pio_z = _z(pio_raw, "pio")
        ild_z = _z(ild_raw, "ild")
        rol_z = _z(rol_raw, "rol")
        ogm_z = _z(ogm_raw, "ogm")

        bonus = 0.0
        if abs(rol_z) >= 1.5 and abs(pio_z) > 0:
            bonus = float(np.sign(pio_z)) * min(1.5, abs(rol_z) - 1.5)
        egm_raw = (pio_z * (1.0 + abs(asymmetry))) + bonus
        egm_z = _z(egm_raw, "egm")

        combined_z = (
            0.45 * pio_z
            + 0.30 * egm_z
            - 0.15 * ild_z
            + 0.10 * rol_z
            + 0.05 * ogm_z
        )
        combined = float(combined_z * 10.0)

        formulas = ticker_data.get("formulas") or {}
        if isinstance(formulas, str) and formulas.strip():
            try:
                formulas = json.loads(formulas)
            except Exception:
                formulas = {}
        ctx: Dict[str, Any] = {
            **{
                "last_price": float(last_price),
                "LastPrice": float(last_price),
                "DBMarket": float(last_price),
                "MidPrice": float(mid),
                "Spread": float(best_ask - best_bid),
                "SpreadPct": float((best_ask - best_bid) / mid) if mid > 0 else 0.0,
                "DBMinBuyout": float(best_ask),
                "DBMaxBuyout": float(best_bid),
            },
            "combined": float(combined),
            "ild": float(ild_z),
            "egm": float(egm_z),
            "rol": float(rol_z),
            "pio": float(pio_z),
            "ogm": float(ogm_z),
            "volatility": float(volatility),
            "pio_raw": float(pio_raw),
            "ild_raw": float(ild_raw),
            "egm_raw": float(egm_raw),
            "rol_raw": float(rol_raw),
            "ogm_raw": float(ogm_raw),
            "weighted_liquidity": float(weighted_liquidity),
        }
        try:
            discovery = calculate_discovery_metrics(candle_data, orderbook_data, ticker_data)
            supports = discovery.get("supports") if isinstance(discovery, dict) else None
            resistances = discovery.get("resistances") if isinstance(discovery, dict) else None
            if isinstance(supports, list):
                for i, d in enumerate(supports[:10], start=1):
                    if isinstance(d, dict) and _tsm_is_valid_number(d.get("price")):
                        ctx[f"Support{i}"] = float(d["price"])
            if isinstance(resistances, list):
                for i, d in enumerate(resistances[:10], start=1):
                    if isinstance(d, dict) and _tsm_is_valid_number(d.get("price")):
                        ctx[f"Resistance{i}"] = float(d["price"])
            if isinstance(discovery, dict):
                for k in ("nearest_support", "nearest_resistance", "support_dist_pct", "resistance_dist_pct", "ild", "rol"):
                    v = discovery.get(k)
                    if _tsm_is_valid_number(v):
                        ctx[k] = float(v)
        except Exception:
            pass
        derived = eval_tsm_formulas(formulas, ctx)

        logger.debug(
            f"Métricas: combined={combined:.2f}, ild={ild_z:.4f}, egm={egm_z:.4f}, rol={rol_z:.4f}, pio={pio_z:.4f}, ogm={ogm_z:.4f}, volatility={volatility:.4f}"
        )
        return {
            "combined": float(combined),
            "ild": float(ild_z),
            "egm": float(egm_z),
            "rol": float(rol_z),
            "pio": float(pio_z),
            "ogm": float(ogm_z),
            "volatility": float(volatility),
            "pio_raw": float(pio_raw),
            "ild_raw": float(ild_raw),
            "egm_raw": float(egm_raw),
            "rol_raw": float(rol_raw),
            "ogm_raw": float(ogm_raw),
            "weighted_liquidity": float(weighted_liquidity),
            **derived,
        }
    except Exception as e:
        logger.error(f"Error en calculate_metrics: {e}", exc_info=True)
        return {"combined": 0.0, "ild": 0.0, "egm": 0.0, "rol": 0.0, "pio": 0.0, "ogm": 0.0, "volatility": 0.0}

def calculate_rolling_volatility(prices: List[float], window: int) -> float:
    """
    Calcula la volatilidad móvil de una lista de precios.

    :param prices: Lista de precios.
    :param window: Tamaño de la ventana para el cálculo.
    :return: Volatilidad móvil.
    """
    if len(prices) < window:
        return 0.0
    log_returns = np.log(np.array(prices[-window:]) / np.array(prices[-window - 1:-1]))
    return np.std(log_returns) * np.sqrt(window)

def generate_signature(api_secret: str, prehash: str) -> str:
    """Genera la firma HMAC SHA256 para la API V5 de Bybit."""
    return hmac.new(
        api_secret.encode("utf-8"),
        prehash.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

def save_results(results: dict, log_dir: str = "logs") -> None:
    """
    Guarda los resultados en un archivo JSON en el directorio especificado.

    :param results: Los resultados a guardar.
    :param log_dir: El directorio donde se guardará el archivo.
    """
    log_dir = os.path.abspath(log_dir)
    os.makedirs(log_dir, exist_ok=True)
    filepath = os.path.join(log_dir, "results.json")
    with _RESULTS_JSON_LOCK:
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if isinstance(existing, dict):
                    for key in ("events", "last_metrics", "thresholds", "last_balance"):
                        if key in existing and key not in results:
                            results[key] = existing[key]
            except Exception:
                pass
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=4)
    logger.info(f"📈 Resultados guardados en {filepath}")


def load_results_json(log_dir: str = "logs") -> dict:
    log_dir = os.path.abspath(log_dir)
    filepath = os.path.join(log_dir, "results.json")
    with _RESULTS_JSON_LOCK:
        if not os.path.exists(filepath):
            return {}
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def append_results_event(event: dict, log_dir: str = "logs", max_events: int = 2000) -> None:
    log_dir = os.path.abspath(log_dir)
    os.makedirs(log_dir, exist_ok=True)
    filepath = os.path.join(log_dir, "results.json")

    with _RESULTS_JSON_LOCK:
        payload: dict = {}
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if isinstance(existing, dict):
                    payload = existing
            except Exception:
                payload = {}

        events = payload.get("events")
        if not isinstance(events, list):
            events = []
        if isinstance(event, dict) and "timestamp" not in event:
            event = {**event, "timestamp": datetime.now(timezone.utc).isoformat()}
        events.append(event)
        if max_events > 0 and len(events) > max_events:
            events = events[-max_events:]
        payload["events"] = events

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=4)

def timestamp_to_datetime(timestamp_ms: int) -> datetime:
    """
    Convierte un timestamp en milisegundos a un objeto datetime.

    :param timestamp_ms: El timestamp en milisegundos.
    :return: El objeto datetime correspondiente.
    """
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)


def calculate_tp_sl(price: float, volatility: float, action: str, tp_factor: float = 1.5, sl_factor: float = 1.0) -> Tuple[float, float]:
    """Calcula Take Profit y Stop Loss dinámicos basados en volatilidad."""
    try:
        price_range = volatility * price
        if action.lower() == "buy":
            tp = price + (price_range * tp_factor)
            sl = price - (price_range * sl_factor)
        else:  # sell
            tp = price - (price_range * tp_factor)
            sl = price + (price_range * sl_factor)
        return round(tp, 2), round(sl, 2)
    except Exception as e:
        logger.error(f"❌ Error en calculate_tp_sl: {e}")
        return 0.0, 0.0


# Clase TpslStrategy
class BaseTradingStrategy:
    def __init__(self, connector=None, data_manager=None, **kwargs):
        self.logger = logging.getLogger("NertzMetalEngine")
        self.connector = connector
        self.data_manager = data_manager


def evaluate_trend(short_ema: float, mid_ema: float, long_ema: float) -> Tuple[str, str]:
    """Evalúa la tendencia para decidir la acción de trading."""
    if short_ema > mid_ema > long_ema:
        return "BUY", "Cruzamiento alcista de Triple EMA"
    elif short_ema < mid_ema < long_ema:
        return "SELL", "Cruzamiento bajista de Triple EMA"
    return "HOLD", "No hay confirmación clara"


class TpslStrategy(BaseTradingStrategy):
    def __init__(self, connector=None, data_manager=None, short_window=5, mid_window=10, long_window=20,
                 tp_percentage=1.5, sl_percentage=0.5, combined_buy_threshold=2.0, combined_sell_threshold=-3.0, **kwargs):
        super().__init__(connector, data_manager, **kwargs)
        self.short_window = short_window
        self.mid_window = mid_window
        self.long_window = long_window
        self.tp_percentage = tp_percentage  # En porcentaje (ej. 1.5 = 1.5%)
        self.sl_percentage = sl_percentage  # En porcentaje (ej. 0.75 = 0.75%)
        self.combined_buy_threshold = combined_buy_threshold
        self.combined_sell_threshold = combined_sell_threshold

    def calculate_ema(self, prices: List[float], window: int) -> float:
        """Calcula EMA de manera pura en Python."""
        if len(prices) < window:
            return sum(prices[-window:]) / min(len(prices), window)  # SMA si no hay suficientes datos
        alpha = 2 / (window + 1)
        ema = prices[-window]  # Primer valor como SMA inicial
        for price in prices[-window + 1:]:
            ema = (price * alpha) + (ema * (1 - alpha))
        return ema

    def generate_signal(self, market_data: Dict, metrics: Dict[str, float]) -> Dict:
        """Genera señales basadas en Triple EMA, TP/SL y métricas."""
        if "close_prices" not in market_data or not market_data["close_prices"]:
            self.logger.warning("⚠️ Faltan datos de 'close_prices' en market_data.")
            return {"action": "HOLD", "confidence": 0.0, "take_profit": 0.0, "stop_loss": 0.0, "reason": "Sin datos"}

        closing_prices = market_data["close_prices"]

        if len(closing_prices) < self.long_window:
            self.logger.warning(f"⚠️ No hay suficientes datos (mínimo {self.long_window} precios).")
            return {"action": "HOLD", "confidence": 0.0, "take_profit": 0.0, "stop_loss": 0.0,
                    "reason": "Datos insuficientes"}

        short_ema = self.calculate_ema(closing_prices, self.short_window)
        mid_ema = self.calculate_ema(closing_prices, self.mid_window)
        long_ema = self.calculate_ema(closing_prices, self.long_window)

        latest_price = closing_prices[-1]
        action, reason = evaluate_trend(short_ema, mid_ema, long_ema)

        # Suspender ventas cortas hasta una señal bajista fuerte
        if action == "SELL" and metrics.get("combined", 0.0) > self.combined_sell_threshold:
            action = "HOLD"
            reason = "Venta suspendida por combined insuficiente"

        # Validar compras con umbral de combined
        if action == "BUY" and metrics.get("combined", 0.0) < self.combined_buy_threshold:
            action = "HOLD"
            reason = "Compra invalidada por combined insuficiente"

        take_profit, stop_loss = self.calculate_take_profit_stop_loss(latest_price, action, metrics.get("volatility", 0.0))

        return {
            "action": action,
            "confidence": 0.9 if action in ["BUY", "SELL"] else 0.5,
            "take_profit": take_profit,
            "stop_loss": stop_loss,
            "reason": reason,
            "metrics": metrics
        }

    def calculate_take_profit_stop_loss(self, latest_price: float, action: str, volatility: float) -> Tuple[float, float]:
        """Calcula el nivel de Take Profit y Stop Loss con ajuste dinámico por volatilidad."""
        if action == "BUY":
            take_profit = latest_price * (1 + (self.tp_percentage + (volatility * 10)) / 100)
            stop_loss = latest_price * (1 - (self.sl_percentage + (volatility * 5)) / 100)
        elif action == "SELL":
            take_profit = latest_price * (1 - (self.tp_percentage + (volatility * 10)) / 100)
            stop_loss = latest_price * (1 + (self.sl_percentage + (volatility * 5)) / 100)
        else:
            take_profit = stop_loss = 0.0
        return round(take_profit, 2), round(stop_loss, 2)
