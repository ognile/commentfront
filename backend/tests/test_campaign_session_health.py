import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import comment_bot
import main
import profile_manager


class _FakeLocator:
    def __init__(self, count_value=0, text_value=""):
        self._count_value = count_value
        self._text_value = text_value

    async def count(self):
        return self._count_value

    async def inner_text(self, timeout=None):
        return self._text_value


class _FakePage:
    def __init__(self, *, url: str, body_text: str, selector_counts=None):
        self.url = url
        self._body_text = body_text
        self._selector_counts = selector_counts or {}

    def locator(self, selector: str):
        if selector == "body":
            return _FakeLocator(1, self._body_text)
        return _FakeLocator(self._selector_counts.get(selector, 0), self._body_text)

    async def text_content(self, selector: str):
        if selector == "body":
            return self._body_text
        return ""

    def get_by_text(self, text: str):
        body = self._body_text.lower()
        return _FakeLocator(1 if text.lower() in body else 0, self._body_text)


def _run(coro):
    return asyncio.run(coro)


def _write_session(sessions_dir: Path, profile_name: str):
    payload = {
        "profile_name": profile_name,
        "display_name": profile_name.replace("_", " ").title(),
        "cookies": [{"name": "c_user", "value": "1"}, {"name": "xs", "value": "2"}],
    }
    (sessions_dir / f"{profile_name}.json").write_text(json.dumps(payload))


@pytest.fixture
def isolated_profile_manager(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    state_file = tmp_path / "profile_state.json"

    old_singleton = profile_manager._profile_manager
    pm = profile_manager.ProfileManager(state_file=str(state_file), sessions_dir=str(sessions_dir))
    profile_manager._profile_manager = pm
    monkeypatch.setattr(profile_manager, "get_profile_manager", lambda: pm)

    yield pm, sessions_dir

    profile_manager._profile_manager = old_singleton


def test_verify_post_loaded_rejects_login_shell_even_with_post_url():
    page = _FakePage(
        url="https://m.facebook.com/story.php?story_fbid=123&id=456",
        body_text="open app log in forgot password",
    )

    assert _run(comment_bot.verify_post_loaded(page)) is False


def test_verify_post_loaded_rejects_checkpoint_shell():
    page = _FakePage(
        url="https://m.facebook.com/checkpoint/",
        body_text="confirm your identity to continue",
    )

    assert _run(comment_bot.verify_post_loaded(page)) is False


def test_recent_performance_lock_ignores_infra_failures(isolated_profile_manager):
    pm, sessions_dir = isolated_profile_manager
    _write_session(sessions_dir, "healthy-ish")
    _write_session(sessions_dir, "dead-profile")
    pm.refresh_from_sessions()

    for _ in range(5):
        pm.mark_profile_used("healthy-ish", success=False, failure_type="infrastructure")
        pm.mark_profile_used("dead-profile", success=False, failure_type="facebook_error")

    assert pm.is_recent_performance_locked("healthy-ish") is False
    assert pm.is_recent_performance_locked("dead-profile") is True

    eligible = pm.get_eligible_profiles(count=10, sessions=[
        {"profile_name": "healthy-ish", "has_valid_cookies": True, "tags": []},
        {"profile_name": "dead-profile", "has_valid_cookies": True, "tags": []},
    ])
    assert "healthy-ish" in eligible
    assert "dead-profile" not in eligible


def test_select_live_profile_reselects_after_reservation_loss(monkeypatch):
    qp = main.QueueProcessor(main.queue_manager)

    class _FakeProfileManager:
        def __init__(self):
            self.reserve_calls = []

        def get_eligible_profiles(self, filter_tags=None, count=1, exclude_profiles=None):
            exclude = set(exclude_profiles or [])
            ordered = ["busy-profile", "healthy-profile"]
            return [name for name in ordered if name not in exclude][:count]

        async def reserve_profile(self, profile_name, source=None, owner=None, metadata=None):
            self.reserve_calls.append((profile_name, source, owner))
            return profile_name != "busy-profile"

        async def release_profile(self, profile_name, source=None, owner=None):
            return True

    fake_pm = _FakeProfileManager()

    async def _fake_test_and_repair_session(**kwargs):
        profile_name = kwargs["profile_name"]
        if profile_name == "healthy-profile":
            return {
                "ok": True,
                "profile_name": profile_name,
                "session": object(),
                "linked_credential": None,
                "health_status": "healthy",
                "health_reason": "ok",
            }
        return {
            "ok": False,
            "profile_name": profile_name,
            "session": None,
            "linked_credential": None,
            "health_status": "needs_attention",
            "health_reason": "bad",
        }

    monkeypatch.setattr(qp, "_test_and_repair_session", _fake_test_and_repair_session)

    selection = _run(
        qp._select_live_profile(
            profile_manager=fake_pm,
            filter_tags=[],
            exclude_profiles=set(),
            reservation_source="campaign_job",
            reservation_owner="owner-1",
            reservation_metadata={"campaign_id": "c1", "job_index": 0},
        )
    )

    assert selection is not None
    assert selection["profile_name"] == "healthy-profile"
    assert [call[0] for call in fake_pm.reserve_calls] == ["busy-profile", "healthy-profile"]
