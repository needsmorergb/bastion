---
phase: 1
slug: foundation-config-rpc-client
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-07-07
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from `01-RESEARCH.md` → ## Validation Architecture. Task-ID column is
> reconciled against `*-PLAN.md` once plans exist; rows are grouped by plan until then.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | `pytest` + `pytest-asyncio` + `respx` (none installed yet — Wave 0 adds them) |
| **Config file** | none yet — Wave 0 creates `pyproject.toml` `[tool.pytest.ini_options]` (`asyncio_mode = "auto"` recommended) |
| **Quick run command** | `uv run pytest tests/unit/ -x -q` |
| **Full suite command** | `uv run pytest tests/unit/ -q` |
| **Estimated runtime** | ~<30 seconds (fully mocked — no live network) |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/unit/ -x -q`
- **After every plan wave:** Run `uv run pytest tests/unit/ -q`
- **Before `/gsd-verify-work`:** Full suite must be green (no devnet/integration dependency in Phase 1 — that starts Phase 3)
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Plan | Wave | Requirement | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|------|------|-------------|-----------------|-----------|-------------------|-------------|--------|
| 01-01 config | — | CLI-05 | Loads all documented env vars from `.env`/process env | unit | `pytest tests/unit/test_config.py::test_loads_all_documented_env_vars -x` | ❌ W0 | ⬜ pending |
| 01-01 config | — | CLI-05 (D-01) | Process env precedence over `.env` | unit | `pytest tests/unit/test_config.py::test_process_env_precedence -x` | ❌ W0 | ⬜ pending |
| 01-01 config | — | CLI-05 (D-02) | `KEYSTORE_PASSPHRASE` unset → `getpass` fallback, never required/echoed in env | unit | `pytest tests/unit/test_config.py::test_passphrase_getpass_fallback -x` (monkeypatch `getpass.getpass`) | ❌ W0 | ⬜ pending |
| 01-01 config | — | CLI-06 (D-08) | Each safety rail independently env-overridable | unit | `pytest tests/unit/test_config.py::test_safety_rail_overrides -x` | ❌ W0 | ⬜ pending |
| 01-01 config | — | CLI-06 | Safety rails have conservative non-zero defaults | unit | `pytest tests/unit/test_config.py::test_safety_rail_defaults -x` | ❌ W0 | ⬜ pending |
| 01-02 rpc/client | — | Success #3 (D-05) | Retries + backs off on injected 429, honors `Retry-After`, no crash | unit (respx) | `pytest tests/unit/test_rpc_client.py::test_retries_on_429_honoring_retry_after -x` | ❌ W0 | ⬜ pending |
| 01-02 rpc/client | — | Success #3 (D-05) | Retry budget exhausts to a **typed error**, not an infinite hang | unit (respx) | `pytest tests/unit/test_rpc_client.py::test_retry_budget_exhaustion_raises_typed_error -x` | ❌ W0 | ⬜ pending |
| 01-02 rpc/client | — | Success #5 (D-07) | `get_signatures` paginates `before`/`until` past 1000 without truncating | unit (respx) | `pytest tests/unit/test_rpc_client.py::test_get_signatures_paginates_past_1000 -x` | ❌ W0 | ⬜ pending |
| 01-02 rpc/client | — | Success #5 (D-07) | Pagination terminates on short final page (no infinite loop) | unit (respx) | `pytest tests/unit/test_rpc_client.py::test_get_signatures_terminates_on_short_page -x` | ❌ W0 | ⬜ pending |
| 01-03 rpc/ws | — | Success #4 (D-06) | Detects **silent** drop (no close frame) via active heartbeat and reconnects | unit (local WS server) | `pytest tests/unit/test_rpc_ws.py::test_detects_silent_drop_via_heartbeat -x` | ❌ W0 | ⬜ pending |
| 01-03 rpc/ws | — | Success #4 (D-06) | Auto-resubscribes after reconnect | unit (local WS server) | `pytest tests/unit/test_rpc_ws.py::test_resubscribes_after_reconnect -x` | ❌ W0 | ⬜ pending |
| 01-03 rpc/ws | — | Success #4 (D-06) | Signals "backfill needed" to caller on reconnect | unit (local WS server) | `pytest tests/unit/test_rpc_ws.py::test_signals_backfill_needed_on_reconnect -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky. Task-ID column reconciled by the executor/nyquist-auditor once `*-PLAN.md` task IDs exist.*

---

## Wave 0 Requirements

- [ ] `pyproject.toml` — `[project]`, `[tool.pytest.ini_options]`, deps (`hatchling` build backend per STACK.md)
- [ ] `uv.lock` — generate via `uv sync` after adding dependencies
- [ ] Framework install: `uv add --dev pytest pytest-asyncio respx`
- [ ] `tests/conftest.py` — shared fixtures: `respx`-mocked `httpx.AsyncClient` factory; local `websockets.serve()` test-server fixture with a hook to force a **silent** drop (pause reads without closing) vs. a clean close
- [ ] `tests/unit/test_config.py`, `tests/unit/test_rpc_client.py`, `tests/unit/test_rpc_ws.py` — all net-new; cover every row in the map above

---

## Manual-Only Verifications

All phase behaviors have automated verification. (Live Helius rate-limit numbers are spot-checked at implementation time per RESEARCH.md open question, but every success criterion is proven against mocks.)

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
