"""Generate warmup task schedules and import sheet data into community_tasks."""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from zoneinfo import ZoneInfo

from community_store import CommunityStore, get_community_store

logger = logging.getLogger("CommunityPlanGenerator")


async def generate_warmup_plan(
    plan_id: str,
    persona_names: List[str],
    config: Dict[str, Any],
    store: Optional[CommunityStore] = None,
) -> Dict[str, Any]:
    """Generate warmup_post tasks for each persona × each day at random times.

    config keys: days, posts_min, posts_max, start_hour, end_hour, timezone
    """
    store = store or get_community_store()
    days = config.get("days", 5)
    posts_min = config.get("posts_min", 2)
    posts_max = config.get("posts_max", 5)
    start_hour = config.get("start_hour", 8)
    end_hour = config.get("end_hour", 22)
    tz_name = config.get("timezone", "America/New_York")
    tz = ZoneInfo(tz_name)

    # Start from tomorrow
    now_local = datetime.now(tz)
    start_date = (now_local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    tasks: List[Dict[str, Any]] = []

    for persona_name in persona_names:
        for day_offset in range(days):
            day_start = start_date + timedelta(days=day_offset)
            num_posts = random.randint(posts_min, posts_max)

            # Generate random times within the hour window
            times = _random_times_in_window(day_start, start_hour, end_hour, num_posts, tz)

            for scheduled_at in times:
                tasks.append({
                    "plan_id": plan_id,
                    "profile_name": persona_name,
                    "action": "warmup_post",
                    "status": "pending",
                    "scheduled_at": scheduled_at.isoformat(),
                })

    await store.insert_tasks(tasks)
    logger.info(f"generated {len(tasks)} warmup tasks for plan {plan_id} ({len(persona_names)} personas × {days} days)")

    return {
        "total_tasks": len(tasks),
        "personas": len(persona_names),
        "days": days,
        "posts_per_day": f"{posts_min}-{posts_max}",
    }


async def import_sheet_data(
    plan_id: str,
    rows: List[Dict[str, Any]],
    config: Dict[str, Any],
    store: Optional[CommunityStore] = None,
) -> Dict[str, Any]:
    """Import parsed Google Sheet rows into community_tasks.

    Each row: {profile_name, day, action, target_url, text, image_prompt, image_url}
    config keys: start_date, start_hour, end_hour, timezone
    """
    store = store or get_community_store()
    start_date_str = config.get("start_date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    start_hour = config.get("start_hour", 8)
    end_hour = config.get("end_hour", 22)
    tz_name = config.get("timezone", "America/New_York")
    tz = ZoneInfo(tz_name)

    base_date = datetime.strptime(start_date_str, "%Y-%m-%d").replace(tzinfo=tz)
    base_date = base_date.replace(hour=0, minute=0, second=0, microsecond=0)

    tasks: List[Dict[str, Any]] = []

    for row in rows:
        day_offset = int(row.get("day", 1)) - 1  # day is 1-based
        day_start = base_date + timedelta(days=day_offset)

        # Random time within window for this day
        times = _random_times_in_window(day_start, start_hour, end_hour, 1, tz)
        scheduled_at = times[0]

        task = {
            "plan_id": plan_id,
            "profile_name": row["profile_name"],
            "action": row["action"],
            "status": "pending",
            "scheduled_at": scheduled_at.isoformat(),
            "target_url": row.get("target_url"),
            "text": row.get("text"),
            "image_prompt": row.get("image_prompt"),
            "image_url": row.get("image_url"),
        }
        # PostgREST bulk insert requires all objects to have same keys — keep None values
        tasks.append(task)

    await store.insert_tasks(tasks)
    logger.info(f"imported {len(tasks)} sheet tasks for plan {plan_id}")

    return {
        "total_tasks": len(tasks),
        "profiles": len(set(r["profile_name"] for r in rows)),
        "days": len(set(int(r.get("day", 1)) for r in rows)),
    }


def _random_times_in_window(
    day_start: datetime,
    start_hour: int,
    end_hour: int,
    count: int,
    tz: ZoneInfo,
) -> List[datetime]:
    """Generate `count` random timestamps within [start_hour, end_hour) on the given day.

    Returns UTC timestamps sorted chronologically.
    """
    window_minutes = (end_hour - start_hour) * 60
    if window_minutes <= 0:
        window_minutes = 14 * 60  # fallback: 14 hours

    # Generate random minute offsets, spread them out
    offsets = sorted(random.sample(range(window_minutes), min(count, window_minutes)))
    if len(offsets) < count:
        offsets = sorted(random.choices(range(window_minutes), k=count))

    times = []
    for offset in offsets:
        local_time = day_start.replace(hour=start_hour, minute=0) + timedelta(minutes=offset)
        # Add random seconds for naturalness
        local_time += timedelta(seconds=random.randint(0, 59))
        utc_time = local_time.astimezone(timezone.utc)
        times.append(utc_time)

    return times
