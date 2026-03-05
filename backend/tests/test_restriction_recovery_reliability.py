import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import appeal_manager
import appeal_scheduler
import fb_session
import main
import profile_manager


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


def test_reset_exhausted_appeal_state_does_not_increment_attempts(isolated_profile_manager):
    pm, sessions_dir = isolated_profile_manager
    _write_session(sessions_dir, "alice")
    pm.refresh_from_sessions()

    pm.mark_profile_restricted("alice", reason="test restriction")
    pm.update_appeal_state("alice", "task_failed", error="one", max_attempts=3)
    pm.update_appeal_state("alice", "task_failed", error="two", max_attempts=3)
    pm.update_appeal_state("alice", "task_failed", error="three", max_attempts=3)

    before = pm.get_profile_state("alice")
    assert before["appeal_status"] == "exhausted"
    assert before["appeal_attempts"] == 3

    pm.reset_appeal_state("alice", reason="batch_retry_window_reset")
    after = pm.get_profile_state("alice")

    assert after["appeal_status"] == "none"
    assert after["appeal_attempts"] == 0
    assert after["appeal_last_result"] is None
    assert after["recovery_last_event"] == "appeal_reset"


def test_analytics_endpoint_keeps_zero_usage_and_manual_unblock_visible(isolated_profile_manager, monkeypatch):
    pm, sessions_dir = isolated_profile_manager
    _write_session(sessions_dir, "alice")
    _write_session(sessions_dir, "bob")
    pm.refresh_from_sessions()

    pm.mark_profile_used("alice", success=True)
    pm.mark_profile_used("bob", success=False, failure_type="restriction")
    pm.mark_profile_restricted("bob", reason="manual")

    monkeypatch.setattr(
        fb_session,
        "list_saved_sessions",
        lambda: [
            {"profile_name": "alice", "display_name": "Alice", "has_valid_cookies": True, "tags": []},
            {"profile_name": "bob", "display_name": "Bob", "has_valid_cookies": True, "tags": []},
        ],
    )

    profiles_before = asyncio.run(main.get_all_profile_analytics(current_user={"username": "tester"}))["profiles"]
    assert {profile["profile_name"] for profile in profiles_before} == {"alice", "bob"}
    assert next(profile for profile in profiles_before if profile["profile_name"] == "bob")["display_name"] == "Bob"

    usage_before = pm.get_profile_analytics("bob")["usage_count"]
    asyncio.run(main.unblock_profile("bob", current_user={"username": "tester"}))

    usage_after = pm.get_profile_analytics("bob")["usage_count"]
    profiles_after = asyncio.run(main.get_all_profile_analytics(current_user={"username": "tester"}))["profiles"]
    bob_after = next(profile for profile in profiles_after if profile["profile_name"] == "bob")

    assert usage_after == usage_before
    assert bob_after["status"] == "active"
    assert bob_after["recovery_last_event"] == "manual_unblock"


def test_verify_all_counts_followup_and_busy_skips(isolated_profile_manager, monkeypatch):
    pm, sessions_dir = isolated_profile_manager
    for name in ("auto", "review", "blocked", "followup", "busy"):
        _write_session(sessions_dir, name)
    pm.refresh_from_sessions()
    for name in ("auto", "review", "blocked", "followup", "busy"):
        pm.mark_profile_restricted(name, reason=f"{name} restricted")
    asyncio.run(pm.reserve_profile("busy"))

    async def fake_verify(profile_name: str):
        mapping = {
            "auto": {"profile_name": "auto", "verified_status": "RESOLVED", "action_taken": "auto_unblocked", "steps_used": 2},
            "review": {"profile_name": "review", "verified_status": "IN_REVIEW", "action_taken": "marked_in_review", "steps_used": 2},
            "blocked": {"profile_name": "blocked", "verified_status": "ACTIVE", "action_taken": "confirmed_restricted", "steps_used": 2},
            "followup": {"profile_name": "followup", "verified_status": "UNKNOWN", "action_taken": "needs_followup", "steps_used": 2},
        }
        return mapping[profile_name]

    monkeypatch.setattr(appeal_manager, "verify_single_profile", fake_verify)
    summary = asyncio.run(appeal_manager._verify_all_restricted_inner())

    assert summary["total"] == 5
    assert summary["unblocked"] == 1
    assert summary["in_review"] == 1
    assert summary["still_restricted"] == 1
    assert summary["needs_followup"] == 1
    assert summary["busy_skipped"] == 1
    assert any(result["profile_name"] == "busy" and result["action_taken"] == "busy_skipped" for result in summary["results"])


def test_scheduler_keeps_followup_profiles_for_later_retry(isolated_profile_manager, monkeypatch, tmp_path):
    pm, sessions_dir = isolated_profile_manager
    _write_session(sessions_dir, "followup")
    pm.refresh_from_sessions()
    pm.mark_profile_restricted("followup", reason="unclear restriction")

    scheduler = appeal_scheduler.AppealScheduler(state_file=str(tmp_path / "appeal_scheduler.json"))
    monkeypatch.setattr(scheduler, "_get_active_campaign_profiles", lambda: [])

    async def fake_broadcast(*_args, **_kwargs):
        return None

    async def fake_verify_all_restricted(skip_profiles=None):
        return {
            "total": 1,
            "unblocked": 0,
            "in_review": 0,
            "still_restricted": 0,
            "needs_followup": 1,
            "busy_skipped": 0,
            "results": [
                {
                    "profile_name": "followup",
                    "verified_status": "UNKNOWN: comment check inconclusive",
                    "action_taken": "needs_followup",
                }
            ],
        }

    appeal_calls = {"count": 0}

    async def fake_batch_appeal_all(*_args, **_kwargs):
        appeal_calls["count"] += 1
        return {"successful": 0, "failed": 0, "results": [], "total_attempts": 0}

    monkeypatch.setattr(appeal_manager, "verify_all_restricted", fake_verify_all_restricted)
    monkeypatch.setattr(appeal_manager, "batch_appeal_all", fake_batch_appeal_all)
    monkeypatch.setattr(main, "broadcast_update", fake_broadcast)

    result = asyncio.run(scheduler._run_batch())
    analytics = pm.get_profile_analytics("followup")

    assert appeal_calls["count"] == 0
    assert result["verify_phase"]["needs_followup"] == 1
    assert result["appeal_phase"] == {"total": 0, "succeeded": 0, "failed": 0}
    assert analytics["recovery_last_event"] == "scheduler_followup_queued"
    assert scheduler.get_status()["last_completed_at"] is not None


def test_single_profile_verify_and_appeal_return_409_when_busy(monkeypatch):
    monkeypatch.setattr(appeal_manager, "get_profile_busy_reason", lambda _profile_name: "reserved")

    with pytest.raises(HTTPException) as verify_exc:
        asyncio.run(
            main.verify_single_endpoint(
                main.VerifyProfileRequest(profile_name="busy_profile"),
                current_user={"username": "tester"},
            )
        )

    assert verify_exc.value.status_code == 409
    assert verify_exc.value.detail["status"] == "busy"

    with pytest.raises(HTTPException) as appeal_exc:
        asyncio.run(
            main.appeal_single_endpoint(
                main.AppealSingleRequest(profile_name="busy_profile"),
                current_user={"username": "tester"},
            )
        )

    assert appeal_exc.value.status_code == 409
    assert appeal_exc.value.detail["status"] == "busy"
