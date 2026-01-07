import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple, Any

import numpy as np
import pandas as pd

logger = logging.getLogger("NertzMetalEngine")

# Store recent combined_scores to check for redundancy
recent_signals = {}  # Format: {symbol: {"score": float, "timestamp": datetime}}


def calculate_rsi(prices: np.ndarray, period: int = 14) -> float:
	try:
		if not isinstance(prices, np.ndarray):
			prices = np.array(prices, dtype=float)
		if len(prices) < period + 1 or np.any(np.isnan(prices)) or np.any(prices <= 0):
			logger.warning("Datos insuficientes o inválidos para RSI")
			return 50.0
		deltas = np.diff(prices)
		gains = np.where(deltas > 0, deltas, 0)
		losses = np.where(deltas < 0, -deltas, 0)
		avg_gain = np.mean(gains[:period])
		avg_loss = np.mean(losses[:period])
		if len(gains) > period:
			for i in range(period, len(gains)):
				avg_gain = (avg_gain * (period - 1) + gains[i]) / period
				avg_loss = (avg_loss * (period - 1) + losses[i]) / period
		if avg_loss == 0:
			return 100.0 if avg_gain > 0 else 50.0
		rs = avg_gain / avg_loss
		rsi = 100 - (100 / (1 + rs))
		return float(rsi)
	except Exception as e:
		logger.error(f"❌ Error calculando RSI: {e}")
		return 50.0


def calculate_adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> float:
	try:
		high = np.array(high, dtype=float)
		low = np.array(low, dtype=float)
		close = np.array(close, dtype=float)
		if (
				len(high) < period + 1
				or len(low) < period + 1
				or len(close) < period + 1
				or np.any(np.isnan(high))
				or np.any(np.isnan(low))
				or np.any(np.isnan(close))
				or np.any(high <= 0)
				or np.any(low <= 0)
				or np.any(close <= 0)
		):
			logger.warning("Datos insuficientes o inválidos para ADX")
			return 25.0
		tr1 = np.abs(high[1:] - low[1:])
		tr2 = np.abs(high[1:] - close[:-1])
		tr3 = np.abs(low[1:] - close[:-1])
		tr = np.maximum(tr1, np.maximum(tr2, tr3))
		up_move = high[1:] - high[:-1]
		down_move = low[:-1] - low[1:]
		plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
		minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
		atr = np.mean(tr[:period]) if np.sum(tr[:period]) > 0 else 0.001
		atr = max(0.001, atr)
		plus_di = 100 * np.mean(plus_dm[:period]) / atr
		minus_di = 100 * np.mean(minus_dm[:period]) / atr
		dx_sum = 0
		if plus_di + minus_di > 0:
			dx_sum = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
		adx = dx_sum
		if len(tr) > period:
			for i in range(period, len(tr)):
				atr = ((period - 1) * atr + tr[i]) / period
				atr = max(0.001, atr)
				plus_di = ((period - 1) * plus_di + 100 * plus_dm[i] / atr) / period
				minus_di = ((period - 1) * minus_di + 100 * minus_dm[i] / atr) / period
				di_sum = plus_di + minus_di
				dx = 100 * np.abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0
				adx = ((period - 1) * adx + dx) / period
		return float(adx)
	except Exception as e:
		logger.error(f"❌ Error calculando ADX: {e}")
		return 25.0


def calculate_ema(prices: np.ndarray, period: int) -> float:
	try:
		if not isinstance(prices, np.ndarray):
			prices = np.array(prices, dtype=float)
		if len(prices) < period or np.any(np.isnan(prices)) or np.any(prices <= 0):
			logger.warning("Datos insuficientes o inválidos para EMA")
			return float(np.mean(prices)) if len(prices) > 0 else 0.0
		series = pd.Series(prices)
		ema = series.ewm(span=period, adjust=False).mean().iloc[-1]
		return float(ema)
	except Exception as e:
		logger.error(f"❌ Error calculando EMA: {e}")
		return float(np.mean(prices)) if len(prices) > 0 else 0.0


def detect_market_regime(closes: np.ndarray, highs: np.ndarray, lows: np.ndarray, config=None) -> str:
	try:
		if len(closes) < 10 or len(highs) < 10 or len(lows) < 10:
			logger.debug("Datos insuficientes para detectar régimen de mercado")
			return "unknown"
		closes = np.array(closes, dtype=float)
		highs = np.array(highs, dtype=float)
		lows = np.array(lows, dtype=float)
		if np.any(np.isnan(closes)) or np.any(np.isnan(highs)) or np.any(np.isnan(lows)):
			logger.warning("Datos inválidos para detectar régimen")
			return "unknown"
		
		rsi = calculate_rsi(closes)
		adx = calculate_adx(highs, lows, closes)
		mean_price = np.mean(closes[-10:])
		recent_volatility = np.std(closes[-10:]) / mean_price if mean_price > 0 else 0
		logger.debug(f"RSI: {rsi:.2f}, ADX: {adx:.2f}, Volatilidad: {recent_volatility:.4f}")
		
		# Determinar umbral de baja volatilidad
		low_volatility_threshold = 0.005  # Valor predeterminado
		
		# Usar configuración si está disponible
		if config and hasattr(config, 'VOLATILITY_RANGES'):
			low_volatility_threshold = config.VOLATILITY_RANGES["LOW_THRESHOLD"]
		
		# Prioritize range detection in low-volatility markets
		if recent_volatility < low_volatility_threshold:
			logger.debug(
				f"Detectado régimen de rango debido a baja volatilidad: {recent_volatility:.4f} < {low_volatility_threshold:.4f}")
			return "range"
		
		if adx > 30 and (rsi > 60 or rsi < 40):  # Stricter ADX threshold for trend
			logger.debug("Detectado régimen de tendencia")
			return "trend"
		elif recent_volatility > 0.01 or (rsi > 70 or rsi < 30):
			logger.debug(f"Detectado régimen volátil (volatilidad: {recent_volatility:.4f}, RSI: {rsi:.2f})")
			return "volatile"
		elif adx < 20 and abs(rsi - 50) < 15:
			logger.debug("Detectado régimen de rango")
			return "range"
		else:
			if len(closes) >= 5 and recent_volatility > 0.005:
				recent_direction = np.sign(closes[-1] - closes[-5])
				very_recent = np.sign(closes[-1] - closes[-2])
				if recent_direction != very_recent and abs(rsi - 50) > 15:
					logger.debug("Detectado régimen de reversión")
					return "reversal"
			logger.debug("Detectado régimen de rango (por defecto)")
			return "range"
	except Exception as e:
		logger.error(f"❌ Error detectando régimen de mercado: {e}")
		return "unknown"


def calculate_dynamic_tp_sl(
		price: float,
		action: str,
		volatility: float,
		base_tp_factor: float = 0.015,
		base_sl_factor: float = 0.008,
		market_regime: str = "unknown"
) -> Tuple[float, float]:
	try:
		if price <= 0 or not isinstance(price, (int, float)):
			logger.warning(f"⚠️ Precio inválido: {price}")
			return price * 1.01, price * 0.99
		if action.lower() == "hold":
			margin = max(0.005, min(0.03, volatility * 10))
			return price * (1 + margin), price * (1 - margin)
		market_regime = market_regime or "unknown"
		if market_regime == "trend":
			tp_multiplier = 1.5
			sl_multiplier = 0.8
		elif market_regime == "volatile":
			tp_multiplier = 1.2
			sl_multiplier = 0.7
		elif market_regime == "range":
			tp_multiplier = 1.0  # Increased to allow more price movement
			sl_multiplier = 1.0
		elif market_regime == "reversal":
			tp_multiplier = 1.3
			sl_multiplier = 0.8
		else:
			tp_multiplier = 1.0
			sl_multiplier = 1.0
		volatility = max(0.001, abs(float(volatility or 0.01)))
		volatility_tp_factor = min(1.0 + volatility * 10, 2.0)
		volatility_sl_factor = max(1.0 - volatility * 5, 0.5)
		final_tp_factor = base_tp_factor * tp_multiplier * volatility_tp_factor
		final_sl_factor = base_sl_factor * sl_multiplier * volatility_sl_factor
		if action.lower() == "buy":
			tp_price = price * (1 + final_tp_factor)
			sl_price = price * (1 - final_sl_factor)
			# Ensure minimum price movement
			if abs(tp_price - price) < price * 0.002:
				tp_price = price * 1.002
			if abs(sl_price - price) < price * 0.002:
				sl_price = price * 0.998
		elif action.lower() == "sell":
			tp_price = price * (1 - final_tp_factor)
			sl_price = price * (1 + final_sl_factor)
			if abs(tp_price - price) < price * 0.002:
				tp_price = price * 0.998
			if abs(sl_price - price) < price * 0.002:
				sl_price = price * 1.002
		else:
			logger.warning(f"⚠️ Acción no reconocida: {action}")
			margin = max(0.005, min(0.03, volatility * 10))
			return price * (1 + margin), price * (1 - margin)
		return tp_price, sl_price
	except Exception as e:
		logger.error(f"❌ Error calculando TP/SL dinámicos: {e}")
		return (
			price * 1.01 if action.lower() == "buy" else price * 0.99,
			price * 0.99 if action.lower() == "buy" else price * 1.01
		)


def calculate_bollinger_bands(prices: np.ndarray, period: int = 20, num_std: float = 2.0) -> Tuple[
	np.ndarray, np.ndarray, np.ndarray]:
	"""
	Calcula las Bandas de Bollinger para una serie de precios.

	Args:
		prices: Array de precios
		period: Período para la media móvil (por defecto 20)
		num_std: Número de desviaciones estándar para las bandas (por defecto 2.0)

	Returns:
		Tupla con (banda superior, media móvil, banda inferior)
	"""
	try:
		if not isinstance(prices, np.ndarray):
			prices = np.array(prices, dtype=float)
		if len(prices) < period or np.any(np.isnan(prices)) or np.any(prices <= 0):
			logger.warning("Datos insuficientes o inválidos para Bandas de Bollinger")
			return np.array([0.0]), np.array([0.0]), np.array([0.0])
		
		# Calcular la media móvil simple
		sma = np.convolve(prices, np.ones(period) / period, mode='valid')
		
		# Calcular la desviación estándar
		roller = np.lib.stride_tricks.sliding_window_view(prices, period)
		std = np.array([np.std(window) for window in roller])
		
		# Calcular las bandas
		upper_band = sma + (std * num_std)
		lower_band = sma - (std * num_std)
		
		return upper_band, sma, lower_band
	except Exception as e:
		logger.error(f"❌ Error calculando Bandas de Bollinger: {e}")
		return np.array([0.0]), np.array([0.0]), np.array([0.0])


def calculate_metrics(candles: List[Dict], orderbook: Dict, ticker: Dict, symbol: str = None) -> Dict[str, float]:
	try:
		metrics = {
			"ild": 0.0,
			"egm": 0.0,
			"rol": 1.0,
			"pio": 0.0,
			"ogm": 0.0,
			"combined_score": 0.0,
			"volatility": 0.01,
			"last_price": float(ticker.get("last_price", 0)),
			"bb_position": 0.0  # Posición relativa en las Bandas de Bollinger
		}
		if not candles or len(candles) < 5:
			logger.warning("Insuficientes velas para calcular métricas")
			return metrics
		closes = np.array([float(c.get("close", 0)) for c in candles])
		highs = np.array([float(c.get("high", 0)) for c in candles])
		lows = np.array([float(c.get("low", 0)) for c in candles])
		volumes = np.array([float(c.get("volume", 0)) for c in candles])
		if (
				np.any(np.isnan(closes))
				or np.any(np.isnan(highs))
				or np.any(np.isnan(lows))
				or np.any(closes <= 0)
		):
			logger.warning("Datos de velas inválidos")
			return metrics
		pct_changes = np.diff(closes) / closes[:-1]
		volatility = np.std(pct_changes) if len(pct_changes) > 0 else 0.01
		volatility = max(0.001, volatility)
		metrics["volatility"] = volatility
		metrics["last_price"] = float(ticker.get("last_price", closes[-1]))
		recent_closes = closes[-5:]
		recent_highs = highs[-5:]
		recent_lows = lows[-5:]
		recent_volumes = volumes[-5:]
		avg_price = np.mean(recent_closes)
		avg_range = np.mean(recent_highs - recent_lows)
		last_close = recent_closes[-1]
		price_distance = (last_close - avg_price) / avg_range if avg_range > 0 else 0
		vol_ratio = recent_volumes[-1] / np.mean(recent_volumes) if np.mean(recent_volumes) > 0 else 1
		# Normalize egm to prevent extreme values
		metrics["egm"] = np.tanh(price_distance * min(vol_ratio, 2.0) * (1 / volatility)) * 100
		
		# Calcular Bandas de Bollinger
		if len(closes) >= 20:
			upper_band, middle_band, lower_band = calculate_bollinger_bands(closes)
			if len(upper_band) > 0 and len(middle_band) > 0 and len(lower_band) > 0:
				# Obtener los valores más recientes
				current_upper = upper_band[-1]
				current_middle = middle_band[-1]
				current_lower = lower_band[-1]
				
				# Calcular la posición relativa del precio actual en las bandas
				band_width = current_upper - current_lower
				if band_width > 0:
					bb_position = (last_close - current_lower) / band_width
					metrics["bb_position"] = bb_position
					
					# Ajustar el combined_score basado en las Bandas de Bollinger
					if bb_position > 0.95:  # Cerca o por encima de la banda superior
						metrics["bb_signal"] = "overbought"
					elif bb_position < 0.05:  # Cerca o por debajo de la banda inferior
						metrics["bb_signal"] = "oversold"
					else:
						metrics["bb_signal"] = "neutral"
		if orderbook and "bids" in orderbook and "asks" in orderbook and orderbook["bids"] and orderbook["asks"]:
			bids = [(float(b[0]), float(b[1])) for b in orderbook["bids"][:10]]
			asks = [(float(a[0]), float(a[1])) for a in orderbook["asks"][:10]]
			bid_liquidity = sum(b[1] for b in bids)
			ask_liquidity = sum(a[1] for a in asks)
			total_liquidity = bid_liquidity + ask_liquidity
			metrics["ild"] = (bid_liquidity - ask_liquidity) / total_liquidity if total_liquidity > 0 else 0
			metrics["rol"] = bid_liquidity / ask_liquidity if ask_liquidity > 0 else 2.0 if bid_liquidity > 0 else 1.0
			best_bid = bids[0][0]
			best_ask = asks[0][0]
			spread = (best_ask - best_bid) / best_bid if best_bid > 0 else 0
			bid_depth = sum(b[0] * b[1] for b in bids) / bid_liquidity if bid_liquidity > 0 else 0
			ask_depth = sum(a[0] * a[1] for a in asks) / ask_liquidity if ask_liquidity > 0 else 0
			metrics["pio"] = (bid_depth - ask_depth) / (spread * 100) if spread > 0 else 0
			metrics["ogm"] = metrics["ild"] * metrics["egm"]
		else:
			logger.debug("Orderbook no disponible o inválido")
		# Calculate combined_score with RSI, ADX, and Bollinger Bands adjustments
		rsi = calculate_rsi(closes)
		adx = calculate_adx(highs, lows, closes)
		combined_score = (
				0.25 * metrics["ild"] +
				0.25 * metrics["egm"] +
				0.2 * metrics["rol"] +
				0.1 * metrics["pio"] +
				0.1 * metrics["ogm"] +
				0.1 * (50 - metrics["bb_position"] * 100)  # Convertir posición BB a un valor centrado
		)
		# Adjust combined_score based on RSI and ADX
		if rsi < 30:
			combined_score *= 1.2  # Boost buy signals in oversold conditions
		elif rsi > 70:
			combined_score *= -1.2  # Boost sell signals in overbought conditions
		if adx < 20:
			combined_score *= 0.7  # Reduce signals in weak trends
		
		# Ajustar según señales de Bandas de Bollinger
		if "bb_signal" in metrics:
			if metrics["bb_signal"] == "oversold" and combined_score > 0:
				combined_score *= 1.3  # Amplificar señales de compra en condiciones de sobreventa
			elif metrics["bb_signal"] == "overbought" and combined_score < 0:
				combined_score *= 1.3  # Amplificar señales de venta en condiciones de sobrecompra
		
		# Suppress signals in low-volatility markets, pero permitir señales de BB
		if volatility < 0.005 and ("bb_signal" not in metrics or metrics["bb_signal"] == "neutral"):
			combined_score *= 0.5
		# Normalize combined_score to [-100, 100]
		metrics["combined_score"] = np.tanh(combined_score / 100) * 100
		# Check for redundant signals if symbol is provided
		if symbol:
			current_time = datetime.now(timezone.utc)
			if symbol in recent_signals:
				prev_score = recent_signals[symbol]["score"]
				prev_time = recent_signals[symbol]["timestamp"]
				time_diff = (current_time - prev_time).total_seconds()
				score_diff = abs(metrics["combined_score"] - prev_score)
				if time_diff < 300 and score_diff < 10:  # 5-minute window, small score change
					logger.info(
						f"🔄 Suppressing redundant signal for {symbol}: score={metrics['combined_score']:.2f}, prev={prev_score:.2f}")
					metrics["combined_score"] = 0  # Neutralize the signal
			recent_signals[symbol] = {"score": metrics["combined_score"], "timestamp": current_time}
		return metrics
	except Exception as e:
		logger.error(f"❌ Error calculando métricas: {e}")
		return {
			"ild": 0.0,
			"egm": 0.0,
			"rol": 1.0,
			"pio": 0.0,
			"ogm": 0.0,
			"combined_score": 0.0,
			"volatility": 0.01,
			"last_price": float(ticker.get("last_price", 0))
		}


def save_results(data: Dict, logs_dir: str, file_path: Optional[str] = None) -> None:
	"""Guarda los resultados en un archivo JSON."""
	if file_path is None or not os.path.isabs(file_path):
		logs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
		file_path = os.path.join(logs_dir, "results.json")
	
	os.makedirs(os.path.dirname(file_path), exist_ok=True)
	try:
		existing_data = {}
		if os.path.exists(file_path):
			try:
				with open(file_path, "r") as f:
					existing_data = json.load(f)
			except json.JSONDecodeError:
				logger.warning(f"⚠️ Error al leer {file_path}, creando nuevo archivo")
		
		merged_data = data.copy()
		current_time = datetime.now(timezone.utc).isoformat()
		# Ensure trades and orders are distinct
		if "trades" in existing_data and "trades" in data:
			existing_trades = {trade["trade_id"]: trade for trade in existing_data["trades"]}
			for trade in data["trades"]:
				trade_id = trade["trade_id"]
				if trade_id in existing_trades:
					# Update existing trade
					existing_trades[trade_id].update(trade)
				else:
					existing_trades[trade_id] = trade
			merged_data["trades"] = list(existing_trades.values())
		if "orders" in existing_data and "orders" in data:
			existing_orders = {order["order_id"]: order for order in existing_data["orders"]}
			for order in data["orders"]:
				order_id = order["order_id"]
				if order_id in existing_orders:
					existing_orders[order_id].update(order)
				else:
					existing_orders[order_id] = order
			merged_data["orders"] = list(existing_orders.values())
		# Update market conditions without overwriting trades
		if "metadata" in data and "market_conditions" in data["metadata"]:
			if "metadata" not in merged_data:
				merged_data["metadata"] = {}
			merged_data["metadata"]["market_conditions"] = data["metadata"]["market_conditions"]
			merged_data["metadata"]["timestamp"] = current_time
			logger.info("🔄 Actualizando condiciones de mercado sin sobrescribir datos de trading")
		
		with open(file_path, "w") as f:
			json.dump(merged_data, f, indent=2, default=str)
		logger.info(f"📊 Resultados guardados en {file_path}")
		
		all_results_path = os.path.join(os.path.dirname(file_path), "all_results.json")
		all_data = {"sessions": []}
		if os.path.exists(all_results_path):
			try:
				with open(all_results_path, "r") as f:
					all_data = json.load(f)
			except json.JSONDecodeError:
				logger.warning("⚠️ Error al leer all_results.json, creando nuevo archivo")
		
		data_with_timestamp = merged_data.copy()
		data_with_timestamp["saved_at"] = current_time
		all_data["sessions"].append(data_with_timestamp)
		
		if len(all_data["sessions"]) > 100:
			all_data["sessions"] = all_data["sessions"][-100:]
		
		with open(all_results_path, "w") as f:
			json.dump(all_data, f, indent=2, default=str)
	except Exception as e:
		logger.error(f"❌ Error guardando resultados: {e}")


class TpslStrategy:
	def __init__(self):
		self.configs = {
			"range": {
				"tp_factor": 0.015,  # Increased for more movement
				"sl_factor": 0.008,
				"trailing_activation": 0.5,
			},
			"trend": {
				"tp_factor": 0.02,
				"sl_factor": 0.012,
				"trailing_activation": 0.3,
			},
			"volatile": {
				"tp_factor": 0.03,
				"sl_factor": 0.015,
				"trailing_activation": 0.4,
			},
			"reversal": {
				"tp_factor": 0.015,
				"sl_factor": 0.008,
				"trailing_activation": 0.4,
			},
			"default": {
				"tp_factor": 0.015,
				"sl_factor": 0.008,
				"trailing_activation": 0.5,
			}
		}
	
	def calculate_levels(self, price: float, action: str, volatility: float, market_regime: str) -> Dict[str, float]:
		try:
			if price <= 0 or not isinstance(price, (int, float)):
				logger.warning(f"⚠️ Precio inválido: {price}")
				return {
					"entry_price": price,
					"take_profit": price * 1.01,
					"stop_loss": price * 0.99,
					"trailing_activation": price,
					"action": action,
					"tp_factor": 0.01,
					"sl_factor": 0.01
				}
			config = self.configs.get(market_regime, self.configs["default"])
			volatility = max(0.001, abs(float(volatility or 0.01)))
			vol_multiplier = max(0.5, min(2.0, 1.0 + (volatility * 10)))
			tp_factor = min(0.1, config["tp_factor"] * vol_multiplier)
			sl_factor = min(0.05, config["sl_factor"] * vol_multiplier)
			if action.lower() == 'buy':
				tp = price * (1 + tp_factor)
				sl = price * (1 - sl_factor)
				if abs(tp - price) < price * 0.002:
					tp = price * 1.002
				if abs(sl - price) < price * 0.002:
					sl = price * 0.998
			else:
				tp = price * (1 - tp_factor)
				sl = price * (1 + sl_factor)
				if abs(tp - price) < price * 0.002:
					tp = price * 0.998
				if abs(sl - price) < price * 0.002:
					sl = price * 1.002
			return {
				"entry_price": price,
				"take_profit": tp,
				"stop_loss": sl,
				"trailing_activation": price + ((tp - price) * config["trailing_activation"]) if action.lower() == 'buy'
				else price - ((price - tp) * config["trailing_activation"]),
				"action": action,
				"tp_factor": tp_factor,
				"sl_factor": sl_factor
			}
		except Exception as e:
			logger.error(f"❌ Error calculando niveles TP/SL: {e}")
			return {
				"entry_price": price,
				"take_profit": price * 1.01 if action.lower() == 'buy' else price * 0.99,
				"stop_loss": price * 0.99 if action.lower() == 'buy' else price * 1.01,
				"trailing_activation": price,
				"action": action,
				"tp_factor": 0.01,
				"sl_factor": 0.01
			}
	
	def update_trailing_stop(self, levels: Dict[str, float], current_price: float) -> Dict[str, float]:
		try:
			action = levels["action"]
			trailing_activated = False
			if action.lower() == 'buy':
				if current_price >= levels["trailing_activation"]:
					trailing_activated = True
					price_move = current_price - levels["entry_price"]
					sl_distance = max(levels["entry_price"] - levels["stop_loss"], price_move * 0.5)
					new_sl = current_price - sl_distance
					if new_sl > levels["stop_loss"]:
						levels["stop_loss"] = new_sl
			else:
				if current_price <= levels["trailing_activation"]:
					trailing_activated = True
					price_move = levels["entry_price"] - current_price
					sl_distance = max(levels["stop_loss"] - levels["entry_price"], price_move * 0.5)
					new_sl = current_price + sl_distance
					if new_sl < levels["stop_loss"]:
						levels["stop_loss"] = new_sl
			levels["trailing_active"] = trailing_activated
			return levels
		except Exception as e:
			logger.error(f"❌ Error actualizando trailing stop: {e}")
			return levels


def timestamp_to_datetime(timestamp: int) -> datetime:
	try:
		timestamp = int(timestamp)
		if timestamp > 10 ** 11:
			timestamp = timestamp // 1000
		return datetime.fromtimestamp(timestamp, tz=timezone.utc)
	except (ValueError, TypeError, OSError) as e:
		logger.warning(f"⚠️ Error al convertir timestamp {timestamp}: {e}")
		return datetime.now(timezone.utc)


class TimeZoneManager:
	def __init__(self):
		self.use_utc = True
	
	def get_current_time(self):
		return datetime.now(timezone.utc)
	
	def convert_timestamp(self, timestamp, to_timezone=None):
		dt_utc = timestamp_to_datetime(timestamp)
		if to_timezone and to_timezone != timezone.utc:
			try:
				import pytz
				target_tz = pytz.timezone(to_timezone)
				return dt_utc.astimezone(target_tz)
			except Exception as e:
				logger.warning(f"⚠️ Error convirtiendo timezone: {e}")
		return dt_utc
	
	def convert_bybit_timestamp(self, timestamp):
		try:
			timestamp = int(timestamp)
			if timestamp > 10 ** 11:
				timestamp = timestamp / 1000
			return datetime.fromtimestamp(timestamp, tz=timezone.utc)
		except Exception as e:
			logger.warning(f"⚠️ Error convirtiendo timestamp de Bybit: {e}")
			return datetime.now(timezone.utc)
	
	def format_for_bybit(self, dt):
		if dt.tzinfo is None:
			dt = dt.replace(tzinfo=timezone.utc)
		return int(dt.timestamp() * 1000)
	
	def get_timestamp_delta(self, timestamp1, timestamp2):
		dt1 = self.convert_timestamp(timestamp1)
		dt2 = self.convert_timestamp(timestamp2)
		return abs((dt1 - dt2).total_seconds())
	
	def is_market_open(self, market_hours=None):
		return True  # Cripto opera 24/7


tz_manager = TimeZoneManager()


def get_cache_key(prefix: str, params: Dict[str, Any]) -> str:
	param_str = json.dumps(params, sort_keys=True, default=str)
	return f"{prefix}:{param_str}"


async def get_cached_result(db_manager, key: str, max_age_seconds: int = 300) -> Optional[Dict]:
	try:
		if db_manager:
			result = await db_manager.execute_query(
				"""
                SELECT calculation_result, created_at
                FROM calculation_cache
                WHERE calculation_key = %s
                  AND expires_at > NOW()
				""",
				(key,)
			)
			if result and len(result) > 0:
				calculation_result = result[0][0]
				created_at = result[0][1]
				if created_at.tzinfo is None:
					created_at = created_at.replace(tzinfo=timezone.utc)
				age = (datetime.now(timezone.utc) - created_at).total_seconds()
				if age <= max_age_seconds:
					logger.debug(f"🔍 Usando resultado en caché para {key} (edad: {age:.1f}s)")
					return json.loads(calculation_result) if isinstance(calculation_result, str) else calculation_result
		else:
			logger.warning("⚠️ db_manager no disponible, omitiendo caché")
		return None
	except Exception as e:
		logger.warning(f"⚠️ Error al recuperar caché para {key}: {e}")
		return None


async def store_cached_result(db_manager, key: str, result: Any, ttl_seconds: int = 1800) -> bool:
	try:
		result_json = json.dumps(result, default=str) if not isinstance(result, str) else result
		current_time = datetime.now(timezone.utc)
		expires_at = current_time + timedelta(seconds=ttl_seconds)
		if db_manager:
			if hasattr(db_manager, 'cache_calculation'):
				success = db_manager.cache_calculation(key, result, ttl_seconds)
				if success:
					logger.debug(f"💾 Almacenado en caché: {key} (TTL: {ttl_seconds}s)")
					return True
			await db_manager.execute_query(
				"""
                INSERT INTO calculation_cache (calculation_key, calculation_result, created_at, expires_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (calculation_key)
                    DO UPDATE SET calculation_result = %s,
                                  created_at         = %s,
                                  expires_at         = %s
				""",
				(key, result_json, current_time, expires_at, result_json, current_time, expires_at)
			)
			logger.debug(f"💾 Almacenado en caché: {key} (TTL: {ttl_seconds}s)")
			return True
		else:
			logger.warning("⚠️ db_manager no disponible, omitiendo caché")
			return False
	except Exception as e:
		logger.warning(f"⚠️ Error al guardar caché para {key}: {e}")
		return False


def analyze_arbitrage_opportunity(
		market_price: float,
		index_price: float,
		volatility: float,
		market_regime: str = "unknown"
) -> Dict[str, Any]:
	try:
		if market_price <= 0 or index_price <= 0:
			logger.warning(f"⚠️ Precios inválidos: mercado={market_price}, índice={index_price}")
			return {
				"is_opportunity": False,
				"confidence": 0.0,
				"action": "none",
				"reason": "Precios inválidos"
			}
		price_diff_pct = abs(market_price - index_price) / index_price * 100
		base_threshold = 0.2 if volatility < 0.005 else 0.3 if volatility < 0.01 else 0.5
		regime_multiplier = 1.5 if market_regime == "volatile" else 0.8 if market_regime == "range" else 1.2
		threshold = base_threshold * regime_multiplier
		if price_diff_pct <= threshold:
			return {
				"is_opportunity": False,
				"confidence": 0.0,
				"action": "none",
				"diff_pct": price_diff_pct,
				"threshold": threshold,
				"reason": f"Diferencia ({price_diff_pct:.2f}%) menor que umbral ({threshold:.2f}%)"
			}
		confidence = min(1.0, price_diff_pct / (threshold * 2))
		action = "sell" if market_price > index_price else "buy"
		reason = f"Precio de mercado ({market_price:.4f}) {'mayor' if market_price > index_price else 'menor'} que índice ({index_price:.4f}) por {price_diff_pct:.2f}%"
		if market_regime == "volatile" and confidence > 0.7:
			confidence = min(0.7, confidence * 0.9)
			reason += " - Confianza limitada debido a régimen volátil"
		elif market_regime == "range" and confidence < 0.3:
			confidence = min(0.6, confidence * 1.5)
			reason += " - Confianza aumentada en régimen de rango"
		return {
			"is_opportunity": True,
			"confidence": confidence,
			"action": action,
			"diff_pct": price_diff_pct,
			"threshold": threshold,
			"reason": reason
		}
	except Exception as e:
		logger.error(f"❌ Error analizando arbitraje: {e}")
		return {
			"is_opportunity": False,
			"confidence": 0.0,
			"action": "none",
			"reason": f"Error en análisis: {str(e)}"
		}


async def update_market_condition(
		db_manager, optimizer, symbol: str, candles: List[Dict],
		volatility: float, orderbook: Dict = None,
		use_cache: bool = True
) -> None:
	if not candles or len(candles) < 5:
		logger.warning(f"⚠️ Insuficientes velas para {symbol}: {len(candles)}")
		return
	try:
		if use_cache and db_manager:
			latest_candles = candles[-3:]
			candle_hashes = [
				f"{c.get('open', 0):.2f}:{c.get('high', 0):.2f}:{c.get('low', 0):.2f}:{c.get('close', 0):.2f}"
				for c in latest_candles
			]
			cache_params = {
				"symbol": symbol,
				"candle_hash": "_".join(candle_hashes),
				"volatility": round(volatility, 6)
			}
			cache_key = get_cache_key("market_condition", cache_params)
			cached = await get_cached_result(db_manager, cache_key, 600)
			if cached and hasattr(optimizer, 'market_conditions'):
				optimizer.market_conditions[symbol] = cached
				logger.info(f"📊 Condición de mercado cargada de caché para {symbol}")
				return
		closes = np.array([float(c.get("close", 0)) for c in candles[-20:]])
		highs = np.array([float(c.get("high", 0)) for c in candles[-20:]])
		lows = np.array([float(c.get("low", 0)) for c in candles[-20:]])
		if np.any(closes <= 0) or np.any(highs <= 0) or np.any(lows <= 0):
			logger.warning(f"⚠️ Datos de velas inválidos para {symbol}")
			return
		ema_short = calculate_ema(closes, 5)
		ema_medium = calculate_ema(closes, 10)
		ema_long = calculate_ema(closes, 20)
		rsi = calculate_rsi(closes)
		adx = calculate_adx(highs, lows, closes)
		market_regime = detect_market_regime(closes, highs, lows)
		
		# Calcular Bandas de Bollinger
		bb_upper, bb_middle, bb_lower = calculate_bollinger_bands(closes)
		bb_width = 0.0
		bb_position = 0.5  # Posición neutral por defecto
		
		if len(bb_upper) > 0 and len(bb_lower) > 0:
			current_upper = bb_upper[-1]
			current_lower = bb_lower[-1]
			current_price = closes[-1]
			bb_width = (current_upper - current_lower) / current_lower if current_lower > 0 else 0
			
			# Calcular posición relativa del precio en las bandas
			if current_upper > current_lower:
				bb_position = (current_price - current_lower) / (current_upper - current_lower)
		
		trend = (
			"bullish" if ema_short > ema_medium > ema_long and adx > 25 else
			"bearish" if ema_short < ema_medium < ema_long and adx > 25 else
			"sideways"
		)
		
		# Usar Bandas de Bollinger para mejorar la clasificación de volatilidad
		vol_class = (
			"low" if (volatility < 0.005 and adx < 20 and bb_width < 0.02) else
			"high" if (volatility > 0.01 or (rsi > 70 or rsi < 30) or bb_width > 0.04) else
			"medium"
		)
		
		support = float(np.mean(np.sort(lows[-10:])[:3])) if len(lows) >= 10 else 0
		resistance = float(np.mean(np.sort(highs[-10:])[-3:])) if len(highs) >= 10 else 0
		current_price = closes[-1]
		orderbook_analysis = {}
		implied_volatility = 0.0
		if orderbook and "bids" in orderbook and "asks" in orderbook:
			bids = orderbook["bids"][:5]
			asks = orderbook["asks"][:5]
			if bids and asks:
				bid_vol = sum(float(b[1]) for b in bids if len(b) > 1)
				ask_vol = sum(float(a[1]) for a in asks if len(a) > 1)
				spread = (
					(float(asks[0][0]) - float(bids[0][0])) / current_price
					if current_price > 0 and len(asks) > 0 and len(bids) > 0
					else 0
				)
				implied_volatility = spread * (bid_vol + ask_vol) / max(bid_vol, ask_vol, 1)
				volume_imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol) if (bid_vol + ask_vol) > 0 else 0
				orderbook_analysis = {
					"imbalance": float(volume_imbalance),
					"spread": float(spread),
					"bid_volume": float(bid_vol),
					"ask_volume": float(ask_vol),
					"signal": "buy" if volume_imbalance > 0.2 else "sell" if volume_imbalance < -0.2 else "neutral"
				}
		market_condition = {
			"trend": trend,
			"regime": market_regime,
			"volatility": vol_class,
			"volatility_value": float(volatility),
			"implied_volatility": float(implied_volatility),
			"current_price": float(current_price),
			"rsi": float(rsi),
			"adx": float(adx),
			"support": float(support),
			"resistance": float(resistance),
			"bb_width": float(bb_width),
			"bb_position": float(bb_position),
			"last_update": datetime.now(timezone.utc).isoformat(),
			"orderbook_analysis": orderbook_analysis
		}
		if hasattr(optimizer, 'market_conditions'):
			optimizer.market_conditions[symbol] = market_condition
			if hasattr(optimizer, 'save_market_conditions'):
				await optimizer.save_market_conditions()
			logs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
			data = {
				"metadata": {
					"session_start": datetime.now(timezone.utc).isoformat(),
					"timestamp": datetime.now(timezone.utc).isoformat(),
					"market_conditions": optimizer.market_conditions
				}
			}
			save_results(data, logs_dir)
		if db_manager and use_cache:
			await store_cached_result(db_manager, cache_key, market_condition, 600)
			try:
				bid_vol = orderbook_analysis.get("bid_volume", 0)
				ask_vol = orderbook_analysis.get("ask_volume", 0)
				combined_score = (rsi / 100.0) - 0.5
				await db_manager.execute_query(
					"""
                    INSERT INTO market_metrics
                    (timestamp, symbol, combined, volatility, last_price, price_range, spread, liquidity_ratio)
                    VALUES (NOW(), %s, %s, %s, %s, %s, %s, %s)
					""",
					(
						symbol, float(combined_score), float(volatility), float(current_price),
						float(abs(resistance - support) if resistance and support else 0),
						float(orderbook_analysis.get("spread", 0)),
						float(bid_vol / ask_vol if ask_vol > 0 else 1.0)
					)
				)
			except Exception as e:
				logger.warning(f"⚠️ Error al guardar métricas en BD: {e}")
		logger.info(
			f"📊 Condición de mercado actualizada para {symbol}: Tendencia={trend}, Régimen={market_regime}, Volatilidad={vol_class} ({volatility:.4f})"
		)
		try:
			market_analysis_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
			                                    "data", "market_analysis", "market_analysis.json")
			os.makedirs(os.path.dirname(market_analysis_path), exist_ok=True)
			market_data = {}
			if os.path.exists(market_analysis_path):
				try:
					with open(market_analysis_path, "r") as f:
						market_data = json.load(f)
				except (FileNotFoundError, json.JSONDecodeError):
					market_data = {}
			if not isinstance(market_data, dict):
				market_data = {}
			if "market_regimes" not in market_data:
				market_data["market_regimes"] = {}
			elif not isinstance(market_data["market_regimes"], dict):
				market_data["market_regimes"] = {}
			if "volatility_regimes" not in market_data:
				market_data["volatility_regimes"] = {}
			elif not isinstance(market_data["volatility_regimes"], dict):
				market_data["volatility_regimes"] = {}
			if "orderbook_analysis" not in market_data:
				market_data["orderbook_analysis"] = {}
			elif not isinstance(market_data["orderbook_analysis"], dict):
				market_data["orderbook_analysis"] = {}
			market_data["market_regimes"][symbol] = market_regime
			market_data["volatility_regimes"][symbol] = float(volatility)
			if orderbook_analysis:
				market_data["orderbook_analysis"][symbol] = orderbook_analysis
		except Exception as e:
			logger.warning(f"⚠️ Error al actualizar archivo market_analysis.json: {e}")
	except Exception as e:
		logger.error(f"❌ Error actualizando condición de mercado para {symbol}: {e}")


async def get_trading_recommendation(
		db_manager, optimizer, symbol: str, metrics: Dict,
		volatility_analysis: Optional[Dict] = None,
		use_cache: bool = True
) -> Dict:
	try:
		metrics = metrics or {}
		if use_cache and db_manager:
			cache_params = {
				"symbol": symbol,
				"metrics": {k: round(float(v), 6) if isinstance(v, (int, float)) else v for k, v in metrics.items()},
				"vol_analysis": str(volatility_analysis)[:100] if volatility_analysis else None
			}
			cache_key = get_cache_key("trading_recommendation", cache_params)
			cached = await get_cached_result(db_manager, cache_key)
			if cached:
				return cached
		egm = metrics.get("egm", 0)
		pio = metrics.get("pio", 0)
		combined_score = metrics.get("combined_score", 0)
		last_price = metrics.get("last_price", 0)
		volatility = metrics.get("volatility", 0.01)
		decision = "hold"
		confidence = 0.5
		reasons = []
		market_condition = {
			"trend": "unknown",
			"regime": "unknown",
			"volatility": "medium",
			"support": 0,
			"resistance": 0
		}
		if hasattr(optimizer, 'market_conditions') and symbol in optimizer.market_conditions:
			market_condition.update(optimizer.market_conditions[symbol])
		if volatility_analysis and isinstance(volatility_analysis, dict):
			market_condition.update({
				"regime": volatility_analysis.get("regime", market_condition["regime"]),
				"support": volatility_analysis.get("support", market_condition["support"]),
				"resistance": volatility_analysis.get("resistance", market_condition["resistance"])
			})
		if hasattr(optimizer, 'config'):
			config = optimizer.config
			if egm > config.EGM_BUY_THRESHOLD and pio > config.PIO_THRESHOLD:
				decision = "buy"
				confidence = min(0.5 + abs(egm) * 0.5, 0.95)
				reasons.append(f"EGM positivo ({egm:.2f} > {config.EGM_BUY_THRESHOLD})")
				reasons.append(f"PIO positivo ({pio:.2f} > {config.PIO_THRESHOLD})")
			elif egm < config.EGM_SELL_THRESHOLD and pio < -config.PIO_THRESHOLD:
				decision = "sell"
				confidence = min(0.5 + abs(egm) * 0.5, 0.95)
				reasons.append(f"EGM negativo ({egm:.2f} < {config.EGM_SELL_THRESHOLD})")
				reasons.append(f"PIO negativo ({pio:.2f} < -{config.PIO_THRESHOLD})")
			else:
				reasons.append("Métricas en rango neutral")
		else:
			if egm > 0.5 and pio > 0.1:
				decision = "buy"
				confidence = min(0.5 + abs(egm) * 0.5, 0.95)
				reasons.append(f"EGM positivo ({egm:.2f} > 0.5)")
				reasons.append(f"PIO positivo ({pio:.2f} > 0.1)")
			elif egm < -0.5 and pio < -0.1:
				decision = "sell"
				confidence = min(0.5 + abs(egm) * 0.5, 0.95)
				reasons.append(f"EGM negativo ({egm:.2f} < -0.5)")
				reasons.append(f"PIO negativo ({pio:.2f} < -0.1)")
			else:
				reasons.append("Métricas en rango neutral")
		if "usd_index_price" in metrics and metrics["usd_index_price"]:
			arb_result = analyze_arbitrage_opportunity(
				market_price=last_price,
				index_price=float(metrics["usd_index_price"]),
				volatility=volatility,
				market_regime=market_condition.get("regime", "unknown")
			)
			if arb_result["is_opportunity"] and arb_result["confidence"] > confidence:
				decision = arb_result["action"]
				confidence = arb_result["confidence"]
				reasons = [f"Arbitraje: {arb_result['reason']}"]
		regime = market_condition.get("regime", "unknown")
		if regime == "volatile" and confidence > 0.7:
			confidence = 0.7
			reasons.append("Confianza limitada por régimen volátil")
		elif regime == "trend" and decision in ["buy", "sell"]:
			confidence = min(confidence * 1.2, 0.95)
			reasons.append("Confianza aumentada por tendencia establecida")
		result = {
			"symbol": symbol,
			"decision": decision,
			"confidence": confidence,
			"reasons": reasons,
			"metrics": {
				"egm": egm,
				"pio": pio,
				"combined_score": combined_score
			},
			"market_condition": market_condition,
			"timestamp": datetime.now(timezone.utc).isoformat()
		}
		if use_cache and db_manager:
			await store_cached_result(db_manager, cache_key, result, 300)
		return result
	except Exception as e:
		logger.error(f"❌ Error generando recomendación de trading: {e}")
		return {
			"symbol": symbol,
			"decision": "hold",
			"confidence": 0.0,
			"reasons": [f"Error: {str(e)}"],
			"error": True,
			"timestamp": datetime.now(timezone.utc).isoformat()
		}
