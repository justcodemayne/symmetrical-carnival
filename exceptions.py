from __future__ import annotations


class GasWatchError(Exception):
    pass


class ConfigError(GasWatchError):
    pass


class RPCError(GasWatchError):
    def __init__(self, message: str, endpoint: str | None = None, cause: BaseException | None = None) -> None:
        super().__init__(message)
        self.endpoint = endpoint
        self.__cause__ = cause


class RPCConnectionError(RPCError):
    pass


class RPCRequestError(RPCError):
    pass


class AllEndpointsFailedError(RPCError):
    def __init__(self, failures: list[tuple[str, str]]) -> None:
        detail = "; ".join(f"{name}: {reason}" for name, reason in failures)
        super().__init__(f"all RPC endpoints failed ({detail})")
        self.failures = failures


class DeadlineExceeded(GasWatchError):
    pass


def friendly_rpc_message(exc: BaseException) -> str:
    import requests

    try:
        from web3.exceptions import (
            BlockNotFound,
            InvalidResponse,
            TransactionNotFound,
            Web3Exception,
            Web3ValueError,
        )
    except ImportError:
        class _MissingWeb3Error(Exception):
            pass

        BlockNotFound = InvalidResponse = TransactionNotFound = _MissingWeb3Error
        Web3ValueError = Web3Exception = _MissingWeb3Error

    if isinstance(exc, AllEndpointsFailedError):
        if len(exc.failures) == 1:
            return f"RPC node unreachable: {exc.failures[0][1]}"
        first = exc.failures[0][1] if exc.failures else "no response"
        return f"Could not reach any of the {len(exc.failures)} Ethereum RPC nodes. Last error: {first}"
    if isinstance(exc, requests.exceptions.Timeout):
        return "The RPC node did not respond in time (network timeout)."
    if isinstance(exc, requests.exceptions.SSLError):
        return "TLS handshake with the RPC node failed. The endpoint may be down or misconfigured."
    if isinstance(exc, requests.exceptions.ProxyError):
        return "A proxy is blocking the connection to the RPC node."
    if isinstance(exc, requests.exceptions.ConnectionError):
        return "Connection to the RPC node was refused or reset. Check the network or switch RPC endpoint."
    if isinstance(exc, (InvalidResponse, ValueError, TypeError)):
        return f"The RPC node returned a malformed response: {exc}"
    if isinstance(exc, (BlockNotFound, TransactionNotFound)):
        return "The RPC node does not have that block data available yet."
    if isinstance(exc, Web3ValueError):
        return f"Unexpected value from the RPC node: {exc}"
    if isinstance(exc, Web3Exception):
        return f"RPC node error: {exc}"
    if isinstance(exc, DeadlineExceeded):
        return "The request timed out before the RPC node answered."
    if isinstance(exc, TimeoutError):
        return "The request timed out before the RPC node answered."
    if isinstance(exc, OSError):
        return f"Network error: {exc}"
    return f"Unexpected RPC failure: {type(exc).__name__}: {exc}"