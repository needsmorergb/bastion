---
phase: 02-encrypted-keystore-key-safety-invariants
plan: 02
subsystem: keystore
tags: [cryptography, scrypt, fernet, keystore, fail-closed, tdd]

requires:
  - phase: 02-01
    provides: "bastion/keystore/errors.py KeystoreError hierarchy (KeystoreWrongPassphraseError, KeystoreConfigError)"
provides:
  - "bastion/keystore/crypto.py: encrypt_secret/decrypt_secret scrypt->Fernet primitives"
  - "Versioned, ciphertext-only keystore blob format (version, kdf, n, r, p, salt, ciphertext)"
  - "_validate_kdf_params fail-loud guard for hand-edited/corrupt keystore files"
affects: [02-05, phase-03-funder]

tech-stack:
  added: []
  patterns:
    - "Fresh Scrypt() instance per derive call (never reused — AlreadyFinalized pitfall)"
    - "Fernet key = base64.urlsafe_b64encode(scrypt_output), never raw scrypt bytes"
    - "Decrypt-or-raise: InvalidToken always re-raised as KeystoreWrongPassphraseError, never caught-and-garbage"
    - "KDF params validated against the blob's own stored values (not global assumption) before every derive"

key-files:
  created:
    - bastion/keystore/crypto.py
    - tests/unit/test_keystore_crypto.py
  modified: []

key-decisions:
  - "Split the single crypto.py implementation into two atomic feat commits (Task 1: core encrypt/decrypt fail-closed; Task 2: KDF param validation) even though both were designed together, to preserve per-task TDD gate fidelity: verified Task 2's 3 validation tests were genuinely RED (raw ValueError) before Task 1's commit landed, then GREEN after Task 2's validation code landed."
  - "Locked constants match plan exactly: KEYSTORE_VERSION=1, KDF_NAME='scrypt', KDF_N=131072 (2**17), KDF_R=8, KDF_P=1, SALT_BYTES=16."

patterns-established:
  - "Blob dict shape {version, kdf, n, r, p, salt, ciphertext} is the versioned, self-describing on-disk keystore format future session.py (02-05) will serialize to JSON."

requirements-completed: [SESS-04, SEC-01]

coverage:
  - id: D1
    description: "Encrypt->decrypt roundtrip recovers exact plaintext bytes with a fresh per-call salt"
    requirement: "SESS-04"
    verification:
      - kind: unit
        ref: "tests/unit/test_keystore_crypto.py::test_roundtrip_recovers_exact_keypair, ::test_fresh_salt_per_call_produces_different_ciphertext"
        status: pass
    human_judgment: false
  - id: D2
    description: "Wrong passphrase and tampered ciphertext both fail closed with KeystoreWrongPassphraseError; no secret value ever appears in the exception message"
    requirement: "SEC-01"
    verification:
      - kind: unit
        ref: "tests/unit/test_keystore_crypto.py::test_wrong_passphrase_fails_closed, ::test_tampered_ciphertext_fails_closed, ::test_no_secret_value_in_exception_message"
        status: pass
    human_judgment: false
  - id: D3
    description: "On-disk blob is ciphertext-only with exactly the locked non-secret metadata fields; no plaintext-key field exists"
    requirement: "SEC-01"
    verification:
      - kind: unit
        ref: "tests/unit/test_keystore_crypto.py::test_blob_shape_has_exactly_locked_fields, ::test_blob_is_ciphertext_only_no_plaintext_field"
        status: pass
    human_judgment: false
  - id: D4
    description: "Malformed stored KDF params (non-power-of-two n, non-positive r/p) are rejected with KeystoreConfigError before any derive is attempted; valid non-default params still decrypt (forward-compat)"
    requirement: "SESS-04"
    verification:
      - kind: unit
        ref: "tests/unit/test_keystore_crypto.py::test_param_validation_rejects_non_power_of_two_n, ::test_param_validation_rejects_zero_r, ::test_param_validation_rejects_zero_p, ::test_forward_compat_decrypts_with_valid_non_default_params"
        status: pass
    human_judgment: false

duration: 5min
completed: 2026-07-07
status: complete
---

# Phase 02 Plan 02: Keystore Crypto Core — scrypt->Fernet Primitives Summary

**Built `bastion/keystore/crypto.py` — the scrypt->Fernet cryptographic core with a versioned, ciphertext-only blob format and a fail-closed decrypt contract, TDD-first against 11 unit tests.**

## Performance

- **Duration:** 5 min
- **Started:** 2026-07-07T13:32:05-07:00
- **Completed:** 2026-07-07T13:34:07-07:00
- **Tasks:** 2 (both `type="auto" tdd="true"`)
- **Files modified:** 2 (bastion/keystore/crypto.py, tests/unit/test_keystore_crypto.py)

## Accomplishments
- `encrypt_secret(passphrase, plaintext) -> dict`: fresh per-call salt, scrypt->Fernet encryption, versioned ciphertext-only blob dict with exactly `{version, kdf, n, r, p, salt, ciphertext}`.
- `decrypt_secret(passphrase, blob) -> bytes`: validates stored KDF params, re-derives the key from the blob's own stored salt/n/r/p, and fails closed (`KeystoreWrongPassphraseError`) on wrong passphrase or tampered ciphertext via Fernet's HMAC authentication (`InvalidToken`).
- `_validate_kdf_params(n, r, p)`: rejects a non-power-of-two `n` or non-positive `r`/`p` with `KeystoreConfigError` before any `Scrypt()` construction is attempted — a hand-edited/corrupt keystore file now fails loud with a typed error instead of a raw `ValueError`.
- `_derive_fernet_key`: constructs a fresh `Scrypt` instance every call (never reused — avoids the `AlreadyFinalized` pitfall) and returns the urlsafe-base64 form Fernet requires.
- 11 unit tests covering roundtrip, fresh-salt, wrong-passphrase fail-closed, tampered-ciphertext fail-closed, no-secret-in-exception-message, blob shape, ciphertext-only property, KDF param rejection (n/r/p), and forward-compat decrypt with valid non-default params.

## Task Commits

Each task was committed atomically, following strict RED -> GREEN TDD gates:

1. **Test file (RED, covers both tasks):** `edaf2ed` — `test(02-02): add failing tests for scrypt->Fernet keystore crypto primitives` (collection error: `bastion.keystore.crypto` did not exist yet)
2. **Task 1 (GREEN): scrypt->Fernet encrypt/decrypt primitives with fail-closed decrypt** — `afb2673` — `feat(02-02): implement scrypt->Fernet encrypt/decrypt primitives (fail-closed)`
3. **Task 2 (GREEN): Versioned blob format + KDF param validation** — `07c57af` — `feat(02-02): validate stored KDF params before deriving (fail loud)`

**Plan metadata:** (this commit, docs: complete plan — see final commit below)

## Files Created/Modified
- `bastion/keystore/crypto.py` - `KEYSTORE_VERSION`, `KDF_NAME`, `KDF_N`, `KDF_R`, `KDF_P`, `SALT_BYTES` constants; `_validate_kdf_params`, `_derive_fernet_key`, `encrypt_secret`, `decrypt_secret`.
- `tests/unit/test_keystore_crypto.py` - 11 unit tests covering all `must_haves.truths` and `key_links` from the plan frontmatter.

## Decisions Made
- **Two-commit TDD split for one designed-together module:** The plan's two tasks (core primitives, then param validation) share the same file. To preserve genuine per-task RED/GREEN gate fidelity rather than committing one big green diff, the KDF-validation code was temporarily removed after writing the full RED test file, confirmed that exactly Task 2's 3 validation tests failed (with a raw `ValueError` from `Scrypt()`, not yet `KeystoreConfigError`) while all 8 Task 1 tests passed, committed Task 1, then re-added validation and confirmed all 11 tests green before committing Task 2.
- **Locked constants:** `KEYSTORE_VERSION=1`, `KDF_NAME="scrypt"`, `KDF_N=2**17` (131072), `KDF_R=8`, `KDF_P=1`, `SALT_BYTES=16` — exactly per `02-RESEARCH.md`'s CONTEXT.md-locked decision and this plan's artifact spec.
- **Tampered-ciphertext test targets byte offset 20** (past Fernet's version+timestamp header) to guarantee the flip lands inside the actual ciphertext/HMAC region rather than accidentally producing a different-but-still-parseable token.

## Deviations from Plan

None - plan executed exactly as written. `_validate_kdf_params`, `_derive_fernet_key`, `encrypt_secret`, `decrypt_secret` all match the plan's exact specified signatures and behavior.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- `bastion/keystore/crypto.py`'s `encrypt_secret`/`decrypt_secret` are ready for `keystore/session.py` (02-05) to wrap as a thin, safe layer (atomic file write, 0600 permission request, `SessionKeypair` redacted type).
- The versioned blob format (`{version, kdf, n, r, p, salt, ciphertext}`) is the exact on-disk JSON shape `session.py` will serialize/deserialize.
- No blockers identified for remaining Wave 2 plans (02-03 vault isolation, 02-04 cloud-sync/passphrase UX).

---
*Phase: 02-encrypted-keystore-key-safety-invariants*
*Completed: 2026-07-07*

## Self-Check: PASSED

- FOUND: bastion/keystore/crypto.py
- FOUND: tests/unit/test_keystore_crypto.py
- FOUND: .planning/phases/02-encrypted-keystore-key-safety-invariants/02-02-SUMMARY.md
- FOUND: edaf2ed (test commit, RED)
- FOUND: afb2673 (Task 1 commit, GREEN)
- FOUND: 07c57af (Task 2 commit, GREEN)
- FOUND: 7e10f3f (SUMMARY commit)
