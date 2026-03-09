import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reddit_program_orchestrator import RedditProgramOrchestrator, RedditProgramScheduler
from reddit_program_store import RedditProgramStore
from reddit_session import RedditSession


def _spec(**overrides):
    spec = {
        "profile_selection": {"profile_names": ["reddit_amy"]},
        "schedule": {
            "start_at": "2026-03-09T08:00:00Z",
            "duration_days": 1,
            "timezone": "Europe/Zurich",
            "random_windows": [{"start_hour": 8, "end_hour": 9}],
        },
        "topic_constraints": {"subreddits": ["womenshealth"], "keywords": ["biopsy"]},
        "content_assignments": {
            "items": [
                {
                    "id": "comment-1",
                    "action": "comment_post",
                    "profile_name": "reddit_amy",
                    "day_offset": 0,
                    "target_url": "https://www.reddit.com/r/womenshealth/comments/abc123/endometrial_biopsy/",
                    "text": "this is an exact supportive comment",
                }
            ]
        },
        "engagement_quotas": {
            "upvotes_per_day": 1,
            "reply_min_per_day": 1,
            "reply_max_per_day": 1,
            "random_reply_templates": ["that sounds painful, i hope recovery goes smoothly"],
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
            "cooldown_minutes": 0,
            "max_actions_per_tick": 5,
            "max_discovery_posts_per_subreddit": 4,
            "max_comment_candidates_per_post": 4,
            "retry_delay_minutes": 5,
            "max_attempts_per_item": 2,
        },
        "metadata": {},
    }
    spec.update(overrides)
    return spec


def _success_result(action, **extra):
    return {
        "success": True,
        "action": action,
        "attempt_id": f"attempt-{action}",
        "trace_id": f"trace-{action}",
        "final_verdict": "success_confirmed",
        "evidence_summary": f"{action} confirmed",
        "current_url": extra.get("current_url"),
    }


def test_discovery_stays_inside_allowed_subreddit_and_keywords(tmp_path, monkeypatch):
    store = RedditProgramStore(file_path=str(tmp_path / "programs.json"))
    program = store.create_program(_spec())
    orchestrator = RedditProgramOrchestrator(store=store)
    item = next(entry for entry in program["compiled"]["work_items"] if entry["action"] == "upvote_post")

    async def fake_fetch_json(url):
        assert "r/womenshealth" in url
        return {
            "data": {
                "children": [
                    {"data": {"id": "good", "permalink": "/r/womenshealth/comments/good/endometrial_biopsy_update/", "title": "endometrial biopsy update", "selftext": "pain and recovery", "score": 55, "num_comments": 12}},
                    {"data": {"id": "bad", "permalink": "/r/womenshealth/comments/bad/unrelated_topic/", "title": "unrelated gardening", "selftext": "", "score": 999, "num_comments": 99}},
                ]
            }
        }

    monkeypatch.setattr(orchestrator, "_fetch_json", fake_fetch_json)
    candidate = asyncio.run(orchestrator._discover_post_target(program, item))

    assert candidate is not None
    assert candidate["target_url"].endswith("/good/endometrial_biopsy_update/")


def test_orchestrator_marks_completed_and_records_target_history(tmp_path, monkeypatch):
    store = RedditProgramStore(file_path=str(tmp_path / "programs.json"))
    program = store.create_program(_spec())

    async def fake_runner(session, *, action, url=None, target_comment_url=None, text=None, **_kwargs):
        return _success_result(action, current_url=url or target_comment_url)

    monkeypatch.setattr(RedditSession, "load", lambda self: {"profile_name": self.profile_name})
    orchestrator = RedditProgramOrchestrator(store=store, action_runner=fake_runner)

    async def fake_fetch_json(url):
        if "search/.json" in url or "hot/.json" in url:
            return {
                "data": {
                    "children": [
                        {"data": {"id": "goodpost", "permalink": "/r/womenshealth/comments/goodpost/endometrial_biopsy_update/", "title": "endometrial biopsy update", "selftext": "still hurting", "score": 50, "num_comments": 10}}
                    ]
                }
            }
        return [
            {},
            {
                "data": {
                    "children": [
                        {
                            "kind": "t1",
                            "data": {
                                "id": "comment1",
                                "body": "biopsy recovery is rough",
                                "author": "someone",
                                "permalink": "/r/womenshealth/comments/goodpost/endometrial_biopsy_update/comment/comment1/",
                                "score": 8,
                            },
                        }
                    ]
                }
            },
        ]

    monkeypatch.setattr(orchestrator, "_fetch_json", fake_fetch_json)
    result = asyncio.run(orchestrator.process_program(program["id"]))
    updated = store.get_program(program["id"])

    assert result["processed"] == 3
    assert updated["status"] == "completed"
    assert not updated["remaining_contract"]
    assert len(updated["target_history"]) == 3


def test_verification_contract_blocks_false_success(tmp_path, monkeypatch):
    store = RedditProgramStore(file_path=str(tmp_path / "programs.json"))
    program = store.create_program(_spec(engagement_quotas={"upvotes_per_day": 0, "reply_min_per_day": 0, "reply_max_per_day": 0, "random_reply_templates": [], "random_upvote_action": "upvote_post"}))

    async def fake_runner(session, *, action, url=None, **_kwargs):
        result = _success_result(action, current_url=url)
        result["final_verdict"] = "needs_review"
        return result

    monkeypatch.setattr(RedditSession, "load", lambda self: {"profile_name": self.profile_name})
    orchestrator = RedditProgramOrchestrator(store=store, action_runner=fake_runner)
    asyncio.run(orchestrator.process_program(program["id"]))
    updated = store.get_program(program["id"])
    item = updated["compiled"]["work_items"][0]

    assert item["status"] == "pending"
    assert "success_confirmed" in item["error"]
    assert updated["remaining_contract"]["comment_post"] == 1


def test_target_history_prevents_same_target_reuse_same_profile_day(tmp_path):
    store = RedditProgramStore(file_path=str(tmp_path / "programs.json"))
    program = store.create_program(_spec())
    orchestrator = RedditProgramOrchestrator(store=store)
    program["target_history"] = [
        {
            "profile_name": "reddit_amy",
            "local_date": "2026-03-09",
            "target_ref": "https://www.reddit.com/r/womenshealth/comments/goodpost/endometrial_biopsy_update/",
        }
    ]

    assert orchestrator._target_already_used(
        program,
        profile_name="reddit_amy",
        local_date="2026-03-09",
        target_ref="https://www.reddit.com/r/womenshealth/comments/goodpost/endometrial_biopsy_update/",
    )


def test_scheduler_start_recovers_interrupted_programs(tmp_path):
    store = RedditProgramStore(file_path=str(tmp_path / "programs.json"))
    program = store.create_program(_spec())
    program["status"] = "running"
    program["compiled"]["work_items"][0]["status"] = "running"
    store.save_program(program)

    orchestrator = RedditProgramOrchestrator(store=store)
    scheduler = RedditProgramScheduler(store=store, orchestrator=orchestrator)

    async def run():
        await scheduler.start()
        recovered = store.get_program(program["id"])
        await scheduler.stop()
        return recovered

    recovered = asyncio.run(run())
    assert recovered["status"] == "active"
    assert recovered["compiled"]["work_items"][0]["status"] == "pending"


def test_orchestrator_blocks_community_restricted_items(tmp_path, monkeypatch):
    store = RedditProgramStore(file_path=str(tmp_path / "programs.json"))
    program = store.create_program(
        _spec(
            engagement_quotas={
                "upvotes_per_day": 0,
                "reply_min_per_day": 0,
                "reply_max_per_day": 0,
                "random_reply_templates": [],
                "random_upvote_action": "upvote_post",
            }
        )
    )

    async def fake_runner(session, *, action, url=None, **_kwargs):
        return {
            "success": False,
            "action": action,
            "error": "reddit community ban: can't comment on posts",
            "failure_class": "community_restricted",
            "attempt_id": "attempt-ban",
            "trace_id": "trace-ban",
            "final_verdict": "failed_confirmed",
            "evidence_summary": "community ban detected",
            "current_url": url,
        }

    monkeypatch.setattr(RedditSession, "load", lambda self: {"profile_name": self.profile_name})
    orchestrator = RedditProgramOrchestrator(store=store, action_runner=fake_runner)

    asyncio.run(orchestrator.process_program(program["id"]))
    updated = store.get_program(program["id"])
    item = updated["compiled"]["work_items"][0]

    assert item["status"] == "blocked"
    assert item["error"] == "reddit community ban: can't comment on posts"
