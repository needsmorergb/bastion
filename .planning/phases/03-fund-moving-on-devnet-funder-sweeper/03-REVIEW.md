---
phase: 03-fund-moving-on-devnet-funder-sweeper
reviewed: 2026-07-07T00:00:00Z
depth: standard
files_reviewed: 15
files_reviewed_list:
  - bastion/funder.py
  - bastion/sweeper.py
  - bastion/land_check.py
  - bastion/fund_errors.py
  - bastion/rpc/client.py
  - bastion/keystore/session.py
  - bastion/keystore/errors.py
  - tests/unit/test_funder.py
  - tests/unit/test_sweeper.py
  - tests/unit/test_land_check.py
  - tests/unit/test_session_retire.py
  - tests/unit/test_keystore_vault_isolation.py
  - tests/e2e/conftest.py
  - tests/e2e/test_devnet_fund_sweep.py
  - pyproject.toml
findings:
  critical: 1
  warning: 4
  info: 5
  total: 10
status: issues_found
---

# Phase 3: Code Review Report

**Reviewed:** 2026-07-07T00:00:00Z
**Depth:** standard
**Files Reviewed:** 15
**Status:** issues_found

## Summary

The isolation contract (SEC-02) is solid and well-enforced: `funder.py` is the
only sanctioned importer of `bastion.keystore.vault` (verified structurally by
the AST-based test, and confirmed by reading `sweeper.py`/`land_check.py`,
neither of which import it or hold vault secret material). Refuse-before-send
guards in `funder.py` (D-03 cap, D-04 balance) run before any RPC call that
could sign/send, and the exact-fee arithmetic in both `funder.py` and
`sweeper.py` is correct for the ordinary case (verified against the unit
tests' byte-level decoding of the signed transaction).

The one **Critical** finding is in the shared `land_check.py` confirmation
loop: neither the per-poll `getSignatureStatuses` call nor the defensive
re-POST of the identical signed blob is guarded against raising. A `None`
(unknown/ambiguous) status is exactly the case D-08/D-09 exist to handle —
but if the re-POST itself fails (a routine, expected outcome once the
transaction's blockhash has aged out of validity, which is entirely possible
within `land_check`'s own 90s default budget), the resulting `RpcError`
escapes uncaught, aborts the loop, and is reported to the caller as a failure
for a transaction that may have already landed. That is precisely the
condition under which a caller retrying `fund_session`/`sweep_session` would
produce a duplicate vault debit — the architectural gap flagged in WR-04
compounds directly with this bug.

The remaining findings are smaller boundary/robustness issues in the
exact-zero sweep math, the D-10 retire guard's "unknown means proceed"
default, and a few code-quality/consistency nits.

## Critical Issues

### CR-01: `land_check`'s in-loop RPC calls are unguarded — a benign resend/poll failure aborts the loop and can look like a failed transaction that actually landed

**File:** `bastion/land_check.py:52-69`

**Issue:** The confirmation loop makes two RPC calls per iteration with no
exception handling around either:

```python
while elapsed < budget_s:
    statuses = await rpc.get_signature_statuses([signature], search_history=True)  # line 54
    status = statuses["value"][0]
    if status is not None:
        ...
    else:
        # Unknown, not failed (Pitfall 2). Re-POST the IDENTICAL blob —
        await rpc.send_raw(signed_b64)   # line 67 — UNGUARDED
    await asyncio.sleep(poll_interval_s)
    elapsed += poll_interval_s
```

Both `RpcClient.get_signature_statuses` and `RpcClient.send_raw` route
through `_request_with_backoff`, which raises `RpcError`/`RpcRateLimitError`/
`RpcTimeoutError` on non-retryable failures or exhausted retry budgets
(`bastion/rpc/client.py:104-137`). None of those exceptions are caught here.

The re-POST branch (line 67) is reached specifically when the status is
`None` — i.e. exactly the ambiguous case the module's own docstring says is
"unknown, not failed." A resend of an already-landed transaction is a
routine, *expected* outcome once its blockhash has fallen out of the
network's recent-blockhash validity window (~60-90s on mainnet/devnet). Given
the default `budget_s=90.0` / `poll_interval_s=1.5`, it is entirely possible
for a poll to land after that window closes, in which case `sendTransaction`
preflight will reject the resend (e.g. "Blockhash not found") and `call()`
raises `RpcError` — which is not the same event as "the transaction failed
on-chain." That RpcError propagates straight out of `land_check`, past
`fund_session`/`sweep_session` (neither of which catches it either), to the
caller — even though the original send may have already confirmed.

**Failure scenario:** `fund_session` sends the vault→session transfer, it
confirms on-chain, but the first `getSignatureStatuses` poll observes a
`None` status (a well-documented ambiguity — see the module's own "Pitfall
2" comment) close to the blockhash's expiry. The resend at line 67 fails with
`RpcError` ("Blockhash not found"). `land_check` raises, `fund_session`
raises, the CLI/caller sees an exception and reports "funding failed." If the
caller (or the user) then retries `fund_session` for the same intent, a
brand-new transaction is built, signed, and sent — the vault is now debited
**twice** for what should have been a single fund operation. The same logic
applies to `sweep_session`, though it is more self-healing there because a
resweep of an already-swept session hits the D-07 dust no-op path.

**Fix:** Treat both in-loop RPC calls as best-effort and never let a
transport-level failure abort the authoritative polling loop before its own
budget is exhausted:

```python
while elapsed < budget_s:
    try:
        statuses = await rpc.get_signature_statuses([signature], search_history=True)
        status = statuses["value"][0]
        if status is not None:
            if status.get("err") is not None:
                raise RpcError(f"transaction {signature} failed on-chain: {status['err']}")
            if status.get("confirmationStatus") in ("confirmed", "finalized"):
                return
        else:
            try:
                await rpc.send_raw(signed_b64)
            except RpcError:
                pass  # best-effort resend; the next status poll is authoritative
    except RpcError:
        # Do not let a transient status-check failure (rate limit, transport
        # blip) abort the loop before the budget is exhausted — only an
        # explicit on-chain `err` should raise early.
        pass
    await asyncio.sleep(poll_interval_s)
    elapsed += poll_interval_s
```
(Keep the explicit on-chain `err` raise as immediate/authoritative — only
wrap the *transport*-level exceptions, not a confirmed on-chain failure.)

---

## Warnings

### WR-01: Sub-lamport `amount_sol` passes validation but silently produces a signed, sent, fee-costing zero-lamport transfer

**File:** `bastion/funder.py:76-88`

**Issue:**
```python
if not math.isfinite(amount_sol) or amount_sol <= 0:
    raise FunderInvalidAmountError(...)
...
amount_lamports = round(amount_sol * LAMPORTS_PER_SOL)
```
`amount_sol` only needs to be a positive finite float to pass V5 — there is
no check that the *rounded lamport amount* is at least 1. For any
`0 < amount_sol < 5e-10` (half a lamport), `round(amount_sol * 1e9)` is `0`,
so `amount_lamports == 0`. The function still proceeds: builds a real
transfer instruction for 0 lamports, signs it with the vault key, sends it,
and runs a full `land_check`. The vault pays a real network fee and the
"funding" call succeeds while the session's balance is unchanged — directly
contradicting the module's own contract ("the session receives a clean,
round `amount_sol`", D-01).

**Fix:**
```python
amount_lamports = round(amount_sol * LAMPORTS_PER_SOL)
if amount_lamports < 1:
    raise FunderInvalidAmountError(
        f"amount_sol={amount_sol!r} rounds to {amount_lamports} lamports; "
        "must be at least 1 lamport"
    )
```
Move this check immediately after the lamport conversion, before
`load_vault(config)` is called.

### WR-02: `session.retire()`'s D-10 guard treats `token_accounts=None`/unknown as "safe to delete" (fail-open, not fail-closed)

**File:** `bastion/keystore/session.py:199-229`

**Issue:** The guard only inspects `token_accounts` when it is truthy:
```python
if token_accounts:
    nonzero = [... for acc in token_accounts if int(...) > 0]
    if nonzero:
        raise KeystoreRetireError(...)
```
`None` and `[]` are documented as "backward compatible, no-op" — which is a
reasonable design for genuinely pre-Phase-3 callers that never look up token
balances at all. But it also means: if a *new* caller attempts to look up
token accounts (as D-10/SESS-07 intends every retire call site to do) and
that lookup fails (RPC error, timeout, rate limit) and the caller — by
mistake or by a "fail open" habit — passes `None` through rather than
propagating the failure, `retire()` cannot distinguish "verified empty" from
"unknown/couldn't check," and will hard-delete the keystore either way. This
is the exact ambiguity the domain brief calls out: the guard's whole purpose
is to protect token funds from being orphaned by a deleted keystore, and its
one blind spot is "I don't know" being silently treated the same as
"confirmed zero."

None of the files reviewed in this phase show the actual call site that
supplies `token_accounts` to `retire()` (the CLI layer is out of scope here),
so this cannot be confirmed as an active bug today — but the API shape
invites the mistake.

**Fix:** Make "unknown" a distinct, explicit state rather than overloading
`None`/`[]`:
```python
def retire(
    session_or_pubkey: SessionKeypair | str,
    keystore_dir: str,
    token_accounts: list[dict] | None = None,
    *,
    token_check_skipped: bool = False,
) -> None:
    ...
    if token_accounts is None and not token_check_skipped:
        raise KeystoreRetireError(
            "Cannot retire: token balance was not checked. Pass "
            "token_check_skipped=True only for pre-Phase-3 callers that "
            "intentionally never look up token accounts."
        )
```
At minimum, document at the call site (CLI) that `token_accounts=None` must
only ever be passed when the caller *deliberately* opts out of the check —
never as a stand-in for "the RPC call failed."

### WR-03: Sweep no-op boundary (`balance <= fee`) at the exact `balance == fee` edge skips ATA closes the session could have fully afforded

**File:** `bastion/sweeper.py:143-146`

**Issue:**
```python
if balance <= fee:
    # D-07: sub-fee dust (or an already-empty session) -> no-op, NOT an error.
    return {"swept": False, "reason": "dust below fee reserve", "balance": balance}
```
When `balance == fee` exactly, the session has *precisely* enough SOL to pay
the network fee for a transaction — including any `close_account_ix`
instructions, whose ATA rent (`destination=vault_pk`) is credited directly to
the vault and does not touch the session's SOL balance at all. In this exact
case, sending the transaction would still land the session at 0 lamports
(fee fully consumes `balance`) and would additionally recover ATA rent to
the vault for free. Instead, the current code skips the whole transaction:
any already-empty ATAs are left open (their rent unrecovered) and the
session is left holding `fee` lamports instead of the documented exact-zero.
This is a narrow edge case, but it directly contradicts both the "exact-zero"
core property and the "no unrecovered rent" goal of the sweep for a case
that costs the session nothing to execute.

**Fix:** Change the boundary so the no-op only triggers when the balance is
*strictly less than* the fee, and additionally treat "no empty ATAs and
nothing to transfer" as its own no-op:
```python
if balance < fee or (balance == fee and not close_ixs):
    return {"swept": False, "reason": "dust below fee reserve", "balance": balance}
```
Add a unit test asserting `balance == fee` with at least one empty ATA
results in `swept: True`, the ATA closed, and the session at exactly 0.

### WR-04: No idempotency guard against a caller retrying `fund_session`/`sweep_session` after an ambiguous failure

**File:** `bastion/funder.py:49-122`, `bastion/sweeper.py:76-162`

**Issue:** Both functions build, sign, and send exactly one transaction per
invocation — correct in isolation — but neither has any way to detect "did a
previous call for this same intent already land?" before doing so. Combined
with CR-01 (where a transaction that actually landed can still surface as an
exception to the caller), a caller that treats any exception from
`fund_session` as "safe to retry" will build an entirely new transaction
(fresh blockhash, same amount) and debit the vault a second time. `sweeper`
is naturally more resilient here (a resweep of an already-swept session hits
the D-07 dust/no-op path and does nothing), but `funder` has no equivalent
self-correcting property — a duplicate `fund_session(..., 0.5)` call always
sends another 0.5 SOL.

**Fix:** This is an architectural gap best closed at the CLI/caller layer,
not necessarily inside `funder.py` itself, but the module should at least
document the hazard explicitly (its current docstring does not mention it).
Options: (a) have the caller re-check the session's current balance before
retrying and skip if it already reflects the intended top-up, or (b) persist
a "funding attempt" record with a client-generated correlation id so retries
can detect "already sent, just re-run land_check" versus "never sent."
Fixing CR-01 removes the most likely trigger for this scenario but does not
eliminate the class of risk on its own.

---

## Info

### IN-01: `float('inf')` raises `FunderCapExceededError` rather than `FunderInvalidAmountError`

**File:** `bastion/funder.py:69-79`
**Issue:** The D-03 cap check (`amount_sol > config.max_session_cap_sol`) runs
before the V5 finiteness check. `float('nan')` is unaffected (NaN comparisons
are always `False`, so it falls through to V5 as expected), but
`float('inf')` compares `True` and is classified as "cap exceeded" rather
than "invalid amount" — a caller pattern-matching on exception type to decide
whether to re-prompt for a smaller amount vs. reject the input outright would
get the wrong branch for this one input.
**Fix:** Run the `math.isfinite(amount_sol)` check before the cap comparison.

### IN-02: SOL/lamport math uses native Python floats throughout

**File:** `bastion/funder.py:88`, `bastion/sweeper.py` (amount comparisons)
**Issue:** `round(amount_sol * LAMPORTS_PER_SOL)` and float comparisons
against `config.max_session_cap_sol` rely on `round()` to mask binary
floating-point representation error. This works correctly for the values
exercised in the test suite, but float is a known-risky type for
money-denominated arithmetic; a future change (e.g. accepting amounts from
untrusted string input with more decimal places, or chaining several
float operations) could reintroduce an off-by-one-lamport class of bug that
`round()` alone won't reliably catch.
**Fix:** Consider accepting/working in integer lamports at the API boundary,
or using `decimal.Decimal` for the SOL→lamport conversion, reserving float
only for user-facing display.

### IN-03: `sweeper.py` reaches into `SessionKeypair._secret`, a "private" attribute, from outside its owning module

**File:** `bastion/sweeper.py:157`
**Issue:** `session_kp = Keypair.from_bytes(bytes(session._secret))` accesses
a leading-underscore field directly from another module. `SessionKeypair`
already exposes `zeroize()` as its one sanctioned mutator; there is no
equivalent sanctioned accessor for "give me a signable `Keypair`."
**Fix:** Add a small method on `SessionKeypair` (e.g. `to_keypair()`) that
sweeper.py (and the e2e tests, which do the same thing) can call instead of
reaching into the underscore-prefixed field.

### IN-04: `config.fee_reserve_lamports` is documented as a fee-lookup fallback but is never actually used as one

**File:** `bastion/funder.py` (module docstring), `bastion/sweeper.py:14-18, 87-91`
**Issue:** Both modules' docstrings describe `config.fee_reserve_lamports` as
"a fallback-only sanity floor," implying it is consulted when
`getFeeForMessage` returns `null`. In the actual code, both modules simply
raise `RpcError` immediately when `fee is None` (`funder.py:108-109`,
`sweeper.py:140-141`) — `config.fee_reserve_lamports` is never read anywhere
in either file. Failing closed here is the safer behavior and not itself a
bug, but the docstring describes a fallback path that does not exist in the
code, which will mislead the next person who reads it into thinking there is
a graceful-degradation path when there isn't one.
**Fix:** Either implement the described fallback (with care — using a stale
sanity-floor fee instead of the real one changes the exact-fee guarantee) or
update the docstrings to state plainly that a null fee is always fail-closed
and `fee_reserve_lamports` is unused by this code path today.

### IN-05: `retire()`'s token-amount parsing raises a raw, untyped exception on malformed input

**File:** `bastion/keystore/session.py:217-222`
**Issue:** `int(acc["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"])`
will raise a bare `KeyError` or `ValueError` if an entry is missing an
expected key or has a non-numeric amount, rather than the module's own typed
`Keystore*Error` hierarchy used everywhere else in this file (e.g.
`_safe_pubkey` explicitly converts `ValueError`/`TypeError` into
`KeystoreConfigError`). The failure is still safe (it aborts before
`os.remove`), but it's an inconsistent error-handling pattern within the
same module.
**Fix:** Wrap the parsing loop and re-raise as `KeystoreRetireError` (or a
new `KeystoreConfigError`) with a message that doesn't include the raw
account payload.

---

_Reviewed: 2026-07-07T00:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
