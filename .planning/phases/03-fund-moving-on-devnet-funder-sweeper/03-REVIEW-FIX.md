---
phase: 03-fund-moving-on-devnet-funder-sweeper
fixed_at: 2026-07-08T02:54:13Z
review_path: .planning/phases/03-fund-moving-on-devnet-funder-sweeper/03-REVIEW.md
iteration: 1
findings_in_scope: 5
fixed: 5
skipped: 0
status: all_fixed
---

# Phase 3: Code Review Fix Report

**Fixed at:** 2026-07-08T02:54:13Z
**Source review:** .planning/phases/03-fund-moving-on-devnet-funder-sweeper/03-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 5 (1 Critical, 4 Warning — Info findings deferred per `fix_scope: critical_warning`)
- Fixed: 5
- Skipped: 0

All fixes were applied inside an isolated git worktree, verified with a full
`uv run pytest -q -m "not devnet"` pass after every change (130 passed, 1
skipped, 3 deselected devnet tests — unchanged pass count throughout), and
committed atomically. The worktree branch was fast-forwarded onto `main`
and cleaned up after the last commit.

## Fixed Issues

### CR-01: `land_check`'s in-loop RPC calls are unguarded — a benign resend/poll failure aborts the loop and can look like a failed transaction that actually landed

**Files modified:** `bastion/land_check.py`, `tests/unit/test_land_check.py`
**Commit:** `ad0163a`
**Applied fix:** Wrapped the per-iteration `getSignatureStatuses` poll in a
`try/except RpcError` that treats a transport-level failure as "unknown,
retry next poll" rather than aborting the loop. The best-effort `send_raw`
resend (triggered only by a `None`/ambiguous status) already swallowed
`RpcError`, but it was nested inside a naive single try/except in the
review's own suggested patch that would have also swallowed the
authoritative on-chain `err` raise — corrected the control flow so the
explicit on-chain-`err` raise is never caught by either transport-level
`except` clause, keeping it immediate and authoritative as required by the
fund-moving correctness mandate. Never re-signs; only ever re-POSTs the
identical `signed_b64` blob (D-08 preserved).
**Regression tests added:**
`test_resend_failure_from_expired_blockhash_does_not_abort_already_landed_tx`
(reproduces the exact already-landed + expired-blockhash-resend scenario
from the finding, asserts `land_check` returns success and exactly one
resend was *attempted*, not a second real transfer) and
`test_transient_status_poll_failure_does_not_abort_loop` (a rate-limit-style
transport failure on the status poll itself must not abort the loop).
`test_explicit_err_raises_rpc_error` (pre-existing) still passes,
confirming a genuine on-chain failure is still raised immediately.

### WR-01: Sub-lamport `amount_sol` passes validation but silently produces a signed, sent, fee-costing zero-lamport transfer

**Files modified:** `bastion/funder.py`, `tests/unit/test_funder.py`
**Commit:** `211a28a`
**Applied fix:** Added a refuse-before-send check immediately after the
lamport conversion (`amount_lamports = round(amount_sol * LAMPORTS_PER_SOL)`)
and before `load_vault(config)` — any `amount_sol` that rounds to 0
lamports now raises `FunderInvalidAmountError` with no RPC calls made,
exactly as suggested in the review.
**Regression test added:** `test_sub_lamport_amount_raises_before_any_rpc_call`
asserts `fund_session(..., 1e-10)` raises `FunderInvalidAmountError` and
`route.call_count == 0`.

### WR-02: `session.retire()`'s D-10 guard treats `token_accounts=None`/unknown as "safe to delete" (fail-open, not fail-closed)

**Files modified:** `bastion/keystore/session.py`, `tests/unit/test_session_retire.py`, `tests/unit/test_keystore_session.py`, `tests/unit/test_keystore_no_secret_leak.py`
**Commit:** `1ddcd48`
**Applied fix:** Added a keyword-only `token_check_skipped: bool = False`
parameter. `token_accounts=None` now raises `KeystoreRetireError`
("token balance was not checked") unless the caller explicitly passes
`token_check_skipped=True`, per the fund-moving correctness mandate's
fail-closed requirement — a failed/omitted RPC lookup can no longer be
silently treated the same as a confirmed-zero balance. An empty list (or a
list whose entries are all zero) remains a verified-empty no-op, unchanged.
Path-traversal/invalid-pubkey rejection still happens before this check
(`_safe_pubkey` runs first), so those pre-existing tests were unaffected.
**Blast radius note:** this changes the default contract for every existing
Phase-2 call site that passed no `token_accounts` at all. Per the module's
own docstring, those are legitimate "pre-Phase-3 callers that intentionally
never look up token balances" — updated all five such call sites (in
`test_keystore_session.py` and `test_keystore_no_secret_leak.py`, neither of
which is in REVIEW.md's `files_reviewed_list` but both of which call
`retire()` without `token_accounts`) to pass `token_check_skipped=True`
explicitly, preserving their original intent under the new explicit
contract. No production call site exists yet (grep confirmed `retire()` is
only called from tests and mentioned in a comment in `sweeper.py`), so no
runtime behavior changes for shipped code paths.
**Regression tests added/changed:** replaced the two "backward compat"
tests that asserted bare `None`/omitted `token_accounts` succeeds with
`test_retire_raises_when_token_accounts_is_none_and_not_skipped` and
`test_retire_raises_when_token_accounts_omitted_and_not_skipped` (assert
`KeystoreRetireError`, file/secret untouched), plus two new tests proving
the explicit opt-out still works exactly as before
(`test_retire_proceeds_when_token_accounts_is_none_and_check_explicitly_skipped`,
`..._omitted_and_check_explicitly_skipped`).

### WR-03: Sweep no-op boundary (`balance <= fee`) at the exact `balance == fee` edge skips ATA closes the session could have fully afforded

**Files modified:** `bastion/sweeper.py`, `tests/unit/test_sweeper.py`
**Commit:** `9d42bb2`
**Applied fix:** Changed the no-op condition from `balance <= fee` to
`balance < fee or (balance == fee and not close_ixs)`, exactly as
suggested — the exact-equal boundary now still sweeps (closing every empty
ATA, recovering rent to the vault) when there is at least one empty ATA to
close, since the fee is paid either way and closing costs the session
nothing extra. The true sub-fee dust no-op (`balance < fee`) and the
already-empty/no-ATAs case (`balance == fee` with nothing to close) are
both preserved.
**Regression tests added:**
`test_balance_equals_fee_with_empty_ata_still_closes_and_sweeps` (asserts
`swept: True`, `closed_atas: 1`, the empty ATA closed, and a 0-lamport
final transfer landing the session at exactly zero) and
`test_balance_equals_fee_with_no_atas_is_still_noop` (asserts the no-op
path is preserved when there is nothing to recover).

### WR-04: No idempotency guard against a caller retrying `fund_session`/`sweep_session` after an ambiguous failure

**Files modified:** `bastion/funder.py`, `bastion/sweeper.py`
**Commit:** `2150cc9`
**Applied fix:** This finding's own Fix section frames it as "an
architectural gap best closed at the CLI/caller layer, not necessarily
inside `funder.py` itself" and explicitly recommends, at minimum,
documenting the hazard. Applied exactly that: extended both `fund_session`'s
and `sweep_session`'s docstrings with an explicit "WR-04 — retry hazard"
paragraph describing the no-built-in-idempotency contract, that
`fund_session` has no self-correcting property on retry (unlike
`sweep_session`, which lands on the D-07 dust no-op), and that callers must
re-check on-chain state before retrying rather than treating any exception
as safe-to-retry. Documentation-only change; no behavioral/test changes
required (Tier 1 re-read + `ast.parse` syntax check + full suite rerun all
passed).

## Skipped Issues

None — all in-scope findings (CR-01, WR-01, WR-02, WR-03, WR-04) were
fixed. Info-tier findings (IN-01 through IN-05) were left untouched per
`fix_scope: critical_warning`.

---

_Fixed: 2026-07-08T02:54:13Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
