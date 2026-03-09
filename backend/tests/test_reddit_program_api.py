import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
from reddit_mission_store import RedditMissionStore
from reddit_program_store import RedditProgramStore


def test_create_reddit_mission_persists_target_comment_url(tmp_path, monkeypatch):
    temp_store = RedditMissionStore(file_path=str(tmp_path / "reddit_missions.json"))
    monkeypatch.setattr(main, "reddit_mission_store", temp_store)

    request = main.RedditMissionCreateRequest(
        profile_name="reddit_amy",
        action="reply_comment",
        target_comment_url="https://www.reddit.com/r/womenshealth/comments/abc123/endometrial_biopsy/comment/xyz789/",
        exact_text="this is the reply",
        cadence=main.RedditMissionCadence(type="once"),
    )

    asyncio.run(main.create_reddit_mission(request=request, current_user={"username": "tester"}))
    stored = temp_store.list_missions()[0]

    assert stored["target_comment_url"].endswith("/comment/xyz789/")


def test_reddit_program_status_exposes_join_and_notification_fields(tmp_path, monkeypatch):
    temp_store = RedditProgramStore(file_path=str(tmp_path / "reddit_programs.json"))
    monkeypatch.setattr(main, "reddit_program_store", temp_store)

    program = temp_store.create_program(
        {
            "profile_selection": {"profile_names": ["reddit_amy"]},
            "schedule": {"start_at": "2026-03-09T08:00:00Z", "duration_days": 1, "timezone": "Europe/Zurich", "random_windows": [{"start_hour": 8, "end_hour": 9}]},
            "topic_constraints": {
                "subreddits": ["WomensHealth"],
                "keywords": ["biopsy"],
                "mandatory_join_urls": ["https://www.reddit.com/r/WomensHealth/"],
            },
            "content_assignments": {"items": []},
            "engagement_quotas": {
                "posts_min_per_day": 1,
                "posts_max_per_day": 1,
                "upvotes_min_per_day": 1,
                "upvotes_max_per_day": 1,
                "comment_upvote_min_per_day": 0,
                "comment_upvote_max_per_day": 0,
                "reply_min_per_day": 1,
                "reply_max_per_day": 1,
                "random_reply_templates": [],
                "random_upvote_action": "upvote_post",
            },
            "generation_config": {
                "style_sample_count": 3,
                "writing_rule_paths": [],
                "uniqueness_scope": "program",
            },
            "notification_config": {
                "email_enabled": True,
                "email_account_mode": "default_gog_account",
                "daily_summary_hour": 20,
                "hard_failure_alerts_enabled": True,
                "recipient_email": "nikitalienov@gmail.com",
            },
            "verification_contract": {
                "require_success_confirmed": True,
                "require_attempt_id": True,
                "required_evidence_summary": True,
                "required_target_reference": True,
            },
            "execution_policy": {
                "strict_quotas": True,
                "allow_target_reuse_within_day": False,
                "cooldown_minutes": 0,
                "max_actions_per_tick": 5,
                "max_discovery_posts_per_subreddit": 4,
                "max_comment_candidates_per_post": 4,
                "retry_delay_minutes": 5,
                "max_attempts_per_item": 2,
            },
            "metadata": {},
        }
    )

    response = asyncio.run(main.get_reddit_program_status(program_id=program["id"], current_user={"username": "tester"}))

    assert "join_progress_matrix" in response
    assert "notification_log" in response
    assert "contract_totals" in response
