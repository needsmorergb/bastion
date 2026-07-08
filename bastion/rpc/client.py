"""Thin, async-first JSON-RPC client over ``httpx.AsyncClient`` for the
Solana RPC surface Bastion needs (D-03, D-04).

Domain-blind transport: pubkeys and signed base64 blobs go in, public JSON
comes out. This module never touches a private key, a passphrase, or any
other secret material, and never logs a request/response body that could
contain a signed transaction.

Every RPC method routes through a single retry/backoff wrapper
(:meth:`RpcClient._request_with_backoff`) that honors the ``Retry-After``
header on 429/transient-5xx responses, falls back to bounded exponential
backoff with jitter otherwise, and raises the typed
:class:`~bastion.rpc.errors.RpcRateLimitError` when the ~30s budget is
exhausted — never an infinite hang (D-05).

Sync wrappers (module-level functions, e.g. :func:`get_balance_sync`) are
provided for one-shot CLI call sites. They call ``asyncio.run()`` at the
true top level and must NEVER be invoked from inside an already-running
event loop (Pitfall 1) — only call them from a plain synchronous CLI
entrypoint, never from ``monitor.py`` or any other async function.
"""

from __future__ import annotations

import asyncio
import random

import httpx

from bastion.rpc.errors import RpcError, RpcRateLimitError, RpcTimeoutError

# Helius free-tier retry guidance (helius.dev/docs/billing/rate-limits):
# wait ~1s before first retry, double each time up to 30s max, +-25% jitter.
_DEFAULT_MAX_WAIT_S = 30.0
# sendTransaction-class calls get a tighter budget per D-05 — the free-tier
# sendTransaction rate is far more constrained (~1/sec) than routine polling.
_SEND_TX_MAX_WAIT_S = 10.0
_RETRYABLE_STATUS_CODES = (429, 502, 503, 504)
# Floor every retry wait to a small positive minimum (H-1, T-01-07 hardening).
# A hostile/misbehaving endpoint answering 429 with `Retry-After: 0` (or a
# negative value) would otherwise leave `wait == 0`, so `elapsed` never advances,
# the budget guard never trips, and the loop spins forever. Flooring guarantees
# `elapsed` always grows so the `max_wait_s` budget is always eventually reached.
_MIN_RETRY_WAIT_S = 0.05


class RpcClient:
    """Thin JSON-RPC caller wrapping an injected ``httpx.AsyncClient``.

    The client is injected (not constructed internally) so tests can supply
    a respx-backed ``httpx.AsyncClient`` bound to a fixed ``base_url``.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client
        self._next_id = 1

    async def _request_with_backoff(
        self, payload: dict, *, max_wait_s: float = _DEFAULT_MAX_WAIT_S
    ) -> httpx.Response:
        """Single retry/backoff wrapper every method routes through.

        On 429 or a transient 5xx (502/503/504): honor ``Retry-After`` (in
        seconds) when present, else use exponential backoff with +-25%
        jitter. Track elapsed time against ``max_wait_s`` and raise
        RpcRateLimitError once the budget would be exceeded, rather than
        sleeping past it (bounded — never an infinite hang, D-05).
        """
        attempt = 0
        elapsed = 0.0
        while True:
            try:
                resp = await self._client.post("", json=payload)
            except httpx.TimeoutException as exc:
                raise RpcTimeoutError(f"RPC request timed out: {exc}") from exc
            except httpx.HTTPError as exc:
                raise RpcError(f"RPC transport error: {exc}") from exc

            if resp.status_code not in _RETRYABLE_STATUS_CODES:
                return resp

            retry_after = resp.headers.get("Retry-After")
            if retry_after is not None:
                try:
                    wait = float(retry_after)
                except ValueError:
                    wait = min(2**attempt, max_wait_s)
            else:
                base = min(2**attempt, max_wait_s)
                wait = base * (0.75 + random.random() * 0.5)  # +-25% jitter

            # H-1: never let a zero/negative Retry-After stall (or spin) the loop.
            wait = max(wait, _MIN_RETRY_WAIT_S)

            if elapsed + wait > max_wait_s:
                raise RpcRateLimitError(
                    f"exhausted retry budget after {elapsed:.1f}s "
                    f"(status {resp.status_code})"
                )
            await asyncio.sleep(wait)
            elapsed += wait
            attempt += 1

    async def call(
        self, method: str, params: list, *, max_wait_s: float = _DEFAULT_MAX_WAIT_S
    ) -> object:
        """Public JSON-RPC entry point. Returns the parsed ``result`` field.

        Raises a typed :class:`~bastion.rpc.errors.RpcError` (or a subclass)
        rather than leaking a raw ``httpx`` exception or an unparsed error
        body.
        """
        request_id = self._next_id
        self._next_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        resp = await self._request_with_backoff(payload, max_wait_s=max_wait_s)

        if resp.status_code != 200:
            raise RpcError(
                f"RPC call {method!r} failed with status {resp.status_code}"
            )

        try:
            body = resp.json()
        except ValueError as exc:
            raise RpcError(f"RPC call {method!r} returned malformed JSON") from exc

        if "error" in body:
            raise RpcError(f"RPC call {method!r} returned an error: {body['error']}")
        if "result" not in body:
            raise RpcError(f"RPC call {method!r} response missing 'result' field")
        return body["result"]

    async def get_balance(self, pubkey: str) -> int:
        """Return the lamport balance of ``pubkey``."""
        result = await self.call("getBalance", [pubkey])
        # getBalance's result is either {"context": ..., "value": N} or,
        # under some mocked/simplified test payloads, a bare int. Accept both
        # so tests can queue a minimal canned response.
        if isinstance(result, dict):
            return result["value"]
        return result

    async def get_latest_blockhash(self) -> object:
        """Return the raw ``result`` of ``getLatestBlockhash``."""
        return await self.call("getLatestBlockhash", [])

    async def get_signatures(
        self,
        pubkey: str,
        *,
        before: str | None = None,
        until: str | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        """Paginate ``getSignaturesForAddress`` via a ``before`` cursor.

        Pages backward, accumulating every page without truncation, until a
        page returns fewer than ``limit`` results (Pattern 5 / D-07).
        ``before``/``until`` are both exclusive cursors and both accept
        ``None``.
        """
        all_sigs: list[dict] = []
        cursor_before = before
        while True:
            batch = await self.call(
                "getSignaturesForAddress",
                [
                    pubkey,
                    {"before": cursor_before, "until": until, "limit": limit},
                ],
            )
            if not batch:
                break
            all_sigs.extend(batch)
            cursor_before = batch[-1]["signature"]
            if len(batch) < limit:
                break  # short page == end of available history
        return all_sigs

    async def get_transaction(self, signature: str) -> object:
        """Return the raw ``result`` of ``getTransaction`` for ``signature``."""
        return await self.call("getTransaction", [signature])

    async def get_fee_for_message(
        self, message_b64: str, *, commitment: str = "confirmed"
    ) -> object:
        """Return the raw ``result`` of ``getFeeForMessage``.

        ``getFeeForMessage`` defaults its own ``commitment`` to
        ``"finalized"`` when not explicitly specified — this method always
        passes ``commitment`` through the params dict explicitly (default
        ``"confirmed"``) so the RPC's own default is never silently
        inherited (Pitfall 4).
        """
        return await self.call(
            "getFeeForMessage", [message_b64, {"commitment": commitment}]
        )

    async def get_signature_statuses(
        self, signatures: list[str], *, search_history: bool = False
    ) -> object:
        """Return the raw ``result`` of ``getSignatureStatuses``.

        Point-in-time only — no ``commitment`` param (statuses already carry
        their own ``confirmationStatus`` per signature).
        """
        return await self.call(
            "getSignatureStatuses",
            [signatures, {"searchTransactionHistory": search_history}],
        )

    async def get_token_accounts_by_owner(self, owner_pubkey: str) -> list[dict]:
        """Return the jsonParsed SPL Token accounts owned by ``owner_pubkey``.

        Issues ``getTokenAccountsByOwner`` scoped to the SPL Token program
        (``TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA``) with
        ``{"encoding": "jsonParsed"}`` and returns ``result["value"]`` (a
        list of account dicts) directly — used by the sweeper (D-06) and
        the session retire guard (D-10) to classify empty vs nonzero ATAs.
        """
        result = await self.call(
            "getTokenAccountsByOwner",
            [
                owner_pubkey,
                {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                {"encoding": "jsonParsed"},
            ],
        )
        return result["value"]

    async def send_raw(self, signed_tx_b64: str) -> object:
        """Issue ``sendTransaction`` carrying the base64-encoded signed blob.

        sendTransaction-class calls use a tighter retry budget than routine
        polling calls (D-05) — the free-tier send rate is far more
        constrained. Never logs ``signed_tx_b64`` at any level.
        """
        return await self.call(
            "sendTransaction", [signed_tx_b64], max_wait_s=_SEND_TX_MAX_WAIT_S
        )


async def _get_balance_async(base_url: str, pubkey: str) -> int:
    async with httpx.AsyncClient(base_url=base_url) as client:
        rpc = RpcClient(client)
        return await rpc.get_balance(pubkey)


def get_balance_sync(base_url: str, pubkey: str) -> int:
    """Thin sync wrapper around :meth:`RpcClient.get_balance` for one-shot
    CLI call sites (e.g. ``start``/``status``).

    Calls ``asyncio.run()`` at the true top level — only call this from a
    plain synchronous CLI entrypoint, never from within an already-running
    event loop (Pitfall 1: ``asyncio.run()`` raises ``RuntimeError`` if
    nested inside a running loop).
    """
    return asyncio.run(_get_balance_async(base_url, pubkey))
