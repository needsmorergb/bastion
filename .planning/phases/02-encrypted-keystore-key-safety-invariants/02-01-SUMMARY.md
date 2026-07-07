---
phase: 02-encrypted-keystore-key-safety-invariants
plan: 01
subsystem: keystore
tags: [cryptography, solders, scrypt, fernet, keystore, error-handling, supply-chain]

requires:
  - phase: 01-solana-rpc-ws-foundation
    provides: "bastion/rpc/errors.py message-only typed-error convention this plan mirrors"
provides:
  - "cryptography>=46,<50 and solders>=0.27,<0.29 as hash-pinned project dependencies"
  - "bastion/keystore/ importable package (no submodule re-exports)"
  - "KeystoreError hierarchy: KeystoreWrongPassphraseError, KeystoreCloudSyncError, KeystoreConfigError"
affects: [02-02, 02-03, 02-04, 02-05, phase-03-funder]

tech-stack:
  added: ["cryptography 49.0.0 (Scrypt KDF + Fernet AEAD)", "solders 0.28.0 (Keypair)"]
  patterns:
    - "Message-only typed exceptions (mirrors bastion/rpc/errors.py) — no secret material ever interpolated into an error string"
    - "Keystore package __init__.py is a marker only; downstream modules import directly from submodules to avoid Wave 2 __init__.py merge conflicts"

key-files:
  created:
    - bastion/keystore/__init__.py
    - bastion/keystore/errors.py
  modified:
    - pyproject.toml
    - uv.lock
    - .gitignore

key-decisions:
  - "Task 1 (package-legitimacy checkpoint) auto-approved under AUTO_MODE with documented evidence: both cryptography and solders are CLAUDE.md-mandated stack, independently PyPI-JSON-API-verified in 02-RESEARCH.md as pyca/cryptography and kevinheavey/solders (the 'SUS' flag was a recency-heuristic false positive, not a typosquat signal)"
  - "uv add cryptography>=46,<50 solders>=0.27,<0.29 resolved to cryptography==49.0.0 and solders==0.28.0, hash-pinned into uv.lock in one pass"
  - "Rule 3 auto-fix: anchored .gitignore's keystore/ pattern to /keystore/ (repo root only) — the original unanchored pattern silently shadowed the bastion/keystore/ source package, which git check-ignore confirmed was blocking staging of __init__.py and errors.py"

patterns-established:
  - "Typed keystore exception hierarchy other Phase 2 modules (crypto.py, cloudsync.py, session.py, vault.py) raise against"

requirements-completed: [SESS-01, SESS-04]

coverage:
  - id: D1
    description: "cryptography and solders added as hash-pinned dependencies after a documented, evidence-based legitimacy check"
    requirement: "SESS-01"
    verification:
      - kind: unit
        ref: "uv.lock diff shows cryptography==49.0.0 and solders==0.28.0 with hashes; pyproject.toml dependencies updated"
        status: pass
    human_judgment: false
  - id: D2
    description: "bastion/keystore/errors.py exposes KeystoreError base and three typed subclasses with correct inheritance"
    requirement: "SESS-04"
    verification:
      - kind: unit
        ref: "uv run python -c \"from bastion.keystore.errors import KeystoreError, KeystoreWrongPassphraseError, KeystoreCloudSyncError, KeystoreConfigError; assert issubclass(...)\""
        status: pass
      - kind: unit
        ref: "uv run pytest -q (full Phase 1 suite, 28 passed)"
        status: pass
    human_judgment: false

duration: 6min
completed: 2026-07-07
status: complete
---

# Phase 02 Plan 01: Keystore Foundation — Locked Deps + Typed Errors Summary

**Added hash-pinned `cryptography 49.0.0` + `solders 0.28.0` dependencies after an auto-approved supply-chain checkpoint, and created the `bastion/keystore/` package with a message-only `KeystoreError` hierarchy mirroring `bastion/rpc/errors.py`.**

## Performance

- **Duration:** 6 min
- **Started:** 2026-07-07T20:26:07Z
- **Completed:** 2026-07-07T20:28:34Z
- **Tasks:** 2 (1 checkpoint, 1 auto)
- **Files modified:** 5 (pyproject.toml, uv.lock, bastion/keystore/__init__.py, bastion/keystore/errors.py, .gitignore)

## Accomplishments
- Supply-chain legitimacy checkpoint for `cryptography`/`solders` auto-approved under AUTO_MODE with documented PyPI/GitHub evidence from 02-RESEARCH.md
- `uv add "cryptography>=46,<50" "solders>=0.27,<0.29"` resolved and hash-pinned `cryptography==49.0.0` + `solders==0.28.0` into `uv.lock` in one pass
- Created importable `bastion/keystore/` package (marker-only `__init__.py`, no re-exports — keeps Wave 2 plans conflict-free)
- Defined `KeystoreError` hierarchy (`KeystoreWrongPassphraseError`, `KeystoreCloudSyncError`, `KeystoreConfigError`) — message-only, mirrors `bastion/rpc/errors.py`'s convention

## Task Commits

Each task was committed atomically:

1. **Task 1: Supply-chain sanity checkpoint** - auto-approved (AUTO_MODE), no code change — documented in this SUMMARY
2. **Task 2: Add deps, create keystore package and typed error hierarchy** - `f2f8d2f` (feat)

**Plan metadata:** (this commit, docs: complete plan — see final commit below)

## Files Created/Modified
- `pyproject.toml` - added `cryptography>=46,<50` and `solders>=0.27,<0.29` to `[project].dependencies`
- `uv.lock` - hash-pinned resolution for both new deps and their transitive deps (`cffi`, `pycparser`, `jsonalias`)
- `bastion/keystore/__init__.py` - package marker with one-line docstring, no submodule re-exports
- `bastion/keystore/errors.py` - `KeystoreError` base + `KeystoreWrongPassphraseError`, `KeystoreCloudSyncError`, `KeystoreConfigError`
- `.gitignore` - anchored the keystore-data-directory ignore rule to `/keystore/` (repo root) so it no longer shadows `bastion/keystore/` source

## Decisions Made
- **Checkpoint auto-approval:** Task 1's `checkpoint:human-verify` gate was approved under AUTO_MODE per the orchestrator's documented rationale — both packages are the CLAUDE.md-locked stack and were independently verified via the PyPI JSON API in 02-RESEARCH.md (`pyca/cryptography`, `kevinheavey/solders`); the "SUS" flag from the automated legitimacy seam was a recency-of-latest-release false positive, not a typosquat signal. This is documented evidence, not a silent skip of the gate.
- **Version pins:** Used the exact ranges from 02-RESEARCH.md (`cryptography>=46,<50`, `solders>=0.27,<0.29`), landing on `49.0.0`/`0.28.0` respectively.
- **No `__init__.py` re-exports:** Per plan instruction, downstream Phase 2 modules import directly from submodules (e.g. `from bastion.keystore.crypto import ...`) to avoid merge conflicts across parallel Wave 2 plans.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] `.gitignore`'s `keystore/` pattern shadowed the source package**
- **Found during:** Task 2, staging `bastion/keystore/__init__.py` and `bastion/keystore/errors.py`
- **Issue:** The repo's existing `.gitignore` (from Phase 1, `SEC-01`/`DIST-02` intent) had an unanchored `keystore/` pattern meant to exclude a runtime keystore-data directory. Because it wasn't anchored to the repo root, it also matched `bastion/keystore/`, silently blocking `git add` on the brand-new source package (`git status` reported the files as "ignored," not just untracked).
- **Fix:** Anchored the pattern to `/keystore/` (repo-root only) and added a comment clarifying intent, so it no longer matches the `bastion/keystore/` Python package while still excluding any root-level runtime keystore directory.
- **Files modified:** `.gitignore`
- **Verification:** `git check-ignore -v bastion/keystore/errors.py bastion/keystore/__init__.py` returned exit 1 (no longer ignored) after the fix; both files staged and committed successfully.
- **Committed in:** `f2f8d2f` (part of Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Necessary correctness fix — without it, no file in `bastion/keystore/` could ever be committed for the rest of Phase 2. No scope creep; only the ignore-pattern anchoring changed.

## Issues Encountered
None beyond the deviation above.

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- `bastion/keystore/errors.py`'s four-exception hierarchy is now available for 02-02 (`crypto.py`), 02-03 (`vault.py`), 02-04 (`cloudsync.py`/`passphrase.py`), and 02-05 (`session.py`) to raise against.
- `cryptography` and `solders` are hash-pinned and importable; no further dependency work needed for the rest of Phase 2.
- No blockers identified for Wave 2 plans.

---
*Phase: 02-encrypted-keystore-key-safety-invariants*
*Completed: 2026-07-07*

## Self-Check: PASSED

- FOUND: bastion/keystore/__init__.py
- FOUND: bastion/keystore/errors.py
- FOUND: .planning/phases/02-encrypted-keystore-key-safety-invariants/02-01-SUMMARY.md
- FOUND: f2f8d2f (Task 2 commit)
- FOUND: 0c7e61b (SUMMARY commit)
