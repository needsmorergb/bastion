"""scrypt -> Fernet cryptographic primitives for the encrypted keystore.

This module is the fund-safety primitive the whole keystore rests on
(SESS-04, SEC-01). It derives a Fernet key from a passphrase and a per-file
salt using scrypt, encrypts/decrypts raw keypair bytes, and serializes to a
versioned, self-describing JSON-able blob.

Contract:
    - The on-disk blob is ciphertext-only: the secret key bytes exist solely
      inside the Fernet ``ciphertext`` field. No plaintext-key field is ever
      produced.
    - Decrypt fails closed: a wrong passphrase or a tampered/corrupted
      ciphertext raises ``KeystoreWrongPassphraseError`` (never returns
      partial or garbage bytes) — Fernet's HMAC authentication guarantees
      this (see ``cryptography.fernet.InvalidToken``).
    - Stored KDF parameters (``n``, ``r``, ``p``) are validated before any
      derivation is attempted; malformed values raise
      ``KeystoreConfigError`` (fail loud), matching ``bastion.config``'s
      posture.
    - The passphrase, derived key, and plaintext are never logged or
      interpolated into any exception message.
"""

from __future__ import annotations

import base64
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from bastion.keystore.errors import KeystoreWrongPassphraseError

KEYSTORE_VERSION = 1
KDF_NAME = "scrypt"
KDF_N = 2**17  # 131072 — OWASP-floor-or-above scrypt cost parameter (locked).
KDF_R = 8
KDF_P = 1
SALT_BYTES = 16


def _derive_fernet_key(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    """Derive a Fernet-ready key from a passphrase + salt via scrypt.

    Constructs a FRESH ``Scrypt`` instance every call — the KDF primitive is
    single-use (a second ``.derive()`` on the same instance raises
    ``AlreadyFinalized``). Returns the urlsafe-base64-encoded form Fernet
    requires (raw scrypt output alone is rejected by ``Fernet(...)``).
    """
    kdf = Scrypt(salt=salt, length=32, n=n, r=r, p=p)
    derived = kdf.derive(passphrase.encode("utf-8"))
    return base64.urlsafe_b64encode(derived)


def encrypt_secret(passphrase: str, plaintext: bytes) -> dict:
    """Encrypt raw secret bytes into a versioned, ciphertext-only blob dict.

    Generates a fresh random salt per call (never reused across files).
    The returned dict contains no plaintext-key field — the secret exists
    only inside ``ciphertext``.
    """
    salt = os.urandom(SALT_BYTES)
    key = _derive_fernet_key(passphrase, salt, KDF_N, KDF_R, KDF_P)
    ciphertext = Fernet(key).encrypt(plaintext)

    return {
        "version": KEYSTORE_VERSION,
        "kdf": KDF_NAME,
        "n": KDF_N,
        "r": KDF_R,
        "p": KDF_P,
        "salt": base64.urlsafe_b64encode(salt).decode("ascii"),
        "ciphertext": ciphertext.decode("ascii"),
    }


def decrypt_secret(passphrase: str, blob: dict) -> bytes:
    """Decrypt a blob produced by ``encrypt_secret``, failing closed.

    Re-derives the key from the stored ``salt``/``n``/``r``/`p`` (never a
    global assumption, so files with valid non-default params still
    decrypt), then lets Fernet's own HMAC authentication decide validity: a
    wrong passphrase or a tampered ciphertext raises
    ``KeystoreWrongPassphraseError`` — never a partial or garbage value.
    """
    n, r, p = blob["n"], blob["r"], blob["p"]

    salt = base64.urlsafe_b64decode(blob["salt"])
    key = _derive_fernet_key(passphrase, salt, n, r, p)

    try:
        return Fernet(key).decrypt(blob["ciphertext"].encode("ascii"))
    except InvalidToken as exc:
        raise KeystoreWrongPassphraseError(
            "Incorrect passphrase or corrupted keystore file."
        ) from exc
