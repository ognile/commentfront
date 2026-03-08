import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
import profile_manager
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


def test_test_campaign_duplicate_guard_blocks_history_conflict(monkeypatch):
    class FakeProfileManager:
        def get_eligible_profiles(self, **_kwargs):
            return ["profile_a"]

        def mark_profile_used(self, **_kwargs):
            return None

        def mark_profile_restricted(self, **_kwargs):
            return None

    main.queue_manager.history = [
        {
            "id": "historical_campaign",
            "created_at": datetime.utcnow().isoformat(),
            "completed_at": datetime.utcnow().isoformat(),
            "results": [{"job_index": 0, "text": "shared duplicate text", "success": True}],
        }
    ]
    monkeypatch.setattr(profile_manager, "get_profile_manager", lambda: FakeProfileManager())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            main.run_test_campaign(
                main.TestCampaignRequest(
                    url=VALID_URL,
                    comments=["shared duplicate text"],
                    filter_tags=None,
                    enable_warmup=True,
                ),
                current_user={"username": "tester"},
            )
        )

    assert exc_info.value.status_code == 409
    assert isinstance(exc_info.value.detail, dict)
    assert exc_info.value.detail.get("message") == "duplicate_text_guard blocked test campaign"
    assert exc_info.value.detail.get("duplicate_conflicts")


def test_auto_retry_success_marks_job_exhausted_to_prevent_repost():
    campaign_id = "campaign_auto_retry_fix"
    main.queue_manager.history = [
        {
            "id": campaign_id,
            "status": "completed",
            "url": VALID_URL,
            "success_count": 0,
            "total_count": 1,
            "created_at": datetime.utcnow().isoformat(),
            "completed_at": datetime.utcnow().isoformat(),
            "results": [
                {
                    "job_index": 0,
                    "text": "same text",
                    "comment": "same text",
                    "success": False,
                }
            ],
            "auto_retry": {
                "status": "scheduled",
                "current_round": 0,
                "max_rounds": 4,
                "next_retry_at": datetime.utcnow().isoformat(),
                "schedule_seconds": [300, 1800, 7200, 21600],
                "failed_jobs": [
                    {
                        "job_index": 0,
                        "comment": "same text",
                        "excluded_profiles": [],
                        "last_profile": "profile_old",
                        "exhausted": False,
                    }
                ],
            },
        }
    ]

    main.queue_manager.record_retry_attempt(
        campaign_id=campaign_id,
        job_index=0,
        profile="profile_new",
        round_num=0,
        success=True,
        error=None,
        was_restriction=False,
    )

    updated = main.queue_manager.get_campaign_from_history(campaign_id)
    assert updated is not None
    failed_job = updated["auto_retry"]["failed_jobs"][0]
    assert failed_job["exhausted"] is True
    assert failed_job["last_profile"] == "profile_new"
    remaining = [fj for fj in updated["auto_retry"]["failed_jobs"] if not fj.get("exhausted")]
    assert remaining == []


def test_due_auto_retry_runs_even_with_pending_campaign(monkeypatch):
    pending = main.queue_manager.add_campaign(
        url=VALID_URL,
        comments=["pending job"],
        jobs=None,
        duration_minutes=30,
        username="tester",
        filter_tags=None,
        enable_warmup=True,
        profile_name=None,
    )

    retry_campaign_id = "campaign_due_retry"
    main.queue_manager.history = [
        {
            "id": retry_campaign_id,
            "status": "completed",
            "url": VALID_URL,
            "success_count": 0,
            "total_count": 1,
            "created_at": datetime.utcnow().isoformat(),
            "completed_at": datetime.utcnow().isoformat(),
            "results": [
                {
                    "job_index": 0,
                    "text": "recover me",
                    "comment": "recover me",
                    "success": False,
                    "method": "verification_inconclusive",
                    "error": "Step 5 INCONCLUSIVE - visual confirmation failed",
                }
            ],
            "auto_retry": {
                "status": "scheduled",
                "current_round": 0,
                "max_rounds": 4,
                "next_retry_at": datetime.utcnow().isoformat(),
                "schedule_seconds": [300, 1800, 7200, 21600],
                "failed_jobs": [
                    {
                        "job_index": 0,
                        "comment": "recover me",
                        "excluded_profiles": [],
                        "last_profile": "profile_old",
                        "exhausted": False,
                    }
                ],
            },
        }
    ]

    processed = []

    async def fake_process_auto_retry(campaign):
        processed.append(campaign["id"])

    async def fake_check_proxy_health():
        return {"healthy": True, "ip": "1.1.1.1"}

    monkeypatch.setattr(main.queue_processor, "_process_auto_retry", fake_process_auto_retry)
    monkeypatch.setattr(main, "check_proxy_health", fake_check_proxy_health)

    ran = asyncio.run(main.queue_processor._run_retry_iteration())

    assert ran is True
    assert processed == [retry_campaign_id]
    assert pending["id"] in main.queue_manager.campaigns


def test_auto_retry_reconciles_existing_comment_before_repost(monkeypatch):
    campaign_id = "campaign_reconcile_retry"
    main.queue_manager.history = [
        {
            "id": campaign_id,
            "status": "completed",
            "url": VALID_URL,
            "filter_tags": [],
            "enable_warmup": True,
            "success_count": 0,
            "total_count": 1,
            "created_at": datetime.utcnow().isoformat(),
            "completed_at": datetime.utcnow().isoformat(),
            "results": [
                {
                    "job_index": 0,
                    "text": "same text",
                    "comment": "same text",
                    "success": False,
                    "method": "verification_inconclusive",
                    "error": "Step 5 INCONCLUSIVE - visual confirmation failed",
                    "profile_name": "profile_old",
                }
            ],
            "auto_retry": {
                "status": "scheduled",
                "current_round": 0,
                "max_rounds": 4,
                "next_retry_at": datetime.utcnow().isoformat(),
                "schedule_seconds": [300, 1800, 7200, 21600],
                "failed_jobs": [
                    {
                        "job_index": 0,
                        "comment": "same text",
                        "excluded_profiles": [],
                        "last_profile": "profile_old",
                        "exhausted": False,
                    }
                ],
            },
        }
    ]

    class FakeFacebookSession:
        def __init__(self, profile_name: str):
            self.profile_name = profile_name

        def load(self) -> bool:
            return True

    class FakeProfileManager:
        def __init__(self):
            self.reserved = set()

        async def reserve_profile(self, profile_name: str) -> bool:
            if profile_name in self.reserved:
                return False
            self.reserved.add(profile_name)
            return True

        async def release_profile(self, profile_name: str):
            self.reserved.discard(profile_name)

        def get_eligible_profiles(self, **_kwargs):
            raise AssertionError("repost selection should not happen when reconciliation succeeds")

        def mark_profile_used(self, **_kwargs):
            raise AssertionError("no new posting attempt should be recorded")

        def mark_profile_restricted(self, **_kwargs):
            raise AssertionError("no restriction update expected")

    async def fake_reconcile_comment_submission(**_kwargs):
        return {"found": True, "confidence": 0.97, "reason": "comment text verified from local DOM evidence"}

    async def fail_if_post_attempted(*_args, **_kwargs):
        raise AssertionError("no repost should happen when reconciliation succeeds")

    monkeypatch.setattr(main, "FacebookSession", FakeFacebookSession)
    monkeypatch.setattr(profile_manager, "get_profile_manager", lambda: FakeProfileManager())
    monkeypatch.setattr(main, "reconcile_comment_submission", fake_reconcile_comment_submission)
    monkeypatch.setattr(main, "post_comment_verified", fail_if_post_attempted)

    campaign = main.queue_manager.get_campaign_from_history(campaign_id)
    assert campaign is not None

    asyncio.run(main.queue_processor._process_auto_retry(campaign))

    updated = main.queue_manager.get_campaign_from_history(campaign_id)
    assert updated is not None
    assert updated["success_count"] == 1
    assert updated["auto_retry"]["status"] == "completed"
    assert any(
        result.get("method") == "reconciled_existing_comment" and result.get("success")
        for result in updated["results"]
    )


def test_retry_all_does_not_exhaust_early_on_infrastructure_failures(monkeypatch):
    campaign_id = "campaign_bulk_retry_infra"
    main.queue_manager.history = [
        {
            "id": campaign_id,
            "status": "completed",
            "url": VALID_URL,
            "filter_tags": [],
            "enable_warmup": False,
            "success_count": 0,
            "total_count": 1,
            "created_at": datetime.utcnow().isoformat(),
            "completed_at": datetime.utcnow().isoformat(),
            "results": [
                {
                    "job_index": 0,
                    "text": "recover me",
                    "comment": "recover me",
                    "success": False,
                    "profile_name": "profile_0",
                }
            ],
        }
    ]

    profiles = [f"profile_{i}" for i in range(6)]

    class FakeFacebookSession:
        def __init__(self, profile_name: str):
            self.profile_name = profile_name

        def load(self) -> bool:
            return True

        def has_valid_cookies(self) -> bool:
            return True

    class FakeProfileManager:
        def __init__(self):
            self.reserved = set()

        def get_eligible_profiles(self, *, exclude_profiles=None, count=5, **_kwargs):
            exclude = set(exclude_profiles or [])
            return [profile for profile in profiles if profile not in exclude][:count]

        async def reserve_profile(self, profile_name: str) -> bool:
            if profile_name in self.reserved:
                return False
            self.reserved.add(profile_name)
            return True

        async def release_profile(self, profile_name: str):
            self.reserved.discard(profile_name)

        def mark_profile_used(self, **_kwargs):
            return None

        def mark_profile_restricted(self, **_kwargs):
            raise AssertionError("infra failures should not restrict profiles")

    async def fake_post_comment_verified(**_kwargs):
        return {
            "success": False,
            "verified": False,
            "method": "vision_verified",
            "error": "Page.goto: net::ERR_EMPTY_RESPONSE at https://www.facebook.com/permalink.php",
            "throttled": False,
        }

    monkeypatch.setattr(main, "FacebookSession", FakeFacebookSession)
    monkeypatch.setattr(main, "post_comment_verified", fake_post_comment_verified)

    result = asyncio.run(
        main._retry_single_campaign(
            campaign=main.queue_manager.get_campaign_from_history(campaign_id),
            campaign_index=0,
            total_campaigns=1,
            profile_manager=FakeProfileManager(),
            browser_semaphore=asyncio.Semaphore(1),
        )
    )

    assert result["jobs_succeeded"] == 0
    assert result["jobs_exhausted"] == 1
    assert result["attempts"] == 6

    updated = main.queue_manager.get_campaign_from_history(campaign_id)
    failed_results = [r for r in updated["results"] if not r.get("success")]
    assert [r.get("profile_name") for r in failed_results[-7:-1]] == profiles
    assert failed_results[-1]["error"] == "No eligible profiles remaining"


def test_retry_all_still_exhausts_early_when_post_is_dead(monkeypatch):
    campaign_id = "campaign_bulk_retry_dead_post"
    main.queue_manager.history = [
        {
            "id": campaign_id,
            "status": "completed",
            "url": VALID_URL,
            "filter_tags": [],
            "enable_warmup": False,
            "success_count": 0,
            "total_count": 2,
            "created_at": datetime.utcnow().isoformat(),
            "completed_at": datetime.utcnow().isoformat(),
            "results": [
                {
                    "job_index": 0,
                    "text": "recover me",
                    "comment": "recover me",
                    "success": False,
                    "profile_name": "profile_0",
                },
                {
                    "job_index": 1,
                    "text": "second job",
                    "comment": "second job",
                    "success": False,
                    "profile_name": "profile_1",
                },
            ],
        }
    ]

    profiles = [f"profile_{i}" for i in range(6)]

    class FakeFacebookSession:
        def __init__(self, profile_name: str):
            self.profile_name = profile_name

        def load(self) -> bool:
            return True

        def has_valid_cookies(self) -> bool:
            return True

    class FakeProfileManager:
        def __init__(self):
            self.reserved = set()

        def get_eligible_profiles(self, *, exclude_profiles=None, count=5, **_kwargs):
            exclude = set(exclude_profiles or [])
            return [profile for profile in profiles if profile not in exclude][:count]

        async def reserve_profile(self, profile_name: str) -> bool:
            if profile_name in self.reserved:
                return False
            self.reserved.add(profile_name)
            return True

        async def release_profile(self, profile_name: str):
            self.reserved.discard(profile_name)

        def mark_profile_used(self, **_kwargs):
            return None

        def mark_profile_restricted(self, **_kwargs):
            return None

    async def fake_post_comment_verified(**_kwargs):
        return {
            "success": False,
            "verified": False,
            "method": "vision_verified",
            "error": "Step 1 FAILED - Post not visible after 6 attempts",
            "throttled": False,
        }

    monkeypatch.setattr(main, "FacebookSession", FakeFacebookSession)
    monkeypatch.setattr(main, "post_comment_verified", fake_post_comment_verified)

    result = asyncio.run(
        main._retry_single_campaign(
            campaign=main.queue_manager.get_campaign_from_history(campaign_id),
            campaign_index=0,
            total_campaigns=1,
            profile_manager=FakeProfileManager(),
            browser_semaphore=asyncio.Semaphore(1),
        )
    )

    assert result["jobs_succeeded"] == 0
    assert result["jobs_exhausted"] == 2
    assert result["attempts"] == 4

    updated = main.queue_manager.get_campaign_from_history(campaign_id)
    failed_results = [r for r in updated["results"] if not r.get("success")]
    assert failed_results[-2]["error"] == "Early termination: 4 consecutive post_not_visible failures"
    assert failed_results[-1]["error"] == "Post URL appears dead (all profiles failed on prior job)"
