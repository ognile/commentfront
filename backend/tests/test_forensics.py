import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
from forensics import build_comment_verdict, has_direct_active_restriction_proof
from profile_manager import ProfileManager


def test_build_comment_verdict_marks_inconclusive_as_success_inconclusive():
    verdict = build_comment_verdict(
        {
            "success": False,
            "method": "verification_inconclusive",
            "error": "Step 5 INCONCLUSIVE - Comment submission evidence is strong but visual confirmation failed",
            "submission_evidence": {"local_comment_text_seen": True},
            "steps_completed": ["submit_clicked"],
        }
    )

    assert verdict.final_verdict == "success_inconclusive"
    assert "local_dom_evidence" in verdict.winning_evidence


def test_historical_restriction_reason_never_counts_as_direct_active_proof():
    assert has_direct_active_restriction_proof("You couldn't comment (Ended on Jan 21, 2026)") is False

    verdict = build_comment_verdict(
        {
            "success": False,
            "throttled": True,
            "throttle_reason": "You couldn't comment (Ended on Jan 21, 2026)",
            "error": "comment failed",
            "steps_completed": ["comments_opened"],
        }
    )

    assert verdict.final_verdict == "restriction_suspected"


def test_profile_manager_marks_suspected_restriction_as_cooldown(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    (sessions_dir / "alice.json").write_text(
        json.dumps(
            {
                "profile_name": "alice",
                "cookies": [{"name": "c_user", "value": "1"}, {"name": "xs", "value": "2"}],
                "has_valid_cookies": True,
            }
        )
    )

    pm = ProfileManager(state_file=str(tmp_path / "profile_state.json"), sessions_dir=str(sessions_dir))
    pm.refresh_from_sessions()
    pm.mark_profile_restriction_suspected("alice", reason="You couldn't comment", attempt_id="attempt-1")

    analytics = pm.get_profile_analytics("alice")
    assert analytics["status"] == "cooldown"
    assert analytics["suspected_restriction_attempt_id"] == "attempt-1"


def test_forensics_attempts_endpoint_returns_rows(monkeypatch):
    async def fake_list_forensic_attempts(*, filters=None, limit=50):
        assert filters == {"campaign_id": "camp_1"}
        assert limit == 10
        return [{"attempt_id": "a1", "campaign_id": "camp_1"}]

    monkeypatch.setattr(main, "list_forensic_attempts", fake_list_forensic_attempts)

    result = asyncio.run(
        main.get_forensics_attempts(
            campaign_id="camp_1",
            platform=None,
            engine=None,
            profile_name=None,
            run_id=None,
            final_verdict=None,
            limit=10,
            current_user={"username": "tester"},
        )
    )

    assert result == {"count": 1, "attempts": [{"attempt_id": "a1", "campaign_id": "camp_1"}]}


def test_forensics_attempt_detail_endpoint_returns_timeline(monkeypatch):
    async def fake_get_forensic_attempt_detail(attempt_id: str):
        assert attempt_id == "attempt_1"
        return {
            "attempt": {"attempt_id": attempt_id},
            "events": [{"event_type": "navigate"}],
            "artifacts": [{"artifact_id": "art_1"}],
            "verdict": {"final_verdict": "success_confirmed"},
            "links": {"children": [], "parents": []},
        }

    monkeypatch.setattr(main, "get_forensic_attempt_detail", fake_get_forensic_attempt_detail)

    result = asyncio.run(main.get_forensics_attempt_timeline("attempt_1", current_user={"username": "tester"}))
    assert result["attempt"]["attempt_id"] == "attempt_1"
    assert result["timeline"][0]["event_type"] == "navigate"
