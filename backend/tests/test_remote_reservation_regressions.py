import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
import profile_manager


def _run(coro):
    import asyncio

    return asyncio.run(coro)


class _FakeFacebookSession:
    def __init__(self, profile_name: str):
        self.profile_name = profile_name

    def load(self):
        return True

    def get_proxy(self):
        return None


class _FakeRedditSession:
    def __init__(self, profile_name: str):
        self.profile_name = profile_name

    def load(self):
        return True


@pytest.fixture
def isolated_profile_manager(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    state_file = tmp_path / "profile_state.json"

    old_profile_manager = profile_manager._profile_manager
    pm = profile_manager.ProfileManager(state_file=str(state_file), sessions_dir=str(sessions_dir))
    profile_manager._profile_manager = pm
    monkeypatch.setattr(profile_manager, "get_profile_manager", lambda: pm)

    yield pm

    profile_manager._profile_manager = old_profile_manager


def test_get_sessions_includes_reservation_metadata(isolated_profile_manager, monkeypatch):
    pm = isolated_profile_manager
    _run(
        pm.reserve_profile(
            "alpha",
            source="remote_lease",
            owner="lease_alpha",
            metadata={"platform": "facebook", "controller_user": "alice"},
        )
    )
    monkeypatch.setattr(
        main,
        "list_saved_sessions",
        lambda: [
            {
                "file": "alpha.json",
                "profile_name": "alpha",
                "display_name": "Alpha",
                "user_id": "1",
                "extracted_at": "2026-03-12T00:00:00Z",
                "has_valid_cookies": True,
                "profile_picture": None,
                "tags": ["warm"],
            }
        ],
    )
    monkeypatch.setattr(main, "FacebookSession", _FakeFacebookSession)
    monkeypatch.setattr(main, "PROXY_URL", None)

    sessions = _run(main.get_sessions(current_user={"username": "tester"}))
    payload = sessions[0].model_dump()

    assert payload["is_reserved"] is True
    assert payload["reservation_source"] == "remote_lease"
    assert payload["reservation_owner"] == "lease_alpha"
    assert payload["reservation_controller_user"] == "alice"
    assert payload["reservation_platform"] == "facebook"


def test_get_reddit_sessions_includes_reservation_metadata(isolated_profile_manager, monkeypatch):
    pm = isolated_profile_manager
    _run(
        pm.reserve_profile(
            "reddit_alpha",
            source="remote_lease",
            owner="lease_reddit_alpha",
            metadata={"platform": "reddit", "controller_user": "bob"},
        )
    )
    monkeypatch.setattr(
        main,
        "list_saved_reddit_sessions",
        lambda: [
            {
                "file": "reddit_alpha.json",
                "profile_name": "reddit_alpha",
                "display_name": "Reddit Alpha",
                "username": "bob",
                "email": "bob@example.com",
                "profile_url": "https://reddit.com/u/bob",
                "extracted_at": "2026-03-12T00:00:00Z",
                "has_valid_session": True,
                "proxy": None,
                "tags": [],
                "fixture": False,
                "linked_credential_id": None,
                "warmup_state": {},
            }
        ],
    )
    monkeypatch.setattr(main, "get_system_proxy", lambda: None)

    sessions = _run(main.get_reddit_sessions(current_user={"username": "tester"}))
    payload = sessions[0].model_dump()

    assert payload["is_reserved"] is True
    assert payload["reservation_source"] == "remote_lease"
    assert payload["reservation_controller_user"] == "bob"
    assert payload["reservation_platform"] == "reddit"


def test_refresh_profile_name_returns_409_when_profile_is_reserved(isolated_profile_manager, monkeypatch):
    pm = isolated_profile_manager
    _run(
        pm.reserve_profile(
            "alpha",
            source="remote_lease",
            owner="lease_alpha",
            metadata={"platform": "facebook", "controller_user": "alice"},
        )
    )

    async def _unexpected_refresh(_profile_name: str):
        raise AssertionError("refresh should not run for a reserved profile")

    monkeypatch.setattr(main, "refresh_session_profile_name", _unexpected_refresh)

    with pytest.raises(HTTPException) as exc:
        _run(main.refresh_profile_name("alpha", current_user={"username": "tester"}))

    assert exc.value.status_code == 409
    assert exc.value.detail["operation"] == "refresh_profile_name"
    assert exc.value.detail["reservation"]["source"] == "remote_lease"


def test_facebook_session_test_releases_operation_reservation(isolated_profile_manager, monkeypatch):
    pm = isolated_profile_manager
    monkeypatch.setattr(main, "FacebookSession", _FakeFacebookSession)

    async def _fake_test_session(session, _proxy):
        reservation = pm.get_reservation(session.profile_name)
        assert reservation is not None
        assert reservation["metadata"]["operation"] == "facebook_session_test"
        return {"success": True}

    monkeypatch.setattr(main, "test_session", _fake_test_session)

    result = _run(main.test_session_endpoint("alpha", current_user={"username": "tester"}))

    assert result["success"] is True
    assert pm.get_reservation("alpha") is None


def test_reddit_session_test_releases_operation_reservation(isolated_profile_manager, monkeypatch):
    pm = isolated_profile_manager
    monkeypatch.setattr(main, "RedditSession", _FakeRedditSession)
    monkeypatch.setattr(main, "_resolve_effective_proxy", lambda *_args, **_kwargs: "http://env-proxy:9090")

    async def _fake_test_reddit_session(session, _proxy):
        reservation = pm.get_reservation(session.profile_name)
        assert reservation is not None
        assert reservation["metadata"]["operation"] == "reddit_session_test"
        return {"success": True}

    monkeypatch.setattr(main, "test_reddit_session", _fake_test_reddit_session)

    result = _run(main.test_reddit_session_endpoint("reddit_alpha", current_user={"username": "tester"}))

    assert result["success"] is True
    assert pm.get_reservation("reddit_alpha") is None
