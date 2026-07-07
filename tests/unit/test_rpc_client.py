"""Behavior suite for bastion.rpc.client.RpcClient.

Covers (per 01-03-PLAN.md):
- 429/transient-5xx retry with bounded exponential backoff, honoring
  Retry-After when present (D-05).
- Retry budget exhaustion raises the typed RpcRateLimitError, never an
  infinite hang.
- get_signatures cursor pagination past the 1000-result cap (D-07),
  and correct termination on a short final page.
- get_fee_for_message explicitly overrides the RPC's `finalized` default
  to `confirmed` (Pitfall 4).
- Sync wrappers are safe to call from a plain synchronous context
  (Pitfall 1 regression canary).
- send_raw issues a sendTransaction JSON-RPC call carrying the base64 blob.

All async tests use the shared `rpc_harness` fixture from tests/conftest.py
(respx-mocked httpx.AsyncClient bound to a fixed base_url) — no live
network call is made anywhere in this module.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from bastion.rpc.client import RpcClient, get_balance_sync
from bastion.rpc.errors import RpcRateLimitError
from tests.conftest import RPC_TEST_BASE_URL


@pytest.mark.asyncio
async def test_retries_on_429_honoring_retry_after(rpc_harness):
    client, router = rpc_harness
    route = router.post(RPC_TEST_BASE_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(
                200, json={"jsonrpc": "2.0", "result": 12345, "id": 1}
            ),
        ]
    )
    rpc = RpcClient(client)
    result = await rpc.call("getBalance", ["SomePubkey111..."])
    assert result == 12345
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_retries_on_transient_5xx(rpc_harness):
    client, router = rpc_harness
    route = router.post(RPC_TEST_BASE_URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(
                200, json={"jsonrpc": "2.0", "result": "ok", "id": 1}
            ),
        ]
    )
    rpc = RpcClient(client)
    result = await rpc.call("getLatestBlockhash", [])
    assert result == "ok"
    assert route.call_count == 2


@pytest.mark.asyncio
async def test_retry_budget_exhaustion_raises_typed_error(rpc_harness, monkeypatch):
    client, router = rpc_harness
    router.post(RPC_TEST_BASE_URL).mock(
        return_value=httpx.Response(429, headers={"Retry-After": "5"})
    )

    # Keep the test's wall-clock time sub-second: the retry loop still
    # accumulates its elapsed-budget accounting against the real Retry-After
    # values, but we don't actually want to sleep for real.
    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    rpc = RpcClient(client)
    with pytest.raises(RpcRateLimitError):
        await rpc.call("getBalance", ["SomePubkey111..."])


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_after", ["0", "-5"])
async def test_zero_or_negative_retry_after_cannot_stall_budget(
    rpc_harness, monkeypatch, retry_after
):
    # H-1 (T-01-07): a hostile endpoint answering every request with
    # `429 + Retry-After: 0` (or negative) must NOT loop forever — the wait
    # floor keeps `elapsed` advancing so the budget still trips with the typed
    # error. Bound the attempt count to catch a regression that reintroduces
    # the infinite loop instead of hanging the test suite.
    client, router = rpc_harness
    route = router.post(RPC_TEST_BASE_URL).mock(
        return_value=httpx.Response(429, headers={"Retry-After": retry_after})
    )

    slept = 0.0

    async def _accumulating_sleep(seconds: float) -> None:
        nonlocal slept
        assert seconds > 0, "retry wait must be floored to a positive minimum"
        slept += seconds

    monkeypatch.setattr(asyncio, "sleep", _accumulating_sleep)

    rpc = RpcClient(client)
    with pytest.raises(RpcRateLimitError):
        await rpc.call("getBalance", ["SomePubkey111..."])

    # Budget is 30s, floor is 0.05s → at most ~600 attempts before it trips.
    assert route.call_count < 1000
    assert slept > 0


@pytest.mark.asyncio
async def test_get_signatures_paginates_past_1000(rpc_harness):
    client, router = rpc_harness
    page1 = [{"signature": f"sig{i}"} for i in range(1000)]
    page2 = [{"signature": f"sig{i}"} for i in range(1000, 1500)]
    router.post(RPC_TEST_BASE_URL).mock(
        side_effect=[
            httpx.Response(200, json={"jsonrpc": "2.0", "result": page1, "id": 1}),
            httpx.Response(200, json={"jsonrpc": "2.0", "result": page2, "id": 1}),
        ]
    )
    rpc = RpcClient(client)
    sigs = await rpc.get_signatures("SomePubkey111...")
    assert len(sigs) == 1500
    assert sigs[0]["signature"] == "sig0"
    assert sigs[-1]["signature"] == "sig1499"


@pytest.mark.asyncio
async def test_get_signatures_terminates_on_short_page(rpc_harness):
    client, router = rpc_harness
    page = [{"signature": f"sig{i}"} for i in range(10)]
    route = router.post(RPC_TEST_BASE_URL).mock(
        return_value=httpx.Response(
            200, json={"jsonrpc": "2.0", "result": page, "id": 1}
        )
    )
    rpc = RpcClient(client)
    sigs = await rpc.get_signatures("SomePubkey111...")
    assert len(sigs) == 10
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_get_fee_for_message_uses_confirmed_commitment(rpc_harness):
    client, router = rpc_harness
    captured: dict = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        import json as _json

        captured["body"] = _json.loads(request.content)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "result": {"value": 5000}, "id": 1}
        )

    router.post(RPC_TEST_BASE_URL).mock(side_effect=_capture)
    rpc = RpcClient(client)
    await rpc.get_fee_for_message("bWVzc2FnZS1ibG9i")
    params = captured["body"]["params"]
    assert params[1]["commitment"] == "confirmed"


def test_sync_wrapper_callable_from_sync_context():
    """Deliberately a PLAIN (non-async) test — must NOT be collected as an
    asyncio test. Regression canary for Pitfall 1 (nested-event-loop
    RuntimeError from calling asyncio.run() inside an already-running loop).
    """
    import respx

    with respx.mock(assert_all_called=False) as router:
        router.post(RPC_TEST_BASE_URL).mock(
            return_value=httpx.Response(
                200, json={"jsonrpc": "2.0", "result": 42, "id": 1}
            )
        )
        result = get_balance_sync(RPC_TEST_BASE_URL, "SomePubkey111...")
    assert result == 42


@pytest.mark.asyncio
async def test_send_raw_posts_sendtransaction(rpc_harness):
    client, router = rpc_harness
    captured: dict = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        import json as _json

        captured["body"] = _json.loads(request.content)
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "result": "sig111", "id": 1}
        )

    router.post(RPC_TEST_BASE_URL).mock(side_effect=_capture)
    rpc = RpcClient(client)
    sig = await rpc.send_raw("c2lnbmVkLXR4LWJsb2I=")
    assert sig == "sig111"
    body = captured["body"]
    assert body["method"] == "sendTransaction"
    assert body["params"][0] == "c2lnbmVkLXR4LWJsb2I="
