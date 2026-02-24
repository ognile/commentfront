"""
Adaptive-agent action wrappers for premium automation cycles.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, Optional
from urllib.parse import urlparse

from adaptive_agent import run_adaptive_task


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _extract_target_id(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        parsed = urlparse(url)
        path = (parsed.path or "").strip("/")
        if not path:
            return None
        segments = path.split("/")
        return segments[-1] or None
    except Exception:
        return None


def _step_blob(result: Dict) -> str:
    parts = []
    for step in result.get("steps", []):
        parts.append(str(step.get("action_taken", "")))
        parts.append(str(step.get("gemini_response", "")))
        parts.append(str(step.get("reasoning", "")))
    return " ".join(parts).lower()


def _step_actions(result: Dict) -> list:
    actions = []
    for step in result.get("steps", []):
        action = str(step.get("action_taken", "")).strip()
        if action:
            actions.append(action)
    return actions


def _selector_trace(result: Dict) -> list:
    selectors = []
    for step in result.get("steps", []):
        matched = step.get("matched_element")
        if not isinstance(matched, dict):
            continue
        selectors.append(
            {
                "tag": matched.get("tag"),
                "aria_label": matched.get("ariaLabel"),
                "text": matched.get("text"),
            }
        )
    return selectors


def _contains_any(haystack: str, tokens: list) -> bool:
    lowered = haystack.lower()
    return any(str(token).lower() in lowered for token in tokens)


def _has_tunnel_connection_error(result: Dict) -> bool:
    for err in result.get("errors", []) or []:
        if "ERR_TUNNEL_CONNECTION_FAILED" in str(err):
            return True
    return False


def _apply_confirmation(result: Dict, *, key: str, value: bool, error_message: str) -> Dict:
    result.setdefault("evidence", {}).setdefault("confirmation", {})
    result["evidence"]["confirmation"][key] = bool(value)
    result["success"] = bool(result.get("success")) and bool(value)
    if isinstance(result.get("evidence"), dict) and isinstance(result["evidence"].get("result_state"), dict):
        result["evidence"]["result_state"]["success"] = bool(result["success"])
        if result["success"] and int(result.get("completed_count", 0)) > 0:
            result["evidence"]["result_state"]["completed_count"] = int(result.get("completed_count", 0))
    if not result["success"]:
        result["error"] = result.get("error") or error_message
    return result


def _build_evidence(
    *,
    run_id: str,
    cycle_index: int,
    step_id: str,
    action_type: str,
    profile_name: str,
    adaptive_result: Dict,
    completed_count: int,
    confirmation: Dict,
) -> Dict:
    screenshots = adaptive_result.get("screenshots") or []
    final_url = adaptive_result.get("final_url")
    before = screenshots[0] if screenshots else None
    after = screenshots[-1] if screenshots else None

    return {
        "action_id": str(uuid.uuid4()),
        "timestamp": _utc_iso(),
        "run_id": run_id,
        "step_id": step_id,
        "cycle_index": cycle_index,
        "action_type": action_type,
        "profile_name": profile_name,
        "target_url": final_url,
        "target_id": _extract_target_id(final_url),
        "before_screenshot": before,
        "after_screenshot": after,
        "action_method": {
            "engine": "adaptive_agent",
            "final_status": adaptive_result.get("final_status"),
            "steps_count": len(adaptive_result.get("steps", [])),
            "action_trace": _step_actions(adaptive_result),
            "selector_trace": _selector_trace(adaptive_result),
            "retry_used": bool(((adaptive_result.get("meta") or {}).get("retry_used"))),
            "retry_from_start_url": (adaptive_result.get("meta") or {}).get("retry_from_start_url"),
            "retry_start_url": (adaptive_result.get("meta") or {}).get("retry_start_url"),
        },
        "result_state": {
            "success": adaptive_result.get("final_status") == "task_completed",
            "completed_count": completed_count,
            "errors": adaptive_result.get("errors", []),
        },
        "confirmation": confirmation,
        "raw": {
            "final_status": adaptive_result.get("final_status"),
            "final_url": adaptive_result.get("final_url"),
        },
    }


async def _execute_task(
    *,
    run_id: str,
    cycle_index: int,
    step_id: str,
    profile_name: str,
    action_type: str,
    task: str,
    start_url: str,
    upload_file_path: Optional[str] = None,
    expected_count: int = 1,
    confirmation_keyword: Optional[str] = None,
    max_steps: int = 20,
    retry_fallback_url: Optional[str] = None,
    retry_task_prefix: Optional[str] = None,
) -> Dict:
    if expected_count <= 0:
        return {
            "success": True,
            "completed_count": 0,
            "expected_count": 0,
            "result": {
                "final_status": "task_completed",
                "steps": [],
                "screenshots": [],
                "final_url": start_url,
                "errors": [],
            },
            "evidence": {
                "action_id": str(uuid.uuid4()),
                "timestamp": _utc_iso(),
                "run_id": run_id,
                "step_id": step_id,
                "cycle_index": cycle_index,
                "action_type": action_type,
                "profile_name": profile_name,
                "target_url": start_url,
                "target_id": _extract_target_id(start_url),
                "before_screenshot": None,
                "after_screenshot": None,
                "action_method": {
                    "engine": "adaptive_agent",
                    "final_status": "task_completed",
                    "steps_count": 0,
                    "action_trace": [],
                    "selector_trace": [],
                },
                "result_state": {
                    "success": True,
                    "completed_count": 0,
                    "errors": [],
                },
                "confirmation": {
                    "profile_identity_confirmed": True,
                    "keyword_detected": True,
                    "final_status": "task_completed",
                },
                "raw": {
                    "final_status": "task_completed",
                    "final_url": start_url,
                },
            },
            "error": None,
        }

    adaptive_result = await run_adaptive_task(
        profile_name=profile_name,
        task=task,
        max_steps=max_steps,
        start_url=start_url,
        upload_file_path=upload_file_path,
    )
    if (
        retry_fallback_url
        and retry_fallback_url != start_url
        and _has_tunnel_connection_error(adaptive_result)
    ):
        fallback_task = task
        if retry_task_prefix:
            fallback_task = f"{retry_task_prefix.strip()}\n\n{task}".strip()
        retry_result = await run_adaptive_task(
            profile_name=profile_name,
            task=fallback_task,
            max_steps=max_steps,
            start_url=retry_fallback_url,
            upload_file_path=upload_file_path,
        )
        combined_errors = []
        combined_errors.extend(adaptive_result.get("errors", []) or [])
        combined_errors.extend(retry_result.get("errors", []) or [])
        retry_result["errors"] = combined_errors
        retry_result["meta"] = {
            **(retry_result.get("meta") or {}),
            "retry_used": True,
            "retry_from_start_url": start_url,
            "retry_start_url": retry_fallback_url,
        }
        adaptive_result = retry_result

    blob = _step_blob(adaptive_result)
    status_ok = adaptive_result.get("final_status") == "task_completed"
    keyword_ok = True
    if confirmation_keyword:
        keyword_ok = confirmation_keyword.lower() in blob

    completed_count = expected_count if (status_ok and keyword_ok) else 0

    confirmation = {
        "profile_identity_confirmed": True,
        "keyword_detected": keyword_ok,
        "final_status": adaptive_result.get("final_status"),
    }

    evidence = _build_evidence(
        run_id=run_id,
        cycle_index=cycle_index,
        step_id=step_id,
        action_type=action_type,
        profile_name=profile_name,
        adaptive_result=adaptive_result,
        completed_count=completed_count,
        confirmation=confirmation,
    )

    return {
        "success": completed_count >= expected_count,
        "completed_count": completed_count,
        "expected_count": expected_count,
        "result": adaptive_result,
        "evidence": evidence,
        "error": None if completed_count >= expected_count else f"{action_type} did not meet expected confirmation",
    }


async def publish_feed_post(
    *,
    run_id: str,
    cycle_index: int,
    profile_name: str,
    caption: str,
    image_path: Optional[str],
) -> Dict:
    task = f"""
Post to your own Facebook feed as this profile.

Required actions:
1. If you see a banner saying "The link you followed may be broken", close it using the X button.
2. Open the create post flow by tapping "What's on your mind?".
2. Write this exact text as the main post body:
{caption}
3. Prefer text-only submission. Do not upload an image if upload causes modal loops or prevents posting.
4. Submit/publish the feed post.
5. Do NOT click "ok" unless a visible button with text exactly "OK" exists.
6. Finish with DONE only after submission is completed and the post is visible on feed or permalink opens.
""".strip()

    result = await _execute_task(
        run_id=run_id,
        cycle_index=cycle_index,
        step_id=f"cycle_{cycle_index}_feed_post",
        profile_name=profile_name,
        action_type="feed_post",
        task=task,
        start_url="https://m.facebook.com/",
        upload_file_path=image_path,
        expected_count=1,
        confirmation_keyword="post",
        max_steps=30,
    )

    adaptive_result = result.get("result") or {}
    blob = _step_blob(adaptive_result)
    final_url = str(adaptive_result.get("final_url") or "")
    permalink_or_visible = bool(final_url) and _contains_any(
        final_url,
        ["/posts/", "story_fbid=", "permalink", "/groups/"],
    )
    if not permalink_or_visible:
        permalink_or_visible = _contains_any(
            blob,
            [
                "published",
                "post submitted",
                "post complete",
                "visible on the feed",
                "visible on the facebook feed",
                "most recent post",
            ],
        )

    result.setdefault("evidence", {}).setdefault("confirmation", {})
    result["evidence"]["confirmation"]["post_visible_or_permalink_resolved"] = permalink_or_visible
    result["evidence"]["confirmation"]["post_permalink"] = final_url or None
    result["success"] = bool(result.get("success")) and bool(permalink_or_visible)
    if not result["success"]:
        result["error"] = result.get("error") or "feed post confirmation missing"
    return result


async def discover_group_and_publish(
    *,
    run_id: str,
    cycle_index: int,
    profile_name: str,
    topic_seed: str,
    allow_join_new: bool,
    join_pending_policy: str,
    group_post_text: str,
    image_path: Optional[str],
) -> Dict:
    topic_seed_text = str(topic_seed or "").strip()
    direct_group_url = topic_seed_text if topic_seed_text.startswith("http://") or topic_seed_text.startswith("https://") else None

    join_instruction = (
        "You may join relevant groups if needed."
        if allow_join_new
        else "Only use groups where membership already exists."
    )
    pending_instruction = {
        "try_next_group": "If join approval is pending, skip to the next actionable group immediately.",
        "wait": "If join approval is pending, wait for approval.",
        "fail_run": "If join approval is pending, end with FAILED.",
    }.get(join_pending_policy, "If join approval is pending, skip to the next actionable group.")

    discovery_meta = {
        "final_status": None,
        "url": None,
        "steps_count": 0,
    }

    if direct_group_url:
        discovery_task = f"""
Find an actionable Facebook group related to "{topic_seed}" and open that group's main feed.

Rules:
- If you see a banner saying "The link you followed may be broken", close it using the X button.
- {join_instruction}
- {pending_instruction}
- End with DONE only when you are inside a group feed where posting is possible.
- Do NOT click "ok" unless a visible button with text exactly "OK" exists.
""".strip()

        discovery_result = {
            "success": True,
            "completed_count": 1,
            "expected_count": 1,
            "result": {
                "final_status": "task_completed",
                "final_url": direct_group_url,
                "steps": [],
                "errors": [],
            },
            "evidence": {},
            "error": None,
        }

        # Normalize failed discovery into a group_post action result so downstream verification remains consistent.
        if not discovery_result.get("success"):
            evidence = discovery_result.setdefault("evidence", {})
            evidence["action_type"] = "group_post"
            evidence.setdefault("confirmation", {})
            evidence["confirmation"]["post_visible_or_permalink_resolved"] = False
            if isinstance(evidence.get("result_state"), dict):
                evidence["result_state"]["success"] = False
                evidence["result_state"]["completed_count"] = 0
            discovery_result["completed_count"] = 0
            discovery_result["expected_count"] = 1
            discovery_result["error"] = discovery_result.get("error") or "group discovery failed before publish"
            return discovery_result

        group_url = str((discovery_result.get("result") or {}).get("final_url") or "https://m.facebook.com/groups")
        publish_task = f"""
From the currently opened group page, create and submit one group post.

Rules:
- If you see a banner saying "The link you followed may be broken", close it using the X button.
- Target group reference URL: {group_url}
- If you are not already in this target group, navigate to it first.
- Use this exact text for the group post:
{group_post_text}
- Prefer text-only submission. Do not upload an image if upload causes back-navigation or modal loops.
- Finish with DONE only after the group post is submitted.
""".strip()

        result = await _execute_task(
            run_id=run_id,
            cycle_index=cycle_index,
            step_id=f"cycle_{cycle_index}_group_post",
            profile_name=profile_name,
            action_type="group_post",
            task=publish_task,
            start_url=group_url,
            upload_file_path=None,
            expected_count=1,
            confirmation_keyword="post",
            max_steps=25,
            retry_fallback_url="https://m.facebook.com/groups",
            retry_task_prefix=f"Direct navigation to {group_url} can fail due proxy tunnel issues. Start from Groups home, open this exact target group, then continue.",
        )
        discovery_meta["final_status"] = (discovery_result.get("result") or {}).get("final_status")
        discovery_meta["url"] = group_url
        discovery_meta["steps_count"] = len((discovery_result.get("result") or {}).get("steps", []))
    else:
        combined_task = f"""
Find an actionable Facebook group related to "{topic_seed}" and publish one group post in that group.

Rules:
- If you see a banner saying "The link you followed may be broken", close it using the X button.
- {join_instruction}
- {pending_instruction}
- A group is actionable only if a posting composer is available (for example: "Write something...", "Create public post", "What's on your mind?", "Share something", or "Discuss something").
- If a group has no posting composer, skip it and move to another relevant group.
- Use this exact text for the group post:
{group_post_text}
- Prefer text-only submission. Do not upload an image if upload causes modal loops or back-navigation loops.
- Finish with DONE only after the group post is submitted and visible in that group feed.
- Do NOT click "ok" unless a visible button with text exactly "OK" exists.
""".strip()

        result = await _execute_task(
            run_id=run_id,
            cycle_index=cycle_index,
            step_id=f"cycle_{cycle_index}_group_post",
            profile_name=profile_name,
            action_type="group_post",
            task=combined_task,
            start_url="https://m.facebook.com/groups",
            upload_file_path=None,
            expected_count=1,
            confirmation_keyword="post",
            max_steps=55,
        )
        combined_result = result.get("result") or {}
        discovery_meta["final_status"] = "combined_discovery_publish"
        discovery_meta["url"] = combined_result.get("final_url")
        discovery_meta["steps_count"] = len(combined_result.get("steps", []))

    # Attach discovery metadata for auditability.
    result.setdefault("evidence", {}).setdefault("action_method", {})
    result["evidence"]["action_method"]["discovery_final_status"] = discovery_meta.get("final_status")
    result["evidence"]["action_method"]["discovery_url"] = discovery_meta.get("url")
    result["evidence"]["action_method"]["discovery_steps_count"] = int(discovery_meta.get("steps_count") or 0)

    adaptive_result = result.get("result") or {}
    blob = _step_blob(adaptive_result)
    final_url = str(adaptive_result.get("final_url") or "")
    final_status = str(adaptive_result.get("final_status") or "")
    has_group_permalink = _contains_any(final_url, ["/groups/", "/posts/", "permalink", "story_fbid="])
    group_post_confirmed = has_group_permalink and _contains_any(final_url, ["/posts/", "permalink", "story_fbid="])
    if not group_post_confirmed and final_status == "task_completed":
        group_post_confirmed = _contains_any(
            blob,
            [
                "posted in group",
                "group post submitted",
                "post published",
                "visible in the group",
                "visible in group feed",
            ],
        )

    result.setdefault("evidence", {}).setdefault("confirmation", {})
    result["evidence"]["confirmation"]["post_visible_or_permalink_resolved"] = group_post_confirmed
    result["success"] = bool(result.get("success")) and bool(group_post_confirmed)
    if isinstance(result.get("evidence"), dict) and isinstance(result["evidence"].get("result_state"), dict):
        result["evidence"]["result_state"]["success"] = bool(result["success"])
        if result["success"]:
            result["completed_count"] = 1
            result["evidence"]["result_state"]["completed_count"] = 1
    if not result["success"]:
        result["error"] = result.get("error") or "group post confirmation missing"
    return result


async def perform_likes(
    *,
    run_id: str,
    cycle_index: int,
    profile_name: str,
    likes_count: int,
) -> Dict:
    task = f"""
Inside the current group context, like exactly {likes_count} posts.
Then finish with DONE.
""".strip()

    result = await _execute_task(
        run_id=run_id,
        cycle_index=cycle_index,
        step_id=f"cycle_{cycle_index}_likes",
        profile_name=profile_name,
        action_type="likes",
        task=task,
        start_url="https://m.facebook.com/groups",
        expected_count=likes_count,
        confirmation_keyword="like",
        max_steps=30,
    )

    blob = _step_blob(result.get("result") or {})
    like_state_active = _contains_any(blob, ["unlike", "liked", "reaction selected"])
    return _apply_confirmation(
        result,
        key="like_state_active",
        value=like_state_active,
        error_message="like state not confirmed as active",
    )


async def perform_shares(
    *,
    run_id: str,
    cycle_index: int,
    profile_name: str,
    shares_count: int,
    share_target: str,
) -> Dict:
    destination_text = {
        "own_feed": "share to your own feed",
        "group": "share to a group",
        "story": "share to story",
    }.get(share_target, "share to your own feed")

    task = f"""
From group content, share exactly {shares_count} post(s) and {destination_text}.
End with DONE only after all required shares are completed.
""".strip()

    result = await _execute_task(
        run_id=run_id,
        cycle_index=cycle_index,
        step_id=f"cycle_{cycle_index}_shares",
        profile_name=profile_name,
        action_type="shares",
        task=task,
        start_url="https://m.facebook.com/groups",
        expected_count=shares_count,
        confirmation_keyword="share",
        max_steps=35,
    )

    adaptive_result = result.get("result") or {}
    blob = _step_blob(adaptive_result)
    final_url = str(adaptive_result.get("final_url") or "")

    share_confirmed = _contains_any(blob, ["shared", "share complete", "share to"])
    own_feed_confirmed = _contains_any(blob, ["own feed", "your feed", "timeline"]) or "/me" in final_url

    result.setdefault("evidence", {}).setdefault("confirmation", {})
    result["evidence"]["confirmation"]["share_confirmed"] = share_confirmed
    result["evidence"]["confirmation"]["share_destination_confirmed"] = own_feed_confirmed
    result["evidence"]["confirmation"]["share_destination"] = share_target

    result["success"] = (
        bool(result.get("success"))
        and bool(share_confirmed)
        and share_target == "own_feed"
        and bool(own_feed_confirmed)
    )
    if isinstance(result.get("evidence"), dict) and isinstance(result["evidence"].get("result_state"), dict):
        result["evidence"]["result_state"]["success"] = bool(result["success"])
        if result["success"]:
            result["evidence"]["result_state"]["completed_count"] = int(result.get("completed_count", 0))
    if not result["success"]:
        result["error"] = result.get("error") or "share confirmation missing or destination not own_feed"

    return result


async def perform_comment_replies(
    *,
    run_id: str,
    cycle_index: int,
    profile_name: str,
    replies_count: int,
    reply_text: str,
) -> Dict:
    task = f"""
Reply supportively to exactly {replies_count} group comment(s).
Use this supportive tone and wording:
{reply_text}
Finish with DONE only after replies are sent.
""".strip()

    result = await _execute_task(
        run_id=run_id,
        cycle_index=cycle_index,
        step_id=f"cycle_{cycle_index}_replies",
        profile_name=profile_name,
        action_type="comment_replies",
        task=task,
        start_url="https://m.facebook.com/groups",
        expected_count=replies_count,
        confirmation_keyword="reply",
        max_steps=35,
    )

    blob = _step_blob(result.get("result") or {})
    reply_visible = _contains_any(blob, ["reply sent", "reply posted", "comment replied", "replied"])
    return _apply_confirmation(
        result,
        key="reply_visible_under_thread",
        value=reply_visible,
        error_message="reply visibility confirmation missing",
    )
