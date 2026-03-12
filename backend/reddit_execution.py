from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from reddit_subreddit_policies import normalize_subreddit_name

REDDIT_EXECUTION_TARGET_KINDS = ("subreddit", "post", "comment")
REDDIT_EXECUTION_TARGET_STRATEGIES = ("explicit", "discover")
REDDIT_EXECUTION_ACTION_TYPES = ("browse", "open", "join", "upvote", "comment", "reply", "create_post")
REDDIT_EXECUTION_DEFAULT_VERIFICATION = {
    "require_success_confirmed": True,
    "require_attempt_id": True,
    "required_evidence_summary": True,
    "required_target_reference": True,
}

_ACTION_TARGET_CAPABILITIES = {
    "browse": {"subreddit"},
    "open": {"subreddit", "post", "comment"},
    "join": {"subreddit"},
    "upvote": {"post", "comment"},
    "comment": {"post"},
    "reply": {"comment"},
    "create_post": {"subreddit"},
}

_LEGACY_TO_CANONICAL_ACTION = {
    "browse_feed": "browse",
    "upvote": "upvote",
    "upvote_post": "upvote",
    "upvote_comment": "upvote",
    "join_subreddit": "join",
    "open_target": "open",
    "create_post": "create_post",
    "comment_post": "comment",
    "reply_comment": "reply",
}

_LEGACY_ACTION_TARGET_KIND = {
    "browse_feed": "subreddit",
    "upvote": "post",
    "upvote_post": "post",
    "upvote_comment": "comment",
    "join_subreddit": "subreddit",
    "open_target": None,
    "create_post": "subreddit",
    "comment_post": "post",
    "reply_comment": "comment",
}

_CANONICAL_TO_RUNTIME_ACTION = {
    ("browse", "subreddit"): "browse_feed",
    ("open", "subreddit"): "open_target",
    ("open", "post"): "open_target",
    ("open", "comment"): "open_target",
    ("join", "subreddit"): "join_subreddit",
    ("upvote", "post"): "upvote_post",
    ("upvote", "comment"): "upvote_comment",
    ("comment", "post"): "comment_post",
    ("reply", "comment"): "reply_comment",
    ("create_post", "subreddit"): "create_post",
}

_DISCOVER_TARGET_MODE = {
    "post": "discover_post",
    "comment": "discover_comment",
}


def _clone(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _clean_str(value: Optional[str]) -> Optional[str]:
    cleaned = str(value or "").strip()
    return cleaned or None


def _clean_list(values: Any) -> List[str]:
    return [str(value).strip() for value in list(values or []) if str(value).strip()]


def subreddit_url(subreddit: Optional[str]) -> Optional[str]:
    normalized = normalize_subreddit_name(subreddit)
    if not normalized:
        return None
    return f"https://www.reddit.com/r/{normalized}/"


def normalize_reddit_execution_actor(actor: Dict[str, Any]) -> Dict[str, Any]:
    profile_name = _clean_str((actor or {}).get("profile_name"))
    if not profile_name:
        raise ValueError("actors[].profile_name is required")
    return {"profile_name": profile_name}


def normalize_reddit_execution_discovery_constraints(value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    constraints = dict(value or {})
    subreddits = [normalize_subreddit_name(entry) for entry in _clean_list(constraints.get("subreddits")) if normalize_subreddit_name(entry)]
    return {
        "subreddits": subreddits,
        "keywords": _clean_list(constraints.get("keywords")),
        "explicit_post_targets": _clean_list(constraints.get("explicit_post_targets")),
        "explicit_comment_targets": _clean_list(constraints.get("explicit_comment_targets")),
        "allow_own_content_targets": bool(constraints.get("allow_own_content_targets", False)),
        "mandatory_join_urls": _clean_list(constraints.get("mandatory_join_urls")),
    }


def normalize_reddit_execution_target(target: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(target or {})
    kind = str(payload.get("kind") or "").strip().lower()
    strategy = str(payload.get("strategy") or "").strip().lower()
    if kind not in REDDIT_EXECUTION_TARGET_KINDS:
        raise ValueError(f"target.kind must be one of {REDDIT_EXECUTION_TARGET_KINDS}")
    if strategy not in REDDIT_EXECUTION_TARGET_STRATEGIES:
        raise ValueError(f"target.strategy must be one of {REDDIT_EXECUTION_TARGET_STRATEGIES}")

    target_url = _clean_str(payload.get("target_url"))
    target_comment_url = _clean_str(payload.get("target_comment_url"))
    subreddit = normalize_subreddit_name(payload.get("subreddit")) or normalize_subreddit_name(target_url) or normalize_subreddit_name(target_comment_url)
    discovery_constraints = normalize_reddit_execution_discovery_constraints(payload.get("discovery_constraints"))
    if subreddit and subreddit not in discovery_constraints["subreddits"]:
        discovery_constraints["subreddits"] = [subreddit, *discovery_constraints["subreddits"]]

    normalized = {
        "kind": kind,
        "strategy": strategy,
        "subreddit": subreddit,
        "target_url": target_url,
        "target_comment_url": target_comment_url,
        "discovery_constraints": discovery_constraints,
    }
    validate_reddit_execution_target(normalized, require_discovery_seed=False)
    return normalized


def normalize_reddit_execution_action(action: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(action or {})
    action_type = str(payload.get("type") or "").strip().lower()
    if action_type not in REDDIT_EXECUTION_ACTION_TYPES:
        raise ValueError(f"action.type must be one of {REDDIT_EXECUTION_ACTION_TYPES}")

    params = dict(payload.get("params") or {})
    normalized_params: Dict[str, Any] = {}
    if action_type == "browse":
        scrolls = params.get("scrolls", 3)
        normalized_params["scrolls"] = max(1, int(scrolls))
    if action_type in {"comment", "reply"}:
        text = _clean_str(params.get("text"))
        if text:
            normalized_params["text"] = text
    if action_type == "create_post":
        title = _clean_str(params.get("title"))
        body = _clean_str(params.get("body"))
        if title:
            normalized_params["title"] = title
        if body:
            normalized_params["body"] = body
        attachments: List[Dict[str, Any]] = []
        for index, entry in enumerate(list(params.get("attachments") or [])):
            image_id = _clean_str((entry or {}).get("image_id"))
            if not image_id:
                raise ValueError(f"action.params.attachments[{index}].image_id is required")
            attachments.append({"image_id": image_id})
        if len(attachments) > 1:
            raise ValueError("create_post currently supports at most one attachment")
        if attachments:
            normalized_params["attachments"] = attachments
    return {"type": action_type, "params": normalized_params}


def normalize_reddit_execution_verification(verification: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    payload = dict(REDDIT_EXECUTION_DEFAULT_VERIFICATION)
    payload.update(dict(verification or {}))
    return {
        "require_success_confirmed": bool(payload.get("require_success_confirmed", True)),
        "require_attempt_id": bool(payload.get("require_attempt_id", True)),
        "required_evidence_summary": bool(payload.get("required_evidence_summary", True)),
        "required_target_reference": bool(payload.get("required_target_reference", True)),
    }


def validate_reddit_execution_target(target: Dict[str, Any], *, require_discovery_seed: bool) -> None:
    kind = str(target.get("kind") or "")
    strategy = str(target.get("strategy") or "")
    target_url = _clean_str(target.get("target_url"))
    target_comment_url = _clean_str(target.get("target_comment_url"))
    subreddit = normalize_subreddit_name(target.get("subreddit"))
    discovery_constraints = dict(target.get("discovery_constraints") or {})

    if strategy == "explicit":
        if kind == "subreddit" and not (subreddit or target_url):
            raise ValueError("target.subreddit or target.target_url is required for explicit subreddit targets")
        if kind == "post" and not target_url:
            raise ValueError("target.target_url is required for explicit post targets")
        if kind == "comment" and not target_comment_url:
            raise ValueError("target.target_comment_url is required for explicit comment targets")
        return

    if not require_discovery_seed:
        return

    has_seed = bool(
        subreddit
        or list(discovery_constraints.get("subreddits") or [])
        or list(discovery_constraints.get("keywords") or [])
        or list(discovery_constraints.get("explicit_post_targets") or [])
        or list(discovery_constraints.get("explicit_comment_targets") or [])
        or list(discovery_constraints.get("mandatory_join_urls") or [])
    )
    if not has_seed:
        raise ValueError("discover targets require subreddit or discovery_constraints seeds")


def validate_reddit_execution_spec(spec: Dict[str, Any], *, require_discovery_seed: bool) -> None:
    actors = list(spec.get("actors") or [])
    if not actors:
        raise ValueError("actors must contain at least one reddit profile")

    action = dict(spec.get("action") or {})
    target = dict(spec.get("target") or {})
    action_type = str(action.get("type") or "")
    target_kind = str(target.get("kind") or "")
    validate_reddit_execution_target(target, require_discovery_seed=require_discovery_seed)

    allowed_targets = _ACTION_TARGET_CAPABILITIES.get(action_type)
    if not allowed_targets:
        raise ValueError(f"unsupported reddit action: {action_type}")
    if target_kind not in allowed_targets:
        raise ValueError(f"reddit action '{action_type}' is not allowed for target kind '{target_kind}'")

    if action_type == "join" and str(target.get("strategy") or "") != "explicit":
        raise ValueError("join requires target.strategy=explicit")


def normalize_reddit_execution_spec(
    spec: Dict[str, Any],
    *,
    require_discovery_seed: bool,
) -> Dict[str, Any]:
    payload = dict(spec or {})
    normalized = {
        "actors": [normalize_reddit_execution_actor(actor) for actor in list(payload.get("actors") or [])],
        "target": normalize_reddit_execution_target(payload.get("target") or {}),
        "action": normalize_reddit_execution_action(payload.get("action") or {}),
        "verification": normalize_reddit_execution_verification(payload.get("verification")),
    }
    validate_reddit_execution_spec(normalized, require_discovery_seed=require_discovery_seed)
    return normalized


def canonical_action_from_legacy(action: Optional[str]) -> Optional[str]:
    normalized = str(action or "").strip().lower()
    return _LEGACY_TO_CANONICAL_ACTION.get(normalized)


def target_kind_from_legacy_action(action: Optional[str], *, target_url: Optional[str], target_comment_url: Optional[str], subreddit: Optional[str]) -> Optional[str]:
    normalized = str(action or "").strip().lower()
    if normalized == "open_target":
        if _clean_str(target_comment_url):
            return "comment"
        if normalize_subreddit_name(subreddit) or normalize_subreddit_name(target_url):
            clean_target_url = str(target_url or "").strip().rstrip("/").lower()
            if "/comments/" not in clean_target_url:
                return "subreddit"
        return "post"
    return _LEGACY_ACTION_TARGET_KIND.get(normalized)


def runtime_action_for_execution_spec(spec: Dict[str, Any]) -> str:
    action_type = str(((spec.get("action") or {}).get("type") or "")).strip().lower()
    target_kind = str(((spec.get("target") or {}).get("kind") or "")).strip().lower()
    runtime_action = _CANONICAL_TO_RUNTIME_ACTION.get((action_type, target_kind))
    if not runtime_action:
        raise ValueError(f"no runtime reddit action for {action_type}/{target_kind}")
    return runtime_action


def work_item_target_mode_for_execution_spec(spec: Dict[str, Any]) -> str:
    action_type = str(((spec.get("action") or {}).get("type") or "")).strip().lower()
    target = dict(spec.get("target") or {})
    strategy = str(target.get("strategy") or "").strip().lower()
    target_kind = str(target.get("kind") or "").strip().lower()
    if action_type == "create_post" and strategy == "discover":
        return "generate_post"
    if strategy == "explicit":
        return "explicit"
    return _DISCOVER_TARGET_MODE.get(target_kind) or "explicit"


def _top_level_target_url(spec: Dict[str, Any]) -> Optional[str]:
    target = dict(spec.get("target") or {})
    action_type = str(((spec.get("action") or {}).get("type") or "")).strip().lower()
    target_kind = str(target.get("kind") or "").strip().lower()
    if action_type == "create_post":
        return None
    if target_kind == "subreddit":
        explicit_url = _clean_str(target.get("target_url"))
        if explicit_url:
            return explicit_url
        return subreddit_url(target.get("subreddit"))
    if target_kind == "comment" and action_type == "open":
        return _clean_str(target.get("target_comment_url"))
    return _clean_str(target.get("target_url"))


def first_attachment_image_id(spec: Dict[str, Any]) -> Optional[str]:
    attachments = list((((spec.get("action") or {}).get("params") or {}).get("attachments") or []))
    if not attachments:
        return None
    return _clean_str((attachments[0] or {}).get("image_id"))


def sync_work_item_with_execution_spec(item: Dict[str, Any], *, verification: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    candidate = dict(item or {})
    existing_spec = candidate.get("execution_spec")
    if existing_spec:
        spec = normalize_reddit_execution_spec(
            {
                **dict(existing_spec or {}),
                "actors": [{"profile_name": candidate.get("profile_name")}],
                "verification": verification or (existing_spec or {}).get("verification"),
            },
            require_discovery_seed=False,
        )
    else:
        action = str(candidate.get("action") or "").strip().lower()
        canonical_action = canonical_action_from_legacy(action)
        if not canonical_action:
            raise ValueError(f"unsupported legacy reddit action: {action}")
        target_kind = target_kind_from_legacy_action(
            action,
            target_url=candidate.get("target_url"),
            target_comment_url=candidate.get("target_comment_url"),
            subreddit=candidate.get("subreddit"),
        )
        target_mode = str(candidate.get("target_mode") or "explicit").strip().lower()
        target_strategy = "discover" if target_mode in {"discover_post", "discover_comment", "generate_post"} else "explicit"
        target = {
            "kind": target_kind,
            "strategy": target_strategy,
            "subreddit": candidate.get("subreddit"),
            "target_url": candidate.get("target_url") or ("https://www.reddit.com/" if action == "browse_feed" else None),
            "target_comment_url": candidate.get("target_comment_url"),
            "discovery_constraints": {},
        }
        params: Dict[str, Any] = {}
        if candidate.get("text") is not None:
            params["text"] = candidate.get("text")
        if candidate.get("title") is not None:
            params["title"] = candidate.get("title")
        if candidate.get("body") is not None:
            params["body"] = candidate.get("body")
        if candidate.get("scrolls") is not None:
            params["scrolls"] = candidate.get("scrolls")
        attachments: List[Dict[str, Any]] = []
        for entry in list(candidate.get("attachments") or []):
            image_id = _clean_str((entry or {}).get("image_id"))
            if image_id:
                attachments.append({"image_id": image_id})
        image_id = _clean_str(candidate.get("image_id"))
        if image_id and not attachments:
            attachments.append({"image_id": image_id})
        if attachments:
            params["attachments"] = attachments
        spec = normalize_reddit_execution_spec(
            {
                "actors": [{"profile_name": candidate.get("profile_name")}],
                "target": target,
                "action": {"type": canonical_action, "params": params},
                "verification": verification,
            },
            require_discovery_seed=False,
        )

    runtime_action = runtime_action_for_execution_spec(spec)
    target = dict(spec.get("target") or {})
    params = dict(((spec.get("action") or {}).get("params") or {}))

    candidate["execution_spec"] = spec
    candidate["action"] = runtime_action
    candidate["target_mode"] = work_item_target_mode_for_execution_spec(spec)
    top_level_target_url = _top_level_target_url(spec)
    if runtime_action == "create_post" and _clean_str(candidate.get("target_url")):
        top_level_target_url = _clean_str(candidate.get("target_url"))
    candidate["target_url"] = top_level_target_url
    candidate["target_comment_url"] = _clean_str(target.get("target_comment_url"))
    candidate["subreddit"] = normalize_subreddit_name(target.get("subreddit")) or normalize_subreddit_name(candidate.get("target_url"))
    candidate["text"] = _clean_str(params.get("text"))
    candidate["title"] = _clean_str(params.get("title"))
    candidate["body"] = _clean_str(params.get("body"))
    candidate["scrolls"] = max(1, int(params.get("scrolls", 3))) if (spec.get("action") or {}).get("type") == "browse" else None
    image_id = first_attachment_image_id(spec)
    candidate["attachments"] = [{"image_id": image_id}] if image_id else []
    candidate["image_id"] = image_id
    return candidate


def execution_request_from_legacy_payload(payload: Dict[str, Any], *, verification: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    normalized_payload = sync_work_item_with_execution_spec(
        {
            "profile_name": payload.get("profile_name"),
            "action": payload.get("action"),
            "target_url": payload.get("target_url") or payload.get("url"),
            "target_comment_url": payload.get("target_comment_url"),
            "subreddit": payload.get("subreddit"),
            "text": payload.get("text") or payload.get("exact_text") or payload.get("brief"),
            "title": payload.get("title"),
            "body": payload.get("body") or payload.get("brief"),
            "image_id": payload.get("image_id"),
            "scrolls": payload.get("scrolls"),
            "target_mode": payload.get("target_mode"),
        },
        verification=verification,
    )
    return normalized_payload["execution_spec"]


def build_execution_result(
    *,
    actor_profile_name: str,
    execution_spec: Dict[str, Any],
    item: Dict[str, Any],
    screenshot_artifact_url: Optional[str],
) -> Dict[str, Any]:
    result = dict(item.get("result") or {})
    target_ref = (
        _clean_str(item.get("target_comment_url"))
        or _clean_str(result.get("target_comment_url"))
        or _clean_str(item.get("target_url"))
        or _clean_str(result.get("target_url"))
        or _clean_str(result.get("current_url"))
    )
    return {
        "actor": {"profile_name": actor_profile_name},
        "action": dict(execution_spec.get("action") or {}),
        "resolved_target": {
            "kind": ((execution_spec.get("target") or {}).get("kind")),
            "strategy": ((execution_spec.get("target") or {}).get("strategy")),
            "subreddit": item.get("subreddit") or ((item.get("discovered_target") or {}).get("subreddit")) or result.get("subreddit"),
            "target_url": item.get("target_url") or result.get("target_url") or result.get("current_url"),
            "target_comment_url": item.get("target_comment_url") or result.get("target_comment_url"),
            "target_ref": target_ref,
            "discovered_target": item.get("discovered_target"),
        },
        "status": item.get("status"),
        "attempt_id": result.get("attempt_id"),
        "final_verdict": result.get("final_verdict"),
        "evidence_summary": result.get("evidence_summary"),
        "screenshot_artifact_url": screenshot_artifact_url,
        "permalink_or_target_ref": target_ref,
        "error": item.get("error") or result.get("error"),
    }
