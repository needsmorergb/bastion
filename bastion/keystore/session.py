"""The session-keystore lifecycle: generate, save, load, retire.

Assembles the crypto core (``bastion.keystore.crypto``) and the safety rails
(``bastion.keystore.cloudsync``) into the user-visible flow for a disposable
session wallet (SESS-01, SESS-04, SESS-05, SEC-01). This module is the ONLY
place that reads/writes ``<pubkey>.json`` keystore files.

``SessionKeypair`` wraps the decrypted 64-byte keypair as a ``bytearray`` so
its ``retire()`` can best-effort zero it in place -- CPython's ``bytes`` type
is immutable, so anything returned as ``bytes`` (Fernet's ``decrypt()``,
``solders``'s ``.secret()``) cannot be wiped in place; only an explicitly
constructed ``bytearray`` can be (see 02-RESEARCH.md Pitfall 5). This is a
best-effort mitigation, not a guarantee -- Python cannot ensure every copy of
the underlying memory is unreachable or overwritten.

The decrypted secret is surfaced per-call only from ``load()``: it is never
cached in a module-level global, matching CONTEXT.md's locked decision.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field

from solders.keypair import Keypair
from solders.pubkey import Pubkey

from bastion.keystore import crypto
from bastion.keystore.cloudsync import check_keystore_dir
from bastion.keystore.errors import KeystoreConfigError, KeystoreRetireError


@dataclass
class SessionKeypair:
    """A disposable session wallet's keypair, held in memory only.

    ``_secret`` is a mutable ``bytearray`` (64 bytes: 32 secret + 32 pubkey,
    matching ``solders``'s ``bytes(Keypair)`` layout) so ``zeroize()`` can
    overwrite it in place on retire. ``repr``/``str`` never render the
    secret -- only ``pubkey`` and the literal string ``REDACTED``.
    """

    pubkey: str
    _secret: bytearray = field(repr=False)

    def __repr__(self) -> str:
        return f"SessionKeypair(pubkey={self.pubkey!r}, secret=REDACTED)"

    def __str__(self) -> str:
        return self.__repr__()

    def zeroize(self) -> None:
        """Best-effort overwrite of the in-memory secret bytearray.

        This does NOT guarantee the secret is unrecoverable from memory --
        Python's memory management, garbage collector, and any prior copies
        (e.g. an intermediate ``bytes`` object from ``Keypair``/``Fernet``)
        may still hold the plaintext elsewhere. This only zeroes the one
        mutable buffer this object owns (02-RESEARCH.md Pitfall 5).
        """
        self._secret[:] = b"\x00" * len(self._secret)


def generate() -> SessionKeypair:
    """Create a fresh, unique session keypair.

    Each call constructs a brand-new ``solders.keypair.Keypair()`` (random),
    so two calls never yield the same pubkey.
    """
    kp = Keypair()
    return SessionKeypair(pubkey=str(kp.pubkey()), _secret=bytearray(bytes(kp)))


def _safe_pubkey(pubkey: str) -> str:
    """Validate ``pubkey`` before it is ever used to build a filesystem path.

    Rejects anything that does not decode as a canonical base58 Ed25519
    pubkey (``solders.pubkey.Pubkey.from_string`` already rejects ``/``,
    ``\\``, and traversal segments since they are not valid base58
    characters), and additionally rejects path separators / ``.``/``..``
    explicitly as defense in depth so this guard does not silently depend on
    base58's alphabet alone. Raises ``KeystoreConfigError`` (never a raw
    ``ValueError``) so callers get the module's typed fail-loud contract.
    """
    try:
        Pubkey.from_string(pubkey)
    except (ValueError, TypeError) as exc:
        raise KeystoreConfigError("Invalid session pubkey.") from exc
    if "/" in pubkey or "\\" in pubkey or os.sep in pubkey or pubkey in (".", ".."):
        raise KeystoreConfigError("Invalid session pubkey.")
    return pubkey


def _atomic_write_json(path: str, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically via temp file + os.replace.

    ``os.chmod(tmp, 0o600)`` is genuinely restrictive on POSIX; on Windows it
    only toggles the DOS read-only attribute and does NOT restrict other
    accounts (02-RESEARCH.md Pitfall 1) -- documented, not silently assumed.
    ``os.replace`` is atomic same-volume on both POSIX and Windows, so a
    crash mid-write never leaves a truncated file at the final path.

    ``os.fsync(fd)`` is called before ``os.close`` so the written bytes are
    durable on disk before the atomic rename (IN-02) -- otherwise a power
    loss immediately after ``os.replace`` could leave an empty/zero-length
    keystore behind despite the rename itself being atomic. A missing
    ``keystore_dir`` raises a typed ``KeystoreConfigError`` instead of a raw
    ``FileNotFoundError`` (IN-04). On any failure in the write/chmod/replace
    sequence, the orphaned temp file is unlinked before the exception
    propagates (IN-01) rather than left behind in ``keystore_dir``.
    """
    directory = os.path.dirname(path) or "."
    try:
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    except FileNotFoundError as exc:
        raise KeystoreConfigError(
            f"Keystore directory does not exist: {directory!r}"
        ) from exc

    try:
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.chmod(tmp_path, 0o600)  # best-effort on Windows, real on POSIX (Pitfall 1)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


def save(
    session: SessionKeypair,
    keystore_dir: str,
    passphrase: str,
    allow_cloud_sync: bool = False,
) -> str:
    """Encrypt and atomically write ``session`` to ``<keystore_dir>/<pubkey>.json``.

    Calls ``check_keystore_dir`` first (empty-dir / cloud-sync guards, SEC-04)
    before touching the filesystem. The written blob is ciphertext-only --
    the passphrase and plaintext secret are never logged or included in any
    exception message. Returns the final file path.
    """
    check_keystore_dir(keystore_dir, allow_cloud_sync)

    blob = crypto.encrypt_secret(passphrase, bytes(session._secret))
    data = json.dumps(blob).encode("utf-8")

    path = os.path.join(keystore_dir, f"{session.pubkey}.json")
    _atomic_write_json(path, data)
    return path


def load(pubkey: str, keystore_dir: str, passphrase: str) -> SessionKeypair:
    """Decrypt and return the ``SessionKeypair`` stored at ``<keystore_dir>/<pubkey>.json``.

    Fails closed (SESS-05): a wrong passphrase or a tampered/corrupted file
    propagates ``KeystoreWrongPassphraseError`` from ``crypto.decrypt_secret``
    unchanged -- never caught-and-swallowed into a partial/garbage return.
    ``Keypair.from_bytes`` provides a second fail-closed validation layer
    (raises on a corrupted 64-byte blob rather than silently accepting it).
    The decrypted secret is returned fresh on every call -- never cached in
    a module-level global. ``pubkey`` is validated (WR-01) before it is used
    to build a filesystem path, so a path-traversal or absolute-path value
    is rejected with ``KeystoreConfigError`` before any file is opened.
    """
    pubkey = _safe_pubkey(pubkey)
    path = os.path.join(keystore_dir, f"{pubkey}.json")
    with open(path, encoding="utf-8") as f:
        blob = json.load(f)

    plaintext = crypto.decrypt_secret(passphrase, blob)
    restored = Keypair.from_bytes(plaintext)  # second fail-closed validation

    return SessionKeypair(pubkey=str(restored.pubkey()), _secret=bytearray(bytes(restored)))


def retire(
    session_or_pubkey: SessionKeypair | str,
    keystore_dir: str,
    token_accounts: list[dict] | None = None,
    *,
    token_check_skipped: bool = False,
) -> None:
    """Remove the keystore file and best-effort zeroize the in-memory secret.

    Accepts either a ``SessionKeypair`` (its ``zeroize()`` is called) or a
    bare pubkey string (no in-memory secret to zeroize). Tolerates an
    already-absent file (best-effort delete, not a hard requirement that the
    file still exists). ``pubkey`` is validated (WR-01) before it is used to
    build a filesystem path, so a path-traversal or absolute-path value is
    rejected with ``KeystoreConfigError`` before any file is deleted.

    ``token_accounts`` (D-10/SESS-07): a list of jsonParsed
    ``getTokenAccountsByOwner``-shaped entries for the session's ATAs,
    freshly read and passed in by the caller (this module stays synchronous
    and never imports ``RpcClient``/``httpx``). When any account's
    ``account.data.parsed.info.tokenAmount.amount`` parses to an int > 0,
    retire refuses to hard-delete: it raises ``KeystoreRetireError`` and
    returns WITHOUT deleting the keystore file or zeroizing the in-memory
    secret (fail-loud, never a silent skip). An empty list (or a list whose
    entries are all zero) is a verified-empty result and the guard is a
    no-op.

    ``token_check_skipped`` (WR-02, fail-closed by design): ``token_accounts
    = None`` is ambiguous on its own -- it could mean "this caller never
    intended to check" (a legitimate, pre-Phase-3 usage) OR "a token lookup
    was attempted and failed (RPC error/timeout/rate limit)". Silently
    treating both the same as "verified empty" would let a failed lookup
    be mistaken for a confirmed-zero balance and orphan token funds behind
    a deleted keystore. To keep ``token_accounts=None`` safe, the caller
    must explicitly opt out of the check by passing
    ``token_check_skipped=True`` -- this is the only way ``None`` is
    accepted. Passing ``None`` without ``token_check_skipped=True`` raises
    ``KeystoreRetireError`` (fail-closed on a genuinely unknown balance)
    rather than silently proceeding.
    """
    if isinstance(session_or_pubkey, SessionKeypair):
        pubkey = session_or_pubkey.pubkey
    else:
        pubkey = session_or_pubkey

    pubkey = _safe_pubkey(pubkey)

    if token_accounts is None and not token_check_skipped:
        raise KeystoreRetireError(
            "Cannot retire session keystore: token balance was not checked "
            "(token_accounts is None). Pass token_check_skipped=True only "
            "for callers that intentionally never look up token accounts; "
            "never pass None as a stand-in for a failed RPC lookup."
        )

    if token_accounts:
        nonzero = [
            acc
            for acc in token_accounts
            if int(acc["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"]) > 0
        ]
        if nonzero:
            raise KeystoreRetireError(
                "Cannot retire session keystore: nonzero token balance "
                f"remains in {len(nonzero)} account(s). Sweep tokens "
                "manually before retiring."
            )

    path = os.path.join(keystore_dir, f"{pubkey}.json")
    try:
        os.remove(path)
    except FileNotFoundError:
        pass

    if isinstance(session_or_pubkey, SessionKeypair):
        session_or_pubkey.zeroize()
