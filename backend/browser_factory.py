"""
Browser Factory - Single source of truth for Playwright browser context creation.
Eliminates copy-paste drift across comment_bot.py, login_bot.py, adaptive_agent.py, etc.
"""

import logging
from typing import Dict, Optional
from urllib.parse import urlparse, unquote

from playwright.async_api import async_playwright, Browser, BrowserContext, Playwright
from playwright_stealth import Stealth

from config import MOBILE_VIEWPORT, DEFAULT_USER_AGENT, BROWSER_ARGS

logger = logging.getLogger("BrowserFactory")


def build_playwright_proxy(proxy_url: str) -> Optional[Dict[str, str]]:
    """Convert proxy URL to Playwright proxy config dict.

    Args:
        proxy_url: URL like http://user:pass@host:port

    Returns:
        Playwright proxy dict or None if invalid
    """
    if not proxy_url:
        return None

    parsed = urlparse(proxy_url)
    if not (parsed.scheme and parsed.hostname and parsed.port):
        return {"server": proxy_url}

    proxy: Dict[str, str] = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
    if parsed.username:
        proxy["username"] = unquote(parsed.username)
    if parsed.password:
        proxy["password"] = unquote(parsed.password)
    return proxy


async def create_browser_context(
    playwright: Playwright,
    user_agent: Optional[str] = None,
    viewport: Optional[Dict] = None,
    proxy_url: Optional[str] = None,
    timezone_id: Optional[str] = None,
    locale: str = "en-US",
    headless: bool = True,
) -> tuple[Browser, BrowserContext]:
    """Create a stealth-enabled browser context with standard config.

    Args:
        playwright: Playwright instance (from async_playwright().start())
        user_agent: Override user agent (defaults to DEFAULT_USER_AGENT)
        viewport: Override viewport (defaults to MOBILE_VIEWPORT)
        proxy_url: Optional proxy URL
        timezone_id: Timezone for fingerprinting
        locale: Locale string
        headless: Whether to run headless

    Returns:
        Tuple of (Browser, BrowserContext) — caller must close browser when done
    """
    browser = await playwright.chromium.launch(headless=headless, args=BROWSER_ARGS)

    context_options = {
        "user_agent": user_agent or DEFAULT_USER_AGENT,
        "viewport": viewport or MOBILE_VIEWPORT,
        "ignore_https_errors": True,
        "device_scale_factor": 1,
        "locale": locale,
    }

    if timezone_id:
        context_options["timezone_id"] = timezone_id

    if proxy_url:
        proxy_config = build_playwright_proxy(proxy_url)
        if proxy_config:
            context_options["proxy"] = proxy_config

    context = await browser.new_context(**context_options)

    # MANDATORY: Apply stealth mode for anti-detection
    await Stealth().apply_stealth_async(context)

    return browser, context
