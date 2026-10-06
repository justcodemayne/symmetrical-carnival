from __future__ import annotations

__version__ = "1.0.0"

from .config import Config
from .exceptions import (
    AllEndpointsFailedError,
    ConfigError,
    DeadlineExceeded,
    GasWatchError,
    RPCConnectionError,
    RPCError,
    RPCRequestError,
)
from .fees import FeeSnapshot, GasService, TipStats
from .thresholds import GasLevel, ThresholdSet

__all__ = [
    "AllEndpointsFailedError",
    "Config",
    "ConfigError",
    "DeadlineExceeded",
    "FeeSnapshot",
    "GasLevel",
    "GasService",
    "GasWatchError",
    "RPCConnectionError",
    "RPCError",
    "RPCRequestError",
    "ThresholdSet",
    "TipStats",
    "__version__",
]