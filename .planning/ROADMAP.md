# Roadmap: Bastion

## Overview

Bastion is built bottom-up as a strictly layered, gated stack: each phase delivers one technical layer that the next depends on, and no fund-moving code runs before the keystore is proven, no mainnet SOL is risked before devnet validates the fund/sweep round-trip, and no armed auto-sweep is wired before the scoring engine is proven against golden fixtures. The journey starts with the shared config + RPC transport both trust zones need, hardens the encrypted keystore, validates capped funding and full sweep on devnet, lays down idempotent persistence + an audit trail, then builds the product's heart — deterministic behavioral scoring with a structurally-enforced LLM-egress boundary — before wiring the live monitor that detects a compromise and (only when armed) contains it. It ends by assembling the CLI, shaking the whole tool out on mainnet with a tiny cap, and hardening distribution so the non-custodial guarantee is auditable before any stranger runs it.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Foundation — Config + RPC Client** - Env config, safety rails, and a mockable JSON-RPC + WebSocket transport both trust zones depend on
- [ ] **Phase 2: Encrypted Keystore + Key-Safety Invariants** - Session keys encrypted at rest (scrypt→Fernet, 0600), never leaked, cloud-sync refused — before any funds move
- [ ] **Phase 3: Fund-Moving on Devnet (Funder + Sweeper)** - Capped vault→session funding and exact-zero session→vault sweep, validated end-to-end on devnet
- [ ] **Phase 4: Persistence — SQLite Store + Audit Log** - WAL-mode idempotent store (sessions/transactions/alerts/baselines/cursors) plus append-only JSONL audit trail
- [ ] **Phase 5: Scoring Engine + LLM-Egress Boundary** - Deterministic, fixture-validated behavioral scoring with the scoring⇏keystore egress boundary enforced structurally
- [ ] **Phase 6: Live Monitor, Out-of-Band Alerting + Armed Auto-Sweep** - Near-real-time detection surviving RPC hiccups; out-of-band alerts and (armed only) auto-sweep containment
- [ ] **Phase 7: CLI Assembly + Mainnet Shakeout** - Full session-lifecycle CLI, devnet dry-run, and a tiny-cap mainnet live shakeout
- [ ] **Phase 8: Distribution Hardening** - Auditable non-custodial guarantee, honest copy, and a trustworthy supply chain before stranger use

## Phase Details

### Phase 1: Foundation — Config + RPC Client
**Goal**: A stable, mockable configuration layer and JSON-RPC + WebSocket transport that both trust zones depend on, with safety rails externalized from day one.
**Depends on**: Nothing (first phase)
**Requirements**: CLI-05, CLI-06
**Success Criteria** (what must be TRUE):
  1. Config loads all documented env vars (SOLANA_RPC, SOLANA_WS, VAULT_SECRET, VAULT_PUBKEY, KEYSTORE_DIR, TELEGRAM_*, PUSHOVER_*) and falls back to `getpass` for the passphrase when unset.
  2. Safety rails (MAX_SESSION_CAP, FEE_RESERVE_LAMPORTS, scoring thresholds) are read from config, not hardcoded; a test asserts each is overridable.
  3. The RPC client retries and backs off on injected 429 responses without crashing (mocked-RPC test passes).
  4. The WebSocket client reconnects and re-subscribes after a forced silent drop, detected via an active heartbeat (not only `onclose`/`onerror`).
  5. The `getSignaturesForAddress` helper paginates via `before`/`until` cursor across a >1000-signature mocked stream without truncating.
**Plans**: 3 plans

Plans:
- [ ] 01-01: config.py — env loading, safety rails (MAX_SESSION_CAP/FEE_RESERVE/thresholds), getpass fallback, hash-pinned lockfile habit from setup
- [ ] 01-02: rpc/client.py — JSON-RPC POST, 429 retry/backoff (priority-aware), getSignaturesForAddress pagination, getFeeForMessage/getLatestBlockhash/send_raw/status helpers
- [ ] 01-03: rpc/ws.py — logsSubscribe/accountSubscribe, active heartbeat, auto-reconnect + resubscribe on silent drop
**UI hint**: no

### Phase 2: Encrypted Keystore + Key-Safety Invariants
**Goal**: Session keys are safe at rest and in memory — encrypted, owner-only, never leaked — with the vault/session split established structurally before any fund-moving code exists.
**Depends on**: Phase 1
**Requirements**: SESS-01, SESS-04, SESS-05, SEC-01, SEC-04, SEC-05
**Success Criteria** (what must be TRUE):
  1. An encrypt→decrypt roundtrip recovers the exact keypair; a wrong passphrase fails closed (raises, never returns a partial or garbage key).
  2. Keystore files are written with 0600 permissions and versioned KDF params (scrypt n at or above the current recommended floor) stored in the file format.
  3. A grep-based test over captured test-suite stdout/stderr finds no secret-shaped strings — no plaintext key ever reaches disk or logs.
  4. Startup refuses to run when KEYSTORE_DIR resolves under a cloud-sync path (Dropbox/OneDrive/iCloud/Google Drive), verified with a synthetic path.
  5. Passphrase entry is confirmed on create, never echoed to the terminal, and never logged.
**Plans**: 4 plans

Plans:
- [ ] 02-01: keystore/crypto.py — scrypt→Fernet primitives, versioned KDF params, redacted `__repr__`/`__str__` on secret-wrapping types
- [ ] 02-02: keystore/session.py — generate/save(0600)/load/retire; wrong-passphrase-fails-closed
- [ ] 02-03: keystore/vault.py — load_vault() isolated so only funder can import it (import-graph fact)
- [ ] 02-04: cloud-sync refusal check + passphrase confirm/no-echo flow + no-secret-in-logs regression test
**UI hint**: no

### Phase 3: Fund-Moving on Devnet (Funder + Sweeper)
**Goal**: The core containment primitive — capped vault→session funding and full session→vault sweep — validated end-to-end on devnet before any mainnet SOL is at risk.
**Depends on**: Phase 2 (keystore roundtrip + perms tests must pass first)
**Requirements**: SESS-02, SESS-03, SESS-06, SESS-07, SEC-02
**Success Criteria** (what must be TRUE):
  1. The funder moves the requested SOL from vault to a fresh session wallet on devnet; a test asserts the exact balance delta.
  2. The funder refuses and sends nothing when the requested cap exceeds MAX_SESSION_CAP.
  3. The sweeper returns remaining SOL to VAULT_PUBKEY using a `getFeeForMessage`-based reserve, ending a devnet wallet (SOL plus one open ATA closed first) at exactly zero lamports.
  4. The sweep path loads only the session key and VAULT_PUBKEY and is structurally incapable of loading the vault secret; an injected post-send timeout produces no double-spend.
  5. A swept session's keystore can be retired, and retire refuses to hard-delete when a nonzero token balance remains.
**Plans**: 4 plans

Plans:
- [ ] 03-01: funder.py — capped vault→session transfer, MAX_SESSION_CAP guard, build→sign→record→send→land-check idempotency
- [ ] 03-02: sweeper.py — session→vault, getFeeForMessage reserve, close empty ATAs first, exact-zero close, already-empty no-op
- [ ] 03-03: retire semantics — don't hard-delete on nonzero token balance; manual end-session flow (sweep→retire)
- [ ] 03-04: devnet end-to-end tests — fund→sweep round trip, injected-timeout no-double-spend, SOL+ATA to exact zero
**UI hint**: no

### Phase 4: Persistence — SQLite Store + Audit Log
**Goal**: Durable, idempotent state (sessions, transactions, alerts, baselines, cursors) plus a tamper-evident audit trail the monitor and scoring layers build on — with idempotency designed in at the schema level.
**Depends on**: Phase 3
**Requirements**: AUD-01, AUD-02, MON-04
**Success Criteria** (what must be TRUE):
  1. The store initializes a WAL-mode SQLite schema (sessions/transactions/alerts/baselines) with single-writer configuration (`journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout`) on an empty DB.
  2. Inserting the same transaction signature twice yields exactly one row (INSERT OR IGNORE on the signature PK) — re-ingesting a seen signature is a no-op.
  3. Every fund/sweep/alert/decision appends one line to an append-only JSONL audit log that is never rewritten.
  4. A per-session last-seen cursor persists and advances only within the same DB transaction that records a scored signature.
**Plans**: 3 plans

Plans:
- [ ] 04-01: store/db.py + schema.sql — WAL/busy_timeout/synchronous connection factory; sessions/transactions/alerts/baselines tables
- [ ] 04-02: store/dao.py — idempotent inserts (INSERT OR IGNORE on sig), tx_seen check, per-session cursor CRUD
- [ ] 04-03: audit.py — append-only JSONL writer routed through one function for every mutating module
**UI hint**: no

### Phase 5: Scoring Engine + LLM-Egress Boundary
**Goal**: The product's heart — deterministic, fixture-validated behavioral scoring — with the scoring⇏keystore egress boundary enforced structurally in the same phase, so the non-custodial guarantee is provable, not hoped-for.
**Depends on**: Phase 4
**Requirements**: SCOR-01, SCOR-02, SCOR-03, SCOR-04, SCOR-05, SCOR-06, SEC-03
**Success Criteria** (what must be TRUE):
  1. `score()` emits a verdict with reasons and summed weights, classifying each transaction OK / WATCH / CRITICAL by threshold.
  2. The rule set fires on velocity spikes, round-trip loss, same-day liquidation, realized-loss bursts, new-counterparty sinks, approve/setAuthority changes, and off-baseline size/timing.
  3. The regression suite replays the 5YEQ-churn drain fixture → CRITICAL and a clean-trading-day fixture → OK, both green.
  4. A per-session behavioral baseline (median clip size, typical hold time, known counterparties) is computed and drives deviation-based scoring, with absolute cap-relative rules active during warm-up.
  5. An import-linter contract plus a canary/runtime test prove scoring and LLM code never import keystore and no key material can reach the network payload; the LLM output feeds only the alert message, never the sweep decision.
**Plans**: 5 plans

Plans:
- [ ] 05-01: scoring/rules.py — pure deterministic rules, reasons + summed weights, OK/WATCH/CRITICAL thresholds, multi-signal cluster logic
- [ ] 05-02: scoring/baseline.py — per-session rolling baseline (median clip/hold/counterparties), t=0 poisoning guard + cross-session priors
- [ ] 05-03: fixture regression suite — recorded 5YEQ churn → CRITICAL, clean day → OK, plus compromised-from-t=0 fixture
- [ ] 05-04: scoring/payload.py + scoring/llm.py — frozen PublicTxView DTO (public fields only), LLM explains/confirms, untrusted on-chain text delimited
- [ ] 05-05: egress boundary — .importlinter contract (scoring ⇏ keystore/funder/sweeper) + canary-secret runtime egress test (SEC-03)
**UI hint**: no

### Phase 6: Live Monitor, Out-of-Band Alerting + Armed Auto-Sweep
**Goal**: Near-real-time detection that survives RPC hiccups and never misses an event, and — only now that scoring is proven against fixtures — contains a compromise by alerting out-of-band and, when armed, auto-sweeping.
**Depends on**: Phase 5 (scoring validated against golden fixtures before armed auto-sweep is wired)
**Requirements**: MON-01, MON-02, MON-03, MON-05, MON-06, ALRT-01, ALRT-02, ALRT-03, ALRT-04
**Success Criteria** (what must be TRUE):
  1. The monitor watches an active session's transactions over WebSocket and scores each new signature through the shared idempotent `ingest_signature` path (WS and poll share one code path).
  2. On every reconnect and on monitor restart, each active session is reconciled against on-chain state and backfilled from its last-seen cursor before resuming live so no event is missed; a forced silent WS drop is detected via heartbeat and recovered.
  3. A CRITICAL verdict pushes a plain-English verdict + reasons + session pubkey + armed state to an out-of-band channel (Telegram/Pushover) whose identity is validated as distinct from the trading session.
  4. With `--armed` on a CRITICAL verdict, the Sweeper drains remaining SOL to the vault and then alerts; without `--armed` the default is alert-only with no automatic action, and auto-sweep requires a multi-signal trigger.
  5. A timed replay of the 5YEQ fixture measures end-to-end alert dispatch within the <5s target, with the LLM call kept off the sweep-critical path and per-stage latency logged.
**Plans**: 5 plans

Plans:
- [ ] 06-01: monitor.py — shared ingest_signature path (dedupe→fetch→score→advance cursor→dispatch); WS + poll both call it
- [ ] 06-02: reconcile-on-restart + backfill pagination loop + balance reconciliation + heartbeat-driven reconnect/resubscribe
- [ ] 06-03: alerter (telegram.py / pushover.py) — out-of-band push, separate-identity validation, distinct WATCH vs CRITICAL formatting
- [ ] 06-04: armed auto-sweep wiring — CRITICAL+armed → sweep→alert, alert-only default, multi-signal AND-gate, sweep off LLM path
- [ ] 06-05: per-stage latency instrumentation + <5s timed 5YEQ replay + priority-aware RPC budget under incident burst
**UI hint**: no

### Phase 7: CLI Assembly + Mainnet Shakeout
**Goal**: A working end-to-end CLI for the full session lifecycle, dry-run on devnet and shaken out on mainnet with a tiny cap against real fee volatility and rate limits.
**Depends on**: Phase 6
**Requirements**: CLI-01, CLI-02, CLI-03, CLI-04, SESS-08
**Success Criteria** (what must be TRUE):
  1. `start --fund <SOL> [--armed] [--rotate-on-loss <SOL>]` mints, funds, and begins a monitored session; `end --wallet <pubkey> [--retire]` sweeps and closes it.
  2. `list` and `status --wallet <pubkey>` inspect sessions, and `monitor [--armed]` watches active sessions via the shared sessions-table polling contract.
  3. Opting into `--rotate-on-loss` rotates the session (sweep old, mint + fund fresh) when the loss or time threshold is crossed — never per-fill.
  4. The full lifecycle (start → trade → score → alert → sweep → end) completes on a devnet dry-run.
  5. A tiny-cap (e.g. 0.05 SOL) mainnet shakeout validates the assembled tool against real fee volatility and Helius rate-limit behavior.
**Plans**: 5 plans

Plans:
- [ ] 07-01: cli.py — `start` and `end` commands wiring keystore/funder/sweeper/store
- [ ] 07-02: cli.py — `list` / `status` / `monitor` commands + sessions-table polling contract with the monitor process
- [ ] 07-03: session rotation orchestration (`--rotate-on-loss`) — loss/time threshold → sweep old + mint/fund new
- [ ] 07-04: devnet full-lifecycle manual dry-run of start→trade→score→alert→sweep→end
- [ ] 07-05: tiny-cap mainnet shakeout — observe fee volatility, rate limits, alert latency on real chain
**UI hint**: no

### Phase 8: Distribution Hardening
**Goal**: Make the non-custodial guarantee auditable, the user-facing framing honest, and the supply chain trustworthy before any stranger runs a fund-moving tool.
**Depends on**: Phase 7
**Requirements**: DIST-01, DIST-02, DIST-03, DIST-04, DIST-05
**Success Criteria** (what must be TRUE):
  1. A documented data-egress list ships, and a test/audit confirms no telemetry path can carry key material.
  2. The repo ships `.gitignore` and `.env.example` that keep secrets, keystores, and DB files out of version control.
  3. A permissive OSS license (MIT/Apache-2.0) and a plain-language non-custodial NOTICE/disclaimer are present.
  4. User-facing CLI and alert copy is audited to frame Bastion as a bounded safety aid — no unqualified "safe/protected/secured" claims.
  5. Releases are signed with published checksums, and dependencies are hash-pinned and verified with `pip-audit` in CI.
**Plans**: 5 plans

Plans:
- [ ] 08-01: data-egress list document + no-telemetry-carries-key-material audit (DIST-01)
- [ ] 08-02: .gitignore + .env.example keeping secrets/keystores/*.db out of version control (DIST-02)
- [ ] 08-03: OSS license (MIT/Apache-2.0) + plain-language non-custodial NOTICE/disclaimer (DIST-03)
- [ ] 08-04: user-facing copy audit — bounded safety-aid framing, no unqualified safe/protected/secured claims (DIST-04)
- [ ] 08-05: signed reproducible releases + published checksums + hash-pinned deps + pip-audit in CI (DIST-05)
**UI hint**: no

## Progress

**Execution Order:**
Phases execute in numeric order: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation — Config + RPC Client | 0/3 | Not started | - |
| 2. Encrypted Keystore + Key-Safety Invariants | 0/4 | Not started | - |
| 3. Fund-Moving on Devnet (Funder + Sweeper) | 0/4 | Not started | - |
| 4. Persistence — SQLite Store + Audit Log | 0/3 | Not started | - |
| 5. Scoring Engine + LLM-Egress Boundary | 0/5 | Not started | - |
| 6. Live Monitor, Out-of-Band Alerting + Armed Auto-Sweep | 0/5 | Not started | - |
| 7. CLI Assembly + Mainnet Shakeout | 0/5 | Not started | - |
| 8. Distribution Hardening | 0/5 | Not started | - |
