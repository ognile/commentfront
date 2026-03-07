import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import browser_manager
from config import REDDIT_MOBILE_USER_AGENT


class _FakeFacebookSession:
    def __init__(self, profile_name: str):
        self.profile_name = profile_name

    def load(self):
        return {"profile_name": self.profile_name}

    def has_valid_cookies(self):
        return True

    def get_proxy(self):
        return "http://session-proxy:8080"

    def get_device_fingerprint(self):
        return {"timezone": "America/New_York", "locale": "en-US"}

    def get_user_agent(self):
        return "facebook-agent"

    def get_viewport(self):
        return {"width": 393, "height": 873}


class _FakeRedditSession:
    def __init__(self, profile_name: str):
        self.profile_name = profile_name

    def load(self):
        return {"profile_name": self.profile_name}

    def get_storage_state(self):
        return {
            "cookies": [{"name": "reddit_session", "value": "abc"}],
            "origins": [],
        }

    def get_cookies(self):
        return [{"name": "reddit_session", "value": "abc"}]

    def get_proxy(self):
        return None

    def get_device_fingerprint(self):
        return {"timezone": "America/Chicago", "locale": "en-US"}

    def get_user_agent(self):
        return None

    def get_viewport(self):
        return {"width": 393, "height": 873}

    def get_profile_url(self):
        return "https://www.reddit.com/user/Neera_Allvere/"


class _NoAuthRedditSession(_FakeRedditSession):
    def get_storage_state(self):
        return {}

    def get_cookies(self):
        return []


def test_resolve_remote_session_spec_prefers_saved_facebook_proxy(monkeypatch):
    monkeypatch.setattr(browser_manager, "FacebookSession", _FakeFacebookSession)
    monkeypatch.setattr(browser_manager, "get_system_proxy", lambda: "http://env-proxy:9090")

    spec = browser_manager._resolve_remote_session_spec("adele_hamilton", "facebook")

    assert spec.platform == "facebook"
    assert spec.proxy_url == "http://session-proxy:8080"
    assert spec.proxy_source == "session"
    assert spec.start_url == "https://m.facebook.com/"
    assert spec.user_agent == "facebook-agent"


def test_resolve_remote_session_spec_uses_reddit_identity_and_env_proxy(monkeypatch):
    monkeypatch.setattr(browser_manager, "RedditSession", _FakeRedditSession)
    monkeypatch.setattr(browser_manager, "get_system_proxy", lambda: "http://env-proxy:9090")

    spec = browser_manager._resolve_remote_session_spec("reddit_neera_allvere", "reddit")

    assert spec.platform == "reddit"
    assert spec.proxy_url == "http://env-proxy:9090"
    assert spec.proxy_source == "env"
    assert spec.start_url == "https://www.reddit.com/user/Neera_Allvere/"
    assert spec.storage_state is not None
    assert spec.user_agent == REDDIT_MOBILE_USER_AGENT
    assert spec.is_mobile is True
    assert spec.has_touch is True


def test_resolve_remote_session_spec_rejects_reddit_without_persisted_auth(monkeypatch):
    monkeypatch.setattr(browser_manager, "RedditSession", _NoAuthRedditSession)
    monkeypatch.setattr(browser_manager, "get_system_proxy", lambda: "http://env-proxy:9090")

    with pytest.raises(RuntimeError, match="no persisted auth state"):
        browser_manager._resolve_remote_session_spec("reddit_neera_allvere", "reddit")
