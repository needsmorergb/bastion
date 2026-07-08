"""Devnet e2e fixtures — opt-in, network-touching, faucet-rate-limit-aware
(03-CONTEXT.md, 03-RESEARCH.md "Environment Availability"/"Validation
Architecture", 03-04-PLAN.md Task 1).

Every fixture here is designed to SKIP (never fail) when the live devnet
environment is unavailable: a missing/non-devnet `SOLANA_RPC`, a missing
vault, or an exhausted airdrop faucet all produce `pytest.skip`, not a
hard failure — the deterministic gate for this project's fund/sweep/
land-check logic is the mocked unit suite (`tests/unit/test_funder.py`,
`tests/unit/test_sweeper.py`, `tests/unit/test_land_check.py`); this
package is the real-chain corroboration layer.

The devnet airdrop faucet is aggressively rate-limited (2 req/8h
anonymous, ~5 SOL/day — 03-RESEARCH.md "Environment Availability"), so
`funded_session` airdrops AT MOST ONCE per test-process run via a
module-level cache and is reused by every devnet test that needs a
funded keypair, rather than airdropping per-test.
"""

from __future__ import annotations

import asyncio
import os
from typing import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from solders.keypair import Keypair

from bastion.config import Config, load_config
from bastion.keystore.session import SessionKeypair, generate
from bastion.rpc.client import RpcClient
from bastion.rpc.errors import RpcError, RpcTimeoutError

# Module-level cache for the single reusable funded devnet keypair — shared
# across every test in the process run (see module docstring). Never
# imported/mutated outside this file.
_FUND_CACHE: dict[str, object] = {}

# Comfortably covers every devnet test's needs (fund/sweep round trips,
# throwaway-mint + ATA rent, tx fees) without wasting faucet quota.
DEVNET_AIRDROP_LAMPORTS = 1_000_000_000  # 1 SOL


def _looks_like_devnet(url: str) -> bool:
    return "devnet" in url.lower()


async def _wait_for_signature(
    rpc: RpcClient, signature: str, *, poll_interval_s: float = 1.5, budget_s: float = 60.0
) -> None:
    """Poll `getSignatureStatuses` until `signature` reaches a terminal
    status. Unlike `bastion.land_check.land_check`, this never re-POSTs a
    blob (there is nothing to re-POST for a bare airdrop/setup signature) —
    it is purely a confirmation wait, used by fixtures/setup helpers that
    are not themselves under test.
    """
    elapsed = 0.0
    while elapsed < budget_s:
        statuses = await rpc.get_signature_statuses([signature], search_history=True)
        status = statuses["value"][0]
        if status is not None:
            if status.get("err") is not None:
                raise RpcError(f"transaction {signature} failed on-chain: {status['err']}")
            if status.get("confirmationStatus") in ("confirmed", "finalized"):
                return
        await asyncio.sleep(poll_interval_s)
        elapsed += poll_interval_s
    raise RpcTimeoutError(f"signature {signature} did not confirm within {budget_s}s")


@pytest_asyncio.fixture
async def devnet_rpc() -> AsyncIterator[tuple[RpcClient, Config]]:
    """Yield (RpcClient, Config) bound to a LIVE devnet RPC endpoint.

    Skips (never fails) unless `SOLANA_RPC` looks like a devnet endpoint
    (contains "devnet") or `BASTION_E2E_DEVNET=1` is explicitly set —
    guards against ever moving real value on mainnet during a test run
    (T-03-17). Also skips if `VAULT_SECRET`/`VAULT_PUBKEY` are unset, since
    every devnet test in this package needs a real devnet vault.
    """
    config = load_config()
    if not _looks_like_devnet(config.solana_rpc) and os.getenv("BASTION_E2E_DEVNET") != "1":
        pytest.skip(
            "SOLANA_RPC does not look like a devnet endpoint "
            f"({config.solana_rpc!r}); point SOLANA_RPC at a devnet URL or "
            "set BASTION_E2E_DEVNET=1 to explicitly opt in."
        )
    if not config.vault_secret.strip() or not config.vault_pubkey.strip():
        pytest.skip(
            "VAULT_SECRET/VAULT_PUBKEY are not set; devnet e2e tests need a "
            "real, funded devnet vault."
        )

    async with httpx.AsyncClient(base_url=config.solana_rpc, timeout=30.0) as client:
        yield RpcClient(client), config


@pytest_asyncio.fixture
async def funded_session(
    devnet_rpc: tuple[RpcClient, Config],
) -> SessionKeypair:
    """A session keypair funded with a small SOL balance, reused across the
    whole devnet test run (module-level cache) to respect the faucet's
    aggressive rate limit.

    Resolution order:
      1. `BASTION_E2E_KEYPAIR` env var (a base58 secret key) — assumed
         pre-funded by the operator; no airdrop is attempted.
      2. A freshly generated session keypair, airdropped once via
         `requestAirdrop`. A rate-limit/faucet-unavailable response skips
         the test rather than failing it (skip-not-fail, 03-CONTEXT.md).
    """
    rpc, _config = devnet_rpc

    if "session" not in _FUND_CACHE:
        env_secret = os.getenv("BASTION_E2E_KEYPAIR", "").strip()
        if env_secret:
            kp = Keypair.from_base58_string(env_secret)
            _FUND_CACHE["session"] = SessionKeypair(
                pubkey=str(kp.pubkey()), _secret=bytearray(bytes(kp))
            )
            _FUND_CACHE["airdropped"] = True  # assumed pre-funded by the operator
        else:
            _FUND_CACHE["session"] = generate()
            _FUND_CACHE["airdropped"] = False

    session: SessionKeypair = _FUND_CACHE["session"]  # type: ignore[assignment]

    if not _FUND_CACHE.get("airdropped"):
        try:
            sig = await rpc.call("requestAirdrop", [session.pubkey, DEVNET_AIRDROP_LAMPORTS])
            await _wait_for_signature(rpc, sig, budget_s=60.0)
        except (RpcError, RpcTimeoutError) as exc:
            pytest.skip(f"devnet airdrop unavailable or did not land (skip-not-fail): {exc}")
        _FUND_CACHE["airdropped"] = True

    return session
