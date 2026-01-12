"""
Facebook Session Manager

Handles extraction, persistence, and validation of Facebook sessions.
Sessions are saved as JSON files containing cookies, user agent, viewport, and proxy info.
"""

import json
import logging
import os
import random
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List

logger = logging.getLogger("FBSession")

# USA timezones only (mobile proxy is in USA)
USA_TIMEZONES = [
    "America/New_York",      # Eastern
    "America/Chicago",       # Central
    "America/Denver",        # Mountain
    "America/Los_Angeles",   # Pacific
    "America/Phoenix",       # Arizona (no DST)
    "America/Anchorage",     # Alaska
]

SESSIONS_DIR = Path(os.getenv("SESSIONS_DIR", str(Path(__file__).parent / "sessions")))
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


class FacebookSession:
    """Manages Facebook session persistence for a single account."""

    def __init__(self, profile_name: str):
        self.profile_name = profile_name
        self.session_file = SESSIONS_DIR / f"{self._sanitize_name(profile_name)}.json"
        self.data: Optional[Dict[str, Any]] = None

    def _sanitize_name(self, name: str) -> str:
        """Convert profile name to safe filename."""
        return name.replace(" ", "_").replace("/", "_").lower()

    async def extract_from_page(self, page, adspower_id: str = None, proxy: str = None) -> Dict[str, Any]:
        """
        Extract session data from an active Playwright page.

        Args:
            page: Playwright page object (must be logged in to Facebook)
            adspower_id: The AdsPower profile ID (for reference)
            proxy: Proxy URL used by this profile

        Returns:
            Dict containing all session data
        """
        context = page.context

        # Extract cookies
        cookies = await context.cookies()

        # Extract user agent
        user_agent = await page.evaluate("navigator.userAgent")

        # Get viewport
        viewport = page.viewport_size or {"width": 393, "height": 873}

        # Build session data
        self.data = {
            "profile_name": self.profile_name,
            "adspower_id": adspower_id,
            "extracted_at": datetime.now().isoformat(),
            "cookies": cookies,
            "user_agent": user_agent,
            "viewport": viewport,
            "proxy": proxy,
        }

        # Check for essential Facebook cookies
        cookie_names = [c.get("name") for c in cookies]
        has_c_user = "c_user" in cookie_names
        has_xs = "xs" in cookie_names

        if not has_c_user or not has_xs:
            logger.warning(f"Missing essential cookies! c_user: {has_c_user}, xs: {has_xs}")
        else:
            c_user = next((c for c in cookies if c.get("name") == "c_user"), None)
            logger.info(f"Session extracted for user {c_user.get('value') if c_user else 'unknown'}")

        return self.data

    def save(self) -> bool:
        """Save session data to file."""
        if not self.data:
            logger.error("No session data to save")
            return False

        try:
            self.session_file.write_text(json.dumps(self.data, indent=2))
            logger.info(f"Session saved to {self.session_file}")
            return True
        except Exception as e:
            logger.error(f"Failed to save session: {e}")
            return False

    def load(self) -> Optional[Dict[str, Any]]:
        """Load session data from file."""
        if not self.session_file.exists():
            logger.info(f"No session file found at {self.session_file}")
            return None

        try:
            self.data = json.loads(self.session_file.read_text())
            logger.info(f"Session loaded from {self.session_file}")
            return self.data
        except Exception as e:
            logger.error(f"Failed to load session: {e}")
            return None

    def get_cookies(self) -> List[Dict]:
        """Get cookies from loaded session."""
        if not self.data:
            return []
        return self.data.get("cookies", [])

    def get_user_agent(self) -> Optional[str]:
        """Get user agent from loaded session."""
        if not self.data:
            return None
        return self.data.get("user_agent")

    def get_viewport(self) -> Dict[str, int]:
        """Get viewport from loaded session."""
        if not self.data:
            return {"width": 393, "height": 873}
        return self.data.get("viewport", {"width": 393, "height": 873})

    def get_proxy(self) -> Optional[str]:
        """Get proxy from loaded session."""
        if not self.data:
            return None
        return self.data.get("proxy")

    def has_valid_cookies(self) -> bool:
        """Check if session has the essential Facebook cookies."""
        if not self.data:
            return False
        cookies = self.data.get("cookies", [])
        cookie_names = [c.get("name") for c in cookies]
        return "c_user" in cookie_names and "xs" in cookie_names

    def get_user_id(self) -> Optional[str]:
        """Get Facebook user ID from c_user cookie."""
        if not self.data:
            return None
        for cookie in self.data.get("cookies", []):
            if cookie.get("name") == "c_user":
                return cookie.get("value")
        return None

    def get_device_fingerprint(self) -> Dict[str, str]:
        """
        Get device fingerprint (timezone, locale) for this session.
        If not set in session data, generates a random USA timezone.
        """
        if not self.data:
            # Generate random fingerprint for new sessions
            return {
                "timezone": random.choice(USA_TIMEZONES),
                "locale": "en-US"
            }

        device = self.data.get("device", {})

        # If device fingerprint exists in session, use it
        if device.get("timezone"):
            return {
                "timezone": device.get("timezone"),
                "locale": device.get("locale", "en-US")
            }

        # Generate consistent fingerprint based on user ID (so same session = same timezone)
        user_id = self.get_user_id()
        if user_id:
            # Use user ID hash to deterministically select timezone
            timezone_index = hash(user_id) % len(USA_TIMEZONES)
            return {
                "timezone": USA_TIMEZONES[timezone_index],
                "locale": "en-US"
            }

        # Fallback to random
        return {
            "timezone": random.choice(USA_TIMEZONES),
            "locale": "en-US"
        }


async def apply_session_to_context(context, session: FacebookSession) -> bool:
    """
    Apply loaded session data to a Playwright browser context.

    Args:
        context: Playwright browser context
        session: FacebookSession with loaded data

    Returns:
        True if cookies were applied successfully
    """
    cookies = session.get_cookies()
    if not cookies:
        logger.error("No cookies to apply")
        return False

    try:
        await context.add_cookies(cookies)
        logger.info(f"Applied {len(cookies)} cookies to context")
        return True
    except Exception as e:
        logger.error(f"Failed to apply cookies: {e}")
        return False


async def verify_session_logged_in(page, debug: bool = True) -> bool:
    """
    Verify if the current page/session is logged into Facebook.
    Uses /me/ navigation which is reliable on both mobile and desktop.

    Args:
        page: Playwright page object
        debug: If True, log extra debugging info

    Returns:
        True if logged in, False otherwise
    """
    try:
        # 1. Check cookies first (fastest check)
        cookies = await page.context.cookies()
        cookie_names = [c.get("name") for c in cookies]

        if "c_user" not in cookie_names:
            logger.info("No c_user cookie - not logged in")
            return False

        c_user_value = next((c.get("value") for c in cookies if c.get("name") == "c_user"), None)
        if debug:
            logger.info(f"Found c_user cookie: {c_user_value}")

        # 2. Navigate to /me/ - this is the DEFINITIVE test
        # If logged in: redirects to user's profile (profile.php?id=XXX or /username)
        # If logged out: redirects to /login
        logger.info("Navigating to /me/ to verify session...")
        await page.goto("https://m.facebook.com/me/", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(2000)

        current_url = page.url.lower()
        if debug:
            logger.info(f"After /me/ redirect, URL: {current_url}")

        # Check for failure conditions
        if "/login" in current_url:
            logger.info("Redirected to login - session invalid")
            return False

        if "checkpoint" in current_url:
            logger.warning("Checkpoint detected - account needs verification")
            return False

        # Check for login form (backup check)
        login_form = await page.locator('input[name="email"]').count()
        if login_form > 0:
            logger.info("Login form present - not logged in")
            return False

        # If we got here with c_user cookie and no redirect to login, we're good!
        logger.info("✅ Session verified: logged in successfully")
        return True

    except Exception as e:
        logger.error(f"Error verifying session: {e}")
        return False


def list_saved_sessions() -> List[Dict[str, Any]]:
    """
    List all saved session files with basic info.

    Returns:
        List of dicts with session info
    """
    sessions = []
    for session_file in sorted(SESSIONS_DIR.glob("*.json")):
        try:
            data = json.loads(session_file.read_text())
            cookie_names = [c.get("name") for c in data.get("cookies", [])]
            sessions.append({
                "file": session_file.name,
                "profile_name": data.get("profile_name"),
                "user_id": None,  # Will extract below
                "extracted_at": data.get("extracted_at"),
                "proxy": data.get("proxy"),
                "has_valid_cookies": ("c_user" in cookie_names and "xs" in cookie_names),
                "profile_picture": data.get("profile_picture"),  # Base64 PNG or None
            })
            # Extract user ID
            for cookie in data.get("cookies", []):
                if cookie.get("name") == "c_user":
                    sessions[-1]["user_id"] = cookie.get("value")
                    break
        except Exception as e:
            logger.error(f"Failed to read {session_file}: {e}")
    return sessions
