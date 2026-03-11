import asyncio
from datetime import datetime, timezone
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
        current_url = url or target_comment_url
        if action == "create_post":
            current_url = "https://www.reddit.com/r/womenshealth/comments/generated123/still_hurting_after_biopsy/"
        return _success_result(action, current_url=current_url)

    monkeypatch.setattr(RedditSession, "load", lambda self: {"profile_name": self.profile_name})
    monkeypatch.setattr(RedditSession, "get_username", lambda self: "reddit_amy")
    orchestrator = RedditProgramOrchestrator(store=store, action_runner=fake_runner)
    async def fake_discover_post_target(_program, _item, *, actor_username=None):
        return {
            "target_url": "https://www.reddit.com/r/womenshealth/comments/goodpost/endometrial_biopsy_update/",
            "subreddit": "womenshealth",
            "title": "endometrial biopsy update",
            "author": "post_author",
        }

    async def fake_discover_comment_target(_program, _item, *, actor_username=None):
        return {
            "target_comment_url": "https://www.reddit.com/r/womenshealth/comments/goodpost/endometrial_biopsy_update/comment/comment1/",
            "thread_url": "https://www.reddit.com/r/womenshealth/comments/goodpost/endometrial_biopsy_update/",
            "subreddit": "womenshealth",
            "author": "someone",
            "body_excerpt": "biopsy recovery is rough",
            "post_title": "endometrial biopsy update",
        }

    monkeypatch.setattr(orchestrator, "_discover_post_target", fake_discover_post_target)
    monkeypatch.setattr(orchestrator, "_discover_comment_target", fake_discover_comment_target)
    result = asyncio.run(orchestrator.process_program(program["id"]))
    updated = store.get_program(program["id"])

    assert result["processed"] == 3
    assert sum(1 for item in updated["compiled"]["work_items"] if item["status"] == "completed") == 3
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


def test_target_history_prevents_same_target_reuse_across_profiles(tmp_path):
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
        profile_name="reddit_beta",
        local_date="2026-03-09",
        target_ref="https://www.reddit.com/r/womenshealth/comments/goodpost/endometrial_biopsy_update/",
    )


def test_target_reuse_checks_pending_reserved_items(tmp_path):
    store = RedditProgramStore(file_path=str(tmp_path / "programs.json"))
    program = store.create_program(_spec())
    orchestrator = RedditProgramOrchestrator(store=store)
    pending_item = next(entry for entry in program["compiled"]["work_items"] if entry["action"] == "reply_comment")
    pending_item["target_comment_url"] = "https://www.reddit.com/r/womenshealth/comments/post/comment/c1/"
    pending_item["discovered_target"] = {
        "thread_url": "https://www.reddit.com/r/womenshealth/comments/post/",
        "author": "other_user",
        "subreddit": "womenshealth",
    }

    assert orchestrator._target_already_used(
        program,
        profile_name="reddit_other",
        local_date=str(pending_item["local_date"]),
        target_ref="https://www.reddit.com/r/womenshealth/comments/post/comment/c1/",
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


def test_orchestrator_reroutes_generated_post_after_community_restriction(tmp_path, monkeypatch):
    store = RedditProgramStore(file_path=str(tmp_path / "programs.json"))
    program = store.create_program(
        _spec(
            topic_constraints={"subreddits": ["womenshealth", "healthyhooha"], "keywords": ["biopsy"]},
            content_assignments={"items": []},
            engagement_quotas={
                "posts_min_per_day": 1,
                "posts_max_per_day": 1,
                "upvotes_min_per_day": 0,
                "upvotes_max_per_day": 0,
                "comment_upvote_min_per_day": 0,
                "comment_upvote_max_per_day": 0,
                "reply_min_per_day": 0,
                "reply_max_per_day": 0,
                "random_reply_templates": [],
                "random_upvote_action": "upvote_post",
            },
            execution_policy={
                "strict_quotas": True,
                "allow_target_reuse_within_day": False,
                "cooldown_minutes": 0,
                "max_actions_per_tick": 5,
                "max_discovery_posts_per_subreddit": 4,
                "max_comment_candidates_per_post": 4,
                "retry_delay_minutes": 5,
                "max_attempts_per_item": 3,
            },
        )
    )
    item = next(entry for entry in program["compiled"]["work_items"] if entry["action"] == "create_post")
    item["subreddit"] = "womenshealth"
    orchestrator = RedditProgramOrchestrator(store=store)

    orchestrator._record_failure(
        program,
        item,
        {
            "success": False,
            "action": "create_post",
            "error": "reddit community ban: can't contribute to community",
            "failure_class": "community_restricted",
            "attempt_id": "attempt-ban",
            "trace_id": "trace-ban",
            "final_verdict": "failed_confirmed",
            "evidence_summary": "community ban detected",
            "subreddit": "womenshealth",
            "current_url": "https://www.reddit.com/r/WomensHealth/submit?type=TEXT",
        },
        None,
    )

    assert item["status"] == "pending"
    assert item["subreddit"] == "healthyhooha"
    assert "womenshealth" in program["community_block_matrix"]["reddit_amy"]


def test_discovery_excludes_profile_blocked_subreddits(tmp_path, monkeypatch):
    store = RedditProgramStore(file_path=str(tmp_path / "programs.json"))
    program = store.create_program(
        _spec(
            topic_constraints={"subreddits": ["womenshealth", "healthyhooha"], "keywords": ["biopsy"]},
            content_assignments={"items": []},
            engagement_quotas={
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
    program["community_block_matrix"] = {"reddit_amy": ["womenshealth"]}
    orchestrator = RedditProgramOrchestrator(store=store)
    item = next(entry for entry in program["compiled"]["work_items"] if entry["action"] == "upvote_post")

    seen = []

    async def fake_discover_posts_for_subreddit(*, subreddit, keywords, max_posts):
        seen.append(subreddit)
        return [
            {
                "target_id": "p1",
                "target_url": f"https://www.reddit.com/r/{subreddit}/comments/p1/example/",
                "subreddit": subreddit,
                "title": "example",
                "score": 5,
                "comment_count": 4,
                "source": "subreddit_hot",
            }
        ]

    monkeypatch.setattr(orchestrator, "_discover_posts_for_subreddit", fake_discover_posts_for_subreddit)
    candidate = asyncio.run(orchestrator._discover_post_target(program, item))

    assert candidate is not None
    assert seen == ["healthyhooha"]
    assert candidate["subreddit"] == "healthyhooha"


def test_select_due_items_prioritizes_mandatory_joins(tmp_path):
    store = RedditProgramStore(file_path=str(tmp_path / "programs.json"))
    program = store.create_program(
        _spec(
            topic_constraints={
                "subreddits": ["womenshealth"],
                "keywords": ["biopsy"],
                "mandatory_join_urls": ["https://www.reddit.com/r/WomensHealth/"],
            },
            content_assignments={"items": []},
            engagement_quotas={
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
        )
    )
    orchestrator = RedditProgramOrchestrator(store=store)

    selected = orchestrator._select_due_items(program, now=datetime.now(timezone.utc), force_due=True)

    assert selected
    assert all(item["action"] == "join_subreddit" for item in selected)


def test_resolve_target_generates_reply_text_when_missing(tmp_path, monkeypatch):
    store = RedditProgramStore(file_path=str(tmp_path / "programs.json"))
    program = store.create_program(
        _spec(
            content_assignments={"items": []},
            topic_constraints={"subreddits": ["womenshealth"], "keywords": ["biopsy"]},
            engagement_quotas={
                "upvotes_per_day": 0,
                "reply_min_per_day": 1,
                "reply_max_per_day": 1,
                "random_reply_templates": [],
                "random_upvote_action": "upvote_post",
            },
        )
    )
    orchestrator = RedditProgramOrchestrator(store=store)
    item = next(entry for entry in program["compiled"]["work_items"] if entry["action"] == "reply_comment")

    async def fake_discover_comment(_program, _item, actor_username=None):
        return {
            "target_comment_url": "https://www.reddit.com/r/womenshealth/comments/post/comment/c1/",
            "thread_url": "https://www.reddit.com/r/womenshealth/comments/post/",
            "subreddit": "womenshealth",
            "author": "helper",
            "body_excerpt": "i am worried about biopsy recovery",
        }

    async def fake_style_samples(_program, *, subreddit, keywords):
        assert subreddit == "womenshealth"
        return [{"target_url": "https://www.reddit.com/r/womenshealth/comments/abc123/sample/", "title": "sample", "body_excerpt": "sample text"}]

    async def fake_generate_reply(**kwargs):
        return type(
            "GenerationResult",
            (),
            {
                "success": True,
                "text": "it may help to ask what pain control and aftercare they usually recommend before you go in.",
                "style_summary": {"sample_count": 1},
                "conversation_summary": {"sample_count": 2, "top_terms": ["biopsy"]},
                "sample_urls": ["https://www.reddit.com/r/womenshealth/comments/abc123/sample/"],
                "validation": {"ok": True, "violations": [], "word_count": 15, "similarity_checks": {}},
                "persona_snapshot": {"persona_id": "amy_blunt_triage", "default_role": "blunt_critique", "case_style": "lowercase"},
                "writing_rule_snapshot": {"source_paths": ["rules"], "rule_source_hashes": {"negative-patterns.md": "hash"}},
                "word_count": 15,
                "raw_response": '{"text":"ok"}',
            },
        )()

    monkeypatch.setattr(orchestrator, "_discover_comment_target", fake_discover_comment)
    monkeypatch.setattr(orchestrator, "_style_samples_for_subreddit", fake_style_samples)
    monkeypatch.setattr(orchestrator.content_generator, "generate_reply", fake_generate_reply)

    payload = asyncio.run(orchestrator._resolve_target(program, item))

    assert payload["text"].startswith("it may help")
    assert payload["generation_evidence"]["kind"] == "reply_comment"
    assert payload["generation_evidence"]["conversation_summary"]["sample_count"] == 2
    assert payload["generation_evidence"]["persona_id"] == "amy_blunt_triage"


def test_record_failure_keeps_mandatory_join_target_url(tmp_path):
    store = RedditProgramStore(file_path=str(tmp_path / "programs.json"))
    program = store.create_program(
        _spec(
            content_assignments={"items": []},
            topic_constraints={
                "subreddits": ["womenshealth"],
                "keywords": ["biopsy"],
                "mandatory_join_urls": ["https://www.reddit.com/r/WomensHealth/"],
            },
            engagement_quotas={
                "upvotes_per_day": 0,
                "reply_min_per_day": 0,
                "reply_max_per_day": 0,
                "random_reply_templates": [],
                "random_upvote_action": "upvote_post",
            },
        )
    )
    orchestrator = RedditProgramOrchestrator(store=store)
    item = next(entry for entry in program["compiled"]["work_items"] if entry["action"] == "join_subreddit")

    assert item["target_url"] == "https://www.reddit.com/r/WomensHealth/"

    orchestrator._record_failure(
        program,
        item,
        {
            "success": False,
            "action": "join_subreddit",
            "error": "net::ERR_EMPTY_RESPONSE",
            "failure_class": "infrastructure",
            "attempt_id": "attempt-join",
            "trace_id": "trace-join",
            "final_verdict": "failed_confirmed",
            "evidence_summary": "join failed transiently",
        },
        None,
    )

    assert item["status"] == "pending"
    assert item["target_url"] == "https://www.reddit.com/r/WomensHealth/"


def test_discover_comment_target_skips_own_content_in_production(tmp_path, monkeypatch):
    store = RedditProgramStore(file_path=str(tmp_path / "programs.json"))
    program = store.create_program(
        _spec(
            content_assignments={"items": []},
            topic_constraints={"subreddits": ["womenshealth"], "keywords": ["biopsy"]},
            engagement_quotas={
                "upvotes_per_day": 0,
                "reply_min_per_day": 1,
                "reply_max_per_day": 1,
                "random_reply_templates": [],
                "random_upvote_action": "upvote_post",
            },
        )
    )
    orchestrator = RedditProgramOrchestrator(store=store)
    item = next(entry for entry in program["compiled"]["work_items"] if entry["action"] == "reply_comment")

    async def fake_discover_posts_for_subreddit(*, subreddit, keywords, max_posts):
        return [
            {
                "target_id": "p1",
                "target_url": "https://www.reddit.com/r/womenshealth/comments/p1/thread/",
                "subreddit": subreddit,
                "title": "biopsy question",
                "body_excerpt": "still cramping after biopsy",
                "author": "different_user",
                "score": 5,
                "comment_count": 2,
                "source": "subreddit_hot",
            }
        ]

    async def fake_fetch_json(url):
        return [
            {},
            {
                "data": {
                    "children": [
                        {
                            "kind": "t1",
                            "data": {
                                "id": "comment1",
                                "body": "i had a similar recovery timeline",
                                "author": "reddit_amy_actual",
                                "permalink": "/r/womenshealth/comments/p1/thread/comment/comment1/",
                                "score": 8,
                            },
                        },
                        {
                            "kind": "t1",
                            "data": {
                                "id": "comment2",
                                "body": "my cramps eased up after two days",
                                "author": "other_user",
                                "permalink": "/r/womenshealth/comments/p1/thread/comment/comment2/",
                                "score": 6,
                            },
                        },
                    ]
                }
            },
        ]

    monkeypatch.setattr(orchestrator, "_discover_posts_for_subreddit", fake_discover_posts_for_subreddit)
    monkeypatch.setattr(orchestrator, "_fetch_json", fake_fetch_json)

    candidate = asyncio.run(orchestrator._discover_comment_target(program, item, actor_username="reddit_amy_actual"))

    assert candidate is not None
    assert candidate["author"] == "other_user"


def test_discover_comment_target_prefers_non_dogpiled_thread(tmp_path, monkeypatch):
    store = RedditProgramStore(file_path=str(tmp_path / "programs.json"))
    program = store.create_program(
        _spec(
            content_assignments={"items": []},
            topic_constraints={"subreddits": ["womenshealth"], "keywords": ["biopsy"]},
            engagement_quotas={
                "upvotes_per_day": 0,
                "reply_min_per_day": 1,
                "reply_max_per_day": 1,
                "random_reply_templates": [],
                "random_upvote_action": "upvote_post",
            },
        )
    )
    orchestrator = RedditProgramOrchestrator(store=store)
    item = next(entry for entry in program["compiled"]["work_items"] if entry["action"] == "reply_comment")
    program["target_history"] = [
        {
            "profile_name": "reddit_alpha",
            "local_date": item["local_date"],
            "action": "reply_comment",
            "thread_url": "https://www.reddit.com/r/womenshealth/comments/p1/thread/",
            "target_ref": "https://www.reddit.com/r/womenshealth/comments/p1/thread/comment/c-old/",
            "subreddit": "womenshealth",
        }
    ]

    async def fake_discover_posts_for_subreddit(*, subreddit, keywords, max_posts):
        return [
            {
                "target_id": "p1",
                "target_url": "https://www.reddit.com/r/womenshealth/comments/p1/thread/",
                "subreddit": subreddit,
                "title": "biopsy question",
                "body_excerpt": "still cramping after biopsy",
                "author": "post_one",
                "score": 50,
                "comment_count": 60,
                "source": "subreddit_hot",
            },
            {
                "target_id": "p2",
                "target_url": "https://www.reddit.com/r/womenshealth/comments/p2/thread/",
                "subreddit": subreddit,
                "title": "healing question",
                "body_excerpt": "question about healing",
                "author": "post_two",
                "score": 10,
                "comment_count": 8,
                "source": "subreddit_hot",
            },
        ]

    async def fake_fetch_json(url):
        if "p1" in url:
            permalink = "/r/womenshealth/comments/p1/thread/comment/c1/"
        else:
            permalink = "/r/womenshealth/comments/p2/thread/comment/c2/"
        return [
            {},
            {
                "data": {
                    "children": [
                        {
                            "kind": "t1",
                            "data": {
                                "id": permalink.rsplit("/", 2)[1],
                                "body": "my cramps eased after two days",
                                "author": "other_user",
                                "permalink": permalink,
                                "score": 5,
                            },
                        }
                    ]
                }
            },
        ]

    monkeypatch.setattr(orchestrator, "_discover_posts_for_subreddit", fake_discover_posts_for_subreddit)
    monkeypatch.setattr(orchestrator, "_fetch_json", fake_fetch_json)

    candidate = asyncio.run(orchestrator._discover_comment_target(program, item, actor_username="reddit_amy_actual"))

    assert candidate is not None
    assert candidate["thread_url"] == "https://www.reddit.com/r/womenshealth/comments/p2/thread/"


def test_finalize_resolution_failure_does_not_consume_attempt_on_generation_failure(tmp_path):
    store = RedditProgramStore(file_path=str(tmp_path / "programs.json"))
    program = store.create_program(
        _spec(
            content_assignments={"items": []},
            engagement_quotas={
                "posts_min_per_day": 1,
                "posts_max_per_day": 1,
                "upvotes_min_per_day": 0,
                "upvotes_max_per_day": 0,
                "comment_upvote_min_per_day": 0,
                "comment_upvote_max_per_day": 0,
                "reply_min_per_day": 0,
                "reply_max_per_day": 0,
                "random_reply_templates": [],
                "random_upvote_action": "upvote_post",
            },
        )
    )
    orchestrator = RedditProgramOrchestrator(store=store)
    item = next(entry for entry in program["compiled"]["work_items"] if entry["action"] == "create_post")
    item["attempts"] = 1

    updated = orchestrator._finalize_resolution_failure(
        program,
        item,
        error="generated reddit post failed",
        failure_class="generation_failed",
        retryable=True,
        consume_attempt=False,
    )
    item = next(entry for entry in updated["compiled"]["work_items"] if entry["id"] == item["id"])

    assert item["attempts"] == 0
    assert item["status"] == "pending"


def test_remember_generated_text_persists_scoped_records(tmp_path):
    store = RedditProgramStore(file_path=str(tmp_path / "programs.json"))
    program = store.create_program(_spec())
    orchestrator = RedditProgramOrchestrator(store=store)
    item = next(entry for entry in program["compiled"]["work_items"] if entry["action"] == "reply_comment")
    item["target_comment_url"] = "https://www.reddit.com/r/womenshealth/comments/post/comment/c1/"
    item["discovered_target"] = {"thread_url": "https://www.reddit.com/r/womenshealth/comments/post/"}

    orchestrator._remember_generated_text(
        program,
        item,
        {
            "text": "go back.",
            "combined_text": "go back.",
            "thread_url": "https://www.reddit.com/r/womenshealth/comments/post/",
        },
    )

    assert program["generated_text_history"] == ["go back."]
    assert program["generated_text_records"][0]["thread_url"] == "https://www.reddit.com/r/womenshealth/comments/post/"


def test_create_post_success_uses_current_url_as_target_reference(tmp_path, monkeypatch):
    store = RedditProgramStore(file_path=str(tmp_path / "programs.json"))
    program = store.create_program(
        _spec(
            content_assignments={"items": []},
            engagement_quotas={
                "posts_min_per_day": 1,
                "posts_max_per_day": 1,
                "upvotes_min_per_day": 0,
                "upvotes_max_per_day": 0,
                "comment_upvote_min_per_day": 0,
                "comment_upvote_max_per_day": 0,
                "reply_min_per_day": 0,
                "reply_max_per_day": 0,
                "random_reply_templates": [],
                "random_upvote_action": "upvote_post",
            },
        )
    )

    async def fake_runner(_session, *, action, **_kwargs):
        assert action == "create_post"
        return {
            **_success_result(action, current_url="https://www.reddit.com/r/womenshealth/comments/post123/a_new_question/"),
            "target_url": None,
        }

    monkeypatch.setattr(RedditSession, "load", lambda self: {"profile_name": self.profile_name})
    monkeypatch.setattr(RedditSession, "get_username", lambda self: "reddit_amy")
    orchestrator = RedditProgramOrchestrator(store=store, action_runner=fake_runner)
    async def fake_generate_post(**_kwargs):
        return type(
            "GenerationResult",
            (),
            {
                "success": True,
                "title": "should i get checked again after this biopsy?",
                "body": "still having more soreness than i expected.",
                "style_summary": {"sample_count": 1},
                "conversation_summary": {"sample_count": 2, "top_terms": ["biopsy"]},
                "sample_urls": ["https://www.reddit.com/r/womenshealth/comments/abc123/sample/"],
                "validation": {"ok": True, "violations": [], "word_count": 14, "similarity_checks": {}},
                "persona_snapshot": {"persona_id": "amy_blunt_triage", "default_role": "blunt_critique", "case_style": "lowercase"},
                "writing_rule_snapshot": {"source_paths": ["rules"], "rule_source_hashes": {"negative-patterns.md": "hash"}},
                "word_count": 14,
                "raw_response": '{"title":"ok","body":"ok"}',
            },
        )()
    monkeypatch.setattr(orchestrator.content_generator, "generate_post", fake_generate_post)
    item = next(entry for entry in program["compiled"]["work_items"] if entry["action"] == "create_post")

    updated = asyncio.run(orchestrator._run_work_item(program, item["id"]))
    item = next(entry for entry in updated["compiled"]["work_items"] if entry["id"] == item["id"])

    assert item["status"] == "completed"
    assert item["target_url"] == "https://www.reddit.com/r/womenshealth/comments/post123/a_new_question/"
