import sys
from datetime import datetime, timezone
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
    assert due[0][1] == int(first_cycle["index"])


def test_due_cycles_skips_runs_with_active_running_cycle(tmp_path):
    store = PremiumStore(file_path=str(tmp_path / "premium_state.json"))
    run = store.create_run(run_spec=_run_spec(), created_by="tester")

    # Force first cycle as active and second cycle as due.
    store.state["runs"][run["id"]]["status"] = "in_progress"
    store.state["runs"][run["id"]]["cycles"][0]["status"] = "running"
    store.state["runs"][run["id"]]["cycles"][1]["scheduled_at"] = "2000-01-01T00:00:00Z"
    store.save()

    due = store.get_due_cycles()
    assert due == []


def test_cancel_run_marks_running_cycles_cancelled(tmp_path):
    store = PremiumStore(file_path=str(tmp_path / "premium_state.json"))
    run = store.create_run(run_spec=_run_spec(), created_by="tester")

    store.state["runs"][run["id"]]["status"] = "in_progress"
    store.state["runs"][run["id"]]["cycles"][0]["status"] = "running"
    store.save()

    cancelled = store.cancel_run(run["id"], actor="tester")
    assert cancelled is not None
    assert cancelled["status"] == "cancelled"
    assert cancelled["next_execute_at"] is None
    assert cancelled["cycles"][0]["status"] == "cancelled"
    assert cancelled["cycles"][0]["completed_at"] is not None


def test_enqueue_or_create_queues_same_profile_and_promotes_fifo(tmp_path):
    store = PremiumStore(file_path=str(tmp_path / "premium_state.json"))

    first = store.enqueue_or_create_run(run_spec=_run_spec(), created_by="tester")
    second = store.enqueue_or_create_run(run_spec=_run_spec(), created_by="tester")
    third = store.enqueue_or_create_run(run_spec=_run_spec(), created_by="tester")

    assert first["status"] == "scheduled"
    assert second["status"] == "queued"
    assert third["status"] == "queued"
    assert second["queue_position"] == 1
    assert third["queue_position"] == 2
    assert second["blocked_by_run_id"] == first["id"]
    assert third["blocked_by_run_id"] == second["id"]
    assert second["admission_policy"] == "queue_behind"

    store.set_run_status(first["id"], "completed")
    second_after = store.get_run(second["id"])
    third_after = store.get_run(third["id"])
    assert second_after["status"] == "scheduled"
    assert second_after["queue_position"] == 0
    assert second_after["blocked_by_run_id"] is None
    assert third_after["status"] == "queued"
    assert third_after["queue_position"] == 1
    assert third_after["blocked_by_run_id"] == second["id"]

    store.set_run_status(second["id"], "completed")
    third_final = store.get_run(third["id"])
    assert third_final["status"] == "scheduled"
    assert third_final["queue_position"] == 0
    assert third_final["blocked_by_run_id"] is None


def test_defer_cycle_reschedules_pending_with_reason(tmp_path):
    store = PremiumStore(file_path=str(tmp_path / "premium_state.json"))
    run = store.create_run(run_spec=_run_spec(), created_by="tester")
    cycle_index = run["cycles"][0]["index"]

    store.set_cycle_status(run_id=run["id"], cycle_index=cycle_index, status="running")
    deferred = store.defer_cycle(
        run_id=run["id"],
        cycle_index=cycle_index,
        delay_seconds=90,
        reason="transient tunnel outage during feed_posts",
        metadata={"action": "feed_posts"},
    )

    assert deferred is not None
    assert deferred["status"] == "pending"
    assert deferred["started_at"] is None
    assert deferred["completed_at"] is None
    assert "tunnel outage" in str(deferred["error"]).lower()
    assert deferred.get("results")
    assert deferred["results"][-1]["type"] == "deferred"
    retry_at = deferred.get("scheduled_at")
    assert isinstance(retry_at, str) and retry_at

    retry_dt = datetime.fromisoformat(retry_at.replace("Z", "+00:00"))
    assert retry_dt > datetime.now(timezone.utc)
