---
phase: 02-encrypted-keystore-key-safety-invariants
fixed_at: 2026-07-07T21:19:26Z
review_path: .planning/phases/02-encrypted-keystore-key-safety-invariants/02-REVIEW.md
iteration: 1
findings_in_scope: 6
fixed: 6
skipped: 0
status: all_fixed
---

# Phase 2: Code Review Fix Report — Encrypted Keystore + Key-Safety Invariants

**Fixed at:** 2026-07-07T21:19:26Z
**Source review:** .planning/phases/02-encrypted-keystore-key-safety-invariants/02-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 6 (WR-01, WR-02, WR-03, IN-01, IN-02, IN-04 — per explicit
  task instructions; IN-03 explicitly excluded per CONTEXT.md's v2 deferral of
  additional cloud-sync providers)
- Fixed: 6
- Skipped: 0

## Fixed Issues

### WR-01: Unvalidated `pubkey` allows path traversal in `load()` and `retire()`

**Files modified:** `bastion/keystore/session.py`, `tests/unit/test_keystore_session.py`
**Commit:** `0c0dfe1`
**Applied fix:** Added `_safe_pubkey()` which calls
`solders.pubkey.Pubkey.from_string(pubkey)` (rejects non-base58 input,
including `/`, `\`, and `..` segments since they aren't valid base58
characters) plus an explicit defense-in-depth check for path separators and
`.`/`..`. Raises `KeystoreConfigError` — never a raw `ValueError`. Called at
the top of both `load()` and `retire()`, before any path is built or file is
touched. Added 6 tests covering: forward-slash pubkeys, `../` traversal (for
both `load` and `retire`), and absolute-path pubkeys (for both `load` and
`retire`) — each asserting the victim file outside `keystore_dir` is
untouched.

### WR-02: No upper bound on stored scrypt `n` — tampered file causes OOM, not a typed fail-loud error

**Files modified:** `bastion/keystore/crypto.py`, `tests/unit/test_keystore_crypto.py`
**Commit:** `bae95ef`
**Applied fix:** Added `KDF_N_MAX = 2**20`, `KDF_R_MAX = 32`, `KDF_P_MAX = 16`
ceilings and extended `_validate_kdf_params` to reject any of `n`/`r`/`p`
above these bounds with `KeystoreConfigError`, before `Scrypt()` is ever
constructed. **Verified live during test development**: writing the
oversized-`n` test against the pre-fix code caused the pytest process's
memory to climb past 2.5GB before I killed it (n=2**24 forces a real
multi-gigabyte scrypt working-set allocation) — direct confirmation this was
an exploitable resource-exhaustion path, not just a theoretical one. Added 3
tests (oversized `n`, `r`, `p`), all of which now fail fast (validation runs
before derivation) rather than allocating memory.

### WR-03: Malformed blob raises raw `KeyError`/`binascii.Error` instead of typed `KeystoreConfigError`

**Files modified:** `bastion/keystore/crypto.py`, `tests/unit/test_keystore_crypto.py`
**Commit:** `70df98f`
**Applied fix:** Wrapped the `blob["n"], blob["r"], blob["p"]` /
`blob["salt"]` / `blob["ciphertext"]` subscript-and-decode parsing in
`decrypt_secret` in a `try/except (KeyError, TypeError, ValueError,
binascii.Error)`, re-raising as `KeystoreConfigError`. Added 2 tests: a blob
missing the `ciphertext` field (raw `KeyError` before the fix), and a salt
with invalid base64 padding (`"abc"`, which triggers `binascii.Error:
Incorrect padding` before the fix — confirmed a naive "invalid characters"
salt does NOT raise by default since `base64.urlsafe_b64decode` silently
discards non-alphabet characters unless `validate=True`; the wrong-length
case was used instead to reliably reproduce the untyped-error path).

### IN-01: Temp file leaked if `os.write`/`os.chmod`/`os.replace` raises

**Files modified:** `bastion/keystore/session.py`, `tests/unit/test_keystore_session.py`
**Commit:** `3322e86`
**Applied fix:** Wrapped the post-mkstemp write/chmod/replace sequence in
`_atomic_write_json` in `try/except Exception`, unlinking the temp file
(ignoring `FileNotFoundError`) before re-raising. Added a test that
monkeypatches `os.replace` to raise and asserts no `.tmp-*` file remains in
the directory afterward.

*(Fixed together with IN-02 and IN-04 — all three land in the same ~15-line
`_atomic_write_json` function body, so splitting them into three separate
diffs against that one function would have been artificial. They are
documented as separate findings here since each is independently
verifiable via its own test.)*

### IN-02: No `fsync` before `os.replace` — durability gap

**Files modified:** `bastion/keystore/session.py`, `tests/unit/test_keystore_session.py`
**Commit:** `3322e86`
**Applied fix:** Added `os.fsync(fd)` after `os.write(fd, data)` and before
`os.close(fd)`, so the write is durable before the atomic rename. Added a
test that spies on `os.fsync` (via monkeypatch, delegating to the real
implementation) and asserts it was invoked during `_atomic_write_json`.

### IN-04: `save()` into a non-existent, non-cloud dir raises raw `FileNotFoundError`

**Files modified:** `bastion/keystore/session.py`, `tests/unit/test_keystore_session.py`
**Commit:** `3322e86`
**Applied fix:** Chose the "wrap and re-raise typed" option (of the two
offered in the finding) rather than auto-creating the directory, to avoid
silently creating a keystore location the user didn't explicitly provision.
Wrapped the `tempfile.mkstemp(dir=directory, ...)` call in
`try/except FileNotFoundError`, re-raising as `KeystoreConfigError` with the
missing directory path in the message (not a secret). Added a test:
`save()` into a `tmp_path / "does-not-exist"` directory now raises
`KeystoreConfigError` instead of a raw `FileNotFoundError`.

## Skipped Issues

None — all 6 in-scope findings were fixed. IN-03 was explicitly excluded
per task instructions (CONTEXT.md defers additional cloud-sync providers
beyond the core four to v2) and left unchanged, as directed.

## Verification

- Full suite: `uv run pytest -q` → **99 passed, 1 skipped** (up from the
  85 passed / 1 skipped baseline before this fix pass — 14 new regression
  tests added across the 6 findings). The 1 skip is the pre-existing
  POSIX-only exact-0600-permissions test (`test_save_writes_exact_0600_permissions_on_posix`),
  skipped on Windows as designed — unrelated to this fix pass.
- Each fix was applied TDD-first: a new test was written and confirmed to
  fail against the pre-fix code (reproducing the exact finding), then the
  fix was applied and the test confirmed to pass, before committing.
- No fail-open path was introduced or touched: `decrypt_secret`'s
  `InvalidToken` → `KeystoreWrongPassphraseError` fail-closed contract is
  unchanged; the new `try/except` blocks in WR-03 sit *before* that
  Fernet-authentication step and only catch parse-time errors, never
  swallowing an authentication failure.
- No secret material is included in any new error message (all new
  `KeystoreConfigError` messages are static strings or reference only
  non-secret values — directory paths, not key/passphrase bytes).

---

_Fixed: 2026-07-07T21:19:26Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
