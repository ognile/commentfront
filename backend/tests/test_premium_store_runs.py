import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from premium_store import PremiumStore


def _run_spec() -> dict:
    return {
        "profile_name": "Vanessa Hines",
        "feed_plan": {
            "total_posts": 4,
            "character_posts": 3,
            "ambient_posts": 1,
        },
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
        "schedule": {
            "duration_days": 2,
            "timezone": "America/New_York",
            "random_windows": [
                {"start_hour": 8, "end_hour": 12},
                {"start_hour": 18, "end_hour": 22},
            ],
        },
        "verification_contract": {},
        "metadata": {},
    }


def test_create_run_builds_cycles_and_mix(tmp_path):
    store = PremiumStore(file_path=str(tmp_path / "premium_state.json"))
    run = store.create_run(run_spec=_run_spec(), created_by="tester")

    assert run["status"] == "scheduled"
    assert len(run["cycles"]) == 4

    post_kinds = [c["post_kind"] for c in run["cycles"]]
    assert post_kinds.count("character") == 3
    assert post_kinds.count("ambient") == 1

    assert run["next_execute_at"] is not None
    assert run["verification_state"]["required"]["feed_posts"] == 4
    assert run["verification_state"]["required"]["likes"] == 8


def test_due_cycles_only_returns_schedulable_runs(tmp_path):
    store = PremiumStore(file_path=str(tmp_path / "premium_state.json"))
    run = store.create_run(run_spec=_run_spec(), created_by="tester")

    # Force first cycle due in the past.
    first_cycle = run["cycles"][0]
    first_cycle["scheduled_at"] = "2000-01-01T00:00:00Z"
    store.state["runs"][run["id"]]["cycles"][0] = first_cycle
    store.save()

    due = store.get_due_cycles()
    assert due
    assert due[0][0] == run["id"]
    assert due[0][1] == 0
