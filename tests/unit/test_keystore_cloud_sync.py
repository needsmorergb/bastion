"""Tests for bastion.keystore.cloudsync -- cloud-sync path refusal (SEC-04).

Covers: cloud-sync segment detection on a synthetic path, default refuse
(raise) behavior, the explicit allow_cloud_sync override downgrading to a
loud warning, and the empty/unset KEYSTORE_DIR guard (fails loud regardless
of the override, so no silent default lands under a synced home).
"""

import pytest

from bastion.keystore.cloudsync import check_keystore_dir, detect_cloud_sync
from bastion.keystore.errors import KeystoreCloudSyncError, KeystoreConfigError


def test_detect_cloud_sync_matches_known_provider_segment(tmp_path):
    synced = tmp_path / "OneDrive" / "keystore"

    assert detect_cloud_sync(str(synced)) is not None


def test_detect_cloud_sync_returns_none_for_plain_path(tmp_path):
    plain = tmp_path / "keystore"

    assert detect_cloud_sync(str(plain)) is None


@pytest.mark.parametrize(
    "provider_segment",
    ["Dropbox", "OneDrive", "Google Drive", "Mobile Documents", "CloudDocs"],
)
def test_detect_cloud_sync_is_case_insensitive_for_each_provider(tmp_path, provider_segment):
    synced = tmp_path / provider_segment / "keystore"

    assert detect_cloud_sync(str(synced).upper()) is not None


def test_check_keystore_dir_raises_by_default_on_cloud_sync_path(tmp_path):
    synced = tmp_path / "Dropbox" / "keystore"

    with pytest.raises(KeystoreCloudSyncError):
        check_keystore_dir(str(synced))


def test_check_keystore_dir_override_downgrades_to_warning_not_raise(tmp_path):
    synced = tmp_path / "Dropbox" / "keystore"

    with pytest.warns(UserWarning):
        check_keystore_dir(str(synced), allow_cloud_sync=True)


def test_check_keystore_dir_raises_config_error_on_empty_path():
    with pytest.raises(KeystoreConfigError):
        check_keystore_dir("")


def test_check_keystore_dir_raises_config_error_on_whitespace_path():
    with pytest.raises(KeystoreConfigError):
        check_keystore_dir("   ")


def test_check_keystore_dir_empty_path_raises_regardless_of_override():
    with pytest.raises(KeystoreConfigError):
        check_keystore_dir("", allow_cloud_sync=True)


def test_check_keystore_dir_returns_none_for_normal_path(tmp_path):
    plain = tmp_path / "keystore"

    assert check_keystore_dir(str(plain)) is None
