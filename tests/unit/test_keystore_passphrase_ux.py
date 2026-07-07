"""Tests for bastion.keystore.passphrase -- confirm-on-create no-echo prompt.

Covers SEC-05: the create-passphrase is confirmed by re-entry (retrying on
mismatch up to a bounded number of attempts, then aborting), entered via
``getpass`` (never ``input``, never echoed), rejects an empty entry, and
warns (does not fail) on a very short passphrase.
"""

import builtins

import pytest

from bastion.keystore.errors import KeystoreError
from bastion.keystore.passphrase import MIN_PASSPHRASE_WARN_LEN, prompt_new_passphrase


def _scripted_getpass(monkeypatch, values):
    """Patch getpass.getpass to return successive values from `values`."""
    iterator = iter(values)

    def fake_getpass(prompt=""):
        return next(iterator)

    monkeypatch.setattr("getpass.getpass", fake_getpass)


def _forbid_input(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("builtins.input must never be called for passphrase entry")

    monkeypatch.setattr(builtins, "input", fail_if_called)


def test_matching_entries_return_passphrase(monkeypatch):
    _forbid_input(monkeypatch)
    _scripted_getpass(monkeypatch, ["correct-horse-battery", "correct-horse-battery"])

    result = prompt_new_passphrase()

    assert result == "correct-horse-battery"


def test_mismatch_then_match_retries_and_returns(monkeypatch):
    _forbid_input(monkeypatch)
    _scripted_getpass(
        monkeypatch,
        [
            "first-attempt-one",
            "first-attempt-two",
            "correct-horse-battery",
            "correct-horse-battery",
        ],
    )

    result = prompt_new_passphrase()

    assert result == "correct-horse-battery"


def test_exhausting_all_attempts_raises_keystore_error(monkeypatch):
    _forbid_input(monkeypatch)
    _scripted_getpass(monkeypatch, ["a", "b", "c", "d", "e", "f"])

    with pytest.raises(KeystoreError):
        prompt_new_passphrase(confirm_attempts=3)


def test_empty_entry_is_rejected_and_retries(monkeypatch):
    _forbid_input(monkeypatch)
    _scripted_getpass(
        monkeypatch,
        ["", "", "correct-horse-battery", "correct-horse-battery"],
    )

    result = prompt_new_passphrase()

    assert result == "correct-horse-battery"


def test_short_passphrase_warns_but_still_returns(monkeypatch):
    _forbid_input(monkeypatch)
    short = "a" * (MIN_PASSPHRASE_WARN_LEN - 1)
    _scripted_getpass(monkeypatch, [short, short])

    with pytest.warns(UserWarning):
        result = prompt_new_passphrase()

    assert result == short


def test_never_raises_with_secret_value_in_message(monkeypatch):
    _forbid_input(monkeypatch)
    secret_looking = "super-secret-value-should-not-leak"
    _scripted_getpass(monkeypatch, [secret_looking, "totally-different-value"] * 3)

    with pytest.raises(KeystoreError) as exc_info:
        prompt_new_passphrase(confirm_attempts=3)

    message = str(exc_info.value)
    assert secret_looking not in message
