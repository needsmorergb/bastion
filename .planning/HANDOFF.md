# Bastion — Session Handoff

**Last updated:** 2026-07-06
**Status:** Project initialized + Phase 1 scoped. No code written yet. Auto-chain OFF.

---

## Where things stand

`/gsd-new-project --auto @bastion-spec.md` ran to completion on GSD `@opengsd/gsd-core` v1.6.1.
All planning artifacts are committed. Phase 1 context is captured and ready to plan.

**Nothing has been implemented.** Phases 1–8 are planned but unexecuted.

### Committed artifacts
| Artifact | Path |
|----------|------|
| Source spec | `bastion-spec.md` |
| Project doc | `.planning/PROJECT.md` |
| Config | `.planning/config.json` |
| Research (4 docs + summary) | `.planning/research/` |
| Requirements (42 v1) | `.planning/REQUIREMENTS.md` |
| Roadmap (8 phases) | `.planning/ROADMAP.md` |
| State memory | `.planning/STATE.md` |
| Phase 1 context + discussion log | `.planning/phases/01-foundation-config-rpc-client/` |
| Project guide | `.claude/CLAUDE.md` |

---

## Roadmap at a glance (8 phases, horizontal layers)

1. **Foundation — Config + RPC client** ← *scoped, ready to plan* (no funds)
2. Encrypted keystore + key-safety (no funds)
3. **Fund-moving on devnet** ⚠️ real devnet SOL
4. Persistence — store + audit (no funds)
5. Scoring + LLM-egress boundary (no funds)
6. Monitor + alerting + armed sweep (devnet)
7. **CLI + mainnet shakeout** ⚠️⚠️ real mainnet SOL + `--armed` auto-sweep
8. Distribution hardening

---

## Resume here

```
/clear
/gsd-plan-phase 1
```

`plan-phase 1` drafts the implementation plan for review — **no code executed**. From there:
- `/gsd-plan-phase 1 --chain` — plan then auto-execute Phase 1 (config/RPC only — safe, no funds)

### Phase 1 scope (locked decisions D-01..D-08)
- `config.py` — `.env` via python-dotenv, real-env override, ship `.env.example`; `KEYSTORE_PASSPHRASE` → `getpass` fallback
- `rpc/client.py` — async-first `httpx.AsyncClient` JSON-RPC + thin sync wrappers; bounded exp backoff + jitter honoring `Retry-After`, cap ~30s; cursor pagination (`getSignaturesForAddress` 1000-cap); add `get_fee_for_message`
- `rpc/ws.py` — modern `websockets` API; active heartbeat, backoff reconnect + resubscribe, "backfill-needed" signal hook
- Safety rails config-driven, never hardcoded (`MAX_SESSION_CAP` ~1.0 SOL default, `FEE_RESERVE_LAMPORTS` fallback)
- Covers requirements **CLI-05, CLI-06**

---

## ⚠️ Standing safety constraints (do NOT violate)

1. **Phase 3 (devnet SOL) and Phase 7 (mainnet SOL + `--armed` auto-sweep) must NOT run unattended.** Stay in the loop for any fund-moving phase regardless of `--auto`/`--chain`.
2. **Non-custodial invariant:** never transmit, upload, phone home, or store any private key/seed/passphrase, under any code path. No plaintext key to disk or logs.
3. Auto-chain flag is currently **OFF** (`workflow._auto_chain_active = false`).

---

## Environment notes

- **GSD package migrated:** now `@opengsd/gsd-core` (v1.6.1), runtime at `~/.claude/gsd-core/`, tooling `gsd-tools.cjs`. The built-in `/gsd-update` can't reach it — update via `npx @opengsd/gsd-core@latest --claude --global`. (Also saved to session memory.)
- Repo remote: `origin` → `github.com/needsmorergb/bastion.git` (branch `main`).
- `.maestro/` is local tool state — gitignored, do not commit.
