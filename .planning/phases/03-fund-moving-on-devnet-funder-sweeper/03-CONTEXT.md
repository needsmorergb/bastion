# Phase 3: Fund-Moving on Devnet (Funder + Sweeper) - Context

**Gathered:** 2026-07-07
**Status:** Ready for planning

<domain>
## Phase Boundary

The core containment primitive: capped vault→session funding and full
session→vault sweep to **exact zero**, validated end-to-end on devnet before
any mainnet SOL is at risk.

Delivers requirements **SESS-02** (fund a session from the vault with a SOL
cap), **SESS-03** (refuse when requested cap exceeds `MAX_SESSION_CAP`),
**SESS-06** (sweep remaining SOL back to the vault on manual session end),
**SESS-07** (retire a swept session keystore), **SEC-02** (vault secret loaded
only for funding; sweeps target `VAULT_PUBKEY` and need no vault secret).

In scope: `bastion/funder.py`, `bastion/sweeper.py`, the retire-guard addition
to `bastion/keystore/session.py`, and devnet end-to-end tests (fund→sweep round
trip, injected post-send timeout → no double-spend, SOL + one open ATA closed
to exact zero).

Out of scope: SQLite persistence / audit log (Phase 4 — no DB exists yet this
phase, so idempotency here is chain-based, not DB-based); scoring, monitor, and
armed auto-sweep wiring (Phase 5–6); CLI assembly and `--rotate-on-loss`
(Phase 7); token auto-liquidation (`sweep_tokens` remains a v2 stub — SOL-only
sweep; nonzero-token ATAs are left untouched).

</domain>

<decisions>
## Implementation Decisions

### Funding Amount Semantics
- **D-01: Fund N SOL → session ends with exactly N SOL.** The vault is debited
  `N + tx_fee`; the session wallet receives a clean, round N. "The cap" the
  whole product is built around equals the session's usable balance, so
  `MAX_SESSION_CAP` is compared against N (the amount the session receives).
- **D-02: `funder.py` funds a handed-in session pubkey; it does NOT mint the keystore.**
  Keystore minting (`session.generate()` / `session.save()`) stays
  in `session.py`. Rationale (trustworthiness): SEC-02 makes `funder.py` the
  only module that imports `vault.py` and holds the vault secret in scope — the
  vault-secret-privileged code path must stay as small and auditable as
  possible. The funder does only: `load_vault()` → build a System transfer to a
  destination pubkey → sign with vault → send → land-check. It never needs the
  session *secret*, only a destination address. Phase 7's `start` orchestrates
  the two steps (`generate → save → fund`). An orphaned unfunded keystore is
  cheap and reversible (retire it); a bloated vault-secret path is not.
- **D-03: Cap refusal is refuse-before-send.** When the requested amount
  exceeds `MAX_SESSION_CAP`, raise a typed error and send zero transactions
  (SESS-03). Equal-to-cap is allowed; only strictly-greater is refused.
- **D-04: Insufficient-vault-balance is also pre-checked and refused.** Query
  the vault balance first; if it cannot cover `N + estimated_fee`, raise a typed
  error and send nothing — same refuse-before-send posture as the cap guard, so
  there is never a partial/failed on-chain attempt.

### Exact-Zero Sweep Mechanics
- **D-05: Exact fee via `getFeeForMessage(commitment="confirmed")`.** Build the
  sweep message, look up the precise fee, then transfer `balance − exact_fee`.
  This is what makes true exact-zero achievable. `FEE_RESERVE_LAMPORTS` is
  demoted to a sanity floor / fallback used only if the RPC fee lookup fails —
  it is no longer the primary fee source.
- **D-06: One atomic sweep transaction.** A single signed tx carries
  `closeAccount` instructions for the empty ATAs (rent destination = the vault)
  plus a System transfer of `(SOL_balance − fee)` to the vault. All-or-nothing,
  single fee, session lands at exact zero, and every lamport (SOL + reclaimed
  ATA rent) ends up in the vault. If the number of empty ATAs overflows one
  transaction's size limit, batch across multiple txs (rare for v1 single-user).
- **D-07: Sub-fee dust → no-op, leave it.** If the session balance is > 0 but
  too small to cover the sweep fee, sweeping would cost more than it recovers.
  Return a "nothing sweepable" result and leave the dust — treat it like the
  already-empty case. Do **not** raise; the caller (and Phase 6 armed
  auto-sweep) should not have to handle an error for a harmless dust remainder.

### No-Double-Spend / Idempotency (chain-based, no DB this phase)
- **D-08: Blockhash-scoped signed-tx reuse.** Sign the transaction exactly once
  → one deterministic signature. On a timed-out / uncertain send, **never
  re-sign**; instead poll that specific signature's status and/or re-send the
  *identical* signed blob (Solana dedups by signature within the blockhash
  validity window, so re-sending the same blob cannot double-spend). Only after
  the original blockhash has provably expired — at which point the old tx can
  never be included — is it safe to rebuild with a fresh blockhash and retry.
  This is the "land-check" in the roadmap's build→sign→send→land-check flow. No
  durable-nonce accounts in v1.
- **D-09: Land-check waits for `confirmed`.** Both funder and sweeper declare
  success at commitment `confirmed` (~1–2s, supermajority-voted) — sufficient
  for moving SOL between the user's own vault and session on devnet/mainnet, and
  responsive for one-shot CLI use.

### Retire Guard (SESS-07 / success criterion 5)
- **D-10:** Extend `session.retire()` so it **refuses to hard-delete when a
  nonzero token balance remains** in the session's ATAs. Because v1 sweeps
  SOL-only and leaves nonzero-token ATAs untouched (D-06), retiring such a
  wallet would orphan real token value. Follow the existing typed fail-loud
  error contract (`KeystoreError` family) — raise rather than silently skip.

### Claude's Discretion
- **Token/ATA detection** (the gray area the user chose not to discuss): use
  `getTokenAccountsByOwner` to enumerate the session's ATAs and classify
  empty (zero token balance, closeable) vs nonzero (left untouched). Requires a
  new RPC helper — at Claude's discretion.
- **Confirmation-poll helper**: the RPC client currently lacks
  `getSignatureStatuses` and a confirmation/land-check loop; add whatever helper
  is cleanest (e.g. `getSignatureStatuses`, or `getTransaction(sig)` returning
  non-null on confirmation). Poll interval, per-poll timeout, and total wait
  budget (bounded by the blockhash validity window) are at Claude's discretion.
- **Sync vs async surface**: the RPC client is async; funder/sweeper cores
  should be async with thin sync wrappers where CLI one-shots need them
  (mirroring `get_balance_sync`), since Phase 6's armed auto-sweep will call the
  sweeper from within the async monitor loop. Exact signatures at Claude's
  discretion.
- Exact module/function signatures, instruction-building details, error-type
  names, and the fee-reserve fallback trigger conditions are Claude's discretion
  within the decisions above.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase scope & requirements
- `.planning/ROADMAP.md` §"Phase 3: Fund-Moving on Devnet (Funder + Sweeper)" —
  goal, dependencies, 5 success criteria, and the 4 proposed plan splits
  (funder / sweeper / retire semantics / devnet e2e tests).
- `.planning/REQUIREMENTS.md` — SESS-02, SESS-03, SESS-06, SESS-07, SEC-02
  (lines 13–14, 17–18, 26).
- `.planning/PROJECT.md` §Constraints (vault/sweep separation; non-custodial),
  §Key Decisions (SOL-only sweep, `sweep_tokens` stub; env-var vault secret),
  §"Out of Scope" (auto token liquidation on sweep; per-fill rotation).

### Structural preconditions carried from Phase 2
- `bastion/keystore/vault.py` — the isolated `load_vault()` loader; funder.py is
  the ONLY permitted importer (SEC-02). Its module docstring names funder.py
  explicitly as the sole future importer.
- `tests/unit/test_keystore_vault_isolation.py` — the AST import-graph test that
  enforces the vault isolation contract; adding `funder.py` as an importer must
  keep this test green (funder is the one allowed importer).
- `.planning/phases/02-encrypted-keystore-key-safety-invariants/02-CONTEXT.md` —
  keystore file format, `SessionKeypair` redaction, no-secret-in-logs
  regression conventions that this phase's new modules must uphold.

### Existing code the plans build on
- `bastion/keystore/session.py` — `SessionKeypair`, `generate/save/load/retire`
  (retire needs the D-10 token-balance guard added here).
- `bastion/rpc/client.py` — `send_raw` (tight retry budget), `get_fee_for_message`,
  `get_latest_blockhash`, `get_balance`, `get_signatures`, `get_transaction`
  (needs a signature-status/confirmation helper added — see Claude's Discretion).
- `bastion/config.py` — `max_session_cap_sol`, `fee_reserve_lamports`,
  `vault_pubkey`, `vault_secret`.

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `bastion/keystore/vault.py::load_vault(config)` — returns the vault
  `solders.keypair.Keypair`; funder.py imports it (the single sanctioned
  importer). Sweeper must NOT import it — it uses `Config.vault_pubkey` only.
- `bastion/keystore/session.py` — `generate()`/`save()` for the Phase 7 caller;
  `load()` gives the sweeper the session `Keypair` to sign the sweep; `retire()`
  is extended with the D-10 guard.
- `bastion/rpc/client.py::RpcClient` — async JSON-RPC surface with
  `get_fee_for_message(commitment="confirmed")` (already exactly what D-05
  wants), `send_raw` (already uses the tighter sendTransaction budget), and
  `get_balance` + `get_balance_sync` wrapper pattern to mirror.
- `bastion/config.py::Config` — frozen dataclass carrying every safety rail;
  `vault_secret`/`keystore_passphrase` are `repr=False` secrets.

### Established Patterns
- Flat `bastion/` package layout (not `src/`); `funder.py` and `sweeper.py` live
  at `bastion/` top level (siblings of `config.py`), per the Phase 1/2 layout.
- Typed, message-only fail-loud errors (see `rpc/errors.py`, `keystore/errors.py`)
  — never a silent `None`, never a secret in an exception message.
- TDD-first with mockable RPC (respx factory + local WS server from Phase 1's
  test harness); tests under `tests/unit/` and devnet e2e tests separated out.
- Async RPC core with thin sync wrappers at CLI one-shot call sites
  (`get_balance_sync` is the reference).

### Integration Points
- Adding `funder.py` as an importer of `vault.py` updates the allowed-importer
  set asserted by `test_keystore_vault_isolation.py` — the funder is the ONE
  permitted addition; nothing else may import vault.py.
- The sweep tx targets `Config.vault_pubkey` (a public key) and signs with the
  session key from `session.load()` — structurally incapable of loading the
  vault secret (success criterion 4, SEC-02).
- Phase 4 (SQLite store) will later record fund/sweep signatures; this phase's
  idempotency is intentionally chain-based (D-08) because no store exists yet.

</code_context>

<specifics>
## Specific Ideas

- "Figure out what's most trustworthy" (user's explicit steer on the funder
  scope question, D-02): the guiding principle for this phase is minimizing the
  code that runs with the vault secret in scope and keeping it independently
  auditable. When a design choice is ambiguous, prefer the option that shrinks
  or clarifies the vault-secret blast radius.
- Exact-zero is a first-class correctness property, not best-effort: the devnet
  e2e test must fund a wallet (with one open ATA), sweep, and assert the session
  ends at exactly 0 lamports with the ATA closed and all value in the vault.
- The injected-timeout no-double-spend test drives D-08 directly: simulate a
  send that times out after the tx actually landed, retry, and assert exactly
  one transfer occurred.

</specifics>

<deferred>
## Deferred Ideas

- **Token auto-liquidation on sweep** — swapping SPL positions back to SOL
  before sweeping. Explicitly out of scope (v2 `sweep_tokens` stub); v1 sweeps
  SOL-only and leaves nonzero-token ATAs, which is exactly why the D-10 retire
  guard exists.
- **Durable-nonce idempotency** — considered and rejected for v1 (D-08); revisit
  only if unbounded-time idempotency is ever needed beyond the blockhash window.
- **Session rotation (`--rotate-on-loss`)** — Phase 7; not a funding/sweeping
  primitive.
- **`finalized`-before-retire hardening** — considered (offered as an option);
  chose `confirmed` (D-09) for v1. Could revisit if a reorg ever bites a retire.

None — discussion otherwise stayed within phase scope.

</deferred>

---

*Phase: 3-Fund-Moving on Devnet (Funder + Sweeper)*
*Context gathered: 2026-07-07*
