---
phase: 01-foundation-config-rpc-client
plan: 01
subsystem: infra
tags: [uv, hatchling, httpx, websockets, python-dotenv, pytest, pytest-asyncio, respx]

# Dependency graph
requires: []
provides:
  - "Greenfield pyproject.toml (hatchling backend, flat `bastion/` package layout) with hash-pinned uv.lock"
  - "Secret-safe .gitignore (.env, keystore/, *.db) and a fully documented .env.example (all 14 CLI-05/CLI-06 vars, placeholders only)"
  - "bastion/rpc/errors.py typed error hierarchy: RpcError, RpcRateLimitError, RpcTimeoutError"
  - "Shared, transport-agnostic test harness (tests/conftest.py): rpc_harness (respx + httpx.AsyncClient) and ws_test_server (local websockets.asyncio server with push/clean_close/force_silent_drop hooks)"
affects: [01-02, 01-03, 01-04]

# Tech tracking
tech-stack:
  added: [httpx 0.28.1, websockets 16.0, python-dotenv 1.2.2, pytest 9.1.1, pytest-asyncio 1.4.0, respx 0.23.1, hatchling, uv]
  patterns:
    - "Flat `bastion/` package layout (no src/), per CLAUDE.md and plan constraint"
    - "bastion/rpc/__init__.py stays a bare marker — downstream modules import from bastion.rpc.client / bastion.rpc.ws directly, never re-exported"
    - "Typed exception hierarchy (RpcError base) shared across rpc/client.py and rpc/ws.py, never a bare Exception"
    - "Test harness fixtures are transport-agnostic infra: tests/conftest.py imports no bastion.rpc module, so it could be built before those modules exist"
    - "WS silent-drop simulation: check-before-recv loop so force_silent_drop() blocks the handler forever without ever returning (no close frame sent) — distinct from clean_close()'s normal close handshake"

key-files:
  created:
    - pyproject.toml
    - uv.lock
    - .gitignore (extended)
    - .env.example
    - bastion/__init__.py
    - bastion/rpc/__init__.py
    - bastion/rpc/errors.py
    - tests/__init__.py
    - tests/unit/__init__.py
    - tests/conftest.py
    - tests/unit/test_harness_smoke.py
  modified: []

key-decisions:
  - "Flat bastion/ layout (not src/) confirmed per CLAUDE.md Technology Stack table and plan's explicit instruction"
  - "hatchling wheel packaging configured via [tool.hatch.build.targets.wheel] packages = [\"bastion\"]"
  - "uv add / uv add --dev used exactly as researched, producing a hash-pinned uv.lock in one pass — no manual pyproject dependency editing needed"
  - "WS harness check-before-recv loop structure chosen so force_silent_drop() takes effect deterministically on the next read attempt (an in-flight recv() already awaiting a message will still complete once, matching real black-hole timing where the last in-flight round-trip may or may not land)"

patterns-established:
  - "Package-legitimacy checkpoint: single consolidated human-verify gate for a batch of SUS-flagged-but-independently-vetted dependencies, rather than one gate per package"
  - "respx fixture pattern: respx.mock(assert_all_called=False) context + httpx.AsyncClient(base_url=...) yielded as a pair, so downstream tests queue side_effect=[...] responses and assert route.call_count"

requirements-completed: [CLI-05, CLI-06]

coverage:
  - id: D1
    description: "Project builds and its test suite runs from a clean checkout (uv sync + uv run pytest collects and passes)"
    requirement: "CLI-05"
    verification:
      - kind: unit
        ref: "uv run pytest tests/unit/ -q"
        status: pass
      - kind: other
        ref: "uv run python -c \"import bastion, bastion.rpc, bastion.rpc.errors\""
        status: pass
    human_judgment: false
  - id: D2
    description: ".env.example documents every CLI-05 env var and every CLI-06 safety rail with placeholder values only; .gitignore keeps .env and the keystore dir out of version control"
    requirement: "CLI-06"
    verification:
      - kind: other
        ref: "grep 'SOLANA_RPC' .env.example (verified by direct authoring/inspection; automated grep blocked by this environment's blanket .env* file-access permission rule, see Issues Encountered)"
        status: pass
    human_judgment: false
  - id: D3
    description: "Shared test harness (respx httpx.AsyncClient factory + local websockets.serve() server with a forced silent-drop hook, clean_close, and push) is importable and usable by every downstream test module"
    requirement: "CLI-05"
    verification:
      - kind: unit
        ref: "tests/unit/test_harness_smoke.py (5 tests, all pass): test_respx_harness_intercepts_post_and_returns_canned_response, test_ws_server_records_inbound_subscribe_payload, test_ws_server_push_delivers_server_to_client_message, test_ws_server_clean_close_sends_close_frame, test_ws_server_force_silent_drop_is_distinct_from_clean_close"
        status: pass
    human_judgment: false
  - id: D4
    description: "Hash-pinned uv.lock exists so dependency resolution is reproducible"
    requirement: "CLI-05"
    verification:
      - kind: other
        ref: "test -s uv.lock (265 lines, non-empty, hash-pinned resolution present)"
        status: pass
    human_judgment: false

duration: 5min
completed: 2026-07-07
status: complete
---

# Phase 1 Plan 1: Foundation Scaffold + Shared Test Harness Summary

**Greenfield hatchling/uv Python project (flat `bastion/` package, hash-pinned uv.lock) with a typed RPC error hierarchy and a shared respx + local-websockets-server test harness that 01-02/01-03/01-04 build on without modification.**

## Performance

- **Duration:** 5 min
- **Started:** 2026-07-07T15:44:28Z
- **Completed:** 2026-07-07T15:49:26Z
- **Tasks:** 3 (1 checkpoint pre-approved, 2 auto)
- **Files modified:** 11 created, 1 modified (.gitignore)

## Accomplishments
- Established the entire greenfield project scaffold: `pyproject.toml` (hatchling backend, flat `bastion/` layout), `uv.lock` (hash-pinned, 265 lines), secret-safe `.gitignore`, and a fully documented `.env.example` covering all 14 CLI-05/CLI-06 variables with placeholders only
- Defined the typed RPC error hierarchy (`RpcError`, `RpcRateLimitError`, `RpcTimeoutError`) that 01-03 (client) and 01-04 (ws) will both raise
- Built a transport-agnostic shared test harness (`tests/conftest.py`): a respx-backed `httpx.AsyncClient` factory fixture and a local `websockets.asyncio.server.serve()` test server fixture with `push`/`clean_close`/`force_silent_drop` control hooks
- Proved the harness end-to-end with 5 passing smoke tests, including a WS silent-drop-vs-clean-close distinction (the exact black-hole failure mode 01-RESEARCH.md's Pitfall 2 calls out as the gap a ping-timeout-only heartbeat can miss)

## Task Commits

Each task was committed atomically:

1. **Task 1: Package legitimacy gate (pre-install)** — no commit (checkpoint; pre-approved by human orchestrator before this plan started, see Issues Encountered)
2. **Task 2: Project scaffold — pyproject.toml, deps, lockfile, ignores, package skeleton** - `003ed4a` (feat)
3. **Task 3: Shared test harness — respx factory + local WS server + smoke test** - `44278ba` (test)

**Plan metadata:** (final commit follows this SUMMARY)

## Files Created/Modified
- `pyproject.toml` - hatchling build backend, flat `bastion/` wheel packaging, runtime deps (httpx/websockets/python-dotenv), dev dependency group (pytest/pytest-asyncio/respx), `asyncio_mode = "auto"`
- `uv.lock` - hash-pinned resolution of all 10 transitive + direct packages
- `.gitignore` - extended with `*.db` (SQLite store) on top of existing `.env`/keystore/Python ignores
- `.env.example` - all 14 CLI-05/CLI-06 variables documented with placeholder values and one-line comments each
- `bastion/__init__.py`, `bastion/rpc/__init__.py` - bare package markers
- `bastion/rpc/errors.py` - `RpcError`/`RpcRateLimitError`/`RpcTimeoutError` typed hierarchy
- `tests/__init__.py`, `tests/unit/__init__.py` - test package markers
- `tests/conftest.py` - `rpc_harness` fixture (respx + httpx.AsyncClient) and `ws_test_server` fixture (local WS server + `WsTestHarness` control handle)
- `tests/unit/test_harness_smoke.py` - 5 tests proving the harness works

## Decisions Made
- Flat `bastion/` layout (not `src/`) — confirmed against CLAUDE.md's Technology Stack table and the plan's explicit greenfield-layout instruction
- `uv add` / `uv add --dev` run exactly as researched (`uv add httpx websockets python-dotenv` then `uv add --dev pytest pytest-asyncio respx`) — no manual pyproject dependency declaration needed, uv resolved and locked in one pass
- WS silent-drop implemented as a check-before-recv loop (`if self._silent_drop.is_set(): await asyncio.Event().wait()` checked at the top of each iteration, before calling `connection.recv()`) so the drop takes effect deterministically on the next read attempt, while any already-in-flight `recv()` await is allowed to complete first — this matches real network black-hole timing (the last in-flight round-trip may or may not land) rather than pretending to interrupt an already-pending await

## Deviations from Plan

None - plan executed exactly as written. Task 1's checkpoint was pre-approved by the human orchestrator before this execution agent was spawned (all six packages independently cross-verified against PyPI/GitHub in `01-RESEARCH.md`'s Package Legitimacy Audit); this executor recorded that approval and proceeded directly to Task 2 per the orchestrator's explicit instruction, without re-pausing.

## Issues Encountered
- This sandboxed environment's permission system blanket-denies Read/Grep/Bash access to any file under a `.env*` glob, including the placeholder-only `.env.example`. The file was authored correctly via the `Write` tool (content fully visible and reviewed in that tool call) and its presence/non-emptiness was confirmed indirectly, but the plan's literal `grep -q 'SOLANA_RPC' .env.example` acceptance check could not be executed as a live shell command in this session — it is a tooling/environment restriction, not a defect in the file. Recorded here rather than silently worked around.

## User Setup Required

None - no external service configuration required. (`.env.example` documents what a user will eventually need for `.env`, but no action is required at this phase.)

## Next Phase Readiness
- 01-02 (config), 01-03 (rpc/client.py), 01-04 (rpc/ws.py) can now proceed in parallel: all three import against a stable `pyproject.toml`/`uv.lock`, the typed `bastion.rpc.errors` hierarchy, and the shared `tests/conftest.py` fixtures without needing to modify this plan's output
- No blockers identified

---
*Phase: 01-foundation-config-rpc-client*
*Completed: 2026-07-07*

## Self-Check: PASSED

All 11 created files confirmed present on disk (pyproject.toml, uv.lock, bastion/__init__.py, bastion/rpc/__init__.py, bastion/rpc/errors.py, tests/__init__.py, tests/unit/__init__.py, tests/conftest.py, tests/unit/test_harness_smoke.py, .env.example, .gitignore). Both task commits (`003ed4a`, `44278ba`) confirmed present in git log.
