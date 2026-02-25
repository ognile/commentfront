"""
Adaptive-agent action wrappers for premium automation cycles.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, Optional
from urllib.parse import quote_plus, urlparse
from pathlib import Path
import re

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


def _to_public_screenshot_url(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    name = Path(path).name
    if not name:
        return None
    return f"/screenshots/{name}"


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


def _count_type_actions_for_caption(action_trace: list, caption: str) -> int:
    """
    Count TYPE actions that appear to type the target caption text.
    Uses token overlap to tolerate truncated action traces.
    """
    tokens = _token_set(caption)
    if not tokens:
        return 0
    threshold = min(3, len(tokens))
    count = 0
    for action in action_trace or []:
        text = str(action or "").strip().lower()
        if not (text.startswith("type:") or text.startswith("type_set_exact:")):
            continue
        payload_tokens = _token_set(text)
        overlap = len(tokens.intersection(payload_tokens))
        if overlap >= threshold:
            count += 1
    return count


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


def _token_set(text: str) -> set[str]:
    return {
        token
        for token in re.split(r"[^a-z0-9]+", str(text or "").lower())
        if len(token) >= 3
    }


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
        "screenshot_urls": {
            "before": _to_public_screenshot_url(before),
            "after": _to_public_screenshot_url(after),
        },
        "action_method": {
            "engine": "adaptive_agent",
            "final_status": adaptive_result.get("final_status"),
            "steps_count": len(adaptive_result.get("steps", [])),
            "action_trace": _step_actions(adaptive_result),
            "selector_trace": _selector_trace(adaptive_result),
            "retry_used": bool(((adaptive_result.get("meta") or {}).get("retry_used"))),
            "retry_from_start_url": (adaptive_result.get("meta") or {}).get("retry_from_start_url"),
            "retry_start_url": (adaptive_result.get("meta") or {}).get("retry_start_url"),
            "retry_attempts": (adaptive_result.get("meta") or {}).get("retry_attempts"),
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
    max_type_actions: Optional[int] = None,
    retry_fallback_url: Optional[str] = None,
    retry_task_prefix: Optional[str] = None,
    profile_identity_confirmed: bool = True,
    identity_check: Optional[Dict] = None,
    duplicate_precheck: Optional[Dict] = None,
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
                    "profile_identity_confirmed": bool(profile_identity_confirmed),
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
        max_type_actions=max_type_actions,
    )
    if _has_tunnel_connection_error(adaptive_result):
        fallback_chain = []
        if retry_fallback_url and retry_fallback_url != start_url:
            fallback_chain.append(retry_fallback_url)
        if "https://m.facebook.com/" not in fallback_chain and start_url != "https://m.facebook.com/":
            fallback_chain.append("https://m.facebook.com/")

        tunnel_persisted = True
        for retry_index, fallback_url in enumerate(fallback_chain, start=1):
            if not tunnel_persisted:
                break
            fallback_task = task
            if retry_task_prefix:
                fallback_task = f"{retry_task_prefix.strip()}\n\n{task}".strip()
            retry_result = await run_adaptive_task(
                profile_name=profile_name,
                task=fallback_task,
                max_steps=max_steps,
                start_url=fallback_url,
                upload_file_path=upload_file_path,
                max_type_actions=max_type_actions,
            )
            tunnel_persisted = _has_tunnel_connection_error(retry_result)
            combined_errors = []
            combined_errors.extend(adaptive_result.get("errors", []) or [])
            combined_errors.extend(retry_result.get("errors", []) or [])
            retry_result["errors"] = combined_errors
            retry_result["meta"] = {
                **(retry_result.get("meta") or {}),
                "retry_used": True,
                "retry_from_start_url": start_url,
                "retry_start_url": fallback_url,
                "retry_attempts": retry_index,
            }
            adaptive_result = retry_result

    blob = _step_blob(adaptive_result)
    status_ok = adaptive_result.get("final_status") == "task_completed"
    keyword_ok = True
    if confirmation_keyword:
        keyword_ok = confirmation_keyword.lower() in blob

    completed_count = expected_count if (status_ok and keyword_ok) else 0

    confirmation = {
        "profile_identity_confirmed": bool(profile_identity_confirmed),
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
    if isinstance(identity_check, dict):
        evidence["identity_check"] = identity_check
    if isinstance(duplicate_precheck, dict):
        evidence["duplicate_precheck"] = duplicate_precheck

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
    profile_identity_confirmed: bool = True,
    identity_check: Optional[Dict] = None,
    duplicate_precheck: Optional[Dict] = None,
    single_submit_guard: bool = True,
) -> Dict:
    task = f"""
Post to your own Facebook feed as this profile.

Required actions:
1. If you see a banner saying "The link you followed may be broken", close it using the X button.
2. Open the create post flow by tapping "What's on your mind?".
3. Write this exact text as the main post body:
{caption}
4. Prefer text-only submission. Do not upload an image if upload causes modal loops or prevents posting.
5. Submit/publish the feed post with EXACTLY ONE click on "POST".
6. After the first "POST" click:
   - NEVER click "POST" again in this task.
   - NEVER reopen the composer in this task.
   - wait for confirmation ("Posted", "Just now", "Uploading your post...", or visible feed post) then finish.
7. If no confirmation appears after waiting, end with FAILED instead of a second submit.
8. Do NOT click "ok" unless a visible button with text exactly "OK" exists.
9. Finish with DONE only after submission is completed and the post is visible on feed or permalink opens.
""".strip()

    result = await _execute_task(
        run_id=run_id,
        cycle_index=cycle_index,
        step_id=f"cycle_{cycle_index}_feed_post",
        profile_name=profile_name,
        action_type="feed_post",
        task=task,
        start_url="https://m.facebook.com/me/?v=timeline",
        upload_file_path=image_path,
        expected_count=1,
        confirmation_keyword="post",
        max_steps=30,
        max_type_actions=1,
        profile_identity_confirmed=profile_identity_confirmed,
        identity_check=identity_check,
        duplicate_precheck=duplicate_precheck,
    )

    adaptive_result = result.get("result") or {}
    blob = _step_blob(adaptive_result)
    final_url = str(adaptive_result.get("final_url") or "")
    permalink_or_visible = bool(final_url) and _contains_any(
        final_url,
        ["/posts/", "story_fbid=", "permalink", "/groups/"],
    )
    own_feed_url_hint = bool(final_url) and _contains_any(
        final_url,
        ["m.facebook.com/", "m.facebook.com/me", "profile.php?id="],
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
                "visible on the user's feed",
                "visible on your feed",
                "most recent post",
                "successfully submitted",
                "posted notification",
                "your post has been posted",
                "your post was posted",
            ],
        )
    if not permalink_or_visible and own_feed_url_hint and bool(result.get("success")):
        permalink_or_visible = _contains_any(
            blob,
            [
                "done:",
                "posted",
                "submitted",
                "uploading your post",
            ],
        )

    result.setdefault("evidence", {}).setdefault("confirmation", {})
    result["evidence"]["confirmation"]["post_visible_or_permalink_resolved"] = permalink_or_visible
    result["evidence"]["confirmation"]["post_permalink"] = final_url or None

    if single_submit_guard:
        action_trace = ((result.get("evidence", {}).get("action_method", {}) or {}).get("action_trace", []) or [])
        post_clicks = [
            str(action).strip().lower()
            for action in action_trace
            if 'click "post"' in str(action).strip().lower()
        ]
        submit_guard_passed = len(post_clicks) <= 1
        result["evidence"]["confirmation"]["submit_guard_passed"] = submit_guard_passed
        if not submit_guard_passed:
            result["evidence"]["result_state"]["success"] = False
            result["evidence"]["result_state"]["completed_count"] = 0
            result["success"] = False
            result["error"] = "submit_idempotency_blocked"
            result["evidence"]["submit_guard"] = {
                "passed": False,
                "post_click_count": len(post_clicks),
                "reason": "multiple feed submit clicks detected in single action",
            }
            return result

        caption_type_count = _count_type_actions_for_caption(action_trace, caption)
        type_guard_passed = caption_type_count <= 1
        if not type_guard_passed:
            result["evidence"]["result_state"]["success"] = False
            result["evidence"]["result_state"]["completed_count"] = 0
            result["success"] = False
            result["error"] = "composer_type_idempotency_blocked"
            result["evidence"]["type_guard"] = {
                "passed": False,
                "caption_type_count": int(caption_type_count),
                "reason": "caption typed multiple times in a single feed composer flow",
            }
            return result
        result["evidence"]["submit_guard"] = {
            "passed": True,
            "post_click_count": len(post_clicks),
        }
        result["evidence"]["type_guard"] = {
            "passed": True,
            "caption_type_count": int(caption_type_count),
        }

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
    profile_identity_confirmed: bool = True,
    identity_check: Optional[Dict] = None,
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
- If both "Visit" and "View group" appear, always click "View group" to enter the real group feed.
- If "View group" appears after joining, click it immediately before any other action.
- Use this exact text for the group post:
{group_post_text}
- Prefer text-only submission. Do not upload an image if upload causes back-navigation or modal loops.
- Finish with DONE only after the group post is submitted.
- If Facebook shows the post is pending admin approval, treat that as submitted and finish with DONE.
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
            profile_identity_confirmed=profile_identity_confirmed,
            identity_check=identity_check,
        )
        discovery_meta["final_status"] = (discovery_result.get("result") or {}).get("final_status")
        discovery_meta["url"] = group_url
        discovery_meta["steps_count"] = len((discovery_result.get("result") or {}).get("steps", []))
    else:
        combined_task_base = f"""
Find an actionable Facebook group related to "{topic_seed}" and publish one group post in that group.

Rules:
- If you see a banner saying "The link you followed may be broken", close it using the X button.
- {join_instruction}
- {pending_instruction}
- Start from group-search results for "{topic_seed}". If result cards are not interactable, tap Search and run the topic query again.
- Prefer groups labeled "Your group" in INTERACTIVE ELEMENTS when available.
- If both "Visit" and "View group" appear, always click "View group" to enter the actual group feed.
- If "View group" appears after joining, click it immediately before any other action.
- Do not click "Visit" repeatedly in the same UI state.
- A group is actionable only if a posting composer is available in the INTERACTIVE ELEMENTS list (for example: "Write something...", "Create public post", "What's on your mind?", "Share something", or "Discuss something").
- If a group has no posting composer, skip it and move to another relevant group.
- Use this exact text for the group post:
{group_post_text}
- Prefer text-only submission. Do not upload an image if upload causes modal loops or back-navigation loops.
- Finish with DONE only after the group post is submitted and visible in that group feed,
  or Facebook confirms the post is pending admin approval.
- Do NOT click "ok" unless a visible button with text exactly "OK" exists.
""".strip()

        tried_group_urls: list[str] = []
        selected_attempt = 0
        result = None
        max_combined_attempts = 3
        search_start_url = f"https://m.facebook.com/search/groups/?q={quote_plus(topic_seed or 'groups')}"
        
        def _group_post_confirmed(candidate_result: Dict) -> bool:
            candidate_adaptive_result = candidate_result.get("result") or {}
            candidate_blob = _step_blob(candidate_adaptive_result)
            candidate_final_url = str(candidate_adaptive_result.get("final_url") or "")
            candidate_final_status = str(candidate_adaptive_result.get("final_status") or "")
            has_group_permalink = _contains_any(candidate_final_url, ["/groups/", "/posts/", "permalink", "story_fbid="])
            confirmed = has_group_permalink and _contains_any(candidate_final_url, ["/posts/", "permalink", "story_fbid="])
            if not confirmed and candidate_final_status == "task_completed":
                confirmed = _contains_any(
                    candidate_blob,
                    [
                        "posted in group",
                        "group post submitted",
                        "post published",
                        "visible in the group",
                        "visible in group feed",
                        "pending admin approval",
                        "post is pending",
                        "pending review by admins",
                    ],
                )
            return bool(confirmed)

        for attempt in range(1, max_combined_attempts + 1):
            avoid_clause = ""
            if tried_group_urls:
                avoid_clause = "- Do not revisit these previously attempted groups: " + ", ".join(tried_group_urls)

            attempt_task = (
                f"{combined_task_base}\n"
                f"{avoid_clause}\n"
                f"- Attempt {attempt} of {max_combined_attempts}: "
                "if no posting composer appears in INTERACTIVE ELEMENTS after a few interactions, switch to another group."
            ).strip()

            candidate = await _execute_task(
                run_id=run_id,
                cycle_index=cycle_index,
                step_id=f"cycle_{cycle_index}_group_post_attempt_{attempt}",
                profile_name=profile_name,
                action_type="group_post",
                task=attempt_task,
                start_url=search_start_url,
                upload_file_path=None,
                expected_count=1,
                confirmation_keyword="post",
                max_steps=28,
                retry_fallback_url="https://m.facebook.com/groups",
                retry_task_prefix="If direct group-search URL fails due proxy tunnel issues, open Groups, tap Search, run the topic query, then continue.",
                profile_identity_confirmed=profile_identity_confirmed,
                identity_check=identity_check,
            )

            selected_attempt = attempt
            result = candidate

            candidate_url = str((candidate.get("result") or {}).get("final_url") or "")
            if "/groups/" in candidate_url and candidate_url not in tried_group_urls:
                tried_group_urls.append(candidate_url)

            if candidate.get("success") and _group_post_confirmed(candidate):
                break

        combined_result = (result or {}).get("result") or {}
        discovery_meta["final_status"] = "combined_discovery_publish"
        discovery_meta["url"] = combined_result.get("final_url")
        discovery_meta["steps_count"] = len(combined_result.get("steps", []))
        discovery_meta["attempt"] = selected_attempt
        discovery_meta["tried_group_urls"] = tried_group_urls

        if result is None:
            result = {
                "success": False,
                "completed_count": 0,
                "expected_count": 1,
                "result": {"final_status": "error", "steps": [], "errors": ["combined group attempts did not execute"]},
                "evidence": {},
                "error": "combined group attempts did not execute",
            }

    # Attach discovery metadata for auditability.
    result.setdefault("evidence", {}).setdefault("action_method", {})
    result["evidence"]["action_method"]["discovery_final_status"] = discovery_meta.get("final_status")
    result["evidence"]["action_method"]["discovery_url"] = discovery_meta.get("url")
    result["evidence"]["action_method"]["discovery_steps_count"] = int(discovery_meta.get("steps_count") or 0)
    if "attempt" in discovery_meta:
        result["evidence"]["action_method"]["group_attempt"] = discovery_meta.get("attempt")
        result["evidence"]["action_method"]["tried_group_urls"] = discovery_meta.get("tried_group_urls", [])

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
                "pending admin approval",
                "post is pending",
                "pending review by admins",
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
    start_url: Optional[str] = None,
    profile_identity_confirmed: bool = True,
    identity_check: Optional[Dict] = None,
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
        start_url=start_url or "https://m.facebook.com/groups",
        expected_count=likes_count,
        confirmation_keyword="like",
        max_steps=30,
        profile_identity_confirmed=profile_identity_confirmed,
        identity_check=identity_check,
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
    start_url: Optional[str] = None,
    profile_identity_confirmed: bool = True,
    identity_check: Optional[Dict] = None,
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
        start_url=start_url or "https://m.facebook.com/groups",
        expected_count=shares_count,
        confirmation_keyword="share",
        max_steps=35,
        profile_identity_confirmed=profile_identity_confirmed,
        identity_check=identity_check,
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
    start_url: Optional[str] = None,
    profile_identity_confirmed: bool = True,
    identity_check: Optional[Dict] = None,
) -> Dict:
    def _annotate_retry_metadata(
        payload: Dict,
        *,
        retry_from_start_url: str,
        retry_start_url: str,
        retry_attempts: int,
    ) -> Dict:
        payload.setdefault("evidence", {}).setdefault("action_method", {})
        payload["evidence"]["action_method"]["retry_used"] = True
        payload["evidence"]["action_method"]["retry_from_start_url"] = retry_from_start_url
        payload["evidence"]["action_method"]["retry_start_url"] = retry_start_url
        payload["evidence"]["action_method"]["retry_attempts"] = int(retry_attempts)
        return payload

    primary_task = f"""
Reply supportively to exactly {replies_count} group comment(s).
Hard rules:
- Open comments on a group post, then click a visible "Reply" control under an existing comment thread.
- Do NOT leave a top-level standalone comment; each submission must be a threaded reply.
- Prefer posts where comment count is already visible and non-zero.
- If a candidate post has no visible "Reply" control after opening comments, back out immediately and try another post.
- Use this exact supportive wording for each reply:
{reply_text}
- Click "Post a comment" once per reply.
- Never click composer controls like "Post a photo", "Photo", or "Video" for reply submission.
- Finish with DONE only after the threaded reply is visible under that thread or Facebook explicitly confirms pending approval.
- If threaded reply visibility cannot be confirmed, finish with FAILED.
""".strip()

    def _finalize_reply_result(result: Dict) -> Dict:
        adaptive_result = result.get("result") or {}
        blob = _step_blob(adaptive_result)
        action_trace = [str(action).strip().lower() for action in _step_actions(adaptive_result)]
        used_fallback_submit = any("fallback_reply_submit" in action for action in action_trace)
        final_status = str(adaptive_result.get("final_status") or "").strip().lower()

        reply_visible = _contains_any(
            blob,
            [
                "reply sent",
                "reply posted",
                "comment replied",
                "replied",
                "fallback_reply_submit",
            ],
        )
        clicked_reply_cta = any('click "reply"' in action for action in action_trace)
        submitted_comment = any("post a comment" in action for action in action_trace)
        typed_tokens = set()
        for action in action_trace:
            if (
                action.startswith("type:")
                or action.startswith("type_set_exact:")
                or action.startswith("type_skipped_duplicate:")
            ):
                typed_tokens.update(_token_set(action))
        expected_tokens = _token_set(reply_text)
        overlap = len(expected_tokens.intersection(typed_tokens))
        typed_expected_reply = overlap >= max(3, min(6, len(expected_tokens)))
        if final_status == "task_completed" and clicked_reply_cta and submitted_comment and typed_expected_reply:
            reply_visible = True
        if used_fallback_submit and final_status == "task_completed":
            reply_visible = True

        result.setdefault("evidence", {}).setdefault("confirmation", {})
        result["evidence"]["confirmation"]["reply_cta_clicked"] = clicked_reply_cta
        result["evidence"]["confirmation"]["reply_submit_clicked"] = submitted_comment
        result["evidence"]["confirmation"]["reply_text_typed"] = typed_expected_reply

        return _apply_confirmation(
            result,
            key="reply_visible_under_thread",
            value=reply_visible,
            error_message="reply visibility confirmation missing",
        )

    result = await _execute_task(
        run_id=run_id,
        cycle_index=cycle_index,
        step_id=f"cycle_{cycle_index}_replies",
        profile_name=profile_name,
        action_type="comment_replies",
        task=primary_task,
        start_url=start_url or "https://m.facebook.com/groups",
        expected_count=replies_count,
        confirmation_keyword=None,
        max_steps=30,
        profile_identity_confirmed=profile_identity_confirmed,
        identity_check=identity_check,
    )
    result = _finalize_reply_result(result)

    needs_broader_retry = not bool(result.get("success"))
    if not needs_broader_retry:
        return result

    fallback_task = f"""
Reply supportively to exactly {replies_count} group comment(s).
Fallback navigation rules:
- Start from group-search results for "menopause groups".
- Open a group with active posts and non-zero comment threads.
- Do not keep opening/closing the same group preview repeatedly in the same state.
- If a post has no comments or no "Reply" control, back out immediately and try another post or group.
- If still on search cards after several attempts, switch to the "Group posts" tab and open a post with visible comments.
- Click a visible "Reply" control under an existing comment thread before typing.
- Use this exact supportive wording:
{reply_text}
- Click "Post a comment" once.
- Finish with DONE only when the threaded reply is visible under the target thread (or Facebook confirms pending approval).
- If no threaded reply path is available in the current location, keep searching in other groups.
""".strip()
    fallback_start_url = f"https://m.facebook.com/search/groups/?q={quote_plus('menopause groups')}"
    retry_result = await _execute_task(
        run_id=run_id,
        cycle_index=cycle_index,
        step_id=f"cycle_{cycle_index}_replies",
        profile_name=profile_name,
        action_type="comment_replies",
        task=fallback_task,
        start_url=fallback_start_url,
        expected_count=replies_count,
        confirmation_keyword=None,
        max_steps=30,
        profile_identity_confirmed=profile_identity_confirmed,
        identity_check=identity_check,
    )
    retry_result = _finalize_reply_result(retry_result)
    retry_result = _annotate_retry_metadata(
        retry_result,
        retry_from_start_url=start_url or "https://m.facebook.com/groups",
        retry_start_url=fallback_start_url,
        retry_attempts=1,
    )
    if retry_result.get("success"):
        return retry_result

    final_retry_task = f"""
Reply supportively to exactly {replies_count} group comment(s).
Strict fallback rules:
- Start from menopause group search.
- Open the "Group posts" tab first.
- Only open posts that show explicit comment-count text (examples: "5 comments", "󰍹 5comments", "View 12 comments").
- Skip generic "󰍹comment" targets when no count is visible.
- Inside comments, click a visible "Reply" button under an existing comment before typing.
- Use this exact supportive wording:
{reply_text}
- Click "Post a comment" once and confirm the reply appears (or pending approval notice appears).
- If no qualified thread is found after several tries, return FAILED.
""".strip()
    final_retry_start_url = f"https://m.facebook.com/search/groups/?q={quote_plus('menopause groups')}"
    final_retry_result = await _execute_task(
        run_id=run_id,
        cycle_index=cycle_index,
        step_id=f"cycle_{cycle_index}_replies",
        profile_name=profile_name,
        action_type="comment_replies",
        task=final_retry_task,
        start_url=final_retry_start_url,
        expected_count=replies_count,
        confirmation_keyword=None,
        max_steps=24,
        profile_identity_confirmed=profile_identity_confirmed,
        identity_check=identity_check,
    )
    final_retry_result = _finalize_reply_result(final_retry_result)
    final_retry_result = _annotate_retry_metadata(
        final_retry_result,
        retry_from_start_url=start_url or "https://m.facebook.com/groups",
        retry_start_url=final_retry_start_url,
        retry_attempts=2,
    )
    return final_retry_result
