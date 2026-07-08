---
phase: 3
slug: fund-moving-on-devnet-funder-sweeper
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-07-07
---

# Phase 3 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x + pytest-asyncio (established Phase 1/2) |
| **Config file** | pyproject.toml (`[tool.pytest.ini_options]`) |
| **Quick run command** | `uv run pytest tests/unit -q` |
| **Full suite command** | `uv run pytest -q -m "not devnet"` (unit + integration; devnet e2e opt-in) |
| **Devnet e2e command** | `uv run pytest -q -m devnet` (opt-in, network + airdrop; skip-not-fail on 429) |
| **Estimated runtime** | ~5s unit; devnet e2e network-bound |

---

## Sampling Rate

- **After every task commit:** Run `uv run pytest tests/unit -q`
- **After every plan wave:** Run `uv run pytest -q -m "not devnet"`
- **Before `/gsd-verify-work`:** Full non-devnet suite must be green; devnet e2e run at least once green (or documented 429-skip)
- **Max feedback latency:** ~5 seconds (unit)

---

## Per-Task Verification Map

> Planner populates concrete rows per PLAN task. Anchors below map success criteria → observable proof.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-----------|--------|
| 03-01-* | 01 | 1 | SESS-02 | T-03-cap | funder moves exactly N SOL to session (vault debited N+fee) | unit (respx) | `uv run pytest tests/unit/test_funder.py -q` | ❌ W0 | ⬜ pending |
| 03-01-* | 01 | 1 | SESS-03 | T-03-cap | request > MAX_SESSION_CAP → typed refuse, zero tx sent | unit (respx) | `uv run pytest tests/unit/test_funder.py -q` | ❌ W0 | ⬜ pending |
| 03-02-* | 02 | 1 | SESS-06 | T-03-zero | sweep leaves session at exactly 0 lamports, SOL+ATA rent → vault | unit (respx) | `uv run pytest tests/unit/test_sweeper.py -q` | ❌ W0 | ⬜ pending |
| 03-02-* | 02 | 1 | SEC-02 | T-03-vault | sweeper cannot import vault.py (AST isolation test stays green) | unit (AST) | `uv run pytest tests/unit/test_keystore_vault_isolation.py -q` | ✅ | ⬜ pending |
| 03-03-* | 03 | 2 | SESS-07 | T-03-retire | retire refuses hard-delete when nonzero token balance remains | unit | `uv run pytest tests/unit/test_session_retire.py -q` | ❌ W0 | ⬜ pending |
| 03-04-* | 04 | 2 | SESS-02/06 | T-03-dbl | injected post-send timeout → exactly one transfer (no double-spend) | e2e/unit | `uv run pytest -q -m devnet` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/unit/test_funder.py` — stubs for SESS-02, SESS-03 (respx mock RPC; reuse Phase 1 factory)
- [ ] `tests/unit/test_sweeper.py` — stubs for SESS-06, SEC-02 (exact-zero, getFeeForMessage)
- [ ] `tests/unit/test_session_retire.py` — stub for SESS-07 D-10 nonzero-token guard
- [ ] `tests/e2e/` + `devnet` pytest marker registered in pyproject.toml (skip-not-fail on airdrop 429)
- [ ] Session-scoped reusable funded devnet keypair fixture (airdrop rate-limit mitigation from RESEARCH Wave 0 gaps)

*Existing infrastructure (Phase 1 respx factory + local WS server) covers mock-RPC unit needs.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Real-chain exact-zero + ATA close | SESS-06 | Requires live devnet + airdrop quota | Run `uv run pytest -q -m devnet`; inspect final `getBalance == 0` and ATA closed |

*Automated coverage exists for all criteria via mock RPC; devnet e2e is the real-chain confirmation.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
