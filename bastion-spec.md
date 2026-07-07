# Bastion — Architecture Spec

**Name:** *Bastion* — a bastion is the hardened, isolated forward point that shields everything behind it. In network security a "bastion host" is the single exposed, locked-down entry that protects the internal system; this tool applies the same pattern to trading — the disposable session wallet is the bastion, taking all the exposure so the vault behind it never does. Distributed as a non-custodial, local-first CLI (see §10). Suggested PyPI slug `bastion` (verify at publish; qualify to `bastion-sol` if claimed); confirm the GitHub org handle and domain before release.

**Purpose:** Blast-radius containment for Solana terminal/bot trading. Trade from disposable, capped session wallets that connect to nothing else you own; watch each session in real time; auto-sweep on anomaly. Converts "lost everything" into "lost the cap."

**Threat model (grounding):** The failure this defends against is a *session compromise* — a hostile trading terminal or phished bot session trading your funds through pools (mechanically indistinguishable from normal swaps, so wallet-level protections and tx simulation never fire). Defense is therefore **containment + behavioral detection**, not signature inspection.

**Design stance:** Isolation, not anonymity. Session wallets funded from one vault are linkable on-chain; that's acceptable. The goal is that a compromised session is a dead end with a small, pre-decided balance.

**Stack:** Python 3.11+. `solders` (keys/tx), `requests` (JSON-RPC), `cryptography` (keystore), `websockets` (live monitor), `apscheduler` or asyncio (scheduling). Helius RPC + WebSocket (free tier). No framework needed; runs as a local CLI + long-running monitor process.

---

## 1. Requirements

**Functional**
- Generate a fresh keypair per session (opt-in per-trade rotation on threshold, not per-fill).
- Fund a session wallet from a vault with a hard SOL cap.
- Encrypt session keys at rest; owner-only files.
- Watch a session wallet's transactions in near-real-time.
- Score each transaction against a behavioral ruleset; classify normal vs. anomalous.
- On anomaly: alert instantly and (optional) auto-sweep remaining SOL to vault.
- Sweep-to-vault on manual session end; retire keystore.
- Full audit log of every fund/sweep/alert/decision.

**Non-functional**
- Alert latency target: < 5s from on-chain confirmation to push.
- Monitor must survive RPC hiccups (reconnect + backfill via polling fallback).
- Never write a plaintext private key to disk. Never log secrets.
- Single-user, single-machine. No server, no inbound ports.

**Constraints**
- Solo maintainer, Python-first. Helius free tier rate limits. Solana rent/ATA economics make per-fill wallets impractical.

---

## 2. High-Level Design

### Component map

```
                    ┌─────────────────────────────────────────┐
                    │              CLI (cli.py)                │
                    │  start │ end │ list │ monitor │ status   │
                    └───────────────┬─────────────────────────┘
                                    │
     ┌──────────────┬──────────────┼───────────────┬──────────────┐
     ▼              ▼              ▼               ▼              ▼
┌─────────┐  ┌────────────┐  ┌──────────┐   ┌───────────┐  ┌──────────┐
│ Keystore│  │  Funder    │  │ Sweeper  │   │  Monitor  │  │  Alerter │
│(vault + │  │(vault→sess │  │(sess→    │   │(ws + poll │  │(telegram/│
│ session │  │  capped)   │  │  vault)  │   │  fallback)│  │ pushover)│
│  keys)  │  └─────┬──────┘  └────┬─────┘   └─────┬─────┘  └────┬─────┘
└────┬────┘        │              │               │             │
     │             ▼              ▼               ▼             │
     │        ┌──────────────────────────────────────┐         │
     └───────▶│         RPC Client (rpc.py)           │         │
              │  JSON-RPC + WS, retry/backoff, Helius  │         │
              └───────────────┬──────────────────────┘         │
                              │                                 │
                              ▼                                 │
                    ┌──────────────────┐    ┌──────────────────┐│
                    │  Scoring Engine  │◀───┤   Session Store  ││
                    │ (rules + LLM)    │    │ (sqlite: state,  ││
                    └────────┬─────────┘    │  baselines, log) ││
                             │              └──────────────────┘│
                             └───────────── anomaly ────────────┘
                                       (→ alert + optional auto-sweep)
```

### Data flow — session lifecycle

1. **start**: Keystore mints keypair → Funder moves capped SOL from vault → Session Store records `{pubkey, cap, started_at, baseline}` → CLI prints pubkey to connect.
2. **trade**: Monitor subscribes to the session pubkey (WS `logsSubscribe` / `accountSubscribe`) → each new signature fetched, parsed, scored.
3. **anomaly**: Scoring Engine flags → Alerter pushes plain-English summary → if `--armed`, Sweeper drains remaining SOL to vault and marks session burned.
4. **end**: Sweeper returns remainder → keystore retired → Session Store closes the record.

---

## 3. Module Breakdown (build order)

Build in this sequence; each is independently testable.

### `rpc.py` — RPC/WS client
- `rpc(method, params)` — JSON-RPC POST with retry + 429 backoff (reuse the pattern already proven).
- `get_balance`, `get_latest_blockhash`, `get_signatures(pubkey, before=None, limit)`, `get_transaction(sig)`.
- `send_raw(signed_tx_b64)`.
- `ws_subscribe_logs(pubkey, callback)` — persistent WebSocket, auto-reconnect, heartbeat. On drop, signal Monitor to backfill via polling.
- **Test:** mock RPC responses; assert backoff on injected 429s.

### `keystore.py` — key management
- `generate() -> Keypair`.
- `save(kp, passphrase) -> path` — scrypt (n=2^14) → Fernet; file chmod 0600; store `{pubkey, salt, ciphertext, created}`.
- `load(pubkey, passphrase) -> Keypair`.
- `load_vault()` — from `VAULT_SECRET` env (base58 or JSON array). Prefer `VAULT_PUBKEY` for receive-only paths so the vault secret isn't loaded to sweep.
- `retire(pubkey)`.
- **Invariants:** never log secret bytes; passphrase from env or `getpass` only.
- **Test:** encrypt→decrypt roundtrip; wrong passphrase fails closed; file perms == 0600.

### `funder.py` — vault → session (capped)
- `fund(vault_kp, session_pubkey, sol_cap)` — balance check, build/sign/send transfer, confirm, audit.
- Guard: refuse if `cap > MAX_SESSION_CAP` (config safety rail).
- **Test:** devnet transfer; assert balance delta and cap enforcement.

### `sweeper.py` — session → vault
- `sweep(session_kp, vault_pubkey)` — send `balance - FEE_RESERVE`; confirm; audit.
- `sweep_tokens(session_kp, ...)` — **stub for v2.** SOL-only in v1; document that SPL positions are swapped back in-terminal first, empty ATAs closed to reclaim rent. Do not auto-liquidate in v1.
- **Test:** devnet fund→sweep; assert vault receives, session left at ~fee reserve.

### `store.py` — state + baselines (SQLite)
- Tables: `sessions`, `transactions`, `alerts`, `baselines` (§5).
- Thin DAO; WAL mode; single-writer (the monitor).
- **Test:** CRUD + migration on empty DB.

### `scoring.py` — the actual product
- `score(tx, session_ctx) -> Verdict{level, reasons[]}`.
- Deterministic rules first (§4). LLM pass second, only on rule-flagged or ambiguous tx, to explain/confirm — never as the sole gate (cost + latency).
- **Test:** replay recorded drain fixtures (the 5YEQ churn) → asserts CRITICAL; replay normal trading → asserts OK. This is the regression suite that matters most.

### `monitor.py` — orchestration loop
- Subscribe active sessions; on new sig → fetch tx → `store` → `score` → route verdict.
- Reconnect logic + polling backfill so nothing is missed across WS drops.
- If verdict ≥ threshold and session `armed`: call Sweeper, then Alerter.
- **Test:** feed a synthetic signature stream; assert alert + (armed) sweep fire.

### `alerter.py` — out-of-band notification
- Telegram Bot API push to a **separate** Telegram account (not the trading one), and/or Pushover.
- Message = plain-English verdict + reasons + session pubkey + "armed/sweeping" state.
- **Rule:** alert channel must not share a compromise domain with the trading session.
- **Test:** dry-run formatter; live send behind `--notify`.

### `cli.py` — entrypoint
- `start --fund <SOL> [--armed] [--rotate-on-loss <SOL>]`
- `end --wallet <pubkey> [--retire]`
- `list` / `status --wallet <pubkey>` / `monitor [--armed]`
- Wires config from env; `getpass` fallback for passphrase.

### `config.py`
- Env: `SOLANA_RPC`, `SOLANA_WS`, `VAULT_SECRET`, `VAULT_PUBKEY`, `KEYSTORE_DIR`, `KEYSTORE_PASSPHRASE`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `PUSHOVER_*`.
- Safety rails: `MAX_SESSION_CAP`, `FEE_RESERVE_LAMPORTS`, scoring thresholds.

---

## 4. Scoring rules (the heart of it)

Any one is noise; a **cluster inside a short window** is the signal. Emit reasons, sum weights, threshold into OK / WATCH / CRITICAL.

| Rule | Signal | Why it caught yesterday |
|---|---|---|
| Velocity spike | N tx in < T seconds (e.g. ≥5 in 60s) | 7+ tx in one minute |
| Round-trip loss | buy→sell same mint < 90s at material loss | ~50% loss per cycle |
| Same-day liquidation | sell of a position accumulated earlier same session | CLAWVILLE dumped hours after buy |
| Realized-loss burst | cumulative realized SOL loss > X% of cap in window | −1.33 SOL in 5 min |
| New-counterparty sink | net SOL exits toward pools/addresses unseen in baseline | 5YEQ pool churn |
| Approve / setAuthority | delegate or authority change appears | (didn't fire here, but classic drain) |
| Off-baseline hour/size | tx size or timing far from this session's profile | attacker's clip pattern ≠ yours |

**Baseline:** per-session rolling profile (median clip size, typical hold time, known counterparties) seeded from the wallet's own early-session activity and your historical norms. Deviation, not absolute values, drives WATCH/CRITICAL — because "signed by you" is meaningless when the session is the attacker.

**LLM layer:** on flagged tx, hand parsed instructions to the model to produce the human sentence ("this looks like value extraction, not trading — 0.5 SOL round-tripped through one pool at 50% loss twice in 80s") and a confirm/deny. Rules gate; LLM explains and reduces false positives. Never let the LLM be the only thing between an attacker and a sweep.

---

## 5. Data model (SQLite)

```sql
sessions(pubkey PK, cap_sol, armed, status,           -- active|swept|burned
         started_at, ended_at, keystore_path)
transactions(sig PK, session_pubkey FK, block_time,
             sol_delta, parsed_json, verdict_level, reasons_json)
alerts(id PK, session_pubkey FK, ts, level, message, delivered)
baselines(session_pubkey PK, median_clip, typ_hold_secs,
          known_counterparties_json, updated_at)
```

Audit log stays append-only JSONL alongside the DB for tamper-evidence.

---

## 6. Scale & reliability
- Load is trivial (one trader, a few live sessions). The reliability risk is **missed events**, not throughput.
- WS is primary; **polling backfill** is the safety net — on every reconnect, pull signatures since last-seen and replay through scoring so a drop never blinds you.
- Idempotency: dedupe on signature; re-scoring a seen sig is a no-op.
- Failure mode to design for: monitor process dies mid-session. On restart, reconcile each active session's on-chain state vs. last-seen and backfill before resuming.

---

## 7. Security invariants (make these tests, not comments)
1. No plaintext key ever hits disk or logs.
2. Keystore files are 0600; keystore dir never synced to cloud.
3. Vault secret is only loaded for funding; sweeps target `VAULT_PUBKEY` and need no vault secret.
4. Alert channel is out-of-band from the trading session (separate Telegram identity).
5. `cap ≤ MAX_SESSION_CAP` enforced before any transfer.
6. Auto-sweep requires explicit `--armed`; default is alert-only.

---

## 8. Build sequence (hand to Claude Code in order)
1. `config.py` + `rpc.py` (+ tests) — foundation.
2. `keystore.py` — roundtrip + perms tests must pass before touching funds.
3. `funder.py` + `sweeper.py` — validate on **devnet** end to end.
4. `store.py` — schema + DAO.
5. `scoring.py` — build against the recorded drain fixtures first (TDD; the 5YEQ churn is your golden CRITICAL case, a clean trading day is your golden OK case).
6. `monitor.py` — wire WS + polling backfill; synthetic stream test.
7. `alerter.py` — Telegram/Pushover.
8. `cli.py` — assemble; manual devnet dry-run of full lifecycle.
9. Only then point at mainnet with a tiny cap (e.g. 0.05 SOL) for a live shakeout.

## 9. Explicitly deferred (v2+)
- Auto token liquidation on sweep (needs careful slippage/route logic before it's trustworthy).
- Per-trade rotation (prefer loss-threshold / time-window rotation over per-fill — Solana rent/ATA tax).
- Multi-wallet dashboard UI; historical PnL attribution.
- Hardware-signer path for the vault (Ledger) in place of env-var secret.

---

**One line to revisit as it grows:** the scoring baseline is the whole game. Start rule-based and session-scoped; graduate to a learned model of *your* trading only once you have enough labeled sessions to beat the handwritten rules. Until then, hand-tuned rules + LLM explanation is both safer and more legible than a black box deciding when to move your money.

---

## 10. Distribution (this ships to other people)

When strangers run this, every design choice that was "fine for me" becomes a promise to users whose funds are at stake. The whole thing hangs on one invariant:

### 10.1 Non-custodial is the load-bearing wall
The tool must **never transmit, upload, phone home, or centrally store any private key, seed, or passphrase — ever, under any code path.** Each user runs it locally, generates keys locally, funds from their own vault, holds their own keys. There is no operator-controlled server anywhere in the fund path. This single property is:
- **The security story:** you never become a honeypot. You can't lose what you never held.
- **The liability shield:** a bug can't drain a central store that doesn't exist.
- **The regulatory posture** (see 10.5).

Make it *auditable*, not just true: no telemetry that could carry key material, deterministic offline key generation, and a documented data-egress list so a user (or reviewer) can verify what leaves the machine.

### 10.2 The LLM-scoring egress boundary (sharpest architectural risk)
If scoring's LLM pass runs through a hosted backend (e.g. Cloudflare Worker + Claude API, ZERØ-style), that backend must receive **only public data** — pubkeys, signatures, parsed instructions — and **never** secrets. Enforce it structurally: the scoring payload is built from on-chain public fields only, in a module that has no access to the keystore. Add a test that fails if any key material can reach the network layer. This is the one place where "helpful cloud feature" could silently break the non-custodial guarantee.

### 10.3 Key-handling UX for non-experts
- `VAULT_SECRET` in an env var is fine for you; it's dangerous guidance for strangers (shell history, committed `.env`). For distribution, make **hardware-signer support (Ledger) a first-class path** for the vault, and treat raw-secret env vars as the discouraged fallback with loud warnings.
- Ship `.gitignore` + `.env.example`, refuse to run if the keystore dir looks cloud-synced, and make the passphrase flow robust (confirm on create, no echo, no logging).

### 10.4 Liability surface
- **Default alert-only.** Shipping `--armed` auto-sweep on by default to strangers invites sweeps to wrong vaults and false-positive liquidations mid-trade. Arming must be a deliberate, well-warned opt-in.
- **Frame honestly:** this is a safety *aid*, not a guarantee. It will have false negatives. Say so prominently. Any implication of "you're now safe" is both false and a liability.
- **License:** permissive OSS (MIT/Apache-2.0) — both carry an "AS IS," no-warranty disclaimer. Add a plain-language NOTICE: non-custodial, you hold your keys, you're responsible for your funds, no guarantee against loss.

### 10.5 Regulatory line (not legal advice — get crypto counsel before shipping)
A locally-run, non-custodial, open-source tool where **the user controls their own keys** is categorically different from a mixer or a hosted service, and longstanding FinCEN guidance distinguishes non-custodial software developers from money transmitters. That distinction is your safe harbor — but it's only intact while you stay non-custodial. The lines you must not cross without counsel:
- custodying or ever touching users' keys,
- hosting any service that sits in the fund path,
- routing fees through wallets you control inside a user's transaction flow.

Keep fees (if any) out-of-band — a license, a subscription to the scoring backend, a donation address — never a cut skimmed from swept funds. Before public release, have a crypto-literate lawyer review the architecture and the disclaimer. This is cheap insurance relative to the downside.

### 10.6 Supply-chain integrity (the tool that prevents drains must not become one)
A key-generating, fund-moving tool is a high-value target: a poisoned release *is* a drainer. Therefore:
- **Signed releases + published checksums;** reproducible builds so users can verify the artifact matches source.
- Pin and vet dependencies (a compromised `solders`/crypto dep is game over). Lockfile + hash verification.
- If distributed via pip/GitHub Releases, protect the publishing pipeline like it holds funds — because effectively it does.

### 10.7 Pre-distribution gate
Before mainnet-for-others: external security review of the key-handling and fund-moving paths, a public non-custodial data-egress statement, signed reproducible builds, and counsel sign-off on the disclaimer. Personal mainnet use (small cap) can precede this; **stranger** mainnet use should not.
