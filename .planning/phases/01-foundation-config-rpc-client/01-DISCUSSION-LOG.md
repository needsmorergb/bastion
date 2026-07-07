# Phase 1: Foundation — Config + RPC Client - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-07-06
**Phase:** 1-Foundation — Config + RPC Client
**Mode:** `--auto` (recommended defaults auto-selected; no interactive prompts)
**Areas discussed:** Config source & precedence, RPC client shape, 429/backoff policy, WebSocket reconnect/heartbeat, Safety-rail defaults

---

## Config source & precedence

| Option | Description | Selected |
|--------|-------------|----------|
| `.env` via python-dotenv + real-env override, ship `.env.example` | 12-factor; distribution-ready | ✓ |
| Env-only (no file loading) | Simpler, but no `.env.example` story for distribution | |

**Auto choice:** `.env` via python-dotenv with real-env precedence.
**Notes:** DIST-02 requires a `.env.example`; env-override preserves CI/secret-manager paths.

---

## RPC client shape (sync vs async)

| Option | Description | Selected |
|--------|-------------|----------|
| Async-first core + thin sync wrappers | httpx.AsyncClient + websockets; sync wrappers for one-shot CLI | ✓ |
| Sync-only | Blocks the monitor event loop against <5s target | |
| Fully async (no sync wrappers) | CLI one-shots awkward | |

**Auto choice:** Async-first core with thin sync wrappers.
**Notes:** Research STACK.md flagged `requests` as a poor fit; httpx supplies both from one dependency.

---

## 429 / backoff policy

| Option | Description | Selected |
|--------|-------------|----------|
| Bounded exp backoff + jitter, honor Retry-After, cap ~30s, then raise | Respects Helius free-tier limits, never wedges | ✓ |
| Fixed-delay retry | Ignores Retry-After; risks throttle | |

**Auto choice:** Bounded exponential backoff + jitter, honor Retry-After.
**Notes:** Helius free tier ~10 RPS / 1M credits/mo; `sendTransaction` gets a tighter budget.

---

## WebSocket reconnect / heartbeat

| Option | Description | Selected |
|--------|-------------|----------|
| Active heartbeat + exp-backoff reconnect + backfill signal | Catches silent drops (PITFALLS #11) | ✓ |
| React to onclose/onerror only | Misses silent drops — documented failure mode | |

**Auto choice:** Active heartbeat with reconnect + resubscribe + backfill-needed signal.
**Notes:** Phase 1 exposes the hooks; the monitor (Phase 6) wires backfill.

---

## Safety-rail defaults

| Option | Description | Selected |
|--------|-------------|----------|
| Config-driven ceilings, conservative defaults; fee reserve computed at sweep time | Rail is a ceiling; real reserve via getFeeForMessage | ✓ |
| Hardcoded constants | Strands dust; not overridable | |

**Auto choice:** Config-driven rails with conservative defaults; `FEE_RESERVE_LAMPORTS` is a fallback, real reserve computed via `getFeeForMessage` in Phase 3.
**Notes:** PITFALLS #1 — flat fee reserve strands dust.

---

## Claude's Discretion

- Exact `httpx` / `websockets` minor versions (lock via `uv add` at implementation).
- Internal naming, test-fixture structure, decorator-vs-loop for backoff.

## Deferred Ideas

- Enhanced/parsed Helius transaction endpoints — revisit Phase 5/6 if raw-log parsing is painful.
- Priority-fee / compute-budget handling — belongs with funder/sweeper (Phase 3).
