"""Isolated loader for the vault secret — the single highest-privilege key.

Isolation contract (SEC-02/SEC-03 precondition): this module is the ONLY
place `VAULT_SECRET` is ever parsed into a ``solders.keypair.Keypair``. No
module under ``bastion/`` other than this one may import
``bastion.keystore.vault`` today; Phase 3's ``bastion/funder.py`` is the only
future module permitted to import it (sweeps, by contrast, target
``Config.vault_pubkey`` directly and never need the vault secret at all).
This is enforced by a static AST import-graph test, not just a comment —
see ``tests/unit/test_keystore_vault_isolation.py``.

Do NOT import the encrypted file-keystore modules (``crypto``, ``session``,
``cloudsync``, ``passphrase``) here: the vault secret lives only in the
``VAULT_SECRET`` environment variable, never in the encrypted on-disk
session keystore.

The vault secret must never appear in a repr, log line, or exception
message raised from this module.
"""

from __future__ import annotations

import json

from solders.keypair import Keypair

from bastion.config import Config
from bastion.keystore.errors import KeystoreConfigError


def load_vault(config: Config) -> Keypair:
    """Reconstruct the vault ``Keypair`` from ``config.vault_secret``.

    Accepts either a base58-encoded secret-key string (the standard Solana
    CLI/wallet export format) or a JSON byte-array string (e.g.
    ``"[1,2,3,...]"``, 64 ints) as a fallback encoding.

    Raises ``KeystoreConfigError`` (fail loud, never a silent ``None``) if
    ``config.vault_secret`` is blank/whitespace-only, or if it cannot be
    parsed into a valid keypair. The raised message names the missing
    ``VAULT_SECRET`` env var but never echoes the secret's value.
    """
    secret = config.vault_secret
    if secret is None or not secret.strip():
        raise KeystoreConfigError(
            "VAULT_SECRET is not set. Set the VAULT_SECRET environment "
            "variable to the vault wallet's secret key (base58 string or "
            "JSON byte-array) before running a command that needs the vault."
        )

    trimmed = secret.strip()
    try:
        if trimmed.startswith("["):
            raw = bytes(json.loads(trimmed))
            return Keypair.from_bytes(raw)
        return Keypair.from_base58_string(trimmed)
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise KeystoreConfigError(
            "VAULT_SECRET is malformed and could not be parsed as a "
            "base58-encoded secret key or a JSON byte-array."
        ) from exc
