---
phase: 02-encrypted-keystore-key-safety-invariants
plan: 04
subsystem: keystore
tags: [cloud-sync, passphrase-ux, fail-loud, getpass, tdd, security-boundary]

requires:
  - phase: 02-01
    provides: "bastion/keystore/errors.py: KeystoreCloudSyncError, KeystoreConfigError, KeystoreError"
  - phase: 01-01
    provides: "bastion/config.py Config.keystore_dir field + get_passphrase() getpass pattern (reused, not modified)"
provides:
  - "bastion/keystore/cloudsync.py: detect_cloud_sync(path) -> str | None, check_keystore_dir(path, allow_cloud_sync=False) -> None"
  - "bastion/keystore/passphrase.py: prompt_new_passphrase(confirm_attempts=3) -> str, MIN_PASSPHRASE_WARN_LEN"
  - "tests/unit/test_keystore_cloud_sync.py: 13 tests covering detection + refuse/warn/empty-dir behaviors"
  - "tests/unit/test_keystore_passphrase_ux.py: 6 tests covering match/retry/exhaustion/empty/short/no-leak behaviors"
affects: [phase-02-session, phase-07-cli]

tech-stack:
  added: []
  patterns:
    - "Case-insensitive path-segment substring match against os.path.realpath() output for cloud-sync detection -- robust to relocated/renamed provider folders rather than matching exact default install paths"
    - "warnings.warn(UserWarning) as the 'loud but non-fatal' signal for an explicit opt-in override (allow_cloud_sync, short-passphrase) -- mirrors bastion/config.py's existing _warn_if_insecure_scheme pattern"
    - "getpass.getpass scripted via monkeypatch.setattr('getpass.getpass', ...) with an iterator of return values to drive confirm/mismatch/exhaustion test paths without real terminal I/O"

key-files:
  created:
    - bastion/keystore/cloudsync.py
    - bastion/keystore/passphrase.py
    - tests/unit/test_keystore_cloud_sync.py
    - tests/unit/test_keystore_passphrase_ux.py
  modified: []

key-decisions:
  - "Each task followed its own strict RED -> GREEN TDD gate as two separate commits (test-only, then feat), rather than a single combined commit -- matches the pattern established in 02-02/02-03 for atomic TDD gate fidelity."
  - "check_keystore_dir's empty/whitespace-path check runs before the cloud-sync detection check, so KeystoreConfigError fires regardless of allow_cloud_sync -- resolves 02-RESEARCH.md Open Question 1 (no silent home-dir default, which could itself land under a synced path)."
  - "detect_cloud_sync matches on segment substring (e.g. 'onedrive' anywhere in a lowercased path segment) rather than exact default install paths, per 02-RESEARCH.md's 'Don't Hand-Roll' guidance -- robust to relocated/renamed folders across OS versions, at the deliberate cost of a plain folder literally named e.g. 'my-dropbox-backup' also matching (accepted low-risk false-positive per CONTEXT.md's Claude's-Discretion note)."
  - "prompt_new_passphrase does not add a Config field or touch config.get_passphrase() -- unlock continues unchanged; this module is additive, used only at create time, matching the plan's explicit instruction."
  - "allow_cloud_sync is exposed only as a function parameter (no CLI flag / Config field wiring yet) -- explicitly deferred to Phase 7 per 02-RESEARCH.md Open Question 2 and the plan's objective."

patterns-established:
  - "Startup safety-rail modules (cloudsync.py, passphrase.py) are pure/self-contained with no dependency on Config or on each other, so session.py (02-05) can call both without introducing a circular import."

requirements-completed: [SEC-04, SEC-05]

coverage:
  - id: D1
    description: "A KEYSTORE_DIR resolving under a Dropbox/OneDrive/iCloud/Google Drive path is refused by default (raises KeystoreCloudSyncError)"
    requirement: "SEC-04"
    verification:
      - kind: unit
        ref: "tests/unit/test_keystore_cloud_sync.py::test_check_keystore_dir_raises_by_default_on_cloud_sync_path, ::test_detect_cloud_sync_matches_known_provider_segment, ::test_detect_cloud_sync_is_case_insensitive_for_each_provider[Dropbox|OneDrive|Google Drive|Mobile Documents|CloudDocs]"
        status: pass
    human_judgment: false
  - id: D2
    description: "The allow_cloud_sync override downgrades the refusal to a loud warning instead of raising"
    requirement: "SEC-04"
    verification:
      - kind: unit
        ref: "tests/unit/test_keystore_cloud_sync.py::test_check_keystore_dir_override_downgrades_to_warning_not_raise"
        status: pass
    human_judgment: false
  - id: D3
    description: "An empty/unset KEYSTORE_DIR raises KeystoreConfigError (no silent default), regardless of the override"
    requirement: "SEC-04"
    verification:
      - kind: unit
        ref: "tests/unit/test_keystore_cloud_sync.py::test_check_keystore_dir_raises_config_error_on_empty_path, ::test_check_keystore_dir_raises_config_error_on_whitespace_path, ::test_check_keystore_dir_empty_path_raises_regardless_of_override"
        status: pass
    human_judgment: false
  - id: D4
    description: "A normal (non-cloud-synced) KEYSTORE_DIR passes silently"
    requirement: "SEC-04"
    verification:
      - kind: unit
        ref: "tests/unit/test_keystore_cloud_sync.py::test_detect_cloud_sync_returns_none_for_plain_path, ::test_check_keystore_dir_returns_none_for_normal_path"
        status: pass
    human_judgment: false
  - id: D5
    description: "New-passphrase entry is confirmed via getpass (no echo), retries up to 3 times on mismatch then aborts, requires non-empty, and warns on a very short passphrase"
    requirement: "SEC-05"
    verification:
      - kind: unit
        ref: "tests/unit/test_keystore_passphrase_ux.py::test_matching_entries_return_passphrase, ::test_mismatch_then_match_retries_and_returns, ::test_exhausting_all_attempts_raises_keystore_error, ::test_empty_entry_is_rejected_and_retries, ::test_short_passphrase_warns_but_still_returns, ::test_never_raises_with_secret_value_in_message"
        status: pass
    human_judgment: false

duration: 12min
completed: 2026-07-07
status: complete
---

# Phase 02 Plan 04: Cloud-Sync Refusal + Passphrase Confirm-UX Summary

**Built two pure, self-contained startup safety rails: `cloudsync.py` refuses a cloud-synced `KEYSTORE_DIR` by default (downgradable to a loud warning only on explicit opt-in) and `passphrase.py` confirms a new keystore passphrase via no-echo `getpass` with retry-then-abort and short-passphrase warning.**

## Performance

- **Duration:** 12 min
- **Tasks:** 2 (both `type="auto" tdd="true"`)
- **Files modified:** 4 (2 source, 2 test) — all newly created

## Accomplishments
- `bastion/keystore/cloudsync.py`:
  - `CLOUD_SYNC_SEGMENTS` — case-insensitive segment list: `dropbox`, `onedrive`, `google drive`, `mobile documents`, `clouddocs`.
  - `detect_cloud_sync(path) -> str | None` — resolves `os.path.realpath(path)`, lowercases it, splits on both `/` and `\`, returns the first matching segment or `None`.
  - `check_keystore_dir(path, allow_cloud_sync=False) -> None` — raises `KeystoreConfigError` on empty/whitespace path (checked first, before cloud-sync detection, so it fires regardless of the override); raises `KeystoreCloudSyncError` on a detected cloud-sync path by default; emits a `UserWarning` instead when `allow_cloud_sync=True`; returns `None` silently for a normal path.
- `bastion/keystore/passphrase.py`:
  - `MIN_PASSPHRASE_WARN_LEN = 8`.
  - `prompt_new_passphrase(confirm_attempts=3) -> str` — loops up to `confirm_attempts` times reading two `getpass.getpass` entries; empty or mismatched entries consume an attempt and retry; on exhaustion raises `KeystoreError` (message-only, no leaked value); on a non-empty match shorter than `MIN_PASSPHRASE_WARN_LEN`, emits a gentle `UserWarning` before returning the passphrase.
- `tests/unit/test_keystore_cloud_sync.py` — 13 tests: segment detection (plain + all 5 providers, case-insensitive, parametrized), default-raise, override-warns, empty/whitespace-path guard (with and without override), normal-path pass-through.
- `tests/unit/test_keystore_passphrase_ux.py` — 6 tests: match, mismatch-then-match retry, exhaustion raise, empty-entry rejection, short-passphrase warning, no-secret-leak-in-exception-message — all scripting `getpass.getpass` via `monkeypatch` and asserting `builtins.input` is never invoked.

## Task Commits

Each task committed atomically, following strict RED -> GREEN TDD gates:

1. **Task 1 test (RED):** `350729e` — `test(02-04): add failing tests for keystore cloud-sync refusal (SEC-04)` (confirmed `ModuleNotFoundError: No module named 'bastion.keystore.cloudsync'`)
2. **Task 1 implementation (GREEN):** `c96495c` — `feat(02-04): implement cloud-sync refusal + empty-dir guard (SEC-04)` (13/13 tests pass)
3. **Task 2 test (RED):** `4c24124` — `test(02-04): add failing tests for create-passphrase confirm UX (SEC-05)` (confirmed `ModuleNotFoundError: No module named 'bastion.keystore.passphrase'`)
4. **Task 2 implementation (GREEN):** `e1b38e0` — `feat(02-04): implement confirm-on-create no-echo passphrase prompt (SEC-05)` (6/6 tests pass)

Full suite verified green after both tasks: 69/69 passed (`uv run pytest -q`).

**Plan metadata:** (this commit — see final commit below)

## Files Created/Modified
- `bastion/keystore/cloudsync.py` — cloud-sync path detection + refuse-or-warn startup check + empty-dir guard.
- `bastion/keystore/passphrase.py` — confirm-on-create no-echo passphrase prompt with retry + short-warning.
- `tests/unit/test_keystore_cloud_sync.py` — 13 unit tests.
- `tests/unit/test_keystore_passphrase_ux.py` — 6 unit tests.

## Decisions Made
- **Two-commit-per-task TDD split** (test RED, then feat GREEN) for both tasks, matching the pattern already established in 02-02/02-03 for genuine per-task gate fidelity.
- **Empty-path guard ordered before cloud-sync detection** in `check_keystore_dir` so `KeystoreConfigError` is unconditional on `allow_cloud_sync` — directly resolves 02-RESEARCH.md Open Question 1.
- **Segment-substring matching** (not exact default install paths) per 02-RESEARCH.md's "Don't Hand-Roll" guidance — deliberately robust to relocated/renamed cloud folders; accepted low-risk tradeoff that a plain folder merely containing a provider name as substring (e.g. `my-dropbox-backup/`) would also match, per CONTEXT.md's explicit "Claude's Discretion" allowance on this exact mechanism.
- **No CLI/Config wiring for `allow_cloud_sync`** in this plan — it remains a function parameter only, deferred to Phase 7 per 02-RESEARCH.md Open Question 2 and the plan's stated scope.
- **`prompt_new_passphrase` does not touch `config.py`** — no new `Config` field, and `config.get_passphrase()` (unlock path) is untouched; this is purely additive create-time UX, per the plan's explicit instruction.

## Deviations from Plan

None — plan executed exactly as written. Both modules' signatures, error types, and behaviors match the plan's `must_haves.truths` and `key_links` exactly (`check_keystore_dir` from `cloudsync.py`, `prompt_new_passphrase` from `passphrase.py`, both raising/warning via the typed `bastion.keystore.errors` hierarchy).

## Issues Encountered

None.

## User Setup Required
None — no external service configuration required. Both modules are pure functions exercised entirely through synthetic `tmp_path` paths and scripted `getpass.getpass` values in tests; no real cloud-sync folder or real terminal input is needed.

## Next Phase Readiness
- `check_keystore_dir` and `prompt_new_passphrase` are both ready for `session.py` (02-05) to call at keystore-directory-init and keystore-create time respectively, with no circular-import risk (neither depends on `Config` or on each other).
- The `allow_cloud_sync` CLI/Config wiring point is explicitly left open for Phase 7, as scoped.
- No blockers identified for the remaining Wave 3 plan (02-05, session.py).

---
*Phase: 02-encrypted-keystore-key-safety-invariants*
*Completed: 2026-07-07*
