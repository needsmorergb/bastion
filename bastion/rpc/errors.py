"""Typed error hierarchy shared by ``bastion.rpc.client`` and ``bastion.rpc.ws``.

Every raised instance carries only a plain message string — never include
secret material (private keys, passphrases, vault secrets) in any error
string raised from this module or its subclasses.
"""


class RpcError(Exception):
    """Base class for all Solana RPC/WS transport failures."""


class RpcRateLimitError(RpcError):
    """Raised when the retry/backoff budget is exhausted after repeated
    429 (rate-limited) responses from the RPC endpoint."""


class RpcTimeoutError(RpcError):
    """Raised when a request or a liveness check (e.g. a WS heartbeat)
    times out without a response."""
