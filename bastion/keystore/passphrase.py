"""Confirm-on-create, no-echo passphrase prompt for new keystore creation.

SEC-05: the create-passphrase must never be echoed to the terminal, must be
confirmed by re-entry (retrying on mismatch up to a bounded number of
attempts before aborting), and must never be logged or printed anywhere.
Mirrors ``bastion.config.get_passphrase``'s ``getpass`` no-echo primitive but
adds the confirm/retry/short-warning UX needed only at *create* time --
unlock continues to use ``config.get_passphrase()`` unchanged.
"""

from __future__ import annotations

import getpass
import warnings

from bastion.keystore.errors import KeystoreError

# Minimal strength policy (02-CONTEXT.md "Cloud-Sync Refusal & Passphrase
# UX"): warn, don't enforce complexity, on a local single-user tool.
MIN_PASSPHRASE_WARN_LEN = 8


def prompt_new_passphrase(confirm_attempts: int = 3) -> str:
    """Prompt for a new keystore passphrase, confirmed by re-entry.

    Reads two ``getpass.getpass`` entries per attempt (no echo, never
    ``input``). An empty entry or a mismatch between the two entries
    consumes one attempt and re-prompts; after ``confirm_attempts`` failed
    attempts, raises ``KeystoreError`` (message-only -- never echoes the
    entered value). On a non-empty match, emits a gentle ``UserWarning`` if
    the passphrase is shorter than ``MIN_PASSPHRASE_WARN_LEN``, then returns
    it.
    """
    for _ in range(confirm_attempts):
        first = getpass.getpass("New keystore passphrase: ")
        second = getpass.getpass("Confirm passphrase: ")

        if not first or not second:
            continue
        if first != second:
            continue

        if len(first) < MIN_PASSPHRASE_WARN_LEN:
            warnings.warn(
                "The keystore passphrase is very short. A longer "
                "passphrase is recommended, though not required.",
                UserWarning,
                stacklevel=2,
            )
        return first

    raise KeystoreError(
        "Passphrase entry did not match after "
        f"{confirm_attempts} attempts. Aborting keystore creation."
    )
