---
phase: 01-foundation-config-rpc-client
plan: 03
subsystem: infra
tags: [httpx, respx, asyncio, json-rpc, solana, retry-backoff, pagination]

# Dependency graph
requires:
  - phase: 01-foundation-config-rpc-client (plan 01)
    provides: "bastion/rpc/errors.py typed error hierarchy (RpcError, RpcRateLimitError, RpcTimeoutError) and the shared respx-backed rpc_harness fixture in tests/conftest.py"
provides:
  - "bastion/rpc/client.py: RpcClient with a single retry/backoff wrapper every method routes through, get_balance, get_latest_blockhash, get_signatures (cursor-paginated), get_transaction, get_fee_for_message (confirmed commitment), send_raw (sendTransaction), and a get_balance_sync top-level sync wrapper"
affects: [01-05, phase-3-funder-sweeper, phase-6-monitor]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Single retry/backoff wrapper (_request_with_backoff) that every RpcClient method routes through — Retry-After-aware, exponential+jitter fallback, ~30s cap, typed RpcRateLimitError on exhaustion; sendTransaction-class calls get a tighter 10s budget"
    - "get_signatures before-cursor pagination loop: accumulate every page, advance cursor to the last signature, terminate on a page shorter than limit"
    - "getFeeForMessage explicitly passes commitment in its params dict (default confirmed) rather than relying on the RPC's own finalized default"
    - "Sync wrappers are module-level functions (not RpcClient methods) that construct their own httpx.AsyncClient and call asyncio.run() at the true top level, keeping the Pitfall-1 nested-event-loop boundary explicit and testable"

key-files:
  created:
    - bastion/rpc/client.py
    - tests/unit/test_rpc_client.py
  modified: []

key-decisions:
  - "Sync wrapper (get_balance_sync) implemented as a module-level function taking (base_url, pubkey) and building its own httpx.AsyncClient internally, rather than an RpcClient instance method — keeps the asyncio.run()-at-top-level boundary a standalone, directly testable unit per Pitfall 1, consistent with RESEARCH.md Open Question 2's recommendation"
  - "get_balance/get_fee_for_message/get_transaction return the raw parsed 'result' JSON value (or its 'value' subfield for get_balance when shaped as {context, value}) rather than a typed dataclass — RESEARCH.md's 'domain-blind transport' framing keeps this layer JSON-in/JSON-out; typed domain objects are a downstream concern (funder/sweeper/monitor, later phases)"
  - "call() assigns an auto-incrementing JSON-RPC id per RpcClient instance rather than a fixed id=1 — avoids any ambiguity about which in-flight request a response corresponds to, at negligible cost, without over-engineering (no request multiplexing exists yet, so it's not load-bearing for correctness today, but it's the natural typed home for this once send_raw supports concurrency)"

patterns-established:
  - "respx.mock's side_effect list drives retry-sequence and pagination-sequence tests without a live network call, per RESEARCH.md's Code Examples section"
  - "Regression-canary test for Pitfall 1: a deliberately plain (non-async, non pytest.mark.asyncio) test function that calls a sync wrapper directly, so a future refactor introducing a nested event loop fails loudly instead of silently"

requirements-completed: [CLI-05]

coverage:
  - id: D1
    description: "RpcClient.call() retries on 429/transient 5xx with bounded exponential backoff, honors Retry-After when present, and raises typed RpcRateLimitError on budget exhaustion (never hangs)"
    requirement: "CLI-05"
    verification:
      - kind: unit
        ref: "tests/unit/test_rpc_client.py#test_retries_on_429_honoring_retry_after"
        status: pass
      - kind: unit
        ref: "tests/unit/test_rpc_client.py#test_retries_on_transient_5xx"
        status: pass
      - kind: unit
        ref: "tests/unit/test_rpc_client.py#test_retry_budget_exhaustion_raises_typed_error"
        status: pass
    human_judgment: false
  - id: D2
    description: "get_signatures paginates via before-cursor across a >1000-signature mocked stream without truncation, and terminates correctly on a short final page"
    requirement: "CLI-05"
    verification:
      - kind: unit
        ref: "tests/unit/test_rpc_client.py#test_get_signatures_paginates_past_1000"
        status: pass
      - kind: unit
        ref: "tests/unit/test_rpc_client.py#test_get_signatures_terminates_on_short_page"
        status: pass
    human_judgment: false
  - id: D3
    description: "get_fee_for_message explicitly requests commitment=confirmed rather than inheriting the RPC's finalized default"
    requirement: "CLI-05"
    verification:
      - kind: unit
        ref: "tests/unit/test_rpc_client.py#test_get_fee_for_message_uses_confirmed_commitment"
        status: pass
    human_judgment: false
  - id: D4
    description: "Sync wrapper (get_balance_sync) is safe to call from a plain synchronous context without a nested-event-loop RuntimeError"
    requirement: "CLI-05"
    verification:
      - kind: unit
        ref: "tests/unit/test_rpc_client.py#test_sync_wrapper_callable_from_sync_context"
        status: pass
    human_judgment: false
  - id: D5
    description: "send_raw issues a sendTransaction JSON-RPC call carrying the base64 signed-transaction blob"
    requirement: "CLI-05"
    verification:
      - kind: unit
        ref: "tests/unit/test_rpc_client.py#test_send_raw_posts_sendtransaction"
        status: pass
    human_judgment: false

duration: 3min
completed: 2026-07-07
status: complete
---

# Phase 1 Plan 3: RPC Client Summary

**Async-first `bastion/rpc/client.py` over `httpx.AsyncClient` with a single Retry-After-aware bounded-backoff wrapper, before-cursor `getSignaturesForAddress` pagination past the 1000-result cap, `confirmed`-commitment fee lookups, and a Pitfall-1-safe sync wrapper for one-shot CLI calls.**

## Performance

- **Duration:** 3 min
- **Started:** 2026-07-07T15:58:06Z
- **Completed:** 2026-07-07T16:00:51Z
- **Tasks:** 2 (TDD: RED then GREEN)
- **Files modified:** 2 created (0 modified)

## Accomplishments
- Built `RpcClient` with one internal retry/backoff wrapper (`_request_with_backoff`) that every public method routes through: honors `Retry-After` on 429/transient 5xx, falls back to exponential backoff with +-25% jitter, caps total wait at ~30s, and raises the typed `RpcRateLimitError` on exhaustion — `send_raw` (sendTransaction-class) uses a tighter 10s budget per D-05
- Implemented correct `getSignaturesForAddress` cursor pagination (`get_signatures`): pages backward with `before=<oldest sig of previous page>`, accumulates every page without truncation, and terminates on a short final page — proven against a mocked 1000+500 two-page stream (1500 total, no truncation)
- `get_fee_for_message` always passes `commitment` explicitly in its params (default `"confirmed"`), never inheriting the RPC's own `finalized` default (Pitfall 4)
- Added a Pitfall-1 regression canary: a deliberately plain (non-`asyncio`) test that calls `get_balance_sync` directly, proving the sync wrapper's `asyncio.run()` boundary is safe from a top-level synchronous context
- Full 8-test behavior suite in `tests/unit/test_rpc_client.py` green; full project unit suite (13 tests total) green with no regression to the 01-01 harness smoke tests

## Task Commits

Each task was committed atomically (TDD RED -> GREEN):

1. **Task 1: Write the rpc/client test suite (RED)** - `8a96a39` (test)
2. **Task 2: Implement bastion/rpc/client.py (GREEN)** - `e6baaf5` (feat)

**Plan metadata:** (final commit follows this SUMMARY)

_Note: This plan is `tdd="true"` on Task 2; RED and GREEN gate commits both confirmed present in git log (see TDD Gate Compliance below)._

## Files Created/Modified
- `tests/unit/test_rpc_client.py` - 8-test respx-mocked behavior suite: 429 retry, transient-5xx retry, budget-exhaustion typed error, >1000-signature pagination, short-page termination, confirmed-commitment fee lookup, sync-wrapper safety canary, sendTransaction transport assertion
- `bastion/rpc/client.py` - `RpcClient` class (`call`, `get_balance`, `get_latest_blockhash`, `get_signatures`, `get_transaction`, `get_fee_for_message`, `send_raw`) plus the module-level `get_balance_sync` sync wrapper

## Decisions Made
- Sync wrapper implemented as a standalone module-level function (`get_balance_sync(base_url, pubkey)`) that builds its own `httpx.AsyncClient` and calls `asyncio.run()` internally, rather than as an `RpcClient` instance method — isolates the Pitfall-1 top-level-only boundary as its own directly testable unit
- `call()` returns the raw parsed `result` JSON value (domain-blind transport, per RESEARCH.md's architectural framing); `get_balance` additionally unwraps a `{context, value}`-shaped result to a bare lamport count, accepting either shape so callers don't need to know which RPC response format Helius returns
- Auto-incrementing JSON-RPC `id` per `RpcClient` instance (not hardcoded to `1`) — cheap correctness improvement with no behavioral downside, and the natural place for it once concurrent in-flight requests exist

## Deviations from Plan

None - plan executed exactly as written. No architectural changes, no missing-dependency blockers, no auth gates.

## Issues Encountered

**Worktree fork-point drift (pre-execution, resolved before Task 1):** This worktree's branch (`worktree-agent-aad2a75a978e3a37c`) was created from an earlier point in `main`'s history (`431e8b3`) than the orchestrator's declared base commit (`f196e0652f370686d969f9fa0be2ab577bafed8a`) — the worktree was missing all of 01-01's actual output (`bastion/rpc/errors.py`, `tests/conftest.py`, `pyproject.toml`, `uv.lock`) and every phase-1 `PLAN.md`/`RESEARCH.md`/`VALIDATION.md` file, since those were committed to `main` after this worktree branched. Confirmed via `git merge-base --is-ancestor HEAD f196e06` (true) that the declared base was a valid fast-forward target, then ran `git merge --ff-only f196e06` to bring the worktree branch up to date before starting Task 1. No divergent local work existed on the worktree branch at that point, so the fast-forward was lossless and clean.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- `bastion/rpc/client.py` is ready for Phase 3 (funder/sweeper) and Phase 6 (monitor) to import directly — `RpcClient(httpx.AsyncClient)` constructor injection keeps both production and test call sites consistent with the shared `rpc_harness` fixture pattern
- `get_fee_for_message`'s `confirmed`-commitment default is already correct for Phase 3's sweeper reserve calculation (PITFALLS.md #1/#2 concern) — no follow-up needed there
- No blockers identified. 01-04 (`rpc/ws.py`) can proceed independently in the same wave; it shares only `bastion/rpc/errors.py` and `tests/conftest.py`'s `ws_test_server` fixture, neither of which this plan touched

---
*Phase: 01-foundation-config-rpc-client*
*Completed: 2026-07-07*

## TDD Gate Compliance

RED gate commit (`test(01-03): ... RED`) at `8a96a39` confirmed before GREEN gate commit (`feat(01-03): ... GREEN`) at `e6baaf5` in git log. No REFACTOR-gate commit was needed (no cleanup pass required after GREEN; the implementation was already minimal and idiomatic).

## Self-Check: PASSED

Both created files confirmed present on disk (`bastion/rpc/client.py`, `tests/unit/test_rpc_client.py`). Both task commits (`8a96a39`, `e6baaf5`) confirmed present in `git log --oneline`. Full test suite (`uv run pytest tests/unit/ -q`) confirmed 13 passed, 0 failed, immediately before writing this SUMMARY.
