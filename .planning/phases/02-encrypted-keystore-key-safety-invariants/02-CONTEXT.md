# Phase 2: Encrypted Keystore + Key-Safety Invariants - Context

**Gathered:** 2026-07-07
**Status:** Ready for planning

<domain>
## Phase Boundary

Session keys are safe at rest and in memory — encrypted (scrypt → Fernet),
owner-only (0600), never leaked to disk or logs — with the vault/session split
established structurally (isolated `vault.py` import boundary) before any
fund-moving code exists.

Delivers requirements **SESS-01** (generate keypair), **SESS-04** (encrypt at
rest + 0600), **SESS-05** (load-by-pubkey, wrong-passphrase-fails-closed),
**SEC-01** (no plaintext key to disk/logs), **SEC-04** (refuse cloud-synced
keystore dir), **SEC-05** (passphrase confirmed, never echoed/logged).

In scope: `keystore/crypto.py`, `keystore/session.py`, `keystore/vault.py`, and
the cloud-sync refusal + passphrase confirm/no-echo + no-secret-in-logs
regression checks. Out of scope: funding, sweeping, and any transaction
building (Phase 3+).

</domain>

<decisions>
## Implementation Decisions

### Keystore File Format & KDF Parameters
- **scrypt cost parameters: n = 2¹⁷ (131072), r = 8, p = 1** — at/above the
  current OWASP recommended floor (satisfies success criterion 2). ~128 MB per
  derivation, acceptable for infrequent keystore unlocks. Parameters are
  versioned in the file so they can be raised later without breaking old files.
- **On-disk format: JSON**, self-describing and versioned. Fields:
  `{version, kdf: "scrypt", n, r, p, salt (base64), ciphertext (base64)}`.
- **File naming: `<pubkey>.json`** in `KEYSTORE_DIR` — the pubkey is public and
  this matches the load-by-pubkey contract (SESS-05).
- **Plaintext (unencrypted) file contents are non-secrets only**: version, KDF
  name + params, salt, and pubkey. The secret key bytes exist **only** inside
  the Fernet ciphertext — never in a plaintext field.

### Secret Handling in Memory & Types
- **Dedicated secret-wrapping type** (e.g. `SessionKeypair`) with redacted
  `__repr__`/`__str__` rendering `secret=REDACTED` — extends Phase 1's
  `repr=False`-on-secrets convention (config.py) to the keystore layer.
- **Best-effort memory zeroization on retire**: overwrite the decrypted key
  `bytearray` where feasible, explicitly documented as best-effort (Python
  cannot guarantee zeroization of all copies).
- **Decrypted key is surfaced per-call via an explicit `load()`** — never
  cached on disk or in module-global state.
- **Redaction covers all secret-wrapping types AND exception messages** — no
  key bytes in any raised error; the SEC-01 regression test greps captured
  test-suite stdout/stderr for secret-shaped strings.

### Cloud-Sync Refusal & Passphrase UX
- **Detected cloud-sync path segments**: Dropbox, OneDrive, iCloud
  (`Mobile Documents` / `CloudDocs`), Google Drive — case-insensitive
  path-segment match.
- **Default behavior: hard refuse to run (raise)** when `KEYSTORE_DIR` resolves
  under a detected cloud-sync path. **[OVERRIDDEN FROM RECOMMENDED]** An
  explicit opt-in override (e.g. `--allow-cloud-sync` flag / equivalent config
  or env switch) downgrades the refusal to a loud warning for advanced users
  who accept the risk. The override is **off by default** and mirrors the
  `--armed` philosophy: dangerous escape hatches require deliberate, warned
  opt-in. Success criterion 4 (synthetic-path refusal) is verified against the
  default path where the override is NOT set, so it remains satisfied.
- **Passphrase confirm-on-create**: re-prompt loop (up to 3 attempts) on
  mismatch, then abort; the passphrase is never echoed to the terminal.
- **Passphrase strength policy: minimal** — require non-empty, emit a gentle
  warning on a very short passphrase; do not enforce complexity rules on a
  local single-user tool.

### Claude's Discretion
- Exact module/function signatures, salt length (default to a secure random
  ≥16 bytes), Fernet token handling, and the precise override switch mechanism
  (flag vs env vs config) are at Claude's discretion within the decisions above.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `bastion/config.py` — `Config` (frozen dataclass) already carries
  `keystore_dir` and `keystore_passphrase` (secret, `repr=False`), and
  `get_passphrase()` (env → `getpass` fallback, never echoed/logged). Phase 2
  reads these rather than re-reading env directly.
- `bastion/rpc/errors.py` — established typed-error module pattern
  (message-only, no secrets). Keystore errors should follow the same shape
  (e.g. a `KeystoreError` / wrong-passphrase error that fails closed).

### Established Patterns
- Flat `bastion/` package layout (not `src/`), per CLAUDE.md and Phase 1.
- Frozen dataclasses; secrets declared `repr=False`; no `logging`/`print` of
  secret material anywhere in `bastion/` (Phase 1 kept this at 0 matches).
- TDD-first: roundtrip and fail-closed tests written against the crypto/session
  primitives; tests live under `tests/unit/`.
- `cryptography` (scrypt KDF + Fernet) and `solders` (Keypair) are already
  project dependencies (uv.lock, hash-pinned).

### Integration Points
- `keystore/vault.py` `load_vault()` must be import-isolated so ONLY the funder
  (Phase 3) can import it — this import-graph fact is the structural basis for
  SEC-02/SEC-03 later. No scoring/monitor module may import it.
- Keystore consumes `Config.keystore_dir` for the cloud-sync check and file
  location, and `get_passphrase()` for unlock.

</code_context>

<specifics>
## Specific Ideas

- The vault/session split is **structural**, not just conventional: enforce it
  as an import-graph boundary that a later canary test (Phase 5, SEC-03) can
  assert against.
- The no-secret-in-logs guarantee is a **grep-based regression test** over
  captured test-suite stdout/stderr, not a code comment (per project
  constraint "Make these tests, not comments").

</specifics>

<deferred>
## Deferred Ideas

- Hardware-signer (Ledger) path for the vault — explicitly v2 (VAULT-V2-01);
  Phase 2 uses the encrypted-file keystore only.
- Additional cloud providers beyond the core four (Mega/Box/pCloud) — can be
  added to the detection list later if needed; not required for v1.

</deferred>
