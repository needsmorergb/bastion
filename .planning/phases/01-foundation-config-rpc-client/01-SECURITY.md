---
phase: 01-foundation-config-rpc-client
name: Foundation — Config + RPC Client
asvs_level: 1
block_on: high
register_authored_at_plan_time: true
threats_total: 13
threats_closed: 13
threats_open: 0
status: SECURED
verified: 2026-07-07
---

# SECURITY.md — Phase 01 (Foundation — Config + RPC Client)

Retroactive verification that every declared threat mitigation in the
plan-time threat register is genuinely present in the implemented code.
Verification method: read the cited implementation + confirm the cited test
exists and asserts the behavior. ASVS Level 1 (presence-at-cited-boundary).
Implementation files were not modified.

**Result: SECURED.** All 13 threats resolve to CLOSED (10 mitigated + 3
accepted). No open threat at or above the `high` block threshold.
`threats_open: 0`.

Full unit suite re-run during this audit: `uv run pytest tests/unit/ -q`
→ **26 passed, 1 warning** (the warning is the intentional non-secure-scheme
`UserWarning`, itself the T-01-05 evidence).

## Threat Verification

| Threat ID | Category | Severity | Disposition | Status | Evidence |
|-----------|----------|----------|-------------|--------|----------|
| T-01-01 | Information Disclosure | high | mitigate | CLOSED | `.gitignore:5-11` (`.env`, `*.env`, `!.env.example`, `keystore/`, `*.keystore`, `*.key`); `.env.example` (via `git show HEAD:.env.example`) = placeholders only (`PLACEHOLDER_*`, `KEYSTORE_PASSPHRASE=` empty); `git ls-files` confirms only `.env.example` tracked, no `.env`/`*.db` |
| T-01-02 | Denial of Service | low | accept | CLOSED (accepted) | `.gitignore:9` (`keystore/`), `.gitignore:23` (`*.db`); see Accepted Risks Log |
| T-01-SC | Tampering (supply chain) | high | mitigate | CLOSED | `uv.lock` present, 84 `hash = "sha256:"` entries (hash-pinned); `pyproject.toml:6-10,23-28` version-bounded direct deps; `01-RESEARCH.md:99-110` Package Legitimacy Audit — all 6 direct pkgs cross-verified against live PyPI + official GitHub repos/maintainers this session; gate recorded pre-approved in `01-01-SUMMARY.md` Task 1 |
| T-01-03 | Information Disclosure | high | mitigate | CLOSED | `bastion/config.py:68-71` (4 secret fields `field(default="", repr=False)`); `config.py:157-168` `get_passphrase()` uses non-echoing `getpass.getpass`; no `logging`/`print` anywhere in `bastion/` (grep, 0 matches); test `test_config.py:140-159` `test_config_repr_excludes_secrets` asserts secret values absent from `repr`/`str` |
| T-01-04 | Tampering/Elevation | medium | mitigate | CLOSED | `config.py:74-84` `_coerce()` raises `ConfigError` on `TypeError`/`ValueError`; applied to all 4 rails `config.py:113-132`; test `test_config.py:162-169` `test_malformed_rail_fails_loudly` asserts `ConfigError` |
| T-01-05 | Tampering (MITM) | medium | mitigate | CLOSED | `config.py:87-100` `_warn_if_insecure_scheme()` emits `UserWarning` (not fail); called for RPC+WS at `config.py:136-137`; test `test_config.py:172-179` `test_non_secure_endpoint_warns` asserts `pytest.warns(UserWarning)` |
| T-01-06 | Information Disclosure | medium | mitigate | CLOSED | `config.py:157-168` `get_passphrase()` env-first then `getpass` fallback — never required in env; `load_config()` never calls it (non-interactive-safe); tests `test_config.py:85-108` (`test_passphrase_getpass_fallback`, `test_passphrase_from_env_when_set`) |
| T-01-07 | Denial of Service | high | mitigate | CLOSED (+ hardening note) | `client.py:52-93` `_request_with_backoff`: Retry-After-aware (76-81), exp backoff + ±25% jitter fallback (82-84), ~30s cap (`_DEFAULT_MAX_WAIT_S=30.0` line 34), raises `RpcRateLimitError` on budget exceed (86-90); test `test_rpc_client.py:67-84` `test_retry_budget_exhaustion_raises_typed_error`. See Hardening Note H-1 |
| T-01-08 | Tampering | medium | mitigate | CLOSED | `client.py:119-127` typed parse: malformed JSON→`RpcError`, `error` field→`RpcError`, missing `result`→`RpcError`; pagination terminates on empty (169) and short page (173); test `test_rpc_client.py:106-117` `test_get_signatures_terminates_on_short_page` (asserts `call_count == 1`) |
| T-01-09 | Denial of Service | low | accept | CLOSED (accepted) | Short-page terminator present `client.py:169-174`; user-chosen endpoint; see Accepted Risks Log |
| T-01-10 | Information Disclosure | medium | mitigate | CLOSED | `client.py:196-205` `send_raw` contains no logging; no `logging`/`print`/logger calls anywhere in `bastion/` (grep, 0 matches); `errors.py:1-6` mandates message-only errors; `call()` error strings (`client.py:115-127`) carry method name + response error object, never request params/blob |
| T-01-11 | Denial of Service (missed events) | high | mitigate | CLOSED | `ws.py:74-91` `_liveness_monitor` independent sibling task force-closes on `monotonic()-last_seen > liveness_factor*ping_interval` (threshold `ws.py:111`), independent of `ping_timeout`; resubscribe every (re)connect `ws.py:129-130`; `on_gap` fired once per gap `ws.py:121-123` (set at 142/144); tests `test_rpc_ws.py:55/89/120` (`test_detects_silent_drop_via_heartbeat`, `test_resubscribes_after_reconnect`, `test_signals_backfill_needed_on_reconnect`) |
| T-01-12 | Denial of Service | medium | mitigate | CLOSED | `ws.py:118` `async for websocket in connect(uri, ...)` — modern `websockets.asyncio` auto-reconnecting iterator provides bounded exp backoff + jitter (no `websockets.legacy`); documented `ws.py:2-6` |
| T-01-13 | Tampering (MITM) | low | accept | CLOSED (accepted) | Scheme warning in config `config.py:87-100,137` (shares T-01-05); `wss://` default `config.py:31`; see Accepted Risks Log |

## Accepted Risks Log

Entries for `accept`-disposition threats. Each retains a residual-risk
rationale that holds; recorded here to close the disposition.

- **T-01-02 (low) — committed `*.db` / keystore artifacts.** `.gitignore`
  covers `*.db` (line 23) and `keystore/` (line 9) as defense-in-depth. No
  such artifacts exist in the tree at this phase (nothing writes a DB yet).
  Residual: a user could force-add an ignored file. **Accepted** — outside
  Phase 1's control surface; git-ignore is the appropriate rail.
- **T-01-09 (low) — endpoint returns unbounded full pages.** `get_signatures`
  terminates deterministically on a page shorter than `limit`
  (`client.py:169-174`). A pathological endpoint that returned an infinite
  stream of full pages could grow memory. Residual: the RPC endpoint is
  user-chosen and trusted (their own provider/API key); real Solana address
  history is finite, so the short-page terminator fires. **Accepted.**
- **T-01-13 (low) — non-wss endpoint.** Config emits a loud `UserWarning`
  on a non-`wss://` `SOLANA_WS` (`config.py:87-100,137`); the default is
  `wss://` (TLS). Residual: a user who ignores the warning and configures a
  plaintext `ws://` endpoint accepts MITM exposure on public
  subscription/notification data only (no keys or funds traverse the WS —
  domain-blind transport, `ws.py:24-27`). **Accepted.**

## Hardening Notes (non-blocking — not open threats)

- **H-1 (T-01-07, informational).** The retry budget is enforced by
  accumulating `elapsed` wall time against `max_wait_s`. For a `Retry-After`
  header of exactly `0` (or negative), `wait == 0`, so `elapsed` never
  advances and the `elapsed + wait > max_wait_s` guard never trips — a
  hostile/misbehaving endpoint returning `429 + Retry-After: 0` on every
  response would loop without raising `RpcRateLimitError`. This does not
  contradict the declared mitigation for the realistic vectors (positive
  `Retry-After`, and the no-header exponential path, both bounded and the
  latter tested): the bounded-cap + typed-error mechanism is present and the
  cited test passes. It sits on the same trust basis as accepted T-01-09 /
  T-01-13 (the endpoint is the user's own trusted RPC).
  **RESOLVED:** `wait` is now floored to `_MIN_RETRY_WAIT_S = 0.05`
  (`client.py:39`, applied at `client.py:88-89`) so a zero/negative
  `Retry-After` always advances `elapsed` and the `max_wait_s` budget still
  trips with `RpcRateLimitError`. Regression:
  `test_rpc_client.py::test_zero_or_negative_retry_after_cannot_stall_budget`
  (parametrized `0` / `-5`).

## Unregistered Flags

None. No `## Threat Flags` section appears in any of the four Phase 1
SUMMARY files (`01-01`/`01-02`/`01-03`/`01-04`); no new attack surface was
reported by the executor during implementation, and none was found that maps
to no threat ID.

## Non-Custodial Invariant Spot-Check

The load-bearing project invariant (never transmit/log/persist a private
key, seed, or passphrase; RPC/WS transport domain-blind) held across the
audited surface:

- No `logging`/`print`/logger call exists anywhere under `bastion/`
  (grep, 0 matches) — nothing can log a secret at any level.
- Secret-bearing `Config` fields are `repr=False` (`config.py:68-71`);
  passphrase is only ever read via non-echoing `getpass` (`config.py:166-168`).
- `bastion/rpc/client.py` and `bastion/rpc/ws.py` handle only pubkeys,
  public JSON, and (for `send_raw`) an already-signed opaque base64 blob that
  is never logged; neither module imports or touches key/secret material
  (`client.py:1-21`, `ws.py:24-27`).
