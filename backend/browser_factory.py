"""
Browser Factory - Single source of truth for Playwright browser context creation.
Eliminates copy-paste drift across comment_bot.py, login_bot.py, adaptive_agent.py, etc.
"""

import json
import logging
import re
from typing import Any, Dict, Optional
from urllib.parse import urlparse, unquote

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright
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
    timezone_id: str = "",
    locale: str = "en-US",
    headless: bool = True,
    storage_state: Optional[Dict] = None,
    is_mobile: Optional[bool] = None,
    has_touch: Optional[bool] = None,
) -> tuple[Browser, BrowserContext]:
    """Create a stealth-enabled browser context with standard config.

    Args:
        playwright: Playwright instance (from async_playwright().start())
        user_agent: Override user agent (defaults to DEFAULT_USER_AGENT)
        viewport: Override viewport (defaults to MOBILE_VIEWPORT)
        proxy_url: Optional proxy URL
        timezone_id: Timezone for fingerprinting (REQUIRED — must not be empty)
        locale: Locale string
        headless: Whether to run headless

    Returns:
        Tuple of (Browser, BrowserContext) — caller must close browser when done
    """
    browser = await playwright.chromium.launch(headless=headless, args=BROWSER_ARGS)

    if not timezone_id:
        raise ValueError("timezone_id is required — every browser context must have a consistent timezone")

    context_options = {
        "user_agent": user_agent or DEFAULT_USER_AGENT,
        "viewport": viewport or MOBILE_VIEWPORT,
        "ignore_https_errors": True,
        "device_scale_factor": 1,
        "locale": locale,
        "timezone_id": timezone_id,
    }

    if storage_state:
        context_options["storage_state"] = storage_state

    if is_mobile is not None:
        context_options["is_mobile"] = is_mobile

    if has_touch is not None:
        context_options["has_touch"] = has_touch

    if proxy_url:
        proxy_config = build_playwright_proxy(proxy_url)
        if proxy_config:
            context_options["proxy"] = proxy_config

    context = await browser.new_context(**context_options)

    # MANDATORY: Apply stealth mode for anti-detection
    await Stealth().apply_stealth_async(context)

    return browser, context


def _build_android_chromium_identity(user_agent: str) -> Optional[Dict[str, Any]]:
    ua = str(user_agent or "")
    if "Android" not in ua or "Chrome/" not in ua:
        return None

    chrome_match = re.search(r"Chrome/([\d.]+)", ua)
    if not chrome_match:
        return None

    android_match = re.search(r"Android\s+([\d.]+)", ua)
    model_match = re.search(r"Android\s+[\d.]+;\s*([^)]+?)\)", ua)

    full_version = chrome_match.group(1)
    major_version = full_version.split(".", 1)[0]
    platform_version = (android_match.group(1) if android_match else "13").replace("_", ".")

    return {
        "platform": "Android",
        "navigator_platform": "Linux armv8l",
        "brands": [
            {"brand": "Not=A?Brand", "version": "99"},
            {"brand": "Chromium", "version": major_version},
            {"brand": "Google Chrome", "version": major_version},
        ],
        "full_version": full_version,
        "platform_version": platform_version,
        "architecture": "",
        "model": (model_match.group(1).strip() if model_match else ""),
        "mobile": True,
    }


async def apply_page_identity_overrides(
    context: BrowserContext,
    page: Page,
    *,
    user_agent: Optional[str],
    locale: str = "en-US",
) -> None:
    """Align chromium client hints and navigator fields with the chosen mobile UA."""
    identity = _build_android_chromium_identity(user_agent or "")
    if not identity:
        return

    cdp_session = await context.new_cdp_session(page)
    await cdp_session.send(
        "Emulation.setUserAgentOverride",
        {
            "userAgent": user_agent,
            "acceptLanguage": locale,
            "platform": identity["navigator_platform"],
            "userAgentMetadata": {
                "brands": identity["brands"],
                "fullVersion": identity["full_version"],
                "platform": identity["platform"],
                "platformVersion": identity["platform_version"],
                "architecture": identity["architecture"],
                "model": identity["model"],
                "mobile": identity["mobile"],
            },
        },
    )

    identity_json = json.dumps(identity)
    script = """
        (() => {
          const identity = __IDENTITY_JSON__;
          const defineValue = (target, key, value) => {
            try {
              Object.defineProperty(target, key, {
                configurable: true,
                get: () => value,
              });
            } catch (error) {
              // Ignore readonly override failures; CDP already handles the network side.
            }
          };

          const uaData = {
            brands: identity.brands,
            mobile: identity.mobile,
            platform: identity.platform,
            getHighEntropyValues: async (hints) => {
              const values = {
                architecture: identity.architecture,
                brands: identity.brands,
                mobile: identity.mobile,
                model: identity.model,
                platform: identity.platform,
                platformVersion: identity.platform_version,
                uaFullVersion: identity.full_version,
                fullVersionList: identity.brands.map((brand) => ({
                  brand: brand.brand,
                  version: identity.full_version,
                })),
              };
              if (!Array.isArray(hints)) {
                return values;
              }
              return hints.reduce((acc, hint) => {
                if (Object.prototype.hasOwnProperty.call(values, hint)) {
                  acc[hint] = values[hint];
                }
                return acc;
              }, {});
            },
            toJSON: () => ({
              brands: identity.brands,
              mobile: identity.mobile,
              platform: identity.platform,
            }),
          };

          defineValue(Navigator.prototype, "platform", identity.navigator_platform);
          defineValue(Navigator.prototype, "userAgentData", uaData);
          defineValue(Navigator.prototype, "maxTouchPoints", 5);
        })()
        """
    await page.add_init_script(script.replace("__IDENTITY_JSON__", identity_json))
