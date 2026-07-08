---
phase: 03-fund-moving-on-devnet-funder-sweeper
plan: 02
subsystem: payments
tags: [solders, solana, spl-token, sweeper, rpc, exact-zero]

# Dependency graph
requires:
  - phase: 03-01
    provides: land_check() shared confirmation loop (D-08/D-09) and the base RpcClient surface (get_balance, get_latest_blockhash, get_fee_for_message, get_signature_statuses, send_raw)
provides:
  - "bastion/sweeper.py — sweep_session() async core, sweep_session_sync() wrapper, close_account_ix() hand-encoded SPL CloseAccount builder"
  - "RpcClient.get_token_accounts_by_owner() — jsonParsed SPL Token account enumeration used by the sweeper and the D-10 retire guard"
  - "Exact-zero sweep: balance - getFeeForMessage(confirmed) transferred in one atomic tx that also closes empty ATAs (rent -> vault)"
  - "Explicit SEC-02 negative-contract test proving sweeper.py never imports bastion.keystore.vault"
affects: [phase-07-cli-assembly, phase-06-armed-auto-sweep, phase-04-persistence]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Hand-encoded SPL Token CloseAccount instruction (discriminant byte 9, 3 account metas) — no new dependency"
    - "Probe-message-then-final-message compile pattern to size fee before fixing the exact-zero transfer amount"

key-files:
  created:
    - bastion/sweeper.py
    - tests/unit/test_sweeper.py
  modified:
    - bastion/rpc/client.py
    - tests/unit/test_keystore_vault_isolation.py

key-decisions:
  - "Sweeper reads Config.vault_pubkey only (never bastion.keystore.vault) and signs with the SESSION Keypair reconstructed via Keypair.from_bytes(bytes(session._secret)) — SEC-02 structurally enforced, not just documented"
  - "Exact-zero transfer amount = balance - fee, where fee comes from getFeeForMessage(confirmed) against a probe MessageV0 (close ixs + zero-lamport placeholder transfer) compiled before the final message — FEE_RESERVE_LAMPORTS is never read by sweep_session"
  - "ATA classification (getTokenAccountsByOwner) is read as the LAST RPC call before building instructions, minimizing the window for Pitfall 3's classification-race"

patterns-established:
  - "Sync wrapper for sweeper mirrors funder.py's asyncio.run()-at-top-level pattern (sweep_session_sync)"

requirements-completed: [SESS-06, SEC-02]

coverage:
  - id: D1
    description: "Sweeper transfers balance minus the exact getFeeForMessage(confirmed) fee to the vault (exact-zero, D-05)"
    requirement: "SESS-06"
    verification:
      - kind: unit
        ref: "tests/unit/test_sweeper.py::test_transfer_amount_is_balance_minus_fee"
        status: pass
    human_judgment: false
  - id: D2
    description: "One atomic transaction closes every empty ATA (rent -> vault) and leaves nonzero ATAs untouched (D-06)"
    requirement: "SESS-06"
    verification:
      - kind: unit
        ref: "tests/unit/test_sweeper.py::test_one_empty_ata_closed_nonzero_left_untouched"
        status: pass
    human_judgment: false
  - id: D3
    description: "Sub-fee dust and already-empty sessions return a no-op result and raise nothing (D-07)"
    requirement: "SESS-06"
    verification:
      - kind: unit
        ref: "tests/unit/test_sweeper.py::test_dust_below_fee_is_noop"
        status: pass
      - kind: unit
        ref: "tests/unit/test_sweeper.py::test_already_empty_session_is_noop"
        status: pass
    human_judgment: false
  - id: D4
    description: "sweeper.py is structurally incapable of importing bastion.keystore.vault (SEC-02)"
    requirement: "SEC-02"
    verification:
      - kind: unit
        ref: "tests/unit/test_keystore_vault_isolation.py::test_sweeper_does_not_import_vault"
        status: pass
    human_judgment: false
  - id: D5
    description: "Session Keypair secret never leaks into the sweep result or captured stdout/stderr"
    verification:
      - kind: unit
        ref: "tests/unit/test_sweeper.py::test_no_secret_leak_in_result_and_output"
        status: pass
    human_judgment: false

duration: 20min
completed: 2026-07-08
status: complete
---

# Phase 3 Plan 2: Sweeper (Exact-Zero Session -> Vault) Summary

**Built `bastion/sweeper.py`'s exact-zero atomic sweep (balance minus the real `getFeeForMessage` fee, empty-ATA closing to the vault in one transaction) plus `RpcClient.get_token_accounts_by_owner`, structurally incapable of touching the vault secret.**

## Performance

- **Duration:** ~20 min
- **Tasks:** 2
- **Files modified:** 4 (2 created, 2 modified)

## Accomplishments
- `RpcClient.get_token_accounts_by_owner()` added, mirroring the existing method idiom (no new retry logic — routes through `self.call()`/`_request_with_backoff` transparently).
- `bastion/sweeper.py`: `sweep_session()` async core reads balance + fresh ATA classification, builds `close_account_ix` for each empty ATA, compiles a probe message to get the exact fee via `getFeeForMessage(confirmed)`, and sets the final transfer to `balance - fee` for a true exact-zero landing (D-05/D-06). Sub-fee dust and already-empty sessions return `{"swept": False, ...}` without raising (D-07). `sweep_session_sync()` thin wrapper added for CLI one-shots.
- `close_account_ix()` hand-encodes the SPL Token `CloseAccount` instruction (discriminant byte `9`, 3 account metas) — no new dependency, per RESEARCH's rejection of `solana-py`.
- Explicit SEC-02 negative-contract test (`test_sweeper_does_not_import_vault`) added alongside the existing subset-based `ALLOWED_IMPORTERS` check, so the sweeper's vault-isolation guarantee fails the build directly rather than only implicitly.

## Task Commits

Each task was committed atomically:

1. **Task 1: get_token_accounts_by_owner + sweeper.py exact-zero atomic sweep** - `0d93389` (feat)
2. **Task 2: SEC-02 negative contract — assert sweeper never imports vault.py** - `e6a486b` (test)

**Plan metadata:** (this commit, docs — see below)

## Files Created/Modified
- `bastion/sweeper.py` - `sweep_session()`, `sweep_session_sync()`, `close_account_ix()`, `TOKEN_PROGRAM_ID`
- `bastion/rpc/client.py` - added `get_token_accounts_by_owner()`
- `tests/unit/test_sweeper.py` - exact-zero transfer, dust/already-empty no-op, ATA classification, no-secret-leak
- `tests/unit/test_keystore_vault_isolation.py` - added `test_sweeper_does_not_import_vault`

## Decisions Made
- Used `solders.system_program.ID` (not a hand-typed System Program pubkey string) in tests to decode instructions reliably — avoids a base58-length typo class of bug when asserting on compiled transaction bytes.
- Kept the vault-isolation contract's prose comment free of the literal string `load_vault` so the acceptance-criteria grep (`grep -c "load_vault" bastion/sweeper.py == 0`) checks the intent (no reference to the loader) rather than just the import statement.

## Deviations from Plan

None - plan executed exactly as written. The one adjustment (avoiding the literal string "load_vault" in a comment) was a self-correction to satisfy the plan's own acceptance criterion, not a scope change.

## Issues Encountered
- Initial test file hardcoded the System Program ID as a 43-character base58 string, which is wrong for an all-zero 32-byte pubkey (base58 collapses leading zero bytes) — fixed by importing `solders.system_program.ID` directly instead of a hand-typed constant.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- `sweep_session`/`sweep_session_sync` are ready for Phase 7's CLI assembly (`end` command) and Phase 6's armed auto-sweep (async core callable directly from the monitor loop).
- `get_token_accounts_by_owner` is available for 03-03's D-10 retire guard (already wired in a sibling plan) and any future token-related work.
- Full unit suite green: `uv run pytest tests/unit -q` — 123 passed, 1 skipped (pre-existing).

---
*Phase: 03-fund-moving-on-devnet-funder-sweeper*
*Completed: 2026-07-08*

## Self-Check: PASSED

- FOUND: bastion/sweeper.py
- FOUND: tests/unit/test_sweeper.py
- FOUND: 0d93389 (Task 1 commit)
- FOUND: e6a486b (Task 2 commit)
