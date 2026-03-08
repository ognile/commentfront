import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
import profile_manager
from campaign_reliability_audit import (
    FIX_NONE,
    VERDICT_FIXES,
    VERDICT_GO,
    VERDICT_WATCHLIST,
    build_campaign_reliability_audit,
)


def _base_health(restricted: int = 0):
    return {
        "status": "healthy",
        "checks": {
            "profiles": {
                "active": 10 - restricted,
                "restricted": restricted,
                "total": 10,
            }
        },
    }


def _base_analytics(restricted: int = 0):
    return {
        "today": {"comments": 10, "success": 10, "success_rate": 100},
        "week": {"comments": 50, "success": 45, "success_rate": 90},
        "profiles": {"active": 10 - restricted, "restricted": restricted, "total": 10},
    }


def test_reliability_audit_go_for_self_healed_infra_noise():
    history = [
        {
            "id": "campaign-a",
            "url": "https://facebook.com/post-a",
            "created_by": "ops",
            "status": "completed",
            "completed_at": "2026-03-07T10:00:00Z",
            "total_count": 10,
            "success_count": 10,
            "auto_retry": {"status": "completed"},
            "results": [
                {
                    "profile_name": "Alpha One",
                    "comment": "a",
                    "success": False,
                    "error": "Page.goto: net::ERR_TUNNEL_CONNECTION_FAILED at https://facebook.com/post-a",
                    "job_index": 1,
                },
                {
                    "profile_name": "Beta Two",
                    "comment": "a",
                    "success": True,
                    "method": "auto_retry",
                    "job_index": 1,
                    "is_retry": True,
                    "auto_retry_round": 1,
                },
            ],
        },
        {
            "id": "campaign-b",
            "url": "https://facebook.com/post-b",
            "created_by": "ops",
            "status": "completed",
            "completed_at": "2026-03-07T12:00:00Z",
            "total_count": 8,
            "success_count": 8,
            "results": [],
        },
    ]

    report = build_campaign_reliability_audit(
        history=history,
        analytics_summary=_base_analytics(),
        appeal_status={"profiles": [], "total": 0},
        health_deep=_base_health(),
        lookback_days=2,
        min_total_count=6,
    )

    assert report["summary"]["verdict"] == VERDICT_GO
    assert report["summary"]["retry_attempt_count"] == 1
    assert report["summary"]["recovered_job_count"] == 1
    assert report["summary"]["unrecovered_job_count"] == 0
    assert report["fix_matrix"][0]["status"] == FIX_NONE


def test_reliability_audit_watchlist_when_retry_overhead_or_repeated_automation_rises():
    history = []
    for index in range(3):
        history.append(
            {
                "id": f"campaign-{index}",
                "url": f"https://facebook.com/post-{index}",
                "created_by": "ops",
                "status": "completed",
                "completed_at": f"2026-03-07T0{index}:00:00Z",
                "total_count": 10,
                "success_count": 10,
                "auto_retry": {"status": "completed"},
                "results": [
                    {
                        "profile_name": f"Retry User {index}",
                        "comment": "c",
                        "success": False,
                        "error": "Step 2 FAILED - Comments not opened: No \"Write a comment...\" input field or text input area is visible.",
                        "job_index": 0,
                    },
                    {
                        "profile_name": f"Retry User {index} B",
                        "comment": "c",
                        "success": True,
                        "method": "auto_retry",
                        "job_index": 0,
                        "is_retry": True,
                        "auto_retry_round": 1,
                    },
                    {
                        "profile_name": f"Retry User {index} C",
                        "comment": "d",
                        "success": False,
                        "error": "Step 2 FAILED - Comments not opened: No \"Write a comment...\" input field or text input area is visible.",
                        "job_index": 1,
                    },
                    {
                        "profile_name": f"Retry User {index} D",
                        "comment": "d",
                        "success": True,
                        "method": "auto_retry",
                        "job_index": 1,
                        "is_retry": True,
                        "auto_retry_round": 1,
                    },
                ],
            }
        )

    report = build_campaign_reliability_audit(
        history=history,
        analytics_summary=_base_analytics(),
        appeal_status={"profiles": [], "total": 0},
        health_deep=_base_health(),
        lookback_days=2,
        min_total_count=6,
    )

    assert report["summary"]["verdict"] == VERDICT_WATCHLIST
    assert report["summary"]["retry_overhead_rate"] > 15
    assert "comment-open" in report["repeated_automation_categories"]


def test_reliability_audit_requires_fixes_for_unrecovered_jobs_and_live_fallout():
    history = [
        {
            "id": "campaign-risky",
            "url": "https://facebook.com/post-risky",
            "created_by": "ops",
            "status": "completed",
            "completed_at": "2026-03-07T18:00:00Z",
            "total_count": 12,
            "success_count": 11,
            "has_retries": True,
            "results": [
                {
                    "profile_name": "Locked User",
                    "comment": "a",
                    "success": False,
                    "error": "Step 4 FAILED - Typed text not visible: The image displays account restriction notifications and does not contain a comment input field.",
                    "job_index": 4,
                },
                {
                    "profile_name": "Locked User Backup",
                    "comment": "a",
                    "success": False,
                    "error": "Account checkpoint detected",
                    "job_index": 4,
                    "is_retry": True,
                    "method": "vision_verified",
                },
            ],
        }
    ]

    report = build_campaign_reliability_audit(
        history=history,
        analytics_summary=_base_analytics(restricted=1),
        appeal_status={
            "total": 1,
            "profiles": [
                {
                    "profile_name": "Locked User",
                    "status": "restricted",
                }
            ],
        },
        health_deep=_base_health(restricted=1),
        lookback_days=2,
        min_total_count=6,
    )

    assert report["summary"]["verdict"] == VERDICT_FIXES
    assert report["summary"]["unrecovered_job_count"] == 1
    assert report["summary"]["manual_intervention_campaigns"] == 1
    assert report["fallout"]["linked_restriction_fallout"] == ["locked_user"]


def test_reliability_audit_helper_accepts_direct_call_defaults(monkeypatch):
    history = [
        {
            "id": "campaign-direct",
            "url": "https://facebook.com/post-direct",
            "created_by": "ops",
            "status": "completed",
            "completed_at": "2026-03-07T08:00:00Z",
            "total_count": 6,
            "success_count": 6,
            "results": [],
        }
    ]

    class FakeProfileManager:
        def get_analytics_summary(self):
            return _base_analytics()

        def get_all_profiles(self):
            return {}

    async def fake_health_deep():
        return _base_health()

    monkeypatch.setattr(main.queue_manager, "get_history", lambda limit=100: history)
    monkeypatch.setattr(profile_manager, "get_profile_manager", lambda: FakeProfileManager())
    monkeypatch.setattr(main, "health_deep", fake_health_deep)

    report = asyncio.run(main.build_queue_reliability_audit_response())

    assert report["summary"]["verdict"] == VERDICT_GO
    assert report["summary"]["campaign_count"] == 1


def test_analytics_summary_uses_final_delivery_and_retry_backlog(monkeypatch):
    class FakeProfileManager:
        def get_analytics_summary(self):
            return {
                "today": {"comments": 40, "success": 40, "success_rate": 100},
                "week": {"comments": 200, "success": 180, "success_rate": 90},
                "profiles": {"active": 10, "restricted": 1, "total": 11},
            }

    monkeypatch.setattr(profile_manager, "get_profile_manager", lambda: FakeProfileManager())
    monkeypatch.setattr(
        main.queue_manager,
        "get_history",
        lambda limit=100: [
            {
                "id": "delivered-campaign",
                "created_at": "2026-03-08T08:00:00Z",
                "completed_at": "2026-03-08T09:00:00Z",
                "total_count": 3,
                "success_count": 3,
                "delivery_state": "delivered",
                "remaining_failed_jobs": 0,
                "retry_overdue_seconds": 0,
            }
        ],
    )
    monkeypatch.setattr(
        main.queue_manager,
        "get_full_state",
        lambda: {
            "pending": [
                {
                    "id": "recovering-campaign",
                    "created_at": "2026-03-08T10:00:00Z",
                    "total_count": 4,
                    "success_count": 2,
                    "delivery_state": "recovering",
                    "remaining_failed_jobs": 2,
                    "retry_overdue_seconds": 600,
                }
            ]
        },
    )

    summary = asyncio.run(main.get_analytics_summary(current_user={"username": "tester"}))

    assert summary["today"]["comments"] == 7
    assert summary["today"]["success"] == 5
    assert round(summary["today"]["success_rate"], 2) == round(5 / 7 * 100, 2)
    assert summary["retry_backlog"] == {"campaigns": 1, "jobs": 2}
    assert summary["overdue_retries"] == {"campaigns": 1, "jobs": 2}
    assert summary["attempt_today"]["comments"] == 40
