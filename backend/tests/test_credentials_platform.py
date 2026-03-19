import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from credentials import CredentialManager


def test_reddit_account_line_import_supports_4_field_lines_with_defaults(tmp_path: Path):
    manager = CredentialManager(file_path=str(tmp_path / "credentials.json"))
    storage_key = manager.import_reddit_account_line(
        "Mary_Miaby:secretpass:mark@example.com:mailpass",
        fixture=False,
        source_label="/tmp/business-order-transaction-details.txt",
    )

    assert storage_key == "reddit::Mary_Miaby"

    record = manager.get_credential(storage_key, platform="reddit")
    assert record is not None
    assert record["platform"] == "reddit"
    assert record["email"] == "mark@example.com"
    assert record["totp_secret"] is None
    assert record["profile_name"] == "reddit_mary_miaby"
    assert record["profile_url"] == "https://www.reddit.com/user/Mary_Miaby/"
    assert record["tags"] == ["reddit", "source_business_order_transaction_details"]


def test_reddit_account_line_import_and_lookup(tmp_path: Path):
    manager = CredentialManager(file_path=str(tmp_path / "credentials.json"))
    storage_key = manager.import_reddit_account_line(
        "Mary_Miaby:secretpass:mark@example.com:mailpass:ABCD EFGH IJKL MNOP:https://www.reddit.com/user/Mary_Miaby/",
        fixture=True,
    )

    assert storage_key == "reddit::Mary_Miaby"

    by_key = manager.get_credential(storage_key)
    assert by_key is not None
    assert by_key["platform"] == "reddit"
    assert by_key["email"] == "mark@example.com"
    assert by_key["profile_url"] == "https://www.reddit.com/user/Mary_Miaby/"
    assert by_key["fixture"] is True

    by_uid = manager.get_credential("Mary_Miaby", platform="reddit")
    assert by_uid is not None
    assert by_uid["credential_id"] == storage_key


def test_reddit_account_line_import_rejects_unsupported_field_count(tmp_path: Path):
    manager = CredentialManager(file_path=str(tmp_path / "credentials.json"))

    with pytest.raises(ValueError, match="Expected 4 or 6 Reddit credential fields"):
        manager.import_reddit_account_line("Mary_Miaby:secretpass:mark@example.com:mailpass:EXTRA")


def test_legacy_facebook_records_default_to_facebook(tmp_path: Path):
    manager = CredentialManager(file_path=str(tmp_path / "credentials.json"))
    key = manager.add_credential(uid="fb_uid_1", password="pw", secret="JBSWY3DPEHPK3PXP", profile_name="FB One")

    assert key == "fb_uid_1"

    listing = manager.get_all_credentials()
    assert len(listing) == 1
    assert listing[0]["platform"] == "facebook"
    assert listing[0]["uid"] == "fb_uid_1"
    assert listing[0]["has_secret"] is True
