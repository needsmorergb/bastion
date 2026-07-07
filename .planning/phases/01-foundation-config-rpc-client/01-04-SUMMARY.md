---
phase: 01-foundation-config-rpc-client
plan: 04
subsystem: infra
tags: [websockets, asyncio, solana, logsSubscribe, accountSubscribe, reconnect, heartbeat]

# Dependency graph
requires:
  - phase: 01-foundation-config-rpc-client (plan 01)
    provides: "bastion/rpc/errors.py typed error hierarchy, tests/conftest.py ws_test_server local WS harness"
provides:
  - "bastion/rpc/ws.py: ws_subscribe_logs / ws_subscribe_account persistent Solana WS subscription client"
  - "Independent message-silence-based liveness heartbeat, layered over websockets.asyncio's own reconnecting iterator"
  - "Auto-resubscribe on every (re)connect + on_gap backfill-needed signal hook consumed later by the Phase 6 monitor"
  - "Two bug fixes to the shared tests/conftest.py ws_test_server fixture (dual-stack bind delay, teardown hang)"
affects: [06-monitor]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Independent, message-silence-based liveness timer (sibling asyncio task) layered over websockets.asyncio's own ping/pong keepalive - the library's ping/pong is answered at the connection's protocol layer regardless of what the app handler is doing, so it cannot by itself catch a true black-holed route; a wall-clock 'time since last data message' timer is the only thing that does"
    - "async for websocket in connect(uri): ... as the sole reconnect mechanism (never websockets.legacy); the loop body itself is responsible for re-subscribing and clearing/signaling gaps every iteration"
    - "on_gap fires from BOTH the normal-exit path and the ConnectionClosed except path of the message-consuming loop, because `async for message in websocket` (via `__aiter__`) swallows a clean ConnectionClosedOK with no exception at all - only ConnectionClosedError propagates"

key-files:
  created:
    - bastion/rpc/ws.py
    - tests/unit/test_rpc_ws.py
  modified:
    - tests/conftest.py

key-decisions:
  - "on_gap fires on EVERY reconnect (clean or abnormal), not just error closes - a clean close still means a window where messages could have been missed until the resubscribe completes, and 'never miss an event' is the whole point of the signal"
  - "Liveness detection is purely message-silence-based (matches RESEARCH.md Pattern 6's own example), not ping/pong-based - a manual ping/pong heartbeat would not have caught this test harness's simulated black hole either, since ping/pong is auto-answered at the protocol layer independent of application-level blocking"
  - "ws_test_server fixture bound to 127.0.0.1 explicitly instead of \"localhost\" (Rule 1 bug fix, see Deviations)"
  - "WsTestHarness force_silent_drop's blocking wait changed from a bare asyncio.Event() to connection.wait_closed() (Rule 1 bug fix, see Deviations)"

patterns-established:
  - "Any future WS test harness blocking construct MUST be tied to the connection's own lifecycle (e.g. connection.wait_closed()), never a bare, unrelated asyncio.Event(), or Server.close()'s 'wait for every handler task to finish' teardown step will hang forever the first time two dropped connections exist simultaneously"

requirements-completed: [CLI-05]

coverage:
  - id: D1
    description: "The WS client detects a silent drop (no close frame) via an active heartbeat and reconnects, not by relying only on onclose/onerror (D-06)"
    requirement: "CLI-05"
    verification:
      - kind: unit
        ref: "tests/unit/test_rpc_ws.py#test_detects_silent_drop_via_heartbeat"
        status: pass
    human_judgment: false
  - id: D2
    description: "Subscriptions are re-sent on every reconnect (they do not survive a reconnect)"
    requirement: "CLI-05"
    verification:
      - kind: unit
        ref: "tests/unit/test_rpc_ws.py#test_resubscribes_after_reconnect"
        status: pass
    human_judgment: false
  - id: D3
    description: "On drop/reconnect the client signals backfill-needed (on_gap) to its caller exactly once per gap"
    requirement: "CLI-05"
    verification:
      - kind: unit
        ref: "tests/unit/test_rpc_ws.py#test_signals_backfill_needed_on_reconnect"
        status: pass
    human_judgment: false
  - id: D4
    description: "Baseline: messages received on a live connection are forwarded to the caller's on_message callback"
    requirement: "CLI-05"
    verification:
      - kind: unit
        ref: "tests/unit/test_rpc_ws.py#test_delivers_messages_to_callback"
        status: pass
    human_judgment: false
  - id: D5
    description: "Full Phase 1 unit suite (config/client/ws/smoke) remains green - no regression from the ws.py addition or the two conftest.py fixes"
    verification:
      - kind: unit
        ref: "uv run pytest tests/unit/ -q (9 passed)"
        status: pass
    human_judgment: false

duration: 20min
completed: 2026-07-07
status: complete
---

# Phase 1 Plan 4: WebSocket Subscription Client (rpc/ws.py) Summary

**Persistent Solana WS subscription client (`ws_subscribe_logs`/`ws_subscribe_account`) on `websockets.asyncio.client.connect()`, with an independent message-silence heartbeat that catches black-holed drops the library's own ping/pong cannot, auto-resubscribe on every reconnect, and a backfill-needed (`on_gap`) signal hook for the future Phase 6 monitor.**

## Performance

- **Duration:** ~20 min (includes debugging two pre-existing bugs in the shared WS test harness)
- **Started:** 2026-07-07T09:05Z
- **Completed:** 2026-07-07T09:20Z
- **Tasks:** 2 (RED test suite, GREEN implementation)
- **Files modified:** 2 created (`bastion/rpc/ws.py`, `tests/unit/test_rpc_ws.py`), 1 modified (`tests/conftest.py`)

## Accomplishments
- `bastion/rpc/ws.py`: `ws_subscribe_logs(uri, pubkey, on_message, *, on_gap=None, ping_interval=20, liveness_factor=2, ...)` and the `accountSubscribe` variant `ws_subscribe_account`, both built on the modern `websockets.asyncio.client.connect()` async-iterator (auto-reconnect + backoff on transient connection-establishment failures) - never `websockets.legacy`.
- An independent liveness sibling task tracks wall-clock time since the last DATA message received and force-closes the socket once it exceeds `liveness_factor * ping_interval`, triggering the outer reconnect - this is the mechanism that catches the true black-hole case the library's own `ping_timeout` alone misses (confirmed: pings/pongs are answered at the connection's protocol layer even while the app handler is fully blocked, so a ping/pong-only heartbeat would not have detected this test harness's simulated drop either).
- Every (re)connect re-sends the subscribe payload(s) (subscriptions do not survive a reconnect) and fires `on_gap` exactly once for the gap that just ended, so the future Phase 6 monitor can backfill.
- `tests/unit/test_rpc_ws.py`: four behavior tests against the shared local `ws_test_server` harness, all green and fast (whole file: 0.26s).

## Task Commits

Each task was committed atomically:

1. **Task 1: Write the rpc/ws test suite (RED)** - `a165363` (test)
2. **Task 2: Implement bastion/rpc/ws.py (GREEN)** - `98a16f1` (feat) - includes the two shared-harness bug fixes below, bundled into this commit since they were required to make the GREEN state verifiable

**Plan metadata:** (this SUMMARY + STATE/ROADMAP updates are handled by the orchestrator after wave merge - worktree mode does not commit those here)

## Files Created/Modified
- `bastion/rpc/ws.py` - `ws_subscribe_logs`, `ws_subscribe_account`, `_run_subscription` (shared reconnect/heartbeat/resubscribe/gap-signal loop), `_liveness_monitor` (independent heartbeat sibling task), `_logs_subscribe_payload`/`_account_subscribe_payload` builders
- `tests/unit/test_rpc_ws.py` - `test_detects_silent_drop_via_heartbeat`, `test_resubscribes_after_reconnect`, `test_signals_backfill_needed_on_reconnect`, `test_delivers_messages_to_callback`, plus `_wait_until`/`_cancel_and_wait` polling helpers
- `tests/conftest.py` - two bug fixes to `WsTestHarness`/`ws_test_server` (see Deviations)

## Decisions Made
- `on_gap` fires on every reconnect regardless of whether the prior connection ended cleanly or abnormally, since a clean close still represents a window where events could have been missed until resubscribe completes.
- Heartbeat detection is purely message-silence-based (per RESEARCH.md Pattern 6's own example code), not manual ping/pong-based - manual pings would not have caught this harness's simulated black hole either, since ping/pong is answered at the protocol layer independent of application-level blocking (confirmed empirically during debugging).
- `ws_test_server` now binds to `127.0.0.1` explicitly rather than `"localhost"`.
- `WsTestHarness.force_silent_drop()`'s blocking wait now uses `connection.wait_closed()` instead of a bare, unrelated `asyncio.Event()`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] `ws_test_server` fixture's `"localhost"` bind caused a ~2s stall per connection on this Windows host**
- **Found during:** Task 2 (initial GREEN verification - all four new tests timed out waiting for `wait_connected()`)
- **Issue:** `ws_serve(handler, "localhost", 0)` opens two independent dual-stack sockets (`127.0.0.1` and `::1`) on **different** ephemeral ports. The fixture built its URI from `server.sockets[0]`'s port only. A client resolving `"localhost"` gets both addresses and (per standard address-ordering) tries the IPv6 candidate first - against the wrong port for that family - before falling back to IPv4, costing ~2.0-2.05s per connection attempt on this machine (confirmed reproducible and consistent across runs; root-caused via a raw non-pytest script isolating `getaddrinfo`/`create_connection` timing).
- **Fix:** `ws_test_server` now binds `ws_serve(..., "127.0.0.1", 0)` and builds the URI as `ws://127.0.0.1:{port}`, eliminating the dual-stack ambiguity entirely.
- **Files modified:** `tests/conftest.py`
- **Verification:** `tests/unit/test_harness_smoke.py` (5 tests, unaffected) and `tests/unit/test_rpc_ws.py` (4 tests) both pass; connection establishment now near-instant.
- **Committed in:** `98a16f1` (Task 2 commit)

**2. [Rule 1 - Bug] `force_silent_drop()` handler blocked on an unrelated `asyncio.Event()`, hanging fixture teardown forever**
- **Found during:** Task 2 (GREEN verification - `test_resubscribes_after_reconnect` and later tests hung the entire pytest process indefinitely after `test_detects_silent_drop_via_heartbeat` passed)
- **Issue:** The harness's silent-drop simulation blocked with `await asyncio.Event().wait()` on a throwaway `Event` that nothing ever sets. `force_silent_drop()` is a persistent, instance-wide flag (never reset), so once a client keeps reconnecting after a drop (the expected, correct client behavior this plan builds), the SECOND (and every subsequent) connection's handler also immediately enters this permanent block. `websockets.asyncio.server.Server.close()` (invoked at fixture teardown) waits for every registered handler task to finish (`await asyncio.wait(self.handlers.values())`) but does not cancel them - so a handler blocked on an unrelated `Event` that nothing will ever `.set()` hangs that wait forever, hanging the whole pytest session.
- **Fix:** Changed the blocking wait to `await connection.wait_closed()`, which still blocks all reads/writes for the "silent, no close frame" simulation but resolves once the connection's own lifecycle actually ends via any path (peer-initiated close, or the server's own close during teardown), so handler tasks always terminate.
- **Files modified:** `tests/conftest.py`
- **Verification:** Full `tests/unit/test_rpc_ws.py` run completes in 0.26s (previously hung indefinitely, confirmed via a `timeout`-wrapped raw reproduction script isolating the exact hang to `Server._close()`'s `asyncio.wait(self.handlers.values())` line).
- **Committed in:** `98a16f1` (Task 2 commit)

**3. [Rule 1 - Bug] `on_gap` never fired for a clean reconnect (`clean_close()`)**
- **Found during:** Task 2 (GREEN verification - `test_signals_backfill_needed_on_reconnect` failed with `gap_calls == []` even after a confirmed resubscribe)
- **Issue:** `websockets`' `Connection.__aiter__` (used by `async for message in websocket:`) internally catches `ConnectionClosedOK` and simply ends the loop with no exception at all - it only propagates `ConnectionClosedError`. My initial implementation only set `gap_since` inside an `except ConnectionClosed:` block, so a clean close (as produced by `clean_close()`, and by my own heartbeat's `websocket.close()` call, which is also a clean/local close) never triggered the gap signal.
- **Fix:** `gap_since` is now set unconditionally after the message loop ends, whether it exited normally (clean close) or via the `except ConnectionClosed:` branch (protocol error/network failure) - `on_gap` fires on every reconnect.
- **Files modified:** `bastion/rpc/ws.py`
- **Verification:** `test_signals_backfill_needed_on_reconnect` passes; `gap_calls == [<timestamp>]` after the reconnect.
- **Committed in:** `98a16f1` (Task 2 commit)

---

**Total deviations:** 3 auto-fixed (all Rule 1 - bugs blocking correct verification of this plan's own deliverable)
**Impact on plan:** All three fixes were necessary to make the plan's required behavior (silent-drop detection, resubscribe, backfill signal) actually observable and correct under test; two live in shared test infrastructure (`tests/conftest.py`) originally authored by plan 01-01 and used only by this plan (`ws_test_server`), so risk to other parallel Phase 1 plans is minimal. No scope creep beyond what was needed to reach a true, non-hanging GREEN state.

## Issues Encountered
- Two lingering `python.exe`/`uv.exe` processes from an earlier interrupted test run were found competing for resources during debugging; killed via `taskkill` before continuing. Not a code issue, but worth noting for anyone debugging similar apparent hangs in this environment - always check for stray processes first.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- `bastion/rpc/ws.py` provides `ws_subscribe_logs`/`ws_subscribe_account` with the full reconnect/heartbeat/resubscribe/backfill-signal mechanism; Phase 6's monitor can import these directly and wire `on_gap` into actual backfill dispatch (this phase intentionally does not implement backfill dispatch itself, per the plan's explicit scope).
- The two `tests/conftest.py` fixes are backward compatible (unaffected on non-Windows or single-stack hosts) and make `ws_test_server` reliably usable by any future test that exercises `force_silent_drop()` + reconnect together - a combination the original harness could not survive past one cycle.
- No blockers identified for 01-03 (rpc/client.py, independent parallel plan) or downstream phases.

---
*Phase: 01-foundation-config-rpc-client*
*Completed: 2026-07-07*

## Self-Check: PASSED

Both created files (`bastion/rpc/ws.py`, `tests/unit/test_rpc_ws.py`) confirmed present on disk. Modified file (`tests/conftest.py`) confirmed present with expected changes. Both task commits (`a165363`, `98a16f1`) confirmed present in `git log`. Full `tests/unit/` suite (9 tests) confirmed passing across 3 consecutive runs with no flakiness.
