"""No-secret-in-logs regression over the full session-keystore flow (SEC-01).

Exercises generate -> save -> load (correct passphrase) -> load (wrong
passphrase, expect fail-closed) -> retire under captured output, then asserts
no secret-shaped string (the sentinel passphrase, or the raw/base58 key
material) ever appears in captured stdout, captured stderr, or the log
record text.

Uses ``capfd`` (OS-file-descriptor capture) rather than ``capsys``, since
``cryptography`` and ``solders`` are both compiled C-extensions that could in
principle write directly to an OS file descriptor, bypassing
``sys.stdout``/``sys.stderr`` redirection that ``capsys`` alone would catch
(02-RESEARCH.md "No-secret-in-logs regression test pattern"). pytest does not
allow using ``capsys`` and ``capfd`` in the same test, so ``capfd`` alone is
used as the stronger guarantee of the two.
"""

from __future__ import annotations

import base64
import logging
import os

import pytest

from bastion.keystore.errors import KeystoreWrongPassphraseError
from bastion.keystore.session import generate, load, retire, save

# Unique, grep-unmistakable sentinel (mirrors tests/unit/test_config.py's
# UNMISTAKABLE-* convention) so a real leak is unambiguous, never a false
# positive from an unrelated short string.
SENTINEL_PASSPHRASE = "UNMISTAKABLE-KEYSTORE-SESSION-PASSPHRASE-SENTINEL"
WRONG_SENTINEL_PASSPHRASE = "UNMISTAKABLE-WRONG-PASSPHRASE-SENTINEL"


def test_no_secret_leak_across_full_generate_save_load_retire_flow(capfd, caplog, tmp_path):
    caplog.set_level(logging.DEBUG)

    session = generate()
    secret_bytes = bytes(session._secret)
    secret_hex = secret_bytes.hex()
    secret_raw_b64 = base64.b64encode(secret_bytes).decode("ascii")

    path = save(session, str(tmp_path), SENTINEL_PASSPHRASE)

    loaded = load(session.pubkey, str(tmp_path), SENTINEL_PASSPHRASE)
    assert bytes(loaded._secret) == secret_bytes

    with pytest.raises(KeystoreWrongPassphraseError):
        load(session.pubkey, str(tmp_path), WRONG_SENTINEL_PASSPHRASE)

    retire(session, str(tmp_path))
    retire(loaded, str(tmp_path))

    fd_out_err = capfd.readouterr()

    rendered_repr = repr(session)

    haystacks = [
        fd_out_err.out,
        fd_out_err.err,
        caplog.text,
    ]

    for haystack in haystacks:
        assert SENTINEL_PASSPHRASE not in haystack
        assert WRONG_SENTINEL_PASSPHRASE not in haystack
        assert secret_hex not in haystack
        assert secret_raw_b64 not in haystack

    assert "REDACTED" in rendered_repr
    assert secret_hex not in rendered_repr

    # Sanity: the file actually existed and was removed by retire (flow ran for real).
    assert not os.path.exists(path)
