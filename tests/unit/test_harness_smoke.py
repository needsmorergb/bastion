"""Proves the shared test harness (tests/conftest.py) actually works
end-to-end, before any real Bastion module depends on it.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import websockets

from tests.conftest import RPC_TEST_BASE_URL


@pytest.mark.asyncio
async def test_respx_harness_intercepts_post_and_returns_canned_response(rpc_harness):
    client, router = rpc_harness
    route = router.post(RPC_TEST_BASE_URL).mock(
        return_value=httpx.Response(
            200, json={"jsonrpc": "2.0", "result": "ok", "id": 1}
        )
    )

    resp = await client.post("")

    assert resp.status_code == 200
    assert resp.json()["result"] == "ok"
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_ws_server_records_inbound_subscribe_payload(ws_test_server):
    uri, harness = ws_test_server

    async with websockets.connect(uri) as client:
        await client.send('{"method": "logsSubscribe"}')

        for _ in range(50):
            if harness.received:
                break
            await asyncio.sleep(0.01)

        assert harness.received == ['{"method": "logsSubscribe"}']


@pytest.mark.asyncio
async def test_ws_server_push_delivers_server_to_client_message(ws_test_server):
    uri, harness = ws_test_server

    async with websockets.connect(uri) as client:
        await harness.wait_connected()
        await harness.push('{"result": "pushed"}')

        message = await asyncio.wait_for(client.recv(), timeout=1.0)

    assert message == '{"result": "pushed"}'


@pytest.mark.asyncio
async def test_ws_server_clean_close_sends_close_frame(ws_test_server):
    uri, harness = ws_test_server

    async with websockets.connect(uri) as client:
        await harness.wait_connected()
        await harness.clean_close()

        with pytest.raises(websockets.ConnectionClosed):
            await asyncio.wait_for(client.recv(), timeout=1.0)


@pytest.mark.asyncio
async def test_ws_server_force_silent_drop_is_distinct_from_clean_close(
    ws_test_server,
):
    uri, harness = ws_test_server

    async with websockets.connect(uri) as client:
        await client.send('{"method": "logsSubscribe"}')

        for _ in range(50):
            if harness.received:
                break
            await asyncio.sleep(0.01)
        assert harness.received == ['{"method": "logsSubscribe"}']

        harness.force_silent_drop()

        # A silent drop delivers neither a message nor a close frame — the
        # read just hangs until its own timeout, which is exactly the
        # asyncio.TimeoutError distinguishing it from clean_close()'s
        # websockets.ConnectionClosed above.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(client.recv(), timeout=0.3)
