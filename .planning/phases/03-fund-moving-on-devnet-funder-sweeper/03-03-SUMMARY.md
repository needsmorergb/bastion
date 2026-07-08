---
phase: 03-fund-moving-on-devnet-funder-sweeper
plan: 03
subsystem: keystore
tags: [keystore, retire, error-handling, tdd, solders]

# Dependency graph
requires:
  - phase: 02-encrypted-keystore-key-safety-invariants
    provides: SessionKeypair, generate/save/load/retire lifecycle, KeystoreError family, _safe_pubkey path-traversal guard
provides:
  - "KeystoreRetireError(KeystoreError) typed exception"
  - "retire() extended with token_accounts parameter and the D-10 nonzero-token-balance guard"
  - "tests/unit/test_session_retire.py covering refusal + backward-compatible proceed paths"
affects: [sweeper, phase-7-cli-end-command]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "D-10 guard: caller-supplied token_accounts snapshot gates an irreversible file delete, keeping retire() synchronous and RPC-transport-agnostic"

key-files:
  created:
    - tests/unit/test_session_retire.py
  modified:
    - bastion/keystore/errors.py
    - bastion/keystore/session.py

key-decisions:
  - "Guard placed BEFORE os.remove and BEFORE zeroize() so the refusal path leaves both the keystore file and the in-memory secret fully untouched."
  - "token_accounts stays an optional parameter (default None) so retire() remains backward compatible with every existing Phase 2 call site and test."
  - "retire() does not import RpcClient/httpx — the caller (sweeper / Phase 7 CLI) is responsible for the fresh getTokenAccountsByOwner read, per D-10/PATTERNS.md."

patterns-established:
  - "Guard-before-mutate: any new irreversible-action guard in this codebase raises a typed KeystoreError subclass and returns before the mutating call, matching funder/sweeper's D-03/D-04/D-07 refuse-before-send posture."

requirements-completed: [SESS-07]

coverage:
  - id: D1
    description: "retire() refuses to hard-delete a keystore when a nonzero token balance remains in the session's ATAs, raising KeystoreRetireError and leaving the file on disk"
    requirement: "SESS-07"
    verification:
      - kind: unit
        ref: "tests/unit/test_session_retire.py#test_retire_raises_on_nonzero_token_balance_and_leaves_file_untouched"
        status: pass
      - kind: unit
        ref: "tests/unit/test_session_retire.py#test_retire_raises_when_any_of_several_accounts_is_nonzero"
        status: pass
      - kind: unit
        ref: "tests/unit/test_session_retire.py#test_retire_nonzero_balance_with_bare_pubkey_raises_and_leaves_file"
        status: pass
    human_judgment: false
  - id: D2
    description: "retire() proceeds exactly as before (file removed, secret zeroized) when token_accounts is all-zero, empty, None, or omitted — backward compatible"
    requirement: "SESS-07"
    verification:
      - kind: unit
        ref: "tests/unit/test_session_retire.py#test_retire_proceeds_when_all_token_accounts_are_zero"
        status: pass
      - kind: unit
        ref: "tests/unit/test_session_retire.py#test_retire_proceeds_when_token_accounts_is_empty_list"
        status: pass
      - kind: unit
        ref: "tests/unit/test_session_retire.py#test_retire_proceeds_when_token_accounts_is_none_backward_compat"
        status: pass
      - kind: unit
        ref: "tests/unit/test_session_retire.py#test_retire_proceeds_when_token_accounts_omitted_backward_compat"
        status: pass
      - kind: unit
        ref: "tests/unit/test_keystore_session.py (full existing retire suite, unmodified)"
        status: pass
    human_judgment: false

# Metrics
duration: 6min
completed: 2026-07-08
status: complete
---

# Phase 3 Plan 3: D-10 Retire Guard Summary

**`retire()` refuses to hard-delete a session keystore when a nonzero token balance remains in its ATAs, raising a new `KeystoreRetireError` and leaving the file untouched — backward compatible when no token_accounts snapshot is supplied.**

## Performance

- **Duration:** 6 min
- **Started:** 2026-07-08T02:09:00Z
- **Completed:** 2026-07-08T02:11:07Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- `KeystoreRetireError(KeystoreError)` added to the keystore error family, following the existing plain-message-only, no-secret-material contract.
- `retire()` extended to `retire(session_or_pubkey, keystore_dir, token_accounts: list[dict] | None = None)` with the D-10 guard inserted before `os.remove`: any ATA with `tokenAmount.amount` parsing to an int > 0 triggers a raise, leaving the file on disk and the in-memory secret un-zeroized.
- Full TDD RED → GREEN cycle: `test_session_retire.py` written first and confirmed failing (6/7 cases, since the pre-existing 2-arg call already matched the old signature), then made to pass by the guard implementation.
- Backward compatibility preserved: all pre-existing `test_keystore_session.py` retire tests (including bare-pubkey and path-traversal cases) remain green untouched.

## Task Commits

Each task was committed atomically:

1. **Task 1: Add KeystoreRetireError to the keystore error family** - `8f0d739` (feat)
2. **Task 2: Extend retire() with the D-10 nonzero-token-balance guard (SESS-07)** - TDD cycle:
   - RED: `a389143` (test) - failing tests for the guard added first
   - GREEN: `29391ff` (feat) - guard implementation, tests pass

**Plan metadata:** (this commit, following SUMMARY)

## Files Created/Modified
- `bastion/keystore/errors.py` - Added `KeystoreRetireError(KeystoreError)`
- `bastion/keystore/session.py` - `retire()` gains `token_accounts` param + D-10 guard, updated docstring
- `tests/unit/test_session_retire.py` - New test file covering refusal (file untouched, secret not zeroized) and backward-compatible proceed paths (all-zero / empty / None / omitted token_accounts, bare-pubkey variant)

## Decisions Made
- Guard runs strictly before both `os.remove` and `zeroize()` so a refusal is a true no-op on all state (file + in-memory secret) — verified explicitly by asserting the secret bytes are still non-zero after a refusal-path raise.
- Kept `token_accounts` as the third positional/keyword parameter with a `None` default rather than a required argument, so every Phase 2 call site (`test_keystore_session.py`'s existing 2-arg retire calls) continues to work unchanged.
- Followed PATTERNS.md's guard shape but used `KeystoreRetireError` (per the plan's explicit Task 1 artifact) instead of PATTERNS.md's example `KeystoreConfigError`, since the plan intentionally introduces a new, more specific error type for this refusal.

## Deviations from Plan

None - plan executed exactly as written.

## TDD Gate Compliance

Gate sequence verified in git log:
- RED gate: `a389143` `test(03-03): add failing tests for D-10 retire token-balance guard` (confirmed failing pre-implementation: 6 of 7 new tests raised `TypeError`/`Failed` before the guard existed).
- GREEN gate: `29391ff` `feat(03-03): extend retire() with D-10 nonzero-token-balance guard` (all tests pass after).
- No REFACTOR commit needed — implementation was minimal and required no follow-up cleanup.

## Issues Encountered
None.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- SESS-07 fully delivered: sessions can be retired safely post-sweep, and the guard prevents orphaning token value left behind by the SOL-only v1 sweep (D-06).
- The sweeper (03-02, if not yet complete) and Phase 7's `end` CLI command are the intended future callers of `retire(..., token_accounts=...)`, passing a freshly-read `get_token_accounts_by_owner` result — `retire()` itself has zero RPC/httpx dependency, preserving its transport-agnostic contract.
- No blockers for remaining Phase 3 plans (03-02 sweeper, 03-04 devnet e2e tests) — this plan touched only `bastion/keystore/errors.py`, `bastion/keystore/session.py`, and `tests/unit/test_session_retire.py`, disjoint from funder/sweeper/rpc files.

---
*Phase: 03-fund-moving-on-devnet-funder-sweeper*
*Completed: 2026-07-08*

## Self-Check: PASSED

- FOUND: bastion/keystore/errors.py
- FOUND: bastion/keystore/session.py
- FOUND: tests/unit/test_session_retire.py
- FOUND commit: 8f0d739
- FOUND commit: a389143
- FOUND commit: 29391ff
