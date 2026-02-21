import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
from main import AddToQueueRequest, DraftRequest


VALID_URL = "https://www.facebook.com/permalink.php?story_fbid=123456&id=987654321"


@pytest.fixture(autouse=True)
def isolate_queue_and_drafts(tmp_path, monkeypatch):
    old_queue_path = main.queue_manager.file_path
    old_draft_path = main.draft_manager.file_path

    main.queue_manager.file_path = str(tmp_path / "campaign_queue.json")
    main.queue_manager.campaigns = {}
    main.queue_manager.history = []
    main.queue_manager.processor_state = {
        "is_running": False,
        "current_campaign_id": None,
        "last_processed_at": None,
    }
    main.queue_manager.save()

    main.draft_manager.file_path = str(tmp_path / "campaign_drafts.json")
    main.draft_manager.drafts = {}
    main.draft_manager.save()

    main.queue_idempotency_index.clear()

    async def _noop_broadcast(*_args, **_kwargs):
        return None

    monkeypatch.setattr(main, "broadcast_update", _noop_broadcast)

    yield

    main.queue_manager.file_path = old_queue_path
    main.draft_manager.file_path = old_draft_path
    main.queue_manager.campaigns = {}
    main.queue_manager.history = []
    main.draft_manager.drafts = {}
    main.queue_idempotency_index.clear()


def test_queue_duplicate_conflict_returns_warning_and_enqueues():
    main.queue_manager.history = [
        {
            "id": "historical_campaign",
            "created_at": datetime.utcnow().isoformat(),
            "completed_at": datetime.utcnow().isoformat(),
            "results": [{"job_index": 0, "text": "shared duplicate text"}],
        }
    ]
    request = AddToQueueRequest(
        url=VALID_URL,
        comments=["shared duplicate text"],
        duration_minutes=30,
        filter_tags=None,
        enable_warmup=True,
        profile_name=None,
    )

    response = asyncio.run(main.add_to_queue(request, current_user={"username": "tester"}))

    assert response["id"] in main.queue_manager.campaigns
    assert "warnings" in response
    assert response["warnings"][0]["code"] == "duplicate_text_guard"
    assert response["warnings"][0]["duplicate_conflicts"]


def test_queue_structural_invalid_payload_still_returns_400():
    request = AddToQueueRequest(
        url="not-a-valid-url",
        comments=["valid text"],
        duration_minutes=30,
        filter_tags=None,
        enable_warmup=True,
        profile_name=None,
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(main.add_to_queue(request, current_user={"username": "tester"}))

    assert exc_info.value.status_code == 400
    assert isinstance(exc_info.value.detail, dict)
    assert exc_info.value.detail.get("message") == "Queue validation failed"


def test_draft_crud_and_publish_delete_flow():
    created = asyncio.run(
        main.create_draft(
            DraftRequest(
                url=VALID_URL,
                comments=["draft comment 1"],
                duration_minutes=15,
                filter_tags=["team-a"],
                enable_warmup=True,
            ),
            current_user={"username": "alice"},
        )
    )
    draft_id = created["id"]

    listed = asyncio.run(main.get_drafts(current_user={"username": "alice"}))
    listed_ids = [d["id"] for d in listed["drafts"]]
    assert draft_id in listed_ids

    updated = asyncio.run(
        main.update_draft(
            draft_id,
            DraftRequest(
                url=VALID_URL,
                comments=["draft comment 1", "draft comment 2"],
                duration_minutes=20,
                filter_tags=["team-b"],
                enable_warmup=True,
            ),
            current_user={"username": "bob"},
        )
    )
    assert updated["updated_by"] == "bob"
    assert len(updated["comments"]) == 2

    published = asyncio.run(main.publish_draft(draft_id, current_user={"username": "carol"}))
    assert published["success"] is True
    assert published["draft_id"] == draft_id
    assert main.draft_manager.get_draft(draft_id) is None
    assert published["campaign"]["id"] in main.queue_manager.campaigns

    another = asyncio.run(
        main.create_draft(
            DraftRequest(
                url=VALID_URL,
                comments=["delete me"],
                duration_minutes=10,
                filter_tags=None,
                enable_warmup=True,
            ),
            current_user={"username": "alice"},
        )
    )
    deleted = asyncio.run(main.delete_draft(another["id"], current_user={"username": "alice"}))
    assert deleted["success"] is True
    assert main.draft_manager.get_draft(another["id"]) is None


def test_inflight_submit_clicked_recovery_marks_uncertain_no_repost(monkeypatch):
    campaign = main.queue_manager.add_campaign(
        url=VALID_URL,
        comments=["recover me"],
        jobs=None,
        duration_minutes=30,
        username="tester",
        filter_tags=None,
        enable_warmup=True,
        profile_name=None,
    )
    campaign_id = campaign["id"]
    main.queue_manager.set_inflight_job(
        campaign_id,
        job_index=0,
        profile_name="profile_a",
        comment_hash="hash_1",
        phase="submit_clicked",
        attempt_id="attempt_1",
        metadata={},
    )

    class FakeFacebookSession:
        def __init__(self, _profile_name: str):
            self.profile_name = _profile_name

        def load(self) -> bool:
            return True

    async def fake_reconcile_comment_submission(**_kwargs):
        return {"found": None, "confidence": 0.11, "reason": "inconclusive after restart"}

    async def fail_if_post_attempted(*_args, **_kwargs):
        raise AssertionError("no repost is allowed during inflight recovery")

    class FakeProfileManager:
        def __init__(self):
            self.calls = []

        def mark_profile_used(self, **kwargs):
            self.calls.append(kwargs)

    monkeypatch.setattr(main, "FacebookSession", FakeFacebookSession)
    monkeypatch.setattr(main, "reconcile_comment_submission", fake_reconcile_comment_submission)
    monkeypatch.setattr(main, "post_comment_verified", fail_if_post_attempted)

    profile_manager = FakeProfileManager()
    live_campaign = main.queue_manager.get_campaign(campaign_id)
    assert live_campaign is not None

    asyncio.run(
        main.queue_processor._recover_inflight_checkpoint(
            campaign=live_campaign,
            jobs=live_campaign["jobs"],
            url=live_campaign["url"],
            profile_manager=profile_manager,
        )
    )

    recovered_campaign = main.queue_manager.get_campaign(campaign_id)
    assert recovered_campaign is not None
    assert recovered_campaign["inflight_job"] is None
    assert len(recovered_campaign["results"]) == 1

    recovery_result = recovered_campaign["results"][0]
    assert recovery_result["job_index"] == 0
    assert recovery_result["method"] == "uncertain_no_repost"
    assert recovery_result["success"] is False
    assert recovery_result["recovered_from_inflight"] is True

