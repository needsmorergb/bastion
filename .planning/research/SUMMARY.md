# Project Research Summary

**Project:** Bastion — Non-custodial Solana session-wallet isolation + behavioral anomaly-detection CLI
**Domain:** Local-first fund-moving tool with autonomous monitoring and containment-based defense
**Researched:** 2026-07-06
**Confidence:** MEDIUM-HIGH (stack/architecture/pitfalls verified; features design-validated but lack user data)

---

## Executive Summary

Bastion solves a real, specific problem in the Solana trading ecosystem: **drains via session-wallet compromise**. Rather than detecting compromises after they happen (like wallet trackers) or trying to prevent malicious transactions before they're signed (like pre-execution firewalls), Bastion combines three layers: **(1) structural containment** via a capped, disposable session wallet that bounds maximum loss even if detection fails; **(2) per-session behavioral baseline** that catches deviations from how *this trader* actually trades in *this session*, rather than absolute global heuristics; and **(3) deterministic rules-first architecture** where an LLM explains anomalies but never gates fund-moving decisions.

The research validates that this approach is architecturally sound, but the project is **complex in distribution and security posture rather than in core functionality**. The actual fund-moving and monitoring code (phases 1–6) follows well-established patterns (WS+poll hybrid, cursor-based reconciliation, WAL-backed SQLite). The real novelty and risk live in two places: **(a) the structural trust-zone separation** (keeping secret material and scoring/LLM code separated by import-linter contracts even though they run in the same process), and **(b) the supply-chain and custody-model discipline** needed to avoid accidentally becoming custodial or compromisable in distribution.

**Key roadmap implication:** Build phases 1–6 (foundation through alerting) following the spec's explicit build order almost exactly — that order is sound and reflects critical dependencies. The optional complexity (scoring fixture diversity, LLM integration, auto-sweep enablement) and the non-negotiable complexity (supply-chain hardening, regulatory posture, custody-line discipline) should be sequenced as post-MVP refinements and pre-distribution gates, not interleaved with core development.

---

## Key Findings

### Recommended Stack

The stack research confirms the spec's choices with one important caveat: **replace `requests` with `httpx`** for the async monitor's RPC calls. `requests` has no async API and would block the monitor's event loop on every JSON-RPC call, directly working against the <5s alert-latency target.

**Core technologies:**
- **Python 3.11+**: Enables `asyncio.TaskGroup`, cleaner structured concurrency for WS+poll fan-out.
- **`solders` 0.27.x** (Rust-backed): Minimal, actively-maintained primitive layer for keypair generation and transaction building.
- **`httpx` 0.27–0.28.x** (not `requests`): Sync *and* async client for both CLI paths and the monitor.
- **`websockets` 16.0+**: Modern async-iterator API (not deprecated legacy); auto-reconnect pattern.
- **`cryptography` 49.0.0**: Fernet + scrypt KDF for keystore encryption (audited standard). NOTE: scrypt n should be raised from the spec's 2^14 toward the current recommended floor (~2^17) for a high-value vault secret.
- **`click` 8.1.x**: CLI entrypoint (minimal supply-chain surface on a fund-moving tool).
- **`sqlite3` (stdlib) with WAL**: Configured with `PRAGMA journal_mode=WAL; synchronous=NORMAL; busy_timeout=5000`.
- **`uv`**: Dependency locking with hash-pinned `uv.lock` + `pip-audit` in CI (supply-chain critical).
- **Dropped from spec:** `requests` (→ httpx), `apscheduler` (→ plain asyncio; the monitor is one continuous loop). Skip Telegram/Pushover client libraries for v1 — both are single outbound POSTs, a small `httpx.post` wrapper is easier to audit.

**Confidence: MEDIUM-HIGH.** Official docs cross-checked; versions verified as of 2026-07-06.

### Expected Features

**Must have for v1 (table stakes):**
- Fresh keypair per session; encrypted keystore (scrypt→Fernet, 0600); capped funding with hard `MAX_SESSION_CAP` enforcement
- Manual sweep-to-vault; real-time monitoring (WS + polling backfill); rules-based scoring (fixture-validated)
- Out-of-band alerting (Telegram/Pushover) with alert-channel isolation; append-only audit log (JSONL)
- Structural LLM-egress boundary (enforced by import-linter + DTO type, tested before LLM enabled)

**Differentiators:**
- Containment as primary control (capped wallet bounds loss structurally)
- Per-session behavioral baseline (deviation-based detection vs. absolute thresholds)
- Rules-first, LLM-explains (deterministic gate; LLM only enriches alerts)
- Cluster-of-signals scoring (velocity + round-trip loss + new-counterparty sink combined)
- Structural egress boundary (scoring ⇏ keystore, enforced by import graph + type system + test)
- Alert-channel isolation (separate Telegram identity from trading session)
- Opt-in auto-sweep (`--armed`), repurposing malicious sweeper-bot technique defensively

No surveyed tool combines containment (capped disposable session wallets) with per-session behavioral scoring — this is genuine white space, not just positioning.

**Defer to v2+:** LLM-explains (add post-validation), opt-in auto-sweep enablement (after tuning), auto token liquidation (risky), hardware signer (for distribution), dashboard UI.

**Confidence: MEDIUM.** Design sound, grounded in threat analysis; validity depends on real-world false-positive rates.

### Architecture Approach

The architecture unifies wallet/keystore CLI, blockchain indexer, and anomaly detector into one local-only process with a **critical constraint: separate trust zones within the same process**. Secret-bearing code (keystore, funder, sweeper) must be structurally separated from scoring/LLM code.

**Four foundational patterns:**
1. **Unified idempotent ingestion**: WS push and polling-backfill call same `ingest_signature()` function; SQL-level dedup via INSERT OR IGNORE on PK.
2. **Cursor-based reconcile-on-restart**: Persist `last_seen_signature`; on reconnect, backfill via `getSignaturesForAddress` pagination loop.
3. **Structural egress boundary**: Scoring ⇏ keystore (enforced by import-linter contract + frozen `PublicTxView` DTO + runtime canary test). Build the boundary test suite in the *same* phase that introduces scoring, not as later hardening.
4. **Vault/sweep asymmetry**: Funder needs vault secret (only funder imports `keystore.vault`); sweeper needs only session key + vault public key (Solana signing model enforces this). Split keystore into `vault.py`/`session.py` so this is a fact about the import graph, not a runtime check.

**Confidence: HIGH for spec-derived structure; MEDIUM for general patterns.**

### Critical Pitfalls (Top 3)

**1. Unsweepable wallet from fee/rent reserve miscalculation** (Pitfall 1)
- Risk: Flat `FEE_RESERVE` constant leaves dust or strands SPL token rent.
- Prevention: Call `getFeeForMessage` on actual sweep tx; close SPL ATAs first; devnet test ends at exact 0 lamports.

**2. WebSocket drops silently blind the monitor** (Pitfall 11)
- Risk: `logsSubscribe` connection idles without a close frame; monitor believes it's watching when it isn't.
- Prevention: Active heartbeat (reconnect if no message within threshold); force backfill on every reconnect; log gap durations.

**3. False-positive auto-sweep mid-trade** (Pitfall 15)
- Risk: Legitimate aggressive trading (velocity spike, round-trip loss, new counterparty) triggers auto-sweep, liquidating a position.
- Prevention: Multi-signal AND-gate for auto-sweep trigger; expand fixture library beyond 2 golden cases before enabling `--armed`.

**Secondary:** Pitfall 4 (double-spend via retry), Pitfall 9 (LLM egress breach), Pitfall 19 (supply-chain compromise — direct precedent: the Dec 2024 `@solana/web3.js` attack, phished maintainer creds, ~$190K in ~5 hours).

**Confidence: HIGH for Solana mechanics; MEDIUM for Bastion-specific scoring/LLM risks.**

---

## Implications for Roadmap

Follow the spec's build order almost exactly. Dependencies are tight; phases should be sequenced linearly. (This is a research-derived suggestion; the roadmapper produces the authoritative phase structure honoring the project's Standard granularity.)

### Phase 1: Foundation — Config, RPC Client
**Delivers:** Environment loading, safety rails (MAX_SESSION_CAP), stateless RPC wrapper (httpx + websockets).
**Avoids:** Pitfalls 2, 14 (foundational design impacts alert latency and rate-limit handling).

### Phase 2: Secrets — Keystore Split, Encryption
**Delivers:** `vault.py` (funder-only import) + `session.py` (scrypt→Fernet, 0600 perms).
**Avoids:** Pitfalls 6–10 (plaintext leakage, shell history, weak KDF, cloud-sync).

### Phase 3: Fund-Moving — Funder, Sweeper, Devnet Test
**Delivers:** Core value prop (capped, disposable wallets) tested end-to-end on devnet.
**Avoids:** Pitfalls 1–5 (fee/rent, blockhash expiry, confirmed reorg race, double-send, stranded ATA).

### Phase 4: Persistence — Store, Cursor, Idempotent Dedup
**Delivers:** WAL SQLite with idempotent schema (PK on signature, cursor tracking per session).
**Avoids:** Pitfalls 12–13 (pagination gaps, duplicate alerts/sweeps).

### Phase 5: Scoring — Rules, Fixtures, Egress Boundary
**Delivers:** Deterministic rules validated against fixtures (5YEQ churn golden CRITICAL + clean day golden OK); import-linter contract + canary test for LLM egress built *now*, not later.
**Avoids:** Pitfalls 9, 15–17 (egress breach, false positives, baseline poisoning, LLM authority, prompt injection).
**Research flag:** Fixture library needs expansion (current 2 golden cases insufficient before `--armed` recommended). Plan a spike to collect/replay aggressive-but-legitimate trading sessions.

### Phase 6: Monitoring — WS Ingest, Poll Backfill, Reconciliation
**Delivers:** Orchestration loop (Patterns 1+2): active heartbeat + reconnect + resubscribe + backfill; per-stage latency instrumentation on CRITICAL events.
**Avoids:** Pitfalls 11–14 (WS silent drops, pagination gaps, duplicate rescoring, rate-limit bursts).

### Phase 7: Alerting — Out-of-Band Push, Channel Isolation
**Delivers:** Telegram/Pushover with separate identity config validation; precise copy (no unqualified "safe" claims).
**Avoids:** Pitfall 21 (false confidence framing).

### Phase 8: CLI Integration
**Delivers:** Thin start/end/list/status/monitor commands; full user-facing copy audit against Pitfall 21.

### Phase 9: Mainnet Shakeout (Validation, not coding)
**Validates:** Phases 1–8 under real fee volatility and actual Helius rate-limit behavior.

### Phase 10: Pre-Distribution Gate (Non-coding checklist)
**Checklist:** Hash-pinned lockfile + `pip-audit` in CI; signed reproducible releases (PyPI Trusted Publishing + Sigstore); hardware-key 2FA on all publish-capable accounts; crypto counsel regulatory review; fixture library validation (multi-signal AND-gate for auto-sweep + diverse trading scenarios); full copy audit; Ledger/alternative vault path planned or explicitly deferred.

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | MEDIUM-HIGH | Official docs verified; one hidden caveat (requests+async incompatibility) caught. Versions cross-checked as of 2026-07-06. |
| Features | MEDIUM | Design internally consistent, grounded in threat analysis (ecosystem incidents, competitor review). Validity depends on deployed user validation (false-positive rates, baseline effectiveness unknown). |
| Architecture | HIGH (spec structure), MEDIUM (general patterns) | Spec is unusually mature. Four patterns are well-established in blockchain systems; application to Bastion is sound but untested at scale. |
| Pitfalls | HIGH (Solana mechanics), MEDIUM (Bastion-specific) | Solana protocol mechanics verified vs. official docs + incident data. Scoring/LLM risks reasoned from spec + general anomaly-detection literature, not post-mortems. |

**Overall confidence:** MEDIUM-HIGH. Project's *design* is mature and sound (spec is detailed and grounded). Research confirms stack, architecture, pitfall landscape. Remaining risk is *implementation and operational validation*.

### Gaps to Address During Planning/Execution

1. **Scoring fixture library expansion**: Current approach (5YEQ churn + clean day) necessary but insufficient. Before recommending `--armed` for real use, plan a research spike to collect/replay real aggressive-but-legitimate trading sessions (sniping, fast scalping, large real losses) as regression fixtures. This is a planning gate, not a blocker.

2. **Baseline cold-start validation**: Spec acknowledges baseline needs "warm-up" data + historical priors. Risk of baseline poisoning if compromise happens at t=0 is real; severity unknown. Requires a "compromised from t=0" test fixture alongside the mid-session-compromise fixture (5YEQ churn).

3. **RPC rate-limit burst validation**: Helius free tier has both monthly-credit and RPS caps. Worst-case burst (detection + backfill + sweep firing simultaneously across multiple concurrent sessions) needs load-testing before mainnet recommendation. Phase 6 validation task (monitor load test) + Phase 9 observation.

4. **Regulatory posture validation**: Crypto counsel review needed before stranger distribution (Phase 10). Research confirms the tech architecture is non-custodial; legal validation requires expertise beyond research scope.

5. **Ledger integration readiness**: Spec defers hardware-signer vault path to distribution. If planned for v1 stranger release, vendor-specific research is needed on current Ledger API availability and Python bindings. If deferring, document explicitly (acceptable for personal use with env-var secret, risky for strangers).

---

## Sources

### Primary (HIGH confidence)
- **Solana Official Docs:** Transaction confirmation & expiration (~150 blocks / 60–90s), commitment levels, RPC methods, WebSocket subscription behavior
- **Helius Official Docs:** WebSocket patterns (reconnect + backfill), rate limits (10 RPS free tier, 1M credits/month), recommended practices
- **PyPI:** package versions verified (solders 0.27.x, cryptography 49.0.0, websockets 16.0+)
- **Project-internal:** `bastion-spec.md` (§4 scoring rules, §6 reliability, §7 invariants, §10 distribution), `.planning/PROJECT.md`

### Secondary (MEDIUM confidence)
- SQLite WAL + concurrency (charlesleifer: "Going Fast with SQLite", SkyPilot: "Abusing SQLite")
- Import-linter documentation (import-graph boundary enforcement)
- WebSocket reconnection patterns (websocket.org reconnection guide)
- Incident data: web3.js Dec 2024 supply-chain attack (~$190K in ~5 hours), SlowMist trading-bot analysis
- Helius: "Build a Wallet Tracker on Solana", "Solana Hacks, Bugs, and Exploits"

### Tertiary (MEDIUM, needs validation during implementation)
- LLM/anomaly-detection literature: arXiv "Explain First, Trust Later", Chainalysis "Wallet Compromise Detection Kit"
- Competitor feature inventory: wallet trackers (handi-cat, royceanton), pre-exec firewalls (Blowfish, Web3Firewall), rug scanners (RugWatch, Solsniffer), revoke tools (Revoke.cash, Solana Revoker)

---

**Research completed:** 2026-07-06
**Ready for roadmap:** Yes

The spec's build order is sound. Follow it as-is for phases 1–8. Phases 9–10 depend on real-world validation and regulatory review.
