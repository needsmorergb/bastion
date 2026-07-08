---
phase: 03-fund-moving-on-devnet-funder-sweeper
verified: 2026-07-08T03:03:54Z
status: human_needed
score: 3/6 must-haves verified
behavior_unverified: 3 # SC1 (devnet exact-N delta), SC3 (devnet exact-zero-with-ATA), SC4b (devnet no-double-spend) — code present, wired, unit-verified with mocked RPC, never exercised against a real chain
overrides_applied: 0
behavior_unverified_items:
  - truth: "A devnet fund->sweep round trip moves exactly N SOL vault->session and asserts the exact balance delta on a real chain (SESS-02, roadmap SC1)."
    test: "Run `uv run pytest -m devnet -q tests/e2e/test_devnet_fund_sweep.py::test_fund_moves_exact_amount` with SOLANA_RPC/SOLANA_WS pointed at devnet, VAULT_SECRET + VAULT_PUBKEY set to a funded devnet vault."
    expected: "Test passes: the session's on-chain balance increases by exactly round(N * LAMPORTS_PER_SOL) lamports after fund_session() confirms."
    why_human: "The devnet test exists, is logically sound, and mirrors the mocked unit test's assertions exactly, but has never been executed against a live RPC endpoint in this environment (no devnet credentials available). Real-chain RPC response shapes, fee estimation, and confirmation timing cannot be proven correct by mocked respx fixtures alone."
  - truth: "A devnet sweep of a wallet holding SOL plus one open empty ATA ends the session at exactly 0 lamports with the ATA closed and all value in the vault (SESS-06, roadmap SC3)."
    test: "Run `uv run pytest -m devnet -q tests/e2e/test_devnet_fund_sweep.py::test_sweep_to_exact_zero_with_ata` with the same devnet credentials (optionally BASTION_E2E_MINT to skip throwaway-mint creation)."
    expected: "Test passes: get_balance(session) == 0 after the sweep, the ATA no longer appears in get_token_accounts_by_owner, and the vault balance increased by the swept SOL plus reclaimed ATA rent."
    why_human: "Same as above — the hand-encoded SPL CloseAccount instruction and multi-instruction MessageV0 compile are unit-tested with mocked RPC responses but the on-chain Associated Token Account creation, close, and rent-reclamation sequence has never been run against the real SPL Token program on devnet."
  - truth: "An injected post-send timeout (observed after the transaction actually landed) followed by a retry produces exactly one transfer on a real chain — no double-spend (D-08, roadmap SC4, real-chain half)."
    test: "Run `uv run pytest -m devnet -q tests/e2e/test_devnet_fund_sweep.py::test_no_double_spend_on_injected_timeout` with the same devnet credentials."
    expected: "Test passes: after the injected null-status observation and retry, exactly one transfer/one confirmed signature is reflected in the session and vault balances — no second on-chain transfer occurred."
    why_human: "The underlying re-send-identical-blob property (never re-sign) is already deterministically proven at the unit level (tests/unit/test_land_check.py — 4 passing cases including transient-failure and expired-blockhash-resend scenarios). What remains unverified is that Solana's real leader/validator dedup-by-signature behavior actually prevents a double-spend when the identical blob is re-POSTed against a live cluster, which cannot be proven by a mock."
human_verification:
  - test: "Run `uv run pytest -m devnet -q` (all three devnet tests) with SOLANA_RPC/SOLANA_WS set to devnet endpoints, VAULT_SECRET + VAULT_PUBKEY set to a real funded devnet vault, and optionally BASTION_E2E_KEYPAIR / BASTION_E2E_MINT to conserve faucet quota."
    expected: "All three tests pass (or documented 429/faucet-exhaustion skip, never a hard failure): exact-N fund delta, exact-zero sweep with ATA closed, and no-double-spend after an injected timeout."
    why_human: "Requires live devnet RPC access and a funded devnet vault keypair — neither is available in the verification environment. This is the real-chain confirmation the phase goal explicitly calls for ('validated end-to-end on devnet'); the logic is already exhaustively unit-verified with mocked RPC responses, but 'on devnet' has not literally been observed."
---

# Phase 3: Fund-Moving on Devnet (Funder/Sweeper) Verification Report

**Phase Goal:** The core containment primitive — capped vault→session funding and full session→vault sweep to exact zero — validated end-to-end on devnet before any mainnet SOL is at risk.
**Verified:** 2026-07-08T03:03:54Z
**Status:** human_needed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (mapped to the 5 ROADMAP success criteria)

| # | Truth (ROADMAP success criterion) | Status | Evidence |
|---|---|---|---|
| SC1 | Funder moves requested SOL vault→session on devnet; a test asserts the exact balance delta. | ⚠️ PRESENT_BEHAVIOR_UNVERIFIED | `bastion/funder.py::fund_session` builds an exact-lamport transfer (`amount_lamports = round(amount_sol * LAMPORTS_PER_SOL)`), unit-proven in `tests/unit/test_funder.py` (mocked RPC, exact-lamport assertion, `test_happy_path_builds_exact_transfer_and_returns_signature`). Devnet analog `tests/e2e/test_devnet_fund_sweep.py::test_fund_moves_exact_amount` exists, collects cleanly (`uv run pytest -m devnet --collect-only -q` → 3 collected), but has never been executed against a real chain (03-04-SUMMARY.md explicitly marks `human_judgment: true`, `status: unknown`). |
| SC2 | Funder refuses and sends nothing when requested cap exceeds MAX_SESSION_CAP. | ✓ VERIFIED | `fund_session` raises `FunderCapExceededError` strictly-greater-than-cap, before touching `vault.py` or the network (`bastion/funder.py:80-84`). `tests/unit/test_funder.py::test_cap_exceeded_raises_and_makes_zero_rpc_calls` asserts the respx router recorded zero requests. Pure refuse-before-send logic — mocked coverage is sufficient; devnet execution adds no new evidence for this truth. Confirmed green: `uv run pytest tests/unit/test_funder.py -q`. |
| SC3 | Sweeper returns remaining SOL to `VAULT_PUBKEY` via a `getFeeForMessage`-based reserve, ending a devnet wallet (SOL + one open ATA closed first) at exactly zero lamports. | ⚠️ PRESENT_BEHAVIOR_UNVERIFIED | `bastion/sweeper.py::sweep_session` computes `transfer = balance - fee` where `fee` comes from `get_fee_for_message(confirmed)` (never `FEE_RESERVE_LAMPORTS`), and closes every empty ATA via a hand-encoded `close_account_ix` in the same atomic tx (`bastion/sweeper.py:130-176`). Unit-proven exactly (`tests/unit/test_sweeper.py::test_transfer_amount_is_balance_minus_fee`, `test_one_empty_ata_closed_nonzero_left_untouched`, plus the WR-03 boundary-fix tests). Devnet analog `test_sweep_to_exact_zero_with_ata` exists and collects, but is unexecuted against a real chain (03-04-SUMMARY.md `human_judgment: true`, `status: unknown`). |
| SC4a | Sweep path loads only the session key and `VAULT_PUBKEY` and is structurally incapable of loading the vault secret. | ✓ VERIFIED | `bastion/sweeper.py` has zero `load_vault` references (`grep -c "load_vault" bastion/sweeper.py` == 0) and signs with `Keypair.from_bytes(bytes(session._secret))` only (session key). The AST import-isolation test `tests/unit/test_keystore_vault_isolation.py::test_sweeper_does_not_import_vault` structurally asserts sweeper.py is never among the modules importing `bastion.keystore.vault` — this is a static, non-behavior-dependent check, fully green. `test_only_allowlisted_modules_import_vault` confirms `funder.py` + `vault.py` are the *only* two importers of `bastion.keystore.vault` in the entire `bastion/` tree. |
| SC4b | An injected post-send timeout produces no double-spend. | ⚠️ PRESENT_BEHAVIOR_UNVERIFIED | `bastion/land_check.py::land_check` never rebuilds/re-signs on a null (ambiguous) status — it re-POSTs the identical `signed_b64` blob only (`bastion/land_check.py:84-96`). Exhaustively unit-proven with mocked RPC across 4+ cases including transient-failure and expired-blockhash-resend scenarios (`tests/unit/test_land_check.py`, all green — this is the actual behavioral test satisfying Step 3's "behavior-dependent truth" bar at the unit level). The **real-chain** confirmation that Solana's dedup-by-signature genuinely prevents a double-spend when re-posting against a live cluster (`tests/e2e/test_devnet_fund_sweep.py::test_no_double_spend_on_injected_timeout`) collects cleanly but is unexecuted (03-04-SUMMARY.md `human_judgment: true`, `status: unknown`). |
| SC5 | A swept session's keystore can be retired, and retire refuses to hard-delete when a nonzero token balance remains. | ✓ VERIFIED | `bastion/keystore/session.py::retire()` (lines 185-260) inserts the D-10 guard before `os.remove`: raises `KeystoreRetireError` when any ATA's parsed `tokenAmount.amount` is > 0, leaving the file untouched and the secret un-zeroized. `token_accounts=None` also fails closed (WR-02 fix) unless `token_check_skipped=True` is explicit. Fully unit-proven: `tests/unit/test_session_retire.py` (10 tests, all green) covers refusal-leaves-file, proceed-on-all-zero, proceed-on-empty-list, fail-closed-on-None, and explicit-opt-out paths. This is a deterministic, file-system-observable behavior — no devnet dependency. |

**Score:** 3/6 truths VERIFIED (SC2, SC4a, SC5); 3/6 PRESENT_BEHAVIOR_UNVERIFIED (SC1, SC3, SC4b — code present, wired, and exhaustively mock-verified, but the literal "on devnet" real-chain assertion required by the phase goal has never been observed).

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `bastion/land_check.py::land_check()` | Shared chain-based confirmation loop (D-08/D-09) | ✓ VERIFIED | Exists, substantive (103 lines, full logic incl. CR-01 transport-failure guards), imported and called by both `funder.py:141` and `sweeper.py:175`. |
| `bastion/fund_errors.py` | `FunderCapExceededError`, `FunderInsufficientBalanceError`, `FunderInvalidAmountError` | ✓ VERIFIED | All three classes present, imported and raised in `bastion/funder.py`. |
| `bastion/funder.py::fund_session()` / `fund_session_sync()` | Capped, exact-N vault→session funding | ✓ VERIFIED | Present, substantive, wired (imported by `tests/e2e/test_devnet_fund_sweep.py` and unit tests). Sole additional `bastion.keystore.vault` importer per the AST test. |
| `bastion/rpc/client.py::RpcClient.get_signature_statuses()` | JSON-RPC `getSignatureStatuses` helper | ✓ VERIFIED | `grep -n "def get_signature_statuses" bastion/rpc/client.py` → line 205; calls `self.call("getSignatureStatuses", ...)`, no duplicate retry logic. |
| `bastion/sweeper.py::sweep_session()` / `sweep_session_sync()` / `close_account_ix()` | Exact-zero session→vault sweep | ✓ VERIFIED | Present, substantive (195 lines), wired into unit + e2e tests. Never imports `bastion.keystore.vault`. |
| `bastion/rpc/client.py::RpcClient.get_token_accounts_by_owner()` | jsonParsed SPL token account enumeration | ✓ VERIFIED | Line 218, present and used by `sweeper.py:120`. |
| `bastion/keystore/errors.py::KeystoreRetireError` | Typed D-10 refusal error | ✓ VERIFIED | Present, subclasses `KeystoreError`, raised from `session.py::retire()`. |
| `bastion/keystore/session.py::retire()` extended | `token_accounts` param + D-10 guard | ✓ VERIFIED | Signature extended exactly as specified; guard runs before `os.remove`/`zeroize()`. |
| `tests/unit/test_land_check.py`, `test_funder.py`, `test_sweeper.py`, `test_session_retire.py`, `test_keystore_vault_isolation.py` | Unit coverage | ✓ VERIFIED | All present; combined run: `uv run pytest tests/unit/test_land_check.py tests/unit/test_funder.py tests/unit/test_sweeper.py tests/unit/test_session_retire.py tests/unit/test_keystore_vault_isolation.py -q` → **42 passed**. |
| `tests/e2e/__init__.py`, `tests/e2e/conftest.py`, `tests/e2e/test_devnet_fund_sweep.py` | Opt-in devnet suite | ✓ VERIFIED (present/wired) — ⚠️ UNEXECUTED (behavior) | Files exist, `devnet` marker registered in `pyproject.toml:25`, all 3 tests collect cleanly offline. Never run against a live chain in this environment (no devnet credentials). |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `funder.fund_session` | `load_vault(config)` | direct import | ✓ WIRED | `bastion/funder.py:41,110` |
| `funder.fund_session` | `RpcClient.get_fee_for_message(confirmed)` | `await rpc.get_fee_for_message(..., commitment="confirmed")` | ✓ WIRED | `bastion/funder.py:124-126` |
| `funder.fund_session` | `land_check()` | direct import + call | ✓ WIRED | `bastion/funder.py:42,141` |
| `test_keystore_vault_isolation.ALLOWED_IMPORTERS` | includes `bastion/funder.py` | set literal | ✓ WIRED | `tests/unit/test_keystore_vault_isolation.py:25` |
| `sweep_session` | `get_token_accounts_by_owner` (classify empty vs nonzero) | `await rpc.get_token_accounts_by_owner(...)` | ✓ WIRED | `bastion/sweeper.py:120-125` |
| `sweep_session` | `close_account_ix()` + transfer → `get_fee_for_message(confirmed)` → `land_check()` | direct calls | ✓ WIRED | `bastion/sweeper.py:130-176` |
| sweep destination | `Config.vault_pubkey` (public string only) | `Pubkey.from_string(config.vault_pubkey)` | ✓ WIRED | `bastion/sweeper.py:112`; no alternate-destination parameter exists in `sweep_session`'s signature. |
| `retire(session_or_pubkey, keystore_dir, token_accounts)` | guard on `tokenAmount.amount > 0` before `os.remove` | inline check | ✓ WIRED | `bastion/keystore/session.py:240-253` (guard precedes `os.remove` at line 254-257). |
| `e2e conftest` | `funder.fund_session` / `sweeper.sweep_session` against a live devnet `RpcClient` | fixture composition | ✓ WIRED (offline) | `tests/e2e/conftest.py` composes `devnet_rpc` + `funded_session`; `test_devnet_fund_sweep.py` imports and calls both. Collects cleanly; unexecuted live. |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full non-devnet unit suite is green and network-free | `uv run pytest -q -m "not devnet"` | 130 passed, 1 skipped, 3 deselected, 19.85s | ✓ PASS |
| Phase-3-specific unit modules are green | `uv run pytest tests/unit/test_land_check.py tests/unit/test_funder.py tests/unit/test_sweeper.py tests/unit/test_session_retire.py tests/unit/test_keystore_vault_isolation.py -q` | 42 passed | ✓ PASS |
| RPC helper symbols exist and follow the existing idiom | `grep -n "def get_signature_statuses\|def get_token_accounts_by_owner" bastion/rpc/client.py` | Lines 205, 218 found | ✓ PASS |
| devnet e2e suite collects offline without import/collection error | `uv run pytest -m devnet --collect-only -q tests/e2e/test_devnet_fund_sweep.py` | 3 tests collected | ✓ PASS |
| devnet e2e suite actually passes against a live chain | `uv run pytest -m devnet -q` (with real credentials) | Not run — no devnet RPC/vault credentials in this environment | ? SKIP → routed to Human Verification |

### Anti-Patterns Found

No debt markers (`TBD`/`FIXME`/`XXX`), warning-level cleanup comments (`TODO`/`HACK`), placeholder returns, or hardcoded-empty stub patterns found in any phase-3-modified production file (`bastion/funder.py`, `bastion/sweeper.py`, `bastion/land_check.py`, `bastion/fund_errors.py`, `bastion/keystore/session.py`, `bastion/keystore/errors.py`, `bastion/rpc/client.py`). The one grep hit for the word "placeholder" (`bastion/sweeper.py:137`, referring to a zero-lamport placeholder transfer instruction used to size the fee) is legitimate design content, not a stub marker.

### Code Review Findings

`03-REVIEW.md` (iteration 1) found 1 Critical + 4 Warning issues (CR-01 land_check double-spend-adjacent robustness gap, WR-01 sub-lamport reject, WR-02 retire fail-open-on-None, WR-03 sweep boundary edge, WR-04 retry-hazard documentation). `03-REVIEW-FIX.md` confirms all 5 were fixed, verified against a full `uv run pytest -q -m "not devnet"` pass (130 passed) after every change, and committed atomically. The iteration-2 re-review (`03-REVIEW.md`'s current content) traced all four fund-affecting fixes line-by-line and found them **Sound** with no regressions; only 5 Info-tier items remain (non-blocking, explicitly out of scope per `fix_scope: critical_warning`), matching this task's stated context.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| SESS-02 | 03-01, 03-04 | User can fund a session wallet from the vault with a specified SOL cap | ✓ SATISFIED (unit) / ⚠️ devnet-unexecuted | `fund_session` exact-lamport transfer, unit-proven; devnet delta test exists, unrun. |
| SESS-03 | 03-01 | System refuses to fund when requested cap exceeds MAX_SESSION_CAP | ✓ SATISFIED | `FunderCapExceededError`, zero-RPC-calls unit-proven. |
| SESS-06 | 03-02, 03-04 | User can sweep remaining SOL back to the vault on manual session end | ✓ SATISFIED (unit) / ⚠️ devnet-unexecuted | `sweep_session` exact-zero arithmetic, unit-proven; devnet exact-zero-with-ATA test exists, unrun. |
| SESS-07 | 03-03 | User can retire (remove) a session keystore after it has been swept | ✓ SATISFIED | `retire()` D-10 guard, 10 unit tests green, backward compatible. |
| SEC-02 | 03-01, 03-02 | The vault secret is loaded only for funding; sweeps target VAULT_PUBKEY and require no vault secret | ✓ SATISFIED | AST isolation tests (`test_only_allowlisted_modules_import_vault`, `test_sweeper_does_not_import_vault`) — structural, not behavior-dependent, fully green. |

No orphaned requirements: `.planning/REQUIREMENTS.md`'s traceability table lists exactly SESS-02, SESS-03, SESS-06, SESS-07, SEC-02 against Phase 3, matching the 5 requirement IDs declared across the four PLAN.md frontmatter blocks (03-01: SESS-02/SESS-03/SEC-02; 03-02: SESS-06/SEC-02; 03-03: SESS-07; 03-04: SESS-02/SESS-06). All 5 are marked "Complete" in REQUIREMENTS.md, which is accurate for the unit-verified/structural portions but the SESS-02/SESS-06 "Complete" marking should be read as "logic complete, devnet confirmation pending" per the human-verification items below — this is a documentation nuance, not a gap, since the underlying code and tests are genuinely done.

### Human Verification Required

1. **Run the full opt-in devnet suite**
   **Test:** `uv run pytest -m devnet -q` with `SOLANA_RPC`/`SOLANA_WS` set to devnet endpoints, `VAULT_SECRET` + `VAULT_PUBKEY` set to a real, funded devnet vault (optionally `BASTION_E2E_KEYPAIR` and `BASTION_E2E_MINT` to conserve the 2 req/8h airdrop faucet quota).
   **Expected:** All three tests pass — exact-N fund delta (SESS-02), exact-zero sweep with the ATA closed (SESS-06), and no double-spend after an injected post-send timeout (D-08) — or a documented 429/faucet-exhaustion skip, never a hard failure.
   **Why human:** This verification environment has no live devnet RPC endpoint or funded vault credentials. The phase goal explicitly requires "validated end-to-end on devnet before any mainnet SOL is at risk" — the logic is exhaustively proven correct against mocked RPC responses, but the literal on-chain observation has not happened yet. This is exactly the class of check no static/mocked verification can substitute for.

### Gaps Summary

No code gaps. All artifacts exist, are substantive, are correctly wired, and are exhaustively covered by a green unit test suite (130 passed / 1 skipped / 3 deselected). A prior code review found and fixed 1 critical + 4 warning issues; the fix was independently re-reviewed and confirmed sound with no regressions. The AST-based SEC-02 isolation invariant (funder is the sole additional vault.py importer; sweeper structurally cannot import it) is proven, not just asserted.

The only outstanding item is that three of the five roadmap success criteria explicitly require observation **on devnet** (a real chain), and while the corresponding `tests/e2e/` suite is written, wired, and collects cleanly, it has never been executed against a live RPC endpoint in any environment to date (per 03-04-SUMMARY.md's own `human_judgment: true` disclosure). This is an honest, correctly-flagged coverage gap, not a hidden one — the executing agent did not claim a real-chain pass that didn't happen. Per the phase goal's explicit "validated ... on devnet" language, this phase cannot be marked fully `passed` until a human with devnet credentials runs `uv run pytest -m devnet -q` and confirms a green (or documented skip) result.

---

_Verified: 2026-07-08T03:03:54Z_
_Verifier: Claude (gsd-verifier)_
