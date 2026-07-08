"""Capped vault -> session funding — the thinnest possible vault-secret-
privileged code path (SESS-02, SESS-03, SEC-02, D-01..D-04).

Isolation contract (SEC-02): this is the ONLY module besides
``bastion.keystore.vault`` itself that imports ``bastion.keystore.vault``.
No other module under ``bastion/`` may import it — enforced by a static
AST import-graph test, not just a comment — see
``tests/unit/test_keystore_vault_isolation.py``. This module does only:
``load_vault()`` -> build one System transfer to a handed-in destination
pubkey -> sign with the vault key -> send -> land-check (D-02). It does
NOT mint the session keystore (that stays in ``bastion.keystore.session``)
and never needs the session secret, only a destination address.

Both the cap guard (D-03) and the balance guard (D-04) run and raise
BEFORE any network call that could result in a signed transaction
reaching the network — refuse-before-send is literal here.

The vault ``Keypair`` and the base64 signed-tx blob are never logged or
placed in any exception message raised from this module.
"""

from __future__ import annotations

import asyncio
import base64
import math

import httpx
from solders.hash import Hash
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

from bastion.config import Config
from bastion.fund_errors import (
    FunderCapExceededError,
    FunderInsufficientBalanceError,
    FunderInvalidAmountError,
)
from bastion.keystore.vault import load_vault  # the ONE sanctioned import (SEC-02)
from bastion.land_check import land_check
from bastion.rpc.client import RpcClient
from bastion.rpc.errors import RpcError

LAMPORTS_PER_SOL = 1_000_000_000


async def fund_session(
    rpc: RpcClient, config: Config, session_pubkey: str, amount_sol: float
) -> str:
    """Fund ``session_pubkey`` with exactly ``amount_sol`` SOL from the vault.

    The vault is debited ``amount_sol + tx_fee``; the session receives a
    clean, round ``amount_sol`` (D-01). Returns the transaction signature
    once confirmed at commitment ``confirmed`` (D-09).

    Raises (all refuse-before-send — zero RPC calls occur before these
    checks run):
        FunderCapExceededError: ``amount_sol`` is strictly greater than
            ``config.max_session_cap_sol`` (D-03). Equal-to-cap is allowed.
        FunderInvalidAmountError: ``amount_sol`` is non-positive/non-finite,
            or ``session_pubkey`` is not a valid base58 pubkey (V5).
        FunderInsufficientBalanceError: the vault balance cannot cover
            ``amount_sol`` + the exact network fee (D-04) — sends nothing.
        RpcError: the latest blockhash expired before the fee lookup
            resolved, or ``land_check`` exhausted its confirmation budget
            without proof the send failed on-chain (see WR-04 below).

    WR-04 — retry hazard: this function has no built-in idempotency guard.
    Each call builds, signs, and sends exactly one new transfer; there is
    no way for it to detect "did a previous call for this same intent
    already land?" A caller that blindly retries after ANY exception here
    (including one raised after the underlying transaction already landed
    on-chain) will debit the vault a second time. Callers MUST re-check the
    session's current balance before retrying and skip the retry if it
    already reflects the intended top-up, or track a correlation id for the
    attempt themselves — this function does not do it for you.
    """
    # D-03: cap guard, before touching vault.py or the network at all.
    if amount_sol > config.max_session_cap_sol:
        raise FunderCapExceededError(
            f"{amount_sol} SOL exceeds MAX_SESSION_CAP={config.max_session_cap_sol}"
        )

    # V5: input validation, before any lamport conversion.
    if not math.isfinite(amount_sol) or amount_sol <= 0:
        raise FunderInvalidAmountError(
            f"amount_sol must be a positive, finite number, got {amount_sol!r}"
        )

    try:
        session_pk = Pubkey.from_string(session_pubkey)
    except Exception as exc:
        raise FunderInvalidAmountError(
            f"session_pubkey is not a valid base58 pubkey: {session_pubkey!r}"
        ) from exc

    amount_lamports = round(amount_sol * LAMPORTS_PER_SOL)
    # WR-01: a positive, finite amount_sol can still round to 0 lamports
    # (any 0 < amount_sol < 5e-10). Refuse before any RPC call rather than
    # signing/sending a real, fee-costing zero-lamport transfer that leaves
    # the session's balance unchanged while still debiting the vault fee.
    if amount_lamports < 1:
        raise FunderInvalidAmountError(
            f"amount_sol={amount_sol!r} rounds to {amount_lamports} lamports; "
            "must be at least 1 lamport"
        )

    vault_kp = load_vault(config)
    vault_balance = await rpc.get_balance(str(vault_kp.pubkey()))

    ix = transfer(
        TransferParams(
            from_pubkey=vault_kp.pubkey(),
            to_pubkey=session_pk,
            lamports=amount_lamports,
        )
    )
    blockhash_result = await rpc.get_latest_blockhash()
    blockhash = Hash.from_string(blockhash_result["value"]["blockhash"])
    message = MessageV0.try_compile(vault_kp.pubkey(), [ix], [], blockhash)

    fee_result = await rpc.get_fee_for_message(
        base64.b64encode(bytes(message)).decode(), commitment="confirmed"
    )
    fee = fee_result["value"]
    if fee is None:
        raise RpcError("blockhash expired during fee lookup; retry")

    # D-04: balance guard, before signing or sending anything.
    if vault_balance < amount_lamports + fee:
        raise FunderInsufficientBalanceError(
            f"vault balance {vault_balance} cannot cover "
            f"{amount_lamports} + {fee} fee lamports"
        )

    tx = VersionedTransaction(message, [vault_kp])
    signed_b64 = base64.b64encode(bytes(tx)).decode()
    sig = await rpc.send_raw(signed_b64)
    await land_check(rpc, sig, signed_b64)
    return sig


async def _fund_session_async(
    base_url: str, config: Config, session_pubkey: str, amount_sol: float
) -> str:
    async with httpx.AsyncClient(base_url=base_url) as client:
        rpc = RpcClient(client)
        return await fund_session(rpc, config, session_pubkey, amount_sol)


def fund_session_sync(
    base_url: str, config: Config, session_pubkey: str, amount_sol: float
) -> str:
    """Thin sync wrapper around :func:`fund_session` for one-shot CLI call
    sites (mirrors ``bastion.rpc.client.get_balance_sync``).

    Calls ``asyncio.run()`` at the true top level — only call this from a
    plain synchronous CLI entrypoint, never from within an already-running
    event loop (Pitfall 1: ``asyncio.run()`` raises ``RuntimeError`` if
    nested inside a running loop) — and never from ``monitor.py``.
    """
    return asyncio.run(_fund_session_async(base_url, config, session_pubkey, amount_sol))
