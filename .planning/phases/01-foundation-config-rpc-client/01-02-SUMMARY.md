---
phase: 01-foundation-config-rpc-client
plan: 02
subsystem: infra
tags: [python-dotenv, dataclass, getpass, config, env-vars]

# Dependency graph
requires:
  - phase: 01-01
    provides: "pyproject.toml/uv.lock, flat bastion/ package layout, tests/unit test infra"
provides:
  - "bastion/config.py: frozen Config dataclass + load_config() + get_passphrase() + ConfigError"
  - "Single source of truth for all 14 CLI-05/CLI-06 env vars — every later module (keystore, funder, sweeper, monitor, scoring, alerter) imports Config instead of reading os.getenv directly"
affects: [01-03, 01-04, keystore-phase, funder-phase, sweeper-phase, monitor-phase]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "load_dotenv() called with its default override=False; process env always wins over .env (D-01) — never flip to override=True, never hand-merge dotenv_values() (Pitfall 3)"
    - "Secret-bearing dataclass fields declared field(repr=False) so Config's default repr/str never leaks vault_secret/keystore_passphrase/telegram_bot_token/pushover_token (T-01-03)"
    - "Numeric safety rails coerced through a guarded _coerce() helper that raises ConfigError on malformed input instead of silently falling back to the default (V5)"
    - "get_passphrase() is a separate call site from load_config() — load_config() never prompts interactively, so non-interactive paths (monitor daemon) never block on stdin"
    - "Non-secure endpoint scheme (non-https SOLANA_RPC / non-wss SOLANA_WS) triggers a UserWarning at load time, never a hard failure (T-01-05, loud-warning-over-silent-risk posture)"

key-files:
  created:
    - bastion/config.py
    - tests/unit/test_config.py
  modified: []

key-decisions:
  - "Rail defaults: MAX_SESSION_CAP=1.0 SOL (per D-08 explicit guidance), FEE_RESERVE_LAMPORTS=5000 (per 01-RESEARCH.md Pattern 3 example), SCORE_WATCH_THRESHOLD=0.5, SCORE_CRITICAL_THRESHOLD=0.8 (WATCH strictly less severe than CRITICAL — not pinned by research, chosen as a conservative placeholder Phase 6 scoring will refine)"
  - "Identity/secret fields (VAULT_SECRET, VAULT_PUBKEY, KEYSTORE_DIR, TELEGRAM_*, PUSHOVER_*) default to empty string rather than raising when unset — Phase 1 config only needs to load them; the modules that actually consume them (Phase 2+) are responsible for validating presence at their own call sites"
  - "getpass.getpass patched/called via the getpass module object (import getpass; getpass.getpass(...)) rather than `from getpass import getpass`, so tests can monkeypatch getpass.getpass directly without needing a bastion.config-specific patch target"

requirements-completed: [CLI-05, CLI-06]

coverage:
  - id: D1
    description: "load_config() returns a frozen Config populated from every documented CLI-05 env var"
    requirement: "CLI-05"
    verification:
      - kind: unit
        ref: "tests/unit/test_config.py#test_loads_all_documented_env_vars"
        status: pass
    human_judgment: false
  - id: D2
    description: "Real process env overrides a .env file value (12-factor precedence, D-01)"
    requirement: "CLI-05"
    verification:
      - kind: unit
        ref: "tests/unit/test_config.py#test_process_env_precedence"
        status: pass
    human_judgment: false
  - id: D3
    description: "KEYSTORE_PASSPHRASE unset falls back to getpass; set in env, getpass is never called (D-02)"
    requirement: "CLI-05"
    verification:
      - kind: unit
        ref: "tests/unit/test_config.py#test_passphrase_getpass_fallback"
        status: pass
      - kind: unit
        ref: "tests/unit/test_config.py#test_passphrase_from_env_when_set"
        status: pass
    human_judgment: false
  - id: D4
    description: "Each CLI-06 safety rail is independently env-overridable with a conservative non-zero default"
    requirement: "CLI-06"
    verification:
      - kind: unit
        ref: "tests/unit/test_config.py#test_safety_rail_overrides"
        status: pass
      - kind: unit
        ref: "tests/unit/test_config.py#test_safety_rail_defaults"
        status: pass
    human_judgment: false
  - id: D5
    description: "Secret-bearing Config fields never appear in repr()/str(); malformed numeric rails raise ConfigError instead of silently defaulting"
    requirement: "CLI-06"
    verification:
      - kind: unit
        ref: "tests/unit/test_config.py#test_config_repr_excludes_secrets"
        status: pass
      - kind: unit
        ref: "tests/unit/test_config.py#test_malformed_rail_fails_loudly"
        status: pass
    human_judgment: false
  - id: D6
    description: "Non-https SOLANA_RPC / non-wss SOLANA_WS emits a UserWarning at load rather than being silently accepted"
    verification:
      - kind: unit
        ref: "tests/unit/test_config.py#test_non_secure_endpoint_warns"
        status: pass
    human_judgment: false

duration: 8min
completed: 2026-07-07
status: complete
---

# Phase 1 Plan 2: Config Loader Summary

**Frozen `Config` dataclass loading all 14 documented CLI-05/CLI-06 env vars with `.env` fallback (process-env-wins precedence), a `getpass`-fallback passphrase helper, secret-safe repr, and loud-fail-on-malformed-rail validation.**

## Performance

- **Duration:** 8 min
- **Started:** 2026-07-07T15:52:00Z (approx, following 01-01 completion at 15:49:26Z)
- **Completed:** 2026-07-07T15:59:26Z
- **Tasks:** 2 (TDD: RED then GREEN)
- **Files modified:** 2 created

## Accomplishments
- Wrote the full nine-test behavior suite (`tests/unit/test_config.py`) encoding every CLI-05/CLI-06 requirement plus the three security invariants (secret-repr-safety, malformed-rail loud failure, non-secure-endpoint warning) before any implementation existed (RED)
- Implemented `bastion/config.py`: a frozen `Config` dataclass, `load_config()`, `get_passphrase()`, and `ConfigError`, making the full suite green (GREEN) with no regression to the 01-01 harness smoke tests (14/14 unit tests pass)
- Established `config.py` as the single source of truth for every safety rail — no later module will read `os.getenv` directly for `MAX_SESSION_CAP`/`FEE_RESERVE_LAMPORTS`/`SCORE_WATCH_THRESHOLD`/`SCORE_CRITICAL_THRESHOLD`, satisfying D-08's "provably config-driven, not hardcoded" requirement
- Proved the non-custodial secret-safety constraint with an executable test: `repr(config)`/`str(config)` cannot leak `vault_secret`, `keystore_passphrase`, `telegram_bot_token`, or `pushover_token`

## Task Commits

Each task was committed atomically:

1. **Task 1: Write the config test suite (RED)** - `7b89c1e` (test)
2. **Task 2: Implement bastion/config.py (GREEN)** - `de11c40` (feat)

**Plan metadata:** (final commit follows this SUMMARY)

## Files Created/Modified
- `tests/unit/test_config.py` - 9 tests covering env-load, process-env precedence, getpass passphrase fallback (both directions), per-rail override + defaults, secret-repr-safety, malformed-rail failure, non-secure-endpoint warning
- `bastion/config.py` - `Config` (frozen dataclass, secret fields `repr=False`), `load_config()` (load_dotenv + guarded numeric coercion + scheme warnings), `get_passphrase()` (env-first, getpass fallback), `ConfigError`

## Decisions Made
- Rail defaults: `MAX_SESSION_CAP=1.0` SOL (explicit D-08 guidance), `FEE_RESERVE_LAMPORTS=5000` (01-RESEARCH.md Pattern 3 worked example), `SCORE_WATCH_THRESHOLD=0.5` / `SCORE_CRITICAL_THRESHOLD=0.8` (not pinned by research — chosen as a conservative WATCH-below-CRITICAL placeholder that Phase 6's scoring engine will tune against the golden fixtures)
- Identity/secret string fields default to `""` rather than raising when unset at config-load time; presence validation for fields a specific module actually needs (e.g. keystore needing `KEYSTORE_DIR`) is that module's responsibility at its own call site, not `config.py`'s
- `getpass` imported as a module (`import getpass`) and called via `getpass.getpass(...)` rather than `from getpass import getpass`, so tests monkeypatch the real `getpass.getpass` function directly — this is more robust than patching a name bound inside `bastion.config`

## Deviations from Plan

None - plan executed exactly as written. TDD gate sequence followed: `test(01-02)` RED commit (`7b89c1e`) confirmed failing at collection (ModuleNotFoundError, since `bastion/config.py` did not yet exist) before any implementation, then `feat(01-02)` GREEN commit (`de11c40`) made the full suite pass. No REFACTOR commit was needed — the initial implementation was already clean.

## Issues Encountered

Worktree setup note (not a plan deviation): this agent's worktree `HEAD` was initially stale — an ancestor of the stated base commit `f196e0652f370686d969f9fa0be2ab577bafed8a` (missing the 01-01 scaffold and all four phase-1 PLAN.md files). Fast-forwarded via `git merge --ff-only f196e0652f370686d969f9fa0be2ab577bafed8a` before reading any plan content; verified safe because `git merge-base HEAD f196e06...` equaled the pre-merge `HEAD` (a pure ancestor relationship, no divergent commits, so the fast-forward lost no work).

## User Setup Required

None - no external service configuration required. `Config` reads from `.env`/process env that the user will populate in a later phase; no action needed now.

## Next Phase Readiness
- `bastion/config.py` is stable and importable; 01-03 (`rpc/client.py`) and 01-04 (`rpc/ws.py`) can both import `Config`/`load_config()` if/when they need rail values (e.g. retry-budget or endpoint URLs), though neither strictly depends on this plan's output to proceed
- No blockers identified

---
*Phase: 01-foundation-config-rpc-client*
*Completed: 2026-07-07*

## Self-Check: PASSED

All created files confirmed present on disk (bastion/config.py, tests/unit/test_config.py, .planning/phases/01-foundation-config-rpc-client/01-02-SUMMARY.md). Both task commits (`7b89c1e`, `de11c40`) confirmed present in git log.
