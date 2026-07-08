"""Devnet end-to-end proofs for fund->sweep (SESS-02, SESS-06) and the
no-double-spend land-check property (D-08), driven on a real Solana
devnet RPC (03-04-PLAN.md Task 2).

Every test here is `@pytest.mark.devnet` (opt-in, excluded from the
default `pytest -m "not devnet"` run) and async. The deterministic
arithmetic/build-instruction correctness these tests corroborate is
already proven against a mocked RPC in `tests/unit/test_funder.py`,
`tests/unit/test_sweeper.py`, and `tests/unit/test_land_check.py` — this
module is the real-chain gate for exact-zero and no-double-spend as
first-class properties (03-CONTEXT.md "Specifics").

Every test tolerates the shared devnet faucet: they lean on the
session-scoped `funded_session` fixture (tests/e2e/conftest.py) and
skip-not-fail on any airdrop/faucet unavailability rather than raising.
"""

from __future__ import annotations

import base64
import os

import pytest
from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.sysvar import RENT as RENT_SYSVAR_ID
from solders.system_program import ID as SYSTEM_PROGRAM_ID
from solders.system_program import (
    CreateAccountParams,
    TransferParams,
    create_account,
    transfer,
)
from solders.transaction import VersionedTransaction

from bastion.config import Config
from bastion.funder import LAMPORTS_PER_SOL, fund_session
from bastion.keystore.session import SessionKeypair
from bastion.land_check import land_check
from bastion.rpc.client import RpcClient
from bastion.rpc.errors import RpcError
from bastion.sweeper import TOKEN_PROGRAM_ID, sweep_session
from tests.e2e.conftest import _wait_for_signature

# Associated Token Account program (mainnet/devnet-identical address).
ATA_PROGRAM_ID = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")
MINT_ACCOUNT_SPACE = 82  # SPL Token Mint account size in bytes

TRANSFER_TEST_AMOUNT_SOL = 0.001
TRANSFER_TEST_AMOUNT_LAMPORTS = round(TRANSFER_TEST_AMOUNT_SOL * LAMPORTS_PER_SOL)


def _derive_ata(owner: Pubkey, mint: Pubkey) -> Pubkey:
    ata, _bump = Pubkey.find_program_address(
        [bytes(owner), bytes(TOKEN_PROGRAM_ID), bytes(mint)], ATA_PROGRAM_ID
    )
    return ata


def _create_ata_ix(payer: Pubkey, ata: Pubkey, owner: Pubkey, mint: Pubkey) -> Instruction:
    """Hand-encode the Associated Token Account program's `Create`
    instruction exactly as specified in 03-04-PLAN.md Task 2: empty
    instruction data, account metas
    [payer(signer,writable), ata(writable), owner, mint, system program,
    SPL token program].
    """
    return Instruction(
        ATA_PROGRAM_ID,
        b"",
        [
            AccountMeta(pubkey=payer, is_signer=True, is_writable=True),
            AccountMeta(pubkey=ata, is_signer=False, is_writable=True),
            AccountMeta(pubkey=owner, is_signer=False, is_writable=False),
            AccountMeta(pubkey=mint, is_signer=False, is_writable=False),
            AccountMeta(pubkey=SYSTEM_PROGRAM_ID, is_signer=False, is_writable=False),
            AccountMeta(pubkey=TOKEN_PROGRAM_ID, is_signer=False, is_writable=False),
        ],
    )


def _initialize_mint_ix(mint: Pubkey, mint_authority: Pubkey) -> Instruction:
    """SPL Token `InitializeMint` (TokenInstruction variant 0): decimals=0,
    the funded session as mint authority, no freeze authority.
    """
    data = bytes([0, 0]) + bytes(mint_authority) + bytes([0])  # COption::None
    return Instruction(
        TOKEN_PROGRAM_ID,
        data,
        [
            AccountMeta(pubkey=mint, is_signer=False, is_writable=True),
            AccountMeta(pubkey=RENT_SYSVAR_ID, is_signer=False, is_writable=False),
        ],
    )


async def _create_mint_or_skip(rpc: RpcClient, payer: SessionKeypair) -> Pubkey:
    """Create a fresh, throwaway zero-decimal SPL mint funded/owned by
    `payer`. Skips (never fails) if mint creation cannot complete on
    devnet — the deterministic close-ATA arithmetic is already proven in
    03-02's unit test (test_one_empty_ata_closed_nonzero_left_untouched);
    this is a real-chain corroboration, not the sole gate.
    """
    mint_kp = Keypair()
    payer_kp = Keypair.from_bytes(bytes(payer._secret))
    payer_pk = Pubkey.from_string(payer.pubkey)

    try:
        rent_result = await rpc.call("getMinimumBalanceForRentExemption", [MINT_ACCOUNT_SPACE])
    except RpcError as exc:
        pytest.skip(f"could not query mint rent-exemption on devnet: {exc}")
    mint_rent = rent_result if isinstance(rent_result, int) else rent_result["value"]

    create_mint_account_ix = create_account(
        CreateAccountParams(
            from_pubkey=payer_pk,
            to_pubkey=mint_kp.pubkey(),
            lamports=mint_rent,
            space=MINT_ACCOUNT_SPACE,
            owner=TOKEN_PROGRAM_ID,
        )
    )
    initialize_mint_ix = _initialize_mint_ix(mint_kp.pubkey(), payer_pk)

    blockhash_result = await rpc.get_latest_blockhash()
    blockhash = Hash.from_string(blockhash_result["value"]["blockhash"])
    message = MessageV0.try_compile(
        payer_pk, [create_mint_account_ix, initialize_mint_ix], [], blockhash
    )
    tx = VersionedTransaction(message, [payer_kp, mint_kp])
    signed_b64 = base64.b64encode(bytes(tx)).decode()

    try:
        sig = await rpc.send_raw(signed_b64)
        await land_check(rpc, sig, signed_b64)
    except RpcError as exc:
        pytest.skip(f"could not create a throwaway devnet mint: {exc}")

    return mint_kp.pubkey()


async def _ensure_empty_ata_or_skip(rpc: RpcClient, session: SessionKeypair) -> Pubkey:
    """Open exactly one empty ATA owned by `session`, so the sweep under
    test must close it first (SESS-06 success criterion 3). Uses
    `BASTION_E2E_MINT` if the operator supplied one, else creates a
    throwaway mint.
    """
    owner_pk = Pubkey.from_string(session.pubkey)
    mint_env = os.getenv("BASTION_E2E_MINT", "").strip()
    mint = Pubkey.from_string(mint_env) if mint_env else await _create_mint_or_skip(rpc, session)

    ata = _derive_ata(owner_pk, mint)
    session_kp = Keypair.from_bytes(bytes(session._secret))

    create_ata_ix = _create_ata_ix(owner_pk, ata, owner_pk, mint)
    blockhash_result = await rpc.get_latest_blockhash()
    blockhash = Hash.from_string(blockhash_result["value"]["blockhash"])
    message = MessageV0.try_compile(owner_pk, [create_ata_ix], [], blockhash)
    tx = VersionedTransaction(message, [session_kp])
    signed_b64 = base64.b64encode(bytes(tx)).decode()

    try:
        sig = await rpc.send_raw(signed_b64)
        await land_check(rpc, sig, signed_b64)
    except RpcError as exc:
        pytest.skip(f"could not open a throwaway ATA on devnet: {exc}")

    return ata


class _NullOnceRpc:
    """Wraps a real `RpcClient` so the FIRST `get_signature_statuses` call
    returns an all-null status (simulating a land-check observing an
    ambiguous/timed-out result) — even though, by the time this wrapper is
    used, the transaction has ALREADY landed for real. Every subsequent
    call (and every other attribute) delegates straight through to the
    real client.

    Proves D-08's re-send-identical-blob path on a live chain: the
    injected null forces `land_check` to re-POST the same signed bytes,
    which Solana's signature-based dedup makes a no-op, not a second
    transfer.
    """

    def __init__(self, rpc: RpcClient) -> None:
        self._rpc = rpc
        self._injected = False

    def __getattr__(self, name: str):
        return getattr(self._rpc, name)

    async def get_signature_statuses(self, signatures, *, search_history: bool = False):
        if not self._injected:
            self._injected = True
            return {"value": [None for _ in signatures]}
        return await self._rpc.get_signature_statuses(
            signatures, search_history=search_history
        )


@pytest.mark.devnet
@pytest.mark.asyncio
async def test_fund_moves_exact_amount(
    devnet_rpc: tuple[RpcClient, Config], funded_session: SessionKeypair
) -> None:
    """SESS-02 success criterion 1: a devnet fund->session transfer moves
    exactly N SOL, asserted via the exact balance delta.
    """
    rpc, config = devnet_rpc
    amount_sol = 0.01

    before = await rpc.get_balance(funded_session.pubkey)
    await fund_session(rpc, config, funded_session.pubkey, amount_sol)
    after = await rpc.get_balance(funded_session.pubkey)

    assert after - before == round(amount_sol * LAMPORTS_PER_SOL)


@pytest.mark.devnet
@pytest.mark.asyncio
async def test_sweep_to_exact_zero_with_ata(
    devnet_rpc: tuple[RpcClient, Config], funded_session: SessionKeypair
) -> None:
    """SESS-06 success criterion 3: sweeping a session holding SOL plus one
    open empty ATA ends the session at exactly 0 lamports, the ATA closed,
    and all value (SOL + reclaimed ATA rent) landed in the vault.
    """
    rpc, config = devnet_rpc

    # Top up so the session can cover mint+ATA rent and fees on top of
    # whatever it already holds (D-01: fund tops up by exactly N).
    await fund_session(rpc, config, funded_session.pubkey, 0.02)

    ata = await _ensure_empty_ata_or_skip(rpc, funded_session)
    vault_before = await rpc.get_balance(config.vault_pubkey)

    result = await sweep_session(rpc, config, funded_session)

    session_balance_after = await rpc.get_balance(funded_session.pubkey)
    assert session_balance_after == 0  # exact zero

    remaining_atas = await rpc.get_token_accounts_by_owner(funded_session.pubkey)
    remaining_pubkeys = {acc["pubkey"] for acc in remaining_atas}
    assert str(ata) not in remaining_pubkeys  # ATA closed, not left dangling

    vault_after = await rpc.get_balance(config.vault_pubkey)
    assert vault_after > vault_before  # swept SOL + reclaimed ATA rent -> vault

    assert result["swept"] is True
    assert result["closed_atas"] >= 1


@pytest.mark.devnet
@pytest.mark.asyncio
async def test_no_double_spend_on_injected_timeout(
    devnet_rpc: tuple[RpcClient, Config], funded_session: SessionKeypair
) -> None:
    """D-08 success criterion 4: an injected post-send timeout (the first
    land-check poll is forced to observe an ambiguous/null status AFTER
    the transaction has already landed) followed by land_check's own
    retry-via-resend produces exactly one transfer — no double-spend.
    """
    rpc, config = devnet_rpc
    await fund_session(rpc, config, funded_session.pubkey, 0.01)

    session_pk = Pubkey.from_string(funded_session.pubkey)
    vault_pk = Pubkey.from_string(config.vault_pubkey)
    session_kp = Keypair.from_bytes(bytes(funded_session._secret))

    ix = transfer(
        TransferParams(
            from_pubkey=session_pk,
            to_pubkey=vault_pk,
            lamports=TRANSFER_TEST_AMOUNT_LAMPORTS,
        )
    )
    blockhash_result = await rpc.get_latest_blockhash()
    blockhash = Hash.from_string(blockhash_result["value"]["blockhash"])
    message = MessageV0.try_compile(session_pk, [ix], [], blockhash)

    fee_result = await rpc.get_fee_for_message(
        base64.b64encode(bytes(message)).decode(), commitment="confirmed"
    )
    fee = fee_result["value"]
    if fee is None:
        pytest.skip("blockhash expired during fee lookup on devnet; flaky, not a hard failure")

    tx = VersionedTransaction(message, [session_kp])
    signed_b64 = base64.b64encode(bytes(tx)).decode()

    session_before = await rpc.get_balance(funded_session.pubkey)
    vault_before = await rpc.get_balance(config.vault_pubkey)

    try:
        sig = await rpc.send_raw(signed_b64)
        # Wait for the send to ACTUALLY land before injecting the fake
        # timeout, so the injected null genuinely happens AFTER real
        # confirmation -- exactly the ambiguous-but-landed scenario D-08
        # exists for.
        await _wait_for_signature(rpc, sig, budget_s=60.0)
    except RpcError as exc:
        pytest.skip(f"devnet send/confirm unavailable (skip-not-fail): {exc}")

    wrapped = _NullOnceRpc(rpc)
    await land_check(wrapped, sig, signed_b64, poll_interval_s=0.5, budget_s=30.0)

    session_after = await rpc.get_balance(funded_session.pubkey)
    vault_after = await rpc.get_balance(config.vault_pubkey)

    # Exactly one transfer occurred -- no double-spend (D-08): the session
    # lost exactly amount+fee once and the vault gained exactly amount
    # once, even though land_check observed an injected null status and
    # re-POSTed the identical signed blob.
    assert session_before - session_after == TRANSFER_TEST_AMOUNT_LAMPORTS + fee
    assert vault_after - vault_before == TRANSFER_TEST_AMOUNT_LAMPORTS

    statuses = await rpc.get_signature_statuses([sig], search_history=True)
    assert statuses["value"][0]["err"] is None
