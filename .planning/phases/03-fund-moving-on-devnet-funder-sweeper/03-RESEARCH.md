# Phase 3: Fund-Moving on Devnet (Funder + Sweeper) - Research

**Researched:** 2026-07-08
**Domain:** Solana transaction construction (`solders` 0.27.x), SPL Token account closing, exact-fee/exact-zero arithmetic, chain-based idempotency, devnet e2e testing
**Confidence:** HIGH (core transaction-building mechanics verified by direct execution against the installed `solders==0.27.1` package; RPC method shapes CITED from official `solana.com` docs; SPL Token instruction encoding CITED from canonical secondary sources)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Funding Amount Semantics**
- D-01: Fund N SOL -> session ends with exactly N SOL. Vault is debited `N + tx_fee`; session receives a clean, round N. `MAX_SESSION_CAP` is compared against N.
- D-02: `funder.py` funds a handed-in session pubkey; it does NOT mint the keystore. Keystore minting stays in `session.py`. Rationale: minimize code running with the vault secret in scope (SEC-02). Funder does only: `load_vault()` -> build System transfer to destination pubkey -> sign with vault -> send -> land-check. Never needs the session secret, only a destination address.
- D-03: Cap refusal is refuse-before-send. Exceeding `MAX_SESSION_CAP` raises a typed error and sends zero transactions (SESS-03). Equal-to-cap is allowed; only strictly-greater is refused.
- D-04: Insufficient-vault-balance is also pre-checked and refused. Query vault balance first; if it cannot cover `N + estimated_fee`, raise typed error, send nothing.

**Exact-Zero Sweep Mechanics**
- D-05: Exact fee via `getFeeForMessage(commitment="confirmed")`. Build the sweep message, look up the precise fee, then transfer `balance - exact_fee`. `FEE_RESERVE_LAMPORTS` demoted to a sanity floor/fallback used only if the RPC fee lookup fails.
- D-06: One atomic sweep transaction. Single signed tx carries `closeAccount` instructions for empty ATAs (rent destination = vault) plus a System transfer of `(SOL_balance - fee)` to the vault. All-or-nothing, single fee. If empty-ATA count overflows one tx's size limit, batch across multiple txs (rare for v1 single-user).
- D-07: Sub-fee dust -> no-op, leave it. If balance > 0 but too small to cover the sweep fee, return a "nothing sweepable" result and leave the dust. Do NOT raise.

**No-Double-Spend / Idempotency (chain-based, no DB this phase)**
- D-08: Blockhash-scoped signed-tx reuse. Sign exactly once -> one deterministic signature. On timed-out/uncertain send, NEVER re-sign; poll that signature's status and/or re-send the identical signed blob (Solana dedups by signature within the blockhash validity window). Only after the original blockhash has provably expired is it safe to rebuild with a fresh blockhash and retry. No durable-nonce accounts in v1.
- D-09: Land-check waits for `confirmed` (~1-2s, supermajority-voted) for both funder and sweeper.

**Retire Guard (SESS-07 / success criterion 5)**
- D-10: Extend `session.retire()` to refuse hard-delete when a nonzero token balance remains in the session's ATAs. Follow the existing typed fail-loud error contract (`KeystoreError` family) — raise, never silently skip.

### Claude's Discretion
- Token/ATA detection: use `getTokenAccountsByOwner` to enumerate the session's ATAs and classify empty (closeable) vs nonzero (untouched). Requires a new RPC helper.
- Confirmation-poll helper: RPC client lacks `getSignatureStatuses` and a confirmation/land-check loop; add whatever helper is cleanest. Poll interval, per-poll timeout, total wait budget (bounded by the blockhash validity window) are at Claude's discretion.
- Sync vs async surface: RPC client is async; funder/sweeper cores should be async with thin sync wrappers where CLI one-shots need them (mirroring `get_balance_sync`), since Phase 6's armed auto-sweep will call the sweeper from within the async monitor loop. Exact signatures at Claude's discretion.
- Exact module/function signatures, instruction-building details, error-type names, and the fee-reserve fallback trigger conditions are Claude's discretion within the decisions above.

### Deferred Ideas (OUT OF SCOPE)
- Token auto-liquidation on sweep — v2 `sweep_tokens` stub; v1 sweeps SOL-only and leaves nonzero-token ATAs (why the D-10 retire guard exists).
- Durable-nonce idempotency — considered and rejected for v1 (D-08).
- Session rotation (`--rotate-on-loss`) — Phase 7, not a funding/sweeping primitive.
- `finalized`-before-retire hardening — considered, chose `confirmed` (D-09) for v1.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SESS-02 | User can fund a session wallet from the vault with a specified SOL cap | See "Funder Flow" pattern + Code Examples (transfer instruction, exact-N funding) |
| SESS-03 | System refuses to fund when the requested cap exceeds `MAX_SESSION_CAP` | See "Refuse-Before-Send Guard" pattern; no RPC/tx-building call happens before the check |
| SESS-06 | User can sweep remaining SOL back to the vault on manual session end | See "Sweeper Flow" pattern + exact-zero fee arithmetic (D-05/D-06) |
| SESS-07 | User can retire a session keystore after it has been swept | See "Retire Guard" pattern + `getTokenAccountsByOwner` helper for the nonzero-token check |
| SEC-02 | Vault secret loaded only for funding; sweeps target `VAULT_PUBKEY` and need no vault secret | See "Architectural Responsibility Map" + AST isolation test update guidance |
</phase_requirements>

## Summary

Phase 3 builds two new top-level modules, `bastion/funder.py` and `bastion/sweeper.py`, on top of already-proven Phase 1/2 infrastructure (`RpcClient`, `Config`, `vault.py`, `session.py`). Every transaction-building primitive this phase needs — System transfer, `MessageV0` compilation, `VersionedTransaction` signing/serialization — exists directly in `solders==0.27.1` and was verified by executing it against the actually-installed package in this repo's environment. The one gap is SPL Token's `close_account` instruction: `solders` 0.27.x ships no SPL Token instruction builders at all (only an ATA-address helper and account-state decoders), and the natural library that would provide it (`solana`/`solana-py`, PyPI `solana` 0.40.0) is flagged `SUS` by the package-legitimacy gate (recency heuristic — the package itself is a 6+-year-old, widely used, actively-maintained SDK; see the Audit table) and — independent of that flag — pulling in a full RPC-wrapping SDK for one three-account, one-byte-discriminant instruction directly contradicts this project's own CLAUDE.md guidance to minimize dependencies on the fund-moving path. The recommendation is to hand-encode `close_account` directly via `solders.instruction.Instruction` + `AccountMeta` (verified to construct correctly against the installed `solders` package) rather than add a new dependency.

The funder is intentionally the thinnest possible vault-secret-privileged code path (D-02): load vault -> build one System transfer instruction for exactly `N` lamports-worth of SOL to the session pubkey -> compile message -> get exact fee via `getFeeForMessage` to pre-check `vault_balance >= N + fee` (D-04) -> sign with the vault keypair only -> send -> land-check at `confirmed`. The cap guard (D-03) and balance guard (D-04) both run and raise *before* any RPC call that touches the network with a signed transaction, satisfying "refuse-before-send" literally.

The sweeper is structurally incapable of loading the vault secret: it only ever imports `Config.vault_pubkey` (a string) and takes a `SessionKeypair` from `session.load()`. It builds one atomic transaction (D-06) that closes every already-empty ATA (rent -> vault) and transfers `balance - exact_fee` (computed via `getFeeForMessage` against the fully-built message, D-05) to the vault, so the session lands at exactly zero lamports in a single all-or-nothing send.

No-double-spend (D-08) is chain-native, not application-native: sign once, and on any uncertain outcome (timeout, connection drop) never re-sign — either poll `getSignatureStatuses` for the existing signature or re-POST the identical base64 blob (Solana's leader/validator set dedups by signature within the ~150-slot/~60-90s blockhash validity window, so re-sending the same bytes is safe and cannot double-spend). Only a *provable* blockhash expiry (a `null` `getFeeForMessage` result, or `BlockhashNotFound`/"block height exceeded" from `getSignatureStatuses` after the window has passed) licenses building a fresh transaction with a new blockhash.

**Primary recommendation:** Build `close_account` by hand with `solders.instruction.Instruction`/`AccountMeta` (no new dependency); use `getFeeForMessage` against the fully-compiled `MessageV0` for both the funder's pre-send balance check and the sweeper's exact-zero transfer amount; add two RPC-client helpers (`get_signature_statuses`, `get_token_accounts_by_owner`) following the existing `RpcClient` method pattern; implement the land-check as "poll `getSignatureStatuses` on a fixed interval, re-POST the identical signed blob on ambiguous timeout, only rebuild after the blockhash window provably expires."

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Vault secret parsing (`load_vault`) | Backend/CLI (funder.py only) | — | SEC-02: single sanctioned importer of `vault.py`; must stay the smallest possible vault-secret-privileged surface |
| Capped fund transfer (System transfer, sign, send) | Backend/CLI (funder.py) | RPC/Backend (RpcClient) | Funder owns tx construction; RpcClient owns transport/retry — funder never talks HTTP directly |
| Cap/balance refusal guards (D-03/D-04) | Backend/CLI (funder.py) | — | Must execute before any network call touching a signed tx — pure in-process validation, no tier crossing |
| Exact-fee lookup (`getFeeForMessage`) | RPC/Backend (RpcClient) | Backend/CLI (funder/sweeper callers) | Already exists on `RpcClient`; funder/sweeper are callers, not implementers, of the RPC method |
| ATA enumeration + empty/nonzero classification | RPC/Backend (new RpcClient helper) | Backend/CLI (sweeper.py, session.py retire guard) | `getTokenAccountsByOwner` is a transport-tier concern; classification logic (amount==0) is a thin caller-side helper reused by both the sweeper and the retire guard |
| Atomic sweep transaction (closeAccount* + transfer) | Backend/CLI (sweeper.py) | RPC/Backend (RpcClient) | Sweeper composes instructions and signs with the session key only; RpcClient sends |
| Land-check / confirmation polling | RPC/Backend (new RpcClient helper: `get_signature_statuses`) | Backend/CLI (funder/sweeper shared land-check loop) | Polling primitive belongs on the RPC client; the retry/backoff *policy* (interval, budget, re-send-vs-rebuild decision) belongs to the caller since funder and sweeper share identical land-check semantics (D-08/D-09) |
| Keystore retire + token-balance guard | Backend/CLI (session.py, extended) | RPC/Backend (getTokenAccountsByOwner via sweeper or a shared helper) | `retire()` already owns the keystore file; the D-10 guard needs an on-chain read, making this the one place `session.py` needs an RPC dependency it didn't have before |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `solders` | 0.27.1 (installed; pyproject pins `>=0.27,<0.29`) | Keypair, `Pubkey`, `system_program.transfer`, `Instruction`/`AccountMeta`, `MessageV0`, `VersionedTransaction` | Already the project's sole tx-building dependency (Phase 1/2 established this); every primitive Phase 3 needs is present and was verified working against this exact installed version. [VERIFIED: solders 0.27.1 installed package] |
| `httpx` | 0.28.x (installed; pyproject pins `>=0.28,<0.29`) | Transport for the two new RPC helpers (`get_signature_statuses`, `get_token_accounts_by_owner`) | Already the project's RPC transport (`bastion/rpc/client.py`); no new dependency needed. [VERIFIED: bastion/rpc/client.py] |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| stdlib `base64` | bundled | Encode message/tx bytes for `getFeeForMessage`/`sendTransaction` | Every RPC call carrying a message or signed tx already needs this; no new dependency |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Hand-encoded `close_account` `Instruction` | `solana` (solana-py) PyPI package's `spl.token.instructions.close_account` | Gives a typed `CloseAccountParams` wrapper instead of a raw byte literal, but pulls in a large SDK (its own `AsyncClient`, retry logic, etc. — all unused, since `bastion/rpc/client.py` is already the project's transport) purely to save ~5 lines of instruction-building code. Flagged `SUS` by the package-legitimacy gate (recency heuristic; see Audit table) and contradicts this project's own CLAUDE.md guidance to minimize fund-path dependencies. **Not recommended** — hand-encoding is the primary recommendation. |
| Application-level idempotency (a "sent" flag in a local file/DB) | Chain-native dedup (D-08, re-send identical signed blob / poll signature status) | Phase 4 (SQLite store) doesn't exist yet this phase — CONTEXT.md explicitly scopes idempotency here as chain-based, not DB-based. Revisit once Phase 4 lands if a persisted send-attempt log becomes valuable defense-in-depth. |

**Installation:** No new dependencies required. `solders` and `httpx` are already in `pyproject.toml`.

**Version verification:**
```
pip show solders   -> Version: 0.27.1   (installed, matches pyproject pin >=0.27,<0.29)
pip show httpx      -> Version: 0.28.0   (installed, matches pyproject pin >=0.28,<0.29)
```
No `uv add` step is needed for this phase — confirm this explicitly in the plan so a planner doesn't assume a Wave 1 dependency-install task is required (unlike Phase 1/2, which each had one).

## Package Legitimacy Audit

> This phase adds **zero new dependencies** to `pyproject.toml` (see Alternatives Considered — the `close_account` instruction is hand-encoded from `solders` primitives already installed). The audit below documents the one package that was evaluated and **rejected**, so the planner does not re-propose it.

| Package | Registry | Age | Downloads | Source Repo | Verdict | Disposition |
|---------|----------|-----|-----------|--------------|---------|-------------|
| `solana` (solana-py) | PyPI | Latest release 2026-06-27 (project itself dates to ~2021, 60+ historical releases 0.0.3 → 0.40.0) | Not returned by the seam (`unknown-downloads`) | github.com/michaelhly/solana-py | `SUS` (`too-new`, `unknown-downloads`) | **REMOVED / not adopted** — see rationale below |

**Rationale for disposition:** The `SUS` verdict's `too-new` signal is a recency-heuristic false positive — it measures the *latest release* date, not the package's founding date. `solana-py` has 60+ published versions dating back years, a well-known GitHub repo (`michaelhly/solana-py`), and is solders' own sibling project (same author ecosystem — `solders` was originally extracted from `solana-py`'s performance-critical core). Phase 2's STATE.md records an identical false-positive pattern for `cryptography`/`solders` under the same heuristic. This package is **not** being rejected as illegitimate/hallucinated — it is being rejected on architectural grounds (unnecessary dependency weight on the fund-moving path, redundant with the project's own `RpcClient`), consistent with this project's CLAUDE.md "Alternatives Considered" table, which already argues against `solana-py` for exactly this reason. If a future phase decides differently, re-run the legitimacy check fresh (age signals will have moved on) and route it through a `checkpoint:human-verify` per protocol since the current run still returned `SUS`.

**Packages removed due to `[SLOP]` verdict:** none — no package returned a `SLOP` verdict.
**Packages flagged as suspicious `[SUS]`:** `solana` (solana-py) — evaluated and explicitly not adopted (see rationale); no checkpoint needed since it is not being installed.

## Architecture Patterns

### System Architecture Diagram

```
                     ┌────────────────────────┐
                     │   CLI one-shot caller    │   (Phase 7 wires this;
                     │  (sync wrapper entry)    │    Phase 3 exposes the
                     └───────────┬──────────────┘    async cores + thin
                                 │                    sync wrappers only)
                                 ▼
   FUNDER FLOW                                    SWEEPER FLOW
   ┌─────────────────────────┐                   ┌──────────────────────────┐
   │ funder.fund_session(     │                   │ sweeper.sweep_session(    │
   │   config, session_pubkey,│                   │   config, session_kp)     │
   │   amount_sol)            │                   └──────────┬─────────────┘
   └───────────┬──────────────┘                              │
               │ 1. cap check (D-03): amount > MAX_SESSION_CAP? -> raise, STOP (no RPC)
               │ 2. load_vault(config)  <-- ONLY funder.py imports vault.py (SEC-02)
               ▼
     RpcClient.get_balance(vault_pubkey)
               │ 3. build System transfer(vault -> session, amount lamports)
               │ 4. compile MessageV0 -> RpcClient.get_fee_for_message(confirmed)
               │ 5. balance guard (D-04): vault_balance >= amount + fee? -> else raise, STOP
               ▼
     sign with vault Keypair -> VersionedTransaction
               │ 6. RpcClient.send_raw(base64 signed tx)
               ▼
     land-check loop (shared helper, D-08/D-09):
       poll RpcClient.get_signature_statuses([sig])
       -> confirmed: done
       -> ambiguous/timeout: re-send IDENTICAL blob or re-poll (never re-sign)
       -> blockhash provably expired: rebuild with fresh blockhash, retry from step 3

                                                   1. RpcClient.get_balance(session_pubkey)
                                                   2. RpcClient.get_token_accounts_by_owner(
                                                        session_pubkey, TOKEN_PROGRAM_ID, jsonParsed)
                                                      -> classify empty (amount==0) vs nonzero ATAs
                                                   3. build [closeAccount(empty_ata, vault, session)*,
                                                             ] instructions (rent -> vault)
                                                   4. append System transfer(session -> vault, TBD)
                                                   5. compile MessageV0 -> get_fee_for_message(confirmed)
                                                   6. dust guard (D-07): balance <= fee? -> no-op, STOP
                                                   7. set transfer amount = balance - fee (exact zero)
                                                   8. sign with SESSION key ONLY (never vault secret)
                                                   9. send_raw + land-check (same shared helper as funder)
                                                      -> session lands at EXACTLY 0 lamports

   RETIRE FLOW (session.py, extended)
   ┌─────────────────────────────┐
   │ session.retire(pubkey, dir)  │
   └──────────────┬────────────────┘
                  │ 1. RpcClient.get_token_accounts_by_owner(pubkey) -- reuse sweeper's helper
                  │ 2. any ATA with amount > 0? -> raise KeystoreError (D-10), keystore file untouched
                  │ 3. else -> proceed with existing delete + zeroize flow
                  ▼
            keystore file removed, in-memory secret zeroized
```

### Recommended Project Structure
```
bastion/
├── funder.py            # NEW: fund_session() async core + sync wrapper; ONLY module besides
│                         #      vault.py itself allowed to import bastion.keystore.vault (SEC-02)
├── sweeper.py            # NEW: sweep_session() async core + sync wrapper; imports Config.vault_pubkey
│                         #      only (never vault.py) + session.load()'s SessionKeypair
├── rpc/
│   ├── client.py          # EXTENDED: + get_signature_statuses(), + get_token_accounts_by_owner()
│   └── errors.py          # unchanged — funder/sweeper raise the existing RpcError family plus
│                           #    new typed errors (e.g. FunderCapExceededError) in a new errors module
│                           #    or bastion/errors.py, at Claude's discretion
├── keystore/
│   └── session.py         # EXTENDED: retire() gains the D-10 nonzero-token-balance guard
└── config.py               # unchanged — max_session_cap_sol, fee_reserve_lamports already present
```

### Pattern 1: Build-Sign-Fee Order (funder and sweeper both use this)
**What:** Instruction(s) are built BEFORE the fee is known, because `getFeeForMessage` requires a fully-compiled message. Only after the exact fee comes back can the final transfer *amount* be fixed (sweeper) or the pre-send balance check be finalized (funder).
**When to use:** Any transaction where an amount depends on the fee (sweeper's exact-zero) or where a pre-send guard needs the real fee (funder's D-04 balance check) — i.e., every transaction this phase builds.
**Example:**
```python
# Source: verified directly against installed solders==0.27.1
from solders.system_program import transfer, TransferParams
from solders.message import MessageV0
from solders.transaction import VersionedTransaction
import base64

ix = transfer(TransferParams(
    from_pubkey=vault_kp.pubkey(),
    to_pubkey=session_pubkey,
    lamports=amount_lamports,
))
message = MessageV0.try_compile(
    vault_kp.pubkey(),   # payer
    [ix],
    [],                   # no address lookup tables
    blockhash,            # solders.hash.Hash, from getLatestBlockhash
)
message_b64 = base64.b64encode(bytes(message)).decode()
fee_result = await rpc.get_fee_for_message(message_b64, commitment="confirmed")
fee_lamports = fee_result["value"]  # int, or None if blockhash expired -> rebuild
if fee_lamports is None:
    raise RpcError("blockhash expired before fee lookup; rebuild with a fresh blockhash")

# Funder (D-04): guard BEFORE signing/sending
if vault_balance < amount_lamports + fee_lamports:
    raise FunderInsufficientBalanceError(...)  # send nothing

tx = VersionedTransaction(message, [vault_kp])
signed_b64 = base64.b64encode(bytes(tx)).decode()
sig = await rpc.send_raw(signed_b64)
```

### Pattern 2: Hand-Encoded `close_account` Instruction (no new dependency)
**What:** SPL Token's `CloseAccount` instruction is a single-byte discriminant (`9`) with no further instruction data, and three account metas. `solders` 0.27.x provides `Instruction`/`AccountMeta` but not this specific builder, so it is composed directly.
**When to use:** Every empty ATA the sweeper closes.
**Example:**
```python
# Discriminant + account-meta ordering: CITED (docs.rs spl-token TokenInstruction;
# michaelhly.com/solana-py close_account source). Construction verified against
# installed solders==0.27.1.
from solders.instruction import Instruction, AccountMeta
from solders.pubkey import Pubkey

TOKEN_PROGRAM_ID = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")

def close_account_ix(ata_pubkey: Pubkey, destination: Pubkey, owner: Pubkey) -> Instruction:
    """SPL Token CloseAccount: transfers the ATA's rent lamports to `destination`,
    deletes the account. Requires the ATA's token amount to already be zero (the
    Token Program itself enforces this — the caller's own empty/nonzero
    classification via getTokenAccountsByOwner is what decides WHICH atas to
    include, but a stale read racing a concurrent deposit is still possible;
    the on-chain program is the final backstop, not this classification)."""
    return Instruction(
        TOKEN_PROGRAM_ID,
        bytes([9]),  # CloseAccount discriminant, no further data
        [
            AccountMeta(pubkey=ata_pubkey, is_signer=False, is_writable=True),
            AccountMeta(pubkey=destination, is_signer=False, is_writable=True),
            AccountMeta(pubkey=owner, is_signer=True, is_writable=False),
        ],
    )
```

### Pattern 3: Refuse-Before-Send Guards (D-03, D-04)
**What:** Both the cap guard and the balance guard raise a typed error and return before any instruction is built or any RPC call that could result in a signed transaction reaching the network.
**When to use:** `funder.fund_session()` entry, before touching `vault.py` at all for the cap check (it needs no chain data), and before signing for the balance check (it needs one `get_balance` + one `get_fee_for_message` call, both read-only).
**Example:**
```python
def fund_session(config: Config, session_pubkey: str, amount_sol: float) -> ...:
    if amount_sol > config.max_session_cap_sol:  # D-03: strictly-greater only
        raise FunderCapExceededError(
            f"requested {amount_sol} SOL exceeds MAX_SESSION_CAP "
            f"({config.max_session_cap_sol} SOL)"
        )
    # ... only now does anything touch the vault secret or the network
```

### Pattern 4: Land-Check Loop (shared by funder and sweeper, D-08/D-09)
**What:** After `send_raw`, poll for confirmation. On timeout/ambiguous failure, never re-sign — re-poll or re-POST the identical blob. Only rebuild with a fresh blockhash once the original blockhash is provably dead.
**When to use:** Every send in this phase (funder's single transfer, sweeper's atomic sweep tx).
**Recommended budget (Claude's discretion, grounded in the ~150-slot/~60-90s window):** poll every 1-2s, total budget capped at ~90s (the outer edge of the blockhash window) so the loop always terminates at or before the point a rebuild becomes necessary anyway.
```python
async def land_check(rpc: RpcClient, signature: str, signed_b64: str,
                      *, poll_interval_s: float = 1.5, budget_s: float = 90.0) -> None:
    elapsed = 0.0
    while elapsed < budget_s:
        statuses = await rpc.get_signature_statuses([signature], search_history=True)
        status = statuses["value"][0]
        if status is not None:
            if status.get("err") is not None:
                raise RpcError(f"transaction {signature} failed on-chain: {status['err']}")
            if status.get("confirmationStatus") in ("confirmed", "finalized"):
                return  # D-09: confirmed is sufficient
        else:
            # not found yet -- re-POST the IDENTICAL blob (never re-sign).
            # Solana dedups by signature; this cannot double-spend.
            await rpc.send_raw(signed_b64)
        await asyncio.sleep(poll_interval_s)
        elapsed += poll_interval_s
    # Budget exhausted without a terminal status: only NOW is it safe to check
    # whether the blockhash has provably expired (e.g. re-run get_fee_for_message
    # against the original message and check for value: null) before rebuilding.
    raise RpcTimeoutError(
        f"land-check for {signature} exceeded {budget_s}s without a terminal status"
    )
```

### Anti-Patterns to Avoid
- **Re-signing on timeout:** Building a fresh `VersionedTransaction` and re-signing after a send timeout, even with the *same* blockhash, risks two valid signed blobs racing on-chain if the first one actually landed — always re-send the identical bytes or poll instead (D-08).
- **Trusting `FEE_RESERVE_LAMPORTS` as the primary fee source:** D-05 demotes it to a fallback-only sanity floor. Using it as the transfer-amount subtrahend in the sweeper would produce dust, not exact zero, whenever the real network fee differs from the configured constant.
- **Classifying ATAs from a stale/cached read:** the empty/nonzero classification from `getTokenAccountsByOwner` must be read fresh immediately before building the sweep tx, not cached from an earlier call in the same session — a concurrent deposit between read and send is still possible; the Token Program's own zero-balance enforcement on `CloseAccount` is the real backstop.
- **Importing `vault.py` from `sweeper.py`:** structurally forbidden by SEC-02 and enforced by the AST isolation test — the sweeper must only ever hold `Config.vault_pubkey` (a string) and the session's own `Keypair`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|--------------|-----|
| Transaction serialization/signing | A manual borsh/wire-format encoder | `solders.transaction.VersionedTransaction` + `bytes(tx)` | Verified correct roundtrip (`VersionedTransaction.from_bytes(bytes(tx))` reproduces identical signatures) against the installed package — no reason to hand-encode the transaction envelope itself (only the one *instruction* solders lacks, `close_account`, is hand-encoded, and that's a 1-byte payload, not a serialization format) |
| Retry/backoff on RPC calls | A second retry loop inside funder/sweeper | The existing `RpcClient._request_with_backoff` (already handles 429/5xx with jittered exponential backoff and a typed `RpcRateLimitError`) | Phase 1 already solved this; funder/sweeper are callers of `RpcClient`, not reimplementers of its retry policy |
| Fee estimation | A hardcoded/estimated lamports-per-signature constant as the *primary* fee source | `getFeeForMessage(commitment="confirmed")` against the actually-compiled message (D-05) | Fees vary with signature count and (for future compute-budget instructions) priority fees; a hardcoded estimate cannot achieve exact-zero and would leave dust or, worse, underestimate and fail to send |

**Key insight:** The only genuinely custom code this phase needs beyond what `solders` already provides is the ~10-line `close_account` instruction builder (Pattern 2) — everything else (signing, serialization, fee lookup, retry/backoff, confirmation polling primitives) is either already built (Phase 1's `RpcClient`) or a thin, well-documented wrapper around a `solders` primitive that was verified to work exactly as expected.

## Common Pitfalls

### Pitfall 1: `getFeeForMessage` defaults to `finalized`, not `confirmed`
**What goes wrong:** Omitting the `commitment` param (or passing it positionally in a way that gets dropped) silently reverts to the RPC's own `finalized` default, adding ~10-15s of latency the D-09 land-check budget doesn't expect, and (more subtly) can return a *different* fee than what will actually be charged at send time if network fee levels are volatile between `confirmed` and `finalized` state.
**Why it happens:** The JSON-RPC method's own default commitment is `finalized`; nothing in the wire protocol forces the caller to be explicit.
**How to avoid:** The existing `RpcClient.get_fee_for_message` already always passes `commitment` explicitly (Phase 1 Pitfall 4, verified in `test_get_fee_for_message_uses_confirmed_commitment`) — reuse it as-is, don't build a second fee-lookup path.
**Warning signs:** A sweep transfer amount that's off by more than the observed fee, or land-checks that take noticeably longer than ~2s on devnet.

### Pitfall 2: Treating a `getSignatureStatuses` `null` entry as "failed" instead of "unknown"
**What goes wrong:** A `null` status entry means the signature simply hasn't been found in the queried cache yet (or ever) — it does NOT mean the transaction failed or was rejected. Treating `null` as a failure and immediately rebuilding-and-resigning is exactly the double-spend risk D-08 exists to prevent (the original tx may still land after the null read).
**Why it happens:** `null` is easy to conflate with an error state when skimming the response shape.
**How to avoid:** Only treat an explicit `status.err != None` as failure. A `null` entry (especially before `searchTransactionHistory: true` is tried) means "keep polling / re-send the identical blob," never "rebuild."
**Warning signs:** Duplicate-looking transfers on devnet during timeout-injection testing (the smoking gun for a re-sign-on-null bug).

### Pitfall 3: Racing the ATA classification against the sweep send
**What goes wrong:** If `getTokenAccountsByOwner` is called well before the sweep tx is built and sent (e.g. cached from an earlier `status` command), a deposit landing in the gap could mean the sweeper tries to close an ATA that's no longer empty. The on-chain Token Program will reject the whole atomic tx (D-06's all-or-nothing property protects fund safety), but the user sees an opaque failure instead of a clear "state changed under you, retry" message.
**Why it happens:** Classification and transaction-building are naturally two separate RPC round-trips; nothing forces them to be temporally adjacent.
**How to avoid:** Call `getTokenAccountsByOwner` as the *last* read before building the sweep instructions, and surface the Token Program's rejection reason distinctly from other send failures (so the CLI/monitor layer in later phases can auto-retry the whole `sweep_session()` call cleanly — since D-06 guarantees atomicity, a clean retry from scratch is always safe).
**Warning signs:** An intermittent sweep failure that only reproduces when a devnet e2e test deposits tokens concurrently with a sweep.

### Pitfall 4: `MessageV0.try_compile` account-key ordering surprises for fee estimation
**What goes wrong:** `try_compile` deduplicates and orders account keys (payer first, then writable signers, then read-only signers, then writable non-signers, then read-only non-signers/programs) — this is correct and required, but a naive re-implementation of message compilation (rather than using `try_compile`) is a common source of "works on 2 accounts, breaks on 5" bugs, especially once the sweeper starts appending multiple `closeAccount` instructions plus the final transfer in one message.
**Why it happens:** The Solana wire format's account-key deduplication/ordering rules are non-obvious and easy to get subtly wrong by hand.
**How to avoid:** Always use `MessageV0.try_compile(payer, instructions, address_lookup_tables, blockhash)` — never hand-build the `MessageHeader`/account-key list. Verified working for both the single-instruction funder case and (by extension, same API) the multi-instruction sweeper case.
**Warning signs:** `try_compile` itself raises on a genuine conflict (e.g. an account marked both signer and non-signer across two instructions) — treat any such exception as a bug in instruction construction, not something to catch-and-ignore.

## Code Examples

### Funder: exact-N transfer with pre-send guards
```python
# Source: pattern verified against installed solders==0.27.1; RpcClient methods
# are the existing bastion/rpc/client.py surface (get_balance, get_fee_for_message,
# get_latest_blockhash, send_raw all already exist and are reused as-is).
from solders.system_program import transfer, TransferParams
from solders.message import MessageV0
from solders.transaction import VersionedTransaction
from solders.hash import Hash
import base64

LAMPORTS_PER_SOL = 1_000_000_000

async def fund_session(rpc: RpcClient, config: Config, session_pubkey: str,
                        amount_sol: float) -> str:
    if amount_sol > config.max_session_cap_sol:  # D-03, refuse-before-send
        raise FunderCapExceededError(
            f"{amount_sol} SOL exceeds MAX_SESSION_CAP={config.max_session_cap_sol}"
        )
    amount_lamports = round(amount_sol * LAMPORTS_PER_SOL)

    vault_kp = load_vault(config)  # ONLY funder.py imports vault.py (SEC-02)
    vault_balance = await rpc.get_balance(str(vault_kp.pubkey()))

    ix = transfer(TransferParams(
        from_pubkey=vault_kp.pubkey(),
        to_pubkey=Pubkey.from_string(session_pubkey),
        lamports=amount_lamports,
    ))
    blockhash_result = await rpc.get_latest_blockhash()
    blockhash = Hash.from_string(blockhash_result["value"]["blockhash"])
    message = MessageV0.try_compile(vault_kp.pubkey(), [ix], [], blockhash)

    fee_result = await rpc.get_fee_for_message(
        base64.b64encode(bytes(message)).decode(), commitment="confirmed"
    )
    fee = fee_result["value"]
    if fee is None:
        raise RpcError("blockhash expired during fee lookup; retry")

    if vault_balance < amount_lamports + fee:  # D-04, refuse-before-send
        raise FunderInsufficientBalanceError(
            f"vault balance {vault_balance} cannot cover {amount_lamports}+{fee} fee"
        )

    tx = VersionedTransaction(message, [vault_kp])
    signed_b64 = base64.b64encode(bytes(tx)).decode()
    sig = await rpc.send_raw(signed_b64)
    await land_check(rpc, sig, signed_b64)  # D-08/D-09, Pattern 4
    return sig
```

### Sweeper: exact-zero atomic sweep
```python
async def sweep_session(rpc: RpcClient, config: Config,
                         session: SessionKeypair) -> dict:
    session_pk = Pubkey.from_string(session.pubkey)
    balance = await rpc.get_balance(session.pubkey)

    token_accounts = await rpc.get_token_accounts_by_owner(session.pubkey)
    empty_atas = [
        Pubkey.from_string(acc["pubkey"])
        for acc in token_accounts
        if int(acc["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"]) == 0
    ]
    # nonzero ATAs are deliberately left untouched (v1 SOL-only sweep, D-06/deferred)

    vault_pk = Pubkey.from_string(config.vault_pubkey)
    instructions = [close_account_ix(ata, vault_pk, session_pk) for ata in empty_atas]

    blockhash_result = await rpc.get_latest_blockhash()
    blockhash = Hash.from_string(blockhash_result["value"]["blockhash"])

    # Compile once WITHOUT the transfer to size the fee, then again WITH it —
    # or (simpler, and what's recommended): compile with a zero-amount transfer
    # placeholder first since instruction *count* (not lamport amount) drives fee.
    placeholder_ix = transfer(TransferParams(
        from_pubkey=session_pk, to_pubkey=vault_pk, lamports=0
    ))
    probe_message = MessageV0.try_compile(
        session_pk, instructions + [placeholder_ix], [], blockhash
    )
    fee_result = await rpc.get_fee_for_message(
        base64.b64encode(bytes(probe_message)).decode(), commitment="confirmed"
    )
    fee = fee_result["value"]
    if fee is None:
        raise RpcError("blockhash expired during fee lookup; retry")

    if balance <= fee:  # D-07: sub-fee dust -> no-op, not an error
        return {"swept": False, "reason": "dust below fee reserve", "balance": balance}

    final_transfer_ix = transfer(TransferParams(
        from_pubkey=session_pk, to_pubkey=vault_pk, lamports=balance - fee  # exact zero
    ))
    message = MessageV0.try_compile(
        session_pk, instructions + [final_transfer_ix], [], blockhash
    )
    tx = VersionedTransaction(message, [session.keypair])  # SESSION key only, never vault
    signed_b64 = base64.b64encode(bytes(tx)).decode()
    sig = await rpc.send_raw(signed_b64)
    await land_check(rpc, sig, signed_b64)
    return {"swept": True, "signature": sig, "closed_atas": len(empty_atas)}
```

### RpcClient additions (mirror the existing method style exactly)
```python
# Extend bastion/rpc/client.py -- new methods follow the existing call()/get_balance()
# pattern verbatim (same class, same _request_with_backoff routing).
async def get_signature_statuses(
    self, signatures: list[str], *, search_history: bool = False
) -> object:
    """Return the raw ``result`` of ``getSignatureStatuses``."""
    return await self.call(
        "getSignatureStatuses",
        [signatures, {"searchTransactionHistory": search_history}],
    )

async def get_token_accounts_by_owner(self, owner_pubkey: str) -> list[dict]:
    """Return the ``value`` array of jsonParsed token accounts owned by
    ``owner_pubkey`` (SPL Token Program only)."""
    result = await self.call(
        "getTokenAccountsByOwner",
        [
            owner_pubkey,
            {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
            {"encoding": "jsonParsed"},
        ],
    )
    return result["value"]
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| Legacy `Transaction` + `Message` (non-versioned) | `VersionedTransaction` + `MessageV0` | Solana v1.9 / v0 transactions (2021-2022 era) | This project already targets the current versioned API; `solders.transaction.Transaction` (legacy) still exists in the installed package but should NOT be used for new code — `MessageV0.try_compile` + `VersionedTransaction` is the correct, current pattern and is what's shown throughout this document |
| `getFeeForMessage` fee nested in `value.feeCalculator.lamportsPerSignature` | Fee returned directly as `value: u64 \| null` | Pre-2021 legacy RPC shape vs. current | Verified via `solana.com/docs/rpc/http/getfeeformessage` — code must NOT look for a `feeCalculator` key; some third-party docs/blog posts still show the old shape and will mislead a naive implementation |

**Deprecated/outdated:**
- Legacy (non-versioned) `Transaction`/`Message` construction: still present in `solders` for backward compatibility but not the recommended path for new code; this phase uses `VersionedTransaction`/`MessageV0` exclusively.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|----------------|
| A1 | `CloseAccount` is TokenInstruction enum variant `9` with a single-byte discriminant and no further instruction data | Pattern 2 / Code Examples | If wrong, the on-chain Token Program would reject the instruction outright (fails loud, not silently) — low risk of silent fund loss, but would block the sweeper entirely. Cross-referenced from docs.rs (spl-token Rust source) and michaelhly.com (solana-py's own encoding); recommend the devnet e2e test (already planned per CONTEXT.md) as the concrete verification gate before this ships. |
| A2 | `MessageV0.try_compile` correctly handles ordering when the message mixes SPL Token Program instructions (`closeAccount`) and System Program instructions (`transfer`) in one message | Sweeper Flow / Code Examples | Verified only for a single-instruction (System-only) message during this research session; the multi-instruction, mixed-program case was not directly executed. Low risk — `try_compile` is a generic Solana wire-format compiler with no program-specific logic, so this is standard usage, but the devnet e2e test should exercise the actual multi-instruction case (SOL + one closed ATA) exactly as CONTEXT.md's specifics section requires. |
| A3 | ~90s total land-check budget (poll every 1.5s) is a reasonable default given the ~150-slot/~60-90s blockhash validity window | Pattern 4 | If too short, a legitimately slow-but-landing confirmed transaction could trigger a premature "exceeded budget" error before the caller decides whether to rebuild; if too long, a CLI one-shot command feels unresponsive. This is explicitly "Claude's Discretion" per CONTEXT.md — the planner/executor should treat this as a starting point, tunable during Phase 3 implementation, not a locked constant. |

## Open Questions

1. **Should the funder's balance guard (D-04) use `confirmed` or account for in-flight vault transactions?**
   - What we know: `get_balance` defaults to whatever commitment `RpcClient` uses internally (currently no explicit commitment param on `get_balance` — it's a bare `getBalance` call, which itself defaults RPC-side to `finalized` unless changed).
   - What's unclear: Whether the vault balance read for D-04 should be pinned to `confirmed` (consistent with D-09's land-check commitment) or left at the RPC default. For a single-user vault with no concurrent writers, this is unlikely to matter in practice, but it's worth an explicit decision during planning.
   - Recommendation: Default to leaving `get_balance` as-is (no new commitment param needed) unless a plan-review surfaces a concrete race; document the choice explicitly in the funder's docstring per this project's established pattern (see `get_fee_for_message`'s Pitfall-4 doc comment as the template).

2. **Where should the new typed errors (`FunderCapExceededError`, `FunderInsufficientBalanceError`, etc.) live?**
   - What we know: The project has an established per-module typed-error pattern (`bastion/rpc/errors.py`, `bastion/keystore/errors.py`, each a small hierarchy rooted in a module-specific base exception).
   - What's unclear: Whether Phase 3 should introduce `bastion/errors.py` (shared funder+sweeper errors) or two separate `bastion/funder_errors.py`/`bastion/sweeper_errors.py` files, mirroring the keystore package's per-concern split.
   - Recommendation: A single shared error module (e.g. `bastion/fund_errors.py` or inline in each of `funder.py`/`sweeper.py` if the error set stays small) is simplest given funder and sweeper share the land-check/`RpcError` family already; left as Claude's Discretion per CONTEXT.md, but the planner should pick one and be explicit in the plan rather than leaving it emergent during implementation.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|--------------|-----------|---------|----------|
| `solders` | tx building/signing (funder, sweeper) | ✓ | 0.27.1 | — |
| `httpx` | RPC transport (existing `RpcClient`) | ✓ | 0.28.0 | — |
| Solana devnet RPC endpoint | funder/sweeper e2e tests | Not probed this session (network-dependent; `Config.solana_rpc` already externalizes this) | — | Point `SOLANA_RPC`/`SOLANA_WS` at a Helius devnet endpoint or `https://api.devnet.solana.com`; already handled by existing `Config` |
| Devnet SOL faucet (`requestAirdrop` / `faucet.solana.com`) | Funding the devnet e2e test keypair | Rate-limited (2 req/8h anonymous, ~5 SOL/day) — see Pitfall/Validation Architecture below | — | Reuse a single session-scoped funded devnet keypair across test runs rather than re-airdropping per run; treat 429 as a skip, not a failure |

**Missing dependencies with no fallback:** none — this phase adds no new external dependencies.
**Missing dependencies with fallback:** devnet airdrop rate limits — mitigated via test design (see Validation Architecture, Wave 0 Gaps).

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.1.1 + pytest-asyncio 1.4.0 (already configured, `asyncio_mode = "auto"` in `pyproject.toml`) |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` (existing — no new config file needed) |
| Quick run command | `pytest tests/unit/ -x -q` (excludes devnet e2e by directory/marker) |
| Full suite command | `pytest tests/ -q` (includes devnet e2e; requires `SOLANA_RPC` pointed at devnet and a pre-funded test keypair) |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|--------------------|--------------|
| SESS-02 | Funder moves exactly N SOL vault->session on devnet; balance delta asserted | e2e (devnet) | `pytest tests/e2e/test_devnet_fund_sweep.py::test_fund_moves_exact_amount -x` | ❌ Wave 0 |
| SESS-02 | Funder builds correct System transfer instruction (unit, mocked RPC) | unit | `pytest tests/unit/test_funder.py::test_builds_transfer_for_exact_amount -x` | ❌ Wave 0 |
| SESS-03 | Funder refuses and sends zero RPC calls when cap exceeded | unit | `pytest tests/unit/test_funder.py::test_refuses_when_cap_exceeded -x` | ❌ Wave 0 |
| SESS-06 | Sweeper returns remaining SOL to vault at exact zero, ATA closed first | e2e (devnet) | `pytest tests/e2e/test_devnet_fund_sweep.py::test_sweep_to_exact_zero_with_ata -x` | ❌ Wave 0 |
| SESS-06 | Sweeper computes `balance - fee` from `getFeeForMessage` (unit, mocked RPC) | unit | `pytest tests/unit/test_sweeper.py::test_transfer_amount_is_balance_minus_fee -x` | ❌ Wave 0 |
| SESS-06 | Sub-fee dust -> no-op, not an error (D-07) | unit | `pytest tests/unit/test_sweeper.py::test_dust_below_fee_is_noop -x` | ❌ Wave 0 |
| SEC-02 | Sweeper never imports `vault.py`; AST isolation test passes with `funder.py` as the one allowed importer | unit (structural) | `pytest tests/unit/test_keystore_vault_isolation.py -x` (extend `ALLOWED_IMPORTERS`) | ✅ exists, needs extension |
| SEC-02 | Injected post-send timeout produces no double-spend | unit (mocked RPC, deterministic) | `pytest tests/unit/test_funder.py::test_timeout_retry_no_double_spend -x` (and sweeper equivalent) | ❌ Wave 0 |
| SESS-07 | Retire refuses hard-delete on nonzero token balance | unit (mocked RPC) | `pytest tests/unit/test_keystore_session.py::test_retire_refuses_nonzero_token_balance -x` | ❌ Wave 0 |
| SESS-07 | Swept session's keystore can be retired (happy path) | e2e (devnet) or unit (mocked, zero-balance ATAs) | `pytest tests/unit/test_keystore_session.py::test_retire_succeeds_after_sweep -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/unit/ -x -q` (fast, mocked-RPC only — no network, no devnet dependency, matches the existing `rpc_harness`/respx pattern)
- **Per wave merge:** `pytest tests/unit/ -q` full unit suite; devnet e2e run manually/CI-gated separately given faucet rate limits
- **Phase gate:** Full suite green (`pytest tests/ -q` including devnet e2e, run against a real devnet RPC with a pre-funded keypair) before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/unit/test_funder.py` — covers SESS-02, SESS-03, SEC-02 (timeout/no-double-spend), using the existing `rpc_harness` respx fixture from `tests/conftest.py`
- [ ] `tests/unit/test_sweeper.py` — covers SESS-06 (exact-zero arithmetic, dust no-op, ATA closing), same `rpc_harness` fixture
- [ ] `tests/e2e/__init__.py` + `tests/e2e/test_devnet_fund_sweep.py` — new directory; devnet-marked tests, gated behind a `SOLANA_RPC`/devnet-keypair fixture that skips (not fails) when unavailable or airdrop-rate-limited
- [ ] `tests/e2e/conftest.py` — a devnet-specific fixture: reuses/airdrops a session-scoped funded devnet keypair once, treats `requestAirdrop` 429 as a `pytest.skip`, not a failure
- [ ] Extend `tests/unit/test_keystore_vault_isolation.py`'s `ALLOWED_IMPORTERS` to include `"bastion/funder.py"` (the module docstring in `vault.py` already names this as the sole future addition)
- [ ] Extend `tests/unit/test_keystore_session.py` with the D-10 retire-guard tests (needs a mocked `get_token_accounts_by_owner` call injected into `session.retire()`)
- [ ] `pytest.ini`/`pyproject.toml`: consider a `devnet_e2e` marker (`@pytest.mark.devnet_e2e`) registered in `[tool.pytest.ini_options]` so `pytest -m "not devnet_e2e"` becomes the fast/CI-safe default — not strictly required (directory-based separation via `tests/e2e/` also works) but reduces accidental devnet calls during routine `pytest tests/`

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|----------------|---------|--------------------|
| V2 Authentication | No | No user-facing auth in this phase; the "identity" that matters is a signing keypair, covered under V6 |
| V3 Session Management | No | Not applicable — "session" here means a disposable trading wallet, not a web session |
| V4 Access Control | Yes | Structural: SEC-02 enforced via the AST import-isolation test (`test_keystore_vault_isolation.py`) restricting which module may import `vault.py`; this IS the access-control mechanism for this phase, verified by a static test rather than a runtime check |
| V5 Input Validation | Yes | `amount_sol` must be validated as a positive, finite float before any lamport conversion (guards against `round(amount_sol * LAMPORTS_PER_SOL)` producing a negative or absurd value from malformed CLI/API input — not explicitly covered by D-01–D-10 but a natural extension of the existing `_coerce`/`ConfigError` pattern in `bastion/config.py`); session pubkey strings passed to `fund_session`/`sweep_session` should be validated via `Pubkey.from_string` (raises on malformed input) before use, mirroring `session.py`'s existing `_safe_pubkey` pattern |
| V6 Cryptography | Yes | Signing is entirely delegated to `solders.keypair.Keypair`/`VersionedTransaction.__init__` (Ed25519 signing via the underlying Rust/`ed25519-dalek` implementation) — never hand-rolled; this phase adds no new cryptographic primitives beyond what Phase 2 already established for keystore encryption |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|------------------------|
| Double-spend via re-signing after an ambiguous send outcome | Tampering / Repudiation | D-08: never re-sign; re-send the identical signed blob or poll `getSignatureStatuses`, relying on Solana's own signature-based dedup within the blockhash validity window (Pattern 4) |
| Vault-secret scope creep (a future module importing `vault.py` "just this once") | Elevation of Privilege | AST import-isolation test (`test_only_allowlisted_modules_import_vault`) fails the build the moment any non-allowlisted module imports `vault.py`; `funder.py` is the one sanctioned addition this phase makes to that allowlist |
| Sweeping to the wrong destination (a bug sends session funds somewhere other than the vault) | Tampering | Sweeper reads `Config.vault_pubkey` directly (a validated string from `Config`, never user/CLI-supplied per-call) as the sole destination for both the System transfer and every `closeAccount`'s rent destination — no code path in `sweeper.py` accepts an alternate destination pubkey as a parameter, by design |
| Racing ATA state between classification and send (Pitfall 3) | Tampering (of the sweeper's own assumptions, not an external attacker) | On-chain Token Program itself rejects `CloseAccount` on a nonzero-balance account (D-06's atomicity means the whole sweep aborts, not a partial state); classification is read as late as possible before building the sweep tx to minimize the race window |
| Leaking the signed transaction bytes or vault secret into logs | Information Disclosure | Follows the existing project-wide contract (`RpcClient.send_raw`'s docstring: "Never logs `signed_tx_b64` at any level") — funder/sweeper must not add logging that echoes `signed_b64`, the vault `Keypair`, or the session `SessionKeypair`'s secret bytes; mirrors Phase 2's no-secret-in-logs regression test pattern (`test_keystore_no_secret_leak.py`) — a Phase 3 equivalent covering funder/sweeper output is recommended in the plan |

## Sources

### Primary (HIGH confidence)
- Direct execution against the installed `solders==0.27.1` package in this repo's Python environment — `system_program.transfer`, `MessageV0.try_compile`, `VersionedTransaction` construction/signing/serialization/roundtrip, `Instruction`/`AccountMeta` construction for the hand-encoded `close_account`. [VERIFIED: solders 0.27.1 installed package]
- `bastion/rpc/client.py`, `bastion/keystore/vault.py`, `bastion/keystore/session.py`, `bastion/config.py`, `tests/unit/test_rpc_client.py`, `tests/unit/test_keystore_vault_isolation.py`, `tests/conftest.py` — read directly from the working repo. [VERIFIED: local codebase]

### Secondary (MEDIUM confidence)
- https://solana.com/docs/rpc/http/getfeeformessage — request params, response shape (`value: u64|null`, direct lamport integer, not `feeCalculator`-nested). [CITED]
- https://solana.com/docs/rpc/http/getsignaturestatuses — request/response shape, `null` entry meaning, `searchTransactionHistory`. [CITED]
- https://michaelhly.com/solana-py/spl/token/instructions/ — `close_account`/`CloseAccountParams` fields and account-meta ordering for the SPL Token `CloseAccount` instruction. [CITED]
- https://docs.rs/spl-token/latest/spl_token/instruction/enum.TokenInstruction.html — confirms `CloseAccount` as a `TokenInstruction` variant (exact discriminant value corroborated via secondary source, not directly read from this page's rendered content). [CITED]
- https://www.helius.dev/blog/how-to-deal-with-blockhash-errors-on-solana and https://solana.com/developers/guides/advanced/confirmation — blockhash validity window (~150 slots / 60-90s), `BlockhashNotFound` semantics. [CITED]
- https://faucet.solana.com/ and Solana devnet airdrop/faucet developer guides — devnet `requestAirdrop` rate limits (2 req/8h anonymous faucet; ~5 SOL/day devnet cap). [CITED]
- PyPI `solana` package version history (`pip index versions solana` -> 0.40.0 latest, 60+ historical releases) and `gsd-tools query package-legitimacy check` (`SUS`, `too-new`/`unknown-downloads` signals). [VERIFIED: PyPI registry, tool output]

### Tertiary (LOW confidence)
- None — every claim above is either directly executed/read (VERIFIED) or sourced from an official/canonical documentation page (CITED). See Assumptions Log for the small number of claims that combine a CITED fact with an untested extension (A1, A2).

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new dependencies; all `solders`/`httpx` usage verified by direct execution against the exact installed versions
- Architecture: HIGH — directly extends already-proven Phase 1/2 modules and patterns (`RpcClient` method style, `SessionKeypair`, AST isolation test) with no structural surprises
- Pitfalls: MEDIUM — grounded in official docs and direct execution, but the multi-instruction (`closeAccount` + `transfer` in one message) compilation path and the exact land-check timing budget are extrapolated/recommended rather than independently execution-verified this session (see Assumptions Log A2/A3)

**Research date:** 2026-07-08
**Valid until:** 30 days (stable domain — Solana RPC method shapes and `solders`' core tx-building API change slowly; re-verify sooner if `solders` is bumped past the `<0.29` pyproject ceiling or if the SPL Token program's `CloseAccount` instruction layout is ever revised, which would be a rare breaking change)
