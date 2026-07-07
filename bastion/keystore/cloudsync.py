"""Cloud-sync directory detection + refuse-or-warn startup check (SEC-04).

A keystore directory silently replicated to a third-party cloud service
(Dropbox, OneDrive, Google Drive, iCloud) breaks the non-custodial guarantee
-- the encrypted keystore file leaves the machine even though its *contents*
remain encrypted. The default behavior is to refuse to run (raise) when
``KEYSTORE_DIR`` resolves under a detected cloud-sync path. An explicit
``allow_cloud_sync`` opt-in downgrades the refusal to a loud ``UserWarning``
for advanced users who accept the risk.

The override is off by default and mirrors the ``--armed`` opt-in philosophy
(CONTEXT.md): dangerous escape hatches require deliberate, warned opt-in.
Wiring this parameter to a CLI flag / Config field is deferred to Phase 7 --
this module only exposes the function parameter.
"""

from __future__ import annotations

import os
import warnings

from bastion.keystore.errors import KeystoreCloudSyncError, KeystoreConfigError

# Case-insensitive path-segment matches. Segment-matching (rather than exact
# default install paths, which change across OS versions and are frequently
# relocated by users) is deliberately robust to relocated/renamed cloud
# folders -- see 02-RESEARCH.md "Don't Hand-Roll" / Assumptions Log A1.
CLOUD_SYNC_SEGMENTS: tuple[str, ...] = (
    "dropbox",
    "onedrive",
    "google drive",
    "mobile documents",
    "clouddocs",
)


def detect_cloud_sync(path: str) -> str | None:
    """Return the matched cloud-sync segment, or None if path looks plain.

    Resolves ``path`` with ``os.path.realpath`` (so a symlink into a synced
    folder is also caught), lowercases it, and checks each path segment
    (handling both ``/`` and ``\\`` separators) for a substring match
    against ``CLOUD_SYNC_SEGMENTS``.
    """
    resolved = os.path.realpath(path).lower()
    segments = resolved.replace("\\", "/").split("/")
    for segment in segments:
        for candidate in CLOUD_SYNC_SEGMENTS:
            if candidate in segment:
                return candidate
    return None


def check_keystore_dir(path: str, allow_cloud_sync: bool = False) -> None:
    """Refuse (or warn on) a KEYSTORE_DIR that resolves under a cloud-sync path.

    Raises ``KeystoreConfigError`` on an empty/whitespace ``path`` -- an
    unset ``KEYSTORE_DIR`` must never silently fall back to a default (a
    silent home-dir default could itself land under a synced path). This
    guard applies regardless of ``allow_cloud_sync``.

    Raises ``KeystoreCloudSyncError`` on a detected cloud-sync path unless
    ``allow_cloud_sync=True``, in which case it emits a loud ``UserWarning``
    instead of raising and returns normally.

    Returns ``None`` silently for a normal, non-cloud-synced path.
    """
    if not path or not path.strip():
        raise KeystoreConfigError(
            "KEYSTORE_DIR is unset or empty. Bastion never silently "
            "defaults the keystore location -- set KEYSTORE_DIR explicitly."
        )

    matched = detect_cloud_sync(path)
    if matched is None:
        return

    if allow_cloud_sync:
        warnings.warn(
            f"KEYSTORE_DIR resolves under a detected cloud-sync path "
            f"({matched!r}). Encrypted keystore files will be replicated "
            "to a third-party cloud service. Continuing because "
            "allow_cloud_sync was explicitly set.",
            UserWarning,
            stacklevel=2,
        )
        return

    raise KeystoreCloudSyncError(
        f"KEYSTORE_DIR resolves under a detected cloud-sync path "
        f"({matched!r}). Refusing to run to avoid replicating encrypted "
        "keystore files to a third-party cloud service. Pass "
        "allow_cloud_sync=True to override (advanced users only)."
    )
