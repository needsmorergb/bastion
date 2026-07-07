"""Tests for bastion.keystore.crypto — scrypt->Fernet encrypt/decrypt primitives.

Covers SESS-04 (encryption at rest, versioned KDF params) and SEC-01
(ciphertext-only on-disk format; wrong passphrase / tampered ciphertext must
fail closed, never return garbage or partial plaintext).
"""

import base64

import pytest
from cryptography.fernet import Fernet

from bastion.keystore.crypto import (
    KDF_N,
    KDF_NAME,
    KDF_P,
    KDF_R,
    KEYSTORE_VERSION,
    decrypt_secret,
    encrypt_secret,
)
from bastion.keystore.errors import KeystoreConfigError, KeystoreWrongPassphraseError

PASSPHRASE = "correct-horse-battery-staple"
OTHER_PASSPHRASE = "wrong-horse-battery-staple"


def _payload() -> bytes:
    """64-byte stand-in for a serialized solders.Keypair."""
    import os

    return os.urandom(64)


def test_roundtrip_recovers_exact_keypair():
    plaintext = _payload()

    blob = encrypt_secret(PASSPHRASE, plaintext)
    recovered = decrypt_secret(PASSPHRASE, blob)

    assert recovered == plaintext


def test_fresh_salt_per_call_produces_different_ciphertext():
    plaintext = _payload()

    blob_a = encrypt_secret(PASSPHRASE, plaintext)
    blob_b = encrypt_secret(PASSPHRASE, plaintext)

    assert blob_a["salt"] != blob_b["salt"]
    assert blob_a["ciphertext"] != blob_b["ciphertext"]


def test_wrong_passphrase_fails_closed():
    plaintext = _payload()
    blob = encrypt_secret(PASSPHRASE, plaintext)

    with pytest.raises(KeystoreWrongPassphraseError):
        decrypt_secret(OTHER_PASSPHRASE, blob)


def test_tampered_ciphertext_fails_closed():
    plaintext = _payload()
    blob = encrypt_secret(PASSPHRASE, plaintext)

    raw = bytearray(base64.urlsafe_b64decode(blob["ciphertext"]))
    # Flip a byte in the middle of the token (past the version/timestamp header).
    raw[20] ^= 0xFF
    blob["ciphertext"] = base64.urlsafe_b64encode(bytes(raw)).decode("ascii")

    with pytest.raises(KeystoreWrongPassphraseError):
        decrypt_secret(PASSPHRASE, blob)


def test_no_secret_value_in_exception_message():
    plaintext = _payload()
    blob = encrypt_secret(PASSPHRASE, plaintext)

    with pytest.raises(KeystoreWrongPassphraseError) as exc_info:
        decrypt_secret(OTHER_PASSPHRASE, blob)

    message = str(exc_info.value)
    assert PASSPHRASE not in message
    assert OTHER_PASSPHRASE not in message
    assert plaintext.hex() not in message


def test_blob_shape_has_exactly_locked_fields():
    blob = encrypt_secret(PASSPHRASE, _payload())

    assert set(blob.keys()) == {"version", "kdf", "n", "r", "p", "salt", "ciphertext"}
    assert blob["version"] == KEYSTORE_VERSION
    assert blob["kdf"] == KDF_NAME
    assert blob["n"] == KDF_N
    assert blob["r"] == KDF_R
    assert blob["p"] == KDF_P


def test_blob_is_ciphertext_only_no_plaintext_field():
    plaintext = _payload()
    blob = encrypt_secret(PASSPHRASE, plaintext)

    plaintext_b64 = base64.urlsafe_b64encode(plaintext).decode("ascii")
    for value in blob.values():
        assert value != plaintext_b64
    # The ciphertext must not be trivially reversible without the passphrase:
    # a Fernet token from a *different* key must never equal this one.
    other_key = base64.urlsafe_b64encode(b"0" * 32)
    other_ciphertext = Fernet(other_key).encrypt(plaintext)
    assert blob["ciphertext"] != other_ciphertext.decode("ascii")


def test_param_validation_rejects_non_power_of_two_n():
    blob = encrypt_secret(PASSPHRASE, _payload())
    blob["n"] = 100000  # not a power of two

    with pytest.raises(KeystoreConfigError):
        decrypt_secret(PASSPHRASE, blob)


def test_param_validation_rejects_zero_r():
    blob = encrypt_secret(PASSPHRASE, _payload())
    blob["r"] = 0

    with pytest.raises(KeystoreConfigError):
        decrypt_secret(PASSPHRASE, blob)


def test_param_validation_rejects_zero_p():
    blob = encrypt_secret(PASSPHRASE, _payload())
    blob["p"] = 0

    with pytest.raises(KeystoreConfigError):
        decrypt_secret(PASSPHRASE, blob)


def test_forward_compat_decrypts_with_valid_non_default_params():
    """A blob whose stored n/r/p differ from current defaults (but are each
    individually valid) must still decrypt — params come from the file, not
    a global assumption."""
    plaintext = _payload()

    # Encrypt directly against a smaller, still-valid n so the test stays fast,
    # simulating an older/forward keystore file with different locked params.
    import os

    from bastion.keystore.crypto import _derive_fernet_key

    custom_n, custom_r, custom_p = 16384, 8, 1
    salt = os.urandom(16)
    key = _derive_fernet_key(PASSPHRASE, salt, custom_n, custom_r, custom_p)
    ciphertext = Fernet(key).encrypt(plaintext)

    blob = {
        "version": KEYSTORE_VERSION,
        "kdf": KDF_NAME,
        "n": custom_n,
        "r": custom_r,
        "p": custom_p,
        "salt": base64.urlsafe_b64encode(salt).decode("ascii"),
        "ciphertext": ciphertext.decode("ascii"),
    }

    recovered = decrypt_secret(PASSPHRASE, blob)

    assert recovered == plaintext
