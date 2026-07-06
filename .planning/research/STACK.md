# Stack Research

**Domain:** Non-custodial, local-first CLI + long-running monitor for Solana session-wallet isolation and behavioral anomaly detection (Python)
**Researched:** 2026-07-06
**Confidence:** MEDIUM-HIGH (version/API facts cross-verified across official docs + multiple independent sources; a few items — Helius exact undocumented internals, long-term solana-py roadmap — are MEDIUM/LOW and flagged inline)

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| Python | 3.11+ | Runtime | Spec's choice is correct. 3.11+ gives you `asyncio.TaskGroup`/exception groups (cleaner structured concurrency for the monitor's WS+poll fan-out) and meaningfully faster asyncio than 3.10. No reason to go lower; no forcing need for 3.12/3.13 yet given `python-telegram-bot` only requires 3.10+ and `solders`/`cryptography`/`websockets` all support 3.11 comfortably. |
| `solders` | 0.27.x (latest on PyPI) | Keypair generation, `Pubkey`, `Message`/`MessageV0`, `VersionedTransaction`, `SystemProgram` instruction builders, base58 (de)serialization | Rust-backed (via PyO3), actively maintained by kevinheavey, is the primitive layer that `solana-py` itself now re-exports. It's the correct, minimal choice for **key generation and transaction building/signing** — the highest-stakes code path in this project. Confirmed current API (see "Transaction Building Pattern" below). |
| `httpx` | 0.27.x–0.28.x (pin latest 0.2x stable; verify no breaking changes before pin) | Sync **and** async HTTP client for Solana JSON-RPC calls | **Challenges the spec.** Replace `requests` — see "What NOT to Use." `httpx.Client` covers the sync CLI paths (`funder.py`, `sweeper.py` fire one JSON-RPC call and exit); `httpx.AsyncClient` covers the async monitor's polling-backfill calls. One HTTP dependency, one mental model, near-identical API to `requests` so the migration cost is trivial. |
| `websockets` | 16.0 (latest stable) | Persistent WS connection to Helius (`logsSubscribe`/`accountSubscribe`), reconnect/backoff | Confirms the spec. This is the standard asyncio-native WS client for Python. **Important current-API note:** the pre-14.0 "legacy" `websockets.legacy` API is deprecated (removal path through 2030) — build against the modern `websockets.asyncio.client.connect` (or the exported `websockets.connect`) which works as an async iterator that **auto-reconnects on error**; wrap it in your own bounded-exponential-backoff loop (cap ~30s, add jitter) so you control the "signal Monitor to backfill via polling" hook the spec calls for in `rpc.py`. |
| `cryptography` (pyca) | 49.0.0 (latest stable; 50.0.0 is pre-release/dev, do not pin) | Keystore: scrypt KDF + Fernet symmetric encryption | Confirms the spec exactly. This is the only serious, audited, general-purpose crypto library in the Python ecosystem (backed by OpenSSL/Rust). `cryptography.hazmat.primitives.kdf.scrypt.Scrypt` (with `n=2**14` per the spec, `r=8, p=1` are reasonable OWASP-aligned defaults) → derived key → `cryptography.fernet.Fernet` for authenticated encryption of the keypair blob. Nothing to swap here; this is the correct, boring, safe choice. |
| `click` | 8.1.x | CLI entrypoint (`start`/`end`/`list`/`status`/`monitor` subcommands) | Not named in the spec's stack line but required by `cli.py`'s module breakdown. Recommend `click` over `typer` for this project specifically: fewer transitive dependencies (`typer` pulls in `rich` + `shellingham`), extremely mature/stable/widely-audited, and Bastion's supply-chain posture (§10.6 of the spec) rewards minimizing the dependency tree on a fund-moving tool. Plain `argparse` is also viable if you want zero third-party CLI deps — reasonable if the maintainer prioritizes minimal surface over developer ergonomics. |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `sqlite3` (stdlib) | bundled with Python 3.11+ | `store.py` — sessions/transactions/alerts/baselines tables | Confirms the spec's implicit choice (no ORM named, and none is needed). Enable `PRAGMA journal_mode=WAL;` at connection open exactly as the spec's §5 requires. At this project's throughput (one trader, a handful of live sessions, a few writes per detected transaction) synchronous stdlib `sqlite3` calls from within the async monitor are not a bottleneck — a single INSERT/UPDATE is sub-millisecond, well inside the <5s alert-latency budget. Enforce the "single-writer" discipline the spec already states rather than adding a dependency for it. |
| `aiosqlite` | 0.20.x | Optional: fully non-blocking DB access from the asyncio monitor loop | Only reach for this if profiling later shows stdlib `sqlite3` calls (which run synchronously on the event-loop thread) are measurably delaying alert dispatch. Given the tiny per-event write volume here, this is very unlikely — treat as a v2 optimization, not a v1 dependency. |
| `uv` | latest (Astral) | Dev-time dependency resolution + lockfile (`uv.lock`) | 2026's default lockfile tool: single cross-platform lockfile (vs. pip-tools' per-platform `requirements.txt`), ~8-10x faster resolves, auto-maintained via `uv add`/`uv remove`. Use it to generate the hash-pinned lock the spec's §10.6 requires (`uv export --format requirements-txt` with hashes, or ship `uv.lock` directly and require `uv sync --frozen` for reproducible installs). |
| `pip-audit` | latest (PyPA-maintained) | Pre-release dependency vulnerability scan | Run in CI against `pyproject.toml`/lockfile before every tagged release; checks against the PyPI Advisory Database + OSV. Non-negotiable for a fund-moving tool per the spec's supply-chain stance (§10.6). |
| `pytest` + `pytest-asyncio` | latest | Test harness for the TDD-first build order (§8 of spec) | The spec's build sequence is explicitly test-first (roundtrip tests, devnet fund/sweep tests, fixture replay for scoring). `pytest-asyncio` is required because `monitor.py`, the WS client, and the async RPC calls all need `async def test_...` support. |
| `python-dotenv` | latest | Local `.env` loading for `config.py` during development only | Convenience for the maintainer's local dev loop reading `SOLANA_RPC`, `VAULT_SECRET`, etc. Never load `.env` in a way that risks shipping it — the spec already requires `.gitignore` + `.env.example` and a refusal-to-run check if the keystore dir looks cloud-synced; extend that same posture to `.env`. |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| `uv` | venv + dependency + lockfile management | Also usable as the project's venv/Python-version manager (`uv venv`, `uv run`) — replaces needing `pyenv` + `pip-tools` + `virtualenv` separately. |
| `pipx` | End-user installation of the published CLI | This is *distribution*, not dev tooling, but belongs here: once packaged with a `console_scripts` entry point in `pyproject.toml`, `pipx install bastion` gives each user an isolated venv on their machine — no dependency collisions with their trading-terminal Python environment, no `sudo pip install`. This is the standard, current (2026) way to ship a Python CLI to end users. |
| `hatchling` (build backend) | `pyproject.toml` build backend | Lightweight, modern, no legacy `setup.py` needed; pairs cleanly with `uv build`. `setuptools` is an equally valid alternative if you prefer its longer track record — either is fine, just pick one and be explicit in `pyproject.toml`. |
| GitHub Actions + `gh-action-pypi-publish` (v1.11+) | Signed, reproducible release pipeline | Configure **PyPI Trusted Publishing** (OIDC-based, short-lived tokens — no long-lived `PYPI_API_TOKEN` secret sitting in CI) and let the action auto-generate **Sigstore attestations** on publish. This directly satisfies the spec's §10.6/§10.7 requirement for "signed reproducible releases + published checksums" without you having to hand-roll GPG key management (which the industry has been actively moving away from because of key-loss/compromise risk). |

## Installation

```bash
# Core (pyproject.toml dependencies)
uv add solders httpx websockets "cryptography>=49,<50" click

# Supporting (stdlib sqlite3 needs no install; add only if you outgrow it)
uv add python-dotenv
uv add --dev pytest pytest-asyncio pip-audit

# Reproducible, hash-verified install for end users / CI
uv sync --frozen        # installs exactly what's in uv.lock
# or, for a plain-pip consumer:
uv export --format requirements-txt --output-file requirements.txt  # includes hashes
pip install -r requirements.txt --require-hashes

# End-user distribution
pipx install bastion    # once published, isolated venv, console script on PATH
```

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|--------------------------|
| `solders` only (hand-rolled `rpc.py`) | `solana-py` (`solana` on PyPI) | `solana-py` is actively maintained and not deprecated — it's a legitimate choice, and it wraps `httpx` + `websockets` for you (`AsyncClient`, `websocket_api.connect`) with typed request/response models. Use it if you'd rather have typed RPC method wrappers than hand-write JSON-RPC dicts. We recommend against it here specifically because the spec's own module breakdown already wants bespoke retry/backoff/reconnect/backfill logic in `rpc.py` that a wrapper library doesn't buy you much for, and every extra dependency on a fund-moving tool is audit surface (§10.6). If Helius-specific methods (priority-fee estimation, enhanced getters) matter later, raw JSON-RPC via `httpx` handles anything `solana-py` would, since it's not Helius-aware either. |
| `httpx` (sync + async) | `requests` (sync) + `aiohttp` (async) | Only if you have a hard reason to keep the two stacks separate (e.g. an existing large `requests`-based codebase you don't want to touch). For a greenfield project, splitting HTTP into two libraries to cover sync CLI paths and the async monitor is pure duplication; `httpx` covers both with one API. `aiohttp` is a legitimate *pure-async* alternative to `httpx.AsyncClient` and is slightly faster in some high-concurrency benchmarks, but Bastion's monitor talks to exactly one RPC endpoint at a time — the performance delta is irrelevant here, and losing the shared sync/async API isn't worth it. |
| `asyncio` only (no scheduler) | `APScheduler` (3.11.3, actively maintained) | `APScheduler` earns its keep when you have multiple independent cron-like jobs with persistence across restarts (e.g. "run this at 3am daily" style workloads). Bastion's monitor is a single continuous loop (WS subscribe + periodic polling-backfill checks) — that's exactly what `asyncio.create_task` + `asyncio.sleep` in a loop already does natively. Reach for `APScheduler` only if a later version adds several independent scheduled jobs (e.g. periodic baseline recalculation, scheduled digest reports) where its job-store/misfire-handling machinery starts paying for itself. |
| Raw `httpx.post()` to Telegram Bot API / Pushover REST endpoint | `python-telegram-bot` (v22.8) / a Pushover client package | Both target APIs are trivial from the sending side: Telegram's `sendMessage` and Pushover's `messages.json` are each a single POST. A full async framework like `python-telegram-bot` (itself dependent on `httpx`) is justified once Bastion wants to *receive* Telegram input — e.g. a v2 "reply to confirm/deny this sweep from your phone" interactive flow — at which point adopting the library's update-handling/polling machinery is worth it. For v1's one-way outbound alert, it's an unnecessary dependency; a 10-line `httpx.post` wrapper in `alerter.py` is simpler to audit. |
| `uv` for locking | `pip-tools` (`pip-compile`/`pip-sync`) | `pip-tools` is still a fine, narrower-scope choice if you want to stay as close to plain `pip` semantics as possible and don't want a newer, faster-moving tool in your supply chain. It's slower (lockfile generation ~4x slower in benchmarks) and needs per-platform requirements files, but it's had longer to earn trust. Given this project's own emphasis on supply-chain conservatism, this is a legitimate "maintainer's call" — either is defensible; just pick one and hash-pin. |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|--------------|
| `requests` for the async monitor's RPC calls | `requests` has no async story at all — using it inside `monitor.py`'s asyncio event loop means every JSON-RPC call blocks the loop thread, directly working against the spec's own <5s alert-latency target and the WS+polling-backfill design that assumes concurrent operations. This is the one part of the spec's proposed stack that's a genuine poor fit, not just a style preference. | `httpx` (`httpx.AsyncClient` for the monitor, `httpx.Client` for the sync CLI paths) — same author-friendly API, actually async when you need it. |
| `websockets.legacy` API (anything pre-14.0 style) | Explicitly deprecated by the `websockets` maintainers with a removal path scheduled through 2030 — building new reconnect/backoff logic against a deprecated API surface in 2026 is technical debt on day one. | The modern asyncio-based API (`websockets.connect` / `websockets.asyncio.client.connect`), which natively supports auto-reconnect-as-async-iterator. |
| Hand-rolled AES/ChaCha "roll your own" encryption for the keystore, or a lesser-known pure-Python crypto package | The keystore is the single highest-value target in this whole system; anything other than the field-standard, OpenSSL-backed `cryptography` library is an unjustifiable risk for a fund-moving tool where a compromised or subtly-buggy crypto implementation is catastrophic. | `cryptography`'s `Fernet` + `Scrypt` KDF, exactly as the spec proposes. Don't substitute. |
| GPG/PGP-signed releases as the supply-chain integrity mechanism | The industry (including PyPI itself) has been actively moving away from long-lived PGP keys for release signing because of key-loss/compromise risk and poor UX; PyPI even removed GPG signature upload support. | PyPI **Trusted Publishing** (OIDC short-lived tokens via GitHub Actions) + **Sigstore** attestations, which are automatically verifiable and don't require you to safeguard a long-lived signing key yourself. |
| Skipping a lockfile / unpinned `install_requires` | For a tool that generates and moves private keys, an unpinned or hash-less dependency resolution means a compromised transitive dependency (e.g. a poisoned patch release of anything in the `solders`/`cryptography` chain) can silently ship in a user's install. The spec calls this out directly in §10.6. | `uv.lock` (or `pip-tools`-generated hash-pinned `requirements.txt`) + `pip-audit` in CI + `--require-hashes` at install time. |
| A general ORM (SQLAlchemy, etc.) for `store.py` | The schema is four small tables with a thin DAO already specified in the spec (§5); an ORM adds a large dependency and an abstraction layer for no real benefit at this scale, and makes the WAL/single-writer discipline harder to reason about directly. | Stdlib `sqlite3` with hand-written SQL, exactly as the spec's module breakdown implies. |

## Stack Patterns by Variant

**If you keep `requests` for some reason (e.g. maintainer preference, existing snippets to reuse):**
- Restrict it strictly to the synchronous CLI paths (`funder.py`, `sweeper.py`, one-shot `start`/`end` commands) and never import it inside `monitor.py`.
- Still bring in `httpx.AsyncClient` (or `aiohttp`) for the monitor's polling-backfill calls — you'll end up maintaining two HTTP libraries, which is why the single-`httpx` recommendation above is the better default.

**If Helius's enhanced/DAS APIs become load-bearing later (e.g. richer parsed-instruction data instead of hand-parsing raw logs):**
- Helius's enhanced transaction-parsing endpoints are plain REST/JSON-RPC extensions — they don't require a Helius-specific SDK; keep calling them through the same `httpx` client used for standard JSON-RPC. Don't add a Helius Python SDK dependency just for this.

**If v2 adds Ledger hardware-signer support for the vault (per the spec's §9/deferred list):**
- This will need a Ledger transport library (typically `ledgerblue`/`ledger-solana` style packages in the Python ecosystem) — treat it as net-new research at that milestone rather than pre-selecting now; the vendor landscape here changes independently of the rest of this stack.

## Version Compatibility

| Package A | Compatible With | Notes |
|-----------|------------------|-------|
| `solders` 0.27.x | Python 3.11+ | `solders` requires only Python >=3.7, so no conflict with the project's 3.11+ floor; the floor is set by the project's own async/tooling needs, not by `solders`. |
| `cryptography` 49.0.0 | Python 3.11+ | Current stable line; avoid pinning to the `50.0.0-dev*` pre-release branch seen on the docs site — that's unreleased development, not a shipping version. |
| `websockets` 16.0 | asyncio (stdlib) | Requires building against the modern (non-legacy) API; if any tutorial/snippet you copy uses `websockets.legacy.client.connect`, treat it as outdated and port it to the current `websockets.connect` async-iterator pattern. |
| `python-telegram-bot` 22.8 (if adopted later) | `httpx` 0.27–0.29 | Pin your own `httpx` version inside that compatible range if you add this library later, to avoid a resolver conflict with the `httpx` version pinned for RPC calls. |
| `pipx` | any packaged CLI with a `pyproject.toml` console-script entry point | Requires the package to be a proper installable distribution (not a loose script) — make sure `[project.scripts]` is set in `pyproject.toml` before publishing. |

## Sources

- https://pypi.org/project/solders/ , https://kevinheavey.github.io/solders/ — solders version (0.27.1) and API confirmation. Confidence: MEDIUM (web search cross-checked against official docs site).
- https://github.com/michaelhly/solana-py , https://solana.com/docs/clients/community/python — solana-py maintenance status and relationship to solders. Confidence: MEDIUM.
- https://www.helius.dev/docs/billing/rate-limits , https://www.helius.dev/pricing — Helius free-tier RPC/WS rate limits (10 RPS, 1 sendTransaction/sec, 5 concurrent WS connections, 1000 subs/connection, 1M credits/month). Confidence: MEDIUM (direct fetch of official docs, single-source).
- https://solana.com/docs/rpc/http/getsignaturesforaddress , https://www.helius.dev/docs/api-reference/rpc/http/getsignaturesforaddress — `getSignaturesForAddress` 1000-signature cap and `before`/`until` pagination. Confidence: MEDIUM.
- https://pypi.org/project/cryptography/ , https://cryptography.io/en/latest/fernet/ — cryptography 49.0.0 stable / 50.0.0-dev, Fernet + Scrypt KDF availability. Confidence: MEDIUM.
- https://pypi.org/project/websockets/ , https://websockets.readthedocs.io/en/stable/ — websockets 16.0, legacy API deprecation, reconnect-as-async-iterator pattern. Confidence: MEDIUM.
- https://oxylabs.io/blog/httpx-vs-requests-vs-aiohttp , https://www.speakeasy.com/blog/python-http-clients-requests-vs-httpx-vs-aiohttp — httpx vs requests vs aiohttp tradeoffs. Confidence: MEDIUM.
- https://pypi.org/project/python-telegram-bot/ , https://docs.python-telegram-bot.org/ — python-telegram-bot v22.8, asyncio-native, httpx dependency. Confidence: MEDIUM.
- Pushover API client landscape (github.com/Thibauth/python-pushover and others) — no official SDK; REST is a single POST endpoint. Confidence: MEDIUM.
- https://apscheduler.readthedocs.io/en/3.x/ , https://pypi.org/project/APScheduler/ — APScheduler 3.11.3, asyncio scheduler use case. Confidence: MEDIUM.
- https://docs.astral.sh/uv/pip/compile/ , https://realpython.com/uv-vs-pip/ — uv vs pip-tools lockfile comparison and performance. Confidence: MEDIUM.
- https://pypa.github.io/pipx/ , https://packaging.python.org/en/latest/guides/creating-command-line-tools/ — pipx distribution model and pyproject.toml console-script requirement. Confidence: MEDIUM.
- https://docs.pypi.org/trusted-publishers/security-model/ , https://github.com/pypa/pip-audit — PyPI Trusted Publishing (OIDC), Sigstore attestations, pip-audit for pre-release vulnerability scanning. Confidence: MEDIUM.

---
*Stack research for: Bastion — non-custodial Solana session-wallet isolation + anomaly-detection CLI*
*Researched: 2026-07-06*
