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


class FakeSafetyPass:
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
                "top_similarity": 0.23,
                "matched_post_permalink": None,
                "passed": True,
            },
        }


class FakeSafetyIdentityFail(FakeSafetyPass):
    @staticmethod
    async def run_feed_safety_precheck(**kwargs):
        payload = await FakeSafetyPass.run_feed_safety_precheck(**kwargs)
        payload["success"] = False
        payload["error"] = "identity_verification_failed"
        payload["identity_check"]["passed"] = False
        payload["identity_check"]["avatar_similarity"] = 0.12
        payload["identity_check"]["avatar_hash_match"] = False
        return payload


class FakeSafetyDuplicateFail(FakeSafetyPass):
    @staticmethod
    async def run_feed_safety_precheck(**kwargs):
        payload = await FakeSafetyPass.run_feed_safety_precheck(**kwargs)
        payload["success"] = False
        payload["error"] = "duplicate_precheck_failed"
        payload["duplicate_precheck"]["top_similarity"] = 0.97
        payload["duplicate_precheck"]["matched_post_permalink"] = "https://m.facebook.com/story.php?story_fbid=1&id=1"
        payload["duplicate_precheck"]["passed"] = False
        return payload


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
        self.likes_kwargs = {}
        self.shares_kwargs = {}
        self.replies_kwargs = {}

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
        self.likes_kwargs = kwargs
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
        self.shares_kwargs = kwargs
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
        self.replies_kwargs = kwargs
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


class TunnelRetryGroupActions(StrictSuccessActions):
    def __init__(self):
        super().__init__()
        self.group_calls = 0

    async def discover_group_and_publish(self, **kwargs):
        self.group_calls += 1
        if self.group_calls == 1:
            return {
                "success": False,
                "completed_count": 0,
                "expected_count": 1,
                "error": "Page.goto: net::ERR_TUNNEL_CONNECTION_FAILED at https://m.facebook.com/groups",
                "result": {
                    "final_status": "error",
                    "errors": [
                        "Page.goto: net::ERR_TUNNEL_CONNECTION_FAILED at https://m.facebook.com/groups"
                    ],
                },
                "evidence": {},
            }
        return await super().discover_group_and_publish(**kwargs)


class TunnelAlwaysFeedActions(StrictSuccessActions):
    async def publish_feed_post(self, **kwargs):
        return {
            "success": False,
            "completed_count": 0,
            "expected_count": 1,
            "error": "Page.goto: net::ERR_TUNNEL_CONNECTION_FAILED at https://m.facebook.com/me/?v=timeline",
            "result": {
                "final_status": "error",
                "errors": [
                    "Page.goto: net::ERR_TUNNEL_CONNECTION_FAILED at https://m.facebook.com/me/?v=timeline",
                ],
            },
            "evidence": {},
        }


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
        safety_module=FakeSafetyPass,
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
        safety_module=FakeSafetyPass,
    )

    summary = asyncio.run(orchestrator.process_due_runs(max_runs=1))
    assert summary["processed"] == 1

    updated_run = store.get_run(run["id"])
    assert updated_run["status"] == "failed"
    assert "evidence contract failed" in str(updated_run.get("error", "")).lower()


def test_orchestrator_retries_group_action_on_tunnel_error(tmp_path):
    store = _setup_store(tmp_path)
    run = store.create_run(run_spec=_run_spec_single_cycle(), created_by="tester")
    store.state["runs"][run["id"]]["cycles"][0]["scheduled_at"] = "2000-01-01T00:00:00Z"
    store.save()

    actions = TunnelRetryGroupActions()
    orchestrator = PremiumOrchestrator(
        store=store,
        broadcast_update=None,
        actions_module=actions,
        content_module=FakeContentModule,
        safety_module=FakeSafetyPass,
    )

    summary = asyncio.run(orchestrator.process_due_runs(max_runs=1))
    assert summary["processed"] == 1

    updated_run = store.get_run(run["id"])
    assert updated_run["status"] == "completed"
    assert updated_run["pass_matrix"]["group_posts"] == "1/1"
    assert actions.group_calls == 2
    assert any(evt.get("type") == "action_retry_scheduled" for evt in updated_run.get("events", []))


def test_orchestrator_defers_cycle_on_transient_tunnel_error(tmp_path):
    store = _setup_store(tmp_path)
    run = store.create_run(run_spec=_run_spec_single_cycle(), created_by="tester")
    run_state = store.state["runs"][run["id"]]
    profile_cfg = store.state["profile_configs"]["vanessa hines"]
    run_state["cycles"][0]["scheduled_at"] = "2000-01-01T00:00:00Z"
    profile_cfg["execution_policy"]["max_retries"] = 0
    profile_cfg["execution_policy"]["tunnel_recovery_cycles"] = 2
    profile_cfg["execution_policy"]["tunnel_recovery_delay_seconds"] = 60
    store.save()

    orchestrator = PremiumOrchestrator(
        store=store,
        broadcast_update=None,
        actions_module=TunnelAlwaysFeedActions(),
        content_module=FakeContentModule,
        safety_module=FakeSafetyPass,
    )

    summary = asyncio.run(orchestrator.process_due_runs(max_runs=1))
    assert summary["processed"] == 1

    updated_run = store.get_run(run["id"])
    assert updated_run["status"] == "in_progress"
    cycle = next(c for c in updated_run["cycles"] if c["index"] == 0)
    assert cycle["status"] == "pending"
    assert cycle["attempts"] == 1
    assert cycle.get("error") == "transient tunnel outage during feed_posts"
    assert cycle.get("results")
    assert cycle["results"][-1]["type"] == "deferred"
    assert len(updated_run.get("evidence", [])) == 0
    assert any(evt.get("type") == "cycle_deferred_transient" for evt in updated_run.get("events", []))


def test_orchestrator_fails_when_tunnel_recovery_budget_exhausted(tmp_path):
    store = _setup_store(tmp_path)
    run = store.create_run(run_spec=_run_spec_single_cycle(), created_by="tester")
    run_state = store.state["runs"][run["id"]]
    profile_cfg = store.state["profile_configs"]["vanessa hines"]
    run_state["cycles"][0]["scheduled_at"] = "2000-01-01T00:00:00Z"
    run_state["cycles"][0]["attempts"] = 1
    profile_cfg["execution_policy"]["max_retries"] = 0
    profile_cfg["execution_policy"]["tunnel_recovery_cycles"] = 0
    profile_cfg["execution_policy"]["tunnel_recovery_delay_seconds"] = 60
    store.save()

    orchestrator = PremiumOrchestrator(
        store=store,
        broadcast_update=None,
        actions_module=TunnelAlwaysFeedActions(),
        content_module=FakeContentModule,
        safety_module=FakeSafetyPass,
    )

    summary = asyncio.run(orchestrator.process_due_runs(max_runs=1))
    assert summary["processed"] == 1

    updated_run = store.get_run(run["id"])
    assert updated_run["status"] == "failed"
    assert "tunnel recovery exhausted" in str(updated_run.get("error", "")).lower()
    assert len(updated_run.get("evidence", [])) == 0
    assert any(evt.get("type") == "cycle_tunnel_recovery_exhausted" for evt in updated_run.get("events", []))


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
        safety_module=FakeSafetyPass,
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


def test_orchestrator_passes_group_context_url_to_engagement_actions(tmp_path):
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
        safety_module=FakeSafetyPass,
    )

    summary = asyncio.run(orchestrator.process_due_runs(max_runs=1))
    assert summary["processed"] == 1

    group_target = "https://m.facebook.com/groups/123/posts/456"
    assert actions.likes_kwargs.get("start_url") == group_target
    assert actions.shares_kwargs.get("start_url") == group_target
    assert actions.replies_kwargs.get("start_url") == group_target


def test_orchestrator_fails_closed_on_identity_mismatch(tmp_path):
    store = _setup_store(tmp_path)
    run = store.create_run(run_spec=_run_spec_single_cycle(), created_by="tester")
    store.state["runs"][run["id"]]["cycles"][0]["scheduled_at"] = "2000-01-01T00:00:00Z"
    store.save()

    orchestrator = PremiumOrchestrator(
        store=store,
        broadcast_update=None,
        actions_module=StrictSuccessActions(),
        content_module=FakeContentModule,
        safety_module=FakeSafetyIdentityFail,
    )

    summary = asyncio.run(orchestrator.process_due_runs(max_runs=1))
    assert summary["processed"] == 1

    updated_run = store.get_run(run["id"])
    assert updated_run["status"] == "failed"
    assert "identity_verification_failed" in str(updated_run.get("error", ""))
    precheck_evidence = [
        item for item in (updated_run.get("evidence") or [])
        if item.get("action_type") == "feed_precheck"
    ]
    assert precheck_evidence
    assert precheck_evidence[-1]["identity_check"]["passed"] is False


def test_orchestrator_blocks_on_duplicate_precheck(tmp_path):
    store = _setup_store(tmp_path)
    run = store.create_run(run_spec=_run_spec_single_cycle(), created_by="tester")
    store.state["runs"][run["id"]]["cycles"][0]["scheduled_at"] = "2000-01-01T00:00:00Z"
    store.save()

    orchestrator = PremiumOrchestrator(
        store=store,
        broadcast_update=None,
        actions_module=StrictSuccessActions(),
        content_module=FakeContentModule,
        safety_module=FakeSafetyDuplicateFail,
    )

    summary = asyncio.run(orchestrator.process_due_runs(max_runs=1))
    assert summary["processed"] == 1

    updated_run = store.get_run(run["id"])
    assert updated_run["status"] == "failed"
    assert "duplicate_precheck_failed" in str(updated_run.get("error", ""))
    precheck_evidence = [
        item for item in (updated_run.get("evidence") or [])
        if item.get("action_type") == "feed_precheck"
    ]
    assert precheck_evidence
    assert precheck_evidence[-1]["duplicate_precheck"]["passed"] is False


def test_orchestrator_retries_content_when_duplicate_precheck_blocks(tmp_path):
    class DuplicateThenUniqueContent:
        def __init__(self):
            self.calls = 0

        async def generate_post_bundle(self, **kwargs):
            self.calls += 1
            caption = "duplicate caption candidate" if self.calls == 1 else "fresh caption candidate"
            return {
                "success": True,
                "post_kind": kwargs["post_kind"],
                "caption": caption,
                "image_path": "/tmp/generated.png",
                "rules_validation": {"ok": True, "violations": []},
            }

        def cleanup_generated_image(self, _path):
            return None

    class DuplicateThenPassSafety:
        async def run_feed_safety_precheck(self, **kwargs):
            duplicate = "duplicate caption candidate" in str(kwargs.get("caption") or "")
            return {
                "success": not duplicate,
                "error": "duplicate_precheck_failed" if duplicate else None,
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
                    "top_similarity": 0.97 if duplicate else 0.11,
                    "matched_post_permalink": "https://m.facebook.com/story.php?story_fbid=1&id=1" if duplicate else None,
                    "passed": not duplicate,
                },
            }

    store = _setup_store(tmp_path)
    run = store.create_run(run_spec=_run_spec_single_cycle(), created_by="tester")
    store.state["runs"][run["id"]]["cycles"][0]["scheduled_at"] = "2000-01-01T00:00:00Z"
    store.save()

    content = DuplicateThenUniqueContent()
    orchestrator = PremiumOrchestrator(
        store=store,
        broadcast_update=None,
        actions_module=StrictSuccessActions(),
        content_module=content,
        safety_module=DuplicateThenPassSafety(),
    )

    summary = asyncio.run(orchestrator.process_due_runs(max_runs=1))
    assert summary["processed"] == 1

    updated_run = store.get_run(run["id"])
    assert updated_run["status"] == "completed"
    assert content.calls >= 2
    assert any(evt.get("type") == "duplicate_precheck_retry_scheduled" for evt in updated_run.get("events", []))
    precheck_evidence = [
        item for item in (updated_run.get("evidence") or [])
        if item.get("action_type") == "feed_precheck"
    ]
    assert precheck_evidence
    assert precheck_evidence[0]["duplicate_precheck"]["passed"] is False


def test_orchestrator_defers_cycle_when_precheck_has_no_authored_posts(tmp_path):
    class NoPostsSafety:
        async def run_feed_safety_precheck(self, **kwargs):
            return {
                "success": False,
                "error": "duplicate_precheck_no_posts",
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
                    "checked_posts": 0,
                    "threshold": float(kwargs.get("threshold", 0.90)),
                    "top_similarity": 0.0,
                    "matched_post_permalink": None,
                    "required_posts": int(kwargs.get("lookback_posts", 5)),
                    "insufficient_posts": True,
                    "history_limited": False,
                    "passed": False,
                    "posts": [],
                },
            }

    store = _setup_store(tmp_path)
    run = store.create_run(run_spec=_run_spec_single_cycle(), created_by="tester")
    run_state = store.state["runs"][run["id"]]
    profile_cfg = store.state["profile_configs"]["vanessa hines"]
    run_state["cycles"][0]["scheduled_at"] = "2000-01-01T00:00:00Z"
    profile_cfg["execution_policy"]["tunnel_recovery_cycles"] = 2
    profile_cfg["execution_policy"]["tunnel_recovery_delay_seconds"] = 60
    store.save()

    orchestrator = PremiumOrchestrator(
        store=store,
        broadcast_update=None,
        actions_module=StrictSuccessActions(),
        content_module=FakeContentModule,
        safety_module=NoPostsSafety(),
    )

    summary = asyncio.run(orchestrator.process_due_runs(max_runs=1))
    assert summary["processed"] == 1

    updated_run = store.get_run(run["id"])
    assert updated_run["status"] == "in_progress"
    cycle = next(c for c in updated_run["cycles"] if c["index"] == 0)
    assert cycle["status"] == "pending"
    assert cycle["attempts"] == 1
    assert cycle.get("error") == "duplicate_precheck_no_posts"
    assert any(evt.get("type") == "duplicate_precheck_no_posts_deferred" for evt in updated_run.get("events", []))
    precheck_evidence = [
        item for item in (updated_run.get("evidence") or [])
        if item.get("action_type") == "feed_precheck"
    ]
    assert precheck_evidence
