"""
Persistent store and compiler for Reddit automation programs.
"""

from __future__ import annotations

import copy
import json
import os
import random
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from safe_io import atomic_write_json, safe_read_json

DEFAULT_TIMEZONE = "America/New_York"
DEFAULT_RANDOM_WINDOW = [{"start_hour": 8, "end_hour": 22}]
DEFAULT_REDDIT_REALISM_POLICY = {
    "forbid_own_content_interactions": True,
    "require_conversation_context": True,
    "require_subreddit_style_match": True,
    "forbid_operator_language": True,
    "forbid_meta_testing_language": True,
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(value: Optional[datetime] = None) -> str:
    dt = value or _utc_now()
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _clone(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _is_stale_runtime_status_regression(existing: Optional[Dict[str, Any]], incoming: Dict[str, Any]) -> bool:
    if not existing:
        return False
    existing_status = str(existing.get("status") or "")
    incoming_status = str(incoming.get("status") or "")
    if existing_status not in {"paused", "cancelled"}:
        return False
    if incoming_status in {"paused", "cancelled"}:
        return False
    existing_updated_at = _parse_iso(existing.get("updated_at"))
    incoming_updated_at = _parse_iso(incoming.get("updated_at"))
    if existing_updated_at and incoming_updated_at:
        return incoming_updated_at < existing_updated_at
    return bool(existing_updated_at) and not incoming_updated_at


def _merge_preserved_program_status(existing: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
    merged = _clone(incoming)
    preserved_status = str(existing.get("status") or "")
    merged["status"] = preserved_status
    if preserved_status != "cancelled":
        return merged
    existing_items = {
        str(item.get("id") or ""): item
        for item in ((existing.get("compiled") or {}).get("work_items") or [])
    }
    for item in ((merged.get("compiled") or {}).get("work_items") or []):
        existing_item = existing_items.get(str(item.get("id") or ""))
        if not existing_item:
            continue
        existing_item_status = str(existing_item.get("status") or "")
        incoming_item_status = str(item.get("status") or "")
        if existing_item_status == "cancelled" and incoming_item_status not in {"completed", "blocked", "exhausted"}:
            item["status"] = "cancelled"
            if not item.get("error") and existing_item.get("error"):
                item["error"] = existing_item.get("error")
    return merged


def _normalize_profile_name(value: Optional[str]) -> str:
    return str(value or "").strip().lower()


def _normalize_subreddit_name(value: Optional[str]) -> str:
    cleaned = str(value or "").strip()
    cleaned = cleaned.replace("https://www.reddit.com/r/", "")
    cleaned = cleaned.replace("https://reddit.com/r/", "")
    cleaned = cleaned.strip("/").strip()
    if cleaned.lower().startswith("r/"):
        cleaned = cleaned[2:]
    if "/" in cleaned:
        cleaned = cleaned.split("/", 1)[0]
    return cleaned


def _item_subreddit(item: Dict[str, Any]) -> str:
    value = (
        item.get("subreddit")
        or ((item.get("discovered_target") or {}).get("subreddit"))
        or ((item.get("result") or {}).get("subreddit"))
        or item.get("target_url")
        or item.get("target_comment_url")
        or ((item.get("result") or {}).get("target_url"))
        or ((item.get("result") or {}).get("target_comment_url"))
    )
    return _normalize_subreddit_name(value)


def _item_failure_class(item: Dict[str, Any]) -> str:
    result = dict(item.get("result") or {})
    failure_class = str(result.get("failure_class") or "").strip()
    if failure_class:
        return failure_class
    error = str(item.get("error") or result.get("error") or "").lower()
    if "community" in error and "ban" in error:
        return "community_restricted"
    if "session" in error:
        return "session_invalid"
    if "policy" in error:
        return "policy_invalid"
    if "generation" in error:
        return "generation_failed"
    if "target" in error or "not found" in error:
        return "target_unavailable"
    if "net::" in error or "timeout" in error or "bad gateway" in error:
        return "infrastructure"
    return "execution_failed"


def _random_windows_from_spec(schedule: Dict[str, Any]) -> List[Dict[str, int]]:
    windows = schedule.get("random_windows") or DEFAULT_RANDOM_WINDOW
    normalized: List[Dict[str, int]] = []
    for item in windows:
        try:
            start_hour = max(0, min(23, int(item.get("start_hour", 8))))
            end_hour = max(start_hour + 1, min(24, int(item.get("end_hour", 22))))
        except Exception:
            start_hour, end_hour = 8, 22
        normalized.append({"start_hour": start_hour, "end_hour": end_hour})
    return normalized or copy.deepcopy(DEFAULT_RANDOM_WINDOW)


def _local_date_for_day(start_local: datetime, day_offset: int) -> datetime.date:
    return (start_local + timedelta(days=day_offset)).date()


def _scheduled_at_for_day(
    *,
    start_local: datetime,
    day_offset: int,
    windows: List[Dict[str, int]],
    rng: random.Random,
    local_tz: ZoneInfo,
) -> str:
    local_date = _local_date_for_day(start_local, day_offset)
    window = windows[day_offset % len(windows)]
    hour = rng.randint(int(window["start_hour"]), int(window["end_hour"]) - 1)
    minute = rng.randint(0, 59)
    second = rng.randint(0, 59)
    scheduled_local = datetime(
        year=local_date.year,
        month=local_date.month,
        day=local_date.day,
        hour=hour,
        minute=minute,
        second=second,
        tzinfo=local_tz,
    )
    if day_offset == 0 and scheduled_local < start_local:
        scheduled_local = start_local + timedelta(seconds=15)
    return _utc_iso(scheduled_local.astimezone(timezone.utc))


def compile_reddit_program_state(
    *,
    program_id: str,
    spec: Dict[str, Any],
    created_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = created_at or _utc_now()
    profile_selection = dict(spec.get("profile_selection") or {})
    schedule = dict(spec.get("schedule") or {})
    topic_constraints = dict(spec.get("topic_constraints") or {})
    content_assignments = dict(spec.get("content_assignments") or {})
    engagement_quotas = dict(spec.get("engagement_quotas") or {})
    execution_policy = dict(spec.get("execution_policy") or {})

    timezone_name = str(schedule.get("timezone") or DEFAULT_TIMEZONE)
    try:
        local_tz = ZoneInfo(timezone_name)
    except Exception:
        local_tz = ZoneInfo(DEFAULT_TIMEZONE)
        timezone_name = DEFAULT_TIMEZONE

    start_at = _parse_iso(schedule.get("start_at")) or now
    start_local = start_at.astimezone(local_tz)
    duration_days = max(1, int(schedule.get("duration_days", 1)))
    windows = _random_windows_from_spec(schedule)
    profile_names = [
        str(name).strip()
        for name in list(profile_selection.get("profile_names") or [])
        if str(name).strip()
    ]

    random_upvote_action = str(engagement_quotas.get("random_upvote_action") or "upvote_post").strip().lower()
    if random_upvote_action not in {"upvote_post", "upvote_comment"}:
        random_upvote_action = "upvote_post"
    upvotes_per_day = max(0, int(engagement_quotas.get("upvotes_per_day", 0)))
    upvotes_min_per_day = max(0, int(engagement_quotas.get("upvotes_min_per_day", upvotes_per_day)))
    upvotes_max_per_day = max(upvotes_min_per_day, int(engagement_quotas.get("upvotes_max_per_day", max(upvotes_per_day, upvotes_min_per_day))))
    posts_min_per_day = max(0, int(engagement_quotas.get("posts_min_per_day", 0)))
    posts_max_per_day = max(posts_min_per_day, int(engagement_quotas.get("posts_max_per_day", posts_min_per_day)))
    comment_upvote_min_per_day = max(0, int(engagement_quotas.get("comment_upvote_min_per_day", 0)))
    comment_upvote_max_per_day = max(comment_upvote_min_per_day, int(engagement_quotas.get("comment_upvote_max_per_day", comment_upvote_min_per_day)))
    reply_min_per_day = max(0, int(engagement_quotas.get("reply_min_per_day", 0)))
    reply_max_per_day = max(reply_min_per_day, int(engagement_quotas.get("reply_max_per_day", reply_min_per_day)))
    random_reply_templates = [
        str(item).strip()
        for item in list(engagement_quotas.get("random_reply_templates") or [])
        if str(item).strip()
    ]
    mandatory_join_urls = [
        str(item).strip()
        for item in list(topic_constraints.get("mandatory_join_urls") or [])
        if str(item).strip()
    ]
    allowed_subreddits = [
        _normalize_subreddit_name(item)
        for item in list(topic_constraints.get("subreddits") or [])
        if _normalize_subreddit_name(item)
    ]
    for join_url in mandatory_join_urls:
        subreddit = _normalize_subreddit_name(join_url)
        if subreddit and subreddit not in allowed_subreddits:
            allowed_subreddits.append(subreddit)
    generation_config = dict(spec.get("generation_config") or {})
    notification_config = dict(spec.get("notification_config") or {})
    realism_policy = dict(spec.get("realism_policy") or {})

    seed = str(spec.get("seed") or program_id)
    work_items: List[Dict[str, Any]] = []
    day_plans: List[Dict[str, Any]] = []
    assignments = list(content_assignments.get("items") or [])

    assignments_by_day_profile: Dict[tuple[int, str], List[Dict[str, Any]]] = {}
    for item in assignments:
        day_offset = max(0, min(duration_days - 1, int(item.get("day_offset", 0))))
        profile_name = str(item.get("profile_name") or "").strip()
        if not profile_name:
            continue
        assignments_by_day_profile.setdefault((day_offset, profile_name), []).append(dict(item))

    for day_offset in range(duration_days):
        local_date = _local_date_for_day(start_local, day_offset).isoformat()
        profile_plans: Dict[str, Dict[str, Any]] = {}

        for profile_name in profile_names:
            profile_seed = f"{seed}:{profile_name}:{local_date}"
            rng = random.Random(profile_seed)
            random_replies_for_day = rng.randint(reply_min_per_day, reply_max_per_day) if reply_max_per_day > 0 else 0
            generated_posts_for_day = rng.randint(posts_min_per_day, posts_max_per_day) if posts_max_per_day > 0 else 0
            total_upvotes_for_day = rng.randint(upvotes_min_per_day, upvotes_max_per_day) if upvotes_max_per_day > 0 else 0
            if total_upvotes_for_day > 0:
                max_comment_upvotes = min(comment_upvote_max_per_day, total_upvotes_for_day)
                min_comment_upvotes = min(comment_upvote_min_per_day, max_comment_upvotes)
                comment_upvotes_for_day = rng.randint(min_comment_upvotes, max_comment_upvotes) if max_comment_upvotes > 0 else 0
            else:
                comment_upvotes_for_day = 0
            post_upvotes_for_day = max(0, total_upvotes_for_day - comment_upvotes_for_day)
            profile_plans[profile_name] = {
                "planned_post_upvotes": post_upvotes_for_day,
                "planned_comment_upvotes": comment_upvotes_for_day,
                "planned_random_replies": random_replies_for_day,
                "planned_generated_posts": generated_posts_for_day,
                "planned_mandatory_joins": len(mandatory_join_urls) if day_offset == 0 else 0,
                "planned_exact_assignments": len(assignments_by_day_profile.get((day_offset, profile_name), [])),
            }

            if day_offset == 0:
                join_window = [{"start_hour": windows[0]["start_hour"], "end_hour": min(24, windows[0]["start_hour"] + 1)}]
                for join_index, join_url in enumerate(mandatory_join_urls):
                    work_items.append(
                        {
                            "id": f"work_{uuid.uuid4().hex[:12]}",
                            "source": "mandatory_join",
                            "assignment_id": f"{profile_name}_join_{join_index}",
                            "profile_name": profile_name,
                            "local_date": local_date,
                            "scheduled_at": _scheduled_at_for_day(
                                start_local=start_local,
                                day_offset=day_offset,
                                windows=join_window,
                                rng=random.Random(f"{profile_seed}:join:{join_index}"),
                                local_tz=local_tz,
                            ),
                            "status": "pending",
                            "attempts": 0,
                            "last_attempt_at": None,
                            "completed_at": None,
                            "action": "join_subreddit",
                            "text": None,
                            "title": None,
                            "body": None,
                            "subreddit": _normalize_subreddit_name(join_url),
                            "target_url": join_url,
                            "target_comment_url": None,
                            "target_mode": "explicit",
                            "day_offset": day_offset,
                            "verification_requirements": [],
                            "result": None,
                            "error": None,
                            "discovered_target": None,
                        }
                    )

            for assignment_index, assignment in enumerate(assignments_by_day_profile.get((day_offset, profile_name), [])):
                work_items.append(
                    {
                        "id": f"work_{uuid.uuid4().hex[:12]}",
                        "source": "explicit_assignment",
                        "assignment_id": str(assignment.get("id") or f"{profile_name}_{day_offset}_{assignment_index}"),
                        "profile_name": profile_name,
                        "local_date": local_date,
                        "scheduled_at": _scheduled_at_for_day(
                            start_local=start_local,
                            day_offset=day_offset,
                            windows=windows,
                            rng=random.Random(f"{profile_seed}:assignment:{assignment_index}"),
                            local_tz=local_tz,
                        ),
                        "status": "pending",
                        "attempts": 0,
                        "last_attempt_at": None,
                        "completed_at": None,
                        "action": str(assignment.get("action") or "comment_post").strip(),
                        "text": assignment.get("text"),
                        "title": assignment.get("title"),
                        "body": assignment.get("body"),
                        "subreddit": assignment.get("subreddit"),
                        "target_url": assignment.get("target_url"),
                        "target_comment_url": assignment.get("target_comment_url"),
                        "target_mode": "explicit",
                        "day_offset": day_offset,
                        "verification_requirements": list(assignment.get("verification_requirements") or []),
                        "result": None,
                        "error": None,
                        "discovered_target": None,
                    }
                )

            for quota_index in range(post_upvotes_for_day):
                work_items.append(
                    {
                        "id": f"work_{uuid.uuid4().hex[:12]}",
                        "source": "quota_post_upvote",
                        "assignment_id": None,
                        "profile_name": profile_name,
                        "local_date": local_date,
                        "scheduled_at": _scheduled_at_for_day(
                            start_local=start_local,
                            day_offset=day_offset,
                            windows=windows,
                            rng=random.Random(f"{profile_seed}:post_upvote:{quota_index}"),
                            local_tz=local_tz,
                        ),
                        "status": "pending",
                        "attempts": 0,
                        "last_attempt_at": None,
                        "completed_at": None,
                        "action": "upvote_post",
                        "text": None,
                        "title": None,
                        "body": None,
                        "subreddit": None,
                        "target_url": None,
                        "target_comment_url": None,
                        "target_mode": "discover_post",
                        "day_offset": day_offset,
                        "verification_requirements": [],
                        "result": None,
                        "error": None,
                        "discovered_target": None,
                    }
                )

            for quota_index in range(comment_upvotes_for_day):
                work_items.append(
                    {
                        "id": f"work_{uuid.uuid4().hex[:12]}",
                        "source": "quota_comment_upvote",
                        "assignment_id": None,
                        "profile_name": profile_name,
                        "local_date": local_date,
                        "scheduled_at": _scheduled_at_for_day(
                            start_local=start_local,
                            day_offset=day_offset,
                            windows=windows,
                            rng=random.Random(f"{profile_seed}:comment_upvote:{quota_index}"),
                            local_tz=local_tz,
                        ),
                        "status": "pending",
                        "attempts": 0,
                        "last_attempt_at": None,
                        "completed_at": None,
                        "action": "upvote_comment",
                        "text": None,
                        "title": None,
                        "body": None,
                        "subreddit": None,
                        "target_url": None,
                        "target_comment_url": None,
                        "target_mode": "discover_comment",
                        "day_offset": day_offset,
                        "verification_requirements": [],
                        "result": None,
                        "error": None,
                        "discovered_target": None,
                    }
                )

            for quota_index in range(random_replies_for_day):
                reply_text = None
                if random_reply_templates:
                    reply_text = random_reply_templates[quota_index % len(random_reply_templates)]
                work_items.append(
                    {
                        "id": f"work_{uuid.uuid4().hex[:12]}",
                        "source": "quota_random_reply",
                        "assignment_id": None,
                        "profile_name": profile_name,
                        "local_date": local_date,
                        "scheduled_at": _scheduled_at_for_day(
                            start_local=start_local,
                            day_offset=day_offset,
                            windows=windows,
                            rng=random.Random(f"{profile_seed}:reply:{quota_index}"),
                            local_tz=local_tz,
                        ),
                        "status": "pending",
                        "attempts": 0,
                        "last_attempt_at": None,
                        "completed_at": None,
                        "action": "reply_comment",
                        "text": reply_text,
                        "title": None,
                        "body": None,
                        "subreddit": None,
                        "target_url": None,
                        "target_comment_url": None,
                        "target_mode": "discover_comment",
                        "day_offset": day_offset,
                        "verification_requirements": [],
                        "result": None,
                        "error": None,
                        "discovered_target": None,
                    }
                )

            subreddit_pool = list(allowed_subreddits)
            if not subreddit_pool and mandatory_join_urls:
                subreddit_pool = [_normalize_subreddit_name(join_url) for join_url in mandatory_join_urls if _normalize_subreddit_name(join_url)]
            for quota_index in range(generated_posts_for_day):
                target_subreddit = subreddit_pool[quota_index % len(subreddit_pool)] if subreddit_pool else None
                work_items.append(
                    {
                        "id": f"work_{uuid.uuid4().hex[:12]}",
                        "source": "quota_generated_post",
                        "assignment_id": None,
                        "profile_name": profile_name,
                        "local_date": local_date,
                        "scheduled_at": _scheduled_at_for_day(
                            start_local=start_local,
                            day_offset=day_offset,
                            windows=windows,
                            rng=random.Random(f"{profile_seed}:post:{quota_index}"),
                            local_tz=local_tz,
                        ),
                        "status": "pending",
                        "attempts": 0,
                        "last_attempt_at": None,
                        "completed_at": None,
                        "action": "create_post",
                        "text": None,
                        "title": None,
                        "body": None,
                        "subreddit": target_subreddit,
                        "target_url": None,
                        "target_comment_url": None,
                        "target_mode": "generate_post",
                        "day_offset": day_offset,
                        "verification_requirements": [],
                        "result": None,
                        "error": None,
                        "discovered_target": None,
                    }
                )

        day_plans.append(
            {
                "day_offset": day_offset,
                "local_date": local_date,
                "profiles": profile_plans,
            }
        )

    work_items.sort(key=lambda item: (item.get("scheduled_at") or "", item.get("profile_name") or "", item.get("id") or ""))

    program = {
        "id": program_id,
        "platform": "reddit",
        "status": "active",
        "created_at": _utc_iso(now),
        "updated_at": _utc_iso(now),
        "last_run_at": None,
        "last_result": None,
        "spec": {
            **_clone(spec),
            "schedule": {
                **schedule,
                "timezone": timezone_name,
                "duration_days": duration_days,
                "random_windows": windows,
                "start_at": _utc_iso(start_at),
            },
            "topic_constraints": {
                "subreddits": allowed_subreddits,
                "keywords": list(topic_constraints.get("keywords") or []),
                "explicit_post_targets": list(topic_constraints.get("explicit_post_targets") or []),
                "explicit_comment_targets": list(topic_constraints.get("explicit_comment_targets") or []),
                "allow_own_content_targets": bool(topic_constraints.get("allow_own_content_targets", False)),
                "mandatory_join_urls": mandatory_join_urls,
            },
            "execution_policy": {
                "strict_quotas": bool(execution_policy.get("strict_quotas", True)),
                "allow_target_reuse_within_day": bool(execution_policy.get("allow_target_reuse_within_day", False)),
                "cooldown_minutes": max(0, int(execution_policy.get("cooldown_minutes", 15))),
                "max_actions_per_tick": max(1, int(execution_policy.get("max_actions_per_tick", 3))),
                "max_discovery_posts_per_subreddit": max(1, int(execution_policy.get("max_discovery_posts_per_subreddit", 6))),
                "max_comment_candidates_per_post": max(1, int(execution_policy.get("max_comment_candidates_per_post", 8))),
                "retry_delay_minutes": max(1, int(execution_policy.get("retry_delay_minutes", 20))),
                "max_attempts_per_item": max(1, int(execution_policy.get("max_attempts_per_item", 5))),
                "target_resolution_timeout_seconds": max(1, int(execution_policy.get("target_resolution_timeout_seconds", 90))),
            },
            "engagement_quotas": {
                "upvotes_per_day": upvotes_per_day,
                "upvotes_min_per_day": upvotes_min_per_day,
                "upvotes_max_per_day": upvotes_max_per_day,
                "posts_min_per_day": posts_min_per_day,
                "posts_max_per_day": posts_max_per_day,
                "comment_upvote_min_per_day": comment_upvote_min_per_day,
                "comment_upvote_max_per_day": comment_upvote_max_per_day,
                "reply_min_per_day": reply_min_per_day,
                "reply_max_per_day": reply_max_per_day,
                "random_reply_templates": random_reply_templates,
                "random_upvote_action": random_upvote_action,
            },
            "generation_config": {
                "style_sample_count": max(1, int(generation_config.get("style_sample_count", 3))),
                "writing_rule_paths": list(generation_config.get("writing_rule_paths") or []),
                "uniqueness_scope": str(generation_config.get("uniqueness_scope") or "program"),
            },
            "realism_policy": {
                "forbid_own_content_interactions": bool(
                    realism_policy.get(
                        "forbid_own_content_interactions",
                        DEFAULT_REDDIT_REALISM_POLICY["forbid_own_content_interactions"],
                    )
                ),
                "require_conversation_context": bool(
                    realism_policy.get(
                        "require_conversation_context",
                        DEFAULT_REDDIT_REALISM_POLICY["require_conversation_context"],
                    )
                ),
                "require_subreddit_style_match": bool(
                    realism_policy.get(
                        "require_subreddit_style_match",
                        DEFAULT_REDDIT_REALISM_POLICY["require_subreddit_style_match"],
                    )
                ),
                "forbid_operator_language": bool(
                    realism_policy.get(
                        "forbid_operator_language",
                        DEFAULT_REDDIT_REALISM_POLICY["forbid_operator_language"],
                    )
                ),
                "forbid_meta_testing_language": bool(
                    realism_policy.get(
                        "forbid_meta_testing_language",
                        DEFAULT_REDDIT_REALISM_POLICY["forbid_meta_testing_language"],
                    )
                ),
            },
            "notification_config": {
                "email_enabled": bool(notification_config.get("email_enabled", True)),
                "email_account_mode": str(notification_config.get("email_account_mode") or "default_gog_account"),
                "daily_summary_hour": max(0, min(23, int(notification_config.get("daily_summary_hour", 20)))),
                "hard_failure_alerts_enabled": bool(notification_config.get("hard_failure_alerts_enabled", False)),
                "recipient_email": str(notification_config.get("recipient_email") or "").strip() or None,
            },
        },
        "compiled": {
            "days": day_plans,
            "work_items": work_items,
        },
        "target_history": [],
        "generated_text_history": [],
        "generated_text_records": [],
        "recent_attempt_ids": [],
        "events": [],
        "notification_log": [],
        "remaining_contract": {},
        "daily_progress": {},
        "join_progress_matrix": {},
        "contract_totals": {},
        "failure_summary": {},
        "next_run_at": None,
    }
    refresh_reddit_program_state(program)
    return program


def refresh_reddit_program_state(program: Dict[str, Any]) -> Dict[str, Any]:
    spec = program.setdefault("spec", {})
    realism_policy = dict(spec.get("realism_policy") or {})
    spec["realism_policy"] = {
        key: bool(realism_policy.get(key, default_value))
        for key, default_value in DEFAULT_REDDIT_REALISM_POLICY.items()
    }
    notification_config = dict(spec.get("notification_config") or {})
    notification_config["hard_failure_alerts_enabled"] = bool(notification_config.get("hard_failure_alerts_enabled", False))
    spec["notification_config"] = notification_config

    work_items = list(((program.get("compiled") or {}).get("work_items") or []))
    remaining: Dict[str, int] = {}
    retryable_remaining: Dict[str, int] = {}
    daily_progress: Dict[str, Dict[str, Any]] = {}
    contract_totals: Dict[str, int] = {}
    join_progress_matrix: Dict[str, Dict[str, str]] = {}
    next_run_at: Optional[str] = None
    terminal_non_completed = False
    failures_by_action: Dict[str, int] = {}
    failures_by_profile: Dict[str, int] = {}
    failures_by_subreddit: Dict[str, int] = {}
    failures_by_class: Dict[str, int] = {}

    for item in work_items:
        status = str(item.get("status") or "pending")
        action = str(item.get("action") or "unknown")
        contract_totals[action] = contract_totals.get(action, 0) + 1
        profile_name = str(item.get("profile_name") or "")
        local_date = str(item.get("local_date") or "")
        day_progress = daily_progress.setdefault(local_date, {})
        profile_progress = day_progress.setdefault(
            profile_name,
            {
                "planned": {},
                "completed": {},
                "pending": {},
                "blocked": {},
            },
        )
        profile_progress["planned"][action] = profile_progress["planned"].get(action, 0) + 1

        if status == "completed":
            profile_progress["completed"][action] = profile_progress["completed"].get(action, 0) + 1
        elif status in {"blocked", "exhausted", "cancelled"}:
            profile_progress["blocked"][action] = profile_progress["blocked"].get(action, 0) + 1
            remaining[action] = remaining.get(action, 0) + 1
            terminal_non_completed = True
            failures_by_action[action] = failures_by_action.get(action, 0) + 1
            failures_by_profile[profile_name] = failures_by_profile.get(profile_name, 0) + 1
            subreddit = _item_subreddit(item)
            if subreddit:
                failures_by_subreddit[subreddit] = failures_by_subreddit.get(subreddit, 0) + 1
            failure_class = _item_failure_class(item)
            failures_by_class[failure_class] = failures_by_class.get(failure_class, 0) + 1
        else:
            profile_progress["pending"][action] = profile_progress["pending"].get(action, 0) + 1
            remaining[action] = remaining.get(action, 0) + 1
            retryable_remaining[action] = retryable_remaining.get(action, 0) + 1
            scheduled_at = item.get("scheduled_at")
            if scheduled_at and (next_run_at is None or str(scheduled_at) < str(next_run_at)):
                next_run_at = str(scheduled_at)

        if action == "join_subreddit":
            join_progress_matrix.setdefault(profile_name, {})[str(item.get("target_url") or "")] = status

    recent_attempt_ids = []
    for item in reversed(work_items):
        attempt_id = ((item.get("result") or {}).get("attempt_id") or "").strip()
        if attempt_id and attempt_id not in recent_attempt_ids:
            recent_attempt_ids.append(attempt_id)
        if len(recent_attempt_ids) >= 50:
            break

    status = str(program.get("status") or "active")
    if status not in {"paused", "cancelled"}:
        if retryable_remaining:
            status = "active"
        elif terminal_non_completed:
            status = "exhausted"
        else:
            status = "completed"

    program["status"] = status
    program["remaining_contract"] = remaining
    program["daily_progress"] = daily_progress
    program["contract_totals"] = contract_totals
    program["join_progress_matrix"] = join_progress_matrix
    program["failure_summary"] = {
        "by_action": failures_by_action,
        "by_profile": failures_by_profile,
        "by_subreddit": failures_by_subreddit,
        "by_class": failures_by_class,
    }
    program["recent_attempt_ids"] = recent_attempt_ids
    program["next_run_at"] = next_run_at if status == "active" else None
    program["updated_at"] = _utc_iso()
    return program


class RedditProgramStore:
    def __init__(self, file_path: Optional[str] = None):
        self.file_path = file_path or self._default_path()
        self._lock = threading.RLock()
        self.state = self._load()

    def _default_path(self) -> str:
        configured = os.getenv("REDDIT_PROGRAMS_PATH")
        if configured:
            return configured
        data_dir = os.getenv("DATA_DIR", "/data")
        preferred = os.path.join(data_dir, "reddit_programs_state.json")
        try:
            os.makedirs(os.path.dirname(preferred), exist_ok=True)
            return preferred
        except Exception:
            return os.path.join(os.path.dirname(__file__), "reddit_programs_state.json")

    def _empty_state(self) -> Dict[str, Any]:
        return {
            "updated_at": _utc_iso(),
            "programs": {},
            "program_order": [],
            "scheduler": {
                "enabled": True,
                "is_running": False,
                "last_tick_at": None,
                "last_error": None,
                "last_processed_count": 0,
            },
        }

    def _load(self) -> Dict[str, Any]:
        data = safe_read_json(self.file_path)
        if not isinstance(data, dict):
            return self._empty_state()
        baseline = self._empty_state()
        baseline.update(data)
        baseline.setdefault("programs", {})
        baseline.setdefault("program_order", [])
        baseline.setdefault("scheduler", self._empty_state()["scheduler"])
        return baseline

    def save(self) -> bool:
        with self._lock:
            self.state["updated_at"] = _utc_iso()
            return atomic_write_json(self.file_path, self.state)

    def preview_program(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        return compile_reddit_program_state(
            program_id=f"reddit_program_preview_{uuid.uuid4().hex[:8]}",
            spec=spec,
        )

    def create_program(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            program_id = f"reddit_program_{uuid.uuid4().hex[:10]}"
            program = compile_reddit_program_state(program_id=program_id, spec=spec)
            self.state["programs"][program_id] = program
            self.state["program_order"].append(program_id)
            self.save()
            return _clone(program)

    def list_programs(self) -> List[Dict[str, Any]]:
        with self._lock:
            items = [refresh_reddit_program_state(_clone(self.state["programs"][program_id])) for program_id in self.state.get("program_order", []) if program_id in self.state.get("programs", {})]
        return items

    def get_program(self, program_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            program = self.state.get("programs", {}).get(program_id)
            if not program:
                return None
            return refresh_reddit_program_state(_clone(program))

    def save_program(self, program: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            existing = self.state.get("programs", {}).get(program.get("id"))
            candidate = _clone(program)
            if _is_stale_runtime_status_regression(existing, candidate):
                candidate = _merge_preserved_program_status(existing, candidate)
            refreshed = refresh_reddit_program_state(candidate)
            self.state.setdefault("programs", {})[refreshed["id"]] = refreshed
            if refreshed["id"] not in self.state.setdefault("program_order", []):
                self.state["program_order"].append(refreshed["id"])
            self.save()
            return _clone(refreshed)

    def update_program(self, program_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self._lock:
            existing = self.state.get("programs", {}).get(program_id)
            if not existing:
                return None

            mutable_spec_keys = {
                "profile_selection",
                "schedule",
                "topic_constraints",
                "content_assignments",
                "engagement_quotas",
                "generation_config",
                "notification_config",
                "verification_contract",
                "execution_policy",
            }
            incoming_spec_updates = {key: value for key, value in updates.items() if key in mutable_spec_keys and value is not None}

            program = _clone(existing)
            execution_started = any(int(item.get("attempts", 0)) > 0 or str(item.get("status") or "") != "pending" for item in ((program.get("compiled") or {}).get("work_items") or []))
            if incoming_spec_updates:
                if execution_started:
                    raise ValueError("cannot update the program spec after execution has started")
                merged_spec = dict(program.get("spec") or {})
                for key, value in incoming_spec_updates.items():
                    merged_spec[key] = value
                program = compile_reddit_program_state(program_id=program_id, spec=merged_spec)

            if "status" in updates and updates["status"] is not None:
                program["status"] = str(updates["status"])
                if program["status"] == "paused":
                    program["next_run_at"] = None
                elif program["status"] == "cancelled":
                    for item in (program.get("compiled") or {}).get("work_items", []):
                        if str(item.get("status") or "pending") in {"pending", "running"}:
                            item["status"] = "cancelled"

            saved = self.save_program(program)
            return saved

    def get_due_programs(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        current = now or _utc_now()
        due: List[Dict[str, Any]] = []
        with self._lock:
            for program_id in self.state.get("program_order", []):
                program = self.state.get("programs", {}).get(program_id)
                if not program:
                    continue
                refreshed = refresh_reddit_program_state(_clone(program))
                if refreshed.get("status") != "active":
                    continue
                next_run = _parse_iso(refreshed.get("next_run_at"))
                if next_run and next_run <= current:
                    due.append(refreshed)
        due.sort(key=lambda item: item.get("next_run_at") or "")
        return due

    def update_scheduler_state(self, **updates: Any) -> Dict[str, Any]:
        with self._lock:
            scheduler = self.state.setdefault("scheduler", self._empty_state()["scheduler"])
            scheduler.update(updates)
            self.save()
            return _clone(scheduler)

    def get_scheduler_state(self) -> Dict[str, Any]:
        with self._lock:
            return _clone(self.state.get("scheduler", {}))

    def recover_interrupted_work(self) -> List[str]:
        recovered: List[str] = []
        with self._lock:
            for program_id, program in list(self.state.get("programs", {}).items()):
                changed = False
                for item in (program.get("compiled") or {}).get("work_items", []):
                    if str(item.get("status") or "") == "running":
                        item["status"] = "pending"
                        changed = True
                if str(program.get("status") or "") == "running":
                    program["status"] = "active"
                    changed = True
                if changed:
                    refresh_reddit_program_state(program)
                    recovered.append(program_id)
            if recovered:
                self.save()
        return recovered
