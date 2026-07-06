# Feature Research

**Domain:** Non-custodial Solana session-wallet isolation ("bastion host" pattern) + on-chain behavioral anomaly detection / drain alerting, for solo terminal/bot traders
**Researched:** 2026-07-06
**Confidence:** MEDIUM

Note on method: no official vendor API docs were pulled via Context7/MCP in this pass (not available in this environment); findings are grounded in web search across open-source repos (GitHub), vendor docs (Helius), and security-vendor blogs (Blowfish, Web3Firewall, Kaspersky, Trust Wallet, SlowMist). Treat repo-description-level claims as MEDIUM confidence; treat cited incident data (web3.js supply-chain compromise, Solareum breach) and official RPC/webhook docs as HIGH confidence. No tool in this space was found that combines containment (capped session wallets) with per-session behavioral-deviation scoring — that combination is Bastion's actual white space, not just a marketing angle.

## Feature Landscape

### Table Stakes (Users Expect These)

Features any credible tool in this space either already has (wallet trackers, revoke tools) or that a trader will assume exist once you frame Bastion as "drain protection for my trading wallet."

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Fresh keypair generation per session | Baseline "burner wallet" hygiene is the #1 piece of community advice for connecting a wallet to any bot/terminal (repeated verbatim across trading-bot guides: "never connect your main wallet, use a burner funded with only what you risk"). Table stakes because it's the precondition everything else depends on. | LOW | `solders` keypair gen is trivial; the work is in what wraps it (keystore, cap, lifecycle). |
| Capped funding from a vault (hard SOL cap, refuse over max) | Every "burner wallet" writeup independently arrives at "fund only what you're willing to lose" — this is the community's existing mental model, Bastion just enforces it programmatically instead of leaving it to discipline. | LOW-MEDIUM | Needs a hard safety rail (`MAX_SESSION_CAP`) that isn't just documentation — a refusal path, not a warning. |
| Encrypted local keystore (not plaintext key files) | `solana-keygen`'s own default file-system wallet is **unencrypted on disk** (docs confirm: "the file contains your unencrypted keypair" even when a seed-phrase passphrase is set). This is a known weak spot in the existing Solana CLI tooling ecosystem — any tool claiming to be safer than the status quo must clear this bar. | MEDIUM | scrypt-derived key → Fernet is a reasonable, well-understood pattern (analogous to Ethereum's web3 keystore JSON, which Solana tooling generally lacks). 0600 perms + no-cloud-sync check are cheap, high-value adds. |
| Real-time transaction monitoring for a given address | Every wallet-tracker bot in this space (Helius webhooks, handi-cat, royceanton's activity-alert bot, cryptocurrencyalerting.com) leads with this. Users expect sub-minute (ideally sub-5s) visibility into "something happened on this address." | MEDIUM | WS (`logsSubscribe`/`accountSubscribe`) primary + polling backfill is the standard resilience pattern Helius itself documents (webhooks + WS + Geyser). Bastion's own spec targets <5s from confirmation to push, which is tighter than most trackers (usually tuned for "notify me a whale moved," not "beat a drain"). |
| Out-of-band push alerting (Telegram and/or Pushover) | Telegram is the default channel for this entire ecosystem — every wallet tracker, rug bot, and price-alert bot found in research pushes to Telegram; Pushover is the second most common for people who want alerts outside a chat app. Users will assume Telegram support exists. | LOW-MEDIUM | Bastion's twist (separate Telegram identity from the trading session) is not something any surveyed tracker bot does — worth calling out as a differentiator-adjacent hardening, not just parity. |
| Manual sweep-to-vault on session end | The obvious complement to capped funding — "give it back when I'm done" is expected once "give it a cap" exists. Malicious "sweeper bots" (documented by MetaMask, Trust Wallet, OneKey support pages) use the identical mechanic offensively (watch a balance, sweep it out instantly) — Bastion repurposes the same low-level primitive defensively. | LOW | Straightforward transfer-minus-fee-reserve; already spec'd. |
| Basic audit trail of fund movements | Any tool that moves money without a visible log is a non-starter for a self-custody trader — this is closer to "obviously required" than "differentiator." Revoke.cash and similar tools implicitly rely on the on-chain record itself as the log; Bastion needs its own because it also records verdicts/alerts, not just transfers. | LOW-MEDIUM | Append-only JSONL alongside SQLite state, as already spec'd, is standard and sufficient for v1; don't over-build (no tamper-proof chain-of-custody crypto needed yet). |
| Reconnect + backfill resilience for the monitor | Every serious webhook/WS provider (Helius docs explicitly call this out) treats "don't miss events across a drop" as core, not optional. A monitoring tool that silently blinds itself on a WS hiccup is not credible for a security use case. | MEDIUM-HIGH | This is the one "boring" table-stakes item with real engineering weight — dedupe on signature, reconcile on restart. Underinvesting here is the single most likely way Bastion quietly fails at its actual job. |
| Approval/delegate-authority change detection | `SetAuthority`/`Approve` (SPL delegate) changes are the canonical drain vector on every chain; Solana's own docs on revoking delegates, plus the entire Revoke.cash/Solana-Revoker/Famous-Foxes-Revoker ecosystem, exist purely because this is a known, common attack surface. A monitoring tool that doesn't flag these is missing the most textbook signal in the space. | LOW-MEDIUM | Cheap to detect (instruction-type match) even though it "didn't fire" in Bastion's own reference incident — it's still table stakes because it's the most well-known drain pattern industry-wide. |

### Differentiators (Competitive Advantage)

These map directly to Bastion's stated edge (containment-first + per-session behavioral baseline + rules-gate/LLM-explains) and were **not found** combined anywhere in the surveyed ecosystem. Wallet trackers monitor; revoke tools clean up after; token-risk bots (RugWatch, Solsniffer, degenfrends/solana-rugchecker) score *incoming* tokens for rug risk; pre-execution firewalls (Blowfish, Web3Firewall, Wallet Guard) simulate a transaction *before* signing. None of them own the fund-containment step, and none baseline *your own session's* behavior over time.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Containment as the primary control (capped, disposable session wallet as the actual defense, not just a monitoring target) | Every competing tool in this space is reactive-only: it watches a wallet and tells you something bad happened, or it screens a transaction/token *before* signing. None of them bound the *maximum possible loss* structurally. This is Bastion's core thesis and its most defensible edge — "lost everything" → "lost the cap" holds even if detection and alerting both fail. | LOW-MEDIUM (the wallet/fund mechanics are simple) | The real complexity isn't the mechanism, it's the discipline of never letting session and vault touch (no shared authority, no linked approvals) — this is a design invariant more than a feature to build. |
| Per-session behavioral baseline (deviation from *this session's own* norms, not absolute thresholds) | Pre-execution firewalls (Blowfish/Web3Firewall) and rug scoring tools (RugWatch/Solsniffer) both score against *global* heuristics (known scam patterns, contract risk, liquidity depth) — they cannot catch "this looks like normal trading in isolation but is wildly unlike how *you* trade this session" because a hostile terminal signs mechanically valid swaps. Solana's own threat model here (self-signed transactions through legitimate pools) defeats every simulation/allowlist approach found in research. Baseline-deviation is the only surveyed technique aimed at this exact failure mode. | HIGH | Needs enough early-session activity to seed a baseline (median clip size, typical hold time, known counterparties) before it's meaningful — cold-start risk for very short sessions is a real gap to flag for the roadmap. |
| Rules-gate + LLM-explains (LLM never the sole trigger) | The broader anomaly-detection literature (arXiv graph-based crypto anomaly detection, Chainalysis/Hexagate wallet-compromise tooling) is trending toward ML-driven verdicts; Bastion's stance — deterministic, auditable rules decide, LLM only narrates/reduces false positives — is a deliberate, defensible departure that keeps the fund-moving decision legible and testable (TDD against golden fixtures), which nothing in the surveyed space does explicitly. | MEDIUM | LLM call sits *after* rule-flagging only, off the hot path for the sweep decision — keeps latency and blast radius of a bad LLM call low. |
| Cluster-of-signals scoring (velocity + round-trip loss + new-counterparty sink + realized-loss burst, combined and weighted) vs. single-signal alerts | Wallet trackers alert on single events ("wallet moved X SOL"); rug bots score a single token. Nobody surveyed combines multiple simultaneous behavioral signals into one severity verdict the way Bastion's scoring engine does (explicitly: "any one is noise; a cluster in a short window is the signal"). This is closer to KYT/AML velocity-rule engineering (didit.me, Chainalysis glossary) than to consumer wallet-tracker patterns — Bastion is importing a compliance-grade technique into a personal-security tool, which is itself somewhat novel for this space. | MEDIUM-HIGH | Weight-tuning against real fixtures (the 5YEQ churn, a clean trading day) is the actual hard part; the plumbing is comparatively easy. |
| Structurally-enforced LLM-scoring egress boundary (payload built from public on-chain fields only, in a module with no keystore access, test-guarded) | No surveyed tool documents this level of rigor around what a "helpful cloud AI feature" is allowed to see. Given that hosted-LLM scoring is explicitly the sharpest architectural risk to the non-custodial invariant, doing this *structurally* (not just "we promise not to log keys") is a genuine differentiator for trust, especially for a stranger-distributed OSS tool. | MEDIUM | This is a test-suite / module-boundary discipline, not a UI feature — but it's the kind of thing security-conscious users and auditors will specifically check for. |
| Alert-channel isolation (separate Telegram identity from the trading session) | Every surveyed wallet-tracker bot assumes the notification channel is trustworthy by default; none explicitly defends against "the same phished device/session that's trading also controls the alert channel." This closes an obvious blind spot none of the competitors address. | LOW | Mostly a deployment/config discipline (separate bot token/account) plus a check that refuses same-identity config. |
| Opt-in auto-sweep-on-anomaly (`--armed`), default alert-only | Malicious "sweeper bots" already prove the *mechanism* (watch balance → sweep on trigger) works and is fast enough to matter — Bastion repurposes the identical primitive defensively, which no legitimate tracker/revoke tool in the surveyed set does (they all stop at "notify" or "let the user manually revoke/transfer"). Framed as a differentiator *only* because it's opt-in and reversible-risk-bounded (capped session, so a false-positive sweep costs at most the cap) — see the corresponding anti-feature entry for why default-armed is excluded. | MEDIUM-HIGH | Depends on: capped funding (bounds the downside of a false trigger) + scoring engine (must be well-tuned before this is safe to enable) + sweeper module. Ship after the scoring engine has validated against real sessions. |
| Loss-threshold / time-window session rotation (vs. static single-session or per-fill) | Community guidance stops at "use one burner wallet, refill as needed" — nobody surveyed formalizes *when* to rotate. Tying rotation to a loss threshold or time window (rather than per-fill, which Solana rent/ATA economics make impractical) is a small but genuinely novel piece of session-lifecycle policy. | MEDIUM | Needs the baseline/audit data to know cumulative loss per session; opt-in per the spec, not default behavior in v1. |

### Anti-Features (Commonly Requested, Often Problematic)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|------------------|-------------|
| LLM as the sole gate on moving funds | Feels "smart" — let the model just decide; industry anomaly-detection research (arXiv graph-based LLM-augmented explanation papers) is actively trending this direction, so it looks like the state of the art. | A black box deciding when to move real money is illegible, non-deterministic across runs, adds cost/latency on the critical path, and can't be TDD'd against golden fixtures the way rules can. It also reintroduces exactly the kind of opaque trust Bastion exists to avoid. | Rules gate (deterministic, weighted, fixture-tested); LLM only explains/confirms after a rule-flag, purely advisory. |
| Auto-sweep armed by default | Looks like "maximum protection out of the box" — why would a security tool default to merely watching? | For a stranger-distributed OSS tool, armed-by-default risks sweeping to a misconfigured/wrong vault address, or auto-liquidating a legitimate but volatile trade mid-flight on a false positive — turning a safety feature into an unrequested fund-mover. This exact failure mode (an automated script moving funds the instant a condition triggers) is *identical in mechanism* to a malicious "sweeper bot," which is precisely the pattern users are trying to defend against — shipping it as the default would be self-undermining. | Default alert-only; `--armed` is a deliberate, loudly-warned opt-in, and only after a user has validated the scoring engine isn't false-positiving on their own trading style. |
| Per-trade / per-fill wallet rotation | Feels like "maximum isolation" — a brand-new wallet per trade sounds strictly safer than reusing one per session. | Solana's rent/ATA economics make this actively wasteful (rent-exempt minimums and ATA creation costs per new wallet/token pair add up fast relative to typical trade sizes) and it destroys the very continuity the behavioral baseline needs (you can't build a per-session profile if there's no session). | Loss-threshold / time-window rotation (already a differentiator above); one wallet persists long enough to establish a meaningful baseline. |
| Any hosted/server component in the fund path (e.g., a cloud service that can initiate or co-sign sweeps) | Convenient for multi-device access, push reliability, or "let us handle scaling the alert infra" — common pattern in every commercial wallet-tracker (cryptocurrencyalerting.com, Helius-hosted webhooks) and pre-execution firewall (Blowfish, Web3Firewall) surveyed, which are all inherently hosted services. | Breaks the non-custodial invariant and the regulatory safe-harbor posture the whole project depends on; turns Bastion into exactly the kind of honeypot/custodial target it exists to prevent. Every hosted wallet-tracker or firewall in this space is a *reasonable* design for *their* problem (monitoring/screening) but a *disqualifying* one for a tool that also moves funds. | Single-user, single-machine, local process only; LLM scoring calls out to a stateless API with public-only data (structurally firewalled), never a fund-authorizing service. |
| Transaction pre-execution simulation / blind-signing firewall (Blowfish/Web3Firewall/Wallet Guard style) as the *primary* defense | These are the most visible, well-funded products in adjacent wallet-security space, so it's tempting to build/bolt on the same "simulate before signing" layer. | Explicitly defeated by Bastion's own threat model: the session *itself* is the attacker, signing mechanically valid, simulation-clean swaps through legitimate pools. Pre-execution simulation catches malicious *contracts*, not a compromised *signer* trading normally through trusted protocols — it would give false confidence on exactly the failure mode Bastion targets. | Containment + post-hoc behavioral-deviation detection (the actual differentiator); simulation-based screening is a different, complementary problem (protecting against malicious dApps/tokens) explicitly out of scope. |
| Incoming-token/rug-pull risk scoring (RugWatch/Solsniffer-style: "is this new token I'm about to buy a scam?") | Overlaps conceptually with "anomaly detection" and there's a large, active open-source ecosystem already doing it (rugpull-scam-token-detection, solana-rugchecker, Solsniffer) that's tempting to fold in for "completeness." | Different problem: it scores a *token's* trustworthiness before you buy, whereas Bastion scores *your own wallet's* behavior after a session starts. Building both would double scope and dilute focus; the existing rug-scoring ecosystem already serves that need reasonably well. | Explicitly out of scope; Bastion assumes the trader is choosing what to trade — its job starts once a session is live and something is moving money oddly. |
| Auto token liquidation on sweep (auto-swap SPL positions back to SOL before sweeping) | Feels incomplete otherwise — "why sweep SOL but leave my open positions behind?" | Needs trustworthy slippage/routing logic (which DEX, what slippage tolerance, MEV exposure) to not itself become a value-destroying or exploitable step; premature auto-selling into a thin/manipulated market during an active anomaly is dangerous, and building a safe router is a project in itself. | SOL-only sweep in v1 (already spec'd); SPL positions swapped back in-terminal first, empty ATAs closed to reclaim rent; `sweep_tokens` remains a documented v2 stub. |
| Multi-wallet dashboard UI / historical PnL attribution | Every commercial wallet-tracker surveyed (handi-cat, Solana-Wallet-Tracking-Telegram-Bot-Portfolio, cryptocurrencyalerting.com) leads with a slick UI/portfolio view, so it looks like expected polish. | Pulls focus and engineering time away from the actual product (containment + detection correctness) toward a UI layer that doesn't change the security outcome; a CLI + monitor process is sufficient for the target user (solo trader, single machine) and ships faster. | v1 surface stays CLI + monitor process, per spec; a dashboard is a legitimate v2+ addition once the core loop is validated. |
| Anonymity / mixing between session and vault | Superficially adjacent to "security" — if funds are linkable, isn't that a privacy leak? | Explicitly a different goal (untraceability) from Bastion's (bounded, pre-decided loss); mixing/obfuscation adds real regulatory and complexity risk for near-zero containment benefit, and session wallets funded from one vault being linkable on-chain is an accepted, documented tradeoff. | Design stance stays isolation-not-anonymity, as already decided; no mixing, no privacy-pool integration. |

## Feature Dependencies

```
Fresh keypair generation per session
    └──requires──> Encrypted local keystore (scrypt→Fernet, 0600)

Capped funding from vault
    └──requires──> Fresh keypair generation per session
    └──requires──> Hard cap safety rail (MAX_SESSION_CAP enforcement)

Real-time transaction monitoring
    └──requires──> RPC/WS client with reconnect + polling backfill
                       └──requires──> Session Store (dedupe on signature, last-seen cursor)

Behavioral scoring (rules engine)
    └──requires──> Real-time transaction monitoring (feeds parsed tx)
    └──requires──> Per-session baseline (seeded from early-session activity)
                       └──requires──> Session Store (persist baseline state)

LLM-explains layer
    └──requires──> Behavioral scoring (only runs on rule-flagged/ambiguous tx)
    └──requires──> Structural egress boundary (public-fields-only module, no keystore access)

Out-of-band alerting (Telegram/Pushover)
    └──requires──> Behavioral scoring (verdict to report)
    └──enhanced by──> Alert-channel isolation (separate identity from trading session)

Opt-in auto-sweep (--armed)
    └──requires──> Behavioral scoring (validated/tuned against fixtures first)
    └──requires──> Capped funding (bounds false-positive sweep downside)
    └──requires──> Sweeper (session → vault, no vault secret needed)

Manual sweep-to-vault on session end
    └──requires──> Sweeper (shared with auto-sweep path)

Loss-threshold / time-window rotation
    └──requires──> Audit log + Session Store (cumulative loss/duration tracking)
    └──conflicts with──> Per-fill rotation (anti-feature; rent/ATA economics + baseline continuity)

Audit logging (JSONL, append-only)
    └──enhances──> every fund-moving and scoring feature (cross-cutting, not a leaf dependency)

Auto token liquidation on sweep ──deferred-until──> trustworthy slippage/routing logic (v2)
Multi-wallet dashboard UI ──deferred-until──> core loop (containment + detection) validated (v2)
Hardware-signer (Ledger) vault path ──deferred-until──> distribution to strangers (v2)
```

### Dependency Notes

- **Capped funding requires fresh keypair generation:** you can't enforce a session cap on a wallet that doesn't yet exist independently of the vault — the session identity has to precede the transfer.
- **Behavioral scoring requires per-session baseline, which requires the Session Store:** the scoring engine's central claim (deviation from *this session's* norms) is meaningless without persisted state to deviate from — this is why `store.py` is sequenced before `scoring.py` in the build order, and why cold-start (very short sessions with no baseline yet) is a real gap worth flagging for phase planning.
- **LLM-explains requires the structural egress boundary, not just a policy:** because this is the single place a hosted-cloud feature could silently violate the non-custodial invariant, the dependency has to be enforced by module boundaries and tests, not documentation — this should land as its own explicit build/test task, not a footnote on the scoring phase.
- **Opt-in auto-sweep requires a validated scoring engine before it's safe to enable:** shipping `--armed` before the rules are tuned against real fixtures inverts the risk/benefit — the roadmap should treat "scoring validated against golden fixtures" as a hard gate before "auto-sweep" work begins, not a parallel track.
- **Loss-threshold/time-window rotation conflicts with per-fill rotation:** these are mutually exclusive design choices (Solana economics rule out per-fill), so no phase should implement both — per-fill rotation should not resurface as a "nice to have" later without revisiting the rent/ATA math.
- **Audit logging is cross-cutting:** every other feature (fund, sweep, alert, verdict) writes to it; it should exist before any fund-moving code lands, not be bolted on after.

## MVP Definition

### Launch With (v1)

Minimum viable product — enough to validate the core containment + detection thesis on the maintainer's own mainnet trading, per the project's own build order.

- [ ] Fresh keypair generation per session — precondition for everything else
- [ ] Encrypted local keystore (scrypt→Fernet, 0600, no plaintext key ever on disk) — non-negotiable given the non-custodial invariant
- [ ] Capped funding from vault with hard `MAX_SESSION_CAP` enforcement — this *is* the core value proposition
- [ ] Manual sweep-to-vault on session end — closes the loop capped funding opens
- [ ] Real-time monitoring (WS primary + polling backfill, dedupe on signature) — detection is worthless if events are missed
- [ ] Rules-based scoring engine validated against the 5YEQ churn (CRITICAL) and a clean trading day (OK) fixtures — the actual product; TDD-first per spec
- [ ] Out-of-band alerting (Telegram and/or Pushover) with alert-channel isolation from the trading session — detection without notification is inert
- [ ] Append-only audit log (JSONL) of every fund/sweep/alert/decision — required for trust and debugging from day one
- [ ] Structural LLM-scoring egress boundary + test that fails if key material can reach the network layer — must exist *before* the LLM-explains layer is turned on, if it ships in v1 at all

### Add After Validation (v1.x)

Features to add once the core containment + detection loop has run against real mainnet sessions.

- [ ] LLM-explains layer on flagged transactions — add once the rules engine has a track record, so LLM output can be judged against known-good verdicts rather than being the first thing users see
- [ ] Opt-in auto-sweep (`--armed`) — add only after scoring has been run alert-only long enough to trust its false-positive rate on the maintainer's own trading style
- [ ] Loss-threshold / time-window session rotation — add once enough sessions have run to know what a sensible threshold/window actually is empirically

### Future Consideration (v2+)

Features to defer until the core product is validated and (if pursued) distribution to strangers is being prepared.

- [ ] Auto token liquidation on sweep (`sweep_tokens`) — defer until slippage/routing logic is trustworthy; premature auto-selling is dangerous
- [ ] Hardware-signer (Ledger) path for the vault — defer to distribution prep; env-var vault secret is acceptable for personal use but not for strangers
- [ ] Multi-wallet dashboard UI; historical PnL attribution — defer until the CLI-only core loop is proven; this is where most competing tools spend their effort, which is exactly why it's not where Bastion should spend its early effort
- [ ] Learned/ML baseline model (replacing hand-tuned rules) — defer until enough labeled sessions exist to beat the handwritten rules, per the project's own stated stance

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Encrypted keystore (scrypt→Fernet, 0600) | HIGH | MEDIUM | P1 |
| Capped funding + hard cap enforcement | HIGH | LOW-MEDIUM | P1 |
| WS monitoring + polling backfill (no missed events) | HIGH | MEDIUM-HIGH | P1 |
| Rules-based scoring engine (fixture-tested) | HIGH | MEDIUM-HIGH | P1 |
| Out-of-band alerting (Telegram/Pushover) | HIGH | LOW-MEDIUM | P1 |
| Manual sweep-to-vault | HIGH | LOW | P1 |
| Audit log (JSONL) | MEDIUM-HIGH | LOW-MEDIUM | P1 |
| Approve/setAuthority change detection | MEDIUM | LOW-MEDIUM | P1 |
| Alert-channel isolation | MEDIUM | LOW | P1 |
| Structural LLM-egress boundary + test | MEDIUM (HIGH if LLM ships in v1) | MEDIUM | P1 (if LLM in v1) / P2 |
| LLM-explains layer | MEDIUM | MEDIUM | P2 |
| Opt-in auto-sweep (`--armed`) | MEDIUM-HIGH | MEDIUM-HIGH | P2 |
| Loss-threshold / time-window rotation | MEDIUM | MEDIUM | P2 |
| Auto token liquidation on sweep | LOW-MEDIUM (v1 users) | HIGH | P3 |
| Hardware-signer (Ledger) vault path | LOW (personal use) / HIGH (distribution) | MEDIUM-HIGH | P3 |
| Multi-wallet dashboard UI | LOW | HIGH | P3 |
| Learned/ML baseline model | LOW (not enough data yet) | HIGH | P3 |

**Priority key:**
- P1: Must have for launch (personal mainnet use)
- P2: Should have, add when possible (post-validation)
- P3: Nice to have, future consideration (distribution-era or data-dependent)

## Competitor Feature Analysis

| Feature | Wallet-tracker bots (handi-cat, royceanton activity-alert, cryptocurrencyalerting.com) | Pre-execution firewalls (Blowfish, Web3Firewall, Wallet Guard) | Rug/token-risk scanners (RugWatch, Solsniffer, solana-rugchecker) | Revoke tools (Revoke.cash, Solana Revoker, Famous Foxes Revoker) | Our Approach (Bastion) |
|---------|--------------|--------------|--------------|--------------|--------------|
| Core question answered | "What did this wallet just do?" | "Should I sign this specific transaction?" | "Is this token I'm about to buy a scam?" | "What approvals/delegates does this wallet still have live?" | "Is this session's overall behavior drifting from how *I* trade?" |
| Timing | Reactive, after the fact | Pre-execution, before signing | Pre-trade, before buying | Reactive, periodic manual check | Real-time, during the session, continuously against a live baseline |
| Scope of comparison | Absolute event thresholds (e.g. "moved >X%") | Contract/heuristic risk model, generic across users | Token-level metadata/liquidity/holder heuristics, generic across tokens | On-chain approval state, generic across wallets | Per-session, per-user behavioral baseline (deviation-based, not absolute) |
| Blast-radius control | None — monitoring only | None — advisory only (allow/deny/escalate signal) | None — informational only | Partial — revokes a specific approval after the fact | Structural — hard-capped disposable session wallet bounds maximum loss regardless of detection outcome |
| Alerting channel | Telegram (near-universal), Discord | In-wallet/browser-extension UI prompt | Telegram/Discord | None (manual dashboard action) | Telegram/Pushover, deliberately out-of-band from the trading session identity |
| Fund-moving action on trigger | None | Blocks/warns pre-signature | None | Manual revoke transaction, user-initiated | Opt-in auto-sweep to vault (`--armed`), default alert-only |
| Handles "session itself is the attacker" (self-signed, simulation-clean swaps) | No — has no behavioral model at all | No — a legitimate-looking swap through a real pool simulates clean | No — orthogonal problem (token risk, not signer behavior) | No — orthogonal problem (approval hygiene, not live drain) | Yes — this is the specific failure mode the baseline-deviation design targets |
| Custody model | Mostly hosted/SaaS (address-only, no keys involved) | Hosted service, browser extension | Mostly hosted/SaaS API | Mostly client-side signed txs via hosted frontend | Fully local, non-custodial, no hosted component in fund path |

## Sources

- [Helius: Solana Webhooks docs](https://www.helius.dev/docs/webhooks) — HIGH confidence, official vendor docs on real-time monitoring patterns (WS/webhook/Geyser), reconnect/backfill norms
- [Helius: Build a Wallet Tracker on Solana](https://www.helius.dev/blog/build-a-wallet-tracker-on-solana) — HIGH confidence, official
- [Solana docs: Revoke Delegate](https://solana.com/docs/tokens/basics/revoke-delegate) — HIGH confidence, official, grounds the approve/setAuthority drain vector
- [Solana docs (Agave/Anza): File System Wallets via CLI](https://docs.solanalabs.com/cli/wallets/file-system) — HIGH confidence, official, confirms `solana-keygen` keypair files are unencrypted on disk (motivates Bastion's encrypted-keystore table stake)
- [GitHub: royceanton/telegram-solana-wallet-activity-alert](https://github.com/royceanton/telegram-solana-wallet-activity-alert) — MEDIUM confidence, repo description
- [GitHub: DracoR22/handi-cat_wallet-tracker](https://github.com/DracoR22/handi-cat_wallet-tracker) — MEDIUM confidence, repo description
- [GitHub: imcrazysteven/Solana-Wallet-Tracking-Telegram-Bot-Portfolio](https://github.com/imcrazysteven/Solana-Wallet-Tracking-Telegram-Bot-Portfolio) — MEDIUM confidence
- [GitHub: machenxi/rugpull-scam-token-detection (RugWatch)](https://github.com/machenxi/rugpull-scam-token-detection) — MEDIUM confidence, repo description
- [GitHub: degenfrends/solana-rugchecker](https://github.com/degenfrends/solana-rugchecker) — MEDIUM confidence
- [Solsniffer](https://www.solsniffer.com/) — MEDIUM confidence, vendor site
- [Web3Firewall: Wallet Drainer Protection](https://www.web3firewall.xyz/wallet-drainer-protection) — MEDIUM confidence, vendor site, pre-execution simulation approach
- [Blockaid: How Wallet Drainers Use Fake Revoke Sites and Twitter Phishing](https://blockaid.io/blog/how-wallet-drainers-use-fake-revoke-sites-and-twitter-phishing-to-exploit-victims) — MEDIUM confidence, vendor blog
- [Trust Wallet: What are Sweeping Bots?](https://trustwallet.com/blog/security/what-are-sweeping-bots) — MEDIUM confidence, vendor blog, grounds the offensive-sweeper-bot mechanic Bastion repurposes defensively
- [MetaMask Help Center: fighting sweeper bots](https://support.metamask.io/stay-safe/protect-yourself/fighting-back-against-sweeper-bots/) — MEDIUM-HIGH confidence, official support docs
- [OneKey: What Are Sweeping Bots](https://onekey.so/blog/ecosystem/what-are-sweeping-bots/) — MEDIUM confidence, vendor blog
- [Squads: Smart Account Program / spending limits & session keys](https://squads.xyz/blog/squads-smart-account-program-live-on-mainnet) and [Squads Multisig](https://squads.xyz/multisig) — HIGH confidence for Squads' own feature claims (audited, on mainnet); illustrates the adjacent "spend-limit smart account" pattern Bastion deliberately does *not* adopt (program-based authority vs. Bastion's disposable-wallet-based containment)
- [Web3isgoinggreat: Solana drain attacks linked to trading bots (Solareum)](https://www.web3isgoinggreat.com/?id=solana-drain-attacks) and [Coinpaper: BONKbot private key leak](https://coinpaper.com/3786/bon-kbot-claims-to-be-unaffected-by-solana-wallet-exploit-but-users-claim-otherwise) — MEDIUM-HIGH confidence, grounds real-world trading-bot key-leak incidents motivating the "burner wallet" table stake
- [Helius: Solana Hacks, Bugs, and Exploits — a complete history](https://www.helius.dev/blog/solana-hacks) — HIGH confidence, official vendor post covering the December 2024 `@solana/web3.js` supply-chain key-exfiltration compromise
- [SlowMist: Analysis of a Malicious Solana Open-source Trading Bot](https://slowmist.medium.com/threat-intelligence-an-analysis-of-a-malicious-solana-open-source-trading-bot-ab580fd3cc89) — MEDIUM-HIGH confidence, security-vendor incident analysis
- [didit.me: Velocity Rules & Structuring Detection](https://didit.me/blog/velocity-rules-structuring-detection/) and [Chainalysis: What Is Transaction Monitoring?](https://www.chainalysis.com/glossary/transaction-monitoring/) — MEDIUM confidence, grounds the velocity/cluster-of-signals scoring approach in existing KYT/AML practice
- [Chainalysis: Hexagate's Wallet Compromise Detection Kit](https://www.chainalysis.com/blog/hexagate-wallet-compromise-detection-kit/) — MEDIUM confidence, grounds "wallets learn their own behavior over time" as an existing (institutional-grade) pattern
- [arXiv: Explain First, Trust Later — LLM-Augmented Explanations for Graph-Based Crypto Anomaly Detection](https://arxiv.org/pdf/2506.14933) — MEDIUM confidence (preprint), informs the anti-feature reasoning against LLM-as-sole-gate
- Project source documents: `.planning/PROJECT.md`, `bastion-spec.md` — HIGH confidence, primary source for scope/requirements this research maps against

---
*Feature research for: non-custodial Solana session-wallet isolation + behavioral anomaly-detection CLI*
*Researched: 2026-07-06*
