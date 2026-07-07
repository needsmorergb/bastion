# Bastion

## What This Is

Bastion is a non-custodial, local-first CLI that provides **blast-radius containment for Solana terminal/bot trading**. You trade from disposable, hard-capped session wallets that connect to nothing else you own; Bastion watches each session's on-chain activity in near-real-time, scores every transaction against a behavioral ruleset, and alerts (optionally auto-sweeps) the moment a session looks compromised. It is for solo Solana traders who run trading terminals or bots and want to cap their downside: it converts "lost everything" into "lost the cap." The name comes from the network-security "bastion host" — the single hardened, exposed entry point that shields everything behind it; here the disposable session wallet is the bastion and the vault behind it is what stays protected.

## Core Value

**Containment: a compromised trading session must be a dead end with a small, pre-decided balance — the vault behind it is never drained.** If everything else fails, this one property (isolation + a hard cap between the session and the vault) is what turns catastrophe into a bounded, acceptable loss.

## Business Context

<!-- Included because this ships to strangers whose funds are at stake — the distribution posture is a hard design constraint, not a business plan. -->

- **Customer**: Solo Solana traders using trading terminals/bots (initially the maintainer; later, other self-custody traders via public OSS release).
- **Revenue model**: None in the fund path. Permissive OSS (MIT/Apache-2.0). Any fees must be **out-of-band** (license, scoring-backend subscription, or donation address) — never a cut skimmed from swept funds.
- **Success metric**: A compromised session never costs more than its cap; zero incidents of Bastion itself touching/leaking a user key.
- **Strategy notes**: Non-custodial is the load-bearing wall (see Constraints and Key Decisions). Regulatory safe harbor depends on staying non-custodial — get crypto counsel before public/stranger mainnet release.

## Requirements

### Validated

<!-- Shipped and confirmed valuable. -->

- ✓ Config + JSON-RPC/WebSocket transport with externalized safety rails — Phase 1 (CLI-05, CLI-06)
- ✓ Generate a fresh Solana keypair per session (rotation on loss/time threshold deferred to Phase 7) — Phase 2 (SESS-01)
- ✓ Encrypt session keys at rest (scrypt n=2¹⁷ → Fernet); keystore files owner-only 0600 on POSIX (Windows perms limitation documented/tested) — Phase 2 (SESS-04)
- ✓ Load a session keypair by pubkey with a passphrase; wrong passphrase fails closed (typed error, never a partial/garbage key) — Phase 2 (SESS-05)
- ✓ No plaintext key ever written to disk or logs — ciphertext-only keystore, redacted `SessionKeypair` repr, capfd/caplog no-secret-leak regression — Phase 2 (SEC-01)
- ✓ Refuse to run when keystore dir resolves under a cloud-sync path (default-refuse + explicit opt-in override) — Phase 2 (SEC-04)
- ✓ Passphrase confirmed on create, never echoed or logged — Phase 2 (SEC-05)
- ✓ Vault/session split enforced structurally — isolated `vault.py` import boundary, proven by an AST import-graph test (precondition for SEC-02/SEC-03) — Phase 2

### Active

<!-- Current scope. Building toward these. All are hypotheses until shipped and validated. -->

**Session & key management**
- [~] Generate a fresh keypair per session — generation shipped (Phase 2); opt-in rotation on a loss/time threshold deferred to Phase 7
- [ ] Fund a session wallet from a vault with a hard SOL cap (refuse if cap > MAX_SESSION_CAP)
- [x] Encrypt session keys at rest (scrypt → Fernet); keystore files owner-only (0600) — Phase 2
- [ ] Sweep remaining SOL back to vault on manual session end; retire the keystore

**Monitoring & detection**
- [ ] Watch a session wallet's transactions in near-real-time (WebSocket primary)
- [ ] Score each transaction against a behavioral ruleset; classify OK / WATCH / CRITICAL
- [ ] Survive RPC hiccups via reconnect + polling backfill so no event is missed
- [ ] On anomaly: alert instantly out-of-band and (optional, `--armed` only) auto-sweep remaining SOL to vault

**Auditability**
- [ ] Full append-only audit log (JSONL) of every fund / sweep / alert / decision

**Distribution (before stranger mainnet use)**
- [ ] Auditable non-custodial guarantee: documented data-egress list; no telemetry that could carry key material
- [ ] LLM-scoring egress boundary enforced structurally (scoring payload built from public on-chain fields only, in a module with no keystore access; test fails if key material can reach the network layer)
- [ ] Default alert-only; arming is a deliberate, well-warned opt-in
- [ ] Ship `.gitignore` + `.env.example`; refuse to run if keystore dir looks cloud-synced
- [ ] Signed reproducible releases + published checksums; pinned/hash-verified dependencies

### Out of Scope

<!-- Explicit boundaries, with reasoning to prevent re-adding. -->

- **Auto token liquidation on sweep (SOL-only in v1)** — needs careful slippage/route logic before it's trustworthy; SPL positions are swapped back in-terminal first and empty ATAs closed to reclaim rent. `sweep_tokens` is a v2 stub.
- **Per-trade / per-fill wallet rotation** — Solana rent/ATA economics make it impractical; prefer loss-threshold / time-window rotation.
- **Multi-wallet dashboard UI; historical PnL attribution** — v2+; the v1 surface is CLI + monitor process.
- **Hardware-signer (Ledger) path for the vault** — deferred to v2; v1 uses env-var vault secret (with loud warnings). Ledger becomes first-class for distribution.
- **Anonymity / mixing** — explicitly not a goal. Session wallets funded from one vault are linkable on-chain; that's acceptable. The design stance is isolation, not anonymity.
- **Any server / hosted service in the fund path; inbound ports** — single-user, single-machine only. A hosted component in the fund path breaks the non-custodial guarantee and the regulatory posture.
- **LLM as the sole gate on moving funds** — rules gate; the LLM only explains/confirms and reduces false positives.

## Context

**Threat model (grounding).** The failure Bastion defends against is a **session compromise** — a hostile trading terminal or a phished bot session trading your funds through pools. These transactions are mechanically indistinguishable from normal swaps, so wallet-level protections and transaction simulation never fire. Defense is therefore **containment + behavioral detection**, not signature inspection. "Signed by you" is meaningless when the session itself is the attacker — so detection keys on *deviation from a per-session behavioral baseline*, not absolute values.

**Prior incident (the golden test case).** A real drain — the "5YEQ churn" — is the reference CRITICAL fixture: 7+ transactions in one minute, ~50% round-trip loss per cycle, −1.33 SOL in 5 minutes, net SOL exiting toward a previously-unseen pool. A clean trading day is the reference OK fixture. The scoring engine is built TDD-first against these fixtures.

**Design stance.** Isolation, not anonymity. The goal is a bounded, pre-decided loss on compromise — not untraceability.

**Operating environment.** Single user, single machine, no server, no inbound ports. Helius RPC + WebSocket on the free tier (rate limits apply). Runs as a local CLI plus a long-running monitor process.

**Distribution reality.** When strangers run this, every "fine for me" choice becomes a promise to users whose funds are at stake. The whole thing hangs on the non-custodial invariant (below). The tool that prevents drains must not become one — supply-chain integrity is treated as if the publishing pipeline holds funds, because effectively it does.

## Constraints

- **Tech stack**: Python 3.11+; `solders` (keys/tx), `requests` (JSON-RPC), `cryptography` (keystore), `websockets` (live monitor), `apscheduler` or asyncio (scheduling) — Because solo maintainer is Python-first and the problem needs no web framework.
- **Infrastructure**: Helius RPC + WebSocket, free tier — Rate limits shape the monitor design (WS primary, polling backfill as safety net).
- **Economics**: Solana rent/ATA costs — Make per-fill session wallets impractical; drives loss-threshold rotation instead.
- **Security — non-custodial (load-bearing)**: The tool must NEVER transmit, upload, phone home, or centrally store any private key, seed, or passphrase, under any code path — This is the security story (never a honeypot), the liability shield, and the regulatory posture all at once. Breaking it breaks everything.
- **Security — key handling**: Never write a plaintext private key to disk; never log secrets; keystore files 0600; keystore dir never cloud-synced — Make these tests, not comments.
- **Security — vault/sweep separation**: Vault secret loaded only for funding; sweeps target `VAULT_PUBKEY` and need no vault secret.
- **Security — alert isolation**: Alert channel must be out-of-band from the trading session (separate Telegram identity), so a compromise of the session can't suppress alerts.
- **Performance**: Alert latency target < 5s from on-chain confirmation to push — Detection is only useful if it beats the drain.
- **Reliability**: The risk is *missed events*, not throughput — Idempotent (dedupe on signature); reconcile + backfill on monitor restart mid-session.
- **Legal**: Not shippable to strangers on mainnet without crypto counsel sign-off on architecture + disclaimer, external security review of key/fund paths, and a public non-custodial data-egress statement — Personal small-cap mainnet use may precede this; stranger use may not.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Containment over inspection (disposable capped session wallets) | Compromised sessions look like normal swaps; you can't inspect your way out, only cap the blast radius | — Pending |
| Rules-first scoring; LLM explains/confirms, never the sole gate | Deterministic rules are safer, cheaper, and more legible than a black box deciding when to move money; LLM adds a human sentence + false-positive reduction | — Pending |
| Per-session behavioral baseline drives WATCH/CRITICAL (deviation, not absolutes) | "Signed by you" is meaningless when the session is the attacker; the attacker's clip/timing pattern ≠ yours | — Pending |
| Default alert-only; auto-sweep requires explicit `--armed` | Shipping armed-by-default to strangers invites wrong-vault sweeps and false-positive liquidations mid-trade | — Pending |
| SOL-only sweep in v1; `sweep_tokens` is a stub | Auto-liquidation needs trustworthy slippage/route logic; premature auto-selling is dangerous | — Pending |
| Loss-threshold / time-window rotation, not per-fill | Solana rent/ATA tax makes per-fill wallets impractical | — Pending |
| Env-var vault secret in v1; Ledger first-class for distribution | Env var is fine for the maintainer, dangerous guidance for strangers | — Pending |
| Non-custodial is non-negotiable; no hosted service in fund path | Security, liability, and regulatory safe harbor all depend on it | — Pending |
| LLM-scoring egress boundary enforced structurally + test-guarded | The one place a "helpful cloud feature" could silently break the non-custodial guarantee | — Pending |
| Build order: config/rpc → keystore → funder/sweeper (devnet) → store → scoring → monitor → alerter → cli; mainnet last with tiny cap | Each module independently testable; never touch funds before keystore + devnet fund/sweep pass | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Business Context check — customer, revenue model, success metric still accurate?
4. Audit Out of Scope — reasons still valid?
5. Update Context with current state

---
*Last updated: 2026-07-07 after Phase 2 (Encrypted Keystore + Key-Safety Invariants)*
