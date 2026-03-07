"""
Reddit Session Manager

Persists authenticated Reddit mobile-web sessions with cookies and storage state.
"""

import hashlib
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import MOBILE_VIEWPORT, USA_TIMEZONES
from safe_io import atomic_write_json, safe_read_json

logger = logging.getLogger("RedditSession")

REDDIT_SESSIONS_DIR = Path(
    os.getenv("REDDIT_SESSIONS_DIR", str(Path(__file__).parent / "sessions" / "reddit"))
)
REDDIT_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


class RedditSession:
    """Manages Reddit session persistence for a single account."""

    def __init__(self, profile_name: str):
        self.profile_name = profile_name
        self.session_file = REDDIT_SESSIONS_DIR / f"{self._sanitize_name(profile_name)}.json"
        self.data: Optional[Dict[str, Any]] = None

    def _sanitize_name(self, name: str) -> str:
        return str(name or "").replace(" ", "_").replace("/", "_").lower()

    async def extract_from_context(
        self,
        context,
        page,
        *,
        username: str,
        email: Optional[str] = None,
        profile_url: Optional[str] = None,
        proxy: Optional[str] = None,
        tags: Optional[List[str]] = None,
        linked_credential_id: Optional[str] = None,
        display_name: Optional[str] = None,
        fixture: bool = False,
        warmup_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Extract session data from an authenticated Playwright context."""
        storage_state = await context.storage_state()
        cookies = storage_state.get("cookies", [])
        user_agent = await page.evaluate("navigator.userAgent")
        viewport = page.viewport_size or MOBILE_VIEWPORT

        self.data = {
            "platform": "reddit",
            "profile_name": self.profile_name,
            "display_name": display_name or username,
            "username": username,
            "email": email,
            "profile_url": profile_url,
            "extracted_at": datetime.utcnow().isoformat(),
            "storage_state": storage_state,
            "cookies": cookies,
            "user_agent": user_agent,
            "viewport": viewport,
            "proxy": proxy,
            "tags": list(tags or ["reddit"]),
            "fixture": bool(fixture),
            "linked_credential_id": linked_credential_id,
            "warmup_state": warmup_state or {},
        }
        return self.data

    def save(self) -> bool:
        if not self.data:
            logger.error("No Reddit session data to save")
            return False
        return atomic_write_json(str(self.session_file), self.data)

    def load(self) -> Optional[Dict[str, Any]]:
        data = safe_read_json(str(self.session_file))
        if data is None:
            logger.info(f"No Reddit session file found at {self.session_file}")
            return None
        self.data = data
        return data

    def delete(self) -> bool:
        if self.session_file.exists():
            self.session_file.unlink()
            return True
        return False

    def get_storage_state(self) -> Dict[str, Any]:
        if not self.data:
            return {}
        return dict(self.data.get("storage_state") or {})

    def get_cookies(self) -> List[Dict[str, Any]]:
        if not self.data:
            return []
        return list(self.data.get("cookies") or [])

    def get_user_agent(self) -> Optional[str]:
        if not self.data:
            return None
        return self.data.get("user_agent")

    def get_viewport(self) -> Dict[str, int]:
        if not self.data:
            return dict(MOBILE_VIEWPORT)
        return dict(self.data.get("viewport") or MOBILE_VIEWPORT)

    def get_proxy(self) -> Optional[str]:
        if not self.data:
            return None
        return self.data.get("proxy")

    def get_device_fingerprint(self) -> Dict[str, str]:
        if not self.data:
            seed = self.profile_name or "reddit"
        else:
            seed = self.data.get("username") or self.profile_name or "reddit"

        index = int(hashlib.md5(seed.encode()).hexdigest(), 16) % len(USA_TIMEZONES)
        return {
            "timezone": USA_TIMEZONES[index],
            "locale": "en-US",
        }

    def has_auth_tokens(self) -> bool:
        cookies = self.get_cookies()
        names = {str(cookie.get("name") or "") for cookie in cookies}
        return bool({"token_v2", "reddit_session"} & names)

    def get_username(self) -> Optional[str]:
        if not self.data:
            return None
        return self.data.get("username")

    def get_profile_url(self) -> Optional[str]:
        if not self.data:
            return None
        return self.data.get("profile_url")

    def get_tags(self) -> List[str]:
        if not self.data:
            return []
        return list(self.data.get("tags") or [])

    def get_warmup_state(self) -> Dict[str, Any]:
        if not self.data:
            return {}
        return dict(self.data.get("warmup_state") or {})

    def update_warmup_state(self, state: Dict[str, Any]) -> bool:
        if not self.data:
            return False
        self.data["warmup_state"] = dict(state or {})
        self.data["updated_at"] = datetime.utcnow().isoformat()
        return self.save()


async def verify_reddit_session_logged_in(page, session: RedditSession, debug: bool = True) -> bool:
    """
    Validate a Reddit session using authenticated destinations, not just public pages.
    """
    try:
        if not session.has_auth_tokens():
            if debug:
                logger.info("Reddit session missing auth cookies")
            return False

        await page.goto("https://www.reddit.com/submit", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2500)
        current_url = page.url.lower()
        if "/login" in current_url:
            if debug:
                logger.info(f"Reddit submit redirected to login: {current_url}")
            return False

        body = (await page.locator("body").inner_text()).lower()
        if "create a post" in body or "post to" in body or "/submit" in current_url:
            return True

        login_inputs = await page.locator('input[name="username"], input[name="password"]').count()
        return login_inputs == 0
    except Exception as exc:
        if debug:
            logger.warning(f"Reddit session verification failed: {exc}")
        return False


def list_saved_reddit_sessions() -> List[Dict[str, Any]]:
    sessions: List[Dict[str, Any]] = []
    for session_file in sorted(REDDIT_SESSIONS_DIR.glob("*.json")):
        data = safe_read_json(str(session_file))
        if not data:
            continue
        cookies = list(data.get("cookies") or [])
        cookie_names = {str(cookie.get("name") or "") for cookie in cookies}
        sessions.append(
            {
                "file": session_file.name,
                "platform": "reddit",
                "profile_name": data.get("profile_name"),
                "display_name": data.get("display_name") or data.get("username") or data.get("profile_name"),
                "username": data.get("username"),
                "email": data.get("email"),
                "profile_url": data.get("profile_url"),
                "extracted_at": data.get("extracted_at"),
                "proxy": data.get("proxy"),
                "has_valid_session": bool({"token_v2", "reddit_session"} & cookie_names),
                "tags": list(data.get("tags") or []),
                "fixture": bool(data.get("fixture", False)),
                "linked_credential_id": data.get("linked_credential_id"),
                "warmup_state": dict(data.get("warmup_state") or {}),
            }
        )
    return sessions
