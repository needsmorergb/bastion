"""Behavior suite for bastion.funder.fund_session — capped vault->session
funding with exact-N semantics and refuse-before-send guards (D-01, D-02,
D-03, D-04, SESS-02, SESS-03, SEC-02).

All async tests use the shared `rpc_harness` fixture from tests/conftest.py
(respx-mocked httpx.AsyncClient bound to a fixed base_url) — no live
network call is made anywhere in this module.
"""

from __future__ import annotations

import json as _json

import httpx
import pytest
from solders.keypair import Keypair

from bastion.config import Config
from bastion.fund_errors import (
    FunderCapExceededError,
    FunderInsufficientBalanceError,
    FunderInvalidAmountError,
)
from bastion.funder import LAMPORTS_PER_SOL, fund_session
from bastion.rpc.client import RpcClient
from tests.conftest import RPC_TEST_BASE_URL

VAULT_KEYPAIR = Keypair()
VAULT_SECRET = str(VAULT_KEYPAIR)
SESSION_PUBKEY = str(Keypair().pubkey())
FAKE_BLOCKHASH = str(Keypair().pubkey())  # base58 32-byte string, valid Hash shape


def _base_config(**overrides: object) -> Config:
    defaults = dict(
        solana_rpc=RPC_TEST_BASE_URL,
        solana_ws="wss://rpc.test/",
        vault_pubkey=str(VAULT_KEYPAIR.pubkey()),
        keystore_dir="",
        telegram_chat_id="",
        pushover_user="",
        max_session_cap_sol=1.0,
        fee_reserve_lamports=5000,
        score_watch_threshold=0.5,
        score_critical_threshold=0.8,
        vault_secret=VAULT_SECRET,
    )
    defaults.update(overrides)
    return Config(**defaults)


def _happy_path_responses(sig: str = "sig111", fee: int = 5000, balance: int = 2_000_000_000):
    return [
        httpx.Response(200, json={"jsonrpc": "2.0", "result": {"value": balance}, "id": 1}),
        httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "result": {
                    "value": {
                        "blockhash": FAKE_BLOCKHASH,
                        "lastValidBlockHeight": 1000,
                    }
                },
                "id": 1,
            },
        ),
        httpx.Response(200, json={"jsonrpc": "2.0", "result": {"value": fee}, "id": 1}),
        httpx.Response(200, json={"jsonrpc": "2.0", "result": sig, "id": 1}),
        httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "result": {"value": [{"err": None, "confirmationStatus": "confirmed"}]},
                "id": 1,
            },
        ),
    ]


@pytest.mark.asyncio
async def test_cap_exceeded_raises_and_makes_zero_rpc_calls(rpc_harness):
    client, router = rpc_harness
    route = router.post(RPC_TEST_BASE_URL).mock(
        return_value=httpx.Response(200, json={"jsonrpc": "2.0", "result": "ok", "id": 1})
    )
    rpc = RpcClient(client)
    config = _base_config(max_session_cap_sol=1.0)

    with pytest.raises(FunderCapExceededError):
        await fund_session(rpc, config, SESSION_PUBKEY, 1.5)

    assert route.call_count == 0


@pytest.mark.asyncio
async def test_invalid_amount_raises_before_rpc(rpc_harness):
    client, router = rpc_harness
    route = router.post(RPC_TEST_BASE_URL).mock(
        return_value=httpx.Response(200, json={"jsonrpc": "2.0", "result": "ok", "id": 1})
    )
    rpc = RpcClient(client)
    config = _base_config()

    with pytest.raises(FunderInvalidAmountError):
        await fund_session(rpc, config, SESSION_PUBKEY, 0)

    with pytest.raises(FunderInvalidAmountError):
        await fund_session(rpc, config, SESSION_PUBKEY, -1.0)

    with pytest.raises(FunderInvalidAmountError):
        await fund_session(rpc, config, SESSION_PUBKEY, float("nan"))

    assert route.call_count == 0


@pytest.mark.asyncio
async def test_sub_lamport_amount_raises_before_any_rpc_call(rpc_harness):
    """WR-01 regression: an amount_sol that is positive/finite but rounds to
    0 lamports must be refused before any signing/sending happens -- never a
    real, fee-costing zero-lamport transfer sent to the network."""
    client, router = rpc_harness
    route = router.post(RPC_TEST_BASE_URL).mock(
        return_value=httpx.Response(200, json={"jsonrpc": "2.0", "result": "ok", "id": 1})
    )
    rpc = RpcClient(client)
    config = _base_config()

    with pytest.raises(FunderInvalidAmountError):
        await fund_session(rpc, config, SESSION_PUBKEY, 1e-10)  # rounds to 0 lamports

    assert route.call_count == 0


@pytest.mark.asyncio
async def test_invalid_session_pubkey_raises_funder_invalid_amount_error(rpc_harness):
    client, router = rpc_harness
    router.post(RPC_TEST_BASE_URL).mock(
        return_value=httpx.Response(200, json={"jsonrpc": "2.0", "result": "ok", "id": 1})
    )
    rpc = RpcClient(client)
    config = _base_config()

    with pytest.raises(FunderInvalidAmountError):
        await fund_session(rpc, config, "not-a-valid-pubkey", 0.5)


@pytest.mark.asyncio
async def test_happy_path_builds_exact_transfer_and_returns_signature(rpc_harness):
    client, router = rpc_harness
    captured_sends: list[dict] = []

    responses = _happy_path_responses()

    def _dispatch(request: httpx.Request) -> httpx.Response:
        body = _json.loads(request.content)
        if body["method"] == "sendTransaction":
            captured_sends.append(body)
        return responses.pop(0)

    router.post(RPC_TEST_BASE_URL).mock(side_effect=_dispatch)
    rpc = RpcClient(client)
    config = _base_config(max_session_cap_sol=1.0)

    sig = await fund_session(rpc, config, SESSION_PUBKEY, 0.5)

    assert sig == "sig111"
    assert len(captured_sends) == 1  # exactly one send (no re-send needed here)


@pytest.mark.asyncio
async def test_equal_to_cap_proceeds(rpc_harness):
    client, router = rpc_harness
    responses = _happy_path_responses(sig="sig222")
    router.post(RPC_TEST_BASE_URL).mock(side_effect=lambda req: responses.pop(0))
    rpc = RpcClient(client)
    config = _base_config(max_session_cap_sol=0.5)

    sig = await fund_session(rpc, config, SESSION_PUBKEY, 0.5)

    assert sig == "sig222"


@pytest.mark.asyncio
async def test_insufficient_balance_raises_and_sends_nothing(rpc_harness):
    client, router = rpc_harness
    captured_sends: list[dict] = []

    responses = [
        httpx.Response(
            200, json={"jsonrpc": "2.0", "result": {"value": 1000}, "id": 1}
        ),  # tiny vault balance
        httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "result": {
                    "value": {"blockhash": FAKE_BLOCKHASH, "lastValidBlockHeight": 1000}
                },
                "id": 1,
            },
        ),
        httpx.Response(200, json={"jsonrpc": "2.0", "result": {"value": 5000}, "id": 1}),
    ]

    def _dispatch(request: httpx.Request) -> httpx.Response:
        body = _json.loads(request.content)
        if body["method"] == "sendTransaction":
            captured_sends.append(body)
        return responses.pop(0)

    router.post(RPC_TEST_BASE_URL).mock(side_effect=_dispatch)
    rpc = RpcClient(client)
    config = _base_config(max_session_cap_sol=1.0)

    with pytest.raises(FunderInsufficientBalanceError):
        await fund_session(rpc, config, SESSION_PUBKEY, 0.5)

    assert captured_sends == []


def test_lamports_per_sol_constant():
    assert LAMPORTS_PER_SOL == 1_000_000_000
