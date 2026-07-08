---
phase: 03-fund-moving-on-devnet-funder-sweeper
reviewed: 2026-07-08T02:59:23Z
depth: standard
files_reviewed: 8
files_reviewed_list:
  - bastion/land_check.py
  - bastion/funder.py
  - bastion/sweeper.py
  - bastion/keystore/session.py
  - tests/unit/test_land_check.py
  - tests/unit/test_funder.py
  - tests/unit/test_sweeper.py
  - tests/unit/test_session_retire.py
findings:
  critical: 0
  warning: 0
  info: 5
  total: 5
status: issues_found
---

# Phase 3: Code Review Report (Iteration 2 — Fix Verification)

**Reviewed:** 2026-07-08T02:59:23Z
**Depth:** standard
**Files Reviewed:** 8
**Status:** issues_found (Info only — no Critical/Warning findings)

## Summary

This is a targeted re-review verifying the four fixes applied since the iteration-1 review (CR-01 land_check double-spend guard, WR-01 sub-lamport reject, WR-03 sweeper balance==fee boundary, WR-02 retire fail-closed on `None` token_accounts, plus the WR-04 docstring hazard note). All four fixes were traced line-by-line against their stated intent, cross-checked against `RpcClient`'s error hierarchy (`bastion/rpc/client.py`, `bastion/rpc/errors.py`) to confirm every transport failure path actually raises a typed `RpcError` subclass (so `land_check`'s `except RpcError` genuinely covers all transport failures, not just a subset), and confirmed against the full test suite for these four modules: `python -m pytest tests/unit/test_land_check.py tests/unit/test_funder.py tests/unit/test_sweeper.py tests/unit/test_session_retire.py -q` → **30 passed**.

**Verification results for the four requested fixes — all CORRECT, no regressions found, no new double-spend/secret-leak/SEC-02 issues introduced:**

1. **`land_check.py` (CR-01 double-spend guard):** Sound.
   - Transport errors on the status poll (`except RpcError: statuses = None`, lines 63-69) never abort the loop — confirmed the loop falls through to `asyncio.sleep` and retries. `RpcTimeoutError`/`RpcRateLimitError` are both subclasses of `RpcError` (`bastion/rpc/errors.py:13,18`), and `RpcClient.call()` (`bastion/rpc/client.py:104-137`) wraps every `httpx` transport exception, JSON decode failure, and JSON-RPC error body into a typed `RpcError` — so this `except RpcError` genuinely catches every failure mode `RpcClient` can raise, not just a subset.
   - Transport errors on the best-effort resend (`except RpcError: pass`, lines 93-96) are likewise swallowed and never abort the loop. Critically, this resend `try/except` is nested *inside* the `else` branch that only runs when `status is None` (unknown) — it can structurally never suppress a genuine on-chain failure, which is checked in the sibling `if status is not None` branch first and unconditionally.
   - An authoritative on-chain `err` (`status.get("err") is not None`, lines 74-81) raises `RpcError` immediately regardless of how many prior transient transport failures occurred on earlier iterations — traced the interleaved-failure case explicitly (transient poll failure on iteration 1 → authoritative err on iteration 2) and confirmed it still raises correctly; this is also directly exercised by `test_explicit_err_raises_rpc_error` and `test_transient_status_poll_failure_does_not_abort_loop`.
   - `search_history=True` is passed unconditionally on every `getSignatureStatuses` call (line 64) — not gated behind a retry count or any conditional — so a landed-but-expired-blockhash tx is detected via chain history search on every poll from the first iteration onward.
   - No re-signing path exists anywhere in this module — `signed_b64` is the sole payload ever POSTed via `rpc.send_raw`; the module's import list (`asyncio`, `RpcClient`, `RpcError`, `RpcTimeoutError`) confirms there is no `Keypair`/message-rebuild capability present at all.
   - Test coverage is adequate and passing: `test_resend_failure_from_expired_blockhash_does_not_abort_already_landed_tx` and `test_transient_status_poll_failure_does_not_abort_loop` both directly exercise the CR-01 scenario.

2. **`funder.py` (WR-01 sub-lamport reject):** Sound. `amount_lamports = round(amount_sol * LAMPORTS_PER_SOL)` followed by `if amount_lamports < 1: raise FunderInvalidAmountError(...)` (lines 99-108) runs strictly before `load_vault(config)` (line 110) and before any RPC call (`get_balance`, `get_latest_blockhash`, `get_fee_for_message`, `send_raw`). `test_sub_lamport_amount_raises_before_any_rpc_call` confirms `route.call_count == 0` after calling with `amount_sol=1e-10` — no zero-value transfer is ever built, signed, or sent on this path.

3. **`session.py::retire()` (WR-02 fail-closed on `None`):** Sound. `token_check_skipped` (lines 232-238) only relaxes the `token_accounts is None` ambiguity check; it has zero effect on the separate nonzero-balance check (lines 240-251), which unconditionally raises `KeystoreRetireError` on any nonzero entry in a genuinely-passed `token_accounts` list regardless of the `token_check_skipped` value. Explicitly traced: passing `token_check_skipped=True` together with an actual nonzero `token_accounts` list still hits the `if nonzero: raise` branch — there is no code path by which `token_check_skipped=True` can suppress a real nonzero-balance finding. All 10 tests in `test_session_retire.py` pass, including the `token_check_skipped=True` cases, and correctly assert file/secret state on both sides of the guard.

4. **`sweeper.py` (WR-03 balance==fee boundary):** Sound. `if balance < fee or (balance == fee and not close_ixs):` (line 152) no-ops only true sub-fee dust or an exact-equal balance with nothing to close; an exact-equal balance *with* empty ATAs falls through to build and send the real transaction with `final_transfer_ix` carrying `balance - fee == 0` lamports, closing ATAs and recovering rent via `CloseAccount`'s own destination (never routed through the session's SOL balance, so it costs nothing extra). Both boundary cases pass: `test_balance_equals_fee_with_empty_ata_still_closes_and_sweeps` (`swept=True`, `closed_atas=1`, transfer amount `== 0`) and `test_balance_equals_fee_with_no_atas_is_still_noop` (`swept=False`, no send). The exact-zero arithmetic for the ordinary `balance > fee` case is unchanged and still verified by `test_transfer_amount_is_balance_minus_fee`.

SEC-02 isolation re-confirmed unbroken across all four files: `funder.py` still imports `bastion.keystore.vault` exclusively and is the only file among these that does; `sweeper.py` and `land_check.py` import neither `bastion.keystore.vault` nor hold/reference vault secret material anywhere in their current source.

The five Info items below are pre-existing, non-blocking robustness/consistency observations (none are regressions introduced by this iteration's fixes, none are fund-loss or double-spend risks, none are required to be fixed before shipping). Two carry over unresolved from the iteration-1 review; three were newly identified while tracing edge cases adjacent to the fixed code during this pass.

## Info

### IN-01: `land_check` indexes `statuses["value"][0]` without defending against a malformed/short response shape

**File:** `bastion/land_check.py:72`
**Issue:** `status = statuses["value"][0]` assumes the JSON-RPC response always contains a `"value"` key holding a list with at least one entry (matching the single-signature input array per the Solana RPC spec). A malformed/non-conformant response with a missing `"value"` key or an empty list would raise an untyped `KeyError`/`IndexError` that escapes `land_check` entirely, bypassing the typed `RpcError`/`RpcTimeoutError` contract every other failure mode in this function honors. The outcome is still fail-*safe* (no fund movement occurs, no failure is silently swallowed), but it is a robustness/consistency gap and would surface to callers as an unexpected exception type.
**Fix:**
```python
if statuses is not None:
    values = statuses.get("value") or []
    status = values[0] if values else None
    if status is not None:
        ...
```

### IN-02: `retire()`'s nonzero-balance check can raise an untyped `ValueError`/`KeyError` instead of `KeystoreRetireError` on malformed token amount data

**File:** `bastion/keystore/session.py:244`
**Issue:** `int(acc["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"])` raises a raw `ValueError`/`KeyError` (not `KeystoreRetireError`) on non-numeric or missing fields. The outcome is still fail-closed in practice (the exception propagates before `os.remove`/`zeroize()` are reached), so this is not a fund-safety issue, but it breaks this function's own documented contract ("raises `KeystoreRetireError`... fail-loud, never a silent skip") and the typed-error discipline `_safe_pubkey` already applies elsewhere in the same module.
**Fix:**
```python
if token_accounts:
    try:
        nonzero = [
            acc for acc in token_accounts
            if int(acc["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"]) > 0
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise KeystoreRetireError(
            "Cannot retire session keystore: malformed token_accounts entry."
        ) from exc
    if nonzero:
        raise KeystoreRetireError(...)
```

### IN-03 (carried over from iteration 1, unresolved): `float('inf')` for `amount_sol` is classified as `FunderCapExceededError` rather than `FunderInvalidAmountError`

**File:** `bastion/funder.py:80-90`
**Issue:** The D-03 cap check (`amount_sol > config.max_session_cap_sol`) still runs before the `math.isfinite(amount_sol)` check. `float('nan')` is unaffected (NaN comparisons are always `False`, so it correctly falls through to the finiteness check), but `float('inf')` compares `True` against any finite cap and is classified as "cap exceeded" rather than "invalid amount." A caller pattern-matching on exception type to decide whether to re-prompt for a smaller amount vs. reject the input outright would take the wrong branch for this one input. Not a fund-safety issue either way — both paths refuse-before-send — but the exception type is misleading.
**Fix:** Run `math.isfinite(amount_sol)` before the cap comparison.

### IN-04 (carried over from iteration 1, unresolved): `sweeper.py` reaches into `SessionKeypair._secret`, a leading-underscore attribute, from outside its owning module

**File:** `bastion/sweeper.py:171`
**Issue:** `session_kp = Keypair.from_bytes(bytes(session._secret))` still accesses a "private" field directly from another module. `SessionKeypair` exposes `zeroize()` as its one sanctioned mutator but has no equivalent sanctioned accessor for "give me a signable `Keypair`."
**Fix:** Add a small method on `SessionKeypair` (e.g. `to_keypair()`) that `sweeper.py` can call instead of reaching into the underscore-prefixed field.

### IN-05: SOL/lamport math uses native Python floats throughout, relying on `round()` to mask binary floating-point representation error

**File:** `bastion/funder.py:99` (`amount_lamports = round(amount_sol * LAMPORTS_PER_SOL)`)
**Issue:** This works correctly for the values exercised by the test suite and the new WR-01 guard closes the most direct exploit of this weakness (sub-lamport dust), but float remains a known-risky representation for money-denominated arithmetic. A future change (e.g. accepting amounts from untrusted string input with more decimal places, or chaining several float operations before this point) could reintroduce an off-by-one-lamport class of bug that `round()` alone won't reliably catch.
**Fix:** Consider accepting/working in integer lamports at the API boundary, or using `decimal.Decimal` for the SOL→lamport conversion, reserving float only for user-facing display.

---

_Reviewed: 2026-07-08T02:59:23Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
