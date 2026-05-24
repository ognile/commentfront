"""
Centralized Configuration for CommentBot
All magic numbers, thresholds, and constants in one place.
"""

import os

from env_loader import load_project_env


load_project_env()

# =============================================================================
# BROWSER / VIEWPORT
# =============================================================================

MOBILE_VIEWPORT = {"width": 393, "height": 873}

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)

REDDIT_MOBILE_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/133.0.0.0 Mobile Safari/537.36"
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
# GEMINI MODELS
# =============================================================================

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

GEMINI_TEXT_MODEL = os.getenv("GEMINI_TEXT_MODEL", "gemini-3.5-flash")
GEMINI_VISION_MODEL = os.getenv("GEMINI_VISION_MODEL", GEMINI_TEXT_MODEL)
GEMINI_COMMUNITY_TEXT_MODEL = os.getenv("GEMINI_COMMUNITY_TEXT_MODEL", GEMINI_TEXT_MODEL)
GEMINI_COMMUNITY_PLANNER_MODEL = os.getenv("GEMINI_COMMUNITY_PLANNER_MODEL", GEMINI_TEXT_MODEL)
GEMINI_REDDIT_GENERATION_MODEL = os.getenv("GEMINI_REDDIT_GENERATION_MODEL", GEMINI_TEXT_MODEL)
GEMINI_IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-3-pro-image-preview")

GEMINI_MODELS = {
    "text": GEMINI_TEXT_MODEL,
    "vision": GEMINI_VISION_MODEL,
    "community_text": GEMINI_COMMUNITY_TEXT_MODEL,
    "community_planner": GEMINI_COMMUNITY_PLANNER_MODEL,
    "reddit_generation": GEMINI_REDDIT_GENERATION_MODEL,
    "image": GEMINI_IMAGE_MODEL,
}

# Public shorthand for the configured vision model.
GEMINI_MODEL = GEMINI_MODELS["vision"]


def get_gemini_model(capability: str) -> str:
    """Return the configured Gemini model for one objective capability."""
    try:
        return GEMINI_MODELS[capability]
    except KeyError as exc:
        raise KeyError(f"unknown gemini capability: {capability}") from exc


CONFIDENCE_THRESHOLD = float(os.getenv("VISION_CONFIDENCE_THRESHOLD", "0.7"))

# =============================================================================
# AI CAMPAIGN GENERATION
# =============================================================================

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

FACEBOOK_APP_TOKEN = os.getenv("FACEBOOK_APP_TOKEN", "")
FACEBOOK_PAGE_ACCESS_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN", "")
FACEBOOK_GRAPH_API_VERSION = os.getenv("FACEBOOK_GRAPH_API_VERSION", "v23.0")

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
