"""Session -> vault exact-zero sweep (SESS-06, SEC-02).

Isolation contract: this module MUST NOT import ``bastion.keystore.vault``
or hold the vault secret at any point. It reads only ``Config.vault_pubkey``
(a public string) as the sweep destination, and signs with the SESSION
``Keypair`` reconstructed from the ``SessionKeypair`` returned by
``bastion.keystore.session.load()``. Enforced by the same AST
import-isolation test as ``funder.py``
(``tests/unit/test_keystore_vault_isolation.py``), which will fail the
build if this contract is ever violated.

Exact-zero mechanics (D-05/D-06/D-07): the sweep transaction closes every
already-empty ATA (rent -> vault) and transfers ``balance - exact_fee`` to
the vault in one atomic, all-or-nothing send, where ``exact_fee`` comes
from ``getFeeForMessage(commitment="confirmed")`` against the fully
compiled message -- never from ``Config.fee_reserve_lamports``, which is a
fallback-only sanity floor, not the primary fee source. A sub-fee dust
balance (or an already-empty session) is a no-op that raises nothing.

The session ``Keypair`` and the base64 signed-tx blob are never logged or
placed in any exception message raised from this module.
"""

from __future__ import annotations

import asyncio
import base64

import httpx
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

from bastion.config import Config
from bastion.keystore.session import SessionKeypair
from bastion.land_check import land_check
from bastion.rpc.client import RpcClient
from bastion.rpc.errors import RpcError

# deliberately NO import of the vault-secret loader from bastion.keystore.vault (SEC-02)

TOKEN_PROGRAM_ID = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")


def close_account_ix(ata_pubkey: Pubkey, destination: Pubkey, owner: Pubkey) -> Instruction:
    """Build the SPL Token ``CloseAccount`` instruction by hand.

    ``solders`` 0.27.x ships no SPL Token instruction builders, so this is
    composed directly: program = ``TOKEN_PROGRAM_ID``, data = a single
    discriminant byte (``9``, no further instruction data), account metas
    ``[account (writable, non-signer), destination (writable, non-signer),
    owner (signer, non-writable)]``. Transfers the ATA's rent lamports to
    ``destination`` and deletes the account.

    The on-chain Token Program itself enforces a zero-balance precondition
    on ``CloseAccount`` -- it is the final backstop against a stale empty/
    nonzero classification racing a concurrent deposit, not this function.
    D-06's atomicity means such a race aborts the whole sweep transaction
    rather than partially applying it.
    """
    return Instruction(
        TOKEN_PROGRAM_ID,
        bytes([9]),  # CloseAccount discriminant, no further data
        [
            AccountMeta(pubkey=ata_pubkey, is_signer=False, is_writable=True),
            AccountMeta(pubkey=destination, is_signer=False, is_writable=True),
            AccountMeta(pubkey=owner, is_signer=True, is_writable=False),
        ],
    )


async def sweep_session(rpc: RpcClient, config: Config, session: SessionKeypair) -> dict:
    """Sweep ``session``'s remaining SOL (and rent from empty ATAs) to the vault.

    Reads ``session.pubkey``'s balance and token accounts, classifies each
    ATA as empty (``tokenAmount.amount == "0"``, closeable) or nonzero
    (left untouched -- v1 is a SOL-only sweep), then builds ONE atomic
    transaction (D-06) carrying a ``close_account_ix`` for every empty ATA
    (rent -> ``config.vault_pubkey``) plus a final System transfer of
    ``balance - exact_fee`` to the vault, landing the session at exactly
    zero lamports.

    The exact fee comes from ``get_fee_for_message(commitment="confirmed")``
    against the fully compiled message (D-05) -- never from
    ``config.fee_reserve_lamports``, which is not consulted here at all
    (it is a fallback-only sanity floor for callers, not this function's
    primary fee source).

    When ``balance <= fee`` (sub-fee dust, or an already-empty session),
    returns ``{"swept": False, "reason": ..., "balance": balance}`` and
    raises nothing (D-07) -- no transaction is built or sent.

    Signs with the SESSION key reconstructed from ``session._secret``
    ONLY -- this module never imports or references the vault secret
    (SEC-02). Returns ``{"swept": True, "signature": sig, "closed_atas":
    N}`` once ``land_check`` confirms the sweep at ``confirmed`` (D-09).
    """
    session_pk = Pubkey.from_string(session.pubkey)
    vault_pk = Pubkey.from_string(config.vault_pubkey)

    balance = await rpc.get_balance(session.pubkey)

    # Read fresh, as the LAST read before building the sweep instructions
    # (Pitfall 3) -- minimizes the window for a deposit to race the
    # classification. The on-chain Token Program's own zero-balance
    # enforcement on CloseAccount is the real backstop either way.
    token_accounts = await rpc.get_token_accounts_by_owner(session.pubkey)
    empty_atas = [
        Pubkey.from_string(acc["pubkey"])
        for acc in token_accounts
        if acc["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"] == "0"
    ]
    # Nonzero ATAs are deliberately left untouched (v1 SOL-only sweep;
    # session.retire()'s D-10 guard refuses to delete the keystore while
    # they remain).

    close_ixs = [close_account_ix(ata, vault_pk, session_pk) for ata in empty_atas]

    blockhash_result = await rpc.get_latest_blockhash()
    blockhash = Hash.from_string(blockhash_result["value"]["blockhash"])

    # Compile a probe message (close ixs + a zero-lamport placeholder
    # transfer) to size the fee -- instruction COUNT drives the fee, not
    # the transfer amount, so a placeholder is sufficient to get the exact
    # fee for the real message's shape (Pattern 1).
    placeholder_ix = transfer(
        TransferParams(from_pubkey=session_pk, to_pubkey=vault_pk, lamports=0)
    )
    probe_message = MessageV0.try_compile(
        session_pk, close_ixs + [placeholder_ix], [], blockhash
    )
    fee_result = await rpc.get_fee_for_message(
        base64.b64encode(bytes(probe_message)).decode(), commitment="confirmed"
    )
    fee = fee_result["value"]
    if fee is None:
        raise RpcError("blockhash expired during fee lookup; retry")

    if balance <= fee:
        # D-07: sub-fee dust (or an already-empty session) -> no-op, NOT
        # an error. Nothing is built or sent.
        return {"swept": False, "reason": "dust below fee reserve", "balance": balance}

    final_transfer_ix = transfer(
        TransferParams(from_pubkey=session_pk, to_pubkey=vault_pk, lamports=balance - fee)
    )
    message = MessageV0.try_compile(
        session_pk, close_ixs + [final_transfer_ix], [], blockhash
    )

    # SESSION key only, never vault -- the single most safety-critical
    # line in this module (SEC-02).
    session_kp = Keypair.from_bytes(bytes(session._secret))
    tx = VersionedTransaction(message, [session_kp])
    signed_b64 = base64.b64encode(bytes(tx)).decode()
    sig = await rpc.send_raw(signed_b64)
    await land_check(rpc, sig, signed_b64)
    return {"swept": True, "signature": sig, "closed_atas": len(empty_atas)}


async def _sweep_session_async(base_url: str, config: Config, session: SessionKeypair) -> dict:
    async with httpx.AsyncClient(base_url=base_url) as client:
        rpc = RpcClient(client)
        return await sweep_session(rpc, config, session)


def sweep_session_sync(base_url: str, config: Config, session: SessionKeypair) -> dict:
    """Thin sync wrapper around :func:`sweep_session` for one-shot CLI call
    sites (mirrors ``bastion.rpc.client.get_balance_sync``).

    Calls ``asyncio.run()`` at the true top level -- only call this from a
    plain synchronous CLI entrypoint, never from within an already-running
    event loop (Pitfall 1: ``asyncio.run()`` raises ``RuntimeError`` if
    nested inside a running loop) -- and never from ``monitor.py``.
    """
    return asyncio.run(_sweep_session_async(base_url, config, session))
