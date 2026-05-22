import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fb_session
import main
import reddit_session
from proxy_manager import ProxyManager


def test_proxy_manager_uses_store_default_before_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PROXY_URL", "http://env-proxy:9000")
    manager = ProxyManager(str(tmp_path / "proxies.json"))

    bootstrap = manager.get_active_proxy()
    assert bootstrap["url"] == "http://env-proxy:9000"
    assert bootstrap["source"] == "bootstrap_env"

    proxy = manager.add_proxy("active", "http://store-proxy:9001")
    active = manager.get_active_proxy()
    assert active["id"] == proxy["id"]
    assert active["url"] == "http://store-proxy:9001"
    assert active["source"] == "proxy_store"


def test_facebook_session_does_not_persist_proxy_on_import():
    session = fb_session.FacebookSession("Proxy Proof")
    data = session.import_from_cookies(
        cookies=[
            {"name": "c_user", "value": "123", "domain": ".facebook.com"},
            {"name": "xs", "value": "abc", "domain": ".facebook.com"},
        ],
        user_agent="agent",
        proxy="http://stale-proxy:9000",
    )

    assert "proxy" not in data
    assert session.get_proxy() is None


def test_session_proxy_migration_removes_only_top_level_proxy(tmp_path, monkeypatch):
    fb_dir = tmp_path / "facebook"
    reddit_dir = tmp_path / "reddit"
    fb_dir.mkdir()
    reddit_dir.mkdir()
    monkeypatch.setattr(fb_session, "SESSIONS_DIR", fb_dir)
    monkeypatch.setattr(reddit_session, "REDDIT_SESSIONS_DIR", reddit_dir)

    facebook_payload = {
        "profile_name": "Adele",
        "cookies": [{"name": "c_user", "value": "1"}, {"name": "xs", "value": "2"}],
        "user_agent": "fb-agent",
        "viewport": {"width": 393, "height": 873},
        "device": {"timezone": "America/New_York", "locale": "en-US"},
        "tags": ["premium"],
        "proxy": "http://stale-facebook:9000",
    }
    reddit_payload = {
        "platform": "reddit",
        "profile_name": "reddit_amy",
        "storage_state": {"cookies": [{"name": "reddit_session", "value": "r"}]},
        "cookies": [{"name": "reddit_session", "value": "r"}],
        "user_agent": "reddit-agent",
        "viewport": {"width": 393, "height": 873},
        "device": {"timezone": "America/Chicago", "locale": "en-US"},
        "linked_credential_id": "cred_1",
        "proxy": "http://stale-reddit:9000",
    }
    (fb_dir / "adele.json").write_text(json.dumps(facebook_payload))
    (reddit_dir / "reddit_amy.json").write_text(json.dumps(reddit_payload))

    dry_run = main._session_proxy_migration_report(apply=False)
    assert dry_run["changed_file_count"] == 2
    assert dry_run["all_protected_fields_preserved"] is True
    assert json.loads((fb_dir / "adele.json").read_text())["proxy"] == "http://stale-facebook:9000"

    applied = main._session_proxy_migration_report(apply=True)
    assert applied["success"] is True
    assert applied["changed_file_count"] == 2
    assert applied["all_protected_fields_preserved"] is True

    migrated_facebook = json.loads((fb_dir / "adele.json").read_text())
    migrated_reddit = json.loads((reddit_dir / "reddit_amy.json").read_text())
    assert "proxy" not in migrated_facebook
    assert "proxy" not in migrated_reddit

    expected_facebook = dict(facebook_payload)
    expected_facebook.pop("proxy")
    expected_reddit = dict(reddit_payload)
    expected_reddit.pop("proxy")
    assert migrated_facebook == expected_facebook
    assert migrated_reddit == expected_reddit
