# Bastion — Session Handoff

**Last updated:** 2026-07-08
**Session:** `/gsd-autonomous` run — executed Phase 3, **stopped by user** at the devnet-validation decision.
**Branch:** `main` (GSD `branching_strategy: none` — all phase work lands on main)
**Remote:** `origin` → `github.com/needsmorergb/bastion.git` (branch `main`)

---

## TL;DR

- **Phases 1–2:** complete and verified (unchanged this session).
- **Phase 3 (Fund-Moving on Devnet — Funder + Sweeper):** fully **built, code-reviewed, and fixed**; **130 unit tests pass**. Verification is **`human_needed`** — the devnet end-to-end tests exist but have never run against a real chain (no devnet credentials in this environment).
- **Phases 4–8:** not started. Milestone lifecycle not reached.

---

## Phase 3 — what shipped

Full autonomous cycle: research → pattern-map → plan (4 plans, 3 waves) → plan-check → execute (sequential on main; worktrees auto-degraded per #683) → code-review → fix → verify.

**New/changed source (all on `main`):**
- `bastion/funder.py` — capped exact-N vault→session funding (D-01), refuse-before-send cap guard (D-03) + insufficient-balance guard (D-04). Sub-lamport amounts rejected before any send (review fix WR-01).
- `bastion/sweeper.py` — exact-zero sweep: `getFeeForMessage(confirmed)` fee (D-05), single atomic tx closing empty ATAs + SOL transfer of `balance − fee` (D-06), sub-fee dust no-op (D-07), `balance == fee` still closes empty ATAs (WR-03). Hand-encoded `close_account` (discriminant 9, 3 metas) — solders has no SPL builder.
- `bastion/land_check.py` — shared, vault-agnostic no-double-spend confirmation loop (D-08/D-09). **See critical fix below.**
- `bastion/keystore/session.py` — D-10 retire guard; now **fails closed** on unknown `token_accounts=None` unless caller passes `token_check_skipped=True` (WR-02).
- `bastion/rpc/client.py` — added `get_signature_statuses()` + `get_token_accounts_by_owner()`.
- `bastion/fund_errors.py` — typed funder errors.
- `tests/e2e/` — opt-in `devnet`-marked fund→sweep round-trip, exact-zero-with-ATA-close, injected-timeout no-double-spend (+ airdrop-429 skip-not-fail fixtures).

**SEC-02 (load-bearing):** proven by an AST import-graph test — `funder.py` is the *only* module allowed to load the vault secret; `sweeper.py` structurally *cannot* import it (negative-contract test green).

**Code review found + fixed 1 CRITICAL:** `land_check.py` — an expired-blockhash resend/poll `RpcError` could escape and falsely report an *already-landed* tx as failed → caller retries → **double-debit**. Fixed: transport errors on poll/resend swallowed (never abort the loop); authoritative on-chain `err` still raises; `confirmed`/`finalized` returns success; unknown status re-POSTs the *identical* blob (never re-signs); `search_history=True` finds landed-but-aged tx; budget exhaustion → timeout. Two regression tests added; re-reviewed independently to **0 critical / 0 warning**. Plus 4 warnings fixed; 5 info deferred (see `03-REVIEW.md`).

---

## The one blocker — devnet validation (Phase 3 verification)

Phase 3's goal is *"…validated end-to-end **on devnet**…"* Three of five success criteria (exact-N fund delta; exact-zero sweep with ATA close; no-double-spend under injected timeout) require a **real-chain** observation. Tests are written and collect cleanly but need devnet credentials this environment lacks. Verifier returned `human_needed` (honest — not a faked pass).

**Close it:**
```bash
# point at devnet + a funded vault, then:
uv run pytest -m devnet -q
# optional: BASTION_E2E_KEYPAIR / BASTION_E2E_MINT to conserve faucet quota
/gsd-verify-work 3     # marks Phase 3 verified once green
```
Env: `SOLANA_RPC` (+ `SOLANA_WS`) on devnet, `VAULT_SECRET` + `VAULT_PUBKEY` for a funded devnet vault.

---

## How to resume the milestone

1. **Validate devnet** (above) → `/gsd-verify-work 3`.
2. Continue:
   - `/gsd-autonomous` — resumes at Phase 4 once Phase 3 shows verified, **or**
   - `/gsd-autonomous --from 4` — proceed to Phase 4 now, validate devnet later.

Remaining: **4** Persistence (SQLite + audit log) → **5** Scoring + LLM-egress boundary → **6** Live monitor + alerting + armed auto-sweep → **7** CLI + mainnet shakeout → **8** Distribution hardening → milestone audit/complete/cleanup.

---

## ⚠️ Standing safety constraints (do NOT violate — carried forward)

1. **Fund-moving phases must NOT run unattended.** Phase 3 (real devnet SOL) and especially **Phase 7 (real mainnet SOL + `--armed` auto-sweep)** require a human in the loop for any real-chain fund movement, regardless of `--auto`/autonomous. *This session honored that: no real funds moved — Phase 3 was code + mocked-RPC unit tests only, and the run stopped at the real-devnet-validation decision.*
2. **Non-custodial invariant:** never transmit, upload, phone home, or store any private key/seed/passphrase, under any code path. No plaintext key to disk or logs. (SEC-02 AST test enforces the sweep-path half structurally.)
3. When resuming autonomously, remember Phase 7's mainnet shakeout is the point where real money is at stake — do not let it execute mainnet transactions without explicit, in-the-loop confirmation.

---

## Gotcha — UI-SPEC gate is buggy for this repo

The GSD UI-plan gate (`ui-safety-gate.cjs`) keyword-matches the token **"UI"** and matches it inside the literal `**UI hint**: no` label — so it reports `frontend: true` for **every** phase, though Bastion is a pure Python CLI with zero frontend. I **skipped** UI-SPEC/UI-review generation for Phase 3 (correct). Do the same for Phases 4–8. Worth reporting upstream to open-gsd. (GSD tooling in `~/.claude/gsd-core/` was **not** modified this session.)

---

## State / tracking

- `STATE.md`: `completed_phases: 2`, status `verification_deferred_human`, Deferred Verification table → `/gsd-verify-work 3`.
- `ROADMAP.md`: Phase 3 checkbox intentionally **left unchecked** ("Executed — devnet verify pending") — corrected from an erroneous auto-"Complete" the plan-progress rollup had set.
- Phase 3 artifacts: CONTEXT, RESEARCH, PATTERNS, VALIDATION, 4× PLAN, 4× SUMMARY, REVIEW, REVIEW-FIX, VERIFICATION — all committed.

## Environment notes

- GSD is `@opengsd/gsd-core`, runtime at `~/.claude/gsd-core/`, tooling `gsd-tools.cjs`. Built-in `/gsd-update` can't reach it — update via `npx @opengsd/gsd-core@latest --claude --global`.
- `.maestro/` is local tool state — gitignored, do not commit.
