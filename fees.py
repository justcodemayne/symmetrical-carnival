from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from .config import GWEI, Config
from .exceptions import RPCRequestError
from .rpc import RPCPool, check_payload
from .thresholds import GasLevel, ThresholdSet

log = logging.getLogger("gaswatch.fees")

WEI = 10**18

COST_TEMPLATES = [
    ("ETH transfer", 21_000),
    ("Token swap", 150_000),
    ("NFT mint", 120_000),
    ("Contract deploy", 500_000),
]


def to_gwei(wei: float | None) -> float | None:
    if wei is None:
        return None
    return round(float(wei) / GWEI, 6)


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


@dataclass
class TipStats:
    low: float | None = None
    median: float | None = None
    high: float | None = None
    suggested: float | None = None
    samples: int = 0

    def to_dict(self) -> dict:
        return {
            "low_gwei": self.low,
            "median_gwei": self.median,
            "high_gwei": self.high,
            "suggested_gwei": self.suggested,
            "samples": self.samples,
        }


@dataclass
class FeeSnapshot:
    base_fee_wei: int
    tip: TipStats
    endpoint: str
    block_number: int | None = None
    block_timestamp: int | None = None
    gas_used: int | None = None
    gas_limit: int | None = None
    next_base_fee_wei: int | None = None
    min_base_fee_wei: int | None = None
    max_base_fee_wei: int | None = None
    history_blocks: int = 0
    fetched_at: float = field(default_factory=time.time)
    eth_price_usd: float | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def base_fee_gwei(self) -> float:
        return round(self.base_fee_wei / GWEI, 6)

    @property
    def next_base_fee_gwei(self) -> float | None:
        return to_gwei(self.next_base_fee_wei)

    @property
    def tip_gwei(self) -> float | None:
        return self.tip.suggested

    @property
    def tip_median_gwei(self) -> float | None:
        return self.tip.median

    @property
    def max_fee_gwei(self) -> float | None:
        if self.tip_gwei is None:
            return None
        return round(self.base_fee_wei / GWEI + self.tip_gwei, 6)

    @property
    def gas_used_ratio(self) -> float | None:
        if not self.gas_limit or self.gas_used is None:
            return None
        return round(self.gas_used / self.gas_limit, 4)

    @property
    def base_trend_pct(self) -> float | None:
        if self.next_base_fee_wei is None or self.base_fee_wei == 0:
            return None
        return round((self.next_base_fee_wei - self.base_fee_wei) / self.base_fee_wei * 100, 2)

    def level(self, thresholds: ThresholdSet) -> GasLevel:
        return thresholds.classify(self.base_fee_gwei)

    def tip_level(self, thresholds: ThresholdSet) -> GasLevel:
        return thresholds.classify(self.tip_gwei)

    def overall_level(self, base_thresholds: ThresholdSet, tip_thresholds: ThresholdSet) -> GasLevel:
        levels = [self.level(base_thresholds), self.tip_level(tip_thresholds)]
        return max(levels, key=lambda lvl: lvl.sort_index)

    def costs_wei(self) -> list[tuple[str, int, float | None]]:
        total = self.max_fee_gwei
        if total is None:
            return []
        return [(label, gas, round(gas * total * 1e-9 * WEI, 6)) for label, gas in COST_TEMPLATES]

    def to_dict(self) -> dict:
        return {
            "base_fee_gwei": self.base_fee_gwei,
            "next_base_fee_gwei": self.next_base_fee_gwei,
            "base_trend_pct": self.base_trend_pct,
            "tip": self.tip.to_dict(),
            "max_fee_gwei": self.max_fee_gwei,
            "gas_used_ratio": self.gas_used_ratio,
            "gas_used": self.gas_used,
            "gas_limit": self.gas_limit,
            "block_number": self.block_number,
            "block_timestamp": self.block_timestamp,
            "min_base_fee_gwei": to_gwei(self.min_base_fee_wei),
            "max_base_fee_gwei": to_gwei(self.max_base_fee_wei),
            "history_blocks": self.history_blocks,
            "endpoint": self.endpoint,
            "fetched_at": self.fetched_at,
            "eth_price_usd": self.eth_price_usd,
            "warnings": list(self.warnings),
        }


class GasService:
    def __init__(self, config: Config, pool: RPCPool | None = None, price_client=None) -> None:
        self.config = config
        self.pool = pool or RPCPool(config)
        self.price_client = price_client
        self.last_snapshot: FeeSnapshot | None = None
        self.last_error: Exception | None = None

    def fetch(self, with_price: bool | None = None) -> FeeSnapshot:
        blocks = self.config.history_blocks
        payload = self.pool.call(_fetch_payload, blocks, self.config)
        snapshot = _build_snapshot(payload, blocks, self.pool)
        self.last_error = None
        self.last_snapshot = snapshot
        if with_price is None:
            with_price = self.config.price_enabled
        if with_price and self.price_client is not None:
            try:
                snapshot.eth_price_usd = self.price_client.eth_usd()
            except Exception as exc:
                log.info("price lookup failed: %s", exc)
                snapshot.warnings.append("ETH/USD price unavailable")
        return snapshot

    def endpoints_report(self) -> list[dict]:
        return [
            {
                "name": h.name,
                "url": h.url,
                "successes": h.successes,
                "failures": h.failures,
                "healthy": h.healthy,
                "cooldown_left": round(h.cooldown_left, 1),
                "last_latency_ms": round(h.last_latency * 1000, 1),
                "last_error": h.last_error,
            }
            for h in self.pool.health_snapshot()
        ]


def _fetch_payload(w3, blocks: int, config: Config) -> dict:
    result: dict = {"endpoint": getattr(w3.provider.endpoint_uri, "__str__", lambda: "")()}
    block = w3.eth.get_block("latest", full_transactions=False)
    base_fee = block.get("baseFeePerGas")
    if base_fee is None:
        raise RPCRequestError("latest block has no baseFeePerGas, the node may be on a pre-EIP-1559 fork")
    result["block"] = block
    result["base_fee"] = int(base_fee)
    result["history"] = None
    result["history_error"] = None
    try:
        history = w3.eth.fee_history(blocks, "latest", [10, 50, 90])
        check_payload(history, "eth_feeHistory")
        bases = [int(v) for v in (history.get("baseFeePerGas") or []) if v is not None]
        rewards = history.get("reward") or []
        result["history"] = {
            "base_fee": bases,
            "rewards": [[int(v) for v in row if v is not None] for row in rewards],
            "oldest_block": int(history.get("oldestBlock") or 0),
            "gas_used_ratio": [float(v) for v in (history.get("gasUsedRatio") or [])],
        }
    except Exception as exc:
        result["history_error"] = f"eth_feeHistory unavailable ({type(exc).__name__}: {exc})"
    try:
        response = w3.provider.make_request("eth_maxPriorityFeePerGas", [])
        value = response.get("result") if isinstance(response, dict) else None
        result["suggested_tip"] = int(value, 16) if isinstance(value, str) else None
    except Exception as exc:
        result["suggested_tip"] = None
        result["tip_error"] = f"eth_maxPriorityFeePerGas failed ({type(exc).__name__}: {exc})"
    return result


def _build_snapshot(payload: dict, blocks: int, pool: RPCPool) -> FeeSnapshot:
    block = payload["block"]
    warnings: list[str] = []
    history = payload.get("history")
    tip = TipStats()
    base_values: list[int] = []
    next_base: int | None = None

    if history:
        bases = history["base_fee"]
        base_values = [b for b in bases[:-1] if b is not None]
        if len(bases) >= 2 and bases[-1] is not None:
            next_base = bases[-1]
        rewards = history["rewards"]
        low: list[float] = []
        mid: list[float] = []
        high: list[float] = []
        for row in rewards:
            values = [v / GWEI for v in row]
            if not values:
                continue
            if len(values) >= 3:
                low.append(values[0])
                mid.append(values[1])
                high.append(values[2])
            else:
                mid.append(median(values) or 0.0)
        tip.samples = len(mid)
        tip.low = round(median(low), 6) if low else None
        tip.median = round(median(mid), 6) if mid else None
        tip.high = round(median(high), 6) if high else None
    else:
        if payload.get("history_error"):
            warnings.append(payload["history_error"])
        if payload.get("tip_error"):
            warnings.append(payload["tip_error"])

    suggested = tip.median
    rpc_suggestion = payload.get("suggested_tip")
    if rpc_suggestion and rpc_suggestion > 0:
        rpc_suggestion_gwei = round(rpc_suggestion / GWEI, 6)
        if suggested is None:
            suggested = rpc_suggestion_gwei
        else:
            suggested = max(suggested, rpc_suggestion_gwei)
        if tip.high is None:
            tip.high = suggested
        if tip.low is None:
            tip.low = round((suggested or 0) * 0.5, 6)
    if suggested is None:
        suggested = 1.5
        warnings.append("fee history and tip oracle unavailable, using a 1.5 gwei fallback tip")
    tip.suggested = round(suggested, 6)

    if tip.low is None:
        tip.low = round(suggested * 0.5, 6)
    if tip.high is None:
        tip.high = round(suggested * 1.5, 6)

    base_fee = int(payload["base_fee"])
    gas_used = block.get("gasUsed")
    gas_limit = block.get("gasLimit")
    endpoint = ""
    for health in pool.health_snapshot():
        if health.successes:
            endpoint = health.name
            break
    if not endpoint:
        endpoint = pool.endpoints[0] if pool.endpoints else "unknown"

    return FeeSnapshot(
        base_fee_wei=base_fee,
        tip=tip,
        endpoint=endpoint,
        block_number=int(block.get("number")) if block.get("number") is not None else None,
        block_timestamp=int(block.get("timestamp")) if block.get("timestamp") is not None else None,
        gas_used=int(gas_used) if gas_used is not None else None,
        gas_limit=int(gas_limit) if gas_limit is not None else None,
        next_base_fee_wei=next_base,
        min_base_fee_wei=min(base_values) if base_values else None,
        max_base_fee_wei=max(base_values) if base_values else None,
        history_blocks=max(0, len(base_values)),
        warnings=warnings,
    )

def build_service(config: Config, pool: RPCPool | None = None) -> GasService:
    from .price import PriceClient

    price = PriceClient() if (config.price_enabled or config.show_usd) else None
    return GasService(config, pool=pool, price_client=price)
