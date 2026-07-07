---
phase: 2
slug: encrypted-keystore-key-safety-invariants
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-07-07
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.
> Derived from 02-RESEARCH.md `## Validation Architecture`.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.1.x + pytest-asyncio 1.4.x (already project dev-deps; this phase's tests are synchronous) |
| **Config file** | `pyproject.toml` `[tool.pytest.ini_options]` (`testpaths = ["tests"]`, existing) |
| **Quick run command** | `uv run pytest tests/unit/test_keystore_crypto.py tests/unit/test_keystore_session.py -x` |
| **Full suite command** | `uv run pytest` |
| **Estimated runtime** | ~5–15 seconds (scrypt n=2¹⁷ derivations dominate; keep KDF-heavy tests few or param-reduced) |

---

## Sampling Rate

- **After every task commit:** Run the quick-run command scoped to the file(s) just touched.
- **After every plan wave:** Run `uv run pytest` (full suite).
- **Before `/gsd-verify-work`:** Full suite must be green.
- **Max feedback latency:** ~15 seconds.

---

## Per-Task Verification Map

> Requirement → test map from research. Task IDs bind to plans 02-01…02-04 during planning;
> the executor/Nyquist auditor updates Status as tests go green.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 02-01-* | 01 | 1 | SESS-04 | T-02 | Encrypt→decrypt recovers exact keypair; wrong key raises (fail-closed) | unit | `uv run pytest tests/unit/test_keystore_crypto.py -x` | ❌ W0 | ⬜ pending |
| 02-02-* | 02 | 2 | SESS-01, SESS-04, SESS-05 | T-02 | Unique keypair per generate; 0600 on POSIX; load-by-pubkey; wrong-passphrase fails closed | unit | `uv run pytest tests/unit/test_keystore_session.py -x` | ❌ W0 | ⬜ pending |
| 02-03-* | 03 | 2 | (structural → SEC-02/03) | T-02 | Only vault.py (later funder.py) may import `bastion.keystore.vault` | unit (AST/static) | `uv run pytest tests/unit/test_keystore_vault_isolation.py -x` | ❌ W0 | ⬜ pending |
| 02-04-* | 04 | 3 | SEC-01, SEC-04, SEC-05 | T-02 | No secret-shaped strings in stdout/stderr/log; cloud-sync refusal by default; passphrase confirm/no-echo | unit (regression) | `uv run pytest tests/unit/test_keystore_no_secret_leak.py tests/unit/test_keystore_cloud_sync.py tests/unit/test_keystore_passphrase_ux.py -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/unit/test_keystore_crypto.py` — SESS-04 (scrypt→Fernet roundtrip, wrong-key `InvalidToken` fail-closed, n-power-of-2 constraint)
- [ ] `tests/unit/test_keystore_session.py` — SESS-01 (unique keypair), SESS-04 (0600 POSIX), SESS-05 (load-by-pubkey, wrong-passphrase fails closed)
- [ ] `tests/unit/test_keystore_cloud_sync.py` — SEC-04 (synthetic cloud-sync path refusal + override downgrade)
- [ ] `tests/unit/test_keystore_passphrase_ux.py` — SEC-05 (confirm-on-create, retry-on-mismatch, getpass no-echo, short-passphrase warning)
- [ ] `tests/unit/test_keystore_no_secret_leak.py` — SEC-01 (capsys+capfd+caplog regression, reusing the sentinel convention from `tests/unit/test_config.py`)
- [ ] `tests/unit/test_keystore_vault_isolation.py` — structural import-isolation (feeds SEC-02/SEC-03)
- [ ] Dependency add: `uv add cryptography solders` (neither is yet in pyproject.toml — Wave 0 / first crypto task must add them, refreshing the hash-pinned uv.lock)

*No new framework/fixture install needed — pytest / pytest-asyncio / monkeypatch / tmp_path / capsys are already project dev-deps, used identically in `tests/unit/test_config.py`.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| 0600 owner-only enforcement on Windows | SESS-04 | POSIX mode bits are advisory on Windows (verified empirically in research — `st_mode` stays 0o666); real protection is NTFS ACL / user profile dir | Test asserts 0600 on POSIX (`os.name == 'posix'`), and documents + asserts the Windows limitation is surfaced (not silently assumed). No mainnet key rides on Windows perms alone. |

*All other phase behaviors have automated verification.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
