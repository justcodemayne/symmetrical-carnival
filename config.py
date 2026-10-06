from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .exceptions import ConfigError

GWEI = 10**9

DEFAULT_ENDPOINTS = [
    "https://ethereum-rpc.publicnode.com",
    "https://rpc.flashbots.net",
    "https://eth.drpc.org",
    "https://rpc.mevblocker.io",
]

ENV_MAP = {
    "rpc_endpoints": "GASWATCH_RPC_ENDPOINTS",
    "rpc_timeout": "GASWATCH_RPC_TIMEOUT",
    "rpc_retries": "GASWATCH_RPC_RETRIES",
    "rpc_cooldown": "GASWATCH_RPC_COOLDOWN",
    "history_blocks": "GASWATCH_HISTORY_BLOCKS",
    "base_low_max": "GASWATCH_BASE_LOW_MAX",
    "base_moderate_max": "GASWATCH_BASE_MODERATE_MAX",
    "base_high_max": "GASWATCH_BASE_HIGH_MAX",
    "tip_low_max": "GASWATCH_TIP_LOW_MAX",
    "tip_moderate_max": "GASWATCH_TIP_MODERATE_MAX",
    "tip_high_max": "GASWATCH_TIP_HIGH_MAX",
    "gui_refresh_interval": "GUI_REFRESH_INTERVAL",
    "show_usd": "GASWATCH_SHOW_USD",
    "units": "GASWATCH_UNITS",
    "price_enabled": "GASWATCH_PRICE_ENABLED",
}


def config_home() -> Path:
    override = os.environ.get("GASWATCH_CONFIG_DIR")
    base = Path(override) if override else Path.home() / ".gaswatch"
    base.mkdir(parents=True, exist_ok=True)
    return base


def config_path() -> Path:
    return config_home() / "config.json"


@dataclass
class Config:
    rpc_endpoints: list[str] = field(default_factory=lambda: list(DEFAULT_ENDPOINTS))
    rpc_timeout: float = 12.0
    rpc_retries: int = 2
    rpc_cooldown: float = 45.0
    history_blocks: int = 20
    base_low_max: float = 2.0
    base_moderate_max: float = 8.0
    base_high_max: float = 25.0
    tip_low_max: float = 0.30
    tip_moderate_max: float = 1.50
    tip_high_max: float = 5.0
    gui_refresh_interval: int = 10
    show_usd: bool = False
    price_enabled: bool = False
    units: str = "gwei"

    @property
    def base_thresholds(self):
        from .thresholds import ThresholdSet

        return ThresholdSet(self.base_low_max, self.base_moderate_max, self.base_high_max)

    @property
    def tip_thresholds(self):
        from .thresholds import ThresholdSet

        return ThresholdSet(self.tip_low_max, self.tip_moderate_max, self.tip_high_max)

    def to_dict(self) -> dict:
        return asdict(self)

    def copy(self) -> Config:
        return Config(**self.to_dict())

    def validate(self) -> None:
        if not self.rpc_endpoints:
            raise ConfigError("No RPC endpoints configured. Set GASWATCH_RPC_ENDPOINTS or edit the config file.")
        for url in self.rpc_endpoints:
            if not isinstance(url, str) or not url.startswith(("http://", "https://", "ws://", "wss://")):
                raise ConfigError(f"Invalid RPC endpoint: {url!r}. It must start with http://, https://, ws:// or wss://")
        if self.rpc_timeout <= 0:
            raise ConfigError("rpc_timeout must be greater than 0")
        if self.rpc_retries < 0:
            raise ConfigError("rpc_retries cannot be negative")
        if not 2 <= self.history_blocks <= 1024:
            raise ConfigError("history_blocks must be between 2 and 1024")
        if not self.base_low_max < self.base_moderate_max < self.base_high_max:
            raise ConfigError("base thresholds must satisfy low < moderate < high")
        if not self.tip_low_max < self.tip_moderate_max < self.tip_high_max:
            raise ConfigError("tip thresholds must satisfy low < moderate < high")
        if self.units not in ("gwei", "kwei", "mwei", "wei"):
            raise ConfigError("units must be one of: wei, kwei, mwei, gwei")
        if self.gui_refresh_interval < 3:
            raise ConfigError("gui_refresh_interval must be at least 3 seconds")

    @classmethod
    def coerce(cls, data: dict) -> Config:
        kwargs: dict = {}
        types = {f.name: f.type for f in fields(cls)}
        for key, value in (data or {}).items():
            if key not in types:
                continue
            kwargs[key] = _coerce_value(key, value)
        return cls(**kwargs)

    @classmethod
    def load(cls, use_env: bool = True) -> Config:
        path = config_path()
        data: dict = {}
        if path.exists():
            try:
                raw = path.read_text(encoding="utf-8")
                data = json.loads(raw) if raw.strip() else {}
            except json.JSONDecodeError as exc:
                raise ConfigError(f"{path} is not valid JSON: {exc}") from exc
            except OSError as exc:
                raise ConfigError(f"Cannot read {path}: {exc}") from exc
            if not isinstance(data, dict):
                raise ConfigError(f"{path} must contain a JSON object")
        cfg = cls.coerce(data)
        if use_env:
            cfg = cfg.apply_env(os.environ)
        return cfg

    def apply_env(self, env: dict) -> Config:
        overrides = {}
        for attr, env_name in ENV_MAP.items():
            if env_name in env and str(env[env_name]).strip() != "":
                overrides[attr] = _coerce_value(attr, env[env_name])
        for key, value in overrides.items():
            setattr(self, key, value)
        return self

    def save(self, path: Path | None = None) -> Path:
        target = path or config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        try:
            target.chmod(0o600)
        except OSError:
            pass
        return target


def _coerce_value(key: str, value):
    if key == "rpc_endpoints":
        if isinstance(value, str):
            parts = [p.strip() for p in value.replace(";", ",").split(",")]
            return [p for p in parts if p]
        if isinstance(value, (list, tuple)):
            return [str(p).strip() for p in value if str(p).strip()]
        raise ConfigError(f"rpc_endpoints must be a list or comma separated string, got {type(value).__name__}")
    if key in ("show_usd", "price_enabled"):
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on", "y")
    if key in ("rpc_timeout", "rpc_retries", "rpc_cooldown", "history_blocks", "base_low_max", "base_moderate_max",
               "base_high_max", "tip_low_max", "tip_moderate_max", "tip_high_max",
               "gui_refresh_interval"):
        try:
            return int(value) if key in ("rpc_retries", "history_blocks", "gui_refresh_interval") else float(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{key} must be a number, got {value!r}") from exc
    return str(value)


def load_dotenv_if_present(path: Path | None = None) -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    candidates = [path] if path else [Path.cwd() / ".env", config_home() / ".env"]
    for candidate in candidates:
        if candidate and candidate.exists():
            load_dotenv(candidate, override=False)