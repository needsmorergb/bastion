---
phase: 03-fund-moving-on-devnet-funder-sweeper
plan: 01
subsystem: payments
tags: [solana, solders, funder, vault, land-check, rpc, tdd]

# Dependency graph
requires:
  - phase: 02-encrypted-keystore-key-safety-invariants
    provides: bastion/keystore/vault.py::load_vault(config), the AST
      vault-isolation test, and the fail-loud KeystoreConfigError contract
provides:
  - "RpcClient.get_signature_statuses() — new JSON-RPC helper"
  - "bastion/land_check.py::land_check() — shared chain-based confirmation
    loop (D-08/D-09) importable by both funder and the future sweeper
    without importing vault.py"
  - "bastion/fund_errors.py — FunderError family
    (CapExceeded/InsufficientBalance/InvalidAmount)"
  - "bastion/funder.py::fund_session()/fund_session_sync() — capped,
    exact-N vault->session funding with refuse-before-send guards"
  - "funder.py added as the sole sanctioned importer of vault.py alongside
    vault.py itself in the AST isolation test"
affects: [03-02-sweeper, 03-03-retire-guard, 03-04-devnet-e2e]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Land-check as a standalone top-level module (not folded into
      funder.py) so a future vault-secret-free sweeper can share it"
    - "Refuse-before-send: cap/amount/pubkey guards run before any RPC call;
      balance guard runs after two read-only RPC calls but before signing"
    - "Async core + asyncio.run() top-level sync wrapper for CLI one-shots"

key-files:
  created:
    - bastion/land_check.py
    - bastion/fund_errors.py
    - bastion/funder.py
    - tests/unit/test_land_check.py
    - tests/unit/test_funder.py
  modified:
    - bastion/rpc/client.py
    - tests/unit/test_keystore_vault_isolation.py

key-decisions:
  - "land_check lives in its own top-level bastion/land_check.py module
    (not inside funder.py) specifically so the Plan 03-02 sweeper can
    import it without ever importing the vault-privileged funder module"
  - "get_signature_statuses adds no new retry logic; it routes through the
    existing self.call()/_request_with_backoff path exactly like every
    other RpcClient method"
  - "Insufficient-balance check runs after get_balance + get_fee_for_message
    (both read-only, no signed tx) but strictly before signing/sending —
    satisfies the plan's refuse-before-send contract for D-04"

patterns-established:
  - "Pattern: shared confirmation-loop module importable by multiple
    vault-secret-tiered callers without violating import isolation"

requirements-completed: [SESS-02, SESS-03, SEC-02]

coverage:
  - id: D1
    description: "Funder funds a handed-in session pubkey with exactly N
      SOL (vault debited N + fee); happy path returns a confirmed
      signature (D-01, SESS-02)"
    requirement: "SESS-02"
    verification:
      - kind: unit
        ref: "tests/unit/test_funder.py#test_happy_path_builds_exact_transfer_and_returns_signature"
        status: pass
      - kind: unit
        ref: "tests/unit/test_funder.py#test_equal_to_cap_proceeds"
        status: pass
    human_judgment: false
  - id: D2
    description: "Requests strictly greater than MAX_SESSION_CAP raise
      FunderCapExceededError and issue zero RPC calls (D-03, SESS-03)"
    requirement: "SESS-03"
    verification:
      - kind: unit
        ref: "tests/unit/test_funder.py#test_cap_exceeded_raises_and_makes_zero_rpc_calls"
        status: pass
    human_judgment: false
  - id: D3
    description: "A vault balance that cannot cover N + fee raises
      FunderInsufficientBalanceError and sends nothing (D-04)"
    verification:
      - kind: unit
        ref: "tests/unit/test_funder.py#test_insufficient_balance_raises_and_sends_nothing"
        status: pass
    human_judgment: false
  - id: D4
    description: "land_check never re-signs on an ambiguous (null) status;
      it re-POSTs the identical signed blob and confirmation is declared
      at commitment confirmed (D-08/D-09)"
    verification:
      - kind: unit
        ref: "tests/unit/test_land_check.py#test_null_status_then_confirmed_resends_identical_blob_only"
        status: pass
      - kind: unit
        ref: "tests/unit/test_land_check.py#test_budget_exhaustion_raises_rpc_timeout_error"
        status: pass
    human_judgment: false
  - id: D5
    description: "funder.py is the only module besides vault.py permitted
      to import bastion.keystore.vault; the AST isolation test enforces
      it (D-02, SEC-02)"
    requirement: "SEC-02"
    verification:
      - kind: unit
        ref: "tests/unit/test_keystore_vault_isolation.py#test_only_allowlisted_modules_import_vault"
        status: pass
    human_judgment: false

duration: ~15min
completed: 2026-07-07
status: complete
---

# Phase 3 Plan 1: Funder + Land-Check Summary

**Capped, exact-N vault->session funding (`fund_session`) built on a new shared chain-based land-check loop, with funder.py as the sole additional sanctioned importer of vault.py.**

## Performance

- **Duration:** ~15 min
- **Started:** 2026-07-07T19:00Z (approx, following phase-plan finalization)
- **Completed:** 2026-07-07T19:06Z
- **Tasks:** 2
- **Files modified:** 7 (4 created, 3 modified)

## Accomplishments
- `RpcClient.get_signature_statuses()` added, mirroring the existing `self.call()` idiom with no new retry logic.
- `bastion/land_check.py::land_check()` — a standalone, vault-agnostic confirmation loop shared by funder (this plan) and the future sweeper (Plan 03-02): returns on `confirmed`/`finalized`, raises `RpcError` on an explicit `err`, re-POSTs the *identical* signed blob on an unknown (`null`) status (never re-signs, D-08), and raises `RpcTimeoutError` once `budget_s` is exhausted so the loop always terminates.
- `bastion/fund_errors.py` — `FunderError` base plus `FunderCapExceededError`, `FunderInsufficientBalanceError`, `FunderInvalidAmountError`, each documenting the D-## it enforces.
- `bastion/funder.py::fund_session()` — builds one System transfer for exactly `amount_sol` lamports-worth of SOL vault->session, refusing before any send when the cap is exceeded (D-03), the amount/pubkey are invalid (V5), or the vault balance can't cover `amount + exact fee` (D-04). Signs with the vault key only, sends, and land-checks. `fund_session_sync()` provides the CLI one-shot wrapper.
- `tests/unit/test_keystore_vault_isolation.py`'s `ALLOWED_IMPORTERS` extended with `bastion/funder.py` — the AST isolation test stays green with funder.py as the one sanctioned addition.

## Task Commits

Each task followed the TDD RED -> GREEN cycle with its own commits:

1. **Task 1: get_signature_statuses + land_check**
   - `78fdf27` (test) - failing land_check tests (confirmed/err/null-resend/timeout)
   - `a2d2bfd` (feat) - get_signature_statuses + land_check implementation, tests green
2. **Task 2: funder.py exact-N funding + vault-isolation allowlist**
   - `9e31b0b` (test) - failing funder tests + fund_errors.py typed-error module
   - `de2cbbc` (feat) - fund_session/fund_session_sync implementation + allowlist edit, tests green

## Files Created/Modified
- `bastion/rpc/client.py` - added `get_signature_statuses`
- `bastion/land_check.py` - new shared confirmation-loop module
- `bastion/fund_errors.py` - new typed error hierarchy for the funder
- `bastion/funder.py` - new `fund_session`/`fund_session_sync`
- `tests/unit/test_land_check.py` - new test suite
- `tests/unit/test_funder.py` - new test suite
- `tests/unit/test_keystore_vault_isolation.py` - `ALLOWED_IMPORTERS` extended with `bastion/funder.py`

## Decisions Made
- `land_check` was kept out of `funder.py` and placed in its own top-level module specifically so Plan 03-02's sweeper can import the confirmation loop without ever importing the vault-privileged funder module — preserves the narrowest possible vault-secret blast radius per the phase's guiding steer.
- The D-04 balance guard executes after `get_balance` + `get_fee_for_message` (both read-only) but strictly before `VersionedTransaction` construction/signing — this satisfies "refuse-before-send" (no signed tx is ever built when the guard trips) while still needing the exact fee to make the comparison.
- `fund_session_sync` mirrors `get_balance_sync`'s `asyncio.run()`-at-top-level pattern exactly, including the "never call from inside a running event loop" docstring warning.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- `bastion/land_check.py` is ready for Plan 03-02's sweeper to import directly (no vault.py dependency).
- `bastion/funder.py` is the complete, tested funder half of SEC-02; Plan 03-02 (sweeper) must NOT import `bastion.keystore.vault` — the AST isolation test now fails the build if it does, since `ALLOWED_IMPORTERS` was extended with exactly `bastion/funder.py` and nothing else.
- Full unit suite green (110 passed, 1 skipped, no network) at the end of this plan.
- No blockers for Plan 03-02 (sweeper) or 03-03 (retire guard).

---
*Phase: 03-fund-moving-on-devnet-funder-sweeper*
*Completed: 2026-07-07*
