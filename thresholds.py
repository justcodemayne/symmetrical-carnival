from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class GasLevel(Enum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    EXTREME = "EXTREME"
    UNKNOWN = "UNKNOWN"

    @property
    def emoji(self) -> str:
        return _EMOJI[self]

    @property
    def color(self) -> str:
        return _COLOR[self]

    @property
    def ansi(self) -> str:
        return _ANSI[self]

    @property
    def sort_index(self) -> int:
        return _SORT[self]


_EMOJI = {
    GasLevel.LOW: "\U0001f7e2",
    GasLevel.MODERATE: "\U0001f7e1",
    GasLevel.HIGH: "\U0001f7e0",
    GasLevel.EXTREME: "\U0001f534",
    GasLevel.UNKNOWN: "⚪",
}

_COLOR = {
    GasLevel.LOW: "#22c55e",
    GasLevel.MODERATE: "#eab308",
    GasLevel.HIGH: "#f97316",
    GasLevel.EXTREME: "#ef4444",
    GasLevel.UNKNOWN: "#94a3b8",
}

_ANSI = {
    GasLevel.LOW: "\033[92m",
    GasLevel.MODERATE: "\033[93m",
    GasLevel.HIGH: "\033[38;5;208m",
    GasLevel.EXTREME: "\033[91m",
    GasLevel.UNKNOWN: "\033[90m",
}

_SORT = {
    GasLevel.UNKNOWN: 0,
    GasLevel.LOW: 1,
    GasLevel.MODERATE: 2,
    GasLevel.HIGH: 3,
    GasLevel.EXTREME: 4,
}

RESET = "\033[0m"

METER_CHARS = 8
METER_FULL = "\u2588"
METER_EMPTY = "\u2591"


@dataclass(frozen=True)
class ThresholdSet:
    low_max: float
    moderate_max: float
    high_max: float

    def classify(self, gwei: float | None) -> GasLevel:
        if gwei is None:
            return GasLevel.UNKNOWN
        if gwei < self.low_max:
            return GasLevel.LOW
        if gwei < self.moderate_max:
            return GasLevel.MODERATE
        if gwei < self.high_max:
            return GasLevel.HIGH
        return GasLevel.EXTREME

    def meter(self, gwei: float | None) -> str:
        if gwei is None:
            return METER_EMPTY * METER_CHARS
        span = self.high_max * 1.5
        ratio = (gwei / span) if span > 0 else 0.0
        filled = int(ratio * METER_CHARS) + 1
        filled = max(1, min(METER_CHARS, filled))
        return METER_FULL * filled + METER_EMPTY * (METER_CHARS - filled)


BASE_THRESHOLDS = ThresholdSet(low_max=2.0, moderate_max=8.0, high_max=25.0)
TIP_THRESHOLDS = ThresholdSet(low_max=0.30, moderate_max=1.50, high_max=5.0)

ADVICE = {
    GasLevel.LOW: "Great time to send. Fees are near the floor of the protocol.",
    GasLevel.MODERATE: "Normal conditions. Fine for transfers, swaps and minting.",
    GasLevel.HIGH: "Busy network. Prefer batching or waiting for the next dip.",
    GasLevel.EXTREME: "Very congested. Sending now is expensive; consider waiting.",
    GasLevel.UNKNOWN: "Fee data is unavailable right now.",
}