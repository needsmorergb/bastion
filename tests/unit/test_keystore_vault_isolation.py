"""Tests for bastion.keystore.vault — isolated vault-secret loader (SEC-01)
and the static AST import-graph isolation invariant (SEC-02/SEC-03
structural precondition).

Covers:
    - load_vault() reconstructs a Keypair from a valid VAULT_SECRET.
    - load_vault() fails loud (KeystoreConfigError) on a blank secret.
    - The vault secret never leaks into a repr or an exception message.
    - No module under bastion/ other than vault.py itself imports
      bastion.keystore.vault (all four import syntaxes).
"""

import ast
from pathlib import Path

import pytest
from solders.keypair import Keypair

from bastion.config import Config
from bastion.keystore.errors import KeystoreConfigError
from bastion.keystore.vault import load_vault

FORBIDDEN_MODULE = "bastion.keystore.vault"
# Phase 3: funder.py is the one sanctioned addition (SEC-02, D-02).
ALLOWED_IMPORTERS = {"bastion/keystore/vault.py", "bastion/funder.py"}


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


def _find_vault_imports_in_source(source: str) -> list[int]:
    """Return line numbers where source imports bastion.keystore.vault.

    Uses ast.parse (never imports the module) so this is safe to run even
    if a forbidden import would otherwise raise or have side effects.
    Detects all four import syntaxes:
        import bastion.keystore.vault
        import bastion.keystore.vault as v
        from bastion.keystore.vault import load_vault
        from bastion.keystore import vault
    """
    tree = ast.parse(source)
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.startswith(FORBIDDEN_MODULE) for alias in node.names):
                lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == FORBIDDEN_MODULE or (
                module == "bastion.keystore"
                and any(alias.name == "vault" for alias in node.names)
            ):
                lines.append(node.lineno)
    return lines


def _find_vault_imports(py_file: Path) -> list[int]:
    """File-reading wrapper around ``_find_vault_imports_in_source``."""
    return _find_vault_imports_in_source(py_file.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "source",
    [
        "import bastion.keystore.vault",
        "import bastion.keystore.vault as v",
        "from bastion.keystore.vault import load_vault",
        "from bastion.keystore import vault",
    ],
    ids=[
        "import-module",
        "import-as-alias",
        "from-module-import-name",
        "from-package-import-submodule",
    ],
)
def test_detects_each_forbidden_import_syntax(source: str):
    assert _find_vault_imports_in_source(source) == [1]


def test_ignores_unrelated_imports():
    source = (
        "import bastion.config\n"
        "from bastion.keystore import crypto\n"
        "from bastion.keystore.errors import KeystoreConfigError\n"
    )
    assert _find_vault_imports_in_source(source) == []


def test_only_allowlisted_modules_import_vault():
    repo_root = Path(__file__).resolve().parents[2]
    bastion_dir = repo_root / "bastion"

    offenders: dict[str, list[int]] = {}
    for py_file in bastion_dir.rglob("*.py"):
        rel_path = py_file.relative_to(repo_root).as_posix()  # OS-independent
        import_lines = _find_vault_imports(py_file)
        if import_lines:
            offenders[rel_path] = import_lines

    importing_files = set(offenders.keys())
    # Subset (not equality): vault.py itself never needs to self-import, and
    # today nothing imports it yet (the funder that will is Phase 3 work).
    # This must still FAIL the moment any non-allowlisted module imports it.
    assert importing_files <= ALLOWED_IMPORTERS, (
        f"Only {ALLOWED_IMPORTERS} may import {FORBIDDEN_MODULE}, but found "
        f"imports in: {offenders}"
    )


def test_sweeper_does_not_import_vault():
    """SEC-02 negative contract, explicit for the sweeper (03-02).

    The sweeper is structurally incapable of loading the vault secret: it
    must never appear among the modules importing bastion.keystore.vault.
    This is stronger than merely implied by the subset check in
    test_only_allowlisted_modules_import_vault — it fails the build the
    moment sweeper.py imports vault.py, even if some other module were
    added to ALLOWED_IMPORTERS in the same change.
    """
    repo_root = Path(__file__).resolve().parents[2]
    bastion_dir = repo_root / "bastion"

    offenders: dict[str, list[int]] = {}
    for py_file in bastion_dir.rglob("*.py"):
        rel_path = py_file.relative_to(repo_root).as_posix()
        import_lines = _find_vault_imports(py_file)
        if import_lines:
            offenders[rel_path] = import_lines

    assert "bastion/sweeper.py" not in offenders, (
        f"sweeper.py must never import {FORBIDDEN_MODULE} (SEC-02), but "
        f"found imports at lines: {offenders.get('bastion/sweeper.py')}"
    )
