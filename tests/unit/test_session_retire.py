"""Tests for the D-10 nonzero-token-balance retire guard (SESS-07).

Covers the contract added on top of ``bastion.keystore.session.retire()``:
retire refuses to hard-delete a keystore when a nonzero token balance
remains in the session's ATAs, raising ``KeystoreRetireError`` and leaving
both the keystore file and the in-memory secret untouched. Empty/None
``token_accounts`` proceed exactly as the pre-existing (Phase 2) behavior.
"""

from __future__ import annotations

import os

import pytest

from bastion.keystore.errors import KeystoreRetireError
from bastion.keystore.session import generate, retire, save

PASSPHRASE = "correct-horse-battery-staple"


def _token_account(amount: str) -> dict:
    """Build a minimal jsonParsed getTokenAccountsByOwner entry."""
    return {
        "account": {
            "data": {
                "parsed": {
                    "info": {
                        "tokenAmount": {
                            "amount": amount,
                        }
                    }
                }
            }
        }
    }


def test_retire_raises_on_nonzero_token_balance_and_leaves_file_untouched(tmp_path):
    session = generate()
    path = save(session, str(tmp_path), PASSPHRASE)
    token_accounts = [_token_account("500")]

    with pytest.raises(KeystoreRetireError):
        retire(session, str(tmp_path), token_accounts)

    assert os.path.exists(path)
    # Refusal path must not zeroize the in-memory secret either.
    assert bytes(session._secret) != b"\x00" * len(session._secret)


def test_retire_raises_when_any_of_several_accounts_is_nonzero(tmp_path):
    session = generate()
    path = save(session, str(tmp_path), PASSPHRASE)
    token_accounts = [_token_account("0"), _token_account("42"), _token_account("0")]

    with pytest.raises(KeystoreRetireError):
        retire(session, str(tmp_path), token_accounts)

    assert os.path.exists(path)


def test_retire_proceeds_when_all_token_accounts_are_zero(tmp_path):
    session = generate()
    path = save(session, str(tmp_path), PASSPHRASE)
    token_accounts = [_token_account("0"), _token_account("0")]

    retire(session, str(tmp_path), token_accounts)

    assert not os.path.exists(path)
    assert bytes(session._secret) == b"\x00" * len(session._secret)


def test_retire_proceeds_when_token_accounts_is_empty_list(tmp_path):
    session = generate()
    path = save(session, str(tmp_path), PASSPHRASE)

    retire(session, str(tmp_path), [])

    assert not os.path.exists(path)
    assert bytes(session._secret) == b"\x00" * len(session._secret)


def test_retire_proceeds_when_token_accounts_is_none_backward_compat(tmp_path):
    session = generate()
    path = save(session, str(tmp_path), PASSPHRASE)

    retire(session, str(tmp_path), None)

    assert not os.path.exists(path)
    assert bytes(session._secret) == b"\x00" * len(session._secret)


def test_retire_proceeds_when_token_accounts_omitted_backward_compat(tmp_path):
    session = generate()
    path = save(session, str(tmp_path), PASSPHRASE)

    retire(session, str(tmp_path))

    assert not os.path.exists(path)
    assert bytes(session._secret) == b"\x00" * len(session._secret)


def test_retire_nonzero_balance_with_bare_pubkey_raises_and_leaves_file(tmp_path):
    session = generate()
    path = save(session, str(tmp_path), PASSPHRASE)
    token_accounts = [_token_account("1")]

    with pytest.raises(KeystoreRetireError):
        retire(session.pubkey, str(tmp_path), token_accounts)

    assert os.path.exists(path)
