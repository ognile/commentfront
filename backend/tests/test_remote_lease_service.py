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

    def get_user_id(self):
        return "12345"


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
        self.fail_json = False
        self.fail_text = False

    async def send_json(self, payload):
        if self.fail_json:
            raise RuntimeError("WebSocket is not connected. Need to call 'accept' first.")
        self.json_messages.append(payload)

    async def send_text(self, payload):
        if self.fail_text:
            raise RuntimeError("WebSocket is not connected. Need to call 'accept' first.")
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
    assert spec.fallback_proxy_url == "http://env-proxy:9090"
    assert spec.fallback_proxy_source == "env"
    assert spec.start_url == "https://m.facebook.com/me/?v=timeline"
    assert spec.fallback_start_urls == [
        "https://m.facebook.com/me/",
        "https://www.facebook.com/",
        "https://m.facebook.com/",
        "https://m.facebook.com/profile.php?id=12345&v=timeline",
        "https://m.facebook.com/profile.php?id=12345",
        "https://www.facebook.com/profile.php?id=12345",
    ]
    assert spec.wait_until == "commit"
    assert spec.user_agent == "facebook-agent"


def test_resolve_remote_proxy_plan_falls_back_to_env(monkeypatch):
    monkeypatch.setattr(remote_lease_service, "get_system_proxy", lambda: "http://env-proxy:9090")

    primary_url, primary_source, fallback_url, fallback_source = remote_lease_service._resolve_remote_proxy_plan(
        "http://session-proxy:8080"
    )

    assert primary_url == "http://session-proxy:8080"
    assert primary_source == "session"
    assert fallback_url == "http://env-proxy:9090"
    assert fallback_source == "env"


def test_resolve_remote_session_spec_uses_reddit_identity_and_env_proxy(monkeypatch):
    monkeypatch.setattr(remote_lease_service, "RedditSession", _FakeRedditSession)
    monkeypatch.setattr(remote_lease_service, "get_system_proxy", lambda: "http://env-proxy:9090")

    spec = remote_lease_service._resolve_remote_session_spec("reddit_neera_allvere", "reddit")

    assert spec.platform == "reddit"
    assert spec.proxy_url == "http://env-proxy:9090"
    assert spec.proxy_source == "env"
    assert spec.fallback_proxy_url is None
    assert spec.fallback_proxy_source is None
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


def test_page_renderable_document_rejects_loading_shell():
    async def _exercise():
        service = remote_lease_service.RemoteLeaseService()
        lease = remote_lease_service.RemoteLease(
            service=service,
            lease_id="lease-alpha",
            session_id="alpha",
            platform="facebook",
            controller_user="alice",
        )
        lease._action_worker_task.cancel()
        try:
            await lease._action_worker_task
        except asyncio.CancelledError:
            pass

        assert lease._page_has_renderable_document(
            {
                "readyState": "loading",
                "bodyTextLength": 0,
                "htmlLength": 1851,
                "title": "",
            }
        ) is False
        assert lease._page_has_renderable_document(
            {
                "readyState": "interactive",
                "bodyTextLength": 0,
                "htmlLength": 4096,
                "title": "",
            }
        ) is True
        assert lease._page_has_renderable_document(
            {
                "readyState": "loading",
                "bodyTextLength": 0,
                "htmlLength": 10,
                "title": "Facebook",
            }
        ) is True

    _run(_exercise())


def test_attach_reuses_same_profile_as_observer_and_persists_logs(isolated_remote_environment, monkeypatch):
    pm, _leases_dir = isolated_remote_environment
    monkeypatch.setattr(remote_lease_service.RemoteLease, "ensure_browser_ready", _async_noop)
    monkeypatch.setattr(remote_lease_service.RemoteLease, "refresh_title", _async_noop)
    monkeypatch.setattr(remote_lease_service.RemoteLease, "_start_frame_stream", _async_noop)
    monkeypatch.setattr(remote_lease_service.RemoteLease, "_stop_frame_stream", _async_noop)
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


def test_action_result_prunes_disconnected_viewer(isolated_remote_environment, monkeypatch):
    monkeypatch.setattr(remote_lease_service.RemoteLease, "ensure_browser_ready", _async_noop)
    monkeypatch.setattr(remote_lease_service.RemoteLease, "refresh_title", _async_noop)
    monkeypatch.setattr(remote_lease_service.RemoteLease, "_start_frame_stream", _async_noop)
    monkeypatch.setattr(remote_lease_service.RemoteLease, "_stop_frame_stream", _async_noop)
    monkeypatch.setattr(remote_lease_service.RemoteLease, "_capture_bootstrap_frame", _async_noop)
    monkeypatch.setattr(remote_lease_service.RemoteLease, "_schedule_idle_close", lambda self: None)
    monkeypatch.setattr(remote_lease_service.RemoteLease, "_teardown_browser", _async_noop)

    service = remote_lease_service.RemoteLeaseService()
    ws = _FakeWebSocket()
    lease, viewer = _run(
        service.attach(
            websocket=ws,
            session_id="alpha",
            platform="facebook",
            username="alice",
        )
    )

    ws.fail_json = True
    sent = _run(
        lease._send_action_result(
            viewer,
            action_id="action-1",
            result={"success": True, "action": "tap"},
        )
    )

    assert sent is False
    assert lease.viewer_count == 0
    assert service.get_viewer(ws) is None

    _run(service.close_lease(lease, reason="test_done"))


def test_takeover_prunes_dead_observer_and_keeps_live_controller(isolated_remote_environment, monkeypatch):
    monkeypatch.setattr(remote_lease_service.RemoteLease, "ensure_browser_ready", _async_noop)
    monkeypatch.setattr(remote_lease_service.RemoteLease, "refresh_title", _async_noop)
    monkeypatch.setattr(remote_lease_service.RemoteLease, "_start_frame_stream", _async_noop)
    monkeypatch.setattr(remote_lease_service.RemoteLease, "_stop_frame_stream", _async_noop)
    monkeypatch.setattr(remote_lease_service.RemoteLease, "_capture_bootstrap_frame", _async_noop)
    monkeypatch.setattr(remote_lease_service.RemoteLease, "_schedule_idle_close", lambda self: None)
    monkeypatch.setattr(remote_lease_service.RemoteLease, "_teardown_browser", _async_noop)

    service = remote_lease_service.RemoteLeaseService()
    ws_controller = _FakeWebSocket()
    ws_observer = _FakeWebSocket()

    lease, _controller = _run(
        service.attach(
            websocket=ws_controller,
            session_id="alpha",
            platform="facebook",
            username="alice",
        )
    )
    _run(
        service.attach(
            websocket=ws_observer,
            session_id="alpha",
            platform="facebook",
            username="bob",
        )
    )

    ws_observer.fail_json = True
    takeover = _run(service.handle_takeover(lease=lease, username="bob"))

    assert takeover["controller_user"] == "bob"
    assert lease.viewer_count == 1
    assert service.get_viewer(ws_observer) is None
    live_viewer = service.get_viewer(ws_controller)
    assert live_viewer is not None
    assert live_viewer.role == "observer"

    _run(service.close_lease(lease, reason="test_done"))


def test_close_cancels_inflight_startup(isolated_remote_environment, monkeypatch):
    pm, _leases_dir = isolated_remote_environment
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def _exercise():
        async def _slow_start(self, *, reason):
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        monkeypatch.setattr(remote_lease_service.RemoteLease, "_start_browser", _slow_start)
        monkeypatch.setattr(remote_lease_service.RemoteLease, "_teardown_browser", _async_noop)

        service = remote_lease_service.RemoteLeaseService()
        lease = await service._get_or_create_lease(session_id="alpha", platform="facebook", username="alice")

        startup_task = asyncio.create_task(lease.ensure_browser_ready(reason="attach"))
        await asyncio.wait_for(started.wait(), timeout=1)

        await service.close_lease(lease, reason="manual_stop")

        assert cancelled.is_set()
        assert lease.closed_at is not None
        assert lease.status == "closed"
        assert pm.get_reservation("alpha") is None

        with pytest.raises(asyncio.CancelledError):
            await startup_task

    _run(_exercise())
