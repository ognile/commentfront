import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import datetime, timedelta

from queue_manager import find_duplicate_text_conflicts


def test_duplicate_guard_checks_current_campaign_and_history_window():
    now = datetime.utcnow()

    candidate_jobs = [
        {"type": "reply_comment", "text": "this is a unique lowercase reply"},
        {"type": "reply_comment", "text": "this is a unique lowercase reply!"},
        {"type": "reply_comment", "text": "completely different text"},
    ]

    history = [
        {
            "id": "recent_campaign",
            "completed_at": (now - timedelta(days=5)).isoformat(),
            "results": [
                {
                    "job_index": 0,
                    "text": "completely different text",
                    "success": True,
                }
            ],
        },
        {
            "id": "old_campaign",
            "completed_at": (now - timedelta(days=40)).isoformat(),
            "results": [
                {
                    "job_index": 0,
                    "text": "this is a unique lowercase reply",
                    "success": True,
                }
            ],
        },
    ]

    conflicts = find_duplicate_text_conflicts(
        candidate_jobs=candidate_jobs,
        history=history,
        now=now,
        lookback_days=30,
    )

    assert any(c["scope"] == "current_campaign" for c in conflicts)
    assert any(
        c["scope"] == "history_30d" and c.get("history_campaign_id") == "recent_campaign"
        for c in conflicts
    )
    assert not any(c.get("history_campaign_id") == "old_campaign" for c in conflicts)
