from __future__ import annotations

import logging
import time
from urllib.request import Request, urlopen

log = logging.getLogger("gaswatch.price")

ENDPOINTS = [
    ("https://api.coingecko.com/api/v3/simple/price?ids=ethereum&vs_currencies=usd", "gecko"),
    ("https://api.coinbase.com/v2/prices/ETH-USD/spot", "coinbase"),
    ("https://api.binance.com/api/v3/ticker/price?symbol=ETHUSDT", "binance"),
]


class PriceClient:
    def __init__(self, timeout: float = 4.0, ttl: float = 60.0) -> None:
        self.timeout = timeout
        self.ttl = ttl
        self._value: float | None = None
        self._stamp = 0.0

    def eth_usd(self) -> float | None:
        if self._value is not None and (time.monotonic() - self._stamp) < self.ttl:
            return self._value
        errors = []
        for url, kind in ENDPOINTS:
            try:
                request = Request(url, headers={"User-Agent": "gaswatch/1.0", "Accept": "application/json"})
                with urlopen(request, timeout=self.timeout) as response:
                    payload = response.read()
                value = _extract(kind, payload)
                if value and value > 0:
                    self._value = value
                    self._stamp = time.monotonic()
                    return value
            except Exception as exc:
                errors.append(f"{kind}: {type(exc).__name__}")
        log.info("price providers failed: %s", ", ".join(errors) or "none")
        return self._value

    def usd(self, wei: float | None) -> float | None:
        if wei is None:
            return None
        price = self.eth_usd()
        if not price:
            return None
        return round(price * float(wei) / 1e18, 2)


def _extract(kind: str, payload: bytes) -> float | None:
    import json

    data = json.loads(payload.decode("utf-8", "replace"))
    if kind == "gecko":
        return float(data["ethereum"]["usd"])
    if kind == "coinbase":
        return float(data["data"]["amount"])
    if kind == "binance":
        return float(data["price"])
    return None