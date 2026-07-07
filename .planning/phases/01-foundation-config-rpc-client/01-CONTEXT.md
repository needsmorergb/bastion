# Phase 1: Foundation — Config + RPC Client - Context

**Gathered:** 2026-07-06
**Status:** Ready for planning

<domain>
## Phase Boundary

Phase 1 delivers the two shared foundations every later phase depends on: (1) a **config layer** that loads all documented env vars with externalized safety rails, and (2) a **JSON-RPC + WebSocket transport** (`rpc/`) with retry/backoff, cursor pagination, and resilient WS subscription. No keys, no funds, no scoring, no persistence — those come later and import these. This is pure infrastructure; its job is to be correct, mockable, and stable.

In scope: `config.py`, `rpc/client.py` (JSON-RPC), `rpc/ws.py` (WebSocket). Out of scope: anything that touches keys, funds, storage, or scoring (Phases 2+).

</domain>

<decisions>
## Implementation Decisions

### Config source & precedence
- **D-01:** Load config from a `.env` file via `python-dotenv`, with real process env taking precedence over `.env` values (12-factor). Ship a `.env.example` documenting every variable. Rationale: distribution (DIST-02) needs `.env.example`; env-override keeps CI/secret-manager paths working.
- **D-02:** Passphrase (`KEYSTORE_PASSPHRASE`) falls back to `getpass` when unset — never required in the environment, never echoed. (Full passphrase UX lands in Phase 2; Phase 1 only wires the read + fallback.)

### RPC client shape (sync vs async)
- **D-03:** Async-first core using `httpx.AsyncClient` (JSON-RPC) and the modern `websockets` async API. Provide thin **sync wrappers** for one-shot CLI calls (e.g. `get_balance` during `start`/`status`). The long-running monitor uses the async path directly. Rationale: research STACK.md — `requests` blocks the event loop against the <5s alert target; `httpx` gives both sync and async from one dependency.
- **D-04:** `rpc/` is a package (`client.py` + `ws.py`), not a single module, so JSON-RPC and WS concerns stay separable and independently testable/mockable.

### 429 / backoff policy
- **D-05:** On HTTP 429 (and transient 5xx), retry with **bounded exponential backoff + jitter**, honoring the `Retry-After` header when present, capped at ~30s, then raise a typed error rather than hanging. `sendTransaction`-class calls get a tighter budget (Helius free tier ~1/sec). Rationale: PITFALLS.md — Helius free tier is 10 RPS / 1M credits/mo; unbounded retries or ignoring Retry-After gets the client throttled or wedged.

### WebSocket reconnect / heartbeat
- **D-06:** Detect silent WS drops with an **active heartbeat** (~20–30s ping/expected-message window), not just `onclose`/`onerror`. On drop: exponential-backoff reconnect (jittered, capped ~30s) + auto-resubscribe, and **signal "backfill needed"** to whatever consumes the client (the monitor, Phase 6). Phase 1 exposes the reconnect + backfill-signal hooks; the monitor wires them later. Rationale: PITFALLS.md #11 — silent drops are a documented Solana WS failure mode; heartbeat is required.

### Cursor pagination
- **D-07:** `get_signatures(pubkey, before=None, until=None, limit=1000)` paginates via `before`/`until` cursors and must correctly walk a >1000-signature stream without truncation (mockable). Rationale: `getSignaturesForAddress` caps at 1000/call; backfill correctness (Phase 4/6) depends on this being right from the start.

### Safety-rail defaults
- **D-08:** Safety rails (`MAX_SESSION_CAP`, `FEE_RESERVE_LAMPORTS`, scoring thresholds) are **config-driven, never hardcoded**, each overridable and asserted so by a test. Defaults are **conservative** (low `MAX_SESSION_CAP`, e.g. ~1.0 SOL ceiling). `FEE_RESERVE_LAMPORTS` is a **fallback only** — the actual sweep reserve is computed via `getFeeForMessage` at sweep time (Phase 3); the config value guards the pre-flight estimate. Rationale: PITFALLS.md #1 — a flat fee reserve strands dust; the rail is a ceiling, not the source of truth.

### Claude's Discretion
- Exact `httpx`/`websockets` minor versions to pin (lock at implementation via `uv add`).
- Internal module/function naming and test-fixture structure.
- Whether retry/backoff is a decorator vs an explicit loop — implementation detail.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Phase spec & requirements
- `bastion-spec.md` §3 (`rpc.py`, `config.py` module contracts) — the authoritative method list and behaviors for this phase
- `.planning/ROADMAP.md` (Phase 1 section) — goal, success criteria, plan breakdown (01-01 config, 01-02 rpc/client, 01-03 rpc/ws)
- `.planning/REQUIREMENTS.md` — CLI-05 (env config + getpass fallback), CLI-06 (configurable safety rails)

### Stack & pitfalls (research)
- `.planning/research/STACK.md` — httpx (not requests), modern `websockets` API, versions, packaging/`uv` lockfile
- `.planning/research/PITFALLS.md` — Helius free-tier rate limits (#14), WebSocket silent drops (#11), `getSignaturesForAddress` 1000-cap/pagination gaps (#12), fee-reserve strand (#1)
- `.planning/research/ARCHITECTURE.md` — unified ingestion + cursor-reconciliation patterns the RPC layer must enable

### Project context
- `.planning/PROJECT.md` — constraints (Helius free tier, non-custodial), Key Decisions

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- None yet — greenfield. This phase creates the first modules.

### Established Patterns
- None yet. Phase 1 SETS the patterns later phases follow: async-first transport, config-driven rails, mockable RPC boundaries.

### Integration Points
- `config.py` is imported by every later module (keystore, funder, sweeper, monitor, scoring, alerter).
- `rpc/` is imported by funder/sweeper (Phase 3) and monitor (Phase 6). Its interfaces should be stable and mock-friendly so those phases can test without a live RPC.

</code_context>

<specifics>
## Specific Ideas

- The spec's `rpc.py` method list is the contract: `rpc(method, params)`, `get_balance`, `get_latest_blockhash`, `get_signatures(pubkey, before=None, limit)`, `get_transaction(sig)`, `send_raw(signed_tx_b64)`, `ws_subscribe_logs(pubkey, callback)`. Add `get_fee_for_message` (needed by the Phase 3 sweeper reserve) per research.
- "Reuse the pattern already proven" (spec) for retry/backoff — the maintainer has a known-good JSON-RPC retry pattern; the planner should ask to see it if referenced, otherwise implement the D-05 policy.

</specifics>

<deferred>
## Deferred Ideas

- Enhanced/parsed-transaction Helius endpoints (beyond raw JSON-RPC) — evaluate during Phase 5/6 if hand-parsing raw instruction logs proves painful. Not Phase 1.
- Priority-fee / compute-budget handling on sends — belongs with the funder/sweeper (Phase 3), not the transport layer.

None of these change Phase 1 scope — discussion stayed within the config + transport boundary.

</deferred>

---

*Phase: 1-Foundation — Config + RPC Client*
*Context gathered: 2026-07-06*
