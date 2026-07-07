---
phase: 02-encrypted-keystore-key-safety-invariants
plan: 05
subsystem: keystore
tags: [solders, cryptography, fernet, scrypt, tdd, session-lifecycle]

# Dependency graph
requires:
  - phase: 02-02
    provides: "crypto.py encrypt_secret/decrypt_secret (scrypt->Fernet, fail-closed on wrong passphrase)"
  - phase: 02-04
    provides: "cloudsync.py check_keystore_dir (empty-dir + cloud-sync refusal guards)"
provides:
  - "bastion/keystore/session.py: SessionKeypair type + generate/save/load/retire lifecycle"
  - "Full non-custodial invariant proof: no secret reaches disk unencrypted or logs/stdout/stderr across the whole flow"
affects: [phase-03-funder-sweeper, phase-04-monitor]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "SessionKeypair wraps decrypted key material as a mutable bytearray (not bytes) specifically so retire() can best-effort zeroize it in place"
    - "Atomic keystore write: tempfile.mkstemp in the target dir + os.chmod(0o600) + os.replace (Pattern 4 from 02-RESEARCH.md)"
    - "Per-call decrypt only, never cached in a module global (load() constructs a fresh SessionKeypair every call)"

key-files:
  created:
    - bastion/keystore/session.py
    - tests/unit/test_keystore_session.py
    - tests/unit/test_keystore_no_secret_leak.py
  modified: []

key-decisions:
  - "Kept the plan's split-commit TDD convention: RED (test-only) commit, then GREEN (feat) commit for Task 1, matching the pattern established in 02-01 through 02-04"
  - "Task 2's no-secret-leak test uses capfd only (not capsys+capfd together) since pytest raises 'cannot use capfd and capsys at the same time' when both fixtures are requested in one test; capfd was kept per 02-RESEARCH.md's guidance that it's the stronger guarantee for compiled-extension dependencies (cryptography, solders)"

patterns-established:
  - "Pattern: SessionKeypair(pubkey: str, _secret: bytearray) dataclass with field(repr=False) on the secret and a manual __repr__/__str__ override to 'REDACTED' -- reusable for any future in-memory secret wrapper (funder.py, sweeper.py in Phase 3)"

requirements-completed: [SESS-01, SESS-04, SESS-05, SEC-01]

coverage:
  - id: D1
    description: "generate() returns a fresh, unique SessionKeypair each call"
    requirement: "SESS-01"
    verification:
      - kind: unit
        ref: "tests/unit/test_keystore_session.py#test_generate_returns_session_keypair"
        status: pass
      - kind: unit
        ref: "tests/unit/test_keystore_session.py#test_generate_yields_distinct_pubkeys_each_call"
        status: pass
    human_judgment: false
  - id: D2
    description: "save() writes <pubkey>.json atomically with 0600 permissions on POSIX and a versioned ciphertext-only blob"
    requirement: "SESS-04"
    verification:
      - kind: unit
        ref: "tests/unit/test_keystore_session.py#test_save_writes_exact_0600_permissions_on_posix"
        status: unknown
      - kind: unit
        ref: "tests/unit/test_keystore_session.py#test_save_writes_no_temp_file_left_behind"
        status: pass
      - kind: unit
        ref: "tests/unit/test_keystore_session.py#test_saved_file_is_ciphertext_only_no_plaintext_key"
        status: pass
    human_judgment: true
    rationale: "0600 assertion is POSIX-only and this execution ran on Windows, where the test is skipped by design (Pitfall 1 documented limitation) -- POSIX CI or a Linux/macOS run must confirm the skipped assertion passes there."
  - id: D3
    description: "load(pubkey, dir, passphrase) recovers the exact keypair and fails closed on a wrong passphrase"
    requirement: "SESS-05"
    verification:
      - kind: unit
        ref: "tests/unit/test_keystore_session.py#test_load_roundtrips_exact_keypair"
        status: pass
      - kind: unit
        ref: "tests/unit/test_keystore_session.py#test_load_wrong_passphrase_fails_closed"
        status: pass
      - kind: unit
        ref: "tests/unit/test_keystore_session.py#test_load_returned_keypair_is_valid_solders_keypair"
        status: pass
    human_judgment: false
  - id: D4
    description: "SessionKeypair repr/str render secret=REDACTED; no secret-shaped string appears across the full flow"
    requirement: "SEC-01"
    verification:
      - kind: unit
        ref: "tests/unit/test_keystore_session.py#test_session_keypair_repr_is_redacted"
        status: pass
      - kind: unit
        ref: "tests/unit/test_keystore_no_secret_leak.py#test_no_secret_leak_across_full_generate_save_load_retire_flow"
        status: pass
    human_judgment: false
  - id: D5
    description: "retire() best-effort-zeroizes the in-memory secret bytearray and removes the keystore file"
    verification:
      - kind: unit
        ref: "tests/unit/test_keystore_session.py#test_retire_removes_keystore_file_and_zeroizes_secret"
        status: pass
      - kind: unit
        ref: "tests/unit/test_keystore_session.py#test_retire_tolerates_already_absent_file"
        status: pass
      - kind: unit
        ref: "tests/unit/test_keystore_session.py#test_retire_accepts_bare_pubkey_string"
        status: pass
    human_judgment: false

duration: 6min
completed: 2026-07-07
status: complete
---

# Phase 02 Plan 05: Session Keystore Lifecycle Summary

**SessionKeypair generate/save/load/retire assembled from scrypt->Fernet crypto core + cloud-sync/empty-dir safety rails, with a full-flow capfd+caplog regression proving no secret ever reaches disk or logs.**

## Performance

- **Duration:** 6 min
- **Started:** 2026-07-07T20:53:26Z
- **Completed:** 2026-07-07T20:58:23Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- `bastion/keystore/session.py` implements the full session-wallet lifecycle: `generate()` (unique per call), `save()` (cloud-sync/empty-dir guarded, atomic 0600 write of a ciphertext-only blob), `load()` (fail-closed on wrong passphrase via `KeystoreWrongPassphraseError`, plus a second `Keypair.from_bytes` validation layer), `retire()` (best-effort file removal + in-memory bytearray zeroization).
- `SessionKeypair` holds its secret as a mutable `bytearray` (not immutable `bytes`) specifically so `zeroize()` can overwrite it in place; `__repr__`/`__str__` always render `secret=REDACTED`.
- A dedicated `tests/unit/test_keystore_no_secret_leak.py` regression exercises generate -> save -> load (correct) -> load (wrong, expect fail-closed) -> retire under `capfd` + `caplog` with a grep-unmistakable sentinel passphrase, proving SEC-01 end-to-end rather than per-module.

## Task Commits

Each task was committed atomically (TDD RED/GREEN split preserved for Task 1, matching the convention from 02-01 through 02-04):

1. **Task 1 (RED): SessionKeypair lifecycle tests** - `2a4904e` (test)
2. **Task 1 (GREEN): SessionKeypair implementation** - `df0f070` (feat)
3. **Task 2: No-secret-leak full-flow regression** - `886076e` (test)

**Plan metadata:** (this commit, following SUMMARY.md write)

## Files Created/Modified
- `bastion/keystore/session.py` - SessionKeypair dataclass + generate/save/load/retire lifecycle functions
- `tests/unit/test_keystore_session.py` - 16 tests covering generate uniqueness, redacted repr, atomic 0600 write, ciphertext-only blob, exact roundtrip, fail-closed wrong passphrase, cloud-sync/empty-dir guards, retire (delete + zeroize + tolerate-absent + bare-pubkey)
- `tests/unit/test_keystore_no_secret_leak.py` - capfd+caplog regression over the full lifecycle with a sentinel passphrase

## Decisions Made
- Preserved the phase's established two-commit-per-task TDD split (test-only RED, then feat GREEN) for Task 1, consistent with 02-01 through 02-04's pattern noted in STATE.md.
- Task 2's test uses `capfd` only, not `capsys` + `capfd` together — pytest raises `cannot use capfd and capsys at the same time` when both are requested in one test function. Kept `capfd` per 02-RESEARCH.md's explicit guidance that it is the stronger guarantee here since `cryptography` and `solders` are compiled C-extensions that could write directly to an OS file descriptor.

## Deviations from Plan

None - plan executed exactly as written. The `capsys`-exclusion above is a test-authoring detail resolving an ambiguity in the plan's illustrative code example (which showed `capsys, caplog` in the signature, not `capsys, capfd, caplog` together) — not a change to any behavior or acceptance criterion.

## Issues Encountered

None. `uv run pytest -q` for the full suite: 85 passed, 1 skipped (the POSIX-only exact-0600 assertion, correctly skipped on this Windows execution environment per the plan's own Windows-conditional test guidance), 1 pre-existing warning (unrelated, from `test_config.py`'s intentional non-https scheme test).

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

Phase 02 (Encrypted Keystore + Key-Safety Invariants) is now fully implemented: crypto core (02-02), vault isolation (02-03), cloud-sync/passphrase safety rails (02-04), and the assembled session lifecycle with its cross-cutting no-secret-leak proof (02-05). Phase 3 (funder/sweeper) can now build on `bastion.keystore.session`'s `generate`/`save`/`load`/`retire` API and the existing `bastion.keystore.vault.load_vault` isolation boundary.

One residual note carried to CI/release checklist: the exact-0600 permission assertion (`test_save_writes_exact_0600_permissions_on_posix`) has never executed on this Windows dev machine — it must be confirmed green on a POSIX runner (Linux/macOS CI) before relying on it as a verified guarantee, per the plan's own Manual-Only Verification note.

---
*Phase: 02-encrypted-keystore-key-safety-invariants*
*Completed: 2026-07-07*
