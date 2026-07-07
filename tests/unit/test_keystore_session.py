"""Tests for bastion.keystore.session -- the full session-keystore lifecycle.

Covers SESS-01 (generate), SESS-04 (encrypt-at-rest + atomic 0600 write),
SESS-05 (load-by-pubkey, fail-closed on wrong passphrase), and the redacted
in-memory ``SessionKeypair`` type's repr-safety + best-effort zeroizing
``retire()``.
"""

from __future__ import annotations

import json
import os
import stat

import pytest
from solders.keypair import Keypair

from bastion.keystore.errors import KeystoreCloudSyncError, KeystoreConfigError, KeystoreWrongPassphraseError
from bastion.keystore.session import SessionKeypair, generate, load, retire, save

PASSPHRASE = "correct-horse-battery-staple"
WRONG_PASSPHRASE = "wrong-horse-battery-staple"


def test_generate_returns_session_keypair():
    session = generate()

    assert isinstance(session, SessionKeypair)
    assert isinstance(session.pubkey, str)
    assert len(session.pubkey) > 0


def test_generate_yields_distinct_pubkeys_each_call():
    first = generate()
    second = generate()

    assert first.pubkey != second.pubkey


def test_session_keypair_repr_is_redacted():
    session = generate()

    rendered_repr = repr(session)
    rendered_str = str(session)

    assert "REDACTED" in rendered_repr
    assert "REDACTED" in rendered_str
    assert bytes(session._secret).hex() not in rendered_repr
    assert bytes(session._secret).hex() not in rendered_str


def test_save_creates_pubkey_json_file(tmp_path):
    session = generate()

    path = save(session, str(tmp_path), PASSPHRASE)

    assert path == str(tmp_path / f"{session.pubkey}.json")
    assert os.path.exists(path)


@pytest.mark.skipif(os.name != "posix", reason="Exact 0600 mode bits are POSIX-only (Pitfall 1)")
def test_save_writes_exact_0600_permissions_on_posix(tmp_path):
    session = generate()

    path = save(session, str(tmp_path), PASSPHRASE)

    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


@pytest.mark.skipif(os.name == "posix", reason="Windows best-effort assertion only")
def test_save_on_windows_documents_best_effort_permissions(tmp_path):
    """On Windows, os.chmod only toggles the DOS read-only bit (Pitfall 1) --
    assert the file exists and is at least not silently claimed as 0600, never
    an unconditional == 0o600 assertion.
    """
    session = generate()

    path = save(session, str(tmp_path), PASSPHRASE)

    assert os.path.exists(path)
    # Best-effort only: Windows st_mode reports 0o666 regardless of chmod.
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode is not None


def test_save_writes_no_temp_file_left_behind(tmp_path):
    session = generate()

    save(session, str(tmp_path), PASSPHRASE)

    entries = list(tmp_path.iterdir())
    assert len(entries) == 1
    assert entries[0].name == f"{session.pubkey}.json"


def test_saved_file_is_ciphertext_only_no_plaintext_key(tmp_path):
    session = generate()

    path = save(session, str(tmp_path), PASSPHRASE)

    with open(path, encoding="utf-8") as f:
        blob = json.load(f)

    assert "version" in blob
    assert "ciphertext" in blob
    assert "salt" in blob
    serialized = json.dumps(blob)
    assert bytes(session._secret).hex() not in serialized


def test_load_roundtrips_exact_keypair(tmp_path):
    session = generate()
    save(session, str(tmp_path), PASSPHRASE)

    loaded = load(session.pubkey, str(tmp_path), PASSPHRASE)

    assert loaded.pubkey == session.pubkey
    assert bytes(loaded._secret) == bytes(session._secret)


def test_load_wrong_passphrase_fails_closed(tmp_path):
    session = generate()
    save(session, str(tmp_path), PASSPHRASE)

    with pytest.raises(KeystoreWrongPassphraseError):
        load(session.pubkey, str(tmp_path), WRONG_PASSPHRASE)


def test_save_into_cloud_sync_dir_raises_by_default(tmp_path):
    session = generate()
    synced_dir = tmp_path / "Dropbox" / "keystore"

    with pytest.raises(KeystoreCloudSyncError):
        save(session, str(synced_dir), PASSPHRASE)


def test_save_into_empty_dir_raises_config_error():
    session = generate()

    with pytest.raises(KeystoreConfigError):
        save(session, "", PASSPHRASE)


def test_retire_removes_keystore_file_and_zeroizes_secret(tmp_path):
    session = generate()
    path = save(session, str(tmp_path), PASSPHRASE)

    retire(session, str(tmp_path))

    assert not os.path.exists(path)
    assert bytes(session._secret) == b"\x00" * len(session._secret)


def test_retire_tolerates_already_absent_file(tmp_path):
    session = generate()
    # Never saved -- file was never created.

    retire(session, str(tmp_path))  # must not raise

    assert bytes(session._secret) == b"\x00" * len(session._secret)


def test_retire_accepts_bare_pubkey_string(tmp_path):
    session = generate()
    path = save(session, str(tmp_path), PASSPHRASE)

    retire(session.pubkey, str(tmp_path))

    assert not os.path.exists(path)


def test_load_returned_keypair_is_valid_solders_keypair(tmp_path):
    session = generate()
    save(session, str(tmp_path), PASSPHRASE)

    loaded = load(session.pubkey, str(tmp_path), PASSPHRASE)

    restored = Keypair.from_bytes(bytes(loaded._secret))
    assert str(restored.pubkey()) == loaded.pubkey
