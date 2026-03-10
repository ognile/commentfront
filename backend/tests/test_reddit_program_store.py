from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reddit_program_store import RedditProgramStore


def _spec(profile_names=None, **overrides):
    spec = {
        "profile_selection": {"profile_names": profile_names or ["reddit_amy", "reddit_victor"]},
        "schedule": {
            "start_at": "2026-03-09T08:00:00Z",
            "duration_days": 3,
            "timezone": "Europe/Zurich",
            "random_windows": [{"start_hour": 9, "end_hour": 12}],
        },
        "topic_constraints": {"subreddits": ["womenshealth"], "keywords": ["biopsy"]},
        "content_assignments": {
            "items": [
                {
                    "id": "comment-day0",
                    "action": "comment_post",
                    "profile_name": "reddit_amy",
                    "day_offset": 0,
                    "target_url": "https://www.reddit.com/r/womenshealth/comments/abc123/endometrial_biopsy/",
                    "text": "this is a supportive exact comment",
                }
            ]
        },
        "engagement_quotas": {
            "upvotes_per_day": 3,
            "reply_min_per_day": 2,
            "reply_max_per_day": 2,
            "random_reply_templates": ["this sounds rough, i hope you get clear answers soon"],
            "random_upvote_action": "upvote_post",
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
            "cooldown_minutes": 15,
            "max_actions_per_tick": 3,
            "max_discovery_posts_per_subreddit": 6,
            "max_comment_candidates_per_post": 8,
            "retry_delay_minutes": 20,
            "max_attempts_per_item": 5,
        },
        "metadata": {"label": "test program"},
    }
    spec.update(overrides)
    return spec


def test_program_compiler_builds_contractual_work_items(tmp_path):
    store = RedditProgramStore(file_path=str(tmp_path / "reddit_programs.json"))
    profile_names = [f"reddit_{idx}" for idx in range(10)]
    spec = _spec(
        profile_names=profile_names,
        content_assignments={
            "items": [
                {
                    "id": "comment-day0",
                    "action": "comment_post",
                    "profile_name": profile_names[0],
                    "day_offset": 0,
                    "target_url": "https://www.reddit.com/r/womenshealth/comments/abc123/endometrial_biopsy/",
                    "text": "this is a supportive exact comment",
                }
            ]
        },
    )
    program = store.create_program(spec)

    work_items = program["compiled"]["work_items"]
    comment_items = [item for item in work_items if item["action"] == "comment_post"]
    upvote_items = [item for item in work_items if item["action"] == "upvote_post"]
    reply_items = [item for item in work_items if item["action"] == "reply_comment" and item["source"] == "quota_random_reply"]

    assert len(comment_items) == 1
    assert len(upvote_items) == 10 * 3 * 3
    assert len(reply_items) == 10 * 3 * 2
    assert all(item["text"] for item in reply_items)
    assert program["remaining_contract"]["upvote_post"] == len(upvote_items)
    assert program["remaining_contract"]["reply_comment"] == len(reply_items)


def test_recover_interrupted_work_resets_running_items(tmp_path):
    store = RedditProgramStore(file_path=str(tmp_path / "reddit_programs.json"))
    program = store.create_program(_spec())
    program["status"] = "running"
    program["compiled"]["work_items"][0]["status"] = "running"
    store.save_program(program)

    recovered = store.recover_interrupted_work()
    restored = store.get_program(program["id"])

    assert recovered == [program["id"]]
    assert restored["status"] == "active"
    assert restored["compiled"]["work_items"][0]["status"] == "pending"


def test_update_program_rejects_recompile_after_execution_started(tmp_path):
    store = RedditProgramStore(file_path=str(tmp_path / "reddit_programs.json"))
    program = store.create_program(_spec())
    program["compiled"]["work_items"][0]["attempts"] = 1
    store.save_program(program)

    try:
        store.update_program(program["id"], {"schedule": {"duration_days": 5}})
    except ValueError as exc:
        assert "cannot update the program spec after execution has started" in str(exc)
    else:
        raise AssertionError("expected program spec update to be rejected after execution started")


def test_compiler_builds_posts_balanced_upvotes_and_mandatory_joins(tmp_path):
    store = RedditProgramStore(file_path=str(tmp_path / "reddit_programs.json"))
    program = store.create_program(
        _spec(
            profile_names=["reddit_alpha", "reddit_beta"],
            topic_constraints={
                "subreddits": ["WomensHealth", "Healthyhooha"],
                "keywords": ["pcos"],
                "mandatory_join_urls": [
                    "https://www.reddit.com/r/WomensHealth/",
                    "https://www.reddit.com/r/Healthyhooha/",
                ],
            },
            content_assignments={"items": []},
            engagement_quotas={
                "posts_min_per_day": 1,
                "posts_max_per_day": 1,
                "upvotes_min_per_day": 6,
                "upvotes_max_per_day": 6,
                "comment_upvote_min_per_day": 2,
                "comment_upvote_max_per_day": 2,
                "reply_min_per_day": 2,
                "reply_max_per_day": 2,
                "random_reply_templates": [],
                "random_upvote_action": "upvote_post",
            },
        )
    )

    work_items = program["compiled"]["work_items"]

    assert len([item for item in work_items if item["action"] == "create_post"]) == 6
    assert len([item for item in work_items if item["action"] == "reply_comment"]) == 12
    assert len([item for item in work_items if item["action"] == "upvote_comment"]) == 12
    assert len([item for item in work_items if item["action"] == "upvote_post"]) == 24
    assert len([item for item in work_items if item["action"] == "join_subreddit"]) == 4
    assert set(program["join_progress_matrix"].keys()) == {"reddit_alpha", "reddit_beta"}


def test_exhausted_items_still_count_against_remaining_contract(tmp_path):
    store = RedditProgramStore(file_path=str(tmp_path / "reddit_programs.json"))
    program = store.create_program(
        _spec(
            profile_names=["reddit_alpha"],
            schedule={
                "start_at": "2026-03-09T08:00:00Z",
                "duration_days": 1,
                "timezone": "Europe/Zurich",
                "random_windows": [{"start_hour": 9, "end_hour": 12}],
            },
            content_assignments={"items": []},
            engagement_quotas={
                "posts_min_per_day": 0,
                "posts_max_per_day": 0,
                "upvotes_min_per_day": 1,
                "upvotes_max_per_day": 1,
                "comment_upvote_min_per_day": 0,
                "comment_upvote_max_per_day": 0,
                "reply_min_per_day": 0,
                "reply_max_per_day": 0,
                "random_reply_templates": [],
                "random_upvote_action": "upvote_post",
            },
        )
    )
    item = next(entry for entry in program["compiled"]["work_items"] if entry["action"] == "upvote_post")
    item["status"] = "exhausted"
    item["error"] = "verification failed"
    item["result"] = {
        "success": False,
        "error": "verification failed",
        "failure_class": "execution_failed",
        "final_verdict": "failed_confirmed",
    }

    refreshed = store.save_program(program)

    assert refreshed["status"] == "exhausted"
    assert refreshed["remaining_contract"]["upvote_post"] == 1
    assert refreshed["daily_progress"]["2026-03-09"]["reddit_alpha"]["blocked"]["upvote_post"] == 1
