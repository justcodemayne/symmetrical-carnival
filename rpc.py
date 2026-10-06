from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager

from .config import Config
from .exceptions import AllEndpointsFailedError, RPCConnectionError, RPCRequestError

log = logging.getLogger("gaswatch.rpc")

BACKOFF_BASE = 0.6

NON_TRANSIENT = (KeyError, IndexError, AttributeError, TypeError, AssertionError, NotImplementedError)


class EndpointHealth:
    __slots__ = ("cooldown_until", "failures", "last_error", "last_latency", "successes", "url")

    def __init__(self, url: str) -> None:
        self.url = url
        self.failures = 0
        self.successes = 0
        self.last_error = ""
        self.last_latency = 0.0
        self.cooldown_until = 0.0

    @property
    def name(self) -> str:
        return pretty_endpoint(self.url)

    @property
    def healthy(self) -> bool:
        return time.monotonic() >= self.cooldown_until

    @property
    def cooldown_left(self) -> float:
        return max(0.0, self.cooldown_until - time.monotonic())


def pretty_endpoint(url: str) -> str:
    from urllib.parse import urlparse

    try:
        return (urlparse(url).hostname or url).replace("www.", "")
    except ValueError:
        return url


def provider_for(url: str, timeout: float):
    from web3 import Web3

    if url.startswith(("ws://", "wss://")):
        from web3.providers.rpc import WebsocketProvider

        return Web3(WebsocketProvider(url, request_kwargs={"timeout": timeout}))
    from web3.providers.rpc import HTTPProvider

    return Web3(HTTPProvider(url, request_kwargs={"timeout": timeout}))


def describe(exc: BaseException | None) -> str:
    if exc is None:
        return "unknown error"
    text = str(exc).strip()
    name = type(exc).__name__
    if not text:
        return name
    if len(text) > 160:
        text = text[:157] + "..."
    return f"{name}: {text}"


class RPCPool:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._lock = threading.RLock()
        self._health: dict[str, EndpointHealth] = {url: EndpointHealth(url) for url in config.rpc_endpoints}

    @property
    def endpoints(self) -> list[str]:
        return list(self.config.rpc_endpoints)

    def health_snapshot(self) -> list[EndpointHealth]:
        with self._lock:
            items = list(self._health.values())
        return sorted(items, key=lambda h: (-h.successes, h.failures, h.url))

    def register_endpoint(self, url: str) -> None:
        url = (url or "").strip()
        if not url:
            return
        with self._lock:
            if url not in self._health:
                self._health[url] = EndpointHealth(url)
            if url not in self.config.rpc_endpoints:
                self.config.rpc_endpoints.append(url)

    def _ordered(self) -> list[EndpointHealth]:
        with self._lock:
            items = list(self._health.values())
        ready = [h for h in items if h.healthy]
        cooling = [h for h in items if not h.healthy]
        ready.sort(key=lambda h: (-h.successes, h.failures, h.last_latency))
        cooling.sort(key=lambda h: h.cooldown_until)
        return ready + cooling

    def _mark_success(self, health: EndpointHealth, latency: float) -> None:
        with self._lock:
            health.successes += 1
            health.failures = 0
            health.last_error = ""
            health.last_latency = latency
            health.cooldown_until = 0.0

    def _mark_failure(self, health: EndpointHealth, reason: str) -> None:
        with self._lock:
            health.failures += 1
            health.last_error = reason[:200]
            health.cooldown_until = time.monotonic() + min(
                max(BACKOFF_BASE, self.config.rpc_cooldown) * health.failures, 300.0
            )
        log.warning("rpc %s failed (%s), cooling down %.0fs", health.name, reason, health.cooldown_left)

    @contextmanager
    def connect(self):
        failures: list[tuple[str, str]] = []
        if not self.config.rpc_endpoints:
            raise AllEndpointsFailedError([("config", "no endpoints configured")])
        for health in self._ordered():
            w3 = None
            handed_over = False
            try:
                w3 = provider_for(health.url, self.config.rpc_timeout)
                started = time.monotonic()
                if not w3.is_connected():
                    raise RPCConnectionError("handshake returned false", endpoint=health.url)
                self._mark_success(health, time.monotonic() - started)
                handed_over = True
                yield w3
                return
            except Exception as exc:
                if handed_over:
                    raise
                reason = describe(exc)
                self._mark_failure(health, reason)
                failures.append((health.name, reason))
            finally:
                self._close(w3)
        raise AllEndpointsFailedError(failures)

    @staticmethod
    def _close(w3) -> None:
        if w3 is None:
            return
        provider = getattr(w3, "provider", None)
        for attr in ("disconnect", "close"):
            fn = getattr(provider, attr, None)
            if callable(fn):
                try:
                    fn()
                except Exception:
                    pass
        for attr in ("_session", "session"):
            session = getattr(provider, attr, None)
            closer = getattr(session, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:
                    pass

    def call(self, fn, *args, **kwargs):
        attempts = max(1, self.config.rpc_retries + 1)
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                with self.connect() as w3:
                    return fn(w3, *args, **kwargs)
            except Exception as exc:
                if isinstance(exc, NON_TRANSIENT):
                    raise
                last = exc
            if attempt < attempts:
                delay = BACKOFF_BASE * (2 ** (attempt - 1))
                log.info("attempt %d/%d failed (%s), retry in %.2fs", attempt, attempts, describe(last), delay)
                time.sleep(delay)
        if isinstance(last, AllEndpointsFailedError):
            raise last
        raise RPCConnectionError(f"RPC call failed after {attempts} attempts: {describe(last)}") from last


def check_payload(value, label: str) -> None:
    if value is None:
        raise RPCRequestError(f"{label} came back empty")
    if isinstance(value, (list, tuple)) and not value:
        raise RPCRequestError(f"{label} came back as an empty list")