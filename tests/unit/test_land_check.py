"""Behavior suite for bastion.land_check.land_check — the shared,
chain-based confirmation loop used by both funder and sweeper (D-08/D-09).

Covers:
    - confirmed on the first poll returns None immediately.
    - an explicit `err` in the status entry raises RpcError.
    - a null-status-then-confirmed sequence proves the loop re-POSTs the
      IDENTICAL signed blob it started with (no re-signing, D-08 no-double-
      spend core) and never sends a second, different blob.
    - all-null status entries until the budget is exhausted raises
      RpcTimeoutError, so the loop always terminates.

All async tests use the shared `rpc_harness` fixture from tests/conftest.py
(respx-mocked httpx.AsyncClient bound to a fixed base_url) — no live
network call is made anywhere in this module. Budget/poll-interval are
monkeypatched down to sub-second values so this suite stays fast.
"""

from __future__ import annotations

import json as _json

import httpx
import pytest

from bastion.land_check import land_check
from bastion.rpc.client import RpcClient
from bastion.rpc.errors import RpcError, RpcTimeoutError
from tests.conftest import RPC_TEST_BASE_URL

SIGNED_B64 = "c2lnbmVkLXR4LWJsb2I="  # "signed-tx-blob"
SIGNATURE = "sig111"


def _status_response(value: list[dict | None]) -> httpx.Response:
    return httpx.Response(
        200,
        json={"jsonrpc": "2.0", "result": {"value": value}, "id": 1},
    )


def _send_response(sig: str = SIGNATURE) -> httpx.Response:
    return httpx.Response(200, json={"jsonrpc": "2.0", "result": sig, "id": 1})


@pytest.mark.asyncio
async def test_confirmed_on_first_poll_returns_none(rpc_harness):
    client, router = rpc_harness
    router.post(RPC_TEST_BASE_URL).mock(
        return_value=_status_response([{"err": None, "confirmationStatus": "confirmed"}])
    )
    rpc = RpcClient(client)

    result = await land_check(rpc, SIGNATURE, SIGNED_B64, poll_interval_s=0.01, budget_s=1.0)

    assert result is None


@pytest.mark.asyncio
async def test_explicit_err_raises_rpc_error(rpc_harness):
    client, router = rpc_harness
    router.post(RPC_TEST_BASE_URL).mock(
        return_value=_status_response(
            [{"err": {"InstructionError": [0, "Custom"]}, "confirmationStatus": None}]
        )
    )
    rpc = RpcClient(client)

    with pytest.raises(RpcError):
        await land_check(rpc, SIGNATURE, SIGNED_B64, poll_interval_s=0.01, budget_s=1.0)


@pytest.mark.asyncio
async def test_null_status_then_confirmed_resends_identical_blob_only(rpc_harness):
    client, router = rpc_harness
    sent_blobs: list[str] = []

    def _dispatch(request: httpx.Request) -> httpx.Response:
        body = _json.loads(request.content)
        method = body["method"]
        if method == "getSignatureStatuses":
            # First poll: unknown (null). Second poll: confirmed.
            if _dispatch.calls == 0:
                _dispatch.calls += 1
                return _status_response([None])
            return _status_response(
                [{"err": None, "confirmationStatus": "confirmed"}]
            )
        elif method == "sendTransaction":
            sent_blobs.append(body["params"][0])
            return _send_response()
        raise AssertionError(f"unexpected method {method}")

    _dispatch.calls = 0
    router.post(RPC_TEST_BASE_URL).mock(side_effect=_dispatch)
    rpc = RpcClient(client)

    result = await land_check(rpc, SIGNATURE, SIGNED_B64, poll_interval_s=0.01, budget_s=1.0)

    assert result is None
    # Exactly one re-send occurred (triggered by the null status), and every
    # blob sent is byte-identical to the original signed blob — never a
    # second, different signed transaction (D-08 no-double-spend core).
    assert len(sent_blobs) == 1
    assert set(sent_blobs) == {SIGNED_B64}


@pytest.mark.asyncio
async def test_budget_exhaustion_raises_rpc_timeout_error(rpc_harness, monkeypatch):
    import asyncio

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    client, router = rpc_harness
    router.post(RPC_TEST_BASE_URL).mock(return_value=_status_response([None]))
    rpc = RpcClient(client)

    with pytest.raises(RpcTimeoutError):
        await land_check(rpc, SIGNATURE, SIGNED_B64, poll_interval_s=0.01, budget_s=0.05)
