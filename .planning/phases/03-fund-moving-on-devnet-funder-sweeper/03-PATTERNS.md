# Phase 3: Fund-Moving on Devnet (Funder + Sweeper) - Pattern Map

**Mapped:** 2026-07-08
**Files analyzed:** 8 (2 new modules, 2 modified modules, 4+ test files)
**Analogs found:** 8 / 8

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `bastion/funder.py` | service | CRUD (build-sign-send-confirm) | `bastion/keystore/vault.py` (secret-loading contract) + `bastion/rpc/client.py` (async/sync call pattern) | role-match (composite) |
| `bastion/sweeper.py` | service | CRUD (build-sign-send-confirm) | `bastion/funder.py` (sibling, once written) + `bastion/rpc/client.py` | role-match (composite) |
| `bastion/rpc/client.py` (extend) | service/transport | request-response | itself — extend `get_balance`/`get_fee_for_message` method style in place | exact (same file) |
| `bastion/keystore/session.py` (extend `retire()`) | model/lifecycle | CRUD | itself — extend `retire()` in place, reusing `_safe_pubkey` and `KeystoreError` family | exact (same file) |
| `bastion/rpc/errors.py` or new `bastion/fund_errors.py` | utility (error types) | n/a | `bastion/rpc/errors.py` / `bastion/keystore/errors.py` | exact |
| `tests/unit/test_funder.py` | test | request-response (mocked RPC) | `tests/unit/test_rpc_client.py` (respx `rpc_harness` pattern) | exact |
| `tests/unit/test_sweeper.py` | test | request-response (mocked RPC) | `tests/unit/test_rpc_client.py` | exact |
| `tests/unit/test_session_retire.py` (or extend `test_keystore_session.py`) | test | CRUD | `tests/unit/test_keystore_vault_isolation.py` (typed-error assertions) + existing keystore session tests | role-match |
| `tests/unit/test_keystore_vault_isolation.py` (MODIFY `ALLOWED_IMPORTERS`) | test | structural/AST | itself | exact (same file) |
| `tests/e2e/test_devnet_fund_sweep.py`, `tests/e2e/conftest.py` | test | e2e/live-network | `tests/conftest.py` (`rpc_harness` fixture pattern, adapted to a live devnet client instead of respx) | role-match |

## Pattern Assignments

### `bastion/funder.py` (service, CRUD/build-sign-send-confirm)

**Analog:** `bastion/keystore/vault.py` (module docstring / isolation contract) + `bastion/rpc/client.py` (method style, sync-wrapper pattern)

**Module docstring / isolation-contract pattern** (`bastion/keystore/vault.py` lines 1-19):
```python
"""Isolated loader for the vault secret — the single highest-privilege key.

Isolation contract (SEC-02/SEC-03 precondition): this module is the ONLY
place `VAULT_SECRET` is ever parsed into a ``solders.keypair.Keypair``. No
module under ``bastion/`` other than this one may import
``bastion.keystore.vault`` today; Phase 3's ``bastion/funder.py`` is the only
future module permitted to import it ...
This is enforced by a static AST import-graph test ...
```
Copy this contract-declaring docstring style into `funder.py`'s own module docstring, stating explicitly: "this is the ONLY module besides `vault.py` itself that imports `bastion.keystore.vault`" and cross-referencing `test_keystore_vault_isolation.py`.

**Import pattern** (`bastion/keystore/vault.py` lines 21-29):
```python
from __future__ import annotations

import json

from solders.keypair import Keypair

from bastion.config import Config
from bastion.keystore.errors import KeystoreConfigError
```
`funder.py` should mirror this shape:
```python
from __future__ import annotations

import base64

from solders.hash import Hash
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

from bastion.config import Config
from bastion.keystore.vault import load_vault   # the ONE sanctioned import
from bastion.rpc.client import RpcClient
from bastion.rpc.errors import RpcError
```

**Fail-loud typed-error pattern** (`bastion/keystore/vault.py` lines 43-49, mirrors `bastion/config.py` lines 74-84):
```python
if secret is None or not secret.strip():
    raise KeystoreConfigError(
        "VAULT_SECRET is not set. Set the VAULT_SECRET environment "
        "variable to the vault wallet's secret key (base58 string or "
        "JSON byte-array) before running a command that needs the vault."
    )
```
Copy this "raise typed error, plain message, no secret material, no silent default" contract for `FunderCapExceededError`/`FunderInsufficientBalanceError` (D-03/D-04, refuse-before-send — raise BEFORE any RPC call touching the network with a signed tx).

**Async core + sync wrapper pattern** (`bastion/rpc/client.py` lines 217-233 — `get_balance_sync`):
```python
async def _get_balance_async(base_url: str, pubkey: str) -> int:
    async with httpx.AsyncClient(base_url=base_url) as client:
        rpc = RpcClient(client)
        return await rpc.get_balance(pubkey)


def get_balance_sync(base_url: str, pubkey: str) -> int:
    """... Calls ``asyncio.run()`` at the true top level — only call this
    from a plain synchronous CLI entrypoint, never from within an already-
    running event loop (Pitfall 1) ..."""
    return asyncio.run(_get_balance_async(base_url, pubkey))
```
`funder.py` should expose `async def fund_session(rpc, config, session_pubkey, amount_sol) -> str` as the core, plus a thin `def fund_session_sync(...)` wrapper following this exact `asyncio.run()` + docstring-warning pattern (mirrors RESEARCH.md's "Sync vs async surface" discretion note).

**RpcClient method style to reuse as-is** (`bastion/rpc/client.py` lines 139-147, 190-214 — `get_balance`, `get_fee_for_message`, `send_raw`): funder calls these three existing methods unmodified; no changes needed to them.

---

### `bastion/sweeper.py` (service, CRUD/build-sign-send-confirm)

**Analog:** same `RpcClient` method style as funder, but the module docstring must explicitly state the NEGATIVE isolation contract (never imports vault.py).

**Docstring pattern to copy (inverted)** — model on `bastion/keystore/vault.py`'s isolation-contract docstring, but state:
```python
"""Session -> vault exact-zero sweep (SESS-06, SEC-02).

Isolation contract: this module MUST NOT import ``bastion.keystore.vault``
or hold the vault secret at any point. It reads only ``Config.vault_pubkey``
(a public string) as the sweep destination, and signs with the SESSION
``Keypair`` obtained from ``bastion.keystore.session.load()``. Enforced by
the same AST import-isolation test as funder.py
(``tests/unit/test_keystore_vault_isolation.py``), which will fail the build
if this contract is ever violated.
"""
```

**Imports** (mirror `funder.py`'s solders imports, but note the *absence* of `bastion.keystore.vault`):
```python
from __future__ import annotations

import base64

from solders.hash import Hash
from solders.instruction import AccountMeta, Instruction
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

from bastion.config import Config
from bastion.keystore.session import SessionKeypair
from bastion.rpc.client import RpcClient
from bastion.rpc.errors import RpcError
# deliberately NO: from bastion.keystore.vault import load_vault
```

**Dust no-op pattern (D-07), modeled on the same "guard before mutating state" shape as funder's D-03/D-04 guards**:
```python
if balance <= fee:  # D-07: sub-fee dust -> no-op, NOT an error
    return {"swept": False, "reason": "dust below fee reserve", "balance": balance}
```

**Signing with session key only, never vault** — the single most safety-critical line; comment it explicitly per SEC-02:
```python
tx = VersionedTransaction(message, [session.keypair])  # SESSION key only, never vault
```

---

### `bastion/rpc/client.py` (extend — new methods)

**Analog:** itself. Follow the existing method-per-RPC-call pattern exactly (lines 139-151, 186-204).

**Existing method shape to replicate** (`get_transaction`, lines 186-188):
```python
async def get_transaction(self, signature: str) -> object:
    """Return the raw ``result`` of ``getTransaction`` for ``signature``."""
    return await self.call("getTransaction", [signature])
```

**New methods to add, following this exact idiom** (docstring + `self.call(...)`, no new retry logic — reuse `_request_with_backoff` transparently via `self.call`):
```python
async def get_signature_statuses(
    self, signatures: list[str], *, search_history: bool = False
) -> object:
    """Return the raw ``result`` of ``getSignatureStatuses``."""
    return await self.call(
        "getSignatureStatuses",
        [signatures, {"searchTransactionHistory": search_history}],
    )

async def get_token_accounts_by_owner(self, owner_pubkey: str) -> list[dict]:
    """Return the ``value`` array of jsonParsed token accounts owned by
    ``owner_pubkey`` (SPL Token Program only)."""
    result = await self.call(
        "getTokenAccountsByOwner",
        [
            owner_pubkey,
            {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
            {"encoding": "jsonParsed"},
        ],
    )
    return result["value"]
```
Note the `get_fee_for_message` explicit-commitment pattern (lines 190-203) is the template for "don't silently inherit an RPC default" — no new commitment param is needed for these two new methods since neither has a commitment-sensitive default worth worrying about here (statuses are point-in-time; token accounts are current-state).

**Do NOT** add a second retry loop inside `funder.py`/`sweeper.py` — every new RPC method above already routes through `_request_with_backoff` via `self.call()` (lines 104-137), exactly like every existing method.

---

### `bastion/keystore/session.py` (extend `retire()` — D-10 guard)

**Analog:** itself, `retire()` at lines 185-209, plus the `_safe_pubkey` validation pattern (lines 76-93) and the `KeystoreError` family (`bastion/keystore/errors.py`).

**Current `retire()` to extend:**
```python
def retire(session_or_pubkey: SessionKeypair | str, keystore_dir: str) -> None:
    """Remove the keystore file and best-effort zeroize the in-memory secret.
    ...
    """
    if isinstance(session_or_pubkey, SessionKeypair):
        pubkey = session_or_pubkey.pubkey
    else:
        pubkey = session_or_pubkey

    pubkey = _safe_pubkey(pubkey)

    path = os.path.join(keystore_dir, f"{pubkey}.json")
    try:
        os.remove(path)
    except FileNotFoundError:
        pass

    if isinstance(session_or_pubkey, SessionKeypair):
        session_or_pubkey.zeroize()
```

**D-10 guard to insert BEFORE the `os.remove` call** — needs a new RPC dependency (`get_token_accounts_by_owner`) injected as a parameter (this is the one place `session.py` gains an RPC dependency it didn't have before, per RESEARCH.md's Architectural Responsibility Map). Recommended signature change: `retire(session_or_pubkey, keystore_dir, token_accounts: list[dict] | None = None)` — caller (sweeper or CLI in Phase 7) passes the freshly-read `get_token_accounts_by_owner` result; `retire()` itself stays synchronous and RPC-transport-agnostic (does not import `RpcClient`/`httpx` directly, matching this module's existing zero-network-dependency stance while still enforcing the guard):
```python
def retire(
    session_or_pubkey: SessionKeypair | str,
    keystore_dir: str,
    token_accounts: list[dict] | None = None,
) -> None:
    ...
    if token_accounts:
        nonzero = [
            acc for acc in token_accounts
            if int(acc["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"]) > 0
        ]
        if nonzero:
            raise KeystoreConfigError(
                "Cannot retire session keystore: nonzero token balance "
                f"remains in {len(nonzero)} account(s). Sweep tokens "
                "manually before retiring."
            )
    # ... existing os.remove + zeroize unchanged below this point
```
Follow the existing `raise KeystoreConfigError(...)` fail-loud contract (no silent skip, per D-10 and the file's own header docstring convention at lines 1-18) — do not introduce a new error subclass unless the plan explicitly wants a `KeystoreNonzeroTokenBalanceError`; either is consistent with the existing `KeystoreError` family in `bastion/keystore/errors.py`.

---

## Shared Patterns

### Typed, message-only fail-loud errors
**Source:** `bastion/rpc/errors.py` (lines 1-21) and `bastion/keystore/errors.py` (lines 1-29)
**Apply to:** `funder.py`, `sweeper.py`, and the D-10 retire guard
```python
class RpcError(Exception):
    """Base class for all Solana RPC/WS transport failures."""


class RpcRateLimitError(RpcError):
    """..."""
```
New Phase 3 errors (`FunderCapExceededError`, `FunderInsufficientBalanceError`) should follow this exact "one-line docstring naming the D-##/requirement, plain-message-only" convention. Per RESEARCH.md's Open Question #2, place them in a single shared module (e.g. `bastion/fund_errors.py`, mirroring `rpc/errors.py`'s and `keystore/errors.py`'s per-package placement) or inline at the top of `funder.py`/`sweeper.py` if the set stays small — either is consistent with the existing convention; pick one and be explicit in the plan.

### Async core + sync wrapper for CLI one-shots
**Source:** `bastion/rpc/client.py` lines 217-233 (`_get_balance_async` / `get_balance_sync`)
**Apply to:** `funder.fund_session` / `funder.fund_session_sync`, `sweeper.sweep_session` / `sweeper.sweep_session_sync`
```python
def get_balance_sync(base_url: str, pubkey: str) -> int:
    """... Calls ``asyncio.run()`` at the true top level — only call this
    from a plain synchronous CLI entrypoint, never from within an already-
    running event loop (Pitfall 1) ..."""
    return asyncio.run(_get_balance_async(base_url, pubkey))
```

### Refuse-before-send / guard-before-mutate
**Source:** `bastion/keystore/vault.py` lines 43-49 (blank-secret guard) and `bastion/config.py` lines 74-84 (`_coerce` raising `ConfigError`)
**Apply to:** funder's D-03 (cap) and D-04 (balance) guards, sweeper's D-07 (dust) guard, retire's D-10 guard — all raise/return BEFORE any state-mutating action (network send or file delete).

### respx-mocked async RPC test harness
**Source:** `tests/conftest.py` lines 36-56 (`rpc_harness` fixture) and `tests/unit/test_rpc_client.py` lines 33-64 (usage pattern with `side_effect=[...]`)
**Apply to:** `tests/unit/test_funder.py`, `tests/unit/test_sweeper.py`, `tests/unit/test_session_retire.py`
```python
@pytest.mark.asyncio
async def test_x(rpc_harness):
    client, router = rpc_harness
    route = router.post(RPC_TEST_BASE_URL).mock(
        side_effect=[
            httpx.Response(200, json={"jsonrpc": "2.0", "result": {...}, "id": 1}),
            ...
        ]
    )
    rpc = RpcClient(client)
    ...
```
For the injected-timeout no-double-spend test (D-08), queue a `side_effect` sequence where an early `sendTransaction`/`getSignatureStatuses` call simulates ambiguity (e.g. a `null` status or a timeout exception) followed by a landed status, and assert `route.call_count` / the specific signature reused equals exactly one distinct signed blob sent (never two different signed transactions).

### AST import-isolation test extension
**Source:** `tests/unit/test_keystore_vault_isolation.py` lines 24-25, 153-172
```python
# Phase 3 appends "bastion/funder.py" to this allowlist when the funder is built.
ALLOWED_IMPORTERS = {"bastion/keystore/vault.py"}
```
Change to:
```python
ALLOWED_IMPORTERS = {"bastion/keystore/vault.py", "bastion/funder.py"}
```
The rest of `test_only_allowlisted_modules_import_vault` (lines 153-172) needs no other changes — it already asserts `importing_files <= ALLOWED_IMPORTERS` as a subset check, so adding `funder.py` to the set is the only edit required. Add a companion assertion (or a new test) that `bastion/sweeper.py` specifically does NOT appear in `offenders`, to make the SEC-02 negative contract for the sweeper explicit rather than merely implied by the subset check.

## No Analog Found

None — every new/modified file this phase touches has a close structural analog already in the codebase (Phase 1's `RpcClient` transport/retry pattern and Phase 2's keystore/vault isolation-and-error conventions cover all of it).

## Metadata

**Analog search scope:** `bastion/` (all modules), `tests/unit/` (test_rpc_client.py, test_keystore_vault_isolation.py), `tests/conftest.py`
**Files scanned:** `bastion/rpc/client.py`, `bastion/rpc/errors.py`, `bastion/keystore/vault.py`, `bastion/keystore/session.py`, `bastion/keystore/errors.py`, `bastion/config.py`, `tests/unit/test_keystore_vault_isolation.py`, `tests/unit/test_rpc_client.py`, `tests/conftest.py`
**Pattern extraction date:** 2026-07-08
