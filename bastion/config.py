"""Typed, frozen application configuration loaded from 12-factor env vars.

Every safety rail (MAX_SESSION_CAP, FEE_RESERVE_LAMPORTS,
SCORE_WATCH_THRESHOLD, SCORE_CRITICAL_THRESHOLD) is externalized here so
later modules (keystore, funder, sweeper, monitor, scoring, alerter) read
rails from `Config` and never hardcode them (CLI-06 / D-08).

Precedence (D-01): `load_dotenv()` is called with its default
`override=False`, so real process env always wins over a `.env` file value.
Do NOT change this to `override=True` and do NOT hand-merge
`dotenv_values()` — see 01-RESEARCH.md Pitfall 3.

Secrets (VAULT_SECRET, KEYSTORE_PASSPHRASE, TELEGRAM_BOT_TOKEN,
PUSHOVER_TOKEN) are never required in the environment beyond what the user
chooses to set, are excluded from `Config`'s repr/str, and are never logged
or printed anywhere in this module (non-custodial constraint, T-01-03).
"""

from __future__ import annotations

import getpass
import os
import warnings
from dataclasses import dataclass, field

from dotenv import load_dotenv

# Conservative, non-zero defaults (D-08). MAX_SESSION_CAP intentionally
# small — the whole point of a session wallet is a small, pre-decided cap.
_DEFAULT_SOLANA_RPC = "https://api.mainnet-beta.solana.com"
_DEFAULT_SOLANA_WS = "wss://api.mainnet-beta.solana.com"
_DEFAULT_MAX_SESSION_CAP_SOL = "1.0"
_DEFAULT_FEE_RESERVE_LAMPORTS = "5000"
_DEFAULT_SCORE_WATCH_THRESHOLD = "0.5"
_DEFAULT_SCORE_CRITICAL_THRESHOLD = "0.8"


class ConfigError(Exception):
    """Raised when a config value is present but malformed.

    Deliberately distinct from a missing/defaulted value: a malformed
    safety-rail value must fail loudly at load time, never silently fall
    back to the default (V5 input validation, T-01-04).
    """


@dataclass(frozen=True)
class Config:
    """Immutable application configuration.

    Secret-bearing fields (`vault_secret`, `keystore_passphrase`,
    `telegram_bot_token`, `pushover_token`) are declared `repr=False` so they
    never appear in this object's default repr/str (T-01-03).
    """

    solana_rpc: str
    solana_ws: str
    vault_pubkey: str
    keystore_dir: str
    telegram_chat_id: str
    pushover_user: str

    max_session_cap_sol: float
    fee_reserve_lamports: int
    score_watch_threshold: float
    score_critical_threshold: float

    vault_secret: str = field(default="", repr=False)
    keystore_passphrase: str = field(default="", repr=False)
    telegram_bot_token: str = field(default="", repr=False)
    pushover_token: str = field(default="", repr=False)


def _coerce(name: str, raw: str, caster: type) -> float | int:
    """Coerce an env-var string to a numeric type, raising ConfigError (not
    silently defaulting) on a malformed value (V5).
    """
    try:
        return caster(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigError(
            f"Environment variable {name} has a malformed value: {raw!r} "
            f"(expected a value convertible to {caster.__name__})"
        ) from exc


def _warn_if_insecure_scheme(name: str, value: str, expected_scheme: str) -> None:
    """Warn (never fail) on a non-secure endpoint scheme (T-01-05).

    Localhost/devnet http(s) endpoints are legitimate during development, so
    this is a loud warning, not a hard error.
    """
    if value and not value.startswith(expected_scheme):
        warnings.warn(
            f"{name}={value!r} does not use the expected {expected_scheme!r} "
            "scheme. A malformed or tampered endpoint URL could point "
            "Bastion's transport at an attacker-controlled host.",
            UserWarning,
            stacklevel=3,
        )


def load_config() -> Config:
    """Load `Config` from process env with a `.env` fallback.

    `load_dotenv()` uses its default `override=False`, so a real process env
    var always wins over the same key in a `.env` file (D-01). This function
    never prompts interactively — `get_passphrase()` is a separate call site
    so non-interactive paths (the monitor daemon) never block on stdin.
    """
    load_dotenv()

    max_session_cap_sol = _coerce(
        "MAX_SESSION_CAP",
        os.getenv("MAX_SESSION_CAP", _DEFAULT_MAX_SESSION_CAP_SOL),
        float,
    )
    fee_reserve_lamports = _coerce(
        "FEE_RESERVE_LAMPORTS",
        os.getenv("FEE_RESERVE_LAMPORTS", _DEFAULT_FEE_RESERVE_LAMPORTS),
        int,
    )
    score_watch_threshold = _coerce(
        "SCORE_WATCH_THRESHOLD",
        os.getenv("SCORE_WATCH_THRESHOLD", _DEFAULT_SCORE_WATCH_THRESHOLD),
        float,
    )
    score_critical_threshold = _coerce(
        "SCORE_CRITICAL_THRESHOLD",
        os.getenv("SCORE_CRITICAL_THRESHOLD", _DEFAULT_SCORE_CRITICAL_THRESHOLD),
        float,
    )

    solana_rpc = os.getenv("SOLANA_RPC", _DEFAULT_SOLANA_RPC)
    solana_ws = os.getenv("SOLANA_WS", _DEFAULT_SOLANA_WS)
    _warn_if_insecure_scheme("SOLANA_RPC", solana_rpc, "https://")
    _warn_if_insecure_scheme("SOLANA_WS", solana_ws, "wss://")

    return Config(
        solana_rpc=solana_rpc,
        solana_ws=solana_ws,
        vault_secret=os.getenv("VAULT_SECRET", ""),
        vault_pubkey=os.getenv("VAULT_PUBKEY", ""),
        keystore_dir=os.getenv("KEYSTORE_DIR", ""),
        keystore_passphrase=os.getenv("KEYSTORE_PASSPHRASE", ""),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        pushover_token=os.getenv("PUSHOVER_TOKEN", ""),
        pushover_user=os.getenv("PUSHOVER_USER", ""),
        max_session_cap_sol=max_session_cap_sol,
        fee_reserve_lamports=fee_reserve_lamports,
        score_watch_threshold=score_watch_threshold,
        score_critical_threshold=score_critical_threshold,
    )


def get_passphrase() -> str:
    """Return KEYSTORE_PASSPHRASE from env if set, else prompt via getpass.

    Per D-02: the passphrase is never required in the environment, never
    echoed, and never logged. `load_config()` itself never calls this —
    only interactive call sites (keystore create/unlock in Phase 2) do, so
    the monitor daemon's non-interactive startup never blocks on stdin.
    """
    env_val = os.getenv("KEYSTORE_PASSPHRASE")
    if env_val:
        return env_val
    return getpass.getpass("Keystore passphrase: ")
