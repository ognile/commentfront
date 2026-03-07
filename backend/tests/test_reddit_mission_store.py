from pathlib import Path
import sys
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reddit_mission_store import RedditMissionStore


def test_once_mission_completes_and_clears_next_run(tmp_path: Path):
    store = RedditMissionStore(file_path=str(tmp_path / "reddit_missions.json"))
    mission = store.create_mission(
        {
            "profile_name": "reddit_mary",
            "action": "browse_feed",
            "brief": "check feed",
            "cadence": {"type": "once"},
        }
    )

    assert mission["status"] == "active"

    updated = store.mark_run_result(mission["id"], {"success": True}, ran_at=datetime(2026, 3, 7, 9, 0, 0))
    assert updated is not None
    assert updated["status"] == "completed"
    assert updated["next_run_at"] is None


def test_interval_mission_rolls_next_run_forward(tmp_path: Path):
    store = RedditMissionStore(file_path=str(tmp_path / "reddit_missions.json"))
    mission = store.create_mission(
        {
            "profile_name": "reddit_neera",
            "action": "comment_post",
            "brief": "support thread",
            "cadence": {"type": "interval_hours", "interval_hours": 6},
        }
    )

    updated = store.mark_run_result(mission["id"], {"success": True}, ran_at=datetime(2026, 3, 7, 12, 0, 0))
    assert updated is not None
    assert updated["status"] == "active"
    assert updated["next_run_at"] == "2026-03-07T18:00:00"
