#!/usr/bin/env python3
import json
import sys
import time
import urllib.error
import urllib.request


BASE_URL = "http://localhost:8081"
SYMBOL = "BTCUSDT"
TIMEOUT_S = 12


def request(method: str, path: str):
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url, method=method)
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as response:
            raw = response.read().decode("utf-8", errors="replace")
            elapsed_ms = int((time.time() - started) * 1000)
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = raw[:500]
            return {
                "ok": 200 <= response.status < 300,
                "status": response.status,
                "elapsed_ms": elapsed_ms,
                "payload": payload,
            }
    except urllib.error.HTTPError as exc:
        elapsed_ms = int((time.time() - started) * 1000)
        body = exc.read().decode("utf-8", errors="replace")
        return {"ok": False, "status": exc.code, "elapsed_ms": elapsed_ms, "payload": body[:500]}
    except Exception as exc:
        elapsed_ms = int((time.time() - started) * 1000)
        return {"ok": False, "status": None, "elapsed_ms": elapsed_ms, "payload": str(exc)}


def summarize(name: str, result: dict) -> None:
    marker = "PASS" if result["ok"] else "FAIL"
    print(f"{marker:4} {name:32} status={result['status']} time={result['elapsed_ms']}ms")

    payload = result.get("payload")
    if isinstance(payload, dict):
        if "message" in payload:
            print(f"     message={payload['message']}")
        if "status" in payload:
            print(f"     status={payload['status']}")
        if "running" in payload:
            print(f"     running={payload['running']}")
        if "ok" in payload:
            print(f"     ok={payload['ok']}")


def main() -> int:
    checks = [
        ("health", "GET", "/health"),
        ("status", "GET", "/status"),
        ("config", "GET", "/config"),
        ("validation", "GET", "/validation"),
        ("ticker", "GET", f"/ticker/{SYMBOL}"),
        ("metrics", "GET", f"/metrics/{SYMBOL}"),
        ("discovery metrics", "GET", f"/discovery/metrics/{SYMBOL}"),
        ("profit", "GET", "/profit"),
        ("ml status", "GET", "/ml/status"),
        ("collect-only cycle", "POST", f"/execute_trade/{SYMBOL}?collect_only=true"),
    ]

    failures = 0
    print(f"Demo sweep against {BASE_URL} for {SYMBOL}")
    print("-" * 72)
    for name, method, path in checks:
        result = request(method, path)
        summarize(name, result)
        if not result["ok"]:
            failures += 1
    print("-" * 72)
    print(f"Result: {len(checks) - failures}/{len(checks)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
