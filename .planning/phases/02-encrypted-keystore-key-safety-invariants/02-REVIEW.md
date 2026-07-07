---
phase: 02-encrypted-keystore-key-safety-invariants
reviewed: 2026-07-07T00:00:00Z
depth: deep
files_reviewed: 13
files_reviewed_list:
  - bastion/keystore/__init__.py
  - bastion/keystore/errors.py
  - bastion/keystore/crypto.py
  - bastion/keystore/vault.py
  - bastion/keystore/cloudsync.py
  - bastion/keystore/passphrase.py
  - bastion/keystore/session.py
  - tests/unit/test_keystore_crypto.py
  - tests/unit/test_keystore_session.py
  - tests/unit/test_keystore_no_secret_leak.py
  - tests/unit/test_keystore_vault_isolation.py
  - tests/unit/test_keystore_cloud_sync.py
  - tests/unit/test_keystore_passphrase_ux.py
findings:
  critical: 0
  warning: 3
  info: 4
  total: 7
status: issues
---

# Phase 2: Code Review Report — Encrypted Keystore + Key-Safety Invariants

**Reviewed:** 2026-07-07
**Depth:** deep (cross-file, fund-safety focus)
**Files Reviewed:** 13 (7 source + 6 test)
**Status:** issues_found

## Summary

The crypto core is well-built and the highest-stakes invariants hold:

- **Fail-closed decrypt is correct.** `decrypt_secret` catches *only* `InvalidToken`
  (not a broad `except`), re-raises a typed `KeystoreWrongPassphraseError`, and
  never returns partial/garbage bytes. `load()` adds a second `Keypair.from_bytes`
  validation layer. There is no fail-open path.
- **No plaintext to disk.** The on-disk blob is ciphertext-only; the secret exists
  only inside Fernet's `ciphertext` field. Verified by tests and by reading the
  write path (`save` -> `encrypt_secret` -> `_atomic_write_json`).
- **Redacted repr is robust.** `SessionKeypair` defines an explicit `__repr__`
  (dataclass does not overwrite a user-defined method) *and* marks `_secret` with
  `field(repr=False)`. `Config.vault_secret` is `field(repr=False)`. No secret
  reaches stdout/stderr/logs (capfd + caplog regression passes).
- **KDF params correct:** scrypt n=2^17/r=8/p=1, fresh 16-byte CSPRNG salt per
  encryption, Fernet key = urlsafe-b64 of 32-byte scrypt output. Salt is stored
  per-file and re-read on decrypt (forward-compat).
- **Cloud-sync refusal** is realpath-resolved, case-insensitive, segment-matched;
  empty `KEYSTORE_DIR` fails loud regardless of override.
- **Vault isolation** enforced by a static AST import-graph test across all four
  import syntaxes.

No BLOCKER-severity (fund-loss / fail-open / secret-leak) defect was found. The
issues below are a latent path-traversal in the load/retire API, two
fail-loud-contract gaps on a tampered keystore file, and minor robustness items.

## Warnings

### WR-01: Unvalidated `pubkey` allows path traversal in `load()` and `retire()`

**File:** `bastion/keystore/session.py:127` (load), `:150` (retire)
**Issue:** Both functions build the target path as
`os.path.join(keystore_dir, f"{pubkey}.json")` with **no validation that `pubkey`
is a real base58 Solana pubkey.** `os.path.join` does not contain traversal:
- `pubkey = "../../etc/passwd"` -> reads/deletes outside `keystore_dir`.
- `pubkey = "/etc/shadow"` (or `C:\...` on Windows) -> `os.path.join` discards
  `keystore_dir` entirely and yields the absolute path `"/etc/shadow.json"`.

In `load()` this becomes an arbitrary-`*.json` file *read* (contents are then fed
to the decrypt path). In `retire()` it becomes an arbitrary-`*.json` file
**deletion** via `os.remove`. Today no caller passes untrusted input, but these
are the public API and the docstrings describe "load by pubkey" — the CLI
`end <pubkey>` wiring in a later phase will pass user-supplied argv straight in.
This is a latent arbitrary-file-delete / arbitrary-file-read.

**Fix:** Validate the pubkey before touching the filesystem. Reject anything that
is not a canonical base58 Ed25519 pubkey:
```python
from solders.pubkey import Pubkey

def _safe_pubkey(pubkey: str) -> str:
    try:
        Pubkey.from_string(pubkey)  # raises on non-base58 / wrong length
    except (ValueError, TypeError) as exc:
        raise KeystoreConfigError("Invalid session pubkey.") from exc
    if "/" in pubkey or "\\" in pubkey or os.sep in pubkey or pubkey in (".", ".."):
        raise KeystoreConfigError("Invalid session pubkey.")
    return pubkey
```
Call it at the top of both `load()` and `retire()` (and it is a no-op for the
`save()` path since that pubkey is derived from a generated `Keypair`).

### WR-02: No upper bound on stored scrypt `n` — tampered file causes OOM, not a typed fail-loud error

**File:** `bastion/keystore/crypto.py:51-54` (`_validate_kdf_params`), consumed at `:106-110`
**Issue:** `_validate_kdf_params` requires `n` to be an int > 1 and a power of two,
but imposes **no ceiling.** scrypt memory cost is ~`128 * n * r` bytes. A
hand-edited or corrupted keystore file setting e.g. `n = 2**30` forces a ~1 TB
allocation on load. `cryptography`'s Rust/OpenSSL Scrypt binding here uses a
`maxmem` of `sys.maxsize // 2` (effectively unbounded), so this surfaces as a real
`MemoryError`/OOM crash — not the `KeystoreConfigError` the module contract
promises ("Stored KDF parameters ... are validated before any derivation is
attempted; malformed values raise `KeystoreConfigError` (fail loud)"). The
docstring even flags this exact risk ("no trust of attacker-controllable n causing
resource exhaustion on load"). The guard does not deliver it.

**Fix:** Add an explicit ceiling (the current locked default is `2**17`; allow some
forward-compat headroom but cap resource use):
```python
KDF_N_MAX = 2**22  # ~4 GB scrypt working set ceiling; well above the 2**17 default
...
if isinstance(n, bool) or not isinstance(n, int) or n <= 1 or (n & (n - 1)) != 0 or n > KDF_N_MAX:
    raise KeystoreConfigError(
        "Malformed keystore KDF parameter: n must be a power of two in a bounded range."
    )
# similarly bound r and p (e.g. r <= 32, p <= 16)
```

### WR-03: Malformed blob raises raw `KeyError`/`binascii.Error` instead of typed `KeystoreConfigError`

**File:** `bastion/keystore/crypto.py:106,109,113`
**Issue:** `decrypt_secret` reads `blob["n"], blob["r"], blob["p"]` (line 106),
`blob["salt"]` (109), and `blob["ciphertext"]` (113) via direct subscript, and
b64-decodes the salt with no error handling. A truncated / hand-edited keystore
file that is missing a field raises a bare `KeyError`; a salt that is not valid
base64 raises `binascii.Error` (a `ValueError`). Neither is a `KeystoreError`
subtype, so callers relying on the documented typed hierarchy (fail loud) get an
untyped exception that could read as an internal bug rather than "corrupt
keystore." The corrupt-file contract is only honored for the ciphertext-auth path.

**Fix:** Wrap the parse in a typed error:
```python
try:
    n, r, p = blob["n"], blob["r"], blob["p"]
    salt = base64.urlsafe_b64decode(blob["salt"])
    token = blob["ciphertext"].encode("ascii")
except (KeyError, TypeError, ValueError, binascii.Error) as exc:
    raise KeystoreConfigError("Malformed or corrupted keystore file.") from exc
```
(Then run `_validate_kdf_params` and derive as today.)

## Info

### IN-01: Temp file leaked if `os.write`/`os.chmod`/`os.replace` raises

**File:** `bastion/keystore/session.py:84-90`
**Issue:** `_atomic_write_json` creates `.tmp-*.json` via `mkstemp`, but only the
`fd` is guaranteed closed (the `finally`). If `os.write`, `os.chmod`, or
`os.replace` raises, the temp file is left orphaned in `keystore_dir`. It holds
ciphertext (not plaintext), so this is not a secret leak — only clutter that could
accumulate. The happy-path "no temp left behind" test does not cover the error
path.
**Fix:** Wrap the post-mkstemp body in `try/except`, and on failure
`os.unlink(tmp_path)` (ignoring `FileNotFoundError`) before re-raising.

### IN-02: No `fsync` before `os.replace` — durability gap (atomicity is fine)

**File:** `bastion/keystore/session.py:86-90`
**Issue:** The write is `os.write` then `os.replace` with no `os.fsync(fd)` (and no
directory fsync). `os.replace` guarantees no *truncated* file ever appears at the
final path (the atomicity claim in the docstring holds), but on a power loss the
renamed file's contents may not be durably flushed, yielding an empty/zero-length
keystore after reboot. For a disposable session wallet that can be regenerated
this is low-impact, but worth an explicit `os.fsync(fd)` before `os.close(fd)`
given the fund-safety posture.

### IN-03: Cloud-sync segment list omits several common providers

**File:** `bastion/keystore/cloudsync.py:28-34`
**Issue:** `CLOUD_SYNC_SEGMENTS` covers Dropbox/OneDrive/Google Drive/iCloud, but
not other mainstream sync clients whose default folders are equally dangerous:
`box`, `pcloud`, `mega`, `nextcloud`, `proton drive`, `sync` / `sync.com`,
`yandex.disk`. The segment-substring approach is sound; the list is just
non-exhaustive. Detection is documented as best-effort, so this is an enhancement,
not a defect. (Note: adding a bare `"box"` or `"sync"` segment risks false
positives on common directory names — prefer more specific tokens.)

### IN-04: `save()` into a non-existent, non-cloud dir raises raw `FileNotFoundError`

**File:** `bastion/keystore/session.py:84` (via `mkstemp(dir=...)`)
**Issue:** If `keystore_dir` passes the cloud-sync/empty guards but does not exist,
`mkstemp(dir=directory, ...)` raises a bare `FileNotFoundError` rather than a typed
`KeystoreConfigError` or creating the directory (with 0700). Minor UX/contract
inconsistency with the module's otherwise fail-loud-typed posture.
**Fix:** Either `os.makedirs(keystore_dir, mode=0o700, exist_ok=True)` in `save()`
before writing, or wrap the `mkstemp` call and re-raise as `KeystoreConfigError`.

---

_Reviewed: 2026-07-07_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: deep_
