"""Behavior suite for bastion.sweeper.sweep_session — exact-zero
session->vault sweep with atomic ATA closing (D-05, D-06, D-07, SESS-06,
SEC-02).

All async tests use the shared `rpc_harness` fixture from tests/conftest.py
(respx-mocked httpx.AsyncClient bound to a fixed base_url) — no live
network call is made anywhere in this module.
"""

from __future__ import annotations

import base64
import json as _json
import struct

import httpx
import pytest
from solders.keypair import Keypair
from solders.system_program import ID as SYSTEM_PROGRAM_ID
from solders.transaction import VersionedTransaction

from bastion.config import Config
from bastion.keystore.session import generate
from bastion.rpc.client import RpcClient
from bastion.sweeper import TOKEN_PROGRAM_ID, sweep_session
from tests.conftest import RPC_TEST_BASE_URL

VAULT_PUBKEY = str(Keypair().pubkey())
FAKE_BLOCKHASH = str(Keypair().pubkey())  # base58 32-byte string, valid Hash shape


def _base_config(**overrides: object) -> Config:
    defaults = dict(
        solana_rpc=RPC_TEST_BASE_URL,
        solana_ws="wss://rpc.test/",
        vault_pubkey=VAULT_PUBKEY,
        keystore_dir="",
        telegram_chat_id="",
        pushover_user="",
        max_session_cap_sol=1.0,
        fee_reserve_lamports=5000,
        score_watch_threshold=0.5,
        score_critical_threshold=0.8,
        vault_secret="",
    )
    defaults.update(overrides)
    return Config(**defaults)


def _token_account(pubkey: str, amount: str) -> dict:
    """Minimal jsonParsed getTokenAccountsByOwner entry."""
    return {
        "pubkey": pubkey,
        "account": {"data": {"parsed": {"info": {"tokenAmount": {"amount": amount}}}}},
    }


def _balance_response(balance: int) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "result": {"value": balance}, "id": 1})


def _token_accounts_response(accounts: list[dict]) -> httpx.Response:
    return httpx.Response(
        200, json={"jsonrpc": "2.0", "result": {"value": accounts}, "id": 1}
    )


def _blockhash_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "result": {"value": {"blockhash": FAKE_BLOCKHASH, "lastValidBlockHeight": 1000}},
            "id": 1,
        },
    )


def _fee_response(fee: int) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "result": {"value": fee}, "id": 1})


def _send_response(sig: str = "sig111") -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "result": sig, "id": 1})


def _status_response() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "result": {"value": [{"err": None, "confirmationStatus": "confirmed"}]},
            "id": 1,
        },
    )


def _decode_signed_tx(signed_b64: str) -> VersionedTransaction:
    return VersionedTransaction.from_bytes(base64.b64decode(signed_b64))


def _transfer_lamports(tx: VersionedTransaction) -> int | None:
    """Find the System-program transfer instruction and return its lamports."""
    msg = tx.message
    for ix in msg.instructions:
        if msg.account_keys[ix.program_id_index] == SYSTEM_PROGRAM_ID:
            data = bytes(ix.data)
            if len(data) >= 12 and struct.unpack_from("<I", data, 0)[0] == 2:  # transfer
                return struct.unpack_from("<Q", data, 4)[0]
    return None


def _close_account_atas(tx: VersionedTransaction) -> list[str]:
    """Return the ATA pubkeys targeted by CloseAccount instructions in tx."""
    msg = tx.message
    atas = []
    for ix in msg.instructions:
        if msg.account_keys[ix.program_id_index] == TOKEN_PROGRAM_ID and bytes(
            ix.data
        ) == bytes([9]):
            atas.append(str(msg.account_keys[ix.accounts[0]]))
    return atas


@pytest.mark.asyncio
async def test_transfer_amount_is_balance_minus_fee(rpc_harness):
    client, router = rpc_harness
    session = generate()
    empty_ata = str(Keypair().pubkey())
    balance = 2_000_000_000
    fee = 5000

    responses = [
        _balance_response(balance),
        _token_accounts_response([_token_account(empty_ata, "0")]),
        _blockhash_response(),
        _fee_response(fee),
        _send_response(),
        _status_response(),
    ]
    captured_sends: list[dict] = []

    def _dispatch(request: httpx.Request) -> httpx.Response:
        body = _json.loads(request.content)
        if body["method"] == "sendTransaction":
            captured_sends.append(body)
        return responses.pop(0)

    router.post(RPC_TEST_BASE_URL).mock(side_effect=_dispatch)
    rpc = RpcClient(client)
    config = _base_config()

    result = await sweep_session(rpc, config, session)

    assert result["swept"] is True
    assert result["signature"] == "sig111"
    assert len(captured_sends) == 1
    tx = _decode_signed_tx(captured_sends[0]["params"][0])
    assert _transfer_lamports(tx) == balance - fee  # exact-zero, D-05


@pytest.mark.asyncio
async def test_dust_below_fee_is_noop(rpc_harness):
    client, router = rpc_harness
    session = generate()
    balance = 4000
    fee = 5000

    responses = [
        _balance_response(balance),
        _token_accounts_response([]),
        _blockhash_response(),
        _fee_response(fee),
    ]
    captured_sends: list[dict] = []

    def _dispatch(request: httpx.Request) -> httpx.Response:
        body = _json.loads(request.content)
        if body["method"] == "sendTransaction":
            captured_sends.append(body)
        return responses.pop(0)

    router.post(RPC_TEST_BASE_URL).mock(side_effect=_dispatch)
    rpc = RpcClient(client)
    config = _base_config()

    result = await sweep_session(rpc, config, session)

    assert result["swept"] is False  # D-07: no-op, not an error
    assert captured_sends == []


@pytest.mark.asyncio
async def test_balance_equals_fee_with_empty_ata_still_closes_and_sweeps(rpc_harness):
    """WR-03 regression: balance == fee exactly must still close any empty
    ATAs and land the session at 0 -- the fee is paid either way, and
    closing costs the session nothing extra (ATA rent goes straight to the
    vault via CloseAccount's own destination, never touching the session's
    SOL balance)."""
    client, router = rpc_harness
    session = generate()
    empty_ata = str(Keypair().pubkey())
    fee = 5000
    balance = fee  # exact boundary

    responses = [
        _balance_response(balance),
        _token_accounts_response([_token_account(empty_ata, "0")]),
        _blockhash_response(),
        _fee_response(fee),
        _send_response(),
        _status_response(),
    ]
    captured_sends: list[dict] = []

    def _dispatch(request: httpx.Request) -> httpx.Response:
        body = _json.loads(request.content)
        if body["method"] == "sendTransaction":
            captured_sends.append(body)
        return responses.pop(0)

    router.post(RPC_TEST_BASE_URL).mock(side_effect=_dispatch)
    rpc = RpcClient(client)
    config = _base_config()

    result = await sweep_session(rpc, config, session)

    assert result["swept"] is True
    assert result["closed_atas"] == 1
    tx = _decode_signed_tx(captured_sends[0]["params"][0])
    assert _transfer_lamports(tx) == 0
    assert _close_account_atas(tx) == [empty_ata]


@pytest.mark.asyncio
async def test_balance_equals_fee_with_no_atas_is_still_noop(rpc_harness):
    """balance == fee with nothing to close remains the D-07 no-op path --
    there is no rent to recover and sending would just burn the fee for
    nothing."""
    client, router = rpc_harness
    session = generate()
    fee = 5000
    balance = fee

    responses = [
        _balance_response(balance),
        _token_accounts_response([]),
        _blockhash_response(),
        _fee_response(fee),
    ]
    captured_sends: list[dict] = []

    def _dispatch(request: httpx.Request) -> httpx.Response:
        body = _json.loads(request.content)
        if body["method"] == "sendTransaction":
            captured_sends.append(body)
        return responses.pop(0)

    router.post(RPC_TEST_BASE_URL).mock(side_effect=_dispatch)
    rpc = RpcClient(client)
    config = _base_config()

    result = await sweep_session(rpc, config, session)

    assert result["swept"] is False
    assert captured_sends == []


@pytest.mark.asyncio
async def test_already_empty_session_is_noop(rpc_harness):
    client, router = rpc_harness
    session = generate()

    responses = [
        _balance_response(0),
        _token_accounts_response([]),
        _blockhash_response(),
        _fee_response(5000),
    ]
    captured_sends: list[dict] = []

    def _dispatch(request: httpx.Request) -> httpx.Response:
        body = _json.loads(request.content)
        if body["method"] == "sendTransaction":
            captured_sends.append(body)
        return responses.pop(0)

    router.post(RPC_TEST_BASE_URL).mock(side_effect=_dispatch)
    rpc = RpcClient(client)
    config = _base_config()

    result = await sweep_session(rpc, config, session)

    assert result["swept"] is False
    assert captured_sends == []


@pytest.mark.asyncio
async def test_one_empty_ata_closed_nonzero_left_untouched(rpc_harness):
    client, router = rpc_harness
    session = generate()
    empty_ata = str(Keypair().pubkey())
    nonzero_ata = str(Keypair().pubkey())
    balance = 2_000_000_000
    fee = 5000

    responses = [
        _balance_response(balance),
        _token_accounts_response(
            [_token_account(empty_ata, "0"), _token_account(nonzero_ata, "500")]
        ),
        _blockhash_response(),
        _fee_response(fee),
        _send_response(),
        _status_response(),
    ]
    captured_sends: list[dict] = []

    def _dispatch(request: httpx.Request) -> httpx.Response:
        body = _json.loads(request.content)
        if body["method"] == "sendTransaction":
            captured_sends.append(body)
        return responses.pop(0)

    router.post(RPC_TEST_BASE_URL).mock(side_effect=_dispatch)
    rpc = RpcClient(client)
    config = _base_config()

    result = await sweep_session(rpc, config, session)

    assert result["closed_atas"] == 1
    tx = _decode_signed_tx(captured_sends[0]["params"][0])
    closed = _close_account_atas(tx)
    assert closed == [empty_ata]
    assert nonzero_ata not in closed


@pytest.mark.asyncio
async def test_no_secret_leak_in_result_and_output(rpc_harness, capfd):
    client, router = rpc_harness
    session = generate()
    secret_hex = bytes(session._secret).hex()
    balance = 2_000_000_000
    fee = 5000

    responses = [
        _balance_response(balance),
        _token_accounts_response([]),
        _blockhash_response(),
        _fee_response(fee),
        _send_response(),
        _status_response(),
    ]
    router.post(RPC_TEST_BASE_URL).mock(side_effect=lambda req: responses.pop(0))
    rpc = RpcClient(client)
    config = _base_config()

    result = await sweep_session(rpc, config, session)

    assert secret_hex not in str(result)
    captured = capfd.readouterr()
    assert secret_hex not in captured.out
    assert secret_hex not in captured.err
