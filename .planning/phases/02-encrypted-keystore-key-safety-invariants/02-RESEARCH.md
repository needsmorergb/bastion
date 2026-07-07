# Phase 2: Encrypted Keystore + Key-Safety Invariants - Research

**Researched:** 2026-07-07
**Domain:** Local-file cryptographic keystore (scrypt KDF + Fernet symmetric encryption) for Solana `solders.Keypair` material, plus filesystem-permission and cloud-sync-detection safety rails, on a Python 3.11+ CLI.
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Keystore File Format & KDF Parameters**
- scrypt cost parameters: n = 2^17 (131072), r = 8, p = 1 — at/above the current OWASP recommended floor. ~128 MB per derivation, acceptable for infrequent keystore unlocks. Parameters are versioned in the file so they can be raised later without breaking old files.
- On-disk format: JSON, self-describing and versioned. Fields: `{version, kdf: "scrypt", n, r, p, salt (base64), ciphertext (base64)}`.
- File naming: `<pubkey>.json` in `KEYSTORE_DIR` — the pubkey is public and this matches the load-by-pubkey contract (SESS-05).
- Plaintext (unencrypted) file contents are non-secrets only: version, KDF name + params, salt, and pubkey. The secret key bytes exist only inside the Fernet ciphertext — never in a plaintext field.

**Secret Handling in Memory & Types**
- Dedicated secret-wrapping type (e.g. `SessionKeypair`) with redacted `__repr__`/`__str__` rendering `secret=REDACTED` — extends Phase 1's `repr=False`-on-secrets convention (config.py) to the keystore layer.
- Best-effort memory zeroization on retire: overwrite the decrypted key `bytearray` where feasible, explicitly documented as best-effort (Python cannot guarantee zeroization of all copies).
- Decrypted key is surfaced per-call via an explicit `load()` — never cached on disk or in module-global state.
- Redaction covers all secret-wrapping types AND exception messages — no key bytes in any raised error; the SEC-01 regression test greps captured test-suite stdout/stderr for secret-shaped strings.

**Cloud-Sync Refusal & Passphrase UX**
- Detected cloud-sync path segments: Dropbox, OneDrive, iCloud (`Mobile Documents` / `CloudDocs`), Google Drive — case-insensitive path-segment match.
- Default behavior: hard refuse to run (raise) when `KEYSTORE_DIR` resolves under a detected cloud-sync path. **[OVERRIDDEN FROM RECOMMENDED]** An explicit opt-in override (e.g. `--allow-cloud-sync` flag / equivalent config or env switch) downgrades the refusal to a loud warning for advanced users who accept the risk. The override is off by default and mirrors the `--armed` philosophy: dangerous escape hatches require deliberate, warned opt-in. Success criterion 4 (synthetic-path refusal) is verified against the default path where the override is NOT set, so it remains satisfied.
- Passphrase confirm-on-create: re-prompt loop (up to 3 attempts) on mismatch, then abort; the passphrase is never echoed to the terminal.
- Passphrase strength policy: minimal — require non-empty, emit a gentle warning on a very short passphrase; do not enforce complexity rules on a local single-user tool.

### Claude's Discretion
- Exact module/function signatures, salt length (default to a secure random ≥16 bytes), Fernet token handling, and the precise override switch mechanism (flag vs env vs config) are at Claude's discretion within the decisions above.
- (Research addition, not in original CONTEXT.md discretion list but genuinely undecided): the default `KEYSTORE_DIR` path when the env var is unset (currently `Config.keystore_dir` defaults to `""`). See Open Questions.

### Deferred Ideas (OUT OF SCOPE)
- Hardware-signer (Ledger) path for the vault — explicitly v2 (VAULT-V2-01); Phase 2 uses the encrypted-file keystore only.
- Additional cloud providers beyond the core four (Mega/Box/pCloud) — can be added to the detection list later if needed; not required for v1.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SESS-01 | User can generate a fresh Solana keypair for a new session | `solders.keypair.Keypair()` verified (see Code Examples, "Keypair generation & serialization"). |
| SESS-04 | Session keys are encrypted at rest (scrypt → Fernet) and keystore files are owner-only (0600) | Scrypt/Fernet API verified end-to-end (roundtrip + wrong-key fail-closed, executed live). 0600 semantics verified cross-platform including the Windows ACL caveat (Pitfall 1). |
| SESS-05 | User can load a session keypair by pubkey with a passphrase; a wrong passphrase fails closed | `InvalidToken` verified to raise (not silently return garbage) on wrong-key decrypt; `Keypair.from_bytes` additionally validates secret/pubkey correspondence as defense-in-depth. |
| SEC-01 | No plaintext private key is ever written to disk or emitted to logs | JSON format keeps ciphertext-only on disk (locked decision); no-secret-in-logs regression test pattern researched (capsys + capfd + caplog, see Validation Architecture). |
| SEC-04 | System refuses to run when the keystore directory appears to be cloud-synced | Path-segment detection approach researched (realpath + case-insensitive segment match) with concrete default-path research per provider. |
| SEC-05 | Passphrase entry is confirmed on create, never echoed to the terminal, and never logged | `getpass.getpass` (already used in `config.py`) is the correct no-echo primitive; confirm/retry-loop pattern researched. |
</phase_requirements>

## Summary

Phase 2 builds a local, file-based encrypted keystore for Solana session keypairs using exactly the primitives already named in the project's stack research: `cryptography`'s `Scrypt` KDF (`hazmat.primitives.kdf.scrypt.Scrypt`) to derive a 32-byte key from the user's passphrase, and `Fernet` (symmetric authenticated encryption) to encrypt/decrypt the raw 64-byte `solders.Keypair` bytes. Every API in this phase was verified by direct execution in this session (not just read from docs), including the full roundtrip, the wrong-passphrase failure mode (`InvalidToken`), the `n`-must-be-power-of-2 constraint, and the exact `solders.Keypair` serialization surface (`bytes(kp)` / `kp.to_bytes()` = 64 bytes, `kp.secret()` = 32 bytes, `Keypair.from_bytes()` reconstructs and additionally *validates* the secret/pubkey correspondence — a second fail-closed layer beyond Fernet's own authentication tag).

The single highest-impact finding for this specific project is that **`os.chmod`/`os.open` mode bits do not actually restrict file access on Windows** — verified empirically on the development machine (win32): a file written with mode `0o600` reports `st_mode` as `0o666` and remains world-readable/writable at the OS level. Windows uses ACLs, not POSIX mode bits, and Python's `os` module only maps the DOS read-only attribute (`stat.S_IWRITE`/`S_IREAD`) on that platform — everything else is silently ignored, with no error raised. Since `bastion` targets a personal, single-machine setup and the dev/target environment observed in this session is Windows, this must be surfaced as a documented, tested limitation (a skip-reason on POSIX-only assertions, or a best-effort `icacls` call), not silently implied to work identically everywhere.

**Primary recommendation:** Build `keystore/crypto.py` around `Scrypt(salt, length=32, n=2**17, r=8, p=1).derive(passphrase.encode())` → `base64.urlsafe_b64encode(derived_key)` → `Fernet(fernet_key)`; encrypt/decrypt raw `bytes(keypair)` (64 bytes); catch `InvalidToken` and re-raise as a project-specific `KeystoreError` subclass (mirroring `bastion/rpc/errors.py`'s pattern) so callers never see the `cryptography`-internal exception type directly. Write files atomically (temp file + `os.replace`) with `os.open(..., os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)` on POSIX, and treat 0600 as best-effort-only on Windows with an explicit test skip/xfail plus a documented limitation, optionally layering a Windows-only `icacls` ACL restriction as defense-in-depth.

## Architectural Responsibility Map

> Note: `bastion` is a local, single-process CLI — there is no browser/CDN/hosted-API tier. The table below maps this phase's capabilities onto the equivalent **local-system tiers** (CLI/App layer, Crypto/Keystore module, Local Filesystem/OS, and the future Vault/Funder boundary) rather than a web-app tier set.

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Keypair generation (SESS-01) | Keystore module (`keystore/session.py`) | — | Pure in-memory operation via `solders`; no I/O until save. |
| Encryption at rest (SESS-04) | Keystore module (`keystore/crypto.py`) | Local Filesystem (OS) | Crypto primitives own the KDF/cipher; filesystem tier owns the actual bytes-on-disk and permission bits. |
| File permission enforcement (SESS-04) | Local Filesystem (OS) | Keystore module | The 0600 guarantee is fundamentally an OS/filesystem property (ACL on Windows, mode bits on POSIX) — the keystore module can only *request* it, not enforce it on every OS. |
| Load-by-pubkey + fail-closed (SESS-05) | Keystore module (`keystore/session.py`) | — | Filename lookup (`<pubkey>.json`) plus decrypt-or-raise is entirely within this module's responsibility. |
| No-secret-in-logs (SEC-01) | Cross-cutting (all tiers) | Keystore module (redaction types) | Enforced structurally via redacted `__repr__`/types, but must hold across CLI, config, and keystore layers — hence a regression test, not a single-module guarantee. |
| Cloud-sync refusal (SEC-04) | CLI/App layer (startup check) | Local Filesystem (OS, via `os.path.realpath`) | The decision to refuse-or-warn belongs to the app's startup sequence; the underlying path-resolution is an OS-level fact. |
| Passphrase confirm/no-echo (SEC-05) | CLI/App layer (`config.get_passphrase`, extended) | — | Terminal I/O belongs to the app/CLI layer; `getpass` is the OS-level no-echo primitive it wraps. |
| Vault/session import isolation (structural, feeds SEC-02/SEC-03) | Keystore module (`keystore/vault.py`) | Package/import-graph (static analysis) | This is enforced by Python's own module system (which files `import` which) — a structural fact checkable via `ast`, not a runtime behavior. |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `cryptography` | `>=46,<50` (latest verified: **49.0.0**, released on PyPI; `46.0.4` also currently installed and fully compatible) | `Scrypt` KDF + `Fernet` authenticated symmetric encryption for the keystore | [VERIFIED: pip index / PyPI JSON API] Maintained by PyCA (github.com/pyca/cryptography, 157 releases); the only serious, audited, general-purpose crypto library in the Python ecosystem. Already named in CLAUDE.md's locked stack. |
| `solders` | `>=0.27,<0.29` (latest verified: **0.28.0**; `0.27.1` also currently installed; no Keypair-API changes between the two per changelog) | `Keypair` generation, raw-bytes (de)serialization, pubkey string for filenames | [VERIFIED: pip index / PyPI JSON API / direct execution] Rust-backed (PyO3), maintained by kevinheavey (github.com/kevinheavey/solders, 46 releases). Already named in CLAUDE.md's locked stack; not yet a project dependency — added fresh in this phase. |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `getpass` (stdlib) | bundled | No-echo passphrase prompt | Already used by `bastion/config.py::get_passphrase()`; Phase 2 extends/reuses it for the confirm-on-create flow rather than re-implementing prompt logic. |
| `os` / `stat` (stdlib) | bundled | 0600 permission request, atomic write (`os.open` + `os.replace`) | POSIX: reliably restricts access. Windows: only toggles the DOS read-only bit — see Pitfall 1. |
| `ast` (stdlib) | bundled | Static import-graph isolation test for `keystore/vault.py` | Verified in this session: `ast.walk()` over `ast.Import`/`ast.ImportFrom` nodes reliably detects every import style (`import x`, `from x import y`, `import x as y`, function-local imports) without executing the target module. |
| `base64` (stdlib) | bundled | Encode the 32-byte scrypt-derived key into Fernet's required urlsafe-base64 form; encode salt/ciphertext for the JSON file | Fernet requires exactly a 32-byte urlsafe-base64-encoded key — verified by direct execution that `base64.urlsafe_b64encode(scrypt_output)` is sufficient with no other transform needed. |
| `json` (stdlib) | bundled | Keystore file serialization (locked decision: JSON format) | Self-describing, versioned, human-inspectable (contains no secrets in plaintext fields). |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| stdlib `os.chmod`/`os.open` mode bits (POSIX-only real enforcement) | `pywin32`'s `win32security` module or shelling out to `icacls` for a real Windows ACL restriction | Adds a Windows-only dependency (`pywin32`) or a subprocess call for a security property that is genuinely hard to get right; CONTEXT.md's decisions do not require this — documenting the limitation and testing for it (not silently claiming parity) satisfies the phase's honesty requirement. Left as an optional Claude's-discretion hardening step, not a hard requirement. |
| Custom cloud-sync path list hardcoded to today's default install locations | A broader heuristic (checking known env vars like `%OneDrive%`, `%OneDriveConsumer%`) in addition to path-segment matching | CONTEXT.md already locks the four segments (Dropbox/OneDrive/iCloud/Google Drive) as case-insensitive path-segment matches against the *resolved* path — this is more robust than env-var checks because users can and do relocate these folders, but env vars are an easy free confirmation for the common case and can be added as a supplementary signal. |
| `Fernet` (single-key symmetric) | `MultiFernet` (key-rotation-aware wrapper) | `MultiFernet` matters when you need to rotate the *encryption* key over time while keeping old ciphertexts decryptable with a list of keys. Not needed here — CONTEXT.md's versioned KDF-params-in-file design already handles evolving `n`/`r`/`p` per-file; each file carries its own salt and is decrypted with its own freshly-re-derived key from the user-supplied passphrase, so there is no stored key to rotate. |

**Installation:**
```bash
uv add cryptography solders
```

**Version verification:** [VERIFIED: pip index versions, executed 2026-07-07]
```
cryptography: latest 49.0.0 (currently installed in this environment: 46.0.4 — both fully support the Scrypt/Fernet API used here)
solders:      latest 0.28.0 (currently installed in this environment: 0.27.1 — no Keypair-API changes between them per CHANGELOG)
```
CLAUDE.md's existing stack table names `cryptography 49.0.0` and (implicitly via "0.27.x") an older `solders` line — `solders` has since released `0.28.0` with no breaking Keypair changes; either pin is safe. Recommend `solders>=0.27,<0.29` to allow picking up `0.28.0` without a follow-up bump, and `cryptography>=46,<50` matching CLAUDE.md's existing lower bound.

## Package Legitimacy Audit

| Package | Registry | Age | Downloads | Source Repo | Verdict | Disposition |
|---------|----------|-----|-----------|-------------|---------|-------------|
| `cryptography` | PyPI | Project started 2013 (pyca); 157 total releases; automated seam flagged "too-new" because it only inspected the *latest* release's publish timestamp | Not returned by seam (`null`); PyPI is one of the top-20 most-downloaded packages ecosystem-wide (widely known, not independently re-verified with a download-stats API in this session) | `github.com/pyca/cryptography` [VERIFIED: PyPI JSON API `project_urls`] | `SUS` (raw seam output) → **manually overridden to OK** | Approved — seam false-positive, see note below |
| `solders` | PyPI | 46 total releases; automated seam flagged "too-new" for the same reason (latest-release recency, not package age) | Not returned by seam (`null`) | `github.com/kevinheavey/solders` [VERIFIED: PyPI JSON API `project_urls`] | `SUS` (raw seam output) → **manually overridden to OK** | Approved — seam false-positive, see note below |

**Note on the SUS→OK override:** The `package-legitimacy check` seam flagged both packages `SUS` for reasons `["too-new", "unknown-downloads", "no-repository"]`. Direct inspection of the PyPI JSON API (`https://pypi.org/pypi/<pkg>/json`) [VERIFIED, executed this session] shows this is a false positive on the seam's side: "too-new" reflects the *publish date of the latest routine release* (both projects release frequently — this is normal maintenance cadence, not a brand-new/abandoned/typosquat signal), and "no-repository" is contradicted by `project_urls.source`/`project_urls.homepage` pointing to well-established, long-running GitHub repos (`pyca/cryptography` — the Python Cryptographic Authority's flagship library; `kevinheavey/solders` — the de facto Solana Rust-bindings library, already named in this project's own CLAUDE.md stack research from an earlier session). Both packages are already present in the local Python environment used for this research (`cryptography==46.0.4`, `solders==0.27.1`) and are the exact packages CLAUDE.md's own stack table names. This override is documented, not silent — the planner should still add a `checkpoint:human-verify`-style sanity check (e.g. confirm `uv add` resolves to the expected PyPI project, not a similarly-named package) before the first `uv add`, consistent with supply-chain conservatism (CLAUDE.md §10.6), but no additional friction beyond a one-time name-confirmation is warranted.

**Packages removed due to `[SLOP]` verdict:** none.
**Packages flagged as suspicious `[SUS]`:** `cryptography`, `solders` — both manually overridden to OK per the note above (recency-of-latest-release heuristic false positive); planner should still add one lightweight `checkpoint:human-verify` before first install confirming `uv add cryptography solders` resolves to `pyca/cryptography` and `kevinheavey/solders`.

## Architecture Patterns

### System Architecture Diagram

```
                         ┌─────────────────────────┐
                         │   CLI entry point        │
                         │  (Phase 7, not this      │
                         │   phase — but session    │
                         │   create/load flows are  │
                         │   exercised via tests     │
                         │   directly in Phase 2)    │
                         └────────────┬─────────────┘
                                      │
                    ┌─────────────────┼──────────────────┐
                    │                 │                  │
                    ▼                 ▼                  ▼
        ┌───────────────────┐ ┌──────────────┐  ┌──────────────────┐
        │ Startup safety     │ │ Passphrase   │  │ Config.keystore_dir│
        │ check:             │ │ confirm/     │  │ (Phase 1, reused) │
        │ cloud-sync refusal │ │ no-echo flow │  └─────────┬─────────┘
        │ (SEC-04)           │ │ (SEC-05,     │            │
        │ realpath() +       │ │ getpass +    │            │
        │ segment match      │ │ retry loop)  │            │
        └─────────┬──────────┘ └──────┬───────┘            │
                   │                  │                    │
                   │   raise (default)│ passphrase          │
                   │   or warn        │ string              │
                   │   (--allow-      │                    │
                   │    cloud-sync)   ▼                    │
                   │        ┌───────────────────────┐      │
                   └───────►│  keystore/session.py   │◄─────┘
                            │  generate()/save()/    │
                            │  load()/retire()        │
                            └───────────┬────────────┘
                                        │
                      generate: Keypair()│      load: read <pubkey>.json
                      → bytes(kp) (64B)  │      → salt, n/r/p, ciphertext
                                        ▼
                            ┌───────────────────────┐
                            │  keystore/crypto.py     │
                            │  Scrypt(salt,n,r,p)     │
                            │   .derive(passphrase)   │
                            │  → 32B key              │
                            │  base64.urlsafe_b64encode│
                            │  → Fernet(key)           │
                            │  .encrypt()/.decrypt()   │
                            └───────────┬─────────────┘
                                        │
                     encrypt: ciphertext│   decrypt: raw 64B keypair bytes
                     → JSON write        │   OR raise InvalidToken→KeystoreError
                     (0600, atomic       ▼   (fails CLOSED — SESS-05)
                      temp+replace)  ┌─────────────────────┐
                                     │ <KEYSTORE_DIR>/       │
                                     │  <pubkey>.json         │
                                     │  {version, kdf, n, r,  │
                                     │   p, salt, ciphertext} │
                                     └─────────────────────┘

        ┌─────────────────────────────────────────────────┐
        │  keystore/vault.py  — import-ISOLATED module       │
        │  load_vault() — only Phase 3's funder.py may import│
        │  this. Verified via ast-based static test that NO  │
        │  other current module (config, rpc, keystore.crypto│
        │  keystore.session) imports it.                     │
        └─────────────────────────────────────────────────┘
```

The primary use case (session create) traces: CLI/test call → cloud-sync check (raise/warn) → passphrase confirm loop → `Keypair()` generate → `bytes(kp)` → `crypto.encrypt()` (Scrypt derive → Fernet encrypt) → atomic 0600 JSON write. The load path traces: read `<pubkey>.json` → re-derive key from passphrase + stored salt/params → `Fernet.decrypt()` (raises `InvalidToken` on wrong passphrase, converted to a typed `KeystoreError` so the caller never sees a `cryptography`-internal exception) → `Keypair.from_bytes()` (a second, independent validation of secret/pubkey correspondence).

### Recommended Project Structure
```
bastion/
├── keystore/
│   ├── __init__.py
│   ├── crypto.py       # Scrypt→Fernet primitives, KDF param versioning, KeystoreCryptoError
│   ├── session.py      # SessionKeypair type, generate/save/load/retire, cloud-sync check, passphrase confirm flow
│   ├── vault.py        # load_vault() — import-isolated, no other module may import this in Phase 2
│   └── errors.py       # KeystoreError hierarchy (mirrors bastion/rpc/errors.py pattern)
tests/
└── unit/
    ├── test_keystore_crypto.py       # Scrypt/Fernet roundtrip, wrong-key InvalidToken, KDF param constraints
    ├── test_keystore_session.py      # generate/save/load/retire, 0600 (POSIX), fail-closed on wrong passphrase
    ├── test_keystore_cloud_sync.py   # synthetic cloud-sync path refusal + override warning path
    ├── test_keystore_passphrase_ux.py# confirm/retry-loop, no-echo, minimal-strength warning
    ├── test_keystore_no_secret_leak.py # capsys/capfd/caplog grep regression (SEC-01)
    └── test_keystore_vault_isolation.py # ast-based import-graph isolation test (structural SEC-02/SEC-03 precursor)
```

### Pattern 1: Versioned, self-describing keystore file format
**What:** Every keystore JSON file embeds its own `version` and full KDF parameter set (`kdf`, `n`, `r`, `p`, `salt`) alongside the ciphertext, rather than assuming a single global format.
**When to use:** Any time a fund-moving tool's on-disk secret format might need its cost parameters raised later (scrypt `n` will likely need to increase as hardware improves) — old files must remain loadable without a migration step, because `load()` reads whatever `n`/`r`/`p` that specific file recorded at creation time.
**Example:**
```python
# Illustrative shape only (not implementation) — matches CONTEXT.md's locked fields.
{
    "version": 1,
    "kdf": "scrypt",
    "n": 131072,
    "r": 8,
    "p": 1,
    "salt": "base64...",
    "ciphertext": "base64..."
}
```

### Pattern 2: Decrypt-or-raise, never decrypt-or-garbage
**What:** `Fernet.decrypt()` is authenticated (HMAC-verified) — a wrong key does not produce corrupted plaintext, it raises `cryptography.fernet.InvalidToken` [VERIFIED empirically this session]. The keystore's `load()` must let this propagate (wrapped in a typed `KeystoreError`), never catch-and-return `None`/empty bytes.
**When to use:** Every keystore `load()`/`unlock()` call site — this is the SESS-05 fail-closed contract.
**Example:**
```python
# Source: cryptography.io/en/latest/fernet/ + direct verification this session
from cryptography.fernet import Fernet, InvalidToken

try:
    plaintext = fernet.decrypt(ciphertext_bytes)
except InvalidToken as exc:
    raise KeystoreWrongPassphraseError("Incorrect passphrase or corrupted keystore file") from exc
```

### Pattern 3: Redacted secret-wrapping type (extends Phase 1's `repr=False` convention)
**What:** A dedicated type (e.g. `SessionKeypair`) wraps the decrypted key material and overrides `__repr__`/`__str__` to render `secret=REDACTED`, mirroring `bastion/config.py`'s existing `field(..., repr=False)` pattern for `Config`'s secret fields.
**When to use:** Any in-memory object that ever holds decrypted key bytes, matching CONTEXT.md's locked decision.
**Example:**
```python
# Pattern mirrors bastion/config.py's existing convention (repr=False on dataclass fields)
from dataclasses import dataclass, field

@dataclass
class SessionKeypair:
    pubkey: str
    _secret: bytes = field(repr=False)

    def __repr__(self) -> str:
        return f"SessionKeypair(pubkey={self.pubkey!r}, secret=REDACTED)"

    def __str__(self) -> str:
        return self.__repr__()
```

### Pattern 4: Atomic keystore write (temp file + `os.replace`)
**What:** Write the encrypted JSON to a temp file in the same directory, then `os.replace()` it onto the final `<pubkey>.json` path. `os.replace` is atomic on both POSIX and Windows (same-volume rename), so a crash mid-write never leaves a truncated/partial keystore file.
**When to use:** Every keystore file write (create), not append operations.
**Example:**
```python
# Standard atomic-write idiom; os.replace() is atomic same-volume on POSIX and Windows.
import os, tempfile

def _atomic_write_json(path: str, data: bytes) -> None:
    directory = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.chmod(tmp_path, 0o600)  # best-effort; no-op semantics on Windows, see Pitfall 1
    os.replace(tmp_path, path)  # atomic on same filesystem, both platforms
```

### Pattern 5: Import-graph isolation via static AST analysis (not a runtime import)
**What:** A test that parses (via `ast.parse`, never actually imports) every `.py` file under `bastion/` (excluding `bastion/keystore/vault.py` itself and the not-yet-existing `bastion/funder.py`) and asserts none of them contain an `Import`/`ImportFrom` node referencing `bastion.keystore.vault`.
**When to use:** Enforcing the SEC-02/SEC-03 structural precondition that only the funder may ever import `load_vault` — verified working in this session against all four import styles (`import x`, `from x import y`, `import x as y`, function-local `import`).
**Example:**
```python
# Source: verified by direct execution in this research session.
import ast
from pathlib import Path

FORBIDDEN_MODULE = "bastion.keystore.vault"
ALLOWED_IMPORTERS = {"bastion/keystore/vault.py"}  # extend with "bastion/funder.py" in Phase 3

def _find_vault_imports(py_file: Path) -> list[int]:
    tree = ast.parse(py_file.read_text(encoding="utf-8"))
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.startswith(FORBIDDEN_MODULE) for alias in node.names):
                lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == FORBIDDEN_MODULE or (
                module == "bastion.keystore" and any(a.name == "vault" for a in node.names)
            ):
                lines.append(node.lineno)
    return lines
```

### Anti-Patterns to Avoid
- **Catching `InvalidToken` and returning `None`/empty bytes:** Silently swallowing the exception breaks the fail-closed contract (SESS-05) — always re-raise as a typed `KeystoreError`.
- **Storing the derived Fernet key anywhere on disk or in a module-global:** Only the passphrase (never persisted) and the per-file salt/params (non-secret) may exist outside the `load()` call stack — CONTEXT.md's "surfaced per-call, never cached" decision.
- **Assuming `os.chmod(path, 0o600)` provides real protection on every OS:** Verified false on Windows this session — document and test the platform difference explicitly rather than asserting parity.
- **Hand-rolling AES/ChaCha instead of `Fernet`:** Explicitly forbidden by CLAUDE.md's "What NOT to Use" table — `Fernet` (HMAC-authenticated `AES-128-CBC`) is the only sanctioned symmetric primitive here.
- **Reusing one salt across multiple keystore files:** Each session's keystore file must have its own randomly generated salt (≥16 bytes) — reusing a salt defeats the point of scrypt's per-derivation cost and enables cross-file precomputation attacks.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Password-based key derivation | A custom PBKDF loop / hand-rolled iterated-hash construction | `cryptography.hazmat.primitives.kdf.scrypt.Scrypt` | Memory-hard KDFs are notoriously easy to get subtly wrong (missing memory-hardness, wrong iteration semantics); `cryptography`'s implementation is OpenSSL-backed and audited. Already the CLAUDE.md-mandated choice. |
| Authenticated symmetric encryption | Raw AES in CBC/CTR mode + a hand-wired HMAC | `cryptography.fernet.Fernet` | Fernet already bundles AES-128-CBC + HMAC-SHA256 + a versioned token format + timestamp, avoiding the classic "encrypt-then-MAC done wrong" bug class. Explicitly the CLAUDE.md-sanctioned choice; "don't substitute." |
| Ed25519 keypair generation/serialization | Manual key-byte manipulation via a raw `nacl`/`ed25519` library call | `solders.keypair.Keypair` | `solders` already validates key material on `from_bytes()` (verified: raises `ValueError` on a corrupted 64-byte blob rather than silently accepting it) — a hand-rolled deserializer would need to reimplement that consistency check itself. |
| Cross-platform secure file permissions | A bespoke ACL-manipulation layer for both POSIX and Windows | `os.chmod`/`os.open` on POSIX (works correctly); explicitly documented best-effort + optional `icacls` subprocess call on Windows | Building a full cross-platform ACL abstraction is a large, security-critical surface for a personal-use v1 tool — CONTEXT.md doesn't require it; document + test the real behavior on each OS instead of building a false abstraction. |
| Cloud-sync folder detection | A hardcoded, unmaintained list of exact default install paths | Case-insensitive path-**segment** matching against `os.path.realpath()` output | Default install locations for OneDrive/Dropbox/Google Drive/iCloud change across OS versions and are frequently relocated by users; matching on the *presence of a segment* (e.g. `"onedrive"` anywhere in the resolved path, case-insensitive) is more robust than matching a specific absolute default path. |

**Key insight:** Every crypto primitive this phase needs (KDF, authenticated encryption, key generation/validation) is already available, audited, and named in CLAUDE.md — there is no domain-specific reason to hand-roll any of it. The only genuinely custom logic in this phase is the *composition* (file format, permission requests, cloud-sync heuristics, redaction types) and the *tests* that prove the composition is fail-closed.

## Common Pitfalls

### Pitfall 1: `os.chmod`/`os.open` mode bits do not restrict access on Windows
**What goes wrong:** Code writes a keystore file with `os.chmod(path, 0o600)` (or `os.open(path, flags, 0o600)`) and the test/implementation assumes this makes the file owner-only. On Windows, `st_mode` reports `0o666` regardless — the file remains readable by any account with filesystem access.
**Why it happens:** Windows uses ACL-based permissions, not POSIX mode bits. Python's `os.chmod` on Windows only maps `stat.S_IWRITE`/`stat.S_IREAD` (the DOS read-only attribute) — every other bit (including group/other read/write) is silently ignored, with **no exception raised** to signal the no-op. [VERIFIED empirically this session: `os.chmod(path, 0o600)` and `os.open(path, os.O_CREAT|os.O_WRONLY|os.O_EXCL, 0o600)` both left `st_mode == 0o100666` on this Windows machine.]
**How to avoid:** Write the permission-setting code for POSIX correctness (it is genuinely correct there), but (a) add a platform-conditional test — assert exact `0o600` on POSIX (`platform.system() != "Windows"`), and on Windows either skip that specific assertion with a documented reason or assert the best-effort behavior actually achieved (DOS read-only flag set). (b) Document the limitation plainly in the module docstring, matching the project's "best-effort, not a guarantee" framing already used for memory zeroization. (c) Optionally (Claude's discretion, not required by CONTEXT.md) add a Windows-only `icacls` subprocess call to actually restrict the ACL to the current user, as defense-in-depth.
**Warning signs:** A "0600 permission" test that only ever runs/passes in CI on Linux/macOS and would silently pass-but-lie on a Windows dev machine if written as an unconditional `assert stat.S_IMODE(st.st_mode) == 0o600`.

### Pitfall 2: `Fernet` key must be *exactly* 32 bytes, urlsafe-base64-encoded — not the raw scrypt output
**What goes wrong:** Passing the raw 32-byte scrypt-derived key directly to `Fernet(key)` raises a `ValueError` ("Fernet key must be 32 url-safe base64-encoded bytes") because Fernet expects the *encoded* form, not raw bytes.
**Why it happens:** Fernet's key format is itself base64-encoded internally (it splits into a signing key and an encryption key after decoding) — `Fernet.generate_key()` already returns bytes in this encoded form, which is easy to miss when deriving a key from a KDF instead of generating one directly.
**How to avoid:** Always wrap the raw scrypt output: `base64.urlsafe_b64encode(derived_key_bytes)` before passing to `Fernet(...)`. [VERIFIED empirically this session — this exact transform produces a valid Fernet instance and successful roundtrip.]
**Warning signs:** A `ValueError` at `Fernet(key)` construction time (not at encrypt/decrypt time) is almost always this exact mistake.

### Pitfall 3: `Scrypt.derive()` can only be called once per instance
**What goes wrong:** Reusing the same `Scrypt(...)` object to derive a key twice (e.g. once to encrypt, again later to "verify" in the same call) raises `cryptography.exceptions.AlreadyFinalized`.
**Why it happens:** The KDF primitive is single-use by design (mirrors the underlying OpenSSL context lifecycle). [VERIFIED empirically this session: second `derive()` call raises `AlreadyFinalized: Context was already finalized.`]
**How to avoid:** Construct a fresh `Scrypt(salt=..., length=32, n=..., r=..., p=...)` instance every time a key needs to be (re-)derived (e.g. once on encrypt, once on each decrypt attempt) — never cache/reuse the KDF object itself (only its *parameters*, which are the non-secret, stored `n`/`r`/`p`/`salt`).
**Warning signs:** `AlreadyFinalized` raised from inside a "verify passphrase" or "retry" code path that reused a KDF object across attempts.

### Pitfall 4: `n` must be a power of two, and larger `n` values are real, user-visible latency/memory costs
**What goes wrong:** An arbitrary `n` (e.g. `100000`) raises `ValueError: n must be greater than 1 and be a power of 2.` at `Scrypt()` construction. Separately, choosing too-large an `n` (e.g. `2**20`) makes every unlock take several seconds and ~1GB+ of memory, which is a poor UX on a CLI tool that unlocks per-session.
**Why it happens:** scrypt's cost parameter is defined as a power-of-two by the algorithm itself (RFC 7914); the `cryptography` binding enforces this at construction time rather than silently rounding.
**How to avoid:** Use exactly the CONTEXT.md-locked `n=2**17, r=8, p=1` (verified this session: ~0.36s and ~128MB per derivation on this machine — acceptable for a per-unlock cost). Store `n`, `r`, `p` in the file (already locked) so future increases don't break old files; never accept an arbitrary user-supplied `n` without validating power-of-two first (fail loudly, matching the project's existing `ConfigError`-style "fail loud on malformed value" convention from `bastion/config.py`).
**Warning signs:** `ValueError` at KDF construction time when parsing an old or hand-edited keystore file's stored `n`.

### Pitfall 5: Decrypted secret bytes cannot be fully zeroized in Python — document, don't overclaim
**What goes wrong:** Code claims "the key is wiped from memory after use" without qualification. In reality: `Fernet.decrypt()` returns an **immutable** `bytes` object (verified: `TypeError: 'bytes' object does not support item assignment` when attempting in-place zeroing); `Keypair.from_bytes()`/`.secret()` similarly returns immutable `bytes` (verified: `type(kp.secret())` is `bytes`, not `bytearray`). Only an *explicitly constructed* `bytearray` can be zeroed in place (`ba[:] = b'\x00' * len(ba)` verified to work).
**Why it happens:** CPython's `bytes` type is immutable by design; any function returning `bytes` (which is the vast majority of the crypto/solders surface here) hands back a value that cannot be overwritten in place — only converted to a *new* `bytearray` copy (which then coexists with the original immutable `bytes` object until GC).
**How to avoid:** Convert to `bytearray` as early as possible after decrypt if zeroization matters for a given code path, zero that `bytearray` explicitly on retire, and **document the limitation honestly** in the module docstring per CONTEXT.md's locked decision ("explicitly documented as best-effort... Python cannot guarantee zeroization of all copies") rather than presenting it as a real security guarantee.
**Warning signs:** Any docstring/comment asserting memory is "securely wiped" or "guaranteed zeroed" without the best-effort qualifier — this is a documentation-accuracy bug, not just a security nuance, given this is a fund-moving tool's stated non-custodial posture.

## Code Examples

### Scrypt → Fernet encrypt/decrypt roundtrip (verified end-to-end this session)
```python
# Source: cryptography.io docs + direct execution/verification in this research session.
import base64
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

def _derive_fernet_key(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    kdf = Scrypt(salt=salt, length=32, n=n, r=r, p=p)  # fresh instance every call (Pitfall 3)
    derived = kdf.derive(passphrase.encode("utf-8"))
    return base64.urlsafe_b64encode(derived)  # Fernet requires urlsafe-b64 form (Pitfall 2)

def encrypt_keypair_bytes(passphrase: str, raw_keypair_bytes: bytes) -> tuple[bytes, bytes]:
    """Returns (salt, ciphertext) — both go into the keystore JSON as base64 fields."""
    salt = os.urandom(16)
    key = _derive_fernet_key(passphrase, salt, n=2**17, r=8, p=1)
    ciphertext = Fernet(key).encrypt(raw_keypair_bytes)
    return salt, ciphertext

def decrypt_keypair_bytes(passphrase: str, salt: bytes, ciphertext: bytes, n: int, r: int, p: int) -> bytes:
    """Raises InvalidToken on wrong passphrase — caller wraps this in a typed KeystoreError."""
    key = _derive_fernet_key(passphrase, salt, n, r, p)
    return Fernet(key).decrypt(ciphertext)  # verified: raises InvalidToken on wrong key, never garbage
```

### Keypair generation & serialization (verified against installed solders 0.27.1 — NOT `to_bytes_array`, which does not exist)
```python
# Source: direct execution against installed solders==0.27.1 this session.
from solders.keypair import Keypair

kp = Keypair()                      # fresh random keypair
raw_64 = bytes(kp)                  # == kp.to_bytes() -- 64 bytes: 32 secret + 32 pubkey
secret_32 = kp.secret()             # 32-byte secret only (immutable bytes, Pitfall 5)
pubkey_str = str(kp.pubkey())       # base58 string, used as the "<pubkey>.json" filename

# Reconstruction — from_bytes VALIDATES secret/pubkey correspondence (defense-in-depth):
restored = Keypair.from_bytes(raw_64)
assert bytes(restored) == raw_64
# Corrupted/mismatched 64 bytes raise ValueError("signature error") here, verified this session --
# a second fail-closed layer beyond Fernet's own authentication tag.
```

### No-secret-in-logs regression test pattern (capsys + capfd + caplog)
```python
# Source: docs.pytest.org/en/stable/how-to/capture-stdout-stderr.html + this project's existing
# test_config.py convention (UNMISTAKABLE sentinel values to grep for).
import logging

def test_no_secret_in_output(capsys, caplog, tmp_path):
    caplog.set_level(logging.DEBUG)
    sentinel_passphrase = "UNMISTAKABLE-TEST-PASSPHRASE-VALUE"
    # ... exercise generate/save/load/retire using sentinel_passphrase ...

    captured = capsys.readouterr()  # Python-level sys.stdout/sys.stderr only
    assert sentinel_passphrase not in captured.out
    assert sentinel_passphrase not in captured.err
    assert sentinel_passphrase not in caplog.text
    # If any dependency here were a C-extension/subprocess writing directly to OS
    # file descriptors, capfd (not capsys) would be required to catch it --
    # cryptography and solders are both compiled extensions, so prefer capfd
    # over capsys for this specific regression test as the stronger guarantee.
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|---------------|--------|
| `websockets.legacy` client API (irrelevant to this phase, carried from Phase 1 research for context) | `websockets.asyncio.client.connect` | Deprecated with removal path through 2030 | Not applicable to Phase 2 — no networking here — noted only to confirm no cross-phase regression. |
| GPG-signed releases | PyPI Trusted Publishing (OIDC) + Sigstore attestations | Industry-wide shift, PyPI removed GPG upload support | Not this phase's concern (Phase 8/DIST-05), noted for completeness per CLAUDE.md. |
| `cryptography`'s `backend=` parameter on KDF/cipher constructors | Backend parameter is vestigial/optional — omit it | `cryptography` has defaulted to a single backend for several major versions | [VERIFIED this session: `Scrypt(..., backend=None)` is still silently accepted for backward compatibility, but is unnecessary — omit it in new code.] |

**Deprecated/outdated:**
- Passing an explicit `backend=` argument to `Scrypt`/`Fernet`-adjacent primitives: no longer required; omit for new code (still accepted, not an error, but adds noise).

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Exact default install-path segments/env-vars for OneDrive/Dropbox/Google Drive/iCloud across every OS version (only partially confirmed by web search; some claims — e.g. Google Drive's `~/Library/CloudStorage/GoogleDrive-<email>` path — are from training knowledge, not freshly verified this session) | Standard Stack / "Alternatives Considered" / Code context for cloud-sync detection | Low — CONTEXT.md's locked design already matches on path *segments* (`"onedrive"`, `"dropbox"`, `"icloud"`/`"mobile documents"`/`"clouddocs"`, `"google drive"`), which is deliberately robust to exact-path drift; getting one platform's exact default path slightly wrong does not break the segment-match approach, only reduces coverage of that one platform's non-default install location. |
| A2 | An `icacls`-based (or `pywin32`) real ACL restriction on Windows is feasible as a follow-up hardening step | Alternatives Considered / Pitfall 1 | Low — this is explicitly optional (Claude's discretion, not a CONTEXT.md requirement); if infeasible, the documented best-effort + platform-conditional test is still a complete, honest v1. |

**If this table is empty:** N/A — two low-risk assumptions remain, both already deliberately scoped as non-blocking by CONTEXT.md's design (segment-matching robustness; ACL hardening being optional).

## Open Questions

1. **What is the default `KEYSTORE_DIR` when the env var is unset?**
   - What we know: `bastion/config.py`'s `Config.keystore_dir` currently defaults to `""` (empty string) when `KEYSTORE_DIR` is not set in the environment — Phase 1 made no decision about a fallback path.
   - What's unclear: Whether Phase 2's `keystore/session.py` should (a) raise a clear `KeystoreError` if `keystore_dir == ""` at call time, or (b) supply its own sensible default (e.g. `Path.home() / ".bastion" / "keystore"`) when the config value is empty.
   - Recommendation: This wasn't in CONTEXT.md's locked decisions or explicit discretion list — flag for the planner to make an explicit task-level decision (likely: raise clearly if empty, since a silent default path could itself end up under a cloud-synced home directory in some setups, undermining SEC-04's intent — an explicit error forces the user to set `KEYSTORE_DIR` deliberately).

2. **Exact override-switch mechanism for `--allow-cloud-sync` (flag vs env vs config)**
   - What we know: CONTEXT.md locks the *behavior* (hard refuse by default; explicit opt-in downgrades to warning) but leaves the *mechanism* to Claude's discretion.
   - What's unclear: Whether Phase 2 should introduce a CLI flag (even though CLI wiring is nominally Phase 7) or an env var (`BASTION_ALLOW_CLOUD_SYNC=1`) / `Config` field for now, with CLI flag wiring deferred to Phase 7.
   - Recommendation: Given Phase 7 (CLI + Mainnet Shakeout) is where `start`/`end`/`monitor` subcommands are actually built, Phase 2 should expose this as a `Config`-level field or an explicit function parameter (e.g. `check_cloud_sync(path, allow_override: bool)`), keeping the interactive CLI flag wiring as a thin Phase 7 concern that reads the same underlying config/parameter.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `cryptography` | Scrypt KDF + Fernet (SESS-04) | ✓ (globally, not yet a project dep) | 46.0.4 installed / 49.0.0 latest on PyPI | Add via `uv add cryptography` — no fallback needed, this is the sanctioned library. |
| `solders` | Keypair generation/serialization (SESS-01, SESS-05) | ✓ (globally, not yet a project dep) | 0.27.1 installed / 0.28.0 latest on PyPI | Add via `uv add solders` — no fallback needed. |
| `uv` | Dependency install/lock | ✓ | 0.10.2 | — |
| Windows ACL tooling (`icacls`/`pywin32`) | Optional Windows-hardening of 0600 (not required by CONTEXT.md) | Not verified this session (would need explicit `subprocess`/`pywin32` check) | — | Skip — best-effort `os.chmod` + documented limitation is sufficient for CONTEXT.md's scope. |

**Missing dependencies with no fallback:** none — both core libraries are confirmed installable and functionally verified.

**Missing dependencies with fallback:** none beyond the explicitly-optional Windows ACL hardening noted above.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | `pytest` 9.1.1+ / `pytest-asyncio` 1.4.0+ (already project dev-deps; this phase's tests are all synchronous, no new async-test needs) |
| Config file | `pyproject.toml` `[tool.pytest.ini_options]` (`testpaths = ["tests"]`, existing) |
| Quick run command | `uv run pytest tests/unit/test_keystore_crypto.py tests/unit/test_keystore_session.py -x` |
| Full suite command | `uv run pytest` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| SESS-01 | Fresh keypair generated, unique each call | unit | `pytest tests/unit/test_keystore_session.py::test_generate_produces_unique_keypair -x` | ❌ Wave 0 |
| SESS-04 | Encrypt→decrypt roundtrip recovers exact keypair; file is 0600 (POSIX) with versioned KDF params in file | unit | `pytest tests/unit/test_keystore_crypto.py::test_roundtrip_recovers_exact_keypair -x` and `test_keystore_session.py::test_save_sets_0600_on_posix -x` | ❌ Wave 0 |
| SESS-05 | Load-by-pubkey works; wrong passphrase raises (fails closed), never returns partial/garbage key | unit | `pytest tests/unit/test_keystore_session.py::test_wrong_passphrase_fails_closed -x` | ❌ Wave 0 |
| SEC-01 | No plaintext key bytes / secret-shaped strings in captured stdout/stderr/log output across the full generate/save/load/retire flow | unit (regression) | `pytest tests/unit/test_keystore_no_secret_leak.py -x` | ❌ Wave 0 |
| SEC-04 | Synthetic cloud-sync path (containing "OneDrive"/"Dropbox"/"Mobile Documents"/"CloudDocs"/"Google Drive" segment) causes refusal by default; `--allow-cloud-sync`-equivalent downgrades to warning | unit | `pytest tests/unit/test_keystore_cloud_sync.py -x` | ❌ Wave 0 |
| SEC-05 | Passphrase confirm-on-create with mismatch retry (up to 3), never echoed (`getpass` used, not `input`), minimal-strength warning on very short passphrase | unit | `pytest tests/unit/test_keystore_passphrase_ux.py -x` | ❌ Wave 0 |
| (structural, feeds SEC-02/SEC-03) | Only `keystore/vault.py` itself (soon, `funder.py`) may import `bastion.keystore.vault` | unit (static/AST) | `pytest tests/unit/test_keystore_vault_isolation.py -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** the quick-run command scoped to the file(s) just touched.
- **Per wave merge:** `uv run pytest` (full suite).
- **Phase gate:** Full suite green before `/gsd-verify-work`.

### Wave 0 Gaps
- [ ] `tests/unit/test_keystore_crypto.py` — covers SESS-04 (roundtrip, wrong-key, KDF param constraints)
- [ ] `tests/unit/test_keystore_session.py` — covers SESS-01, SESS-04 (0600), SESS-05
- [ ] `tests/unit/test_keystore_cloud_sync.py` — covers SEC-04
- [ ] `tests/unit/test_keystore_passphrase_ux.py` — covers SEC-05
- [ ] `tests/unit/test_keystore_no_secret_leak.py` — covers SEC-01 (capsys+capfd+caplog regression, matching the "UNMISTAKABLE-..." sentinel convention already used in `test_config.py`)
- [ ] `tests/unit/test_keystore_vault_isolation.py` — covers the structural import-isolation precondition for SEC-02/SEC-03
- [ ] No new framework/fixture install needed — `pytest`/`pytest-asyncio`/`monkeypatch`/`tmp_path` are already project dev-deps and used identically in `tests/unit/test_config.py`.

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-------------------|
| V2 Authentication | Partial | The passphrase is a local, single-user secret unlocking a local file, not a network-facing auth credential — `getpass` no-echo entry + scrypt-derived key stand in for a full V2 authentication scheme (no session tokens/MFA applicable to a local CLI). |
| V3 Session Management | No | No network sessions in this phase; "session" here means "trading session wallet," not an ASVS auth session. |
| V4 Access Control | Yes | Filesystem-level access control (0600 / ACL) is the entire access-control mechanism for keystore files — see Pitfall 1 for the Windows caveat. |
| V5 Input Validation | Yes | KDF params (`n`/`r`/`p`) read from a keystore file must be validated (power-of-two `n`, positive `r`/`p`) before use, matching `bastion/config.py`'s existing "fail loud, never silently default" `ConfigError` convention. |
| V6 Cryptography | Yes | `Scrypt` (KDF) + `Fernet` (AEAD) — both from `cryptography`, never hand-rolled, per CLAUDE.md's explicit "What NOT to Use" prohibition on custom crypto. |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|----------------------|
| Wrong-passphrase silently returning garbage/partial key material instead of failing | Tampering / Information Disclosure | `Fernet.decrypt()`'s built-in HMAC authentication raises `InvalidToken` on any tampering or wrong key — verified this session; never catch-and-swallow this exception. |
| Keystore file readable by other local users/processes | Information Disclosure | 0600 permissions on write (POSIX-correct; Windows best-effort, see Pitfall 1); atomic write prevents a partial/racy file being briefly world-readable during creation. |
| Passphrase or key bytes leaking via logs, stdout, or exception messages | Information Disclosure | Redacted `__repr__`/`__str__` on all secret-wrapping types; typed exceptions that never interpolate secret values into their message string (mirrors `bastion/rpc/errors.py`'s existing "message-only, no secrets" convention); capsys+capfd+caplog regression test. |
| Keystore directory silently synced to a third-party cloud provider, exposing the encrypted file (and, if the passphrase is weak, its ciphertext) off-machine | Information Disclosure / Elevation of Privilege (via a compromised cloud account) | Cloud-sync path-segment detection + hard refuse-by-default (SEC-04), matching the project's non-custodial, single-machine posture (CLAUDE.md's "no server/hosted service in the fund path" out-of-scope line extends conceptually to "no unintentional cloud replication of secrets either"). |
| A non-funder module accidentally importing `load_vault` and gaining vault-secret access it shouldn't have | Elevation of Privilege | Structural import-graph isolation (this phase's `keystore/vault.py` isolation + the AST-based static test), which is the load-bearing precondition for Phase 3's SEC-02 and Phase 5's SEC-03. |

## Sources

### Primary (HIGH confidence — verified by direct execution this session)
- Installed `cryptography==46.0.4` — `Scrypt`/`Fernet` roundtrip, `InvalidToken` on wrong key, `AlreadyFinalized` on double-`derive()`, `n`-power-of-two `ValueError`, `backend=` still-accepted-but-optional, timing (~0.36s) and memory (~128MB) for `n=2**17,r=8,p=1`.
- Installed `solders==0.27.1` — `Keypair()`, `bytes(kp)`/`kp.to_bytes()` (64 bytes), `kp.secret()` (32 bytes, immutable `bytes`), `Keypair.from_bytes()` round-trip and its `ValueError("signature error")` on corrupted input, `str(kp.pubkey())` (44-char base58).
- `os.chmod`/`os.open` mode-bit behavior on this Windows (win32) machine — verified `st_mode` remains `0o666` regardless of requested `0o600`.
- `ast`-based import-detection approach — verified against all four import syntaxes (`import x`, `from x import y`, `import x as y`, function-local `import`).
- `bytearray` in-place zeroization vs. `bytes` immutability — verified both behaviors directly.
- PyPI JSON API (`https://pypi.org/pypi/<pkg>/json`) — `cryptography` and `solders` `project_urls`/release counts, overriding the `package-legitimacy check` seam's `SUS` false-positive.
- `pip index versions cryptography` / `pip index versions solders` — confirmed latest PyPI versions (49.0.0 / 0.28.0 respectively) against this project's target ecosystem (PyPI, not npm/crates).

### Secondary (MEDIUM confidence — WebFetch/WebSearch of official or near-official sources)
- `https://cryptography.io/en/latest/hazmat/primitives/key-derivation-functions/` — Scrypt constructor params, RFC 7914 `n`/`r`/`p` guidance.
- `https://cryptography.io/en/latest/fernet/` — Fernet key format, `encrypt`/`decrypt`/`InvalidToken` semantics.
- `https://kevinheavey.github.io/solders/api_reference/keypair.html` and `https://github.com/kevinheavey/solders/blob/main/CHANGELOG.md` — Keypair API surface and confirmation of no breaking changes 0.27.x→0.28.0.
- `https://docs.pytest.org/en/stable/how-to/capture-stdout-stderr.html` — `capsys` vs `capfd` capture-layer distinction.
- Python documentation (via WebSearch aggregation) on `os.chmod` Windows-only `S_IWRITE`/`S_IREAD` behavior — cross-checked against this session's own empirical Windows test, which matched exactly.

### Tertiary (LOW confidence — WebSearch only, flagged for validation, see Assumptions Log A1)
- Exact default install paths/env-vars for OneDrive/Dropbox/Google Drive/iCloud across every OS/version — partially confirmed (OneDrive `%OneDrive%`/`%OneDriveConsumer%`, Dropbox `%USERPROFILE%\Dropbox`), partially from training knowledge (Google Drive `~/Library/CloudStorage/GoogleDrive-<email>`, iCloud `~/Library/Mobile Documents/com~apple~CloudDocs`). Low risk per Assumption A1 — CONTEXT.md's segment-matching design is robust to this uncertainty.

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — both core libraries verified installed, versions confirmed against PyPI registry, exact API surface confirmed by direct execution (not just docs reading).
- Architecture: HIGH — file format, module boundaries, and import-isolation test approach all directly verified or directly derivable from CONTEXT.md's locked decisions.
- Pitfalls: HIGH — all five documented pitfalls were reproduced by direct execution in this session, not inferred from training data alone.
- Cloud-sync default paths: LOW (see Assumptions Log A1) — does not block planning since the locked design (segment matching) is deliberately robust to this gap.

**Research date:** 2026-07-07
**Valid until:** 2026-08-06 (30 days — stable, mature libraries; re-verify package versions if planning is delayed past this window, per CLAUDE.md's own version-compatibility table which is already dated to this same research cycle).
