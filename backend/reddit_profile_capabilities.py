from __future__ import annotations

import json
import urllib.request
from functools import lru_cache
from typing import Any, Dict, Optional

REDDIT_PUBLIC_USER_AGENT = "commentfront-reddit-bot/1.0"


@lru_cache(maxsize=256)
def fetch_public_reddit_profile_stats(username: Optional[str]) -> Dict[str, Any]:
    normalized = str(username or "").strip()
    if not normalized:
        return {}

    request = urllib.request.Request(
        f"https://www.reddit.com/user/{normalized}/about.json?raw_json=1",
        headers={"User-Agent": REDDIT_PUBLIC_USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.load(response)
    except Exception:
        return {}

    data = dict(payload.get("data") or {})
    return {
        "username": normalized,
        "comment_karma": int(data.get("comment_karma") or 0),
        "link_karma": int(data.get("link_karma") or 0),
        "created_utc": data.get("created_utc"),
    }
