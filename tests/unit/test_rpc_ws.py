"""Behavior suite for `bastion.rpc.ws` against a real local WS server.

Drives the client with a SHORT heartbeat/liveness interval so every test
resolves in well under a second (the ws API exposes `ping_interval` and
`liveness_factor` as parameters specifically so tests don't need to wait
out the 20s production default).

Two distinct reconnect triggers are used deliberately:

- `force_silent_drop()` (no close frame ever sent) proves detection does
  NOT depend on a close frame / onclose/onerror (D-06) — used only by
  `test_detects_silent_drop_via_heartbeat`.
- `clean_close()` (a normal close handshake) is used by the resubscribe
  and backfill-signal tests, since those two behaviors (re-send
  subscriptions, fire on_gap) are triggered by ANY reconnect regardless of
  cause, and a clean close gives a deterministic, non-timing-sensitive way
  to drive exactly one reconnect (a silently-dropped connection can never
  itself accept and fully service a second connection under this harness,
  since `force_silent_drop()` is a permanent, instance-wide flag that also
  blocks all future connections' `recv()` loops from ever running).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time

from bastion.rpc.ws import ws_subscribe_logs

PUBKEY = "SomePubkey11111111111111111111111111111111"

# Short heartbeat tuning shared by every test - keeps the whole suite fast.
PING_INTERVAL = 0.1
LIVENESS_FACTOR = 2.0


async def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.005) -> None:
    """Poll `predicate` until it returns truthy or `timeout` elapses."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s")


async def _cancel_and_wait(task: "asyncio.Task") -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_detects_silent_drop_via_heartbeat(ws_test_server):
    uri, harness = ws_test_server
    task = asyncio.create_task(
        ws_subscribe_logs(
            uri,
            PUBKEY,
            lambda _msg: None,
            ping_interval=PING_INTERVAL,
            liveness_factor=LIVENESS_FACTOR,
        )
    )
    try:
        await harness.wait_connected()
        await _wait_until(lambda: len(harness.received) >= 1)

        # A real message resets the liveness clock; proves detection is
        # driven by an active timer, not just "nothing has ever arrived".
        await harness.push(json.dumps({"jsonrpc": "2.0", "result": 1, "id": 1}))

        first_connection = harness._connection
        harness.force_silent_drop()  # no close frame is ever sent

        # The heartbeat must detect the quiet connection and force a
        # reconnect - observed as a brand new server-side connection object,
        # entirely independent of any ConnectionClosed/onclose signal.
        await _wait_until(
            lambda: harness._connection is not None
            and harness._connection is not first_connection,
            timeout=2.0,
        )
    finally:
        await _cancel_and_wait(task)


async def test_resubscribes_after_reconnect(ws_test_server):
    uri, harness = ws_test_server
    task = asyncio.create_task(
        ws_subscribe_logs(
            uri,
            PUBKEY,
            lambda _msg: None,
            ping_interval=PING_INTERVAL,
            liveness_factor=LIVENESS_FACTOR,
        )
    )
    try:
        await harness.wait_connected()
        await _wait_until(lambda: len(harness.received) >= 1)

        await harness.clean_close()

        # Subscriptions do not survive a reconnect - the client must
        # re-send the subscribe payload on the fresh connection.
        await _wait_until(lambda: len(harness.received) >= 2, timeout=2.0)

        first = json.loads(harness.received[0])
        second = json.loads(harness.received[1])
        assert first["method"] == "logsSubscribe"
        assert second["method"] == "logsSubscribe"
        assert first["params"][0]["mentions"] == [PUBKEY]
        assert second["params"][0]["mentions"] == [PUBKEY]
    finally:
        await _cancel_and_wait(task)


async def test_signals_backfill_needed_on_reconnect(ws_test_server):
    uri, harness = ws_test_server
    gap_calls: list[float] = []
    task = asyncio.create_task(
        ws_subscribe_logs(
            uri,
            PUBKEY,
            lambda _msg: None,
            on_gap=gap_calls.append,
            ping_interval=PING_INTERVAL,
            liveness_factor=LIVENESS_FACTOR,
        )
    )
    try:
        await harness.wait_connected()
        await _wait_until(lambda: len(harness.received) >= 1)

        assert gap_calls == []  # no gap before any drop has occurred

        await harness.clean_close()

        await _wait_until(lambda: len(harness.received) >= 2, timeout=2.0)
        await _wait_until(lambda: len(gap_calls) >= 1, timeout=1.0)

        assert len(gap_calls) == 1
    finally:
        await _cancel_and_wait(task)


async def test_delivers_messages_to_callback(ws_test_server):
    uri, harness = ws_test_server
    received_messages: list[str] = []
    task = asyncio.create_task(
        ws_subscribe_logs(
            uri,
            PUBKEY,
            received_messages.append,
            ping_interval=PING_INTERVAL,
            liveness_factor=LIVENESS_FACTOR,
        )
    )
    try:
        await harness.wait_connected()
        await _wait_until(lambda: len(harness.received) >= 1)

        await harness.push(json.dumps({"jsonrpc": "2.0", "method": "logsNotification", "params": {"foo": "bar"}}))

        await _wait_until(lambda: len(received_messages) >= 1)
        payload = json.loads(received_messages[0])
        assert payload["params"]["foo"] == "bar"
    finally:
        await _cancel_and_wait(task)
