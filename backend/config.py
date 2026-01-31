"""
Centralized Configuration for CommentBot
All magic numbers, thresholds, and constants in one place.
"""

import os

# =============================================================================
# BROWSER / VIEWPORT
# =============================================================================

MOBILE_VIEWPORT = {"width": 393, "height": 873}

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)

BROWSER_ARGS = ["--disable-notifications", "--disable-geolocation"]

# =============================================================================
# TIMEZONES (for device fingerprinting)
# =============================================================================

USA_TIMEZONES = [
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Phoenix",
    "America/Anchorage",
]

# =============================================================================
# GEMINI VISION
# =============================================================================

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
CONFIDENCE_THRESHOLD = float(os.getenv("VISION_CONFIDENCE_THRESHOLD", "0.7"))

# =============================================================================
# PATHS
# =============================================================================

DEBUG_DIR = os.getenv("DEBUG_DIR", os.path.join(os.path.dirname(__file__), "debug"))

# =============================================================================
# TIMEOUTS (milliseconds)
# =============================================================================

NAVIGATION_TIMEOUT = 45000
SCREENSHOT_TIMEOUT = 10000
SELECTOR_TIMEOUT = 3000
