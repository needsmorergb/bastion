"""Persistent Solana WebSocket subscription client.

Built on `websockets.asyncio.client.connect()`'s auto-reconnecting async
iterator (auto exponential backoff + jitter on transient connection
failures). NEVER use `websockets.legacy` - it is deprecated with removal
scheduled through 2030 (RESEARCH.md State of the Art).

The library's own `ping_interval`/`ping_timeout` keepalive is answered at
the connection's protocol layer independently of whatever the application
handler is doing - it does not, by itself, catch a fully black-holed
route (RESEARCH.md Pitfall 2 / PITFALLS.md #11). This module layers an
INDEPENDENT active liveness timer on top: it tracks wall-clock time since
the last DATA message of any kind was received and force-closes the
current socket once that exceeds `liveness_factor * ping_interval`,
triggering the outer reconnect loop. This is what catches the silent
drop that `ping_timeout` alone can miss.

On every (re)connect, every subscribe payload is re-sent - subscriptions
do not survive a reconnect. On an ABNORMAL reconnect (any connection
after the very first), the caller's `on_gap` callback fires exactly once
so it can backfill the gap. Phase 1 delivers this mechanism and hook only;
Phase 6's monitor wires `on_gap` into actual backfill dispatch.

Domain-blind transport: only pubkeys and public subscription/notification
data pass through this module. No private keys, secrets, or signed
transactions are ever handled or logged here.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import time
from typing import Callable, Optional

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from bastion.rpc.errors import RpcTimeoutError

OnMessage = Callable[[str], None]
OnGap = Callable[[float], None]

DEFAULT_PING_INTERVAL = 20.0
DEFAULT_LIVENESS_FACTOR = 2.0

_id_counter = itertools.count(1)


def _next_id() -> int:
    return next(_id_counter)


def _logs_subscribe_payload(pubkey: str, *, commitment: str = "confirmed") -> dict:
    return {
        "jsonrpc": "2.0",
        "id": _next_id(),
        "method": "logsSubscribe",
        "params": [{"mentions": [pubkey]}, {"commitment": commitment}],
    }


def _account_subscribe_payload(pubkey: str, *, commitment: str = "confirmed") -> dict:
    return {
        "jsonrpc": "2.0",
        "id": _next_id(),
        "method": "accountSubscribe",
        "params": [pubkey, {"commitment": commitment, "encoding": "base64"}],
    }


async def _liveness_monitor(
    websocket,
    last_seen: list,
    threshold: float,
    check_interval: float,
) -> None:
    """Independent active-heartbeat sibling task.

    Compares wall-clock time since the last DATA message against
    `threshold`; force-closes `websocket` when exceeded. Runs alongside
    the message-consuming loop for the lifetime of one connection; the
    caller cancels this task once that connection ends.
    """
    while True:
        await asyncio.sleep(check_interval)
        if time.monotonic() - last_seen[0] > threshold:
            await websocket.close()
            return


async def _run_subscription(
    uri: str,
    build_subscribe_payloads: Callable[[], list],
    on_message: OnMessage,
    *,
    on_gap: Optional[OnGap],
    ping_interval: float,
    ping_timeout: Optional[float],
    liveness_factor: float,
    liveness_check_interval: Optional[float],
) -> None:
    """Shared subscribe/reconnect/heartbeat/backfill-signal loop.

    Runs forever until the enclosing task is cancelled by the caller.
    """
    if ping_timeout is None:
        ping_timeout = ping_interval
    liveness_threshold = liveness_factor * ping_interval
    if liveness_check_interval is None:
        liveness_check_interval = max(liveness_threshold / 4, 0.005)

    gap_since: Optional[float] = None  # set when a connection exits abnormally

    try:
        async for websocket in connect(uri, ping_interval=ping_interval, ping_timeout=ping_timeout):
            last_seen = [time.monotonic()]

            if gap_since is not None and on_gap is not None:
                on_gap(gap_since)
            gap_since = None

            liveness_task = asyncio.create_task(
                _liveness_monitor(websocket, last_seen, liveness_threshold, liveness_check_interval)
            )
            try:
                for payload in build_subscribe_payloads():
                    await websocket.send(json.dumps(payload))

                async for message in websocket:
                    last_seen[0] = time.monotonic()
                    on_message(message)
                # The async-iterator protocol (`__aiter__`) swallows a clean
                # closure (ConnectionClosedOK) and simply ends the loop with
                # no exception - it only raises for ConnectionClosedError
                # (protocol error / network failure). Either way - clean or
                # abnormal - this connection has ended, so the caller may
                # have missed events until the next (re)connect: signal a
                # gap unconditionally, whichever path got us here.
                gap_since = last_seen[0]
            except ConnectionClosed:
                gap_since = last_seen[0]
            finally:
                liveness_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await liveness_task
    except (OSError, TimeoutError) as exc:
        # Fatal connection-establishment failure the library's own
        # reconnect classification gave up on (e.g. DNS/refused/handshake
        # timeout) - surface as a typed error rather than a raw exception.
        raise RpcTimeoutError(f"WS connection to {uri} failed: {exc}") from exc


async def ws_subscribe_logs(
    uri: str,
    pubkey: str,
    on_message: OnMessage,
    *,
    on_gap: Optional[OnGap] = None,
    commitment: str = "confirmed",
    ping_interval: float = DEFAULT_PING_INTERVAL,
    ping_timeout: Optional[float] = None,
    liveness_factor: float = DEFAULT_LIVENESS_FACTOR,
    liveness_check_interval: Optional[float] = None,
) -> None:
    """Subscribe to `logsSubscribe` notifications mentioning `pubkey`.

    Runs forever (auto-reconnect + auto-resubscribe on every reconnect);
    cancel the enclosing task to stop. `on_gap` fires once per abnormal
    reconnect so the caller (the Phase 6 monitor) can backfill the gap.
    """
    await _run_subscription(
        uri,
        lambda: [_logs_subscribe_payload(pubkey, commitment=commitment)],
        on_message,
        on_gap=on_gap,
        ping_interval=ping_interval,
        ping_timeout=ping_timeout,
        liveness_factor=liveness_factor,
        liveness_check_interval=liveness_check_interval,
    )


async def ws_subscribe_account(
    uri: str,
    pubkey: str,
    on_message: OnMessage,
    *,
    on_gap: Optional[OnGap] = None,
    commitment: str = "confirmed",
    ping_interval: float = DEFAULT_PING_INTERVAL,
    ping_timeout: Optional[float] = None,
    liveness_factor: float = DEFAULT_LIVENESS_FACTOR,
    liveness_check_interval: Optional[float] = None,
) -> None:
    """Subscribe to `accountSubscribe` notifications for `pubkey`.

    Same reconnect/heartbeat/resubscribe/backfill-signal semantics as
    `ws_subscribe_logs`.
    """
    await _run_subscription(
        uri,
        lambda: [_account_subscribe_payload(pubkey, commitment=commitment)],
        on_message,
        on_gap=on_gap,
        ping_interval=ping_interval,
        ping_timeout=ping_timeout,
        liveness_factor=liveness_factor,
        liveness_check_interval=liveness_check_interval,
    )
