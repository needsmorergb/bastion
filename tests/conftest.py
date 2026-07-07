"""Shared, transport-agnostic test harness fixtures for the whole test suite.

This module intentionally does NOT import any `bastion.rpc.client` or
`bastion.rpc.ws` module — those don't exist yet (built in 01-03/01-04). The
fixtures here only depend on `httpx`, `respx`, and `websockets`, so they can
be built and proven before the modules that will consume them exist.

Two fixtures are exposed:

- `rpc_harness` — an (httpx.AsyncClient, respx.MockRouter) pair bound to a
  fixed test base_url, used by 01-03 to mock JSON-RPC POST responses
  (sequenced via `side_effect=[...]`, including 429-with-Retry-After -> 200).
- `ws_test_server` — a (uri, WsTestHarness) pair backed by a real local
  `websockets.asyncio.server.serve()` instance on an ephemeral localhost
  port, used by 01-04 to drive reconnect/heartbeat/backfill-signal tests
  against a genuine WS connection rather than a mock.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import httpx
import pytest
import pytest_asyncio
import respx
import websockets
from websockets.asyncio.server import ServerConnection, serve as ws_serve

# Fixed base URL used by every respx-backed test. No live network call is
# ever made against this address — respx intercepts the transport entirely.
RPC_TEST_BASE_URL = "https://rpc.test/"


@pytest_asyncio.fixture
async def rpc_harness() -> AsyncIterator[tuple[httpx.AsyncClient, respx.MockRouter]]:
    """Yields (httpx.AsyncClient, respx.MockRouter) with the router already
    active and intercepting all outbound HTTP calls made by the client.

    Usage (mirrors the D-05 retry/backoff pattern from 01-RESEARCH.md):

        async def test_x(rpc_harness):
            client, router = rpc_harness
            route = router.post(RPC_TEST_BASE_URL).mock(
                side_effect=[
                    httpx.Response(429, headers={"Retry-After": "1"}),
                    httpx.Response(200, json={"result": "ok"}),
                ]
            )
            resp = await client.post("")
            assert route.call_count == 2
    """
    with respx.mock(assert_all_called=False) as router:
        async with httpx.AsyncClient(base_url=RPC_TEST_BASE_URL) as client:
            yield client, router


class WsTestHarness:
    """Control handle for the local WS test server fixture.

    Owned entirely by the test process — records every inbound message and
    exposes hooks to simulate the three server-side behaviors downstream WS
    tests (01-04) need to distinguish:

    - `push`         : a normal server -> client message.
    - `clean_close`  : a normal WS close handshake (client sees ConnectionClosed).
    - `force_silent_drop`: the server stops reading/writing entirely WITHOUT
      sending a close frame — simulates a black-holed NAT/idle-drop route,
      which is the failure mode a `ping_timeout`-only heartbeat can miss
      (01-RESEARCH.md Pitfall 2). The client observes neither a message nor
      a close frame; a `recv()` call just hangs until its own timeout.
    """

    def __init__(self) -> None:
        self.received: list[str] = []
        self._connection: ServerConnection | None = None
        self._connected = asyncio.Event()
        self._silent_drop = asyncio.Event()

    async def _handle_connection(self, connection: ServerConnection) -> None:
        self._connection = connection
        self._connected.set()
        try:
            while True:
                if self._silent_drop.is_set():
                    # Block forever: never read again, never write again,
                    # never return (so the library never sends a close
                    # frame). Only fixture teardown (server shutdown) ends
                    # this connection.
                    await asyncio.Event().wait()
                message = await connection.recv()
                self.received.append(message)
        except websockets.ConnectionClosed:
            pass

    async def wait_connected(self, timeout: float = 2.0) -> None:
        """Block until a client has connected to this server."""
        await asyncio.wait_for(self._connected.wait(), timeout=timeout)

    def force_silent_drop(self) -> None:
        """Stop reading/writing WITHOUT sending a close frame."""
        self._silent_drop.set()

    async def clean_close(self) -> None:
        """Send a normal WS close frame to the connected client."""
        if self._connection is None:
            raise RuntimeError("no client connected yet")
        await self._connection.close()

    async def push(self, message: str) -> None:
        """Send a server -> client message on the active connection."""
        if self._connection is None:
            raise RuntimeError("no client connected yet")
        await self._connection.send(message)


@pytest_asyncio.fixture
async def ws_test_server() -> AsyncIterator[tuple[str, WsTestHarness]]:
    """Yields (uri, WsTestHarness) for a local websockets.asyncio server
    bound to an ephemeral localhost port. Uses the modern
    `websockets.asyncio.server` API, never `websockets.legacy`.
    """
    harness = WsTestHarness()
    async with ws_serve(harness._handle_connection, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        uri = f"ws://localhost:{port}"
        yield uri, harness
