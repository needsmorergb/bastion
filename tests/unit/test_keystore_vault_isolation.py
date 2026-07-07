"""Tests for bastion.keystore.vault — isolated vault-secret loader (SEC-01).

Covers:
    - load_vault() reconstructs a Keypair from a valid VAULT_SECRET.
    - load_vault() fails loud (KeystoreConfigError) on a blank secret.
    - The vault secret never leaks into a repr or an exception message.

The AST-based import-isolation test (SEC-02/SEC-03 structural precondition)
is added to this file in a follow-up task.
"""

import pytest
from solders.keypair import Keypair

from bastion.config import Config
from bastion.keystore.errors import KeystoreConfigError
from bastion.keystore.vault import load_vault


def _base_config(**overrides: object) -> Config:
    """Minimal Config fixture — non-secret fields don't matter for these tests."""
    defaults = dict(
        solana_rpc="https://api.mainnet-beta.solana.com",
        solana_ws="wss://api.mainnet-beta.solana.com",
        vault_pubkey="",
        keystore_dir="",
        telegram_chat_id="",
        pushover_user="",
        max_session_cap_sol=1.0,
        fee_reserve_lamports=5000,
        score_watch_threshold=0.5,
        score_critical_threshold=0.8,
        vault_secret="",
    )
    defaults.update(overrides)
    return Config(**defaults)


def test_load_vault_returns_keypair_for_valid_secret():
    fresh = Keypair()
    secret_str = str(fresh)  # base58-encoded secret string, per solders API
    config = _base_config(vault_secret=secret_str)

    result = load_vault(config)

    assert result.pubkey() == fresh.pubkey()


def test_load_vault_raises_on_blank_secret():
    config = _base_config(vault_secret="")

    with pytest.raises(KeystoreConfigError):
        load_vault(config)


def test_load_vault_raises_on_whitespace_only_secret():
    config = _base_config(vault_secret="   ")

    with pytest.raises(KeystoreConfigError):
        load_vault(config)


def test_load_vault_raises_on_malformed_secret():
    config = _base_config(vault_secret="not-a-real-secret-key")

    with pytest.raises(KeystoreConfigError):
        load_vault(config)


def test_secret_never_leaks_into_repr_or_error_message():
    fresh = Keypair()
    secret_str = str(fresh)
    config = _base_config(vault_secret=secret_str)

    result = load_vault(config)

    assert secret_str not in repr(result)
    assert secret_str not in repr(config)

    with pytest.raises(KeystoreConfigError) as exc_info:
        load_vault(_base_config(vault_secret=""))
    assert secret_str not in str(exc_info.value)
