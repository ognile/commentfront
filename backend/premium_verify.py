"""
Verification contract utilities for premium automation runs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional


ACTION_KEY_TO_EVIDENCE_TYPE = {
    "feed_posts": "feed_post",
    "group_posts": "group_post",
    "likes": "likes",
    "shares": "shares",
    "comment_replies": "comment_replies",
}

EVIDENCE_METRIC_KEYS = ("feed_posts", "group_posts", "likes", "shares", "comment_replies")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_required_totals(run_spec: Dict) -> Dict[str, int]:
    feed_plan = run_spec.get("feed_plan", {})
    engagement = run_spec.get("engagement_recipe", {})
    contract = run_spec.get("verification_contract", {})

    total_posts = int(feed_plan.get("total_posts", 0))
    required = {
        "feed_posts": int(contract.get("required_feed_posts") or total_posts),
        "group_posts": int(contract.get("required_group_posts") or total_posts),
        "likes": int(contract.get("required_likes") or (total_posts * int(engagement.get("likes_per_cycle", 0)))),
        "shares": int(contract.get("required_shares") or (total_posts * int(engagement.get("shares_per_cycle", 0)))),
        "comment_replies": int(contract.get("required_comment_replies") or (total_posts * int(engagement.get("replies_per_cycle", 0)))),
        "character_posts": int(contract.get("required_character_posts") or int(feed_plan.get("character_posts", 0))),
        "ambient_posts": int(contract.get("required_ambient_posts") or int(feed_plan.get("ambient_posts", 0))),
    }
    return required


def initialize_verification_state(run_spec: Dict) -> Dict:
    required = build_required_totals(run_spec)
    observed = {k: 0 for k in required.keys()}
    return {
        "required": required,
        "observed": observed,
        "evidence": [],
        "last_updated": _utc_iso(),
    }


def register_progress(
    state: Dict,
    *,
    key: str,
    count: int,
    post_kind: Optional[str] = None,
    evidence: Optional[Dict] = None,
) -> Dict:
    observed = state.setdefault("observed", {})
    observed[key] = int(observed.get(key, 0)) + int(count)

    if key == "feed_posts" and post_kind:
        if post_kind == "character":
            observed["character_posts"] = int(observed.get("character_posts", 0)) + int(count)
        elif post_kind == "ambient":
            observed["ambient_posts"] = int(observed.get("ambient_posts", 0)) + int(count)

    if evidence:
        state.setdefault("evidence", []).append(evidence)
    state["last_updated"] = _utc_iso()
    return state


def build_pass_matrix(state: Dict) -> Dict[str, str]:
    required = state.get("required", {})
    observed = state.get("observed", {})
    matrix: Dict[str, str] = {}
    for key, required_count in required.items():
        matrix[key] = f"{int(observed.get(key, 0))}/{int(required_count)}"
    return matrix


def evaluate_verification_state(state: Dict) -> Dict:
    required = state.get("required", {})
    observed = state.get("observed", {})

    missing = []
    for key, required_count in required.items():
        got = int(observed.get(key, 0))
        if got < int(required_count):
            missing.append({
                "metric": key,
                "required": int(required_count),
                "observed": got,
            })

    return {
        "passed": len(missing) == 0,
        "missing": missing,
        "pass_matrix": build_pass_matrix(state),
        "evaluated_at": _utc_iso(),
    }


def _evidence_matches_action_key(evidence: Dict, action_key: str) -> bool:
    if str(evidence.get("verification_key") or "").strip().lower() == action_key:
        return True

    action_type = str(evidence.get("action_type") or "").strip().lower()
    mapped = ACTION_KEY_TO_EVIDENCE_TYPE.get(action_key, action_key)
    return action_type == mapped or action_type == action_key


def validate_action_evidence(
    *,
    evidence: Dict,
    action_key: str,
    run_id: str,
    expected_profile: str,
    verification_contract: Optional[Dict],
) -> Dict:
    contract = verification_contract or {}
    require_evidence = bool(contract.get("require_evidence", True))
    if not require_evidence:
        return {"ok": True, "missing": [], "errors": []}

    require_target_reference = bool(contract.get("require_target_reference", True))
    require_action_metadata = bool(contract.get("require_action_metadata", True))
    require_before_after_screenshots = bool(contract.get("require_before_after_screenshots", True))
    require_profile_identity = bool(contract.get("require_profile_identity", True))

    missing: List[str] = []
    errors: List[str] = []

    def _present(path: str, value) -> None:
        if value is None:
            missing.append(path)
            return
        if isinstance(value, str) and not value.strip():
            missing.append(path)

    _present("action_id", evidence.get("action_id"))
    _present("timestamp", evidence.get("timestamp"))
    _present("run_id", evidence.get("run_id"))
    _present("step_id", evidence.get("step_id"))

    if require_target_reference:
        target_url = evidence.get("target_url")
        target_id = evidence.get("target_id")
        if not (target_url or target_id):
            missing.append("target_url_or_target_id")

    if require_before_after_screenshots:
        _present("before_screenshot", evidence.get("before_screenshot"))
        _present("after_screenshot", evidence.get("after_screenshot"))

    method = evidence.get("action_method")
    if require_action_metadata:
        if not isinstance(method, dict):
            missing.append("action_method")
        else:
            _present("action_method.engine", method.get("engine"))
            _present("action_method.final_status", method.get("final_status"))
            if method.get("steps_count") is None:
                missing.append("action_method.steps_count")
            if not isinstance(method.get("action_trace"), list):
                missing.append("action_method.action_trace")

    result_state = evidence.get("result_state")
    if not isinstance(result_state, dict):
        missing.append("result_state")
    else:
        if not bool(result_state.get("success")):
            errors.append("result_state.success=false")
        if result_state.get("completed_count") is None:
            missing.append("result_state.completed_count")

    confirmation = evidence.get("confirmation")
    if not isinstance(confirmation, dict):
        missing.append("confirmation")
        confirmation = {}

    if require_profile_identity:
        if not bool(confirmation.get("profile_identity_confirmed")):
            errors.append("confirmation.profile_identity_confirmed=false")
        if expected_profile and str(evidence.get("profile_name") or "").strip().lower() != expected_profile.lower():
            errors.append("profile_name_mismatch")

    # Action-specific hard confirmations.
    if action_key in ("feed_posts", "group_posts"):
        if not bool(confirmation.get("post_visible_or_permalink_resolved")):
            errors.append("confirmation.post_visible_or_permalink_resolved=false")
        rules_validation = evidence.get("rules_validation")
        if not isinstance(rules_validation, dict):
            missing.append("rules_validation")
        elif "ok" not in rules_validation:
            missing.append("rules_validation.ok")
    elif action_key == "likes":
        if not bool(confirmation.get("like_state_active")):
            errors.append("confirmation.like_state_active=false")
    elif action_key == "shares":
        if not bool(confirmation.get("share_confirmed")):
            errors.append("confirmation.share_confirmed=false")
        if str(confirmation.get("share_destination") or "") != "own_feed":
            errors.append("confirmation.share_destination!=own_feed")
        if not bool(confirmation.get("share_destination_confirmed")):
            errors.append("confirmation.share_destination_confirmed=false")
    elif action_key == "comment_replies":
        if not bool(confirmation.get("reply_visible_under_thread")):
            errors.append("confirmation.reply_visible_under_thread=false")

    evidence_run_id = str(evidence.get("run_id") or "").strip()
    if evidence_run_id and evidence_run_id != run_id:
        errors.append("run_id_mismatch")

    return {
        "ok": not missing and not errors,
        "missing": missing,
        "errors": errors,
    }


def evaluate_evidence_contract(
    *,
    run_id: str,
    run_spec: Dict,
    evidence_items: List[Dict],
) -> Dict:
    contract = run_spec.get("verification_contract", {}) or {}
    required_totals = build_required_totals(run_spec)
    expected_profile = str(run_spec.get("profile_name") or "").strip()

    if not bool(contract.get("require_evidence", True)):
        return {
            "passed": True,
            "missing": [],
            "invalid_evidence": [],
            "valid_counts": {k: 0 for k in EVIDENCE_METRIC_KEYS},
            "required_counts": {k: required_totals.get(k, 0) for k in EVIDENCE_METRIC_KEYS},
            "profile_identity_ok": True,
            "profiles_seen": [],
            "evaluated_at": _utc_iso(),
        }

    valid_counts = {k: 0 for k in EVIDENCE_METRIC_KEYS}
    invalid_evidence: List[Dict] = []

    for action_key in EVIDENCE_METRIC_KEYS:
        matched = [e for e in evidence_items if isinstance(e, dict) and _evidence_matches_action_key(e, action_key)]
        for evidence in matched:
            verdict = validate_action_evidence(
                evidence=evidence,
                action_key=action_key,
                run_id=run_id,
                expected_profile=expected_profile,
                verification_contract=contract,
            )
            if verdict["ok"]:
                completed_count = int((evidence.get("result_state") or {}).get("completed_count") or 1)
                valid_counts[action_key] += max(1, completed_count)
            else:
                invalid_evidence.append(
                    {
                        "action_key": action_key,
                        "action_id": evidence.get("action_id"),
                        "step_id": evidence.get("step_id"),
                        "missing": verdict.get("missing", []),
                        "errors": verdict.get("errors", []),
                    }
                )

    profiles_seen = sorted(
        {
            str(e.get("profile_name") or "").strip().lower()
            for e in evidence_items
            if isinstance(e, dict) and str(e.get("profile_name") or "").strip()
        }
    )
    profile_identity_ok = not expected_profile or profiles_seen in ([], [expected_profile.lower()])

    missing = []
    for key in EVIDENCE_METRIC_KEYS:
        required_count = int(required_totals.get(key, 0))
        observed = int(valid_counts.get(key, 0))
        if observed < required_count:
            missing.append({"metric": key, "required": required_count, "observed": observed})

    if not profile_identity_ok:
        missing.append(
            {
                "metric": "profile_identity",
                "required": expected_profile.lower() if expected_profile else None,
                "observed": profiles_seen,
            }
        )

    return {
        "passed": len(missing) == 0 and len(invalid_evidence) == 0,
        "missing": missing,
        "invalid_evidence": invalid_evidence,
        "valid_counts": valid_counts,
        "required_counts": {k: int(required_totals.get(k, 0)) for k in EVIDENCE_METRIC_KEYS},
        "profile_identity_ok": profile_identity_ok,
        "profiles_seen": profiles_seen,
        "evaluated_at": _utc_iso(),
    }
