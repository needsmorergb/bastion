"""Chain-based confirmation loop shared by ``funder.py`` and (Phase 3's
later plan) ``sweeper.py`` (D-08/D-09).

A top-level sibling of ``funder.py``/``sweeper.py`` — deliberately NOT
folded into ``funder.py`` — so the sweeper can depend on this module's
land-check without ever importing the vault-privileged funder module
(SEC-02).

Isolation from the double-spend problem, not from the vault: this module
holds no secret and imports neither ``bastion.keystore.vault`` nor
``bastion.keystore.session``. It is pure caller-side confirmation *policy*
sitting on top of :class:`~bastion.rpc.client.RpcClient`.

Per D-08, this loop NEVER re-signs a transaction. A signed transaction is
produced exactly once by the caller; `land_check` only ever re-POSTs that
same identical base64 blob (Solana's leader/validator set dedups sends by
signature within the blockhash validity window, so re-sending identical
bytes cannot double-spend) or polls the existing signature's status. Only
a caller that observes a *provable* blockhash expiry (outside this
function's scope) may safely rebuild-and-resign a fresh transaction.

The signed transaction blob is never logged at any level, mirroring the
`RpcClient.send_raw` contract.
"""

from __future__ import annotations

import asyncio

from bastion.rpc.client import RpcClient
from bastion.rpc.errors import RpcError, RpcTimeoutError


async def land_check(
    rpc: RpcClient,
    signature: str,
    signed_b64: str,
    *,
    poll_interval_s: float = 1.5,
    budget_s: float = 90.0,
) -> None:
    """Block until ``signature`` reaches ``confirmed``/``finalized`` (D-09).

    Polls ``getSignatureStatuses`` on a fixed interval. A ``None`` status
    entry means "unknown/not yet seen" (Pitfall 2) — NOT failure — and
    triggers a re-POST of the identical ``signed_b64`` blob, never a
    rebuild/re-sign (D-08). An explicit non-null ``err`` raises
    :class:`~bastion.rpc.errors.RpcError`. If ``budget_s`` elapses without
    a terminal status, raises :class:`~bastion.rpc.errors.RpcTimeoutError`
    so the loop always terminates.

    CR-01: the status poll and the best-effort resend are each individually
    best-effort against *transport*-level failures (rate limits, a resend
    rejected because its blockhash has since expired, etc.) — neither may
    abort the loop before ``budget_s`` is exhausted, because a transport
    failure on either call says nothing about whether the original send
    already landed. Only an explicit on-chain ``err`` observed in a
    successfully-fetched status is treated as an immediate, authoritative
    failure.
    """
    elapsed = 0.0
    while elapsed < budget_s:
        try:
            statuses = await rpc.get_signature_statuses([signature], search_history=True)
        except RpcError:
            # Transient transport/rate-limit failure on the status poll
            # itself (not an on-chain result) — do not let it abort the
            # loop before the budget is exhausted; just retry next poll.
            statuses = None

        if statuses is not None:
            status = statuses["value"][0]
            if status is not None:
                if status.get("err") is not None:
                    # Authoritative: a successfully-fetched status with a
                    # non-null err is a genuine on-chain failure — raise
                    # immediately, never swallowed by the transport-level
                    # best-effort handling above or below.
                    raise RpcError(
                        f"transaction {signature} failed on-chain: {status['err']}"
                    )
                if status.get("confirmationStatus") in ("confirmed", "finalized"):
                    return  # D-09: confirmed is sufficient
            else:
                # Unknown, not failed (Pitfall 2). Re-POST the IDENTICAL
                # blob — never rebuild/re-sign. Solana dedups by signature
                # within the blockhash window, so this cannot double-spend.
                # A routine, expected failure here (e.g. "Blockhash not
                # found" once the tx's blockhash has aged out) does NOT
                # mean the original send failed — it may have already
                # landed. Swallow it and let the next status poll be
                # authoritative instead of raising out of the loop.
                try:
                    await rpc.send_raw(signed_b64)
                except RpcError:
                    pass
        await asyncio.sleep(poll_interval_s)
        elapsed += poll_interval_s

    raise RpcTimeoutError(
        f"land-check for {signature} exceeded {budget_s}s without a terminal status"
    )
