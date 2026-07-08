"""Tests for the D-10 nonzero-token-balance retire guard (SESS-07) and the
WR-02 fail-closed contract on top of it.

Covers the contract added on top of ``bastion.keystore.session.retire()``:
retire refuses to hard-delete a keystore when a nonzero token balance
remains in the session's ATAs, raising ``KeystoreRetireError`` and leaving
both the keystore file and the in-memory secret untouched. A verified-empty
``token_accounts`` (empty list, or all-zero entries) proceeds exactly as
before. ``token_accounts=None`` is fail-closed (WR-02): it raises unless the
caller explicitly opts out via ``token_check_skipped=True``, so a failed
RPC lookup can never be silently mistaken for a confirmed-zero balance.
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


def test_retire_raises_when_token_accounts_is_none_and_not_skipped(tmp_path):
    """WR-02: None alone is ambiguous (never-checked vs. lookup-failed) and
    must fail closed -- the keystore file and in-memory secret are left
    untouched, exactly like the nonzero-balance refusal path."""
    session = generate()
    path = save(session, str(tmp_path), PASSPHRASE)

    with pytest.raises(KeystoreRetireError):
        retire(session, str(tmp_path), None)

    assert os.path.exists(path)
    assert bytes(session._secret) != b"\x00" * len(session._secret)


def test_retire_raises_when_token_accounts_omitted_and_not_skipped(tmp_path):
    """WR-02: omitting token_accounts entirely is the same as passing None
    -- still fail-closed without an explicit token_check_skipped=True."""
    session = generate()
    path = save(session, str(tmp_path), PASSPHRASE)

    with pytest.raises(KeystoreRetireError):
        retire(session, str(tmp_path))

    assert os.path.exists(path)
    assert bytes(session._secret) != b"\x00" * len(session._secret)


def test_retire_proceeds_when_token_accounts_is_none_and_check_explicitly_skipped(tmp_path):
    """WR-02: a caller that deliberately never intends to check token
    balances must say so explicitly -- token_check_skipped=True is the only
    way None is accepted, and it behaves exactly like the old backward-
    compatible default."""
    session = generate()
    path = save(session, str(tmp_path), PASSPHRASE)

    retire(session, str(tmp_path), None, token_check_skipped=True)

    assert not os.path.exists(path)
    assert bytes(session._secret) == b"\x00" * len(session._secret)


def test_retire_proceeds_when_token_accounts_omitted_and_check_explicitly_skipped(tmp_path):
    session = generate()
    path = save(session, str(tmp_path), PASSPHRASE)

    retire(session, str(tmp_path), token_check_skipped=True)

    assert not os.path.exists(path)
    assert bytes(session._secret) == b"\x00" * len(session._secret)


def test_retire_nonzero_balance_with_bare_pubkey_raises_and_leaves_file(tmp_path):
    session = generate()
    path = save(session, str(tmp_path), PASSPHRASE)
    token_accounts = [_token_account("1")]

    with pytest.raises(KeystoreRetireError):
        retire(session.pubkey, str(tmp_path), token_accounts)

    assert os.path.exists(path)
