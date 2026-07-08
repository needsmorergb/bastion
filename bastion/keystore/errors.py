"""Typed error hierarchy shared by every ``bastion.keystore`` module.

Every raised instance carries only a plain message string — never include
secret material (private keys, passphrases, vault secrets) in any error
string raised from this module or its subclasses.
"""


class KeystoreError(Exception):
    """Base class for all keystore failures."""


class KeystoreWrongPassphraseError(KeystoreError):
    """Raised when decrypting a keystore file fails (wrong passphrase or a
    corrupted/tampered file). Fail-closed contract (SESS-05): callers must
    never receive partial or garbage key material on this path."""


class KeystoreCloudSyncError(KeystoreError):
    """Raised when the keystore directory resolves under a detected
    cloud-sync path (Dropbox, OneDrive, iCloud, Google Drive) and no
    explicit override has been granted (SEC-04)."""


class KeystoreConfigError(KeystoreError):
    """Raised on an empty ``KEYSTORE_DIR`` or a malformed stored KDF
    parameter. Fails loud rather than silently defaulting, mirroring
    ``bastion.config``'s ``ConfigError`` posture."""


class KeystoreRetireError(KeystoreError):
    """Raised when ``retire()`` is asked to hard-delete a keystore while a
    nonzero token balance remains in the session's ATAs (D-10, SESS-07)."""
