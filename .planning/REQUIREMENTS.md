# Requirements: Bastion

**Defined:** 2026-07-06
**Core Value:** A compromised trading session is a dead end with a small, pre-decided balance — the vault behind it is never drained.

## v1 Requirements

Requirements for the initial release (personal-use MVP through mainnet shakeout + distribution hardening). Each maps to a roadmap phase.

### Session & Keys

- [x] **SESS-01**: User can generate a fresh Solana keypair for a new session
- [x] **SESS-02**: User can fund a session wallet from the vault with a specified SOL cap
- [x] **SESS-03**: System refuses to fund when the requested cap exceeds `MAX_SESSION_CAP`
- [x] **SESS-04**: Session keys are encrypted at rest (scrypt → Fernet) and keystore files are owner-only (0600)
- [x] **SESS-05**: User can load a session keypair by pubkey with a passphrase; a wrong passphrase fails closed
- [ ] **SESS-06**: User can sweep remaining SOL back to the vault on manual session end
- [x] **SESS-07**: User can retire (remove) a session keystore after it has been swept
- [ ] **SESS-08**: User can opt into session rotation triggered by a loss or time threshold (not per-fill)

### Security Invariants

<!-- Each is a test, not a comment. -->

- [x] **SEC-01**: No plaintext private key is ever written to disk or emitted to logs
- [x] **SEC-02**: The vault secret is loaded only for funding; sweeps target `VAULT_PUBKEY` and require no vault secret
- [ ] **SEC-03**: The scoring/LLM path can access only public on-chain data and has no keystore access (structural import boundary + canary test)
- [x] **SEC-04**: System refuses to run when the keystore directory appears to be cloud-synced
- [x] **SEC-05**: Passphrase entry is confirmed on create, never echoed to the terminal, and never logged

### Monitoring

- [ ] **MON-01**: User can watch a session wallet's transactions in near-real-time over WebSocket
- [ ] **MON-02**: Monitor backfills signatures since last-seen on every reconnect so no event is missed
- [ ] **MON-03**: Monitor auto-reconnects and uses heartbeat detection to catch silent WebSocket drops
- [ ] **MON-04**: Ingestion is idempotent — signatures are deduped and re-scoring a seen signature is a no-op
- [ ] **MON-05**: On monitor restart, each active session is reconciled against on-chain state and backfilled before resuming
- [ ] **MON-06**: Alert fires within < 5s of on-chain confirmation (latency target)

### Scoring

- [ ] **SCOR-01**: System scores each transaction with deterministic rules, emitting reasons and summed weights
- [ ] **SCOR-02**: System classifies each transaction as OK / WATCH / CRITICAL by threshold
- [ ] **SCOR-03**: System maintains a per-session behavioral baseline (median clip size, typical hold time, known counterparties)
- [ ] **SCOR-04**: Rule set detects velocity spikes, round-trip loss, same-day liquidation, realized-loss bursts, new-counterparty sinks, approve/setAuthority changes, and off-baseline size/timing
- [ ] **SCOR-05**: A regression suite replays the 5YEQ-churn drain fixture (→ CRITICAL) and a clean-trading-day fixture (→ OK)
- [ ] **SCOR-06**: An LLM layer explains/confirms flagged transactions in plain English but is never the sole gate on moving funds

### Alerting

- [ ] **ALRT-01**: System pushes a plain-English verdict + reasons + session pubkey + armed state to an out-of-band channel (Telegram and/or Pushover)
- [ ] **ALRT-02**: The alert channel is isolated from the trading session (separate identity)
- [ ] **ALRT-03**: On a CRITICAL verdict when a session is `--armed`, the Sweeper drains remaining SOL to the vault and then alerts
- [ ] **ALRT-04**: Auto-sweep requires explicit `--armed`; the default is alert-only

### Audit & State

- [ ] **AUD-01**: System writes an append-only JSONL audit log of every fund / sweep / alert / decision
- [ ] **AUD-02**: System persists sessions, transactions, alerts, and baselines in a WAL-mode SQLite store with a single writer (the monitor)

### CLI & Config

- [ ] **CLI-01**: User can run `start --fund <SOL> [--armed] [--rotate-on-loss <SOL>]` to mint, fund, and begin a session
- [ ] **CLI-02**: User can run `end --wallet <pubkey> [--retire]` to sweep and close a session
- [ ] **CLI-03**: User can run `list` and `status --wallet <pubkey>` to inspect sessions
- [ ] **CLI-04**: User can run `monitor [--armed]` to watch active sessions
- [x] **CLI-05**: Configuration is read from env (`SOLANA_RPC`, `SOLANA_WS`, `VAULT_SECRET`, `VAULT_PUBKEY`, `KEYSTORE_DIR`, `KEYSTORE_PASSPHRASE`, `TELEGRAM_*`, `PUSHOVER_*`) with a `getpass` fallback for the passphrase
- [x] **CLI-06**: Safety rails are configurable (`MAX_SESSION_CAP`, `FEE_RESERVE_LAMPORTS`, scoring thresholds)

### Distribution Hardening

- [ ] **DIST-01**: A documented data-egress list exists; no telemetry can carry key material
- [ ] **DIST-02**: Repo ships `.gitignore` and `.env.example` that keep secrets and keystores out of version control
- [ ] **DIST-03**: Project carries a permissive OSS license (MIT/Apache-2.0) and a plain-language non-custodial NOTICE/disclaimer
- [ ] **DIST-04**: User-facing copy frames Bastion as a safety aid, not a guarantee — no unqualified "you're safe now" claims
- [ ] **DIST-05**: Releases are signed with published checksums; dependencies are pinned and hash-verified with `pip-audit` in CI

## v2 Requirements

Deferred to a future release. Tracked but not in the current roadmap.

### Sweep

- **SWEEP-V2-01**: Auto token liquidation on sweep (swap SPL positions back, close empty ATAs to reclaim rent) — needs trustworthy slippage/route logic
- **SWEEP-V2-02**: `sweep_tokens` promoted from v1 stub to full implementation

### Scoring

- **SCOR-V2-01**: Expanded fixture library of aggressive-but-legitimate trading sessions before `--armed` is recommended broadly
- **SCOR-V2-02**: Learned model of the user's trading, once enough labeled sessions exist to beat the handwritten rules

### Vault

- **VAULT-V2-01**: Hardware-signer (Ledger) path for the vault, first-class for distribution

### Distribution (stranger release, external-party gated)

- **DIST-V2-01**: External security review of key-handling and fund-moving paths
- **DIST-V2-02**: Crypto counsel sign-off on architecture and disclaimer before stranger mainnet release
- **DIST-V2-03**: Public non-custodial data-egress statement published

### UI

- **UI-V2-01**: Multi-wallet dashboard UI
- **UI-V2-02**: Historical PnL attribution

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Per-trade / per-fill wallet rotation | Solana rent/ATA economics make it impractical; loss-threshold / time-window rotation is used instead |
| Anonymity / mixing | Design stance is isolation, not anonymity; session wallets funded from one vault are linkable on-chain and that's acceptable |
| Any server / hosted service in the fund path; inbound ports | Breaks the non-custodial guarantee and the regulatory posture; Bastion is single-user, single-machine |
| LLM as the sole gate on moving funds | Deterministic rules gate; the LLM only explains/confirms and reduces false positives |
| Armed-by-default auto-sweep | Invites wrong-vault sweeps and false-positive liquidations; arming must be a deliberate, well-warned opt-in |

## Traceability

Which phases cover which requirements. Populated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| SESS-01 | Phase 2 | Complete |
| SESS-02 | Phase 3 | Complete |
| SESS-03 | Phase 3 | Complete |
| SESS-04 | Phase 2 | Complete |
| SESS-05 | Phase 2 | Complete |
| SESS-06 | Phase 3 | Pending |
| SESS-07 | Phase 3 | Complete |
| SESS-08 | Phase 7 | Pending |
| SEC-01 | Phase 2 | Complete |
| SEC-02 | Phase 3 | Complete |
| SEC-03 | Phase 5 | Pending |
| SEC-04 | Phase 2 | Complete |
| SEC-05 | Phase 2 | Complete |
| MON-01 | Phase 6 | Pending |
| MON-02 | Phase 6 | Pending |
| MON-03 | Phase 6 | Pending |
| MON-04 | Phase 4 | Pending |
| MON-05 | Phase 6 | Pending |
| MON-06 | Phase 6 | Pending |
| SCOR-01 | Phase 5 | Pending |
| SCOR-02 | Phase 5 | Pending |
| SCOR-03 | Phase 5 | Pending |
| SCOR-04 | Phase 5 | Pending |
| SCOR-05 | Phase 5 | Pending |
| SCOR-06 | Phase 5 | Pending |
| ALRT-01 | Phase 6 | Pending |
| ALRT-02 | Phase 6 | Pending |
| ALRT-03 | Phase 6 | Pending |
| ALRT-04 | Phase 6 | Pending |
| AUD-01 | Phase 4 | Pending |
| AUD-02 | Phase 4 | Pending |
| CLI-01 | Phase 7 | Pending |
| CLI-02 | Phase 7 | Pending |
| CLI-03 | Phase 7 | Pending |
| CLI-04 | Phase 7 | Pending |
| CLI-05 | Phase 1 | Complete |
| CLI-06 | Phase 1 | Complete |
| DIST-01 | Phase 8 | Pending |
| DIST-02 | Phase 8 | Pending |
| DIST-03 | Phase 8 | Pending |
| DIST-04 | Phase 8 | Pending |
| DIST-05 | Phase 8 | Pending |

**Coverage:**

- v1 requirements: 42 total
- Mapped to phases: 42 ✓ (100% coverage; each requirement to exactly one phase, no orphans, no duplicates)
- Unmapped: 0

**Per-phase requirement counts:**

- Phase 1 (Foundation — Config + RPC): 2 — CLI-05, CLI-06
- Phase 2 (Encrypted Keystore + Key-Safety): 6 — SESS-01, SESS-04, SESS-05, SEC-01, SEC-04, SEC-05
- Phase 3 (Fund-Moving on Devnet): 5 — SESS-02, SESS-03, SESS-06, SESS-07, SEC-02
- Phase 4 (Persistence — Store + Audit): 3 — AUD-01, AUD-02, MON-04
- Phase 5 (Scoring + LLM-Egress Boundary): 7 — SCOR-01, SCOR-02, SCOR-03, SCOR-04, SCOR-05, SCOR-06, SEC-03
- Phase 6 (Monitor + Alerting + Armed Sweep): 9 — MON-01, MON-02, MON-03, MON-05, MON-06, ALRT-01, ALRT-02, ALRT-03, ALRT-04
- Phase 7 (CLI + Mainnet Shakeout): 5 — CLI-01, CLI-02, CLI-03, CLI-04, SESS-08
- Phase 8 (Distribution Hardening): 5 — DIST-01, DIST-02, DIST-03, DIST-04, DIST-05

---
*Requirements defined: 2026-07-06*
*Last updated: 2026-07-06 after roadmap creation (traceability populated)*
