"""
Duplicate profile-name remediation workflow.

Builds deterministic plans grouped by display_name and applies renames
sequentially with retry-on-failure semantics.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

from adaptive_agent import run_adaptive_task
from fb_session import FacebookSession

logger = logging.getLogger("NameDedupeWorkflow")

FIRST_NAMES = [
    "Avery", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Skyler", "Parker",
    "Quinn", "Emerson", "Rowan", "Cameron", "Drew", "Harper", "Reese", "Sawyer",
]

LAST_NAMES = [
    "Bennett", "Hayes", "Foster", "Morris", "Perry", "Coleman", "Brooks", "Griffin",
    "Sullivan", "Wallace", "Warren", "Pierce", "Hunter", "Palmer", "Russell", "Shaw",
]


def _normalize_name(value: Optional[str]) -> str:
    return " ".join(str(value or "").strip().split())


def _name_key(value: Optional[str]) -> str:
    return _normalize_name(value).lower()


def _choose_keep_profile(group: List[dict]) -> dict:
    """Deterministically keep one profile per duplicate group."""
    return sorted(
        group,
        key=lambda s: (
            0 if s.get("has_valid_cookies") else 1,
            str(s.get("profile_name") or ""),
        ),
    )[0]


def _generate_unique_display_name(
    group_key: str,
    profile_name: str,
    used_name_keys: set,
) -> str:
    """Generate deterministic but unique replacement display_name."""
    total_space = len(FIRST_NAMES) * len(LAST_NAMES)

    for offset in range(total_space * 2):
        seed = f"{group_key}|{profile_name}|{offset}"
        digest = int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16)
        first = FIRST_NAMES[digest % len(FIRST_NAMES)]
        last = LAST_NAMES[(digest // len(FIRST_NAMES)) % len(LAST_NAMES)]
        candidate = f"{first} {last}"

        key = _name_key(candidate)
        if key not in used_name_keys:
            used_name_keys.add(key)
            return candidate

    # Guaranteed fallback if the deterministic name space is exhausted.
    idx = 1
    while True:
        fallback = f"{FIRST_NAMES[idx % len(FIRST_NAMES)]} {LAST_NAMES[idx % len(LAST_NAMES)]} {idx}"
        key = _name_key(fallback)
        if key not in used_name_keys:
            used_name_keys.add(key)
            return fallback
        idx += 1


def build_dedupe_plan(sessions: List[dict]) -> Dict:
    """
    Build deterministic duplicate-name remediation plan.

    Output includes duplicate groups with keep/rename split and stable plan_id.
    """
    groups_by_name: Dict[str, List[dict]] = {}
    used_name_keys = {
        _name_key(s.get("display_name") or s.get("profile_name"))
        for s in sessions
        if s.get("profile_name")
    }

    for session in sessions:
        profile_name = session.get("profile_name")
        if not profile_name:
            continue
        display_name = _normalize_name(session.get("display_name") or profile_name)
        key = _name_key(display_name)
        groups_by_name.setdefault(key, []).append({
            "profile_name": profile_name,
            "display_name": display_name,
            "user_id": session.get("user_id"),
            "has_valid_cookies": bool(session.get("has_valid_cookies", False)),
        })

    duplicate_groups: List[Dict] = []

    for key in sorted(groups_by_name.keys()):
        group = groups_by_name[key]
        if len(group) <= 1:
            continue

        keep = _choose_keep_profile(group)
        rename_items = []

        for profile in sorted(group, key=lambda x: str(x.get("profile_name") or "")):
            if profile["profile_name"] == keep["profile_name"]:
                continue

            new_display_name = _generate_unique_display_name(
                group_key=key,
                profile_name=profile["profile_name"],
                used_name_keys=used_name_keys,
            )
            rename_items.append({
                "profile_name": profile["profile_name"],
                "user_id": profile.get("user_id"),
                "from_display_name": profile["display_name"],
                "to_display_name": new_display_name,
            })

        duplicate_groups.append({
            "display_name": keep["display_name"],
            "display_name_key": key,
            "group_size": len(group),
            "keep_profile": {
                "profile_name": keep["profile_name"],
                "user_id": keep.get("user_id"),
                "display_name": keep["display_name"],
            },
            "rename_profiles": rename_items,
        })

    plan_material = {
        "duplicate_groups": [
            {
                "display_name_key": g["display_name_key"],
                "keep_profile": g["keep_profile"]["profile_name"],
                "rename_profiles": [
                    {
                        "profile_name": r["profile_name"],
                        "to_display_name": r["to_display_name"],
                    }
                    for r in g["rename_profiles"]
                ],
            }
            for g in duplicate_groups
        ]
    }

    plan_id = hashlib.sha256(
        json.dumps(plan_material, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]

    total_renames = sum(len(g["rename_profiles"]) for g in duplicate_groups)

    return {
        "plan_id": plan_id,
        "generated_at": datetime.utcnow().isoformat(),
        "duplicate_groups": duplicate_groups,
        "total_duplicate_groups": len(duplicate_groups),
        "total_profiles_to_rename": total_renames,
    }


async def _apply_single_rename(profile_name: str, to_display_name: str) -> Dict:
    """
    Attempt a single Facebook name-change action through Adaptive Agent.
    On success, update local session display_name.
    """
    task = (
        "Change your Facebook profile name to exactly: "
        f"{to_display_name}. "
        "Go to settings/account center, edit name, save, and confirm when complete. "
        "Use DONE only after the new name is saved."
    )

    adaptive_result = await run_adaptive_task(
        profile_name=profile_name,
        task=task,
        max_steps=30,
        start_url="https://m.facebook.com/me",
    )

    final_status = adaptive_result.get("final_status")
    success = final_status == "task_completed"

    if success:
        session = FacebookSession(profile_name)
        if session.load():
            session.data["display_name"] = to_display_name
            session.data["dedupe_renamed_at"] = datetime.utcnow().isoformat()
            session.save()

    return {
        "success": success,
        "final_status": final_status,
        "steps": len(adaptive_result.get("steps", [])),
        "errors": adaptive_result.get("errors", []),
    }


async def apply_dedupe_plan(plan: Dict, retries: int = 2) -> Dict:
    """
    Apply dedupe plan sequentially.

    - Sequential execution
    - Retries each failed profile up to 2 additional times
    - Continue on failure (no silent abort)
    """
    duplicate_groups = plan.get("duplicate_groups", [])
    results: List[Dict] = []

    for group in duplicate_groups:
        for rename in group.get("rename_profiles", []):
            profile_name = rename["profile_name"]
            to_display_name = rename["to_display_name"]

            profile_result = {
                "profile_name": profile_name,
                "from_display_name": rename.get("from_display_name"),
                "to_display_name": to_display_name,
                "attempts": [],
                "success": False,
            }

            for attempt in range(1, retries + 2):  # initial + retry count
                try:
                    attempt_result = await _apply_single_rename(
                        profile_name=profile_name,
                        to_display_name=to_display_name,
                    )
                except Exception as exc:
                    attempt_result = {
                        "success": False,
                        "final_status": "error",
                        "steps": 0,
                        "errors": [str(exc)],
                    }

                attempt_result["attempt"] = attempt
                profile_result["attempts"].append(attempt_result)

                if attempt_result.get("success"):
                    profile_result["success"] = True
                    break

            results.append(profile_result)

    succeeded = sum(1 for item in results if item.get("success"))
    failed = len(results) - succeeded

    return {
        "plan_id": plan.get("plan_id"),
        "executed_at": datetime.utcnow().isoformat(),
        "total_profiles": len(results),
        "succeeded": succeeded,
        "failed": failed,
        "results": results,
    }
