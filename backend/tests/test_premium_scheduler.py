import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from premium_orchestrator import PremiumOrchestrator
from premium_scheduler import PremiumScheduler
from premium_store import PremiumStore


class FakeContent:
    @staticmethod
    async def generate_post_bundle(**kwargs):
        return {
            "success": True,
            "post_kind": kwargs["post_kind"],
            "caption": "supportive post",
            "image_path": "/tmp/image.png",
            "rules_validation": {"ok": True, "violations": []},
        }

    @staticmethod
    def cleanup_generated_image(_path):
        return None


class FakeSafety:
    @staticmethod
    async def run_feed_safety_precheck(**kwargs):
        return {
            "success": True,
            "error": None,
            "checked_at": "2026-02-24T12:00:00Z",
            "profile_url": "https://m.facebook.com/profile.php?id=1",
            "before_screenshot": "/tmp/precheck_before.png",
            "after_screenshot": "/tmp/precheck_after.png",
            "screenshot_urls": {
                "before": "/screenshots/precheck_before.png",
                "after": "/screenshots/precheck_after.png",
            },
            "identity_check": {
                "profile_name_expected": kwargs.get("profile_name"),
                "profile_name_seen": kwargs.get("profile_name"),
                "name_match": True,
                "avatar_similarity": 1.0,
                "avatar_hash_match": True,
                "passed": True,
            },
            "duplicate_precheck": {
                "checked_posts": int(kwargs.get("lookback_posts", 5)),
                "threshold": float(kwargs.get("threshold", 0.90)),
                "top_similarity": 0.2,
                "matched_post_permalink": None,
                "passed": True,
            },
        }


def _evidence(action_type: str, profile_name: str, completed_count: int, confirmation: dict) -> dict:
    return {
        "action_id": str(uuid.uuid4()),
        "timestamp": "2026-02-24T12:00:00Z",
        "step_id": f"{action_type}_step",
        "action_type": action_type,
        "profile_name": profile_name,
        "target_url": "https://m.facebook.com/groups/123/posts/456",
        "target_id": "456",
        "before_screenshot": "/tmp/before.png",
        "after_screenshot": "/tmp/after.png",
        "action_method": {
            "engine": "adaptive_agent",
            "final_status": "task_completed",
            "steps_count": 2,
            "action_trace": [action_type, "done"],
            "selector_trace": [],
        },
        "result_state": {"success": True, "completed_count": completed_count, "errors": []},
        "confirmation": {"profile_identity_confirmed": True, **confirmation},
    }


class FakeActions:
    async def publish_feed_post(self, **kwargs):
        return {
            "success": True,
            "completed_count": 1,
            "expected_count": 1,
            "evidence": _evidence("feed_post", kwargs["profile_name"], 1, {"post_visible_or_permalink_resolved": True}),
            "error": None,
        }

    async def discover_group_and_publish(self, **kwargs):
        return {
            "success": True,
            "completed_count": 1,
            "expected_count": 1,
            "evidence": _evidence("group_post", kwargs["profile_name"], 1, {"post_visible_or_permalink_resolved": True}),
            "error": None,
        }

    async def perform_likes(self, **kwargs):
        count = int(kwargs["likes_count"])
        return {
            "success": True,
            "completed_count": count,
            "expected_count": count,
            "evidence": _evidence("likes", kwargs["profile_name"], count, {"like_state_active": True}),
            "error": None,
        }

    async def perform_shares(self, **kwargs):
        count = int(kwargs["shares_count"])
        return {
            "success": True,
            "completed_count": count,
            "expected_count": count,
            "evidence": _evidence(
                "shares",
                kwargs["profile_name"],
                count,
                {"share_confirmed": True, "share_destination": "own_feed", "share_destination_confirmed": True},
            ),
            "error": None,
        }

    async def perform_comment_replies(self, **kwargs):
        count = int(kwargs["replies_count"])
        return {
            "success": True,
            "completed_count": count,
            "expected_count": count,
            "evidence": _evidence("comment_replies", kwargs["profile_name"], count, {"reply_visible_under_thread": True}),
            "error": None,
        }


def _run_spec():
    return {
        "profile_name": "Vanessa Hines",
        "feed_plan": {"total_posts": 1, "character_posts": 1, "ambient_posts": 0},
        "group_discovery": {"topic_seed": "menopause groups", "allow_join_new": True, "join_pending_policy": "try_next_group"},
        "engagement_recipe": {"likes_per_cycle": 2, "shares_per_cycle": 1, "replies_per_cycle": 1, "share_target": "own_feed"},
        "schedule": {"duration_days": 1, "timezone": "America/New_York", "random_windows": [{"start_hour": 8, "end_hour": 22}]},
        "verification_contract": {
            "require_evidence": True,
            "require_target_reference": True,
            "require_action_metadata": True,
            "require_before_after_screenshots": True,
            "require_profile_identity": True,
        },
        "metadata": {},
    }


def test_scheduler_tick_is_idempotent_for_same_due_cycle(tmp_path):
    store = PremiumStore(file_path=str(tmp_path / "premium_state.json"))
    store.upsert_profile_config(
        "Vanessa Hines",
        {
            "character_profile": {"persona_description": "supportive community member"},
            "content_policy": {"casing_mode": "natural_mixed"},
            "execution_policy": {"enabled": True, "max_retries": 1, "stop_on_first_failure": True},
        },
    )
    store.set_rules_snapshot({"version": "v_test", "negative_patterns": [], "vocabulary_guidance": [], "raw": {}})

    run = store.create_run(run_spec=_run_spec(), created_by="tester")
    store.state["runs"][run["id"]]["cycles"][0]["scheduled_at"] = "2000-01-01T00:00:00Z"
    store.save()

    orchestrator = PremiumOrchestrator(
        store=store,
        broadcast_update=None,
        actions_module=FakeActions(),
        content_module=FakeContent,
        safety_module=FakeSafety,
    )
    scheduler = PremiumScheduler(store=store, orchestrator=orchestrator)

    async def _run_ticks():
        first = await scheduler.tick(source="test_first")
        second = await scheduler.tick(source="test_second")
        return first, second

    first_tick, second_tick = asyncio.run(_run_ticks())
    assert first_tick["processed"] == 1
    assert second_tick["processed"] == 0

    updated_run = store.get_run(run["id"])
    assert updated_run["status"] == "completed"


def test_scheduler_start_recovers_interrupted_running_cycle(tmp_path):
    store = PremiumStore(file_path=str(tmp_path / "premium_state.json"))
    store.upsert_profile_config(
        "Vanessa Hines",
        {
            "character_profile": {"persona_description": "supportive community member"},
            "content_policy": {"casing_mode": "natural_mixed"},
            "execution_policy": {"enabled": True, "max_retries": 1, "stop_on_first_failure": True},
        },
    )
    store.set_rules_snapshot({"version": "v_test", "negative_patterns": [], "vocabulary_guidance": [], "raw": {}})

    run = store.create_run(run_spec=_run_spec(), created_by="tester")
    store.state["runs"][run["id"]]["status"] = "in_progress"
    store.state["runs"][run["id"]]["cycles"][0]["status"] = "running"
    store.state["runs"][run["id"]]["cycles"][0]["started_at"] = "2026-02-25T00:00:00Z"
    store.save()

    orchestrator = PremiumOrchestrator(
        store=store,
        broadcast_update=None,
        actions_module=FakeActions(),
        content_module=FakeContent,
        safety_module=FakeSafety,
    )
    scheduler = PremiumScheduler(store=store, orchestrator=orchestrator)

    async def _run_start_stop():
        await scheduler.start()
        await scheduler.stop()

    asyncio.run(_run_start_stop())
    recovered_run = store.get_run(run["id"])
    assert recovered_run["status"] == "failed"
    assert recovered_run["error"] == "interrupted_by_process_restart"
    assert recovered_run["cycles"][0]["status"] == "failed"
