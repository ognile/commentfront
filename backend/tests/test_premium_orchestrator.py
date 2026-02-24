import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from premium_orchestrator import PremiumOrchestrator
from premium_store import PremiumStore


class FakeContentModule:
    @staticmethod
    async def generate_post_bundle(**kwargs):
        return {
            "success": True,
            "post_kind": kwargs["post_kind"],
            "caption": "supportive message for the group",
            "image_path": "/tmp/generated.png",
            "rules_validation": {"ok": True, "violations": []},
        }

    @staticmethod
    def cleanup_generated_image(_path):
        return None


def _evidence_payload(
    *,
    action_type: str,
    profile_name: str,
    completed_count: int,
    confirmation: dict,
) -> dict:
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
            "action_trace": [f"{action_type}: click", f"{action_type}: done"],
            "selector_trace": [{"tag": "DIV", "aria_label": "button", "text": action_type}],
        },
        "result_state": {
            "success": True,
            "completed_count": completed_count,
            "errors": [],
        },
        "confirmation": {
            "profile_identity_confirmed": True,
            **confirmation,
        },
    }


class StrictSuccessActions:
    def __init__(self):
        self.sequence = []
        self.group_kwargs = {}

    async def publish_feed_post(self, **kwargs):
        self.sequence.append("feed")
        return {
            "success": True,
            "completed_count": 1,
            "expected_count": 1,
            "evidence": _evidence_payload(
                action_type="feed_post",
                profile_name=kwargs["profile_name"],
                completed_count=1,
                confirmation={"post_visible_or_permalink_resolved": True},
            ),
            "error": None,
        }

    async def discover_group_and_publish(self, **kwargs):
        self.sequence.append("group")
        self.group_kwargs = kwargs
        return {
            "success": True,
            "completed_count": 1,
            "expected_count": 1,
            "evidence": _evidence_payload(
                action_type="group_post",
                profile_name=kwargs["profile_name"],
                completed_count=1,
                confirmation={"post_visible_or_permalink_resolved": True},
            ),
            "error": None,
        }

    async def perform_likes(self, **kwargs):
        self.sequence.append("likes")
        likes_count = int(kwargs.get("likes_count", 0))
        return {
            "success": True,
            "completed_count": likes_count,
            "expected_count": likes_count,
            "evidence": _evidence_payload(
                action_type="likes",
                profile_name=kwargs["profile_name"],
                completed_count=likes_count,
                confirmation={"like_state_active": True},
            ),
            "error": None,
        }

    async def perform_shares(self, **kwargs):
        self.sequence.append("shares")
        shares_count = int(kwargs.get("shares_count", 0))
        return {
            "success": True,
            "completed_count": shares_count,
            "expected_count": shares_count,
            "evidence": _evidence_payload(
                action_type="shares",
                profile_name=kwargs["profile_name"],
                completed_count=shares_count,
                confirmation={
                    "share_confirmed": True,
                    "share_destination": "own_feed",
                    "share_destination_confirmed": True,
                },
            ),
            "error": None,
        }

    async def perform_comment_replies(self, **kwargs):
        self.sequence.append("replies")
        replies_count = int(kwargs.get("replies_count", 0))
        return {
            "success": True,
            "completed_count": replies_count,
            "expected_count": replies_count,
            "evidence": _evidence_payload(
                action_type="comment_replies",
                profile_name=kwargs["profile_name"],
                completed_count=replies_count,
                confirmation={"reply_visible_under_thread": True},
            ),
            "error": None,
        }


class MissingEvidenceActions(StrictSuccessActions):
    async def publish_feed_post(self, **kwargs):
        result = await super().publish_feed_post(**kwargs)
        # Break strict evidence requirements on purpose.
        result["evidence"]["after_screenshot"] = None
        return result


def _run_spec_single_cycle():
    return {
        "profile_name": "Vanessa Hines",
        "feed_plan": {"total_posts": 1, "character_posts": 1, "ambient_posts": 0},
        "group_discovery": {
            "topic_seed": "menopause groups",
            "allow_join_new": True,
            "join_pending_policy": "try_next_group",
        },
        "engagement_recipe": {
            "likes_per_cycle": 2,
            "shares_per_cycle": 1,
            "replies_per_cycle": 1,
            "share_target": "own_feed",
        },
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


def _run_spec_full_pilot():
    return {
        "profile_name": "Vanessa Hines",
        "feed_plan": {"total_posts": 4, "character_posts": 3, "ambient_posts": 1},
        "group_discovery": {
            "topic_seed": "menopause groups",
            "allow_join_new": True,
            "join_pending_policy": "try_next_group",
        },
        "engagement_recipe": {
            "likes_per_cycle": 2,
            "shares_per_cycle": 1,
            "replies_per_cycle": 1,
            "share_target": "own_feed",
        },
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


def _setup_store(tmp_path):
    store = PremiumStore(file_path=str(tmp_path / "premium_state.json"))
    store.upsert_profile_config(
        "Vanessa Hines",
        {
            "character_profile": {"persona_description": "supportive community member"},
            "content_policy": {"casing_mode": "natural_mixed"},
            "execution_policy": {"enabled": True, "max_retries": 1, "stop_on_first_failure": True},
        },
    )
    store.set_rules_snapshot(
        {
            "version": "v_test",
            "negative_patterns": [],
            "vocabulary_guidance": [],
            "raw": {},
        }
    )
    return store


def test_orchestrator_completes_run_with_strict_verification_pass(tmp_path):
    store = _setup_store(tmp_path)
    run = store.create_run(run_spec=_run_spec_single_cycle(), created_by="tester")
    store.state["runs"][run["id"]]["cycles"][0]["scheduled_at"] = "2000-01-01T00:00:00Z"
    store.save()

    actions = StrictSuccessActions()
    orchestrator = PremiumOrchestrator(
        store=store,
        broadcast_update=None,
        actions_module=actions,
        content_module=FakeContentModule,
    )

    summary = asyncio.run(orchestrator.process_due_runs(max_runs=1))
    assert summary["processed"] == 1

    updated_run = store.get_run(run["id"])
    assert updated_run["status"] == "completed"
    assert updated_run["pass_matrix"]["feed_posts"] == "1/1"
    assert updated_run["pass_matrix"]["group_posts"] == "1/1"
    assert updated_run["pass_matrix"]["likes"] == "2/2"
    assert updated_run["pass_matrix"]["shares"] == "1/1"
    assert updated_run["pass_matrix"]["comment_replies"] == "1/1"
    assert actions.sequence == ["feed", "group", "likes", "shares", "replies"]
    assert actions.group_kwargs.get("join_pending_policy") == "try_next_group"


def test_orchestrator_fails_when_evidence_is_incomplete(tmp_path):
    store = _setup_store(tmp_path)
    run = store.create_run(run_spec=_run_spec_single_cycle(), created_by="tester")
    store.state["runs"][run["id"]]["cycles"][0]["scheduled_at"] = "2000-01-01T00:00:00Z"
    store.save()

    orchestrator = PremiumOrchestrator(
        store=store,
        broadcast_update=None,
        actions_module=MissingEvidenceActions(),
        content_module=FakeContentModule,
    )

    summary = asyncio.run(orchestrator.process_due_runs(max_runs=1))
    assert summary["processed"] == 1

    updated_run = store.get_run(run["id"])
    assert updated_run["status"] == "failed"
    assert "evidence contract failed" in str(updated_run.get("error", "")).lower()


def test_orchestrator_full_pilot_hits_strict_pass_matrix(tmp_path):
    store = _setup_store(tmp_path)
    run = store.create_run(run_spec=_run_spec_full_pilot(), created_by="tester")
    for cycle in store.state["runs"][run["id"]]["cycles"]:
        cycle["scheduled_at"] = "2000-01-01T00:00:00Z"
    store.save()

    actions = StrictSuccessActions()
    orchestrator = PremiumOrchestrator(
        store=store,
        broadcast_update=None,
        actions_module=actions,
        content_module=FakeContentModule,
    )

    # Run until all due cycles are consumed.
    for _ in range(8):
        summary = asyncio.run(orchestrator.process_due_runs(max_runs=10))
        if summary["processed"] == 0:
            break

    updated_run = store.get_run(run["id"])
    assert updated_run["status"] == "completed"
    assert updated_run["pass_matrix"]["feed_posts"] == "4/4"
    assert updated_run["pass_matrix"]["group_posts"] == "4/4"
    assert updated_run["pass_matrix"]["likes"] == "8/8"
    assert updated_run["pass_matrix"]["shares"] == "4/4"
    assert updated_run["pass_matrix"]["comment_replies"] == "4/4"
    assert updated_run["pass_matrix"]["character_posts"] == "3/3"
    assert updated_run["pass_matrix"]["ambient_posts"] == "1/1"
