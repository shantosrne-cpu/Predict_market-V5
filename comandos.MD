Aquí tienes todos los comandos de tu API FastAPI formateados como enlaces clicables (usando markdown).
Puedes hacer clic directamente en cada uno para copiar el comando completo al portapapeles (en la mayoría de interfaces compatibles), o simplemente copiarlo manualmente.
Variables recomendadas (PowerShell)
PowerShell$BASE = "http://localhost:8081"
$SYMBOL = "BTCUSDT"
Soporte (órdenes / reconciliación)

curl.exe "$BASE/support/status"
curl.exe -X POST "$BASE/support/start?interval_s=2.0"
curl.exe -X POST "$BASE/support/stop"
curl.exe -X POST "$BASE/support/reconcile_now"
curl.exe -X POST "$BASE/support/reconcile_now?symbol=$SYMBOL"

Lecturas (market/metrics)

curl.exe "$BASE/settings"
curl.exe "$BASE/market_data/$SYMBOL"
curl.exe "$BASE/ticker/$SYMBOL"
curl.exe "$BASE/metrics/$SYMBOL"
curl.exe "$BASE/profit"
curl.exe "$BASE/orderbook/$SYMBOL"
curl.exe "$BASE/exchange/open_orders/$SYMBOL"
curl.exe "$BASE/candles/$SYMBOL/5"
curl.exe "$BASE/trades/$SYMBOL"
curl.exe "$BASE/last_trade/$SYMBOL"

Calibración

curl.exe "$BASE/calibrate/$SYMBOL?lookback=600&target_signal_rate=0.06"
curl.exe -X POST "$BASE/calibrate/$SYMBOL?lookback=600&target_signal_rate=0.06&alpha=1.0&apply=true"

Configuración

curl.exe "$BASE/config"
curl.exe -X POST "$BASE/config/update_thresholds?egm_buy_threshold=0.10&egm_sell_threshold=-0.10&combined_buy_threshold=2.0&combined_sell_threshold=-2.0"
curl.exe -X POST "$BASE/config/update_all" -H "Content-Type: application/json" -d '{"capital_usdt":100,"risk_factor":0.05,"egm_buy_threshold":0.10,"egm_sell_threshold":-0.10,"combined_buy_threshold":2.0,"combined_sell_threshold":-2.0}'

Operaciones (ciclo/bot/HFT) – Recomendación segura: usar collect_only=true

curl.exe -X POST "$BASE/execute_trade/$SYMBOL?collect_only=true&force_trade=false"
curl.exe -X POST "$BASE/hft/start/$SYMBOL?interval_ms=250&collect_only=true"
curl.exe -X POST "$BASE/hft/stop/$SYMBOL"
curl.exe -X POST "$BASE/hft/run/$SYMBOL?cycles=100&interval_ms=250&collect_only=true"
curl.exe "$BASE/balance?account_type=UNIFIED&coin=USDT"
curl.exe -X POST "$BASE/start"
curl.exe -X POST "$BASE/stop"

Estado

curl.exe "$BASE/status"
curl.exe "$BASE/health"

Secuencia completa de prueba (todo en uno)
Puedes copiar y pegar este bloque completo en PowerShell después de definir las variables:
PowerShell$BASE = "http://localhost:8081"
$SYMBOL = "BTCUSDT"

curl.exe "$BASE/health"
curl.exe "$BASE/status"
curl.exe "$BASE/support/status"
curl.exe -X POST "$BASE/support/start?interval_s=2.0"
curl.exe -X POST "$BASE/support/reconcile_now?symbol=$SYMBOL"
curl.exe "$BASE/config"
curl.exe "$BASE/settings"
curl.exe "$BASE/market_data/$SYMBOL"
curl.exe "$BASE/ticker/$SYMBOL"
curl.exe "$BASE/metrics/$SYMBOL"
curl.exe "$BASE/orderbook/$SYMBOL"
curl.exe "$BASE/exchange/open_orders/$SYMBOL"
curl.exe "$BASE/candles/$SYMBOL/5"
curl.exe "$BASE/trades/$SYMBOL"
curl.exe "$BASE/last_trade/$SYMBOL"
curl.exe "$BASE/profit"
curl.exe "$BASE/calibrate/$SYMBOL?lookback=600&target_signal_rate=0.06"
curl.exe -X POST "$BASE/execute_trade/$SYMBOL?collect_only=true&force_trade=false"
curl.exe -X POST "$BASE/hft/start/$SYMBOL?interval_ms=250&collect_only=true"
curl.exe -X POST "$BASE/hft/stop/$SYMBOL"
curl.exe "$BASE/balance?account_type=UNIFIED&coin=USDT"
curl.exe -X POST "$BASE/support/stop"