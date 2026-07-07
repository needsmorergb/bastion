---
phase: 02-encrypted-keystore-key-safety-invariants
verified: 2026-07-07T00:00:00Z
status: passed
score: 7/7 must-haves verified
behavior_unverified: 0
overrides_applied: 0
---

# Phase 2: Encrypted Keystore + Key-Safety Invariants Verification Report

**Phase Goal:** Session keys are safe at rest and in memory — encrypted (scrypt → Fernet), owner-only (0600), never leaked to disk or logs — with the vault/session split established structurally (isolated vault.py import boundary) before any fund-moving code exists.
**Verified:** 2026-07-07
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Encrypt→decrypt roundtrip recovers exact keypair; wrong passphrase fails closed, never partial/garbage | ✓ VERIFIED | `bastion/keystore/crypto.py:120-151` `decrypt_secret` catches only `InvalidToken`, re-raises `KeystoreWrongPassphraseError`. `tests/unit/test_keystore_crypto.py::test_roundtrip_recovers_exact_keypair`, `test_wrong_passphrase_fails_closed`, `test_tampered_ciphertext_fails_closed` all pass. `session.py::load()` adds a second `Keypair.from_bytes` validation layer. |
| 2 | Keystore files written 0600 (POSIX) with versioned KDF params (scrypt n≥2^17) in file format; Windows limitation documented+tested-as-skip | ✓ VERIFIED | `session.py::_atomic_write_json` does `os.chmod(tmp_path, 0o600)` then `os.replace`. `test_keystore_session.py::test_save_writes_exact_0600_permissions_on_posix` (`skipif(os.name != "posix")`) asserts exact `0o600`; a companion `test_save_on_windows_documents_best_effort_permissions` (`skipif(os.name == "posix")`) exists for the Windows path — not silently assumed. `crypto.py`: `KDF_N = 2**17` (131072), stored in blob (`version`, `kdf`, `n`, `r`, `p`, `salt`, `ciphertext` — confirmed via `test_blob_shape_has_exactly_locked_fields`). |
| 3 | No plaintext key to disk or logs (grep-style regression over captured output) | ✓ VERIFIED | `tests/unit/test_keystore_no_secret_leak.py::test_no_secret_leak_across_full_generate_save_load_retire_flow` runs generate→save→load→wrong-load→retire under `capfd` + `caplog` (DEBUG), asserts sentinel passphrase, wrong passphrase, secret hex, and secret raw-b64 are absent from stdout/stderr/log text; also asserts `repr(session)` contains "REDACTED". Confirmed by direct read of test file and passing execution. |
| 4 | Startup refuses to run when KEYSTORE_DIR resolves under cloud-sync path (synthetic); default-refuse, opt-in override downgrades to warning | ✓ VERIFIED | `bastion/keystore/cloudsync.py::check_keystore_dir` raises `KeystoreCloudSyncError` by default on a matched segment (Dropbox/OneDrive/Google Drive/Mobile Documents/CloudDocs), emits `UserWarning` instead when `allow_cloud_sync=True`, and raises `KeystoreConfigError` on empty path regardless of override. All 5 behaviors covered and passing in `test_keystore_cloud_sync.py`. `session.py::save()` calls `check_keystore_dir` first — verified end-to-end via `test_save_into_cloud_sync_dir_raises_by_default`. |
| 5 | Passphrase entry confirmed on create, never echoed, never logged | ✓ VERIFIED | `bastion/keystore/passphrase.py::prompt_new_passphrase` uses `getpass.getpass` exclusively (never `input` — asserted via `_forbid_input` monkeypatch in tests), confirms via two entries, retries up to `confirm_attempts`, rejects empty, warns on short (`MIN_PASSPHRASE_WARN_LEN=8`). All behaviors covered and passing in `test_keystore_passphrase_ux.py`, including `test_never_raises_with_secret_value_in_message`. |
| 6 | Fresh keypair generated per session (unique) | ✓ VERIFIED | `session.py::generate()` constructs a new `solders.keypair.Keypair()` per call. `test_generate_yields_distinct_pubkeys_each_call` passes. |
| 7 | vault.py load_vault() is import-isolated (AST/static test asserts only vault.py may import it) | ✓ VERIFIED | `tests/unit/test_keystore_vault_isolation.py::test_only_allowlisted_modules_import_vault` walks every `*.py` under `bastion/` via `ast.parse` (never imports), checking all 4 import syntaxes, asserting the importing-file set is a subset of `ALLOWED_IMPORTERS = {"bastion/keystore/vault.py"}`. Passes. `vault.py` itself never imports `crypto`/`session`/`cloudsync`/`passphrase` (confirmed by direct file read). |

**Score:** 7/7 truths verified (0 present, behavior-unverified)

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `bastion/keystore/__init__.py` | keystore package marker | ✓ VERIFIED | One-line module docstring, no re-exports (matches plan's "no submodule re-export" constraint) |
| `bastion/keystore/errors.py` | `KeystoreError` hierarchy, message-only | ✓ VERIFIED | `KeystoreError`, `KeystoreWrongPassphraseError`, `KeystoreCloudSyncError`, `KeystoreConfigError` — all inherit correctly, no secret interpolation in any docstring/message |
| `bastion/keystore/crypto.py` | scrypt→Fernet primitives + versioned blob + KDF param validation | ✓ VERIFIED | `encrypt_secret`, `decrypt_secret`, `_derive_fernet_key`, `_validate_kdf_params` all present and match plan spec exactly, including post-review `KDF_N_MAX`/`KDF_R_MAX`/`KDF_P_MAX` ceilings and typed parse-error wrapping (WR-02, WR-03) |
| `bastion/keystore/vault.py` | isolated `load_vault()` | ✓ VERIFIED | Fail-loud on blank secret, base58/byte-array parsing, no secret leak, no keystore-module imports |
| `bastion/keystore/cloudsync.py` | cloud-sync detection + refuse-or-warn | ✓ VERIFIED | `detect_cloud_sync`, `check_keystore_dir` implement full contract |
| `bastion/keystore/passphrase.py` | confirm-on-create no-echo prompt | ✓ VERIFIED | `prompt_new_passphrase` implements full contract |
| `bastion/keystore/session.py` | `SessionKeypair` + generate/save/load/retire | ✓ VERIFIED | Redacted dataclass, atomic 0600 write with fsync + temp-cleanup (IN-01/IN-02), `_safe_pubkey` path-traversal guard (WR-01) on both `load` and `retire`, per-call decrypt (never cached), best-effort `zeroize()` |
| `tests/unit/test_keystore_*.py` (6 files) | must-have test coverage | ✓ VERIFIED | All 6 files present: `test_keystore_crypto.py`, `test_keystore_session.py`, `test_keystore_vault_isolation.py`, `test_keystore_cloud_sync.py`, `test_keystore_passphrase_ux.py`, `test_keystore_no_secret_leak.py` |
| `pyproject.toml` / `uv.lock` | `cryptography` + `solders` hash-pinned deps | ✓ VERIFIED | Both packages present and resolvable (imports succeed throughout the suite) |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| `keystore/errors.py` | `config.py` + `rpc/errors.py` convention | message-only typed-error pattern | ✓ WIRED | Mirrors established convention; `KeystoreConfigError` posture matches `ConfigError` |
| `keystore/crypto.py` | `keystore/errors.py` | `InvalidToken`→`KeystoreWrongPassphraseError`; bad params→`KeystoreConfigError` | ✓ WIRED | Confirmed via source read and passing tests |
| `keystore/session.py` | `keystore/crypto.py` | `encrypt_secret`/`decrypt_secret` | ✓ WIRED | `save()` calls `crypto.encrypt_secret`; `load()` calls `crypto.decrypt_secret` |
| `keystore/session.py` | `keystore/cloudsync.py` + `keystore/passphrase.py` | `check_keystore_dir` before write | ✓ WIRED | `save()` calls `check_keystore_dir(keystore_dir, allow_cloud_sync)` as its first statement |
| `tests/test_keystore_vault_isolation.py` | `bastion/**/*.py` | AST scan for forbidden import | ✓ WIRED | Confirmed passing; scan covers all `.py` files under `bastion/` |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| SESS-01 | 02-01 (declared), 02-05 (implemented) | Generate fresh Solana keypair for new session | ✓ SATISFIED | `session.py::generate()`; unique-pubkey test passes |
| SESS-04 | 02-01, 02-02, 02-05 | Encrypted at rest (scrypt→Fernet), 0600 files | ✓ SATISFIED | `crypto.py` + `session.py::_atomic_write_json`; POSIX 0600 test passes |
| SESS-05 | 02-02, 02-05 | Load by pubkey with passphrase; wrong passphrase fails closed | ✓ SATISFIED | `session.py::load()`; fail-closed tests pass |
| SEC-01 | 02-02, 02-03, 02-05 | No plaintext key to disk or logs | ✓ SATISFIED | Ciphertext-only blob tests + full-flow capfd/caplog regression pass |
| SEC-04 | 02-04 | Refuse cloud-synced keystore dir | ✓ SATISFIED | `cloudsync.py::check_keystore_dir`; all 5 behaviors tested and pass |
| SEC-05 | 02-04 | Passphrase confirmed on create, no echo, never logged | ✓ SATISFIED | `passphrase.py::prompt_new_passphrase`; all behaviors tested and pass |

**Orphan check:** REQUIREMENTS.md maps exactly SESS-01, SESS-04, SESS-05, SEC-01, SEC-04, SEC-05 to Phase 2 (6 requirements) — all 6 appear in plan frontmatter (`requirements:` fields across 02-01 through 02-05) and are traced above. No orphaned requirements found for this phase.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| — | — | None found | — | Grep scan for `TBD\|FIXME\|XXX\|TODO\|HACK\|PLACEHOLDER` and stub-shaped patterns (`return null`, empty returns, "not yet implemented") across `bastion/keystore/*.py` and `tests/unit/test_keystore_*.py` returned zero matches. |

### Post-Review Fixes Confirmed (02-REVIEW.md → 02-REVIEW-FIX.md)

| Finding | Status | Evidence |
|---------|--------|----------|
| WR-01: pubkey path traversal in `load()`/`retire()` | ✓ FIXED & VERIFIED | `session.py::_safe_pubkey()` validates via `Pubkey.from_string` + explicit separator/`.`/`..` checks, called at top of both `load()` and `retire()` before any path is built. 6 regression tests pass (forward-slash, `../` traversal, absolute path — for both load and retire — asserting victim files untouched). |
| WR-02: no upper bound on scrypt `n` (OOM risk) | ✓ FIXED & VERIFIED | `KDF_N_MAX = 2**20`, `KDF_R_MAX = 32`, `KDF_P_MAX = 16` added to `_validate_kdf_params`, checked before `Scrypt()` construction. 3 tests (oversized n/r/p) pass. |
| WR-03: malformed blob raises raw KeyError/binascii.Error | ✓ FIXED & VERIFIED | `decrypt_secret` wraps field parse in `try/except (KeyError, TypeError, ValueError, binascii.Error)` → `KeystoreConfigError`. 2 tests (missing field, malformed base64) pass. |
| IN-01: temp file leaked on write failure | ✓ FIXED & VERIFIED | `_atomic_write_json` wraps write/chmod/replace in try/except, unlinks temp on failure. Test simulates `os.replace` failure and confirms no `.tmp-*` leftover. |
| IN-02: no fsync before replace | ✓ FIXED & VERIFIED | `os.fsync(fd)` added before `os.close`. Test spies on `os.fsync` and confirms invocation. |
| IN-04: raw FileNotFoundError on missing keystore dir | ✓ FIXED & VERIFIED | `mkstemp` wrapped in try/except FileNotFoundError → `KeystoreConfigError`. Test confirms typed error on nonexistent dir. |
| IN-03: cloud-sync provider list non-exhaustive | Explicitly deferred (v2), not a defect | Per CONTEXT.md; documented as best-effort in `cloudsync.py` docstring |

### Full Test Suite Execution

`uv run pytest -q` → **99 passed, 1 skipped, 1 warning** in 17.91s (skip is the documented POSIX-only exact-0600 test, correctly guarded for Windows). Keystore-specific subset re-run in isolation: **71 passed, 1 skipped**. Matches 02-REVIEW-FIX.md's claimed final count exactly.

### Human Verification Required

None. All must-haves are structurally verifiable via source read + passing automated tests; no visual/UX/external-service behavior in this phase.

### Gaps Summary

No gaps found. All 7 derived observable truths (mapped from the roadmap phase goal and merged with PLAN frontmatter must-haves across all 5 plans) are verified against actual source code and passing tests — not just SUMMARY.md claims. All 6 phase requirement IDs (SESS-01, SESS-04, SESS-05, SEC-01, SEC-04, SEC-05) are satisfied with direct evidence and correctly traced with no orphans. All 6 post-code-review findings (3 warnings, 3 info) were fixed and each fix independently re-verified against the current source (not just trusting 02-REVIEW-FIX.md's narrative — the ceilings, typed-error wrapping, path-traversal guard, fsync call, and temp-file cleanup were all located and read directly in `bastion/keystore/crypto.py` and `bastion/keystore/session.py`). No debt markers, no stub patterns, no secret-leak paths found. Full test suite is green.

---

_Verified: 2026-07-07_
_Verifier: Claude (gsd-verifier)_
