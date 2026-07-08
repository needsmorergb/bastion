# Phase 3: Fund-Moving on Devnet (Funder + Sweeper) - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-07-07
**Phase:** 3-Fund-Moving on Devnet (Funder + Sweeper)
**Areas discussed:** Funding amount semantics, Exact-zero sweep mechanics, No-double-spend on timeout

---

## Area selection

| Option | Description | Selected |
|--------|-------------|----------|
| Funding amount semantics | What "the cap" means; session-received vs vault-debited | ✓ |
| Exact-zero sweep mechanics | getFeeForMessage vs static reserve; atomic vs sequential ATA close | ✓ |
| No-double-spend on timeout | Chain-based idempotency with no DB until Phase 4 | ✓ |
| Token/ATA + retire guard | Empty-ATA detection + retire refusal on nonzero token balance | (left to Claude) |

---

## Funding amount semantics

### Q1 — resulting on-chain state when funding N SOL

| Option | Description | Selected |
|--------|-------------|----------|
| Session gets exactly N | Session ends with exactly N; vault debited N + fee. Cap = usable balance. | ✓ |
| Vault debits exactly N | Vault sends exactly N; session receives N − fee. | |

**User's choice:** Session gets exactly N.

### Q2 — funder.py responsibility boundary

| Option | Description | Selected |
|--------|-------------|----------|
| Mint + save + fund | funder.py mints the keystore then funds it. | |
| Fund a handed-in session | funder.py only moves SOL to a given pubkey; minting stays in session.py. | ✓ (via Claude ruling) |

**User's choice:** "Other" → "Figure out what's the most trustworthy."
**Notes:** Claude ruled **fund a handed-in session pubkey** — SEC-02 makes
funder.py the sole holder of the vault secret, so the vault-secret-privileged
path must stay minimal and auditable; minting (passphrase/disk) does not belong
in it. Reflected back and accepted implicitly as the user proceeded.

### Q3 — vault can't cover N + fee

| Option | Description | Selected |
|--------|-------------|----------|
| Pre-check + refuse, send nothing | Query vault balance first; typed error, zero txs sent. | ✓ |
| Just attempt the send | Skip pre-check; let it fail on-chain. | |

**User's choice:** Pre-check + refuse, send nothing.

---

## Exact-zero sweep mechanics

### Q1 — sweep amount / fee computation

| Option | Description | Selected |
|--------|-------------|----------|
| getFeeForMessage exact | Exact fee via RPC; transfer balance − exact_fee; FEE_RESERVE is fallback floor. | ✓ |
| Static FEE_RESERVE only | Transfer balance − FEE_RESERVE_LAMPORTS constant. | |

**User's choice:** getFeeForMessage exact.

### Q2 — ATA close + SOL transfer structure

| Option | Description | Selected |
|--------|-------------|----------|
| One atomic tx | closeAccount(empty ATAs, rent→vault) + transfer(SOL − fee) → vault, one signed tx. | ✓ |
| Sequential txs | Tx1 closes ATAs (rent→session), Tx2 sweeps SOL. | |

**User's choice:** One atomic tx.

### Q3 — sub-fee dust (balance > 0 but < fee)

| Option | Description | Selected |
|--------|-------------|----------|
| No-op, leave the dust | "Nothing sweepable" result; don't waste a fee. | ✓ |
| Raise an error | Surface a typed error to the caller. | |

**User's choice:** No-op, leave the dust.

---

## No-double-spend on timeout

### Q1 — idempotency mechanism

| Option | Description | Selected |
|--------|-------------|----------|
| Blockhash-scoped signed-tx reuse | Sign once; on timeout poll/re-send identical blob; rebuild only after blockhash expiry. | ✓ |
| Durable nonce account | Non-expiring signed tx via durable nonce; extra account/instructions. | |

**User's choice:** Blockhash-scoped signed-tx reuse.

### Q2 — confirmation commitment for the land-check

| Option | Description | Selected |
|--------|-------------|----------|
| confirmed | ~1–2s, supermajority-voted; responsive for one-shot CLI. | ✓ |
| finalized | ~13s, rooted/irreversible; maximally safe. | |
| confirmed, then finalized to retire | Report success at confirmed; require finalized before retire deletes. | |

**User's choice:** confirmed.

---

## Claude's Discretion

- Token/ATA detection via `getTokenAccountsByOwner` (empty vs nonzero ATAs) —
  the area the user chose not to discuss.
- Retire guard (D-10): refuse to hard-delete on nonzero token balance, using the
  existing typed fail-loud error contract.
- Adding a signature-status / confirmation-poll helper to the RPC client; poll
  interval and total wait budget (bounded by the blockhash window).
- Async cores with thin sync wrappers (mirroring `get_balance_sync`) since Phase
  6 armed auto-sweep calls the sweeper from the async monitor loop.
- Exact module/function signatures, instruction building, and error-type names.

## Deferred Ideas

- Token auto-liquidation on sweep (v2 `sweep_tokens` stub) — SOL-only in v1.
- Durable-nonce idempotency — considered and rejected for v1.
- Session rotation (`--rotate-on-loss`) — Phase 7.
- `finalized`-before-retire hardening — considered; chose `confirmed` for v1.
