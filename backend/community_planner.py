"""Daily AI planner — generates all community tasks for a given day."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from community_store import CommunityStore, get_community_store

logger = logging.getLogger("CommunityPlanner")

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

ARC_STAGES = ["newcomer", "exploring", "trying_product", "seeing_results", "advocate"]

# Days in each stage before auto-advance
ARC_STAGE_DURATIONS = {
    "newcomer": 3,
    "exploring": 5,
    "trying_product": 7,
    "seeing_results": 10,
    "advocate": 999,  # stays here
}


async def generate_daily_plan(
    target_date: Optional[str] = None,
    store: Optional[CommunityStore] = None,
) -> Dict[str, Any]:
    """Generate a full day's tasks for all community profiles.

    Args:
        target_date: ISO date string (YYYY-MM-DD). Defaults to tomorrow.
        store: CommunityStore instance.

    Returns: summary dict with task counts.
    """
    store = store or get_community_store()
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key or not genai:
        return {"error": "gemini unavailable", "tasks_created": 0}

    # Gather all context
    kb = await store.get_knowledge_base()
    personas = await store.list_personas()
    arcs = await store.get_all_arcs()
    memory = await store.get_recent_memory(limit_per_profile=10)
    config = await store.get_planner_config()

    if not personas:
        return {"error": "no personas defined", "tasks_created": 0}

    # Build arc lookup
    arc_map = {a["profile_name"]: a["current_stage"] for a in arcs}

    # Build memory lookup
    memory_map: Dict[str, List[str]] = {}
    for m in memory:
        name = m["profile_name"]
        if name not in memory_map:
            memory_map[name] = []
        summary = m.get("content_summary") or m.get("action", "")
        memory_map[name].append(f"{m.get('action','')}: {summary}")

    # Config values
    tz_name = config.get("timezone", "America/New_York")
    tz = ZoneInfo(tz_name)
    start_hour = config.get("start_hour", 8)
    end_hour = config.get("end_hour", 22)
    group_url = config.get("group_url", "https://www.facebook.com/groups/793232133423520/")

    timeline_range = config.get("timeline_posts_per_day", [2, 4])
    group_posts_range = config.get("group_posts_per_day", [0, 2])
    group_likes_range = config.get("group_likes_per_day", [1, 3])
    group_replies_range = config.get("group_replies_per_day", [0, 2])
    image_ratio = config.get("image_ratio", 0.5)
    product_mention_ratio = config.get("product_mention_ratio", 0.3)

    # Target date
    if target_date:
        plan_date = datetime.strptime(target_date, "%Y-%m-%d").replace(tzinfo=tz)
    else:
        plan_date = (datetime.now(tz) + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    date_str = plan_date.strftime("%Y-%m-%d")

    # Build prompt for LLM
    prompt = _build_planner_prompt(
        kb=kb,
        personas=personas,
        arc_map=arc_map,
        memory_map=memory_map,
        config=config,
        date_str=date_str,
        group_url=group_url,
        timeline_range=timeline_range,
        group_posts_range=group_posts_range,
        group_likes_range=group_likes_range,
        group_replies_range=group_replies_range,
        image_ratio=image_ratio,
        product_mention_ratio=product_mention_ratio,
    )

    # Call Gemini
    client = genai.Client(api_key=api_key)
    model = os.getenv("COMMUNITY_PLANNER_MODEL", "gemini-2.5-flash")

    try:
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=model,
            contents=[prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        raw = (response.text or "").strip()
        tasks_data = json.loads(raw)
        if isinstance(tasks_data, dict) and "tasks" in tasks_data:
            tasks_data = tasks_data["tasks"]
    except Exception as exc:
        logger.error(f"planner LLM call failed: {exc}")
        return {"error": str(exc), "tasks_created": 0}

    # Create a plan
    plan = await store.create_plan(
        name=f"auto_{date_str}",
        phase="community",
        config={"date": date_str, "generated_by": "daily_planner"},
    )
    plan_id = plan.get("id")
    if not plan_id:
        return {"error": "failed to create plan", "tasks_created": 0}

    # Convert LLM output to community_tasks
    window_minutes = (end_hour - start_hour) * 60
    all_tasks = []

    for i, task_data in enumerate(tasks_data):
        profile_name = task_data.get("profile_name", "")
        action = task_data.get("action", "")
        if action not in ("warmup_post", "post_in_group", "reply_to_post", "like_post"):
            continue

        # Generate random time within window
        offset = random.randint(0, max(window_minutes - 1, 0))
        local_time = plan_date.replace(hour=start_hour, minute=0) + timedelta(minutes=offset, seconds=random.randint(0, 59))
        utc_time = local_time.astimezone(timezone.utc)

        task = {
            "plan_id": plan_id,
            "profile_name": profile_name,
            "action": action,
            "status": "pending",
            "scheduled_at": utc_time.isoformat(),
            "text": task_data.get("text"),
            "target_url": task_data.get("target_url") or (group_url if action in ("post_in_group", "like_post", "reply_to_post") else None),
            "image_prompt": task_data.get("image_prompt"),
            "image_url": None,
        }
        all_tasks.append(task)

    if all_tasks:
        await store.insert_tasks(all_tasks)
        await store.update_plan_status(plan_id, "active")

    # Auto-advance arcs
    await _maybe_advance_arcs(store, arcs)

    logger.info(f"daily plan generated for {date_str}: {len(all_tasks)} tasks across {len(set(t['profile_name'] for t in all_tasks))} profiles")

    return {
        "plan_id": plan_id,
        "date": date_str,
        "tasks_created": len(all_tasks),
        "profiles": len(set(t["profile_name"] for t in all_tasks)),
        "breakdown": {
            "warmup_post": sum(1 for t in all_tasks if t["action"] == "warmup_post"),
            "post_in_group": sum(1 for t in all_tasks if t["action"] == "post_in_group"),
            "like_post": sum(1 for t in all_tasks if t["action"] == "like_post"),
            "reply_to_post": sum(1 for t in all_tasks if t["action"] == "reply_to_post"),
        },
    }


def _build_planner_prompt(
    *,
    kb: str,
    personas: List[Dict],
    arc_map: Dict[str, str],
    memory_map: Dict[str, List[str]],
    config: Dict,
    date_str: str,
    group_url: str,
    timeline_range: list,
    group_posts_range: list,
    group_likes_range: list,
    group_replies_range: list,
    image_ratio: float,
    product_mention_ratio: float,
) -> str:
    # Build personas section
    personas_text = ""
    for p in personas:
        name = p["profile_name"]
        stage = arc_map.get(name, "newcomer")
        recent = memory_map.get(name, [])
        recent_text = "\n    ".join(recent[:5]) if recent else "no recent activity"
        personas_text += f"""
- {name} (age {p.get('age', '?')}, {p.get('persona_prompt', '')})
  Arc stage: {stage}
  Recent activity:
    {recent_text}
"""

    return f"""You are a social media community planner. Generate a full day of Facebook activity for {len(personas)} profiles for {date_str}.

## Knowledge Base (MUST follow these rules)
{kb}

## Profiles
{personas_text}

## Output Requirements
For EACH profile, generate:
- {timeline_range[0]}-{timeline_range[1]} timeline posts (action: "warmup_post"). About {int(image_ratio*100)}% should have image_prompt set.
- {group_posts_range[0]}-{group_posts_range[1]} group posts (action: "post_in_group", target_url: "{group_url}")
- {group_likes_range[0]}-{group_likes_range[1]} group likes (action: "like_post", target_url: "{group_url}")
- {group_replies_range[0]}-{group_replies_range[1]} group replies (action: "reply_to_post", target_url: "{group_url}")

## Content Rules
- {int(product_mention_ratio*100)}% of posts can mention the product naturally. Rest = personal life content.
- Each profile's content must match their persona and arc stage:
  - newcomer: introducing themselves, getting to know the group, general life posts
  - exploring: asking questions, curious about the product, health-curious posts
  - trying_product: sharing early experiences, first impressions, cautiously optimistic
  - seeing_results: sharing specific results, recommending to others, confident
  - advocate: established member, helping newcomers, sharing tips, loyal fan
- Cross-interactions: profiles should like and reply to each other's hypothetical posts
- NEVER repeat content that appears in a profile's recent activity
- Text must follow ALL writing rules from the knowledge base

## Output Format
Return a JSON array of task objects:
```json
[
  {{"profile_name": "...", "action": "warmup_post", "text": "...", "image_prompt": "..." or null}},
  {{"profile_name": "...", "action": "post_in_group", "text": "...", "target_url": "...", "image_prompt": null}},
  {{"profile_name": "...", "action": "like_post", "target_url": "..."}},
  {{"profile_name": "...", "action": "reply_to_post", "text": "...", "target_url": "..."}}
]
```

Generate ALL tasks for ALL {len(personas)} profiles. Return ONLY the JSON array, no other text."""


async def _maybe_advance_arcs(store: CommunityStore, arcs: List[Dict[str, Any]]) -> None:
    """Auto-advance profiles that have been in their current stage long enough."""
    now = datetime.now(timezone.utc)
    for arc in arcs:
        stage = arc.get("current_stage", "newcomer")
        if stage == "advocate":
            continue
        started = arc.get("stage_started_at")
        if not started:
            continue
        try:
            started_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            continue
        days_in_stage = (now - started_dt).days
        required_days = ARC_STAGE_DURATIONS.get(stage, 999)
        if days_in_stage >= required_days:
            await store.advance_arc(arc["profile_name"])
