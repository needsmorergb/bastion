# Architecture Research

**Domain:** Non-custodial local CLI + long-running monitor for blast-radius-contained crypto trading (Solana session-wallet isolation, behavioral anomaly detection)
**Researched:** 2026-07-06
**Confidence:** HIGH for spec-derived structure (project's own architecture doc is unusually mature); MEDIUM for general patterns cross-checked against public sources (WAL concurrency, WS reconnection, Solana signature pagination, import-boundary linting)

## Standard Architecture

Systems in this space are a hybrid of three well-understood shapes, combined:

1. **A wallet/keystore CLI** (like `solana-keygen`, hardware-wallet CLIs) — local secret material, encrypted at rest, narrow signing surface.
2. **A blockchain indexer/watcher** (like a mini version of what Helius/QuickNode/The Graph do internally) — WS-primary, poll-fallback, cursor-based backfill, idempotent ingestion.
3. **A fraud/anomaly-detection pipeline** (rules engine + optional ML/LLM enrichment) — deterministic gate first, probabilistic/explanatory layer second, never the reverse.

The distinguishing architectural problem for Bastion is that **all three shapes share one process boundary** (single user, single machine, no server) but must not share one **trust boundary**. The key management surface (shape 1) and the detection/scoring surface (shape 3) must be as separated as if they ran on different machines, even though they run in the same Python process tree. That separation — not the monitor's reliability engineering — is the architecturally novel part of this project; the WS+poll pattern and WAL-SQLite pattern below are well-trodden and mostly a matter of not skipping steps.

### System Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│  PROCESS: bastion CLI (short-lived, one-shot commands)                    │
│  start | end | list | status | monitor (spawns/attaches long-lived proc) │
└───────────────────────────────┬──────────────────────────────────────────┘
                                 │ writes session rows, triggers fund/sweep
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  TRUST ZONE A — SECRET-BEARING (keystore, funder, sweeper)                │
│  ┌───────────┐   ┌────────────────┐   ┌─────────────────┐                │
│  │ keystore  │──▶│ funder          │   │ sweeper          │              │
│  │ (secrets) │   │ vault→session   │   │ session→vault    │              │
│  │           │   │ needs vault key │   │ needs session key│              │
│  │           │   │                │    │ only (VAULT_     │              │
│  │           │   │                │    │ PUBKEY, no vault │              │
│  │           │   │                │    │ secret)          │              │
│  └───────────┘   └────────┬───────┘    └────────┬─────────┘             │
└───────────────────────────┼─────────────────────┼───────────────────────┘
              signed tx bytes only (never a Keypair, never raw secret)
                            ▼                     ▼
              ┌────────────────────────────────────────────┐
              │  rpc client (JSON-RPC + WS) — Helius        │
              └───────────────┬──────────────────────────────┘
                              │ public data only from here down
                              ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  PROCESS: bastion monitor (long-lived daemon, single writer)              │
│  ┌───────────┐  ┌────────────┐  ┌───────────────────────────────────┐   │
│  │ WS ingest │  │ poll        │  │ store (SQLite, WAL)               │   │
│  │(logs/acct │─▶│ backfill    │─▶│ sessions/transactions/alerts/     │   │
│  │ subscribe)│  │(cursor since│  │ baselines + last-seen cursor      │   │
│  └───────────┘  │ last-seen)  │  └───────────────┬───────────────────┘   │
│         ▲       └────────────┘                   ▼                       │
│         │ reconcile on restart          ┌──────────────────────┐         │
│         └──────────────────────────────▶│  TRUST ZONE B —      │         │
│                                          │  SCORING (no keystore│         │
│                                          │  import, ever)       │         │
│                                          │  rules.py (pure,     │         │
│                                          │  in-process, no      │         │
│                                          │  network) →          │         │
│                                          │  payload.py (public- │         │
│                                          │  fields-only DTO) →  │         │
│                                          │  llm.py (egress,     │         │
│                                          │  optional, HTTP out) │         │
│                                          └──────────┬───────────┘         │
│                                                     ▼                     │
│                                    verdict (OK/WATCH/CRITICAL)            │
│                              ┌──────────────┴───────────────┐            │
│                              ▼                              ▼            │
│                     ┌────────────────┐          ┌────────────────────┐  │
│                     │ alerter        │          │ sweeper (if armed)  │  │
│                     │ (Telegram/     │          │ — re-enters Zone A  │  │
│                     │ Pushover, sep. │          │   via session key   │  │
│                     │ identity)      │          │   only              │  │
│                     └────────────────┘          └────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
```

The one arrow that must never exist in this diagram, and must be tested for its absence, is **Zone B → Zone A** (scoring/LLM code importing or receiving anything from keystore). The arrow from Zone A into the monitor is one-directional and narrow: signed transaction bytes and public keys only, never a `Keypair` object, never raw secret bytes, never the passphrase.

### Component Responsibilities

| Component | Responsibility | Typical Implementation |
|-----------|----------------|------------------------|
| `keystore` | Generate/encrypt/decrypt/retire keypairs; sole owner of secret bytes and the passphrase path | scrypt→Fernet, 0600 files, split into `vault` (secret-loading) and `session` (per-session) sub-surfaces so callers can depend on one without the other |
| `funder` | Vault→session capped transfer | Only module allowed to call `keystore.load_vault()`; enforces `cap ≤ MAX_SESSION_CAP` before ever touching the RPC client |
| `sweeper` | Session→vault transfer | Only needs the session's own keypair (already decrypted for the session) + `VAULT_PUBKEY` (public, no secret); must be structurally incapable of loading the vault secret |
| `rpc` (client + ws) | JSON-RPC + WebSocket transport, retry/backoff, reconnect | Stateless w.r.t. domain logic; takes pubkeys/signed-tx-bytes in, returns public chain data out; the one shared dependency of both trust zones, but it never sees which zone called it |
| `store` | Durable state: sessions, transactions, alerts, baselines, last-seen cursor | SQLite WAL, single logical writer (the monitor loop) for the high-frequency tables, thin DAO, migrations |
| `scoring.rules` | Deterministic ruleset scoring a parsed tx against session baseline | Pure functions — no I/O, no network, no keystore import; the regression-tested core (5YEQ churn fixture) |
| `scoring.payload` | Builds the *only* object that may leave the machine for LLM enrichment | A frozen DTO type whose fields are enumerable and 100% public on-chain data; the structural firewall lives here |
| `scoring.llm` | Optional network call to explain/confirm a rule-flagged tx | Only module allowed to do outbound HTTP for scoring; accepts only `payload.PublicTxView`, never a raw parsed tx or session object |
| `monitor` | Orchestration loop: WS ingest, poll backfill, reconcile-on-restart, dispatch to scoring/alert/sweep | asyncio event loop; single process; owns the write-cursor |
| `alerter` | Out-of-band push (Telegram/Pushover) | Separate credentials/identity from the trading terminal's channel |
| `audit` | Append-only JSONL log of every fund/sweep/alert/decision | Every mutating module writes through this one function; never mutated, only appended |
| `cli` | Human-facing entrypoint | Typer/Click; thin — delegates to the above, does not embed business logic |

## Recommended Project Structure

```
bastion/
├── pyproject.toml
├── .env.example
├── .gitignore                      # keystore dir, .env, *.db, *.db-wal/-shm
├── .importlinter                   # structural contract: scoring ⇏ keystore
├── src/
│   └── bastion/
│       ├── __init__.py
│       ├── config.py                # env loading + safety rails (MAX_SESSION_CAP, thresholds, FEE_RESERVE)
│       ├── rpc/
│       │   ├── client.py            # JSON-RPC http, retry/backoff, get_signatures(before/until), send_raw
│       │   └── ws.py                # logsSubscribe/accountSubscribe, heartbeat, reconnect
│       ├── keystore/
│       │   ├── vault.py             # load_vault() ONLY — imported by funder, nowhere else
│       │   ├── session.py           # generate/save/load/retire session keypairs
│       │   └── crypto.py            # scrypt→Fernet primitives, no domain knowledge
│       ├── funder.py                 # imports keystore.vault + keystore.session + rpc + audit
│       ├── sweeper.py                 # imports keystore.session ONLY (never keystore.vault) + rpc + audit
│       ├── store/
│       │   ├── db.py                # connection factory: WAL, busy_timeout, synchronous=NORMAL
│       │   ├── schema.sql
│       │   └── dao.py               # sessions/transactions/alerts/baselines/cursors CRUD, idempotent inserts
│       ├── scoring/
│       │   ├── rules.py             # pure, deterministic, no imports outside stdlib + dataclasses
│       │   ├── baseline.py          # per-session rolling profile from store data
│       │   ├── payload.py           # PublicTxView DTO + build_payload(); the firewall
│       │   └── llm.py               # optional egress; imports ONLY payload.PublicTxView, requests/httpx
│       ├── monitor.py                # ws ingest + poll backfill + reconcile + dispatch; single writer loop
│       ├── alerter/
│       │   ├── telegram.py
│       │   └── pushover.py
│       ├── audit.py                  # append-only JSONL writer
│       └── cli.py                    # Typer entrypoint: start/end/list/status/monitor
└── tests/
    ├── unit/                         # per-module, mocked RPC/store
    ├── fixtures/                     # recorded 5YEQ churn (golden CRITICAL), clean day (golden OK)
    ├── devnet/                       # fund→sweep round trip against real devnet RPC
    └── boundary/                     # egress/import boundary tests — see Pattern 3 below
```

### Structure Rationale

- **`keystore/` split into `vault.py` / `session.py`:** this is not cosmetic. It makes "sweeper never sees the vault secret" a fact about the import graph, not a convention. `sweeper.py` physically cannot import a function it never imports; a reviewer (or `import-linter`) can verify this in one line without reading sweeper's logic.
- **`scoring/payload.py` as its own file, not a function inside `scoring/llm.py`:** the DTO construction is the choke point. Every field on `PublicTxView` is auditable in one place; `llm.py` never touches a raw parsed-transaction dict, only the DTO, so even a bug in `llm.py` can't smuggle an extra field across — it can only omit/misuse fields the DTO already exposes.
- **`rpc/` has no domain knowledge:** it's the one module both trust zones legitimately share, so it must be domain-blind — pubkeys and bytes in, JSON out. This keeps the shared dependency from becoming a covert channel between zones.
- **`tests/boundary/` as a distinct suite:** separates "does the code work" tests from "does the trust boundary hold" tests, so the latter can be run as a required, always-green CI gate independent of feature work, and so a reviewer/auditor knows exactly where to look for the non-custodial proof.
- **`monitor.py` is one file/loop, not a class hierarchy:** at this scale (one user, a handful of concurrent sessions), an orchestration loop with clear ingest→store→score→dispatch stages is more auditable than a framework. Resist the urge to build a generic "event bus" — the fixed pipeline is the point.

## Architectural Patterns

### Pattern 1: Unified idempotent ingestion (WS and poll-backfill share one code path)

**What:** Both the WebSocket push callback and the polling-backfill loop call the exact same `ingest_signature(session, sig)` function. That function does: dedupe-check (SQLite `INSERT OR IGNORE` / `ON CONFLICT (sig) DO NOTHING` keyed on the transaction signature) → fetch full tx if new → parse → score → advance cursor → dispatch verdict, all inside one DB transaction per signature.

**When to use:** Any "real-time push + polling fallback" system where the two sources can both observe the same event (this is exactly the Solana case: a signature seen via `logsSubscribe` may also show up in a `getSignaturesForAddress` backfill sweep after a reconnect).

**Trade-offs:** Slightly more indirection than writing two separate handlers, but eliminates an entire class of bugs (scoring twice, alerting twice, drift between what WS-path does vs poll-path does). This is the single highest-leverage design decision in the monitor — do not special-case WS vs. poll beyond "who calls `ingest_signature`."

**Example:**
```python
def ingest_signature(session: Session, sig: str) -> None:
    with store.transaction() as tx:
        if tx.dao.tx_seen(sig):          # PK on `sig`, idempotent no-op
            return
        parsed = rpc.get_transaction(sig)
        verdict = scoring.rules.score(parsed, baseline=tx.dao.get_baseline(session.pubkey))
        tx.dao.insert_transaction(sig, parsed, verdict)
        tx.dao.advance_cursor(session.pubkey, sig)   # cursor moves only on committed write
    dispatch(session, verdict)   # alert / optional sweep — outside the DB transaction
```

### Pattern 2: Cursor-based reconcile-on-restart (Solana `until` pagination)

**What:** Persist a `last_seen_signature` (and `last_seen_slot`/timestamp as a secondary check) per session in the `sessions` (or a `cursors`) table, updated only as part of the same transaction that records a scored tx. On monitor start, and on every WS reconnect, call `getSignaturesForAddress(pubkey, until=last_seen_signature, limit=1000)`, paginating backwards with `before=<oldest signature returned>` if the gap exceeds 1000 signatures, and replay every returned signature through `ingest_signature` (which no-ops anything already stored) before resubscribing to the WS stream. Only after the backfill sweep completes does the monitor treat the session as "live."

**When to use:** Any monitor where "we might have missed something while disconnected" is the primary risk (true here — the spec explicitly frames missed events, not throughput, as the reliability risk). This is the standard blockchain-indexer reconciliation shape: WS/webhook for low latency, periodic/on-reconnect authoritative re-pull for correctness, with a persisted cursor bounding the re-pull.

**Trade-offs:** Costs one or more extra RPC calls per reconnect (Helius free-tier budget must account for this) but is the only way to make the "never miss an event" reliability requirement provable rather than hoped-for. Do **not** rely on WS `logsSubscribe` alone — it can drop silently (no close frame) under load or provider-side hiccups, so also run backfill on a fixed low-frequency timer (e.g., every 30–60s) even while the WS looks healthy, not only on detected disconnects.

**Example:**
```python
async def reconcile(session: Session) -> None:
    cursor = store.dao.get_cursor(session.pubkey)
    sigs = []
    before = None
    while True:
        batch = rpc.get_signatures(session.pubkey, until=cursor, before=before, limit=1000)
        if not batch:
            break
        sigs.extend(batch)
        before = batch[-1].signature
        if len(batch) < 1000:
            break
    for sig in reversed(sigs):          # oldest-first, preserves ordering for baseline math
        ingest_signature(session, sig.signature)
```

### Pattern 3: Structural egress boundary (scoring ⇏ keystore), enforced two ways

**What:** The non-custodial guarantee for the LLM-scoring path is treated as an architectural contract, not a code review checklist item. Enforce it at two independent layers so either one catches a regression:

1. **Import-graph layer (static, cheap, runs every commit):** an `import-linter` (or equivalent) `.importlinter` contract declaring `bastion.scoring` (and specifically `bastion.scoring.llm`, `bastion.scoring.payload`) **forbidden** from importing `bastion.keystore` (any submodule) or `bastion.funder`/`bastion.sweeper`. This fails CI the moment anyone adds `from bastion.keystore import ...` inside scoring, before the code even runs.
2. **Type/runtime layer (dynamic, catches what static analysis can't):** `scoring.llm`'s only public function signature accepts a single, frozen `PublicTxView` dataclass whose fields are enumerable and each individually reviewable as public on-chain data (signature, slot, parsed instruction summary, mint, lamport deltas, counterparty pubkeys). It is a type error to pass a `Keypair`, raw secret bytes, or a full session object — the function simply has no parameter that accepts them. A `tests/boundary/test_egress.py` test then: (a) asserts the import-linter contract passes, (b) constructs a payload from a recorded transaction fixture, serializes it to JSON, and asserts the serialization contains none of: a base58 secret-key pattern, a byte-array of secret-key length, or any field name overlapping keystore's internal vocabulary (`ciphertext`, `salt`, `passphrase`), and (c) monkeypatches the HTTP transport used by `scoring.llm` and asserts, across a full simulated monitor run with a real decrypted session key resident in-process, that the transport is never invoked with anything other than a `PublicTxView`-shaped JSON body.

**When to use:** Any place a "helpful cloud feature" sits downstream of secret-bearing state in the same process — this is the general pattern for LLM/analytics enrichment bolted onto a system that also holds credentials (also applies to e.g. sending logs to a SaaS APM, error-reporting SDKs, etc.). The general principle: make the disallowed data flow a *type error* and an *import-graph error*, not just a documented rule, because documented rules erode under future edits by people (or agents) who didn't read the doc.

**Trade-offs:** Slightly more ceremony (an extra DTO layer, an extra lint config) for a single-maintainer project — worth it specifically because this project's entire value proposition and legal posture rests on the non-custodial claim being true, and because it's exactly the kind of boundary that's cheap to preserve at design time and expensive to retrofit after a "just pass the whole parsed tx object, it's easier" shortcut ships.

**Example:**
```python
# scoring/payload.py
@dataclass(frozen=True)
class PublicTxView:
    signature: str
    slot: int
    block_time: int
    sol_delta: float
    counterparties: tuple[str, ...]   # pubkeys only
    instruction_summary: str          # human-readable, no raw account data blobs

def build_payload(parsed_tx: dict) -> PublicTxView:
    ...  # pulls only public fields; never touches session/keystore objects

# .importlinter
# [importlinter:contract:no-keystore-in-scoring]
# name = Scoring must never import keystore
# type = forbidden
# source_modules = bastion.scoring
# forbidden_modules = bastion.keystore, bastion.funder, bastion.sweeper
```

### Pattern 4: Vault/sweep asymmetry as a signing-model consequence, not a policy

**What:** Funding (vault→session) requires the vault's private key, because the sender must sign. Sweeping (session→vault) requires only the session's own already-decrypted key plus the vault's **public** key (`VAULT_PUBKEY`) as the recipient — the vault never signs anything to receive funds. This means "sweeper doesn't need the vault secret" isn't a rule you have to remember to follow; it's a fact about how Solana transfers work, and the architecture should make that the *only* path available: `keystore.vault.load_vault()` exists in exactly one file, imported from exactly one call site (`funder.py`), so `sweeper.py` and `monitor.py` are structurally incapable of touching the vault secret even if someone tries.

**When to use:** Any custody-adjacent system with a "hot wallet funds from cold/vault, hot wallet can return to vault" shape — the return path should always be modeled as "recipient is a public key," never "recipient's secret is loaded to authorize receipt," because that's both how the chain works and the minimal-privilege design.

**Trade-offs:** None — this is free correctness once the keystore module is split by function rather than exposing one `load(role)` function that both funder and sweeper could call with different arguments (which would make the separation a runtime `if` instead of an import-graph fact).

## Data Flow

### Session Lifecycle (the primary flow)

```
CLI: bastion start --fund 0.5 [--armed]
    ↓
keystore.session.generate() → fresh Keypair (secret held in-process only)
    ↓
keystore.session.save(kp, passphrase) → encrypted file, 0600, secret leaves process only as ciphertext to disk
    ↓
funder.fund(vault_kp, session_pubkey, cap)     [Zone A: needs keystore.vault]
    - guard: refuse if cap > MAX_SESSION_CAP
    - rpc.send_raw(signed transfer) → confirm
    - audit.log("fund", ...)
    ↓
store.dao.create_session({pubkey, cap, armed, started_at, baseline=seed})
    ↓
CLI prints session pubkey → user points trading terminal/bot at it
    ↓
════════════════════════════ (monitor process, separately started) ════════════════
monitor.py: on startup, poll `sessions where status='active'` → reconcile() each (Pattern 2)
    → subscribe WS logs/account for each active session pubkey
    ↓
[live trading happens against the session wallet]
    ↓
WS push OR poll-backfill tick → ingest_signature(session, sig)   [Pattern 1, idempotent]
    ↓
scoring.rules.score(parsed_tx, baseline) → Verdict{level, reasons[]}   [Zone B, no keystore]
    ↓ (only if rule-flagged/ambiguous)
scoring.payload.build_payload(parsed_tx) → PublicTxView
    ↓
scoring.llm.explain(payload) → human-readable confirm/deny sentence   [egress boundary — Pattern 3]
    ↓
store.dao.insert_transaction(sig, parsed, verdict); store.dao.insert_alert(...)
    ↓
verdict == OK → loop continues, baseline updates
verdict >= WATCH → alerter.push(telegram/pushover, out-of-band identity)
verdict >= CRITICAL and session.armed → sweeper.sweep(session_kp, VAULT_PUBKEY)  [re-enters Zone A, session key only]
    → audit.log("sweep", ...) → store.dao.mark_session("burned")
    → alerter.push("swept" state)
    ↓
════════════════════════════════════════════════════════════════════════════════
CLI: bastion end --wallet <pubkey> [--retire]
    ↓
sweeper.sweep(session_kp, VAULT_PUBKEY)   [manual end, same code path as auto-sweep]
    ↓
keystore.session.retire(pubkey) → keystore file removed/zeroed
    ↓
store.dao.close_session(pubkey, ended_at)
```

### Key Data Flows

1. **Secret flow (Zone A only):** vault secret → funder (signs once) → never persisted beyond the signed tx; session secret → keystore file (encrypted) → decrypted in-process only for the duration of a sign operation (fund confirmation, sweep) → re-encrypted/discarded. Neither secret ever crosses into `store`, `scoring`, or `alerter`.
2. **Public chain-data flow (shared spine):** `rpc` → `monitor` → `store` (persisted) → `scoring.rules` (in-process) → optionally `scoring.payload`/`scoring.llm` (leaves the machine, public fields only) → back into `store`/`alerter`. This is the one flow both zones legitimately touch, via the domain-blind `rpc` module.
3. **Control flow (CLI ↔ monitor):** the CLI's `start`/`end` write session rows; the monitor discovers new/ended sessions by polling the `sessions` table on a short interval rather than requiring IPC — reuses the same "poll as safety net" idea already needed for the chain-watching side, and avoids adding a socket/RPC surface between two local processes.

## Scaling Considerations

This system's scale axis is not "more users" — it's single-user, single-machine by design (explicitly out of scope: multi-wallet dashboard, hosted service). The realistic scale axis is **concurrent sessions per user** and **RPC call budget on a free tier**.

| Scale | Architecture Adjustments |
|-------|--------------------------|
| 1 session, personal devnet/small-mainnet use | Exactly the design above: one monitor process, WAL SQLite, WS + low-frequency poll backfill. No changes needed. |
| A handful of concurrent sessions (a few bots/terminals) | Poll-backfill and reconcile-on-restart calls must be budgeted against Helius free-tier rate limits — stagger reconciliation across sessions (don't reconcile all sessions in the same tick), and keep the idle-health-check poll interval config-driven so it can be relaxed as session count grows. |
| Distribution to strangers (many independent single-user installs) | Each install is still single-user/single-machine — this scales by replication, not by making one instance bigger. The only shared-infra risk is the *publishing pipeline* (Section 10.6 of the spec), not the runtime architecture. |

### Scaling Priorities

1. **First bottleneck: RPC rate limit (Helius free tier), not the database or CPU.** SQLite WAL and a rules-based scorer are trivially fast at this event volume; the binding constraint is API quota consumed by backfill polling × session count. Mitigate by making poll cadence adaptive (less frequent when WS has been healthy, more frequent right after a reconnect) and by only calling `scoring.llm` on rule-flagged transactions, never on every tx.
2. **Second bottleneck: LLM latency/cost on the explain path.** Keep the LLM pass off the critical alerting path where possible — the deterministic verdict (and, if armed, the sweep) should not block on the LLM call; the LLM enriches the alert message asynchronously or with a short timeout, never gates the sweep decision (matches the spec's explicit "LLM never the sole gate").

## Anti-Patterns

### Anti-Pattern 1: One generic `parsed_tx` dict passed everywhere, including into the LLM call

**What people do:** Reuse the same rich dict (full parsed instructions, account keys, sometimes even the signer's `Keypair` reference for convenience) across rules, storage, and the LLM explain call, because it's less code today.

**Why it's wrong:** This is exactly how a keystore-to-network leak happens later — not through a "malicious" change, but through an innocuous refactor six months from now that threads one more field through the shared object, and that object happens to also flow into `scoring.llm`. The egress boundary can only be enforced structurally if the LLM path's input type is narrower than the internal working type.

**Do this instead:** Always construct a narrow `PublicTxView` DTO (Pattern 3) as the sole input to anything that makes a network call, even though it duplicates a few fields already present in the internal parsed-tx representation.

### Anti-Pattern 2: Two separate ingestion code paths for "live WS event" vs. "backfilled event"

**What people do:** Write a WS callback that scores+alerts inline, and a separate backfill function that does its own (slightly different) fetch/score/alert logic, because they were written at different times or by different people/agents.

**Why it's wrong:** Guarantees drift — the two paths will diverge on dedupe logic, baseline updates, or alert formatting, and the divergence is exactly where "missed events" bugs hide (e.g., backfill path forgets to update the baseline, so post-reconnect scoring silently uses a stale baseline).

**Do this instead:** One `ingest_signature()` function (Pattern 1), called from both places. If WS and poll disagree about whether a signature is "new," the dedupe check inside `ingest_signature` is the single source of truth.

### Anti-Pattern 3: SQLite writes scattered across the CLI and the monitor without WAL/busy_timeout configured

**What people do:** Open a bare `sqlite3.connect(path)` in whichever module happens to need it, without setting `journal_mode=WAL`, `busy_timeout`, or `synchronous=NORMAL`, and without funneling all connections through one factory.

**Why it's wrong:** Without WAL, a long-lived monitor process holding a read/write connection can block short-lived CLI writes (or vice versa) with `database is locked` errors — surprising given the light actual write volume, purely because of default SQLite locking behavior. WAL mode also silently misbehaves (or falls back) on network-mounted/cloud-synced volumes, which is doubly relevant here since a cloud-synced keystore dir is already an explicit anti-pattern for this project.

**Do this instead:** One `store/db.py` connection factory that always sets `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA busy_timeout=5000;` immediately after opening, used by every module that touches the DB (CLI included). Keep write transactions short (one signature's worth of work, not a whole backfill batch in one transaction). Treat the monitor as the logical single writer for the high-frequency `transactions`/`alerts` tables; the CLI's low-frequency `sessions` row writes are safe under WAL's automatic writer serialization as long as they're short.

### Anti-Pattern 4: Loading the vault secret anywhere "just in case"

**What people do:** Add a general-purpose `keystore.load(role: str)` that both funder and sweeper call with `role="vault"` or `role="session"`, for symmetry.

**Why it's wrong:** Turns a structural guarantee into a runtime `if` — any future call site can pass `role="vault"` by mistake or by a bad merge, and nothing in the import graph would catch it.

**Do this instead:** Split into `keystore.vault.load_vault()` and `keystore.session.load_session()` as physically separate functions/files (Pattern 4); only `funder.py` imports the former.

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Helius RPC + WS (free tier) | `rpc/client.py` (HTTP JSON-RPC, retry/backoff on 429) + `rpc/ws.py` (persistent WS, reconnect/heartbeat) | Free tier rate limits are the real capacity constraint (see Scaling); `getSignaturesForAddress` supports `before`/`until` pagination and is capped at ~1000 results per call — multi-page backfill must loop with `before` until exhausted |
| Telegram Bot API / Pushover | `alerter/telegram.py`, `alerter/pushover.py` | Must use a bot/identity separate from anything the trading terminal/bot could reach, so a session compromise can't suppress alerts (explicit spec invariant) |
| Optional hosted LLM-scoring backend | `scoring/llm.py` → HTTP POST of `PublicTxView` JSON only | Treat exactly like any third-party SaaS integration handling sensitive-adjacent data: narrow DTO in, no ambient credentials in scope, timeout short enough to never gate the sweep decision |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| `keystore.vault` ↔ `funder` | Direct function call, in-process | Only call site; no other module imports `keystore.vault` |
| `keystore.session` ↔ `sweeper`/`monitor` | Direct function call, in-process | Session key decrypted only for the duration of a sign operation |
| `rpc` ↔ everything else | Direct function call, in-process | Domain-blind; pubkeys/bytes/signed-tx in, public JSON out; shared by both trust zones without becoming a covert channel |
| `scoring.rules`/`scoring.payload`/`scoring.llm` ↔ `keystore`/`funder`/`sweeper` | **None — forbidden**, enforced by import-linter contract + DTO type boundary + runtime test (Pattern 3) | The one boundary this entire architecture exists to protect |
| `cli` ↔ `monitor` (separate processes) | Shared SQLite `sessions` table, polled by monitor | Avoids adding an IPC/socket surface; reuses the "poll as safety net" idea already required for chain-watching |

## Build Order Implications

The spec's own build sequence (§8) is sound and should be followed as-is; the research above sharpens the *why* behind the ordering and flags two additions.

1. **`config.py` + `rpc.py` first.** Nothing else can be meaningfully tested without a mockable RPC boundary; this is the shared dependency of both trust zones, so it must exist and be stable before either zone is built.
2. **`keystore` (vault/session split) second, before any fund-moving code.** Roundtrip + file-permission tests must pass before anything touches real value. Build the vault/session split now, not later — retrofitting the split after `funder`/`sweeper` already exist against a monolithic `keystore.load()` is exactly the kind of refactor that risks Anti-Pattern 4.
3. **`funder` + `sweeper`, validated on devnet end-to-end, third.** This is the correct gating point: devnet-before-mainnet isn't just caution, it's the only way to test the vault/session asymmetry (Pattern 4) against a real chain's signing model before any mainnet SOL is at risk.
4. **`store` (schema + DAO, WAL configured from the start) fourth.** Cursor persistence and idempotent-insert semantics (Pattern 1/2) are schema-level decisions (a `UNIQUE`/PK constraint on `transactions.sig`, a cursor column) — get this right before `monitor` is built on top of it, since retrofitting idempotency into an already-written ingestion loop is more error-prone than designing it in.
5. **`scoring` fifth, TDD against the golden fixtures — and build the egress boundary (Pattern 3) as part of this phase, not deferred to "later hardening."** This is the one addition to the spec's sequence worth calling out explicitly: because `scoring.llm` is the sharpest architectural risk in the whole project, the import-linter contract and the boundary test suite should be written in the *same phase* that introduces `scoring.payload`/`scoring.llm` — not bolted on afterward once the module already has organic dependencies. Standing the fence up before the field exists is materially cheaper than fencing an already-grazing field.
6. **`monitor` sixth**, wiring WS + poll backfill + reconcile (Patterns 1–2) on top of a `store` that already has cursor support and a `scoring` that already has its boundary enforced. Test against a synthetic signature stream, including an injected "gap" to prove reconcile-on-restart actually replays it.
7. **`alerter` seventh** — depends on `monitor` producing verdicts to format, and should be built with the out-of-band-identity invariant as a literal config validation (refuse to start if the alert channel's identity matches any configured trading-session identity, if that's ever detectable).
8. **`cli` eighth**, assembling everything — by this point every module underneath is independently proven, so the CLI is mostly wiring plus the `sessions`-table polling contract with `monitor` (Internal Boundaries table).
9. **Mainnet, tiny cap, last** — this is not a build-order step so much as a gating criterion: nothing in steps 1–8 should require mainnet to validate. Devnet exercises the full lifecycle (fund → monitor → score → alert → sweep) end-to-end; mainnet-with-a-tiny-cap is a live shakeout of the same already-tested paths, not a new code path.

## Sources

- [Import Linter documentation](https://import-linter.readthedocs.io/) — forbidden/layered contract types for enforcing module import boundaries in Python (MEDIUM confidence, cross-checked across multiple independent write-ups)
- [seddonym/import-linter (GitHub)](https://github.com/seddonym/import-linter) — contract config format used in Pattern 3 example
- [Curvegrid — Blockchain Event Monitoring: Polling, WebSockets, and Webhooks](https://www.curvegrid.com/blog/2024-01-17-blockchain-event-monitoring-possibilities-with-multibaas-polling-websockets-and-webhooks) — WS-vs-poll-vs-webhook trade-off framing for chain event ingestion (MEDIUM confidence)
- [WebSocket.org — Reconnection: State Sync and Recovery Guide](https://websocket.org/guides/reconnection/) — session-vs-connection-identity pattern; at-least-once delivery + idempotency keys on reconnect (MEDIUM confidence)
- [SQLite — Write-Ahead Logging](https://sqlite.org/wal.html) — authoritative WAL semantics (HIGH confidence, primary source)
- [charlesleifer — Going Fast with SQLite and Python](https://charlesleifer.com/blog/going-fast-with-sqlite-and-python/) and [SkyPilot — Abusing SQLite to Handle Concurrency](https://blog.skypilot.co/abusing-sqlite-to-handle-concurrency/) — WAL + busy_timeout + short-transaction guidance for Python long-running processes, network-filesystem WAL caveat (MEDIUM confidence, cross-checked)
- [Helius — getSignaturesForAddress RPC reference](https://www.helius.dev/docs/api-reference/rpc/http/getsignaturesforaddress) and [Chainstack — overcoming the 1000-tx limit](https://chainstack.com/solana-how-to-getsignaturesforaddress-1000-transaction-limit/) — `before`/`until` pagination mechanics for cursor-based backfill (MEDIUM confidence, cross-checked)
- `bastion-spec.md` (this repo) — primary source for component list, data model, invariants, and the existing build sequence; treated as HIGH confidence / authoritative for project-specific decisions

---
*Architecture research for: non-custodial Solana session-wallet isolation + anomaly-detection CLI*
*Researched: 2026-07-06*
