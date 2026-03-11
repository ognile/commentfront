from __future__ import annotations

from typing import Any, Dict, List, Optional

DEFAULT_REDDIT_PROGRAM_ACTIONS = {
    "create_post",
    "comment_post",
    "reply_comment",
    "upvote_post",
    "upvote_comment",
    "join_subreddit",
    "open_target",
}


def normalize_subreddit_name(value: Optional[str]) -> str:
    cleaned = str(value or "").strip()
    cleaned = cleaned.replace("https://www.reddit.com/r/", "")
    cleaned = cleaned.replace("https://reddit.com/r/", "")
    cleaned = cleaned.strip("/").strip()
    if cleaned.lower().startswith("r/"):
        cleaned = cleaned[2:]
    if "/" in cleaned:
        cleaned = cleaned.split("/", 1)[0]
    return cleaned


def _normalize_action_list(values: Any) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for value in list(values or []):
        action = str(value or "").strip().lower()
        if not action or action in seen:
            continue
        seen.add(action)
        normalized.append(action)
    return normalized


def normalize_subreddit_policy(raw: Dict[str, Any]) -> Dict[str, Any]:
    subreddit = normalize_subreddit_name(raw.get("subreddit"))
    if not subreddit:
        return {}
    enabled_actions = _normalize_action_list(raw.get("enabled_actions"))
    requires_user_flair_for = _normalize_action_list(raw.get("requires_user_flair_for"))
    profile_user_flairs: Dict[str, str] = {}
    for profile_name, flair in dict(raw.get("profile_user_flairs") or {}).items():
        normalized_flair = str(flair or "").strip()
        normalized_profile = str(profile_name or "").strip()
        if normalized_profile and normalized_flair:
            profile_user_flairs[normalized_profile] = normalized_flair
    keyword_overrides = [str(value).strip() for value in list(raw.get("keyword_overrides") or []) if str(value).strip()]
    return {
        "subreddit": subreddit,
        "allocation_weight": max(1, int(raw.get("allocation_weight", 1) or 1)),
        "enabled_actions": enabled_actions or sorted(DEFAULT_REDDIT_PROGRAM_ACTIONS),
        "auto_user_flair": bool(raw.get("auto_user_flair", True)),
        "requires_user_flair_for": requires_user_flair_for,
        "profile_user_flairs": profile_user_flairs,
        "keyword_overrides": keyword_overrides,
    }


def normalize_subreddit_policies(values: Any) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    seen = set()
    for raw in list(values or []):
        policy = normalize_subreddit_policy(dict(raw or {}))
        subreddit = str(policy.get("subreddit") or "")
        if not subreddit:
            continue
        key = subreddit.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(policy)
    return normalized


def subreddit_policy_map(values: Any) -> Dict[str, Dict[str, Any]]:
    return {
        str(policy.get("subreddit") or "").lower(): policy
        for policy in normalize_subreddit_policies(values)
        if str(policy.get("subreddit") or "").strip()
    }


def subreddit_policy_for(values: Any, subreddit: Optional[str]) -> Dict[str, Any]:
    normalized = normalize_subreddit_name(subreddit).lower()
    return dict(subreddit_policy_map(values).get(normalized) or {})


def subreddit_allows_action(policy: Dict[str, Any], action: Optional[str]) -> bool:
    normalized_action = str(action or "").strip().lower()
    if not normalized_action:
        return True
    enabled = _normalize_action_list(policy.get("enabled_actions")) or sorted(DEFAULT_REDDIT_PROGRAM_ACTIONS)
    return normalized_action in enabled


def subreddit_keywords(base_keywords: List[str], policy: Dict[str, Any]) -> List[str]:
    overrides = [str(value).strip() for value in list(policy.get("keyword_overrides") or []) if str(value).strip()]
    return overrides or list(base_keywords or [])


def subreddit_requires_user_flair(policy: Dict[str, Any], action: Optional[str]) -> bool:
    normalized_action = str(action or "").strip().lower()
    required_for = _normalize_action_list(policy.get("requires_user_flair_for"))
    return bool(normalized_action and normalized_action in required_for)


def subreddit_auto_user_flair_enabled(policy: Dict[str, Any]) -> bool:
    return bool(policy.get("auto_user_flair", True))


def profile_user_flair(policy: Dict[str, Any], profile_name: Optional[str]) -> Optional[str]:
    mapping = dict(policy.get("profile_user_flairs") or {})
    flair = str(mapping.get(str(profile_name or "").strip()) or "").strip()
    return flair or None


def subreddit_profile_is_eligible(policy: Dict[str, Any], *, profile_name: Optional[str], action: Optional[str]) -> bool:
    if not subreddit_allows_action(policy, action):
        return False
    return True
