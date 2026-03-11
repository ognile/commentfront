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


def _default_reddit_sessions_dir() -> Path:
    env_value = os.getenv("REDDIT_SESSIONS_DIR")
    if env_value:
        return Path(env_value)
    if Path("/data").exists():
        return Path("/data/sessions/reddit")
    return Path(__file__).parent / "sessions" / "reddit"


REDDIT_SESSIONS_DIR = _default_reddit_sessions_dir()
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
        device: Optional[Dict[str, str]] = None,
        bootstrap_source_session_id: Optional[str] = None,
        cookie_blocklist_domains: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Extract session data from an authenticated Playwright context."""
        storage_state = await context.storage_state()
        cookies = self._filter_cookies(storage_state.get("cookies", []), cookie_blocklist_domains)
        filtered_storage_state = dict(storage_state or {})
        filtered_storage_state["cookies"] = cookies
        user_agent = await page.evaluate("navigator.userAgent")
        viewport = page.viewport_size or MOBILE_VIEWPORT
        device_fingerprint = dict(device or self.get_device_fingerprint())

        self.data = {
            "platform": "reddit",
            "profile_name": self.profile_name,
            "display_name": display_name or username,
            "username": username,
            "email": email,
            "profile_url": profile_url,
            "extracted_at": datetime.utcnow().isoformat(),
            "storage_state": filtered_storage_state,
            "cookies": cookies,
            "user_agent": user_agent,
            "viewport": viewport,
            "proxy": proxy,
            "device": device_fingerprint,
            "tags": list(tags or ["reddit"]),
            "fixture": bool(fixture),
            "linked_credential_id": linked_credential_id,
            "warmup_state": warmup_state or {},
        }
        if bootstrap_source_session_id:
            self.data["bootstrap_source_session_id"] = bootstrap_source_session_id
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
        if self.data:
            device = dict(self.data.get("device") or {})
            if device.get("timezone"):
                return {
                    "timezone": str(device.get("timezone")),
                    "locale": str(device.get("locale") or "en-US"),
                }

        seed = self.profile_name or "reddit"
        if self.data:
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

    def get_subreddit_identity_state(self, subreddit: Optional[str] = None) -> Dict[str, Any]:
        if not self.data:
            return {}
        identities = dict(((self.data.get("community_identity") or {}).get("subreddits")) or {})
        if subreddit is None:
            return identities
        return dict(identities.get(str(subreddit or "").strip().lower()) or {})

    def update_subreddit_identity_state(self, subreddit: str, state: Dict[str, Any]) -> bool:
        normalized = str(subreddit or "").strip().lower()
        if not normalized or not self.data:
            return False
        community_identity = dict(self.data.get("community_identity") or {})
        subreddits = dict(community_identity.get("subreddits") or {})
        subreddits[normalized] = dict(state or {})
        community_identity["subreddits"] = subreddits
        self.data["community_identity"] = community_identity
        self.data["updated_at"] = datetime.utcnow().isoformat()
        return self.save()

    @staticmethod
    def _filter_cookies(
        cookies: List[Dict[str, Any]],
        blocked_domains: Optional[List[str]],
    ) -> List[Dict[str, Any]]:
        blocked = [str(domain or "").lower().lstrip(".") for domain in list(blocked_domains or []) if str(domain or "").strip()]
        if not blocked:
            return list(cookies or [])

        filtered: List[Dict[str, Any]] = []
        for cookie in list(cookies or []):
            domain = str(cookie.get("domain") or "").lower().lstrip(".")
            if any(domain == blocked_domain or domain.endswith(f".{blocked_domain}") for blocked_domain in blocked):
                continue
            filtered.append(cookie)
        return filtered

    def update_warmup_state(self, state: Dict[str, Any]) -> bool:
        if not self.data:
            return False
        self.data["warmup_state"] = dict(state or {})
        self.data["updated_at"] = datetime.utcnow().isoformat()
        return self.save()


async def verify_reddit_session_logged_in(page, session: RedditSession, debug: bool = True, audit=None) -> bool:
    """
    Validate a Reddit session using authenticated destinations, not just public pages.
    """
    try:
        cookie_names = sorted({str(cookie.get("name") or "") for cookie in session.get_cookies()})
        if debug:
            logger.info(f"[{session.profile_name}] verify cookie names: {cookie_names}")

        destinations = [
            "https://www.reddit.com/submit",
            "https://www.reddit.com/settings/account",
        ]

        for destination in destinations:
            last_exc = None
            for attempt in range(1, 3):
                try:
                    await page.goto(destination, wait_until="domcontentloaded", timeout=30000)
                    last_exc = None
                    break
                except Exception as exc:
                    last_exc = exc
                    logger.warning(
                        f"[{session.profile_name}] verify navigation attempt {attempt}/2 failed for {destination}: {exc}"
                    )
                    if attempt == 2:
                        raise
                    await page.wait_for_timeout(1200 * attempt)
            if last_exc:
                raise last_exc
            await page.wait_for_timeout(2500)
            current_url = page.url.lower()
            logger.info(f"[{session.profile_name}] verify: {destination} resolved to {current_url}")

            body = (await page.locator("body").inner_text()).lower()
            body_preview = body[:200].replace("\n", " ")
            logger.info(f"[{session.profile_name}] verify body: {body_preview}")

            login_inputs = await page.locator('input[name="username"], input[name="password"]').count()
            logger.info(f"[{session.profile_name}] verify: login inputs found={login_inputs}")

            if audit:
                checkpoint_name = "protected_destination_verify_submit" if "/submit" in destination else "protected_destination_verify_settings"
                await audit.capture_checkpoint(page, page.context, checkpoint_name)

            if "/login" in current_url or login_inputs > 0:
                continue

            if any(marker in body for marker in ("create a post", "post to", "account settings", "email address")):
                return True

            if "/submit" in current_url or "/settings/" in current_url:
                return True

        return False
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
        storage_state = dict(data.get("storage_state") or {})
        persisted_cookie_count = len(cookies or list(storage_state.get("cookies") or []))
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
                "has_valid_session": persisted_cookie_count > 0,
                "tags": list(data.get("tags") or []),
                "fixture": bool(data.get("fixture", False)),
                "linked_credential_id": data.get("linked_credential_id"),
                "warmup_state": dict(data.get("warmup_state") or {}),
            }
        )
    return sessions
