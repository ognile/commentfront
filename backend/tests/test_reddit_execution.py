import asyncio
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
from reddit_execution import (
    build_execution_result,
    normalize_reddit_execution_spec,
    sync_work_item_with_execution_spec,
)
from reddit_execution_store import RedditExecutionStore


def test_normalize_reddit_execution_spec_enforces_capability_matrix():
    with pytest.raises(ValueError, match="not allowed"):
        normalize_reddit_execution_spec(
            {
                "actors": [{"profile_name": "reddit_amy"}],
                "target": {
                    "kind": "post",
                    "strategy": "explicit",
                    "target_url": "https://www.reddit.com/r/womenshealth/comments/abc123/example_post/",
                },
                "action": {"type": "reply", "params": {"text": "this should fail"}},
                "verification": {},
            },
            require_discovery_seed=True,
        )


def test_sync_work_item_with_execution_spec_migrates_legacy_reply_item():
    migrated = sync_work_item_with_execution_spec(
        {
            "profile_name": "reddit_amy",
            "action": "reply_comment",
            "target_comment_url": "https://www.reddit.com/r/womenshealth/comments/abc123/example_post/comment/xyz789/",
            "text": "supportive reply",
            "target_mode": "explicit",
        },
        verification={
            "require_success_confirmed": True,
            "require_attempt_id": True,
            "required_evidence_summary": True,
            "required_target_reference": True,
        },
    )

    assert migrated["action"] == "reply_comment"
    assert migrated["execution_spec"]["action"]["type"] == "reply"
    assert migrated["execution_spec"]["target"]["kind"] == "comment"
    assert migrated["execution_spec"]["target"]["target_comment_url"].endswith("/comment/xyz789/")


def test_build_execution_result_prefers_comment_target_reference():
    execution_spec = normalize_reddit_execution_spec(
        {
            "actors": [{"profile_name": "reddit_amy"}],
            "target": {
                "kind": "comment",
                "strategy": "explicit",
                "target_comment_url": "https://www.reddit.com/r/womenshealth/comments/abc123/example_post/comment/xyz789/",
            },
            "action": {"type": "reply", "params": {"text": "reply text"}},
            "verification": {},
        },
        require_discovery_seed=False,
    )
    result = build_execution_result(
        actor_profile_name="reddit_amy",
        execution_spec=execution_spec,
        item={
            "profile_name": "reddit_amy",
            "status": "completed",
            "target_comment_url": "https://www.reddit.com/r/womenshealth/comments/abc123/example_post/comment/xyz789/",
            "discovered_target": {"subreddit": "womenshealth"},
            "result": {
                "attempt_id": "attempt_reply",
                "final_verdict": "success_confirmed",
                "evidence_summary": "reply confirmed",
                "success": True,
            },
        },
        screenshot_artifact_url="/forensics/artifacts/reply-shot",
    )

    assert result["permalink_or_target_ref"].endswith("/comment/xyz789/")
    assert result["screenshot_artifact_url"] == "/forensics/artifacts/reply-shot"


def test_execute_reddit_execution_request_persists_run_record(tmp_path, monkeypatch):
    temp_execution_store = RedditExecutionStore(file_path=str(tmp_path / "reddit_executions.json"))
    monkeypatch.setattr(main, "reddit_execution_store", temp_execution_store)
    monkeypatch.setattr(
        main,
        "list_saved_reddit_sessions",
        lambda: [{"profile_name": "reddit_amy", "has_valid_session": True}],
    )

    class _FakeStore:
        def __init__(self, file_path=None):
            self.file_path = file_path
            self.program = None

        def create_program(self, spec):
            execution_spec = spec["content_assignments"]["items"][0]["execution_spec"]
            self.program = {
                "id": "reddit_program_execution",
                "status": "completed",
                "compiled": {
                    "work_items": [
                        {
                            "id": "work_1",
                            "profile_name": "reddit_amy",
                            "status": "completed",
                            "action": "comment_post",
                            "target_mode": "discover_post",
                            "target_url": "https://www.reddit.com/r/womenshealth/comments/abc123/example_post/",
                            "target_comment_url": None,
                            "subreddit": "womenshealth",
                            "execution_spec": execution_spec,
                            "discovered_target": {"subreddit": "womenshealth"},
                            "result": {
                                "success": True,
                                "attempt_id": "attempt_1",
                                "final_verdict": "success_confirmed",
                                "evidence_summary": "comment confirmed",
                                "target_url": "https://www.reddit.com/r/womenshealth/comments/abc123/example_post/",
                                "current_url": "https://www.reddit.com/r/womenshealth/comments/abc123/example_post/",
                            },
                        }
                    ]
                },
                "contract_totals": {"comment_post": 1},
                "remaining_contract": {},
            }
            return self.program

        def get_program(self, _program_id):
            return self.program

    class _FakeOrchestrator:
        def __init__(self, **_kwargs):
            pass

        async def process_program(self, program_id, *, force_due=True):
            assert program_id == "reddit_program_execution"
            assert force_due is True
            return {"program_id": program_id, "processed": 1, "status": "completed"}

    async def fake_attempt_detail(_attempt_id: str):
        return {
            "artifacts": [
                {"artifact_type": "screenshot", "download_url": "/forensics/artifacts/comment-shot"},
            ]
        }

    monkeypatch.setattr(main, "RedditProgramStore", _FakeStore)
    monkeypatch.setattr(main, "RedditProgramOrchestrator", _FakeOrchestrator)
    monkeypatch.setattr(main, "get_forensic_attempt_detail", fake_attempt_detail)

    run = asyncio.run(
        main._execute_reddit_execution_request(
            {
                "actors": [{"profile_name": "reddit_amy"}],
                "target": {
                    "kind": "post",
                    "strategy": "discover",
                    "subreddit": "womenshealth",
                    "discovery_constraints": {"subreddits": ["womenshealth"], "keywords": ["biopsy"]},
                },
                "action": {"type": "comment", "params": {"text": "supportive comment"}},
                "verification": {
                    "require_success_confirmed": True,
                    "require_attempt_id": True,
                    "required_evidence_summary": True,
                    "required_target_reference": True,
                },
            },
            run_id="reddit_execution_test",
        )
    )

    assert run["run_id"] == "reddit_execution_test"
    assert run["success"] is True
    assert run["results"][0]["screenshot_artifact_url"] == "/forensics/artifacts/comment-shot"
    assert temp_execution_store.get_run("reddit_execution_test")["request"]["action"]["type"] == "comment"
