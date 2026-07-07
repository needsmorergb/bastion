"""Tests for bastion.config — typed, frozen Config loaded from 12-factor env.

Covers CLI-05 (documented env vars + process-env precedence + passphrase
getpass fallback) and CLI-06/D-08 (independently-overridable safety rails),
plus the security invariants from 01-RESEARCH.md's Security Domain (V5 input
validation, secret-repr-safety, non-secure-endpoint warning).
"""

import getpass

import pytest

from bastion.config import Config, ConfigError, get_passphrase, load_config

# Every documented env var (CLI-05 identity/secret vars + CLI-06 safety rails).
ALL_ENV_VARS = [
    "SOLANA_RPC",
    "SOLANA_WS",
    "VAULT_SECRET",
    "VAULT_PUBKEY",
    "KEYSTORE_DIR",
    "KEYSTORE_PASSPHRASE",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "PUSHOVER_TOKEN",
    "PUSHOVER_USER",
    "MAX_SESSION_CAP",
    "FEE_RESERVE_LAMPORTS",
    "SCORE_WATCH_THRESHOLD",
    "SCORE_CRITICAL_THRESHOLD",
]


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch, tmp_path):
    """Isolate every test: no documented env var leaks in, no stray `.env`
    file leaks in (cwd is a fresh empty tmp_path with no `.env` present).
    """
    for var in ALL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    yield


def test_loads_all_documented_env_vars(monkeypatch):
    """CLI-05: with every documented var set, load_config() exposes each one."""
    monkeypatch.setenv("SOLANA_RPC", "https://rpc.example.com")
    monkeypatch.setenv("SOLANA_WS", "wss://ws.example.com")
    monkeypatch.setenv("VAULT_SECRET", "vault-secret-value")
    monkeypatch.setenv("VAULT_PUBKEY", "VaultPubkey11111111111111111111111111111")
    monkeypatch.setenv("KEYSTORE_DIR", "/tmp/keystore")
    monkeypatch.setenv("KEYSTORE_PASSPHRASE", "keystore-passphrase-value")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-bot-token-value")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456789")
    monkeypatch.setenv("PUSHOVER_TOKEN", "pushover-token-value")
    monkeypatch.setenv("PUSHOVER_USER", "pushover-user-value")

    config = load_config()

    assert isinstance(config, Config)
    assert config.solana_rpc == "https://rpc.example.com"
    assert config.solana_ws == "wss://ws.example.com"
    assert config.vault_secret == "vault-secret-value"
    assert config.vault_pubkey == "VaultPubkey11111111111111111111111111111"
    assert config.keystore_dir == "/tmp/keystore"
    assert config.keystore_passphrase == "keystore-passphrase-value"
    assert config.telegram_bot_token == "telegram-bot-token-value"
    assert config.telegram_chat_id == "123456789"
    assert config.pushover_token == "pushover-token-value"
    assert config.pushover_user == "pushover-user-value"


def test_process_env_precedence(monkeypatch, tmp_path):
    """D-01: real process env overrides a `.env` file value (do NOT hand-merge
    or flip python-dotenv's override default — Pitfall 3).
    """
    (tmp_path / ".env").write_text("SOLANA_RPC=dotenv-value\n")
    monkeypatch.setenv("SOLANA_RPC", "process-value")

    config = load_config()

    assert config.solana_rpc == "process-value"


def test_passphrase_getpass_fallback(monkeypatch):
    """D-02: KEYSTORE_PASSPHRASE unset -> falls back to getpass, never
    required in the environment.
    """
    monkeypatch.delenv("KEYSTORE_PASSPHRASE", raising=False)
    monkeypatch.setattr(getpass, "getpass", lambda *a, **k: "sentinel-from-getpass")

    result = get_passphrase()

    assert result == "sentinel-from-getpass"


def test_passphrase_from_env_when_set(monkeypatch):
    """D-02: KEYSTORE_PASSPHRASE set -> returned directly, getpass never called."""
    monkeypatch.setenv("KEYSTORE_PASSPHRASE", "env-passphrase-value")

    def _fail_if_called(*a, **k):
        raise AssertionError("getpass.getpass must not be called when env var is set")

    monkeypatch.setattr(getpass, "getpass", _fail_if_called)

    result = get_passphrase()

    assert result == "env-passphrase-value"


def test_safety_rail_overrides(monkeypatch):
    """CLI-06/D-08: each safety rail is independently env-overridable."""
    monkeypatch.setenv("MAX_SESSION_CAP", "0.25")
    monkeypatch.setenv("FEE_RESERVE_LAMPORTS", "7500")
    monkeypatch.setenv("SCORE_WATCH_THRESHOLD", "0.4")
    monkeypatch.setenv("SCORE_CRITICAL_THRESHOLD", "0.9")

    config = load_config()

    assert config.max_session_cap_sol == 0.25
    assert config.fee_reserve_lamports == 7500
    assert config.score_watch_threshold == 0.4
    assert config.score_critical_threshold == 0.9


def test_safety_rail_defaults():
    """CLI-06: with all rail vars unset, each rail has a conservative,
    non-zero default (MAX_SESSION_CAP ~1.0 SOL per D-08).
    """
    config = load_config()

    assert config.max_session_cap_sol == pytest.approx(1.0)
    assert config.fee_reserve_lamports > 0
    assert config.score_watch_threshold > 0
    assert config.score_critical_threshold > 0
    # WATCH must be strictly less severe (lower) than CRITICAL.
    assert config.score_watch_threshold < config.score_critical_threshold


def test_config_repr_excludes_secrets(monkeypatch):
    """T-01-03: repr(config)/str(config) never leak secret field values."""
    monkeypatch.setenv("VAULT_SECRET", "UNMISTAKABLE-VAULT-SECRET-VALUE")
    monkeypatch.setenv("KEYSTORE_PASSPHRASE", "UNMISTAKABLE-PASSPHRASE-VALUE")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "UNMISTAKABLE-TELEGRAM-TOKEN-VALUE")
    monkeypatch.setenv("PUSHOVER_TOKEN", "UNMISTAKABLE-PUSHOVER-TOKEN-VALUE")

    config = load_config()

    rendered_repr = repr(config)
    rendered_str = str(config)

    for secret_value in (
        "UNMISTAKABLE-VAULT-SECRET-VALUE",
        "UNMISTAKABLE-PASSPHRASE-VALUE",
        "UNMISTAKABLE-TELEGRAM-TOKEN-VALUE",
        "UNMISTAKABLE-PUSHOVER-TOKEN-VALUE",
    ):
        assert secret_value not in rendered_repr
        assert secret_value not in rendered_str


def test_malformed_rail_fails_loudly(monkeypatch):
    """V5 input validation: a malformed rail value raises ConfigError,
    never silently falls back to the default.
    """
    monkeypatch.setenv("MAX_SESSION_CAP", "not-a-number")

    with pytest.raises(ConfigError):
        load_config()


def test_non_secure_endpoint_warns(monkeypatch):
    """T-01-05: a non-https SOLANA_RPC emits a warning rather than being
    silently accepted (loud-warning posture; not a hard failure).
    """
    monkeypatch.setenv("SOLANA_RPC", "http://insecure.example.com")

    with pytest.warns(UserWarning):
        load_config()
