import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import profile_manager
import remote_lease_service
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


class _FakeWebSocket:
    def __init__(self):
        self.json_messages = []
        self.text_messages = []
        self.closed = []

    async def send_json(self, payload):
        self.json_messages.append(payload)

    async def send_text(self, payload):
        self.text_messages.append(payload)

    async def close(self, code=1000, reason=""):
        self.closed.append({"code": code, "reason": reason})


async def _async_noop(*_args, **_kwargs):
    return None


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def isolated_remote_environment(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    state_file = tmp_path / "profile_state.json"

    old_profile_manager = profile_manager._profile_manager
    pm = profile_manager.ProfileManager(state_file=str(state_file), sessions_dir=str(sessions_dir))
    profile_manager._profile_manager = pm
    monkeypatch.setattr(profile_manager, "get_profile_manager", lambda: pm)

    old_dir = remote_lease_service.REMOTE_LEASES_DIR
    leases_dir = tmp_path / "remote_leases"
    leases_dir.mkdir()
    monkeypatch.setattr(remote_lease_service, "REMOTE_LEASES_DIR", leases_dir)

    yield pm, leases_dir

    profile_manager._profile_manager = old_profile_manager
    monkeypatch.setattr(remote_lease_service, "REMOTE_LEASES_DIR", old_dir)


def test_resolve_remote_session_spec_prefers_saved_facebook_proxy(monkeypatch):
    monkeypatch.setattr(remote_lease_service, "FacebookSession", _FakeFacebookSession)
    monkeypatch.setattr(remote_lease_service, "get_system_proxy", lambda: "http://env-proxy:9090")

    spec = remote_lease_service._resolve_remote_session_spec("adele_hamilton", "facebook")

    assert spec.platform == "facebook"
    assert spec.proxy_url == "http://session-proxy:8080"
    assert spec.proxy_source == "session"
    assert spec.start_url == "https://m.facebook.com/"
    assert spec.user_agent == "facebook-agent"


def test_resolve_remote_session_spec_uses_reddit_identity_and_env_proxy(monkeypatch):
    monkeypatch.setattr(remote_lease_service, "RedditSession", _FakeRedditSession)
    monkeypatch.setattr(remote_lease_service, "get_system_proxy", lambda: "http://env-proxy:9090")

    spec = remote_lease_service._resolve_remote_session_spec("reddit_neera_allvere", "reddit")

    assert spec.platform == "reddit"
    assert spec.proxy_url == "http://env-proxy:9090"
    assert spec.proxy_source == "env"
    assert spec.start_url == "https://www.reddit.com/user/Neera_Allvere/"
    assert spec.storage_state is not None
    assert spec.user_agent == REDDIT_MOBILE_USER_AGENT
    assert spec.is_mobile is True
    assert spec.has_touch is True


def test_resolve_remote_session_spec_rejects_reddit_without_persisted_auth(monkeypatch):
    monkeypatch.setattr(remote_lease_service, "RedditSession", _NoAuthRedditSession)
    monkeypatch.setattr(remote_lease_service, "get_system_proxy", lambda: "http://env-proxy:9090")

    with pytest.raises(RuntimeError, match="no persisted auth state"):
        remote_lease_service._resolve_remote_session_spec("reddit_neera_allvere", "reddit")


def test_attach_reuses_same_profile_as_observer_and_persists_logs(isolated_remote_environment, monkeypatch):
    pm, _leases_dir = isolated_remote_environment
    monkeypatch.setattr(remote_lease_service.RemoteLease, "ensure_browser_ready", _async_noop)
    monkeypatch.setattr(remote_lease_service.RemoteLease, "refresh_title", _async_noop)
    monkeypatch.setattr(remote_lease_service.RemoteLease, "_start_screencast", _async_noop)
    monkeypatch.setattr(remote_lease_service.RemoteLease, "_stop_screencast", _async_noop)
    monkeypatch.setattr(remote_lease_service.RemoteLease, "_capture_bootstrap_frame", _async_noop)
    monkeypatch.setattr(remote_lease_service.RemoteLease, "_teardown_browser", _async_noop)

    service = remote_lease_service.RemoteLeaseService()
    ws_one = _FakeWebSocket()
    ws_two = _FakeWebSocket()

    lease, first_viewer = _run(
        service.attach(
            websocket=ws_one,
            session_id="alpha",
            platform="facebook",
            username="alice",
        )
    )
    same_lease, second_viewer = _run(
        service.attach(
            websocket=ws_two,
            session_id="alpha",
            platform="facebook",
            username="bob",
        )
    )

    assert same_lease is lease
    assert first_viewer.role == "controller"
    assert second_viewer.role == "observer"
    assert pm.get_reservation("alpha")["owner"] == lease.lease_id

    takeover = _run(service.handle_takeover(lease=lease, username="bob"))
    assert takeover["controller_user"] == "bob"

    _run(service.close_lease(lease, reason="test_done"))

    assert pm.get_reservation("alpha") is None
    assert ws_one.closed
    assert ws_two.closed

    persisted_logs = service.get_logs(session_id="alpha", platform="facebook", limit=20)
    assert any(event["action"] == "lease_takeover" for event in persisted_logs)


def test_start_detached_enforces_capacity_limit(isolated_remote_environment, monkeypatch):
    monkeypatch.setattr(remote_lease_service.RemoteLease, "ensure_browser_ready", _async_noop)
    monkeypatch.setattr(remote_lease_service.RemoteLease, "refresh_title", _async_noop)
    monkeypatch.setattr(remote_lease_service.RemoteLease, "_schedule_idle_close", lambda self: None)
    monkeypatch.setattr(remote_lease_service.RemoteLease, "_teardown_browser", _async_noop)
    monkeypatch.setattr(remote_lease_service, "MAX_ACTIVE_LEASES", 1)

    service = remote_lease_service.RemoteLeaseService()

    first = _run(service.start_detached(session_id="alpha", platform="facebook", username="alice"))
    assert first["success"] is True

    with pytest.raises(remote_lease_service.RemoteLeaseError, match="remote capacity full"):
        _run(service.start_detached(session_id="bravo", platform="reddit", username="bob"))

    lease = service.find_active_lease(session_id="alpha", platform="facebook")
    assert lease is not None
    _run(service.close_lease(lease, reason="test_done"))


def test_prepare_upload_excludes_temp_path(isolated_remote_environment, monkeypatch):
    monkeypatch.setattr(remote_lease_service.RemoteLease, "ensure_browser_ready", _async_noop)
    monkeypatch.setattr(remote_lease_service.RemoteLease, "refresh_title", _async_noop)
    monkeypatch.setattr(remote_lease_service.RemoteLease, "_schedule_idle_close", lambda self: None)
    monkeypatch.setattr(remote_lease_service.RemoteLease, "_teardown_browser", _async_noop)

    service = remote_lease_service.RemoteLeaseService()
    _run(service.start_detached(session_id="alpha", platform="facebook", username="alice"))
    lease = service.find_active_lease(session_id="alpha", platform="facebook")
    assert lease is not None

    lease.pending_upload = {
        "image_id": "img_1",
        "path": "/tmp/private-file.png",
        "filename": "private-file.png",
        "size": 128,
        "expires_at": "2030-01-01T00:00:00Z",
    }
    result = service.prepare_upload(session_id="alpha")

    assert result["success"] is True
    assert result["filename"] == "private-file.png"
    assert "path" not in result

    _run(service.close_lease(lease, reason="test_done"))
