# Pitfalls Research

**Domain:** Non-custodial Solana session-wallet CLI — fund-moving transactions, local key custody, real-time on-chain monitoring, LLM-assisted anomaly scoring, OSS distribution of a fund-moving tool
**Researched:** 2026-07-06
**Confidence:** HIGH (Solana protocol mechanics, rent/blockhash economics, RPC pagination behavior, and the solana-web3.js supply-chain incident are all independently verifiable against Solana docs, Helius docs, and public incident write-ups); MEDIUM on Bastion-specific scoring/LLM pitfalls (reasoned from spec + general anomaly-detection and prompt-injection literature, not a project-specific post-mortem)

This file is organized by the five pitfall families in the research question: **fund-moving**, **key-handling**, **monitoring reliability**, **scoring**, and **distribution**. Each critical pitfall follows the template format; family membership is noted in the title.

---

## Critical Pitfalls

### Pitfall 1: Unsweepable session wallet from fee/rent reserve miscalculation [fund-moving]

**What goes wrong:**
`sweeper.py` computes `send_amount = balance - FEE_RESERVE_LAMPORTS` and sends the session wallet's SOL back to the vault. If `FEE_RESERVE_LAMPORTS` is a flat guess (e.g. "5000 lamports for the fee") rather than fee + rent-exemption-aware, one of two failures happens: (a) the transfer amount leaves the *session account itself* below the ~0.00089088 SOL rent-exempt minimum, so instead of closing to zero the account sits there under-funded and rent-eviction-adjacent (Solana no longer collects rent from established accounts, but many wallets/RPCs still treat sub-rent-exempt non-zero balances as "dust" that's awkward to reclaim); or (b) if the session wallet also holds an SPL token account (ATA) from an in-terminal trade, the sweep computes only against the *native SOL* balance and ignores that ~0.00203928 SOL per ATA is rent locked in a separate account the sweep never touches — so the "swept" wallet silently still holds value in unclosed ATAs, and the user believes the session is fully drained-to-zero when it isn't.

**Why it happens:**
Fee estimation on Solana is deceptively simple 99% of the time (5000 lamports/signature is the common case) which trains developers to hardcode it, until priority fees, multiple signers, or an ATA-closing instruction change the real cost. Rent exemption and "fee" are conceptually different budgets (one is protocol-level minimum balance, one is compute/signature cost) but get lumped into a single `FEE_RESERVE` constant in a v1 build.

**How to avoid:**
- Compute the reserve as `estimated_tx_fee (from getFeeForMessage / simulate) + 0 for the session account itself since it's being emptied to zero, not kept rent-exempt` — the session account can legitimately go to exactly 0 lamports (closing it entirely), it does NOT need to stay above the rent-exempt minimum once it's being retired. The bug to avoid is leaving it at some *nonzero-but-below-rent-exempt* dust amount by under- or over-subtracting.
- Sweep sequence: close any SPL token accounts first (recovers their rent to the session's SOL balance, per spec §9 "empty ATAs closed to reclaim rent"), then sweep 100% minus the actual simulated fee for that final transaction, targeting an exact-zero close.
- Never assume flat 5000 lamports; call `getFeeForMessage` (or simulate) against the actual constructed transaction, and pad by a small safety margin for priority-fee volatility, not a guess.
- Write a devnet test that sweeps a wallet holding both SOL and an SPL token balance and asserts the session ends at exactly 0 lamports with no open token accounts.

**Warning signs:**
- Session wallets in `store.py` marked `status=swept` that still show nonzero `getBalance` on-chain.
- Any hardcoded lamport constant named `FEE_RESERVE` used without a corresponding `getFeeForMessage` call nearby in the diff.
- Devnet sweep tests that only ever fund/sweep pure-SOL wallets (never one with a leftover ATA).

**Phase to address:** `sweeper.py` build phase (spec build order step 3), verified on devnet before mainnet.

---

### Pitfall 2: Blockhash expiry leaves a fund-moving transaction in an unknown state [fund-moving]

**What goes wrong:**
A `recentBlockhash` is only valid for ~150 blocks (~60–90 seconds at Solana's ~400–600ms slot time). If `funder.py` or `sweeper.py` builds and signs a transaction, then the RPC send is slow, retried, or queued behind a rate-limit backoff, the blockhash can expire before the network processes it — and the transaction fails with `TransactionExpiredBlockheightExceededError`. Worse, the caller often can't tell from the client side whether the transaction actually landed before expiring (network partition, RPC lag) — leading to either a false "it failed, resend" (risking a double-send if it actually landed) or a false "it's still pending" (leaving a fund operation dangling with no audit resolution).

**Why it happens:**
Developers coming from EVM chains expect nonces with indefinite validity; Solana's expiring-blockhash model is unfamiliar. Fetching the blockhash with `finalized` commitment (rather than `confirmed`) shrinks the usable window further (finalized lags confirmed by ~13+ seconds), which is easy to get backwards if "finalized = safer" intuition is applied without understanding the expiry tradeoff.

**How to avoid:**
- Fetch blockhash with `confirmed` commitment (the official Solana recommendation), not `finalized`, to maximize the usable window.
- After sending, poll for the transaction's status using `getSignatureStatuses` up to the blockhash's `lastValidBlockHeight`; if it expires unconfirmed, only then is it safe to consider it dropped and re-buildable with a fresh blockhash.
- Never resend/retry with a *new* transaction (new blockhash) while the *old* signature's status is still unknown — check `getSignatureStatuses` for the original signature first, since Solana transactions are idempotent by signature but a re-signed retry with a different blockhash produces a **different signature**, so idempotency dedup by signature won't catch it. This is the direct fund-moving link to Pitfall 4 (double-spend via retry).
- Audit-log every fund/sweep attempt with its blockhash + lastValidBlockHeight + final resolved status (`landed` / `expired-unconfirmed` / `expired-confirmed-later`), so a human can reconcile from the JSONL if automated resolution is ambiguous.

**Warning signs:**
- Funder/sweeper code that calls `send_raw` in a bare retry loop without checking `getSignatureStatuses` on the *previous* attempt's signature first.
- Audit log entries showing a fund/sweep event with no terminal status (stuck at "sent").
- Any use of `commitment=finalized` when fetching blockhash for a transaction about to be built.

**Phase to address:** `rpc.py` (send/confirm helpers) and `funder.py`/`sweeper.py`, spec build order steps 1 and 3.

---

### Pitfall 3: Acting on "confirmed" state that a reorg later invalidates [fund-moving]

**What goes wrong:**
`confirmed` commitment means a supermajority has voted on the block, but it is not yet irreversible — a rare but real cluster reorg can still drop a `confirmed` transaction. If the Scoring Engine or Monitor treats a `confirmed` sighting of a drain transaction as ground truth for baseline updates or as the trigger to fire an irreversible action (like alerting "session burned" or writing a `baseline` update assuming the sink transaction happened), and it later reorgs out, the audit trail and any downstream automated reaction (e.g., an auto-sweep it triggered) is now based on a transaction that no longer exists on-chain.

**Why it happens:**
The spec's <5s alert-latency target pushes toward using `confirmed` (or even `processed`) for speed, which is correct for *alerting fast* but risky if the same signal is reused to make an *irreversible* decision (like marking a session permanently "burned" and retiring its keystore) before the improbable-but-real reorg window passes.

**How to avoid:**
- Use `confirmed` for the fast path (alerting, scoring, WATCH/CRITICAL classification) — this is the right tradeoff per the <5s target, and Solana reorgs at `confirmed` level are rare enough that optimizing for detection speed is correct.
- Reserve any *irreversible, hard-to-undo* action (permanently retiring a keystore, closing out the audit record as final) for after `finalized` confirmation, or make those actions idempotent/reversible enough that a reorg reconciliation is cheap.
- On monitor restart/reconciliation (§6), re-verify any transaction that was scored at `confirmed` but not yet `finalized` against current chain state before trusting it for baseline math.

**Warning signs:**
- `store.py` baseline updates or `status=burned` transitions that never get revisited once `finalized` status is available.
- No reconciliation pass distinguishing "confirmed, still pending finalization" from "finalized" in the transactions table.

**Phase to address:** `scoring.py` and `monitor.py`, spec build order steps 5–6.

---

### Pitfall 4: Retry logic causes a double-spend / double-sweep [fund-moving]

**What goes wrong:**
A naive retry-on-timeout in `funder.py` or `sweeper.py` re-signs and resends a fresh transaction (new blockhash → new signature) after a network hiccup, without first confirming whether the original attempt actually landed. If it did land, the retry is a second, real fund movement — either double-funding a session (minor, just wastes vault SOL) or double-sweeping (attempting to sweep an already-empty wallet, which fails safely) or, most dangerously, in a partial-fill scenario where a fee-reserve calculation error (Pitfall 1) leaves *some* dust behind and a "retry the sweep" mistakenly treats the dust balance as a full balance to resend against a wrong reserve assumption.

**Why it happens:**
"Just retry on failure" is a reasonable default for read-only RPC calls but actively dangerous for a fund-moving instruction, because a network-layer failure (timeout, connection reset) does **not** imply the instruction failed — it may have landed and the response was simply lost. Idempotency-by-signature (which the spec correctly calls out for monitoring, §6) does not automatically apply to *retries that generate a new signature*.

**How to avoid:**
- Treat every fund/sweep operation as: build → sign → record `{signature, blockhash, lastValidBlockHeight, status=pending}` in the audit log *before* sending → send → poll `getSignatureStatuses` until `landed`, `expired`, or `lastValidBlockHeight` passed → only then decide whether a *new* attempt (new blockhash, new signature) is warranted.
- Before any retry, re-check current balance against expected pre/post-transaction balance — if the balance already reflects the intended transfer, skip the retry (this is the practical, chain-verifiable idempotency check that survives a blockhash/signature change).
- Never retry inside the same function call without this landed-check; make "did it land" a first-class return value distinct from "did the RPC call itself error."

**Warning signs:**
- Any `try/except` around `send_raw` that immediately loops back to rebuild-and-resend on exception, without a balance or `getSignatureStatuses` check in between.
- Audit log showing two fund or sweep entries for the same session within a few seconds of each other.

**Phase to address:** `funder.py` + `sweeper.py`, spec build order step 3; regression-tested on devnet with injected RPC timeouts.

---

### Pitfall 5: Dust and unclosed ATAs strand value outside the SOL-only v1 sweep [fund-moving]

**What goes wrong:**
Per spec (Out of Scope), v1 sweep is SOL-only; SPL positions are meant to be "swapped back in-terminal first" and ATAs closed to reclaim rent — but this is a *process* expectation, not something the code enforces. If a session ends (manually or via anomaly auto-sweep) while it still holds SPL tokens, `sweep()` moves the native SOL and leaves the token balance and its rent-locked ATA behind, unrecovered and effectively orphaned (the keystore may be retired per spec, meaning the only way to later recover that token balance is if the keystore retirement is soft/recoverable, not a hard-delete). If keystore retirement is a hard delete, this is a full loss of whatever was in the ATA.

**Why it happens:**
Auto-sweep is precisely the path most likely to fire *mid-trade*, i.e. exactly when the session is most likely to be holding non-SOL positions — the SOL-only design assumption ("user swaps back before ending session") is weakest exactly when an anomaly-triggered auto-sweep needs it most.

**How to avoid:**
- Sweep flow should attempt to close known ATAs (reclaiming their rent to SOL) as part of `sweep()`, even in v1 — this doesn't require the "risky" part (auto-liquidating token *value*, which stays out of scope), only closing *empty or account-closable* token accounts to reclaim rent; if an ATA still holds a nonzero token balance, closing it isn't possible without transferring the tokens somewhere first (also out of scope) — so the correct v1 behavior is: sweep SOL, attempt to close zero-balance ATAs, and **explicitly flag in the alert/audit log "N SPL positions left on this wallet, keystore retained until manually resolved"** rather than silently retiring the keystore.
- Do not hard-delete keystores for sessions ending with a nonzero non-SOL balance; retire (mark inactive) but keep recoverable until the user confirms manual resolution.

**Warning signs:**
- `retire(pubkey)` unconditionally deletes/wipes the keystore file regardless of whether the session's token balances are all zero.
- Auto-sweep test fixtures that only cover pure-SOL sessions, never a mid-trade session holding an open position.

**Phase to address:** `sweeper.py` (spec build order step 3) for the close-ATA logic; `keystore.retire()` semantics revisited when `monitor.py`/auto-sweep is wired (step 6), since that's the path most likely to trigger mid-trade.

---

### Pitfall 6: Plaintext key material reaches disk, logs, swap, or crash dumps [key-handling]

**What goes wrong:**
Even with `keystore.py` correctly encrypting keys at rest, plaintext key bytes exist transiently in process memory every time a keypair is loaded/used to sign — and Python makes it easy to leak that transient plaintext: an uncaught exception's traceback printed to a log file can include local variables (keypair bytes) if verbose tracebacks or a debugger-style exception hook is enabled; `pdb`/`breakpoint()` left in during dev can dump locals; a core dump on crash captures the full process memory including decrypted keys; OS-level swap can page out memory containing decrypted key bytes to disk if the machine is under memory pressure; and naive `print(kp)` / `repr(Keypair)` during debugging can put the plaintext into terminal scrollback or a redirected log file.

**Why it happens:**
"Never write a plaintext key to disk" is usually interpreted narrowly as "don't call `open(file, 'w').write(secret_bytes)`" — but plaintext key material has many indirect paths to persistent storage (logs, crash dumps, swap, shell scrollback, clipboard) that a naive interpretation misses, especially in a hobby/solo-maintainer codebase where debug logging and print-statement debugging are common.

**How to avoid:**
- Never log any object that could `repr()`/`str()` to secret bytes; explicitly implement `__repr__`/`__str__` overrides on any secret-wrapping type to return a redacted placeholder, so an accidental `logger.debug(f"{keypair}")` can't leak.
- Set exception hooks / logging config to NOT include local variables in tracebacks (Python's default traceback doesn't, but many "rich" logging/debug libraries do — audit before adopting one).
- Zero out or scope decrypted key bytes to the smallest possible lifetime (load → sign → discard reference) rather than holding a `Keypair` object for the life of a long-running monitor process where avoidable.
- Where the OS supports it, consider disabling swap for the process or using `mlock`-style memory locking for the keystore-decryption module (acceptable to defer, but disable core dumps at minimum: `ulimit -c 0` / equivalent, documented in setup instructions).
- Grep the codebase pre-release for any `print(`, `logging.*(`, or f-string that includes a variable named like `kp`, `keypair`, `secret`, `passphrase`, `private_key`.

**Warning signs:**
- Any log line, error message, or audit-log JSONL entry that contains a base58 string matching the shape of a 32/64-byte secret key.
- Debug/dev logging configured at DEBUG level in default config with no redaction filter.
- Core dumps enabled by default on the dev/deploy machine.

**Phase to address:** `keystore.py` (spec build order step 2) — this is exactly what spec's own invariant #1 ("no plaintext key ever hits disk or logs") demands as a *test*, not a comment; extend the test to include a "no secret-shaped string in any log output" grep-based regression test run against the full test suite's captured stdout/stderr.

---

### Pitfall 7: Vault secret or passphrase leaks via shell history or process environment [key-handling]

**What goes wrong:**
The spec's v1 design loads `VAULT_SECRET` from an env var and accepts passphrases via env or `getpass`. Two common leak paths: (1) a user sets `export VAULT_SECRET=...` interactively or passes `VAULT_SECRET=... bastion start` inline, both of which land in shell history (`.bash_history`/`.zsh_history`) in plaintext, persisting long after the session and often synced via dotfile-sync tools; (2) any env var is visible to every child process the CLI spawns and, on Linux, readable by other processes owned by the same user via `/proc/<pid>/environ` — so a compromised trading terminal (the exact threat model Bastion defends against) running as the *same OS user* could read `VAULT_SECRET` directly out of the Bastion process's environment if they share a machine/user account, which is a realistic setup for a solo trader running both the terminal and Bastion locally.

**Why it happens:**
Env vars are the path of least resistance for local secrets and are explicitly sanctioned in the spec as the v1 approach (with the acknowledgment it's "dangerous guidance for strangers"). The shell-history leak is a UX/documentation gap, not a code gap — nothing in the code prevents a user from typing the secret directly instead of sourcing it from a file.

**How to avoid:**
- Document (and where possible enforce/warn) that `VAULT_SECRET` should be sourced from a `chmod 600` file loaded via `source .env` mechanisms that don't echo to history, never typed inline or `export`ed interactively — and that shells should have `HISTCONTROL=ignorespace`/`ignorespace`-prefixed commands as a documented mitigation.
- Prefer reading `VAULT_SECRET` from a dedicated 0600 file path over a raw env var where possible, since a file is at least excluded from shell history and process-listing tools by default (though still visible via `/proc/<pid>/environ` if read via env either way — so a file-read approach is strictly better here).
- Explicitly note the same-machine/same-OS-user threat model gap in documentation: Bastion's isolation guarantee is about the *trading session's on-chain blast radius*, not about OS-level process isolation between the trading terminal and Bastion itself — if they run as the same OS user, a sufficiently privileged terminal compromise could still read Bastion's process environment. This is a genuine, not fully closable, gap for v1; flag it rather than imply it's covered.
- Ship the Ledger/hardware-signer path (spec §10.3, deferred to distribution) sooner rather than later for the vault specifically, since the vault secret is the highest-value target and Ledger removes it from the OS environment entirely.

**Warning signs:**
- Any documentation or onboarding script that shows `export VAULT_SECRET=abc123...` as a copy-pasteable example.
- No `.bash_history`/shell-history guidance in the README's setup section.

**Phase to address:** `config.py` (spec build order step 1, for the file-based secret loading pattern) and the distribution/documentation gate (§10.3) for the Ledger migration and history-hygiene warnings.

---

### Pitfall 8: Weak KDF parameters make the keystore crackable offline [key-handling]

**What goes wrong:**
Spec specifies scrypt (n=2^14) → Fernet for keystore encryption. `n=2^14` (16384) is scrypt's commonly-cited *minimum interactive* work factor from the original 2009 scrypt paper — reasonable for a login prompt in 2009, but by 2026 standards (given commodity GPU/ASIC advances) it is on the low end for protecting a high-value secret like a vault key against an offline brute-force attack if the keystore file itself is ever exfiltrated (e.g., via the cloud-sync pitfall below, or simple laptop theft). A weak KDF doesn't matter while the passphrase is strong and the file never leaves the machine — but the whole point of a KDF work factor is defense-in-depth for exactly the scenario where the file *does* leak.

**Why it happens:**
`n=2^14` is the textbook "this is scrypt, it's secure" number developers copy from tutorials without revisiting it against current hardware-cost-of-attack guidance (OWASP and recent scrypt guidance recommend N=2^17 or higher, i.e. 131072, for high-value secrets where the interactive-login latency tradeoff is less important than for a web login).

**How to avoid:**
- Bump to at least `n=2^17` (or the current OWASP-recommended scrypt parameters) for the vault keystore specifically, since it's unlocked infrequently (only at funding time per spec §10.3) so the added latency (still well under a second on modern hardware) is a non-issue; session keystores can use a lighter setting if unlocked more frequently, but consider using the stronger setting everywhere for simplicity, since keystore unlock is not a hot path.
- Make the KDF parameters configurable/versioned in the keystore file format (`{pubkey, salt, kdf_params, ciphertext, created}`) so they can be strengthened later without breaking old keystores (store the params used, don't hardcode assumption of a fixed `n` at decrypt time).
- Add a test asserting the configured `n` is at or above the current recommended floor, so a future refactor can't silently regress it.

**Warning signs:**
- Keystore file format has no stored KDF parameter field (implies a global hardcoded constant that can silently drift or never be revisited).
- No comment/test anchoring the "why n=2^14" decision to a specific threat-model justification.

**Phase to address:** `keystore.py`, spec build order step 2 — cheap to get right before any real funds touch it; expensive to migrate later if the format doesn't version its KDF params.

---

### Pitfall 9: Key material reaches the network layer via the LLM scoring path [key-handling / distribution]

**What goes wrong:**
This is the single scenario the spec itself calls "the sharpest architectural risk" (§10.2). If the scoring module that hands parsed transaction data to an LLM (locally or via a hosted backend) is built without a hard module boundary, it's trivially easy for a future refactor to accidentally pass more context than intended — e.g., passing the full `session_ctx` object (which might include a reference to the keystore path, or worse, a decrypted `Keypair` held in memory for convenience) into the LLM prompt-building function, because "just pass the whole context object" is the path of least resistance when building a prompt template. Even without direct key bytes, indirect leakage is possible: a verbose LLM prompt that includes the keystore file *path* combined with a compromised or overly chatty hosted LLM logging provider could reconstruct enough to matter, or a stack trace bubbling up from the scoring module on error could include local variables from a caller that had key material in scope.

**Why it happens:**
LLM prompt-construction code tends to be written for correctness/expressiveness ("give the model everything it might need") rather than for least-privilege; the natural Python pattern of passing a single `session_ctx` dict/object through several layers of function calls makes it easy for a field to ride along into the LLM-facing function without anyone reviewing exactly what crossed the boundary.

**How to avoid:**
- Build the LLM-facing module so that its *only* possible input type is a plain, already-serialized structure containing exclusively public on-chain fields (signature, parsed instructions, pubkeys, amounts, timestamps) — never pass `session_ctx`, `Keypair`, or any keystore-path-bearing object into it, even if unused fields would be "harmless" in principle. Enforce this with a type at the function signature level (a narrow `PublicTxSummary` dataclass), not just discipline.
- Add exactly the test the spec calls for: assert that no code path can construct the LLM payload from an object that has keystore access — e.g., a static/import-boundary test that the LLM-scoring module never imports `keystore.py`, plus a runtime test that feeds a `session_ctx` containing a canary secret value and asserts it never appears in the constructed LLM payload.
- If the LLM backend is hosted (per spec's ZERØ-style example), treat the network call itself as an untrusted boundary: log (locally, redacted) exactly what payload was sent for each scored transaction, so a post-hoc audit can verify no secret-shaped data ever crossed it.

**Warning signs:**
- The LLM-scoring function signature accepts a broad `session_ctx` or `Session` object rather than a narrow, explicitly-public-fields struct.
- No import-boundary or canary test for this specific invariant (the spec explicitly asks for one — its absence in the actual test suite is the warning sign).

**Phase to address:** `scoring.py` (spec build order step 5) — build the module boundary and the canary test *before* wiring any hosted LLM call, not after.

---

### Pitfall 10: Keystore directory silently lives in a cloud-synced folder [key-handling]

**What goes wrong:**
If `KEYSTORE_DIR` defaults to (or a user points it at) a path under Dropbox, OneDrive, iCloud Drive, or Google Drive's sync folder, the encrypted keystore files get uploaded to a third party automatically — and worse, most cloud-sync clients also maintain local *version history* and sometimes temp/conflict copies, so even deleting a keystore locally doesn't guarantee it's gone from the cloud provider's history. While the keystore contents are encrypted (scrypt→Fernet), this still meaningfully weakens the security model: it turns an offline-only brute-force target into one an attacker (or a compelled cloud provider, or a cloud-account compromise) can obtain remotely and attack at leisure, and it directly contradicts the spec's own non-custodial/no-network-egress design intent by moving key material off the local machine via a side channel the tool doesn't control.

**Why it happens:**
Users on Windows/Mac commonly have their whole home directory or `Documents` folder backed up by OneDrive/iCloud by default (often enabled by the OS itself, not a conscious user choice), so a keystore dir like `~/Documents/bastion/keystore` can be cloud-synced without the user realizing it.

**How to avoid:**
- Implement the spec's own requirement literally: at startup, detect common cloud-sync folder markers (path contains `Dropbox`, `OneDrive`, `Google Drive`, `iCloud Drive`/`CloudStorage`, or the dir has a sync-client extended attribute/placeholder-file marker on Windows/macOS) and refuse to run, with a clear error pointing at how to relocate `KEYSTORE_DIR`.
- Default `KEYSTORE_DIR` to a path outside commonly-synced locations (e.g., an XDG-style app-data dir, not `~/Documents`) so the safe path is also the path of least resistance.
- Document that even with detection, a user could still manually move/copy a keystore file into a synced folder later — detection at startup only catches the configured dir, not ad-hoc copies, so pair the technical check with an explicit warning in docs.

**Warning signs:**
- `KEYSTORE_DIR` default value points at a path under the user's home/Documents without any cloud-sync check.
- No startup-time check exists at all (this is explicitly listed as an Active requirement in PROJECT.md — its absence is the direct warning sign).

**Phase to address:** `config.py`/`keystore.py` (spec build order steps 1–2), verified with a specific test before v1 ships even for personal use.

---

### Pitfall 11: WebSocket drops silently blind the monitor [monitoring reliability]

**What goes wrong:**
`logsSubscribe` over a WebSocket is not a durable subscription — RPC providers (including Helius) can drop the connection without a clean close frame, idle-timeout it, or fail to signal subscription errors back through the client library at all (a documented gap in Solana's own web3.js ecosystem: subscribe failures can be silently swallowed with no `onError` callback firing). If Bastion's monitor doesn't actively detect "I haven't received an expected heartbeat/message in N seconds" and instead purely relies on the WebSocket library's close-event callback, a silent drop leaves the monitor believing it's watching a session that is, in reality, unmonitored — during exactly the window an attacker could be draining it. This is the single most dangerous monitoring-reliability failure because it's invisible: no error is thrown, nothing crashes, the process just stops observing.

**Why it happens:**
WebSocket libraries commonly only expose "connection closed" and "error" events, both of which assume the failure is loud; the more insidious failure (idle NAT timeout, a proxy silently discarding packets) never fires either — a "silent socket isn't a healthy socket," in the industry phrase, but code that only reacts to explicit close/error events won't know that.

**How to avoid:**
- Implement an active heartbeat: track the timestamp of the last message (of any kind, including subscription confirmations) received on the socket; if no message (or no successful `ping`/`pong`) arrives within a threshold (e.g., 2x the expected quiet period), proactively tear down and reconnect rather than waiting for a close event.
- On every reconnect (whether triggered by a clean close, an error, or the heartbeat timeout), re-subscribe to every active session's pubkey from the in-memory subscription list (subscriptions do not survive a reconnect) and immediately trigger a polling backfill via `getSignaturesForAddress` since the last-seen signature for that session, before trusting the new subscription alone.
- Log (to the audit trail, not just stdout) every reconnect event with the gap duration, so post-hoc review can see exactly how long any session went unmonitored — this is the single most important reliability metric for this tool.
- Test this specifically: a test harness that forcibly kills the WS connection mid-stream (no close frame) and asserts the monitor detects the gap within the heartbeat threshold and backfills correctly.

**Warning signs:**
- Reconnect logic that only triggers on the WS library's `onclose`/`onerror` events, with no independent liveness timer.
- No "reconnect gap duration" field in the audit log.
- Manual testing that only ever tests clean disconnects (killing the RPC-side process), never a silent black-hole (e.g., blocking the port with a firewall rule) which is the failure mode that actually matters.

**Phase to address:** `rpc.py` (WS client, spec build order step 1) for the heartbeat/reconnect mechanics; `monitor.py` (step 6) for the resubscribe + backfill orchestration and its specific test.

---

### Pitfall 12: getSignaturesForAddress pagination gaps during backfill miss transactions [monitoring reliability]

**What goes wrong:**
The polling-backfill safety net (spec §6) calls `getSignaturesForAddress` to catch up after a reconnect or restart, using `until=<last_seen_signature>` to page backward. Two concrete failure modes: (1) the API caps results at 1000 per call, so backfilling after a long outage or a very active session requires correct `before`-cursor pagination across multiple calls — a naive single-call backfill silently truncates at 1000 and misses anything older; (2) there is a documented Solana RPC bug where querying with `before`/`until` set to neighboring signatures can return unexpected/incomplete results around the boundary, meaning even "correct" pagination logic can occasionally skip a signature right at a page boundary — an edge case that matters enormously here because the one skipped transaction could be the drain.

**Why it happens:**
Cursor-based pagination against `before`/`until` looks straightforward from the docs, but the 1000-item cap and the boundary-edge-case bug are the kind of detail that only surfaces under real load (a very active session, or a long outage), which a happy-path devnet test won't exercise.

**How to avoid:**
- Implement backfill as a loop: `until=last_seen_sig`, page with `before=<oldest sig from previous page>` until a page returns fewer than the requested `limit` (signalling no more results), rather than assuming one call suffices.
- After backfilling, reconcile by cross-checking the current on-chain balance against the sum of scored transaction deltas since last-seen; if they don't match, that's a strong forcing signal that a signature was missed somewhere in the pagination (even without pinpointing which), and should raise a WATCH-level "reconciliation mismatch" alert rather than silently trusting the backfill.
- Treat the boundary-edge-case bug as a reason to overlap pages slightly (re-fetch the last couple of signatures from the prior page as a sanity check) rather than trusting exact adjacency at page boundaries.

**Warning signs:**
- Backfill code that makes exactly one `getSignaturesForAddress` call regardless of how many transactions might be pending.
- No balance-reconciliation check after backfill completes.
- No test simulating an outage long enough to produce >1000 missed signatures.

**Phase to address:** `rpc.py` (pagination helper, step 1) and `monitor.py` (backfill-on-restart orchestration + reconciliation check, step 6).

---

### Pitfall 13: Non-idempotent re-scoring causes duplicate alerts or duplicate auto-sweeps [monitoring reliability]

**What goes wrong:**
Because backfill necessarily re-fetches signatures that may already have been seen (overlap is a deliberate safety margin, per Pitfall 12's mitigation), the same transaction can enter the scoring pipeline more than once. If `score()` and its downstream actions (alert, auto-sweep) aren't strictly deduped on signature *before* the action fires (not just before storage), a single real anomaly can trigger two Telegram alerts (merely annoying) or — far worse — two auto-sweep attempts. The second sweep attempt against an already-emptied wallet should fail harmlessly, but only if the sweeper correctly handles "wallet already at zero" as a no-op rather than erroring in a way that, say, retries with a stale balance assumption (tying back into Pitfall 4).

**Why it happens:**
"Dedupe on signature" (spec §6) is easy to implement at the storage layer (`INSERT OR IGNORE` into `transactions`) but the action-triggering logic (alert, sweep) is sometimes wired to fire *before* the storage-layer dedupe check, or in a separate code path that doesn't share the same dedupe gate — especially if scoring and alerting are invoked as a chain of callbacks rather than a single transactional step.

**How to avoid:**
- Make the dedupe check the *first* gate in the pipeline, before scoring even runs, not just before storage: `if store.has_seen(sig): return` as literally the first line of the per-transaction handler.
- Make the sweep action itself idempotent regardless of the above: sweeping an already-empty (or below-fee-reserve) wallet should be a detected no-op with a log line, never an error path that triggers a retry loop.
- Test explicitly: feed the same signature through the monitor's handler twice (simulating a backfill overlap) and assert exactly one alert and at most one sweep attempt (with the second being a clean no-op).

**Warning signs:**
- Dedupe logic exists only as a SQL `INSERT OR IGNORE`/unique constraint with no corresponding early-return in the handler function.
- Sweep function has no explicit "already empty" branch.

**Phase to address:** `monitor.py` (orchestration loop, step 6) and `sweeper.py` (idempotent-no-op case, step 3).

---

### Pitfall 14: Helius free-tier rate limits throttle the monitor exactly when it's under the most load [monitoring reliability]

**What goes wrong:**
Free-tier Helius has both a monthly credit cap and a requests-per-second cap. The failure mode isn't steady-state usage (a handful of sessions polling occasionally is trivial load) — it's *bursty* load exactly during an incident: a CRITICAL-level anomaly triggers rapid `getTransaction` fetches for several new signatures in a tight window, plus the reconciliation/backfill logic from Pitfall 12 firing its own burst of `getSignaturesForAddress` calls, plus (if `--armed`) the sweep's own `getFeeForMessage`/`getLatestBlockhash`/`send_raw`/status-poll calls — all landing in the same few seconds. If the 429 backoff (spec's `rpc.py`, already noted as "reuse the pattern already proven") backs off *before* the sweep-critical calls complete, the very moment detection succeeds is the moment RPC throttling delays the containment action, directly undermining the <5s alert-latency target and the race against the drainer.

**Why it happens:**
Rate limits are usually tuned/tested against steady background polling, not against the correlated burst that a real incident naturally produces (detection, backfill, and sweep all firing near-simultaneously) — the exact moment the system needs to be *fastest* is the moment it's most likely to hit a rate limit.

**How to avoid:**
- Prioritize RPC calls: give sweep-path calls (fee estimate, blockhash, send, status-poll) priority over reconciliation/backfill calls when both are contending for RPC budget during an incident — e.g., a lightweight in-process priority queue rather than treating all RPC calls as equal-priority FIFO.
- Keep the backoff's max-retry latency bounded and known (document the worst-case added latency against the <5s target) rather than an unbounded exponential backoff that could silently blow the SLA during exactly a real incident.
- Track and alert on rate-limit-driven delay in the audit log (a `429` count during an incident should itself be a visible signal, not just an invisible retry).
- Size the free-tier's RPS budget against the worst-case correlated burst (detection + backfill + sweep for N concurrent sessions) during design, not just against average load, and document the free tier's known ceiling as an explicit assumption to revisit if it's ever exceeded in practice.

**Warning signs:**
- A single shared rate-limit/backoff wrapper with no priority distinction between "routine polling" and "sweep-critical" calls.
- No metric/log line correlating alert latency with concurrent RPC call volume.

**Phase to address:** `rpc.py` (step 1, priority-aware backoff) and revisited at `monitor.py` (step 6) once the full incident-response call pattern exists to load-test against.

---

### Pitfall 15: False-positive auto-sweep mid-trade [scoring]

**What goes wrong:**
`--armed` auto-sweep fires the moment a WATCH/CRITICAL threshold is crossed. Legitimate trading behavior can look like the very patterns the rules watch for: a fast series of swaps during a volatile market (velocity spike), a quick buy-then-sell that turns out to be a loss because the market moved (round-trip loss), or trading into a genuinely new pool/counterparty the baseline hasn't seen yet (new-counterparty sink) are all things a real, non-compromised trader legitimately does. If the rule thresholds are tuned only against the one "golden" drain fixture (the 5YEQ churn) and one "golden" clean day, without a broader library of legitimate-but-aggressive trading sessions, the system will auto-sweep a real trade in progress — which is not just a false alarm, it's an involuntary liquidation that can realize a loss on its own (selling out of a position at a bad moment) and destroys user trust in the tool immediately (the exact opposite of its purpose).

**Why it happens:**
The spec's TDD approach (build scoring against the 5YEQ churn as the CRITICAL fixture and one clean day as the OK fixture) is right as a starting point but is only two points in a much larger space of legitimate trading styles (scalping, sniping new launches, martingale-style DCA that looks like a "realized-loss burst") — a rule set that cleanly separates two fixtures can still have a high false-positive rate against the full distribution of real trading behavior it will actually see.

**How to avoid:**
- Before enabling `--armed` by default in any session, require a WATCH-then-confirm flow for borderline cases rather than an instant CRITICAL-triggered sweep: e.g., WATCH alerts require no action (just visibility), and even CRITICAL could have a short grace/confirmation window (a few seconds, consistent with the <5s target being about *alerting*, not necessarily instant unattended action) unless the pattern is unambiguous (matches the drain fixture's specific signature combination, e.g. velocity + new-counterparty-sink + round-trip-loss *together*, not any single rule alone).
- Require **multiple independent rule signals** to co-occur before auto-sweep fires (the spec's own framing — "any one is noise; a cluster... is the signal" — should be enforced as a hard AND/threshold-sum requirement for the auto-sweep trigger specifically, even if a single strong signal is enough for a WATCH-level alert).
- Build a broader fixture library before recommending `--armed` for real use: deliberately aggressive-but-legitimate trading sessions (sniping, fast scalping, a big real loss on a bad trade) as explicit "must stay OK" regression fixtures alongside the drain fixture.
- Make the false-positive cost visible and reversible where possible: log exactly which rules fired and their weights on every sweep, and treat any user-reported false-positive as a required fixture addition before the thresholds are considered stable.

**Warning signs:**
- Threshold-sum logic that lets a single high-weight rule alone cross the auto-sweep threshold.
- Test suite with only the two golden fixtures and no "aggressive-but-legitimate" counter-fixtures.
- No mechanism to record/replay a real false-positive as a new regression fixture after it happens.

**Phase to address:** `scoring.py` (step 5) for the fixture library and threshold design; revisit before recommending `--armed` as a real-use default (this is a "personal mainnet use" gate, distinct from the stranger-distribution gate).

---

### Pitfall 16: Baseline poisoning if the attacker acts early in the session [scoring]

**What goes wrong:**
The per-session behavioral baseline is "seeded from the wallet's own early-session activity" (spec §4). If a session is compromised from the very start — e.g., a malicious trading terminal that was already hostile before the user ever made a legitimate trade through it, or a phished bot session where the attacker's automation starts acting within the same window used to build the baseline — the baseline itself gets built from attacker behavior. Every subsequent rule that keys on "deviation from baseline" is now blind to an attacker whose behavior *is* the baseline; the exact rule the spec calls the core insight ("signed by you is meaningless when the session is the attacker... detection keys on deviation from a per-session behavioral baseline") is defeated at its root if the baseline-seeding window itself isn't safe from the same threat.

**Why it happens:**
A rolling baseline needs *some* warm-up data, and "the session's own early activity" is the only data available for a brand-new session (there's no prior history to fall back on) — this is a structural tension, not a simple bug: the same mechanism that makes the baseline personal (rather than a one-size-fits-all absolute threshold) is what makes it poisonable if compromise happens at t=0.

**How to avoid:**
- Don't rely solely on the session's own early activity for the baseline; seed it (per spec's own wording, "and your historical norms") from the *user's* longer-term trading profile across prior sessions, not just the current session's first few minutes — a new session should inherit priors from the vault owner's established patterns, with the current session's activity refining (not solely defining) the baseline.
- Apply the absolute, cap-relative rules (velocity spike, realized-loss burst as % of cap, new-counterparty sink) even during the baseline warm-up window itself — these don't require a mature baseline to be meaningful (a 7-tx-in-60s velocity spike is suspicious on minute one just as much as hour three), so the warm-up gap should only weaken the *relative/deviation* rules, not disable detection entirely.
- Treat the first N minutes/transactions of a new session as inherently higher-scrutiny (not lower), since it's both the baseline-poisoning-vulnerable window and (per the threat model) a session could be compromised before its first legitimate trade ever happens.

**Warning signs:**
- Scoring logic that effectively no-ops or heavily discounts all rules until the baseline has "enough" data points.
- No cross-session historical-profile input into a new session's baseline — every session starts from a completely blank slate.

**Phase to address:** `scoring.py` (step 5), specifically the baseline-seeding design — needs explicit fixtures for "compromised from t=0" alongside the mid-session-compromise fixture (5YEQ churn), since they exercise different weaknesses.

---

### Pitfall 17: LLM layer treated as more authoritative than its actual role, or manipulable via on-chain data [scoring]

**What goes wrong:**
Two related risks around the LLM pass. First (already explicitly guarded against in the spec's Out of Scope and Key Decisions — good), a future maintainer or contributor could be tempted to let the LLM's "confirm/deny" silently gate the sweep for convenience (e.g., "the LLM said this looks fine, skip the sweep it would have otherwise triggered") which reintroduces exactly the black-box-decides-when-to-move-money risk the architecture is designed to avoid — this needs continued enforcement, not just a one-time design decision. Second, and less obviously guarded: the LLM's input includes *parsed on-chain instruction data*, which is attacker-influenced in a compromised session — a sufficiently motivated attacker who anticipates being scored could craft transaction memos, program logs, or instruction data designed to read as "textbook legitimate arbitrage" to a language model (a prompt-injection-adjacent risk specific to feeding untrusted on-chain data into an LLM prompt), potentially suppressing the LLM's "this looks like a drain" framing even while the underlying rule-based verdict (which doesn't reason in natural language and isn't as easily gamed) still fires correctly.

**Why it happens:**
LLM outputs read as confident, well-reasoned natural language, which creates pressure (especially under time constraints, or from a maintainer wanting to reduce alert fatigue) to trust them more than their designed role warrants; and on-chain data is exactly the kind of "external untrusted input reaching a prompt" pattern that prompt-injection concerns apply to, even though it's not literally a chat message.

**How to avoid:**
- Keep the rules-first architecture exactly as decided: rules alone are sufficient to trigger WATCH/CRITICAL and (if armed) sweep; the LLM's output is *never* read anywhere in the code path that decides whether to sweep, only in the alert message shown to the human. Enforce this with a test: verify the sweep-trigger function's inputs don't include any LLM-derived field.
- Treat the LLM's explanation as informational only, and design the alert message so a "the LLM says this looks fine" framing can never override or soften a rule-based CRITICAL classification shown to the user — e.g., always show the rule-based verdict first and prominently, LLM explanation as a secondary annotation.
- Don't feed raw, attacker-influenced free-text fields (memo fields, arbitrary program log strings) into the LLM prompt without labeling them clearly as untrusted/quoted data in the prompt structure (basic prompt-injection hygiene), and don't let anything the LLM outputs be parsed as a structured "command" the code then executes.

**Warning signs:**
- Any code path where an LLM response field feeds into the sweep/alert-severity decision rather than purely into the display message.
- LLM prompt-building code that interpolates raw on-chain memo/log strings directly into an instruction-following prompt without delimiting them as data.

**Phase to address:** `scoring.py` (step 5) — this is a design-invariant enforcement point, worth its own explicit test alongside the egress-boundary test from Pitfall 9.

---

### Pitfall 18: Alert latency loses the race to the drainer [scoring / monitoring reliability]

**What goes wrong:**
The <5s alert-latency target (confirmation → push) is aspirational against a threat model where the golden fixture shows 7+ transactions in one minute — meaning a fast, automated drainer can execute several more drain transactions within the very window Bastion is still detecting the first one. Even a "perfect" detection at t=2s doesn't help if the human on the other end of the Telegram alert takes 30+ seconds to notice and react manually — and the whole reason `--armed` auto-sweep exists is to remove the human from that loop, but auto-sweep only helps if it fires fast enough to beat the *next* drain transaction, not just the first one. If any part of the pipeline (RPC fetch → parse → score → LLM pass if triggered → sweep-build → sweep-send → confirm) has unbounded or unaudited latency (e.g., the LLM call itself, which can take 1-3+ seconds against a hosted API and is squarely in the CRITICAL-alert critical path if scoring is architected as fully sequential), the tool can correctly detect a drain and still lose most of the capped balance before containment completes.

**Why it happens:**
Sequential architectures (fetch → parse → score → LLM → alert/sweep) are the natural way to write this code, but if the LLM call sits in the *sweep-triggering* path rather than being decoupled (spec says LLM should never gate the sweep, per Pitfall 17 — but if it's implemented as a blocking step *before* the sweep call regardless of whether it gates it, it still adds latency to the critical path even if its answer is ignored for the sweep decision).

**How to avoid:**
- Architect the pipeline so the sweep decision (rule-based verdict crossing threshold + `armed`) can fire immediately after the rules pass, in parallel with (not blocked on) the LLM explanation call — the LLM's output enriches the alert message whenever it arrives, but the sweep and the initial "CRITICAL — sweeping" alert should not wait for it.
- Measure and log per-stage latency (fetch, parse, score, sweep-build, sweep-confirm) on every CRITICAL event, so the <5s target is continuously verified against real incidents, not just assumed from initial testing.
- Recognize and document the honest limit: even a well-optimized pipeline cannot make auto-sweep instantaneous (it's still bounded by Solana confirmation time for the sweep transaction itself, ~1-2s minimum at `confirmed`) — the real target is "beat most of the drain," not "prevent any loss," and this should be communicated to users (ties to Pitfall 20's "you're safe now" framing risk) rather than implied as a hard guarantee.

**Warning signs:**
- No per-stage latency instrumentation/logging on the CRITICAL path.
- LLM call implemented as a blocking `await`/synchronous call ahead of the sweep trigger in the code, even if its result isn't used to gate the sweep decision.

**Phase to address:** `monitor.py` (step 6, pipeline architecture/parallelism) and `scoring.py` (step 5, decoupling LLM from the critical timing path) — verify with a timed synthetic-stream test replaying the 5YEQ fixture and asserting sweep dispatch latency, not just correctness.

---

### Pitfall 19: Supply-chain compromise of a poisoned release — the tool itself becomes the drainer [distribution]

**What goes wrong:**
This is not hypothetical for this exact ecosystem: in December 2024, `@solana/web3.js` versions 1.95.6–1.95.7 were backdoored after a maintainer's npm publish credentials were phished, injecting code that exfiltrated private keys via disguised Cloudflare-header traffic, netting attackers over $190,000 before the compromised versions were pulled. A Python fund-moving CLI with a `solders`/`cryptography` dependency chain is exactly the same shape of target: a compromised maintainer account (phishing, stolen 2FA, or a socially-engineered co-maintainer add), a typosquatted PyPI package name close to the real one, or a compromised transitive dependency of `solders`/`cryptography`/`websockets` could all inject key-exfiltrating code that looks, to a casual reviewer, like a normal patch release — and because Bastion's entire premise is "trust this tool with your keys," a poisoned release of Bastion itself (or one of its core dependencies) is a worst-case, self-defeating outcome.

**Why it happens:**
Open-source publishing pipelines are frequently the weakest link precisely because they're optimized for developer convenience (an npm/PyPI account with publish rights, a CI pipeline with a long-lived token) rather than treated with the security posture of a system that moves money — exactly the mismatch the spec's §10.6 already calls out ("protect the publishing pipeline like it holds funds — because effectively it does").

**How to avoid:**
- Signed, reproducible releases with published checksums (spec §10.6) — verified before any user upgrades, not just documented as a nice-to-have.
- Pin and hash-verify all dependencies (lockfile with hashes, e.g. `pip-compile --generate-hashes` or `uv.lock`/`poetry.lock` with hash pinning), so a compromised transitive dependency publishing a new version doesn't silently get pulled in on the next install.
- Enable 2FA (hardware-key-based, not SMS) on every account with publish rights to PyPI/GitHub for this project, and prefer a minimal, audited set of maintainers with publish access over convenience-driven broad access.
- Treat any dependency upgrade to `solders`, `cryptography`, or `websockets` as a reviewed event (diff the changelog, verify the release matches the tagged source) before bumping, rather than auto-upgrading on `pip install -U`.
- Consider vendoring or at least deeply pinning the exact commit/version of the cryptography-adjacent dependencies rather than tracking `latest` even within a semver range, given how narrow the compromise window was in the web3.js incident (~5 hours) — automatic minor-version bumps in CI would have caught that exact window.

**Warning signs:**
- No lockfile with hash verification in the repo.
- CI/release pipeline uses long-lived, broadly-scoped publish tokens rather than short-lived/scoped credentials.
- Dependency versions specified as open ranges (`>=x.y`) rather than pinned/hash-locked.

**Phase to address:** Distribution/pre-release gate (spec §10.6–10.7), but the lockfile/hash-pinning habit should start at project setup (spec build order step 1, `config.py`/environment setup) rather than being retrofitted right before a public release.

---

### Pitfall 20: Custody/regulatory line creep — becoming custodial without noticing [distribution]

**What goes wrong:**
The non-custodial posture (spec §10.1, §10.5) is a binary property, not a spectrum — but architecture tends to drift toward convenience in ways that quietly cross the line: a "helpful" hosted scoring backend that, for debugging or feature convenience, ends up logging more of the transaction payload than strictly public data (edging toward Pitfall 9's boundary in a way that also has regulatory weight, not just security weight); a future "sync your sessions across devices" feature that needs *some* server-side state and is tempting to add once a hosted LLM backend already exists anyway; or fee collection implemented as a small skim routed through a wallet the maintainer controls inside the sweep transaction itself (rather than fully out-of-band) because it's technically simpler to bundle. Any of these — even well-intentioned — moves the project across the FinCEN non-custodial/money-transmitter distinction the spec explicitly relies on as its safe harbor.

**Why it happens:**
Feature requests and monetization pressure accumulate gradually, and each individual addition can seem like a small, reasonable convenience in isolation ("just log a bit more for debugging," "just route the fee through this address since we're already building the transaction") without anyone stepping back to ask whether it crosses the custodial/non-custodial line as a whole.

**How to avoid:**
- Treat the three explicit lines from spec §10.5 (never custody/touch user keys; never host a service in the fund path; never route fees through maintainer-controlled wallets inside a user's transaction flow) as hard architectural constraints checked at every feature-addition decision, not just at initial design time — add them as a standing checklist item in any future roadmap/phase review.
- Any hosted component (even just the LLM scoring backend) should be scoped, documented, and reviewed specifically against "does this receive anything beyond public on-chain fields" every time its interface changes, not just once at initial build.
- Keep any monetization mechanism (license, subscription, donation) structurally separate from the transaction-building code path — no fee logic should ever live inside `funder.py`/`sweeper.py`.
- Get crypto counsel review (already planned per spec §10.5/§10.7) specifically re-triggered any time a new feature touches hosting, data collection, or monetization, not just once before the first stranger release.

**Warning signs:**
- A new feature PR that adds a server-side component "just for this one feature" without an explicit non-custodial impact review.
- Any fee-routing logic appearing inside the same function that builds a user's fund-moving transaction.

**Phase to address:** Ongoing architectural discipline from day one (spec build order step 1 onward), formally re-gated at the distribution pre-release checklist (§10.7) and any future milestone that adds hosted features.

---

### Pitfall 21: "You're safe now" framing creates a false sense of security [distribution]

**What goes wrong:**
Bastion's actual guarantee is probabilistic and bounded ("lost everything" → "lost the cap," and even that assumes detection/sweep wins the race per Pitfall 18) — not absolute safety. If onboarding copy, CLI output, or marketing framing implies "your funds are now protected" or "safe to trade" without prominent caveats (alert-only by default won't stop anything without `--armed`; auto-sweep can still lose the full cap if it loses the latency race; false negatives are possible; the OS-level same-user gap from Pitfall 7 is real), users may fund larger session caps than they'd otherwise risk, disable manual vigilance entirely, or treat a WATCH alert as "handled" when it actually requires their attention. This is exactly the liability and trust failure mode the spec already flags (§10.4) — but framing risk shows up continuously in UX copy choices (success messages, alert wording, README claims), not just in a single disclaimer document, so it has to be enforced at every user-facing string, not just once in a NOTICE file.

**Why it happens:**
Reassuring language is simply better marketing copy and better UX in the moment ("Session started — you're protected!" reads better than "Session started — capped at 0.5 SOL, monitored, not guaranteed") — the pull toward confident, simple messaging works against the honesty the spec explicitly calls for.

**How to avoid:**
- Default CLI/alert copy to precise, bounded language: "capped at X SOL," "alert-only — no automatic action will be taken" (when not armed), "sweep attempted — see audit log for outcome" (never "you're safe," "protected," or "secured" as standalone claims).
- Any UAT/copy review pass (whenever CLI or alert message strings are touched) should include a check against the spec §10.4 framing constraint, treated like a lint rule, not a one-time doc.
- Surface the tool's actual limitations prominently at `start` time (not buried in a README): the cap is the maximum loss *only if* detection and sweep succeed in time; false negatives are possible; alert-only by default takes no automatic action.
- Keep the NOTICE/disclaimer (spec §10.4) as the legal backstop, but treat the day-to-day CLI/alert copy as the primary place this pitfall actually manifests, since that's what users read repeatedly, not the NOTICE file read once at install.

**Warning signs:**
- CLI success/status messages using words like "safe," "protected," "secured" without a qualifying cap/limitation statement in the same message.
- README or marketing copy that leads with "never lose your funds again" style claims.

**Phase to address:** `cli.py`/`alerter.py` message copy (spec build order steps 7–8) and the distribution pre-release gate (§10.4/§10.7) for a full copy audit before any public release.

---

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|-----------------|-----------------|
| Flat `FEE_RESERVE_LAMPORTS` constant instead of `getFeeForMessage`-based estimate | Simpler sweep code, ships faster | Unsweepable dust, failed sweeps under priority-fee volatility (Pitfall 1) | Never past devnet validation — must be fixed before any mainnet cap |
| Raw env var for `VAULT_SECRET` instead of Ledger/hardware signer | No hardware dependency for solo dev use | Shell-history/process-env leak risk, dangerous guidance if copied by strangers (Pitfall 7) | Acceptable for personal, small-cap mainnet use only; must be replaced/de-emphasized before stranger distribution |
| Sequential fetch→score→LLM→sweep pipeline instead of decoupled/parallel | Simplest possible `monitor.py` implementation | Added latency in the exact path racing a drainer (Pitfall 18) | Acceptable for initial devnet correctness testing; must be revisited before mainnet arming |
| Two-fixture TDD (5YEQ churn + one clean day) as the entire scoring test suite | Fast to build, matches spec's stated golden fixtures | High false-positive rate against real trading diversity (Pitfall 15) | Acceptable to start scoring development; must grow before recommending `--armed` for real use |
| Single-call `getSignaturesForAddress` backfill (no pagination loop) | Simpler backfill code initially | Silent truncation on long outages/high-volume sessions (Pitfall 12) | Never — pagination loop is cheap to implement correctly from the start |
| Hard-delete keystore on any session `end`/auto-sweep | Simpler retirement logic | Silent loss of any stranded SPL token value (Pitfall 5) | Acceptable only if session is verified all-zero-balance (SOL and all token accounts) before retiring |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|-----------------|-------------------|
| Helius RPC (JSON-RPC) | Treating rate-limit backoff as uniform-priority FIFO, so sweep-critical calls queue behind routine polling during an incident (Pitfall 14) | Priority-aware backoff/queue: sweep-path calls (fee, blockhash, send, status) preempt reconciliation/backfill calls |
| Helius WebSocket (`logsSubscribe`) | Relying only on `onclose`/`onerror` to detect a dead connection (Pitfall 11) | Active heartbeat/liveness timer independent of library close/error events; forced reconnect + resubscribe + backfill on timeout |
| `getSignaturesForAddress` | Single call assumed to return the full backlog after an outage (Pitfall 12) | Cursor-loop pagination (`before=<oldest sig>`) until a short page signals end-of-data; reconcile against balance afterward |
| Telegram Bot API (Alerter) | Using the same Telegram account/bot for both trading-session-adjacent notifications and the out-of-band alert, defeating the "separate identity" invariant | Provision a dedicated bot + chat under a distinct Telegram identity, never reused for anything session-adjacent (spec invariant #4) |
| Hosted LLM backend (scoring explain/confirm pass) | Passing the full session context object into the prompt-building function "for convenience" (Pitfall 9) | Narrow, explicitly-public-fields struct as the only possible input type to the LLM-facing module; import-boundary + canary test |
| PyPI / dependency installs (`solders`, `cryptography`, `websockets`) | Open version ranges (`>=x.y`) auto-pulling a compromised patch release (Pitfall 19) | Hash-pinned lockfile; manual review of changelogs before bumping crypto-adjacent dependencies |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Rate-limit budget sized for average load, not correlated incident bursts | Sweep/alert delayed by 429 backoff exactly during a CRITICAL event | Reserve/prioritize RPC budget for sweep-path calls; measure worst-case burst (detect + backfill + sweep) against free-tier RPS cap | First real drain incident, or any session count high enough that concurrent polling + one incident's burst exceeds free-tier RPS |
| Sequential (non-parallel) score→LLM→sweep pipeline | Alert-to-push latency creeps above the 5s target as LLM call latency varies | Decouple LLM explain/confirm from the sweep-trigger critical path; instrument per-stage latency | As soon as a hosted LLM backend is added with real network latency (not a mocked/local call) |
| Single-writer SQLite (`store.py`) under concurrent monitor + CLI access | Lock contention/`database is locked` errors if `status`/`list` CLI commands run while the monitor is mid-write | WAL mode (spec already specifies this) + short transactions; keep monitor as the sole writer, CLI as read-only where possible | Multiple concurrent sessions plus frequent CLI polling from the same user during an active incident |
| Growing `baselines`/`transactions` tables scanned in full for deviation checks | Scoring latency creeps up as a long-running session accumulates transaction history | Bound baseline computation to a rolling window (e.g., last N transactions or last T minutes), not a full-table scan per score call | Long-running sessions (many hours) with high trade frequency |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Logging/`repr()`-ing any object that can surface decrypted key bytes | Plaintext key exposure via logs/terminal scrollback (Pitfall 6) | Redacted `__repr__`/`__str__` on all secret-wrapping types; grep-based test for secret-shaped strings in captured test output |
| Flat/unversioned KDF parameters in the keystore format | Offline brute-force feasibility if a keystore file ever leaks (Pitfall 8) | Store KDF params in the keystore file itself; use current-recommended scrypt cost (≥2^17) for the vault keystore specifically |
| No cloud-sync detection on `KEYSTORE_DIR` | Encrypted keystore uploaded to a third party via OS-level sync defaults (Pitfall 10) | Startup check for sync-folder markers (Dropbox/OneDrive/iCloud/Google Drive paths, sync placeholder attributes); refuse to run if detected |
| Broad `session_ctx`/`Keypair` object passed into the LLM-facing scoring function | Key material or keystore-path leakage into a network-bound LLM call (Pitfall 9) | Narrow public-fields-only struct as the LLM module's sole input type; import-boundary test forbidding `keystore.py` import in the scoring/LLM module |
| Raw on-chain memo/log strings interpolated directly into an LLM prompt | Prompt-injection-style manipulation of the LLM's explanation/confirm output by an attacker crafting on-chain data (Pitfall 17) | Delimit/label untrusted on-chain text fields explicitly as data in the prompt; never let LLM output drive the sweep decision |
| Long-lived, broadly-scoped PyPI/GitHub publish credentials | Poisoned release via phished maintainer credentials (Pitfall 19, precedented by the Dec 2024 `@solana/web3.js` incident) | Hardware-key 2FA on all publish-capable accounts; signed reproducible releases; minimal maintainer set with publish access |

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-------------------|
| Reassuring copy ("you're protected," "safe to trade") in CLI/alert output | False sense of security; users risk larger caps or disable vigilance (Pitfall 21) | Precise, bounded language everywhere: state the cap, the armed/alert-only status, and that detection/sweep can still lose the race |
| Copy-pasteable `export VAULT_SECRET=...` examples in docs/onboarding | Secret lands in shell history indefinitely (Pitfall 7) | Show file-based secret loading (0600 file + `source`) as the documented pattern; never an inline/exported example |
| Silent keystore hard-delete on session end | Users discover stranded SPL token value is unrecoverable only after the fact (Pitfall 5) | Refuse to hard-retire a keystore with nonzero token balances; surface an explicit "N positions left, resolve manually" message |
| WATCH-level alerts indistinguishable in urgency from CRITICAL in the notification text | Alert fatigue, or under-reaction to a real CRITICAL because WATCH noise trained the user to ignore alerts | Visually/textually distinct severity formatting; reserve urgent phrasing and channel-priority for CRITICAL only |

## "Looks Done But Isn't" Checklist

- [ ] **Sweeper:** Often missing a proper fee estimate (uses a flat constant instead of `getFeeForMessage`/simulation) — verify a devnet sweep against a wallet holding both SOL and an SPL token ends at exactly 0 lamports with no open token accounts (Pitfall 1, 5).
- [ ] **Retry/idempotency:** Often missing a "did the previous attempt land" check before any retry — verify by injecting a network timeout after a successful send and asserting no double-spend/double-sweep occurs (Pitfall 4).
- [ ] **WebSocket reconnect:** Often missing an active heartbeat (only reacts to `onclose`/`onerror`) — verify by silently black-holing the WS connection (not closing it) and asserting the monitor detects the gap and backfills within the heartbeat threshold (Pitfall 11).
- [ ] **Backfill/reconciliation:** Often missing pagination beyond a single `getSignaturesForAddress` call and missing a post-backfill balance reconciliation check — verify with a simulated outage producing >1000 pending signatures (Pitfall 12).
- [ ] **Keystore invariants:** Often "tested" only via the encrypt/decrypt roundtrip, missing the no-secret-in-logs and cloud-sync-refusal checks — verify with a grep-based test over captured test-suite output and a synthetic cloud-sync-path startup test (Pitfall 6, 10).
- [ ] **LLM egress boundary:** Often implemented as "the scoring module doesn't currently pass secrets" (true today, unenforced tomorrow) rather than a structural/import-boundary guarantee — verify with an import-boundary test (LLM module never imports `keystore.py`) and a canary-secret runtime test (Pitfall 9).
- [ ] **Auto-sweep threshold:** Often tuned against only the two golden fixtures — verify against a broader "aggressive-but-legitimate trading" fixture set before recommending `--armed` for real use (Pitfall 15).
- [ ] **Alert latency:** Often "meets the target" in a quiet synthetic test but untested under the LLM-call-in-critical-path and rate-limit-burst conditions that actually occur during an incident — verify with a timed replay of the 5YEQ fixture measured end-to-end including sweep confirmation (Pitfall 14, 18).
- [ ] **Release/dependency pinning:** Often deferred to "right before public release" — verify a hash-pinned lockfile exists from early in the build, not retrofitted late (Pitfall 19).

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|----------------|-----------------|
| Unsweepable dust left after a fee-miscalculated sweep (Pitfall 1) | LOW | Manually construct and send a follow-up transfer for the exact remaining lamports using the session's still-available keystore (before retirement); add a regression fixture once identified |
| Double-spend/double-sweep from a naive retry (Pitfall 4) | LOW–MEDIUM | Reconcile via audit log + on-chain balance history; if a real double-transfer occurred, funds are still recoverable (they went to the vault, not an attacker) — the cost is confusion/audit-trail cleanup, not fund loss, as long as sweeps always target `VAULT_PUBKEY` |
| WebSocket silent gap causing a missed window (Pitfall 11) | HIGH | If funds were lost during the blind window, recovery is generally not possible (this is the core risk the tool exists to prevent) — the recovery is purely forensic: reconstruct the gap duration from reconnect logs to understand exposure and fix the heartbeat/detection gap for next time |
| Backfill pagination gap missing a signature (Pitfall 12) | MEDIUM | Balance-reconciliation mismatch alert (if implemented, Pitfall 12's prevention) surfaces the gap; manually re-run `getSignaturesForAddress` across the full range to identify and manually score the missed transaction |
| Stranded SPL token value after a SOL-only sweep (Pitfall 5) | LOW (if keystore retained) / HIGH (if hard-deleted) | If the keystore wasn't hard-deleted, manually swap-back and close the ATA later using the retained key; if hard-deleted, the position may be unrecoverable — this is why the prevention (don't hard-delete on nonzero token balance) matters more than the recovery path |
| A real false-positive auto-sweep mid-trade (Pitfall 15) | MEDIUM | Funds are safe (swept to vault, not lost) but a position may have been liquidated at a bad moment — the "recovery" is financial (re-enter the position manually if still desired) and process (add the triggering pattern as a new "must stay OK" regression fixture immediately) |
| Poisoned release shipped to users (Pitfall 19) | HIGH | Immediate: pull the compromised release from PyPI/GitHub, publish an advisory, force-invalidate any credentials the compromised publish access could have touched; users who installed the compromised version must be told to treat any keys generated/used during that window as compromised and rotate (move funds to a fresh, verified-clean-tool-generated vault) |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|-------------------|----------------|
| 1. Unsweepable wallet (fee/rent miscalc) | `sweeper.py` (build order step 3) | Devnet sweep test: SOL + open ATA → asserts exact-zero end state |
| 2. Blockhash expiry / unknown tx state | `rpc.py` (step 1), `funder.py`/`sweeper.py` (step 3) | Test: injected send-delay past blockhash expiry → asserts correct expired-vs-landed resolution, no blind resend |
| 3. Confirmed-vs-finalized reorg race | `scoring.py` (step 5), `monitor.py` (step 6) | Test: baseline/burn-state updates re-verified at finalized commitment before treated as permanent |
| 4. Retry-induced double-spend | `funder.py`/`sweeper.py` (step 3) | Devnet test: injected timeout after successful send → asserts no duplicate transfer |
| 5. Dust/ATA rent stranding | `sweeper.py` (step 3), `keystore.retire()` (step 6 for auto-sweep path) | Test: session with open SPL position ends → asserts ATA closed or keystore retained with explicit flag, never silent loss |
| 6. Plaintext key to disk/logs/swap | `keystore.py` (step 2) | Grep-based test over full test-suite captured output for secret-shaped strings; core-dump-disabled check |
| 7. Shell history / env leakage | `config.py` (step 1), distribution docs (§10.3) | Documentation review + no inline-`export` examples in onboarding; file-based secret loading path implemented |
| 8. Weak KDF params | `keystore.py` (step 2) | Test asserting configured scrypt `n` ≥ current recommended floor; versioned KDF params in keystore file format |
| 9. LLM egress boundary breach | `scoring.py` (step 5) | Import-boundary test (no `keystore.py` import in LLM module) + canary-secret runtime test |
| 10. Cloud-synced keystore dir | `config.py`/`keystore.py` (steps 1–2) | Startup test with a synthetic Dropbox/OneDrive/iCloud path → asserts refusal to run |
| 11. Silent WebSocket drop | `rpc.py` (step 1), `monitor.py` (step 6) | Test: forced silent (non-close) connection kill → asserts heartbeat-triggered reconnect + resubscribe + backfill within threshold |
| 12. `getSignaturesForAddress` pagination gap | `rpc.py` (step 1), `monitor.py` (step 6) | Test: simulated >1000-signature outage → asserts full pagination + post-backfill balance reconciliation |
| 13. Non-idempotent re-scoring | `monitor.py` (step 6), `sweeper.py` (step 3) | Test: same signature fed twice through handler → asserts exactly one alert, sweep is a clean no-op on second pass |
| 14. Helius rate-limit burst during incident | `rpc.py` (step 1), `monitor.py` (step 6) | Load test: simulated concurrent detect+backfill+sweep burst against free-tier RPS assumption; latency-vs-429-count logged |
| 15. False-positive auto-sweep mid-trade | `scoring.py` (step 5) | Fixture library expanded beyond the two golden cases before `--armed` recommended for real use; multi-signal AND-gate for sweep trigger |
| 16. Baseline poisoning at session start | `scoring.py` (step 5) | Fixture: "compromised from t=0" session alongside mid-session-compromise fixture; absolute cap-relative rules active during warm-up |
| 17. LLM as sole/manipulable gate | `scoring.py` (step 5) | Test: sweep-trigger function's inputs contain no LLM-derived field; prompt-injection-style on-chain data doesn't alter rule-based verdict |
| 18. Alert latency loses the race | `monitor.py` (step 6), `scoring.py` (step 5) | Timed replay of 5YEQ fixture measuring end-to-end sweep-dispatch latency, not just correctness; LLM decoupled from critical path |
| 19. Supply-chain poisoned release | Distribution gate (§10.6–10.7), habits start step 1 | Hash-pinned lockfile from early build; signed/reproducible release process verified before any public release |
| 20. Custody/regulatory line creep | Ongoing (all phases), re-gated at distribution (§10.5/§10.7) | Standing checklist against the three hard lines (no key custody, no hosted fund-path service, no in-flow fee skim) reviewed at every feature addition |
| 21. "You're safe now" framing | `cli.py`/`alerter.py` copy (steps 7–8), distribution gate (§10.4) | Full user-facing copy audit before public release; no unqualified "safe/protected/secured" language |

## Sources

- [Solana: Transaction Confirmation & Expiration](https://solana.com/docs/core/transactions/confirmation) — blockhash validity window (~150 blocks / 60–90s), commitment-level tradeoffs
- [Helius: How to Deal with Blockhash Errors on Solana](https://www.helius.dev/blog/how-to-deal-with-blockhash-errors-on-solana)
- [Helius: What are Solana Commitment Levels?](https://www.helius.dev/blog/solana-commitment-levels) — confirmed vs finalized tradeoffs
- [QuickNode: Solana Transaction Propagation — Handling Dropped Transactions](https://www.quicknode.com/guides/solana-development/transactions/solana-transaction-propagation-handling-dropped-transactions)
- [Chainstack: Solana rent-exemption overview](https://www.quicknode.com/guides/solana-development/getting-started/understanding-rent-on-solana) / [GemWallet: What is Solana Rent?](https://docs.gemwallet.com/blockchains/solana/rent/) — basic account (~0.00089088 SOL) vs ATA (~0.00203928 SOL) rent-exempt minimums
- [Alchemy: What is an Associated Token Account on Solana?](https://www.alchemy.com/overviews/associated-token-account)
- [Helius: How to Use getSignaturesForAddress](https://www.helius.dev/docs/rpc/guides/getsignaturesforaddress) — 1000-item cap, before/until cursor pagination
- [Chainstack / Medium: overcoming the 1000-transaction getSignaturesForAddress limit](https://chainstack.com/solana-how-to-getsignaturesforaddress-1000-transaction-limit/)
- [solana-labs/solana GitHub Issue #21039: getSignaturesForAddress boundary bug with before/until](https://github.com/solana-labs/solana/issues/21039)
- [solana-labs/solana GitHub Issue #19072: web3.js subscribe errors are silent](https://github.com/solana-labs/solana/issues/19072) — WebSocket subscription failures not surfaced
- [Solana docs: RPC WebSocket Methods](https://solana.com/docs/rpc/websocket) / [logsSubscribe](https://solana.com/docs/rpc/websocket/logssubscribe)
- [Helius: Rate Limits](https://www.helius.dev/docs/billing/rate-limits) and [Helius Pricing](https://www.helius.dev/pricing) — free-tier credit/RPS model
- [Socket.dev: Supply Chain Attack Detected in Solana's web3.js Library](https://socket.dev/blog/supply-chain-attack-solana-web3-js-library)
- [BleepingComputer: Solana Web3.js library backdoored to steal secret, private keys](https://www.bleepingcomputer.com/news/security/solana-web3js-library-backdoored-to-steal-secret-private-keys/)
- [Wiz: Solana web3.js Supply Chain Attack incident record](https://threats.wiz.io/all-incidents/solana-web3js-supply-chain-attack) — phishing of maintainer publish credentials, ~5-hour exposure window, ~$190K stolen
- [The Hacker News: Researchers Uncover Backdoor in Solana's Popular Web3.js npm Library](https://thehackernews.com/2024/12/researchers-uncover-backdoor-in-solanas.html)
- [Medium: The Anatomy of a Solana Wallet Drain in 2025](https://medium.com/@julianpierre1975/the-anatomy-of-a-solana-wallet-drain-in-2025-8f67f9ceb841) — direct-transfer drainer techniques, phishing UI cloning
- [SEAL: Advisory on Reflected XSS Exploits by Perpetual Drainer](https://www.securityalliance.org/news/2025-03-perpetual-drainer) — 2025/2026 drainer evolution bypassing wallet simulation
- Project-internal: `D:\apaul\Documents\bastion\bastion-spec.md` (§4 scoring rules, §6 reliability, §7 invariants, §10 distribution) and `D:\apaul\Documents\bastion\.planning\PROJECT.md` — primary grounding for which pitfalls are already partially addressed by design vs. still open risks in implementation

---
*Pitfalls research for: Non-custodial Solana session-wallet CLI with behavioral anomaly detection (Bastion)*
*Researched: 2026-07-06*
</content>
