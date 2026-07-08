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
    """
    elapsed = 0.0
    while elapsed < budget_s:
        statuses = await rpc.get_signature_statuses([signature], search_history=True)
        status = statuses["value"][0]
        if status is not None:
            if status.get("err") is not None:
                raise RpcError(
                    f"transaction {signature} failed on-chain: {status['err']}"
                )
            if status.get("confirmationStatus") in ("confirmed", "finalized"):
                return  # D-09: confirmed is sufficient
        else:
            # Unknown, not failed (Pitfall 2). Re-POST the IDENTICAL blob —
            # never rebuild/re-sign. Solana dedups by signature within the
            # blockhash window, so this cannot double-spend.
            await rpc.send_raw(signed_b64)
        await asyncio.sleep(poll_interval_s)
        elapsed += poll_interval_s

    raise RpcTimeoutError(
        f"land-check for {signature} exceeded {budget_s}s without a terminal status"
    )
