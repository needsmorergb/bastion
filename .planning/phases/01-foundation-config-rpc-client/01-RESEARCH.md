# Phase 1: Foundation — Config + RPC Client - Research

**Researched:** 2026-07-07
**Domain:** Solana JSON-RPC/WebSocket transport client + 12-factor config loading (Python, async-first)
**Confidence:** MEDIUM (no context7/ref MCP docs provider was available in this environment — all findings are `websearch`/`webfetch` against primary sources (solana.com, helius.dev, websockets.readthedocs.io, respx/python-dotenv official docs), which the confidence-classification seam floors at LOW-provider-tier; content itself is drawn from authoritative first-party docs, so treat as MEDIUM in practice, and re-verify version-sensitive claims at implementation time)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** Load config from a `.env` file via `python-dotenv`, with real process env taking precedence over `.env` values (12-factor). Ship a `.env.example` documenting every variable.
- **D-02:** Passphrase (`KEYSTORE_PASSPHRASE`) falls back to `getpass` when unset — never required in the environment, never echoed. (Full passphrase UX lands in Phase 2; Phase 1 only wires the read + fallback.)
- **D-03:** Async-first core using `httpx.AsyncClient` (JSON-RPC) and the modern `websockets` async API. Provide thin **sync wrappers** for one-shot CLI calls (e.g. `get_balance` during `start`/`status`). The long-running monitor uses the async path directly.
- **D-04:** `rpc/` is a package (`client.py` + `ws.py`), not a single module, so JSON-RPC and WS concerns stay separable and independently testable/mockable.
- **D-05:** On HTTP 429 (and transient 5xx), retry with **bounded exponential backoff + jitter**, honoring the `Retry-After` header when present, capped at ~30s, then raise a typed error rather than hanging. `sendTransaction`-class calls get a tighter budget (Helius free tier ~1/sec).
- **D-06:** Detect silent WS drops with an **active heartbeat** (~20–30s ping/expected-message window), not just `onclose`/`onerror`. On drop: exponential-backoff reconnect (jittered, capped ~30s) + auto-resubscribe, and **signal "backfill needed"** to whatever consumes the client (the monitor, Phase 6). Phase 1 exposes the reconnect + backfill-signal hooks; the monitor wires them later.
- **D-07:** `get_signatures(pubkey, before=None, until=None, limit=1000)` paginates via `before`/`until` cursors and must correctly walk a >1000-signature stream without truncation (mockable).
- **D-08:** Safety rails (`MAX_SESSION_CAP`, `FEE_RESERVE_LAMPORTS`, scoring thresholds) are **config-driven, never hardcoded**, each overridable and asserted so by a test. Defaults are **conservative** (low `MAX_SESSION_CAP`, e.g. ~1.0 SOL ceiling). `FEE_RESERVE_LAMPORTS` is a **fallback only**.

### Claude's Discretion

- Exact `httpx`/`websockets` minor versions to pin (lock at implementation via `uv add`).
- Internal module/function naming and test-fixture structure.
- Whether retry/backoff is a decorator vs an explicit loop — implementation detail.

### Deferred Ideas (OUT OF SCOPE)

- Enhanced/parsed-transaction Helius endpoints (beyond raw JSON-RPC) — evaluate during Phase 5/6 if hand-parsing raw instruction logs proves painful. Not Phase 1.
- Priority-fee / compute-budget handling on sends — belongs with the funder/sweeper (Phase 3), not the transport layer.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-------------------|
| CLI-05 | Configuration is read from env (`SOLANA_RPC`, `SOLANA_WS`, `VAULT_SECRET`, `VAULT_PUBKEY`, `KEYSTORE_DIR`, `KEYSTORE_PASSPHRASE`, `TELEGRAM_*`, `PUSHOVER_*`) with a `getpass` fallback for the passphrase | See "Config Loading" pattern below — `python-dotenv` precedence behavior confirmed (`override=False` default already gives process-env-wins), `getpass.getpass()` fallback pattern, typed `Config` dataclass |
| CLI-06 | Safety rails are configurable (`MAX_SESSION_CAP`, `FEE_RESERVE_LAMPORTS`, scoring thresholds) | See "Safety-Rail Config Pattern" below — env-var-backed dataclass fields with conservative defaults, each independently overridable and covered by an override-assertion test per D-08 |
</phase_requirements>

## Summary

This phase builds two things every later phase imports: a typed `config.py` that loads 12-factor env config (with `.env` fallback and a `getpass` passphrase prompt), and an `rpc/` package wrapping Helius's JSON-RPC + WebSocket surface with retry/backoff, cursor pagination, and heartbeat-based reconnect. Nothing here touches keys or funds — the RPC client only needs a signed base64 blob to send and a pubkey to query.

The two hardest correctness properties, both testable without a live RPC, are: (1) `get_signatures` must walk a >1000-signature stream via `before`-cursor pagination without truncating (Helius/Solana caps every `getSignaturesForAddress` call at 1000 results), and (2) the WebSocket client must detect a **silent** drop (no close frame — the NAT/idle-timeout failure mode, not just a clean `onclose`) via an active ping/pong heartbeat, not passive event listening, because `logsSubscribe` connections can go quiet without ever firing a close event.

`python-dotenv`'s `load_dotenv()` defaults to `override=False`, which is exactly the D-01 precedence requirement out of the box — no `os.environ` merge dance is needed, just `load_dotenv()` then `os.getenv(...)`. The modern `websockets.asyncio.client.connect()` API defaults `ping_interval=20s, ping_timeout=20s` and exposes reconnect-as-async-iterator with a `process_exception` hook to distinguish transient vs. fatal errors — this is the natural home for the D-06 heartbeat/reconnect/backfill-signal design. `respx` is the standard way to mock `httpx.AsyncClient` transports for the 429/backoff tests (D-05) via `side_effect=[...]` response sequences.

**Primary recommendation:** Build `rpc/client.py` as a thin `httpx.AsyncClient`-backed JSON-RPC caller with a single retry/backoff wrapper function (bounded exponential + jitter, `Retry-After`-aware, ~30s cap) that every method routes through; build `rpc/ws.py` on `websockets.asyncio.client.connect()`'s async-iterator reconnect loop, adding your own heartbeat-liveness timer on top since the library's `ping_timeout` alone only catches an unresponsive *peer*, not a route that black-holes packets before the ping is even sent (test both paths separately — see Validation Architecture).

## Architectural Responsibility Map

> Bastion is a local CLI + long-running monitor process, not a web app — there is no browser/CDN tier. Tiers are adapted to this shape: **Local Process** (in-process code, both trust zones), **External Service Client** (this phase's `rpc/` package — the boundary to Helius), **Environment** (OS env vars / `.env` file), **External Service** (Helius RPC + WS, out of process control).

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Env/config loading (`config.py`) | Local Process | Environment | Reads OS env + `.env` file at process startup; produces one immutable, typed config object consumed by every later module (keystore, funder, sweeper, monitor, scoring, alerter) |
| Passphrase acquisition | Local Process | — | `getpass.getpass()` reads directly from the controlling TTY, never from env unless `KEYSTORE_PASSPHRASE` is explicitly set — this is a Local Process concern, never touches External Service |
| Safety-rail values (`MAX_SESSION_CAP`, `FEE_RESERVE_LAMPORTS`, thresholds) | Local Process | Environment | Config-driven per D-08; enforced by *callers* (funder/sweeper/scoring in later phases), not by `rpc/` itself — this phase only guarantees they're readable, typed, and overridable |
| JSON-RPC request/response (`rpc/client.py`) | External Service Client | External Service (Helius) | Domain-blind transport: pubkeys/signed-bytes in, public JSON out (per ARCHITECTURE.md's "rpc has no domain knowledge" rule) — this phase owns the retry/backoff/pagination logic, Helius owns the actual RPC processing |
| WebSocket subscription + heartbeat (`rpc/ws.py`) | External Service Client | External Service (Helius) | Same boundary as above but stateful (persistent connection, reconnect state machine) — this phase owns liveness detection and resubscribe-after-reconnect; downstream consumers (Phase 6 monitor) own what to *do* with a "backfill needed" signal |

## Standard Stack

> Core stack already selected and version-researched in `.planning/research/STACK.md` (httpx not requests, modern `websockets` not legacy, `python-dotenv`, `pytest`+`pytest-asyncio`) — not re-derived here. This section adds the phase-specific testing dependency and confirms current versions via direct registry query in this session.

### Core (confirmed this session)

| Library | Version (verified via `pip index versions`, 2026-07-07) | Purpose | Why Standard |
|---------|-----------|---------|--------------|
| `httpx` | 0.28.1 | Async + sync JSON-RPC HTTP client | `httpx.AsyncClient` for the monitor's async path, `httpx.Client` (or a sync wrapper around the async client, see Pitfall below) for one-shot CLI calls — one dependency for both, per D-03 |
| `websockets` | 16.0 | Persistent WS transport for `logsSubscribe`/`accountSubscribe` | Modern `websockets.asyncio.client.connect()` API — auto-reconnect-as-async-iterator + `ping_interval`/`ping_timeout` heartbeat built in; `websockets.legacy` is deprecated, do not use |
| `python-dotenv` | 1.2.2 (latest); STACK.md pinned no specific version | `.env` file loading | `load_dotenv()` defaults to `override=False` — this is the D-01 precedence requirement (process env wins) with zero extra code, confirmed this session against official docs |

### Supporting (new to this phase — not yet in STACK.md)

| Library | Version (verified) | Purpose | When to Use |
|---------|---------|---------|-------------|
| `respx` | 0.23.1 | Mock `httpx` requests/transports in tests | Required to make D-05's 429/backoff test (`get response.mock(side_effect=[httpx.Response(429, headers={"Retry-After":"1"}), httpx.Response(200, ...)])`) deterministic and fast — no live RPC needed |
| `pytest` | 9.1.1 latest (9.0.2 was the version resolved in this environment's local pip cache — either is fine, pin one) | Test runner | Confirms STACK.md's unpinned "latest" — pin explicitly now that the phase needs `pytest-asyncio` interop |
| `pytest-asyncio` | 1.4.0 latest (1.3.0 resolved locally) | `async def test_...` support | Required for testing `rpc/client.py`'s async methods and `rpc/ws.py`'s reconnect loop directly, without wrapping every test in `asyncio.run()` |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Hand-rolled retry loop (D-05 discretion: loop vs decorator) | `httpx-retries`' `RetryTransport` (`Retry(total=5, backoff_factor=0.5)`) mounted on the client | A maintained transport-level retry saves boilerplate but adds a dependency for a fund-moving tool where every dependency is audit surface (STACK.md's supply-chain stance); a ~40-line hand-rolled wrapper function is simple enough to keep in-house and gives full control over the Retry-After-vs-sendTransaction-tighter-budget split D-05 requires. **Recommendation: hand-roll**, consistent with STACK.md's existing "every extra dependency is audit surface" reasoning for `rpc.py`. |
| `websockets`' own reconnect-as-async-iterator + `ping_timeout` | A fully custom reconnect/heartbeat loop wrapping `websockets.connect()` in oneshot mode | The library's async-iterator reconnect (`async for websocket in connect(uri): ...`) already handles exponential backoff on transient errors and is well-tested — reinventing it is pure risk. **Recommendation: use the library's iterator, add your own liveness timer on top for the "no ping ever sent because the route is dead" edge case (see Pitfall 2 below), don't replace the whole reconnect machinery.** |

**Installation:**
```bash
uv add httpx websockets python-dotenv
uv add --dev pytest pytest-asyncio respx
```

**Version verification performed:** `pip index versions <pkg>` run directly against PyPI in this session (2026-07-07) for all six packages above; results shown in the table. `httpx` 0.28.1 and `websockets` 16.0 match STACK.md's prior research exactly (no drift). `python-dotenv` has moved from "latest" (unpinned in STACK.md) to a confirmed 1.2.2; pin this explicitly at implementation.

## Package Legitimacy Audit

| Package | Registry | Age (first PyPI release) | Downloads | Source Repo | Verdict | Disposition |
|---------|----------|-----|-----------|-------------|---------|-------------|
| `httpx` | pypi | ~2019, 100+ historical releases | Not returned by this environment's legitimacy tool (`unknown-downloads`) | github.com/encode/httpx (matches, confirmed) | SUS* | Approved — see note |
| `websockets` | pypi | ~2013, 50+ historical releases | Not returned (`unknown-downloads`) | github.com/python-websockets/websockets (matches, confirmed) | SUS* | Approved — see note |
| `python-dotenv` | pypi | ~2014, 50+ historical releases | Not returned (`unknown-downloads`) | github.com/theskumar/python-dotenv (matches, confirmed) | SUS* | Approved — see note |
| `respx` | pypi | ~2019, 40+ historical releases | Not returned (`unknown-downloads`) | lundberg.github.io/respx (github.com/lundberg/respx, confirmed) | SUS* | Approved — see note |
| `pytest` | pypi | ~2004 lineage / 100+ historical releases on current name | Not returned (`unknown-downloads`, also flagged `too-new` — false positive, see note) | github.com/pytest-dev/pytest (matches, confirmed) | SUS* | Approved — see note |
| `pytest-asyncio` | pypi | ~2015, 60+ historical releases | Not returned (`unknown-downloads`) | github.com/pytest-dev/pytest-asyncio (matches, confirmed) | SUS* | Approved — see note |

**\*Note on the SUS verdicts:** `gsd-tools query package-legitimacy check` flagged all six as `SUS` solely on the `unknown-downloads` signal (this sandboxed environment has no live weekly-download feed wired up) — one package (`pytest`) also got a spurious `too-new` flag that is a registry-snapshot artifact, not real (pytest's first release predates this project by two decades). All six were independently cross-verified this session via `pip index versions <pkg>`, which returned 40–100+ historical version entries each and confirmed GitHub repo URLs matching the well-known official maintainers (`encode`, `python-websockets`, `theskumar`, `lundberg`, `pytest-dev` ×2). These are among the most widely-used packages in the Python ecosystem and were already independently selected in `.planning/research/STACK.md`. **Disposition: approved without a blocking `checkpoint:human-verify`** — the SUS verdict here is a tooling/environment limitation (missing download telemetry), not a legitimacy signal, and the planner should note this reasoning rather than insert a redundant human-verify step for six packages already vetted by prior project research. If the planner's risk tolerance differs, a single lightweight `checkpoint:human-verify` covering "confirm these six package names against PyPI before `uv add`" is a reasonable compromise — not one per package.

**Packages removed due to `[SLOP]` verdict:** none.
**Packages flagged as suspicious `[SUS]`:** all six, for the `unknown-downloads` reason explained above — treated as a tooling artifact, not a real risk signal, given the cross-verification performed.

## Architecture Patterns

### System Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│  Process startup (CLI one-shot OR monitor daemon)                 │
│  1. config.load() ──▶ load_dotenv() [override=False]              │
│                        ──▶ os.getenv(...) per field                │
│                        ──▶ KEYSTORE_PASSPHRASE unset?              │
│                             ──▶ getpass.getpass() fallback          │
│                        ──▶ returns frozen Config dataclass          │
└──────────────────────────────┬────────────────────────────────────┘
                                │ Config passed into rpc client ctor
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  rpc/client.py — JSON-RPC over httpx.AsyncClient                  │
│                                                                     │
│  call(method, params) ──▶ POST {SOLANA_RPC}                       │
│         │                                                          │
│         ├─ 200 ──▶ parse result, return                           │
│         ├─ 429/5xx ──▶ retry_with_backoff()                       │
│         │       │  read Retry-After header if present              │
│         │       │  else exponential(attempt) + jitter              │
│         │       │  cap at ~30s total budget                        │
│         │       │  sendTransaction-class: tighter budget           │
│         │       └─ exhausted ──▶ raise RpcRateLimitError (typed)   │
│         └─ other 4xx/5xx ──▶ raise RpcError (typed)                │
│                                                                     │
│  get_signatures(pubkey, before, until, limit=1000)                │
│         └─ loop: call getSignaturesForAddress                     │
│               before = last batch's oldest signature               │
│               until batch returns < limit results                 │
└──────────────────────────────┬────────────────────────────────────┘
                                │ (shared Config; independent connection)
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  rpc/ws.py — persistent subscription over websockets.asyncio      │
│                                                                     │
│  async for websocket in connect(SOLANA_WS,                        │
│                    ping_interval=20, ping_timeout=20,              │
│                    process_exception=classify_ws_error):           │
│      resubscribe(websocket, active_pubkeys)   # every (re)connect │
│      last_seen = now()                                            │
│      async for message in websocket:                              │
│          last_seen = now()                                        │
│          dispatch(message)  # to caller's callback                │
│      # loop exits on transient error ──▶ library retries w/ backoff│
│      #        └─ before retrying: signal_backfill_needed(gap)      │
│                                                                     │
│  + independent liveness timer task:                                │
│      if now() - last_seen > heartbeat_threshold (~2x ping_interval)│
│          force-close current connection ──▶ triggers reconnect     │
│                                                                     │
└─────────────────────────────────────────────────────────────────┘
```

### Recommended Project Structure
```
src/bastion/
├── config.py                # env loading + safety rails; Config dataclass
├── rpc/
│   ├── __init__.py
│   ├── client.py             # JSON-RPC: call(), get_balance, get_latest_blockhash,
│   │                          #   get_signatures (paginated), get_transaction,
│   │                          #   get_fee_for_message, send_raw; retry/backoff wrapper
│   ├── ws.py                  # ws_subscribe_logs/ws_subscribe_account, heartbeat,
│   │                          #   reconnect, backfill-needed signal hook
│   └── errors.py              # RpcError, RpcRateLimitError, RpcTimeoutError (typed)
tests/
├── unit/
│   ├── test_config.py         # env precedence, getpass fallback, safety-rail overrides
│   ├── test_rpc_client.py     # respx-mocked: 429/backoff, pagination >1000, typed errors
│   └── test_rpc_ws.py         # local websockets.serve() test server: silent drop,
│                                #   reconnect, resubscribe, backfill signal
└── conftest.py                 # shared fixtures: mock RPC transport, WS test server
```

### Pattern 1: Config loading with 12-factor precedence (D-01)

**What:** `python-dotenv`'s `load_dotenv()` already defaults to `override=False` — it will NOT overwrite a variable that's already present in `os.environ` (real process env). So calling `load_dotenv()` once at startup, then reading everything via `os.getenv(...)`, already satisfies "process env wins over `.env` file" with zero extra merge logic.

**When to use:** Every `config.py` load path — CLI one-shot commands and the monitor daemon both call this once at startup.

**Example:**
```python
# Source: python-dotenv official docs (saurabh-kumar.com/python-dotenv), fetched 2026-07-07
from dotenv import load_dotenv
import os

load_dotenv()  # override=False is the default — process env already wins
solana_rpc = os.getenv("SOLANA_RPC", "https://api.mainnet-beta.solana.com")
```

**Do NOT** use the `dotenv_values()` + manual `{**dotenv_values(...), **os.environ}` merge pattern shown in some docs/tutorials — that's for the case where you deliberately want dotenv_values as a *separate* dict (e.g. multiple `.env` files layered). For Bastion's single-`.env` case, plain `load_dotenv()` + `os.getenv()` is simpler and already correct.

### Pattern 2: `getpass` fallback for the passphrase (D-02)

**What:** Read `KEYSTORE_PASSPHRASE` from env first; if unset (`None`), fall back to an interactive `getpass.getpass()` prompt. Never require it in the environment, never echo, never log.

**Example:**
```python
import getpass, os

def get_passphrase() -> str:
    env_val = os.getenv("KEYSTORE_PASSPHRASE")
    if env_val:
        return env_val
    return getpass.getpass("Keystore passphrase: ")  # no echo, not logged
```

Phase 1 only wires this read + fallback function into `config.py` (per CONTEXT.md: "Full passphrase UX lands in Phase 2"). Do not build confirm-on-create or wrong-passphrase handling here — that's SEC-05/SESS-05 in Phase 2.

### Pattern 3: Safety-rail config fields, each independently overridable (D-08)

**What:** `MAX_SESSION_CAP`, `FEE_RESERVE_LAMPORTS`, and scoring thresholds are typed dataclass fields on `Config`, each read from its own env var with a conservative default, and each covered by a dedicated override-assertion test.

**Example:**
```python
from dataclasses import dataclass

@dataclass(frozen=True)
class Config:
    solana_rpc: str
    solana_ws: str
    max_session_cap_sol: float   # conservative default, e.g. 1.0
    fee_reserve_lamports: int    # fallback only — real reserve computed via
                                  #   getFeeForMessage at sweep time (Phase 3)
    # ... vault_pubkey, keystore_dir, telegram_*, pushover_* etc.

def load_config() -> Config:
    load_dotenv()
    return Config(
        solana_rpc=os.getenv("SOLANA_RPC", "https://api.mainnet-beta.solana.com"),
        solana_ws=os.getenv("SOLANA_WS", "wss://api.mainnet-beta.solana.com"),
        max_session_cap_sol=float(os.getenv("MAX_SESSION_CAP", "1.0")),
        fee_reserve_lamports=int(os.getenv("FEE_RESERVE_LAMPORTS", "5000")),
    )
```

**Test per D-08:** `monkeypatch.setenv("MAX_SESSION_CAP", "0.25"); assert load_config().max_session_cap_sol == 0.25` — one such assertion per safety-rail field, proving it's config-driven and not a hardcoded constant anywhere else in the codebase (grep-based regression test recommended alongside the unit test, per PITFALLS.md's general "grep for the invariant" pattern).

### Pattern 4: JSON-RPC retry/backoff honoring `Retry-After` (D-05)

**What:** A single wrapper function every `rpc/client.py` method routes through. On 429 (or transient 5xx), check for a `Retry-After` header (seconds, per HTTP spec); if present, sleep that long (still capped); if absent, use exponential backoff with jitter. Cap total wait at ~30s, then raise a typed `RpcRateLimitError`. `sendTransaction`-class calls get a tighter budget, per D-05 and PITFALLS.md #14 (sweep-critical calls shouldn't queue behind routine polling backoff).

**Example:**
```python
# Backoff numbers per Helius's own documented retry guidance
# (helius.dev/docs/billing/rate-limits, fetched 2026-07-07):
# "wait ~1s before first retry, double each time up to 30s max, +-25% jitter"
import asyncio, random, httpx

class RpcRateLimitError(Exception): ...

async def _request_with_backoff(
    client: httpx.AsyncClient, payload: dict, *, max_wait_s: float = 30.0,
) -> httpx.Response:
    attempt = 0
    elapsed = 0.0
    while True:
        resp = await client.post("", json=payload)
        if resp.status_code not in (429, 502, 503, 504):
            return resp
        retry_after = resp.headers.get("Retry-After")
        if retry_after is not None:
            wait = float(retry_after)
        else:
            base = min(2 ** attempt, max_wait_s)
            wait = base * (0.75 + random.random() * 0.5)  # +-25% jitter
        if elapsed + wait > max_wait_s:
            raise RpcRateLimitError(f"exhausted retry budget after {elapsed:.1f}s")
        await asyncio.sleep(wait)
        elapsed += wait
        attempt += 1
```

### Pattern 5: `getSignaturesForAddress` cursor pagination past the 1000-cap (D-07)

**What:** `getSignaturesForAddress` caps every call at 1000 results (Solana/Helius, confirmed this session). Page backward with `before=<oldest signature from the previous page>` until a page returns fewer than `limit` results.

**Example:**
```python
# Source: solana.com/docs/rpc/http/getsignaturesforaddress,
# helius.dev/docs/api-reference/rpc/http/getsignaturesforaddress (fetched 2026-07-07)
async def get_signatures(
    self, pubkey: str, *, before: str | None = None,
    until: str | None = None, limit: int = 1000,
) -> list[dict]:
    all_sigs: list[dict] = []
    cursor_before = before
    while True:
        batch = await self.call("getSignaturesForAddress", [
            pubkey, {"before": cursor_before, "until": until, "limit": limit},
        ])
        if not batch:
            break
        all_sigs.extend(batch)
        cursor_before = batch[-1]["signature"]
        if len(batch) < limit:
            break  # short page == end of available history
    return all_sigs
```

`before`: exclusive, searches backward starting just before this signature. `until`: exclusive, stops when this signature is reached (used as the "last known" cursor on reconcile — see ARCHITECTURE.md Pattern 2). Both accept `None`.

### Pattern 6: WebSocket heartbeat + reconnect + backfill-needed signal (D-06)

**What:** `websockets.asyncio.client.connect()` defaults to `ping_interval=20, ping_timeout=20` and supports infinite reconnection via `async for websocket in connect(uri): ...` — transient errors (network errors, HTTP 500/502/503/504) are retried with exponential backoff automatically; fatal errors break the loop (customizable via `process_exception`). This covers *responsive-but-erroring* peers. It does **not**, by itself, cover the specific PITFALLS.md #11 failure mode (a route that silently black-holes all packets, including the client's own outgoing ping, so no timeout fires on the client side because nothing ever fails). Layer your own liveness timer over the library's reconnect loop: track "time since last message of any kind" and force-close+reconnect if it exceeds a threshold (~2x `ping_interval`), independent of whether the library's own `ping_timeout` would have fired.

**Example:**
```python
# Source: websockets.readthedocs.io/en/stable (modern asyncio client), fetched 2026-07-07
import time, asyncio
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

async def watch(uri: str, subscribe_payloads: list[dict], on_message, on_gap):
    async for websocket in connect(uri, ping_interval=20, ping_timeout=20):
        gap_start = None  # set when the previous iteration exited abnormally
        if gap_start is not None:
            on_gap(gap_start)  # signal "backfill needed" to the caller (Phase 6 monitor)
        try:
            for payload in subscribe_payloads:
                await websocket.send(json.dumps(payload))  # re-subscribe every reconnect —
                                                              # subscriptions do NOT survive reconnect
            last_seen = time.monotonic()
            async for message in websocket:
                last_seen = time.monotonic()
                on_message(message)
                # heartbeat check would run as a sibling task comparing
                # time.monotonic() - last_seen against a threshold and
                # calling websocket.close() to force the outer loop to reconnect
        except ConnectionClosed:
            gap_start = time.monotonic()
            continue  # library backs off, then the async-for gives us a fresh connection
```

**Landmine:** the async-iterator's built-in backoff-and-retry only engages once an exception actually surfaces. A true black-hole (packets silently dropped, no RST, no timeout) can leave the `async for message in websocket` line simply hanging forever with no exception at all until the OS-level TCP timeout (minutes). The independent liveness timer (a sibling `asyncio` task, not shown fully above) is what actually closes that gap — don't rely on `ping_timeout` alone to be "the heartbeat," since a fully dead route may never even get a chance to notice its own ping failed to elicit a pong if reads are blocked entirely. Test this distinction explicitly (see Validation Architecture).

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| WebSocket reconnect state machine | A custom retry loop around raw `websockets.connect()` (oneshot mode) | `websockets.asyncio.client.connect()`'s async-iterator reconnect (auto exponential backoff on transient errors, `process_exception` hook for fatal-vs-transient classification) | The library's reconnect logic is well-tested against real-world WS failure modes; reimplementing it risks subtly wrong backoff/classification behavior in exactly the code path that matters most for detection latency |
| Mocking `httpx` for retry/429 tests | Hand-rolled monkeypatching of `httpx.AsyncClient.post` | `respx` (`respx_mock.post(url).mock(side_effect=[httpx.Response(429, headers={"Retry-After": "1"}), httpx.Response(200, json=...)])`) | Purpose-built for exactly this: sequenced responses per call, header injection, and clean pytest fixture integration — a hand-rolled mock would need to reimplement all of this |
| JSON-RPC pagination cursor math | A single unpaginated `getSignaturesForAddress` call "for now, add pagination later" | The paginated loop from Pattern 5, built correctly from day one | PITFALLS.md #12 explicitly documents this as a "looks fine on devnet, silently truncates in production" trap — a >1000-signature backfill after any real outage silently drops data if this isn't right from the start |
| Config validation/typing | Raw `os.getenv()` calls scattered across every module that needs a setting | One `config.py` module producing a single frozen `Config` object, imported everywhere | Matches ARCHITECTURE.md's stated integration point ("`config.py` is imported by every later module") — scattering env reads defeats the "safety rails are config-driven, testable" requirement (D-08) since there's no single place to assert overridability |

**Key insight:** Every "don't hand-roll" item above maps to a library or pattern that is specifically designed for the async/reconnect/retry problem space this phase lives in — the temptation to write a quick custom version is highest exactly here (transport code "feels" simple) but is also where PITFALLS.md's #11/#12/#14 documented failure modes actually originate.

## Common Pitfalls

> This section adds phase-1-specific pitfalls not already covered by `.planning/research/PITFALLS.md` #11/#12/#14 (silent WS drops, pagination gaps, rate-limit bursts — already documented there and referenced throughout the patterns above). Read those three alongside this section; they are the authoritative treatment for those three failure modes.

### Pitfall 1: `asyncio.run()` inside a sync wrapper breaks if called from an already-running loop

**What goes wrong:** D-03 calls for "thin sync wrappers" around the async core for one-shot CLI calls (e.g. `get_balance` during `start`/`status`). The naive wrapper is `def get_balance_sync(pubkey): return asyncio.run(client.get_balance(pubkey))`. This works fine when called from a plain synchronous CLI entrypoint — but breaks with `RuntimeError: asyncio.run() cannot be called from a running event loop` the moment any caller (a future async CLI framework, a test using `pytest-asyncio`, or a future refactor where `cli.py` itself becomes async) invokes it from inside an already-running loop.

**Why it happens:** `asyncio.run()` always creates a brand-new event loop and refuses to nest — a very easy trap in a codebase that's async-first at the core but sync at the edges (exactly this phase's D-03 shape).

**How to avoid:** Isolate every sync wrapper's `asyncio.run()` call at the true top level (the `cli.py` entrypoint functions themselves, called directly by `click`/`argparse`, never nested inside another async context). Do not call sync wrappers from within `monitor.py` or any other async function — those call the async methods directly, per D-03's own design ("the long-running monitor uses the async path directly"). Add a test that imports and calls each sync wrapper directly (not from inside a `pytest.mark.asyncio` test) to catch an accidental nested-loop regression early.

**Warning signs:** A sync wrapper function called from anywhere other than a top-level CLI command handler; a `pytest.mark.asyncio` test that also calls a sync wrapper (this will fail immediately and is the regression's own canary).

**Phase to address:** `rpc/client.py` sync wrapper design, this phase — get the boundary right now since `funder.py`/`sweeper.py` (Phase 3) and `cli.py` (Phase 7) both depend on these wrappers existing and being safe to call from a plain synchronous context.

### Pitfall 2: `websockets`' `ping_timeout` alone doesn't catch every "silent drop" shape

**What goes wrong:** It's tempting to treat `connect(uri, ping_interval=20, ping_timeout=20)` as "the heartbeat" and consider D-06 satisfied. The library's ping/pong does catch *most* silent-drop scenarios (peer stopped responding but the local socket is still open) — but a fully black-holed route (a firewall/NAT silently dropping all packets in both directions, common in some VPN/proxy setups) can leave even the outgoing ping frame unsent or unacknowledged at the TCP layer, which surfaces as a much longer OS-level TCP timeout, not the library's 20s `ping_timeout`.

**Why it happens:** `ping_timeout` measures "did I get a pong back after I successfully sent a ping" — it assumes the ping itself made it out, which isn't guaranteed on every kind of network failure.

**How to avoid:** Layer an independent liveness timer (Pattern 6) that tracks wall-clock time since the last *any* message (data message or pong) and force-closes+reconnects if that exceeds ~2x `ping_interval`, regardless of what the library's internal ping machinery thinks is happening. This is redundant with the library's own timeout in the common case (harmless) and is the only thing that catches the harder black-hole case.

**Warning signs:** A monitor that only relies on `ConnectionClosed` exceptions to detect drops (no independent timer); no test that specifically simulates "server accepts connection, then stops reading/writing entirely without closing" (as opposed to "server sends a close frame" or "server crashes/RSTs the connection") — see Validation Architecture for how to construct this test.

**Phase to address:** `rpc/ws.py`, this phase (the mechanism); `monitor.py`, Phase 6 (wiring the resulting "backfill needed" signal into actual backfill dispatch).

### Pitfall 3: `python-dotenv`'s `override=False` default is correct — don't "fix" it

**What goes wrong:** A developer reviewing `config.py` and seeing `load_dotenv()` called with no arguments might "fix" it to `load_dotenv(override=True)` thinking real env vars should be refreshed from `.env` on every call, or might add a manual `os.environ.update(dotenv_values())` — either change silently inverts the D-01 precedence requirement, making `.env` values win over real process/secret-manager env vars (exactly backwards from 12-factor and from what CI/secret-manager deployment paths need).

**Why it happens:** `override=True` reads as "more correct/complete" to someone unfamiliar with the specific default, and the default's correctness is non-obvious without checking the docs (confirmed explicitly this session: `override=False` is the default and is exactly right for D-01 — no change needed).

**How to avoid:** Add an explicit unit test asserting the precedence: `monkeypatch.setenv("SOLANA_RPC", "process-env-value")`, write a temp `.env` with `SOLANA_RPC=dotenv-value`, call `load_config()`, assert the result is `"process-env-value"`. This turns the "don't touch this default" invariant into a regression test rather than a comment.

**Warning signs:** Any `override=True` argument to `load_dotenv()` anywhere in the codebase; any manual `os.environ.update(...)` or `{**dotenv_values(), **os.environ}` construction that isn't specifically justified by a multi-file `.env` layering need (not present in this project's design).

**Phase to address:** `config.py`, this phase.

### Pitfall 4: `getFeeForMessage` defaults to `finalized` commitment, which is the wrong default here

**What goes wrong:** `getFeeForMessage`'s config object defaults `commitment` to `finalized` when not explicitly specified (confirmed this session against Solana's own RPC docs). PITFALLS.md #2 already documents that fetching a *blockhash* at `finalized` commitment (vs. `confirmed`) shrinks the usable expiry window; the same reasoning applies to fee estimation calls made as part of the same transaction-building flow — mixing commitment levels across the blockhash-fetch and fee-estimate calls for the same in-flight transaction can produce inconsistent state views.

**Why it happens:** RPC method defaults aren't uniform across methods, and it's easy to assume "no commitment specified = whatever I used elsewhere" when actually each call independently defaults to `finalized` unless told otherwise.

**How to avoid:** Explicitly pass `{"commitment": "confirmed"}` on every `getFeeForMessage` call (this phase's `rpc/client.py` method signature should make `commitment` a required or clearly-defaulted-to-`confirmed` parameter, not silently inherit the RPC's own `finalized` default), consistent with PITFALLS.md #2's guidance for blockhash fetches. This phase only builds the method; Phase 3's sweeper is the actual caller and consumer of the fee estimate, but getting the default right here prevents an inherited bug.

**Warning signs:** A `get_fee_for_message` call in `rpc/client.py` with no `commitment` key in its params dict.

**Phase to address:** `rpc/client.py`, this phase (method signature/default); consumed correctly by Phase 3's sweeper reserve calculation (PITFALLS.md #1).

## Code Examples

Verified patterns from official sources (see Sources section for exact URLs/fetch dates):

### Retry/backoff test with `respx` (D-05 verification)
```python
# Source: github.com/lundberg/respx, lundberg.github.io/respx/guide (fetched 2026-07-07)
import httpx, pytest, respx

@pytest.mark.asyncio
@respx.mock
async def test_retries_on_429_honoring_retry_after():
    route = respx.post("https://rpc.example/").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(200, json={"jsonrpc": "2.0", "result": 12345, "id": 1}),
        ]
    )
    async with httpx.AsyncClient(base_url="https://rpc.example/") as client:
        resp = await _request_with_backoff(client, {"method": "getBalance"})
    assert resp.status_code == 200
    assert route.call_count == 2
```

### `getSignaturesForAddress` pagination boundary test (D-07 verification)
```python
# Simulates a >1000-signature mocked stream to prove no truncation
@pytest.mark.asyncio
@respx.mock
async def test_get_signatures_paginates_past_1000():
    page1 = [{"signature": f"sig{i}"} for i in range(1000)]
    page2 = [{"signature": f"sig{i}"} for i in range(1000, 1500)]
    respx.post("https://rpc.example/").mock(
        side_effect=[
            httpx.Response(200, json={"result": page1}),
            httpx.Response(200, json={"result": page2}),
        ]
    )
    async with httpx.AsyncClient(base_url="https://rpc.example/") as client:
        rpc = RpcClient(client)
        sigs = await rpc.get_signatures("SomePubkey111...")
    assert len(sigs) == 1500  # no truncation at the 1000-cap boundary
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| `websockets.legacy.client.connect()` (pre-14.0 style, callback-based reconnect handling) | `websockets.asyncio.client.connect()` used as an async iterator with built-in reconnect | `websockets` 14.0 (Oct 2024) introduced the new asyncio implementation as default; legacy path is deprecated with removal scheduled through 2030 | Any tutorial/StackOverflow snippet showing `websockets.connect()` in a `try/except` reconnect loop without the `async for websocket in connect(...)` pattern is likely written against the legacy API — port it, don't copy it verbatim |
| Manual `{**dotenv_values(), **os.environ}` merge for env precedence | Plain `load_dotenv()` (default `override=False`) + `os.getenv()` | N/A — `override=False` has been the default for many `python-dotenv` releases; not a recent change, just a commonly-missed detail | Simpler `config.py`; no need for the merge-dict pattern shown in some dotenv tutorials aimed at multi-file layering scenarios Bastion doesn't have |

**Deprecated/outdated:**
- `websockets.legacy.*` — deprecated, do not build new code against it (already called out in STACK.md's "What NOT to Use").

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|----------------|
| A1 | Helius free-tier `sendTransaction` limit is exactly "1/sec" (carried from STACK.md/PITFALLS.md, re-confirmed via websearch this session but the fetched Helius rate-limits page text was a search-engine excerpt, not a full authoritative page render) | Standard Stack / Pattern 4 | If actual limit differs (e.g. per-connection vs per-account, or has changed), the "tighter budget for sendTransaction-class calls" backoff tuning in D-05 could be mistuned — low risk since the mechanism (typed budget, Retry-After-aware) is correct regardless of the exact number; verify the live number against `https://www.helius.dev/docs/billing/rate-limits` at implementation time |
| A2 | `getSignaturesForAddress`'s `before` parameter is "exclusive" (searches strictly before, not inclusive of, the given signature) | Pattern 5 | If actually inclusive, the pagination loop in Pattern 5 could re-fetch one duplicate signature per page boundary — harmless given `ingest_signature`'s idempotent dedupe (ARCHITECTURE.md Pattern 1), but worth a boundary-overlap test either way (already recommended in Validation Architecture below) |
| A3 | `websockets.asyncio.client.connect()`'s default `process_exception` treats HTTP 500/502/503/504 (server-side transient errors during the WS upgrade handshake) as retryable, and everything else as fatal, out of the box | Pattern 6 | If the actual default classification differs (e.g. some transient errors are treated as fatal by default), the reconnect loop could stop retrying on an error class this phase assumed was transient — verify against the installed `websockets==16.0` package's actual default `process_exception` implementation during implementation, not just docs |

**If this table is empty:** N/A — three assumptions logged above; all are low-blast-radius (mechanism is correct either way, only exact numeric/boundary details are unverified against a live environment) and should be spot-checked at implementation time, not treated as blocking.

## Open Questions

1. **Exact current Helius free-tier numeric limits (RPS, sendTransaction/sec, WS connection/subscription caps, monthly credit cap)**
   - What we know: STACK.md/PITFALLS.md previously researched "10 RPS, 1 sendTransaction/sec, 5 concurrent WS, 1000 subs/connection, 1M credits/month"; this session's websearch of `helius.dev/docs/billing/rate-limits` returned "10 requests/s", "1/sec" for sendTransaction, "5/sec" for getProgramAccounts, "5" concurrent WS connections, "1,000" subscriptions per connection — consistent with prior research, no monthly credit cap number was returned this time.
   - What's unclear: Whether Helius has changed these numbers since the last research pass (free-tier limits are a plan-level product decision that changes without much notice).
   - Recommendation: Treat the numbers above as the working assumption for D-05's backoff budget tuning; add a note in the plan to re-check `helius.dev/docs/billing/rate-limits` directly (live page, not search excerpt) immediately before implementing the sendTransaction-class tighter-budget logic, since this is the one number that directly gates a fund-moving code path (Phase 3 will inherit whatever budget this phase bakes in).

2. **Should `rpc/client.py`'s sync wrappers share a client instance/loop, or spin up a fresh `httpx.Client` + fresh event loop per call?**
   - What we know: D-03 asks for "thin sync wrappers for one-shot CLI calls" — the CLI paths (`start`/`status`) each make one or a handful of RPC calls and exit, so connection reuse across calls isn't a strong requirement within a single CLI invocation.
   - What's unclear: Whether to use `httpx.Client` (fully separate sync HTTP client, simplest, but duplicates the async client's retry/backoff logic) vs. `asyncio.run(async_method(...))` per sync wrapper call (reuses the async implementation exactly, but incurs a fresh event loop per call, and risks Pitfall 1 if ever called from a nested context).
   - Recommendation: Left to Claude's Discretion per CONTEXT.md ("internal module/function naming and test-fixture structure... implementation detail"), but given D-03's explicit framing ("thin sync wrappers... around the async core"), lean toward `asyncio.run()`-wrapping the async methods (reuses one retry/backoff implementation, avoids logic duplication) — just enforce the Pitfall 1 boundary (only called from true top-level CLI handlers) with a test.

## Environment Availability

No live-environment probing was performed for this phase — Phase 1 has no runtime service dependency beyond an eventual Helius RPC/WS endpoint (which is mocked entirely for this phase's tests per the Validation Architecture below; no live network call is required to build or test `config.py`/`rpc/`). Python 3.11+, `uv`, and the packages listed in Standard Stack are the only requirements, and their registry availability was confirmed directly via `pip index versions` in this session (see Package Legitimacy Audit).

| Dependency | Required By | Available | Version | Fallback |
|------------|--------------|-----------|---------|----------|
| PyPI registry access (`uv add` / `pip install`) | Installing this phase's dependencies | Not probed this session (registry queries via `pip index versions` succeeded, implying network access is present in the dev environment) | — | — |
| Live Helius RPC/WS endpoint | NOT required for Phase 1 — all tests are mocked (`respx`, local `websockets.serve()`) | N/A by design | — | — |

**Missing dependencies with no fallback:** none identified.
**Missing dependencies with fallback:** none identified — this phase is deliberately network-independent at test time.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | `pytest` 9.x + `pytest-asyncio` 1.x (neither installed yet — Wave 0 must add both) |
| Config file | none yet — Wave 0 must create `pyproject.toml` with `[tool.pytest.ini_options]` (`asyncio_mode = "auto"` recommended, or explicit `@pytest.mark.asyncio` per D-03's discretion on decorator-vs-loop style) |
| Quick run command | `uv run pytest tests/unit/ -x -q` |
| Full suite command | `uv run pytest tests/unit/ -q` (this phase has no devnet/integration suite — those start in Phase 3) |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|---------------------|--------------|
| CLI-05 | Config loads all documented env vars from `.env`/process env | unit | `pytest tests/unit/test_config.py::test_loads_all_documented_env_vars -x` | ❌ Wave 0 |
| CLI-05 | Process env takes precedence over `.env` file value (D-01) | unit | `pytest tests/unit/test_config.py::test_process_env_precedence -x` | ❌ Wave 0 |
| CLI-05 | `KEYSTORE_PASSPHRASE` unset falls back to `getpass.getpass()`, never required in env (D-02) | unit | `pytest tests/unit/test_config.py::test_passphrase_getpass_fallback -x` (monkeypatch `getpass.getpass`) | ❌ Wave 0 |
| CLI-06 | Each safety rail (`MAX_SESSION_CAP`, `FEE_RESERVE_LAMPORTS`, scoring thresholds) is independently overridable via env (D-08) | unit | `pytest tests/unit/test_config.py::test_safety_rail_overrides -x` (one assertion per field) | ❌ Wave 0 |
| CLI-06 | Safety rails have conservative, non-zero defaults when env unset | unit | `pytest tests/unit/test_config.py::test_safety_rail_defaults -x` | ❌ Wave 0 |
| Success criterion 3 (RPC retry) | RPC client retries and backs off on injected 429 without crashing, honors `Retry-After` (D-05) | unit (mocked) | `pytest tests/unit/test_rpc_client.py::test_retries_on_429_honoring_retry_after -x` | ❌ Wave 0 |
| Success criterion 3 (RPC retry) | Retry budget exhausts to a typed error, not an infinite hang | unit (mocked) | `pytest tests/unit/test_rpc_client.py::test_retry_budget_exhaustion_raises_typed_error -x` | ❌ Wave 0 |
| Success criterion 4 (WS reconnect) | WS client detects a **silent** drop (no close frame — simulated by pausing the server's read/write, not sending a close) via active heartbeat and reconnects | unit (local WS server) | `pytest tests/unit/test_rpc_ws.py::test_detects_silent_drop_via_heartbeat -x` | ❌ Wave 0 |
| Success criterion 4 (WS reconnect) | On reconnect, client auto-resubscribes (subscriptions don't survive reconnect) | unit (local WS server) | `pytest tests/unit/test_rpc_ws.py::test_resubscribes_after_reconnect -x` | ❌ Wave 0 |
| Success criterion 4 (WS reconnect) | On drop, client signals "backfill needed" to its caller | unit (local WS server) | `pytest tests/unit/test_rpc_ws.py::test_signals_backfill_needed_on_reconnect -x` | ❌ Wave 0 |
| Success criterion 5 (pagination) | `get_signatures` paginates via `before`/`until` across a >1000-signature mocked stream without truncating | unit (mocked) | `pytest tests/unit/test_rpc_client.py::test_get_signatures_paginates_past_1000 -x` | ❌ Wave 0 |
| Success criterion 5 (pagination) | Pagination terminates correctly on a short final page (no infinite loop) | unit (mocked) | `pytest tests/unit/test_rpc_client.py::test_get_signatures_terminates_on_short_page -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `uv run pytest tests/unit/ -x -q` (fast — everything is mocked, no live network, should run in well under 30s)
- **Per wave merge:** `uv run pytest tests/unit/ -q` (full unit suite for this phase)
- **Phase gate:** Full suite green before `/gsd-verify-work`; no devnet/integration dependency for Phase 1 (first devnet suite starts Phase 3)

### Wave 0 Gaps
- [ ] `pyproject.toml` — project doesn't exist yet; needs `[project]`, `[tool.pytest.ini_options]`, dependency declarations (`hatchling` build backend per STACK.md)
- [ ] `uv.lock` — generate via `uv sync` after adding dependencies
- [ ] `tests/conftest.py` — shared fixtures: a `respx`-mocked `httpx.AsyncClient` factory, a local `websockets.serve()` test-server fixture with a hook to force a silent drop (pause reading without closing) vs. a clean close
- [ ] `tests/unit/test_config.py`, `tests/unit/test_rpc_client.py`, `tests/unit/test_rpc_ws.py` — all net-new, covers every row in the requirements map above
- [ ] Framework install: `uv add --dev pytest pytest-asyncio respx`

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|----------------|---------|--------------------|
| V2 Authentication | No | This phase has no authentication surface — no user login, no session tokens. Deferred to keystore/passphrase work in Phase 2. |
| V3 Session Management | No | Not applicable — "session" here means a Solana trading session wallet (Phase 2+), not a web session. |
| V4 Access Control | No | Single-user, single-machine, no server, no multi-tenant boundary in this phase. |
| V5 Input Validation | Yes | Config values read from env/`.env` must be validated/typed at load time (e.g. `MAX_SESSION_CAP` parsed as `float` and rejected/defaulted if malformed, not passed through as an unvalidated string that later code trusts blindly) — use Python's own type coercion (`float(...)`, `int(...)`) with explicit `try/except ValueError` and a clear startup-time failure, not a silent fallback to a wrong default |
| V6 Cryptography | No | No cryptographic operations in this phase (keystore/scrypt/Fernet is Phase 2). RPC transport uses standard TLS via `httpx`/`websockets` (both verify certificates by default) — no custom crypto code here. |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|------------------------|
| Secret (`VAULT_SECRET`, `KEYSTORE_PASSPHRASE`) accidentally logged via a debug log line, exception traceback, or `repr()` of the `Config` object | Information Disclosure | `Config` dataclass should exclude the raw passphrase/secret from any default `__repr__`/`__str__` (use `field(repr=False)` on `dataclasses.field` for any secret-bearing field, even though Phase 1 config only *reads* the passphrase, doesn't yet handle `VAULT_SECRET` — that's Phase 2/3, but the dataclass pattern should be established now if `Config` grows to include it) |
| Unbounded RPC retry loop used as an accidental self-inflicted DoS against the Helius free-tier quota | Denial of Service | D-05's ~30s-capped backoff with a typed-error exit is exactly the mitigation — never retry indefinitely; this is already the phase's own design, verified by the retry-budget-exhaustion test in the Validation Architecture |
| A malformed/malicious `SOLANA_RPC`/`SOLANA_WS` env value pointing the client at an attacker-controlled endpoint (SSRF-adjacent, though this is a local single-user tool so the "attacker" model here is closer to "a compromised `.env` file", not a remote attacker) | Tampering | Out of scope to *prevent* (a user who can edit `.env` already has local access equivalent to running the tool directly), but worth documenting: this phase should not silently accept a non-HTTPS/non-WSS URL without at least a startup warning, consistent with the project's broader "loud warnings over silent risky defaults" posture (PITFALLS.md #7, #10) |

## Sources

### Primary (fetched directly via WebFetch this session, 2026-07-07 — official/authoritative pages)
- https://www.helius.dev/docs/billing/rate-limits — free-tier RPS/sendTransaction/WS limits, documented retry-backoff guidance
- https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html — modern `connect()` API, `ping_interval`/`ping_timeout` defaults, async-iterator reconnect, `process_exception`
- https://saurabh-kumar.com/python-dotenv/ — `load_dotenv()` `override` parameter default and behavior
- https://solana.com/docs/rpc/http/sendtransaction — `sendTransaction` params/response, `maxRetries`, `getSignatureStatuses` guidance

### Secondary (WebSearch, cross-referencing official doc pages this session)
- https://solana.com/docs/rpc/http/getsignaturesforaddress , https://www.helius.dev/docs/api-reference/rpc/http/getsignaturesforaddress , https://chainstack.com/solana-how-to-getsignaturesforaddress-1000-transaction-limit/ — `before`/`until`/`limit` pagination semantics, 1000-cap
- https://solana.com/docs/rpc/http/getfeeformessage , https://www.helius.dev/docs/rpc/guides/getfeeformessage — params, `commitment` default (`finalized`), response shape
- https://github.com/lundberg/respx , https://lundberg.github.io/respx/guide/ — `side_effect` sequenced-response mocking pattern for retry tests
- https://docs.python.org/3/library/asyncio-eventloop.html and community write-ups (GitHub discussions) — `asyncio.run()` nested-loop `RuntimeError` and mitigation pattern

### Tertiary (project's own prior research — not re-verified this session, cited for continuity)
- `.planning/research/STACK.md` — core library selection/versions (httpx, websockets, python-dotenv already chosen)
- `.planning/research/PITFALLS.md` #1, #2, #7, #11, #12, #14 — fee-reserve miscalculation, blockhash expiry, secret leak paths, silent WS drops, pagination gaps, rate-limit bursts
- `.planning/research/ARCHITECTURE.md` — unified ingestion Pattern 1, cursor-reconciliation Pattern 2, trust-zone separation (this phase's `rpc/` is explicitly the shared, domain-blind boundary both zones use)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — versions independently re-verified via `pip index versions` against live PyPI this session; matches prior STACK.md research exactly for httpx/websockets, adds a confirmed current `python-dotenv`/`respx`/`pytest`/`pytest-asyncio` version
- Architecture: MEDIUM — patterns are drawn from official docs (websockets, httpx-adjacent, python-dotenv) fetched directly this session, but no context7/ref MCP provider was available to cross-verify API surface beyond web search + direct doc fetch, and the environment's `classify-confidence` seam floors `webfetch`/`websearch` providers at LOW regardless of source authority — treat version-sensitive API details (e.g. exact `process_exception` default classification, Assumption A3) as needing a final check against the installed package at implementation time
- Pitfalls: HIGH for the three carried from PITFALLS.md (#11/#12/#14, already HIGH-confidence in that document); MEDIUM for the four new phase-1-specific pitfalls identified this session (reasoned from the fetched docs + Solana/websockets mechanics, not independently incident-verified)

**Research date:** 2026-07-07
**Valid until:** ~30 days for the config/pagination/retry patterns (stable, unlikely to change); ~14 days specifically for the Helius free-tier numeric limits (Open Question 1 — product-tier limits can change without notice, re-verify immediately before implementing the sendTransaction-class backoff budget)

---
*Phase: 1-Foundation — Config + RPC Client*
*Research completed: 2026-07-07*
