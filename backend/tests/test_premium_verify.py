import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from premium_verify import (
    evaluate_evidence_contract,
    evaluate_verification_state,
    initialize_verification_state,
    register_progress,
)


RUN_SPEC = {
    "profile_name": "Vanessa Hines",
    "feed_plan": {"total_posts": 4, "character_posts": 3, "ambient_posts": 1},
    "engagement_recipe": {"likes_per_cycle": 2, "shares_per_cycle": 1, "replies_per_cycle": 1, "share_target": "own_feed"},
    "verification_contract": {
        "require_evidence": True,
        "require_target_reference": True,
        "require_action_metadata": True,
        "require_before_after_screenshots": True,
        "require_profile_identity": True,
    },
}


def _evidence(
    *,
    run_id: str,
    action_key: str,
    profile_name: str,
    completed_count: int,
    step_id: str,
) -> dict:
    action_type = {
        "feed_posts": "feed_post",
        "group_posts": "group_post",
        "likes": "likes",
        "shares": "shares",
        "comment_replies": "comment_replies",
    }[action_key]
    confirmation = {"profile_identity_confirmed": True}
    if action_key in ("feed_posts", "group_posts"):
        confirmation["post_visible_or_permalink_resolved"] = True
    elif action_key == "likes":
        confirmation["like_state_active"] = True
    elif action_key == "shares":
        confirmation.update(
            {
                "share_confirmed": True,
                "share_destination": "own_feed",
                "share_destination_confirmed": True,
            }
        )
    elif action_key == "comment_replies":
        confirmation["reply_visible_under_thread"] = True

    payload = {
        "action_id": str(uuid.uuid4()),
        "timestamp": "2026-02-24T12:00:00Z",
        "run_id": run_id,
        "step_id": step_id,
        "verification_key": action_key,
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
            "action_trace": ["CLICK", "DONE"],
            "selector_trace": [{"tag": "DIV", "aria_label": "button", "text": "ok"}],
        },
        "result_state": {"success": True, "completed_count": completed_count, "errors": []},
        "confirmation": confirmation,
    }
    if action_key in ("feed_posts", "group_posts"):
        payload["rules_validation"] = {"ok": True, "violations": []}
    return payload


def test_verification_state_tracks_counts_and_mix():
    state = initialize_verification_state(RUN_SPEC)

    # 4 feed posts, 3 character + 1 ambient.
    register_progress(state, key="feed_posts", count=1, post_kind="character")
    register_progress(state, key="feed_posts", count=1, post_kind="character")
    register_progress(state, key="feed_posts", count=1, post_kind="character")
    register_progress(state, key="feed_posts", count=1, post_kind="ambient")

    register_progress(state, key="group_posts", count=4)
    register_progress(state, key="likes", count=8)
    register_progress(state, key="shares", count=4)
    register_progress(state, key="comment_replies", count=4)

    verdict = evaluate_verification_state(state)
    assert verdict["passed"] is True
    assert verdict["missing"] == []
    assert verdict["pass_matrix"]["feed_posts"] == "4/4"
    assert verdict["pass_matrix"]["character_posts"] == "3/3"
    assert verdict["pass_matrix"]["ambient_posts"] == "1/1"


def test_evidence_contract_passes_for_strict_pilot_counts():
    run_id = "run_strict"
    evidence = []
    for cycle in range(4):
        evidence.append(_evidence(run_id=run_id, action_key="feed_posts", profile_name="Vanessa Hines", completed_count=1, step_id=f"c{cycle}_feed"))
        evidence.append(_evidence(run_id=run_id, action_key="group_posts", profile_name="Vanessa Hines", completed_count=1, step_id=f"c{cycle}_group"))
        evidence.append(_evidence(run_id=run_id, action_key="likes", profile_name="Vanessa Hines", completed_count=2, step_id=f"c{cycle}_likes"))
        evidence.append(_evidence(run_id=run_id, action_key="shares", profile_name="Vanessa Hines", completed_count=1, step_id=f"c{cycle}_shares"))
        evidence.append(_evidence(run_id=run_id, action_key="comment_replies", profile_name="Vanessa Hines", completed_count=1, step_id=f"c{cycle}_replies"))

    verdict = evaluate_evidence_contract(run_id=run_id, run_spec=RUN_SPEC, evidence_items=evidence)
    assert verdict["passed"] is True
    assert verdict["invalid_evidence"] == []
    assert verdict["valid_counts"]["feed_posts"] == 4
    assert verdict["valid_counts"]["group_posts"] == 4
    assert verdict["valid_counts"]["likes"] == 8
    assert verdict["valid_counts"]["shares"] == 4
    assert verdict["valid_counts"]["comment_replies"] == 4


def test_evidence_contract_fails_on_share_destination_mismatch():
    run_id = "run_bad_share"
    evidence = [
        _evidence(run_id=run_id, action_key="feed_posts", profile_name="Vanessa Hines", completed_count=1, step_id="feed"),
        _evidence(run_id=run_id, action_key="group_posts", profile_name="Vanessa Hines", completed_count=1, step_id="group"),
        _evidence(run_id=run_id, action_key="likes", profile_name="Vanessa Hines", completed_count=2, step_id="likes"),
        _evidence(run_id=run_id, action_key="comment_replies", profile_name="Vanessa Hines", completed_count=1, step_id="replies"),
    ]
    bad_share = _evidence(run_id=run_id, action_key="shares", profile_name="Vanessa Hines", completed_count=1, step_id="shares")
    bad_share["confirmation"]["share_destination"] = "group"
    bad_share["confirmation"]["share_destination_confirmed"] = False
    evidence.append(bad_share)

    verdict = evaluate_evidence_contract(run_id=run_id, run_spec=RUN_SPEC, evidence_items=evidence)
    assert verdict["passed"] is False
    assert verdict["invalid_evidence"]
    assert any(item["action_key"] == "shares" for item in verdict["invalid_evidence"])
