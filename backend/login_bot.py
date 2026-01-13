"""
Login Bot - Automated Facebook login with 2FA support
Uses the same audit trail pattern as comment_bot.py
"""

import asyncio
import logging
import os
import random
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Dict, List, Optional, Any
from playwright.async_api import async_playwright, Page, BrowserContext
from playwright_stealth import Stealth

from fb_session import FacebookSession, apply_session_to_context
from fb_selectors import LOGIN, TWO_FA, PAGE_STATE, SIGNUP_PROMPT
from credentials import CredentialManager

# Setup logging
logger = logging.getLogger("LoginBot")

# Mobile viewport (same as comment_bot)
MOBILE_VIEWPORT = {"width": 393, "height": 873}
DEFAULT_USER_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"

# USA timezones for device fingerprinting (matches fb_session.py)
USA_TIMEZONES = [
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Phoenix",
    "America/Anchorage",
]

# Debug directory
DEBUG_DIR = os.path.join(os.path.dirname(__file__), "debug")
os.makedirs(DEBUG_DIR, exist_ok=True)


# ============================================================================
# COMPREHENSIVE LOGGING HELPERS
# ============================================================================

@asynccontextmanager
async def log_timing(operation: str, trace_id: str = ""):
    """Log how long an operation takes."""
    prefix = f"[{trace_id}] " if trace_id else ""
    start = time.time()
    try:
        yield
    finally:
        elapsed = time.time() - start
        logger.info(f"{prefix}⏱️ {operation}: {elapsed:.2f}s")


async def log_failure_context(page: Page, operation: str, error: str, trace_id: str = ""):
    """Log comprehensive context when something fails."""
    prefix = f"[{trace_id}] " if trace_id else ""
    logger.error(f"{prefix}FAILURE: {operation}")
    logger.error(f"{prefix}  Error: {error}")
    logger.error(f"{prefix}  URL: {page.url}")

    try:
        title = await page.title()
        logger.error(f"{prefix}  Page title: {title}")
    except:
        pass

    # Dump visible elements for debugging
    await dump_interactive_elements(page, f"FAILURE CONTEXT: {operation}")

    # Save debug screenshot
    await save_debug_screenshot(page, f"failure_{operation.replace(' ', '_')}")


def setup_navigation_logging(page: Page, trace_id: str = ""):
    """Set up event listeners to log all navigation events."""
    prefix = f"[{trace_id}] " if trace_id else ""

    def on_request(request):
        if request.resource_type == "document":
            logger.info(f"{prefix}📤 Request: {request.method} {request.url}")

    def on_response(response):
        if response.request.resource_type == "document":
            logger.info(f"{prefix}📥 Response: {response.status} {response.url}")

    page.on("request", on_request)
    page.on("response", on_response)


async def save_debug_screenshot(page: Page, name: str) -> Optional[str]:
    """Save a screenshot for debugging."""
    try:
        timestamp = datetime.now().strftime("%H%M%S")
        filename = f"{name}_{timestamp}.png"
        path = os.path.join(DEBUG_DIR, filename)
        await page.screenshot(path=path, scale="css")

        # Also save as latest.png for live view
        latest_path = os.path.join(DEBUG_DIR, "latest.png")
        await page.screenshot(path=latest_path, scale="css")

        logger.info(f"Screenshot saved: {filename}")
        return path
    except Exception as e:
        logger.warning(f"Failed to save screenshot: {e}")
        return None


async def dump_interactive_elements(page: Page, context: str = "") -> List[dict]:
    """
    Dump all interactive elements on the page with their selectors.
    Exact same function as in comment_bot.py - provides audit trail for debugging.
    """
    try:
        elements = await page.evaluate('''() => {
            const elements = [];
            document.querySelectorAll(
                'button, [role="button"], a[href], input, textarea, ' +
                '[contenteditable="true"], [data-sigil], [aria-label]'
            ).forEach((el, i) => {
                const rect = el.getBoundingClientRect();
                // Only include visible elements in viewport
                if (rect.width > 0 && rect.height > 0 && rect.top < window.innerHeight && rect.top > -100) {
                    elements.push({
                        tag: el.tagName,
                        text: (el.innerText || '').slice(0, 30).replace(/\\n/g, ' '),
                        ariaLabel: el.getAttribute('aria-label') || '',
                        role: el.getAttribute('role') || '',
                        sigil: el.getAttribute('data-sigil') || '',
                        placeholder: el.getAttribute('placeholder') || '',
                        contentEditable: el.getAttribute('contenteditable') || '',
                        type: el.getAttribute('type') || '',
                        name: el.getAttribute('name') || '',
                        bounds: {x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height)}
                    });
                }
            });
            return elements;
        }''')

        # Log the elements
        if context:
            logger.info(f"=== {context.upper()} ===")
        logger.info(f"Found {len(elements)} interactive elements:")
        for i, el in enumerate(elements):
            text_info = el.get('text', '')[:20] or el.get('ariaLabel', '')[:20] or el.get('placeholder', '')[:20]
            role_info = f"role=\"{el['role']}\"" if el['role'] else ""
            aria_info = f"aria-label=\"{el['ariaLabel']}\"" if el['ariaLabel'] else ""
            sigil_info = f"data-sigil=\"{el['sigil']}\"" if el['sigil'] else ""
            type_info = f"type=\"{el['type']}\"" if el['type'] else ""
            name_info = f"name=\"{el['name']}\"" if el['name'] else ""

            attrs = " ".join(filter(None, [role_info, aria_info, sigil_info, type_info, name_info]))
            bounds = el['bounds']
            logger.info(f"  [{i}] {el['tag']} {attrs} text=\"{text_info}\" ({bounds['x']},{bounds['y']} {bounds['w']}x{bounds['h']})")

        return elements
    except Exception as e:
        logger.warning(f"Failed to dump interactive elements: {e}")
        return []


async def extract_profile_picture(page: Page) -> Optional[str]:
    """
    Extract profile picture from the current page (should be on profile page).

    Tries multiple strategies:
    1. Screenshot the profile picture element directly
    2. Find and download the profile picture URL

    Returns:
        Base64 encoded PNG image data, or None if extraction fails
    """
    import base64

    logger.info("Attempting to extract profile picture...")

    # Profile picture selectors (on mobile FB profile page)
    # Priority: main profile photo in header area first, then fallback to other areas
    profile_pic_selectors = [
        # Main profile picture in header - "Edit profile photo" area contains the image
        '[aria-label="Edit profile photo"] img',
        '[aria-label*="Edit profile photo"] img',
        # Profile picture elements
        'img[aria-label*="profile picture"]',
        'img[alt*="profile picture"]',
        'svg[aria-label*="profile picture"]',  # Sometimes it's an SVG placeholder
        'div[aria-label*="profile picture"] img',
        'div[aria-label*="Profile Picture"] img',
        # Alternative: Profile header area
        'img[data-sigil="profile-cover-photo-id"]',
        # Profile avatar in header
        'a[aria-label*="profile picture"] img',
        # Generic profile avatar
        '[role="img"][aria-label*="profile"]',
    ]

    for selector in profile_pic_selectors:
        try:
            locator = page.locator(selector).first
            count = await locator.count()
            logger.info(f"Profile pic selector '{selector}' → found {count}")

            if count > 0 and await locator.is_visible():
                # Get bounding box
                box = await locator.bounding_box()
                if box and box['width'] > 20 and box['height'] > 20:
                    # Take screenshot of just this element
                    screenshot_bytes = await locator.screenshot(type="png")
                    if screenshot_bytes:
                        base64_data = base64.b64encode(screenshot_bytes).decode('utf-8')
                        logger.info(f"✅ Extracted profile picture via: {selector} ({len(screenshot_bytes)} bytes)")
                        return base64_data
        except Exception as e:
            logger.warning(f"Failed to extract via '{selector}': {e}")
            continue

    # Strategy 2: Find any img in profile header area and screenshot it
    try:
        # Look for the profile section which usually has the picture
        profile_header = page.locator('div[data-sigil*="profile"]').first
        if await profile_header.count() > 0:
            img = profile_header.locator('img').first
            if await img.count() > 0 and await img.is_visible():
                screenshot_bytes = await img.screenshot(type="png")
                if screenshot_bytes:
                    base64_data = base64.b64encode(screenshot_bytes).decode('utf-8')
                    logger.info(f"✅ Extracted profile picture from profile header ({len(screenshot_bytes)} bytes)")
                    return base64_data
    except Exception as e:
        logger.warning(f"Profile header strategy failed: {e}")

    # Strategy 3: Take screenshot of area where profile pic usually is
    # On mobile FB profile.php page, the "Edit profile photo" element is at (12,105) 149x149
    try:
        # Crop the profile picture area based on observed page layout
        full_screenshot = await page.screenshot(type="png", clip={
            "x": 12,   # Left position of profile photo
            "y": 105,  # Top position (below header bar)
            "width": 149,
            "height": 149
        })
        if full_screenshot:
            base64_data = base64.b64encode(full_screenshot).decode('utf-8')
            logger.info(f"✅ Extracted profile picture from fixed region ({len(full_screenshot)} bytes)")
            return base64_data
    except Exception as e:
        logger.warning(f"Fixed region strategy failed: {e}")

    logger.warning("❌ Failed to extract profile picture with any strategy")
    return None


async def smart_click(page: Page, selectors: List[str], description: str) -> bool:
    """
    Try to click an element using multiple selectors.
    Same pattern as comment_bot.py.
    """
    logger.info(f"=== ATTEMPTING CLICK: {description} ===")
    logger.info(f"Trying {len(selectors)} selectors...")

    for selector in selectors:
        try:
            locator = page.locator(selector).first
            count = await locator.count()
            logger.info(f"  Selector '{selector}' → found {count} element(s)")

            if count > 0:
                if await locator.is_visible():
                    await save_debug_screenshot(page, f"pre_click_{description.replace(' ', '_')}")
                    # Use real click(), not dispatch_event() which Facebook ignores
                    await locator.click()
                    logger.info(f"  → CLICKED successfully via: {selector}")
                    await save_debug_screenshot(page, f"post_click_{description.replace(' ', '_')}")
                    return True
                else:
                    logger.info(f"  → Found but not visible, skipping")
        except Exception as e:
            continue

    # Fallback: Text search
    try:
        text_locator = page.get_by_text(description, exact=False).first
        if await text_locator.count() > 0 and await text_locator.is_visible():
            await text_locator.click()
            logger.info(f"Clicked '{description}' using text match")
            return True
    except:
        pass

    logger.warning(f"  → FAILED: No selector matched for '{description}'")
    await save_debug_screenshot(page, f"failed_click_{description.replace(' ', '_')}")
    return False


async def smart_focus(page: Page, selectors: List[str], description: str) -> bool:
    """
    Focus a text input field.
    Same pattern as comment_bot.py.
    """
    logger.info(f"smart_focus: Looking for '{description}' with {len(selectors)} selectors")

    for selector in selectors:
        try:
            locator = page.locator(selector).first
            count = await locator.count()
            logger.info(f"  Selector '{selector}' → found {count} element(s)")

            if count > 0:
                await save_debug_screenshot(page, f"pre_focus_{description.replace(' ', '_')}")

                if await locator.is_visible():
                    await locator.focus()
                    logger.info(f"Focused '{description}' using: {selector}")
                    await save_debug_screenshot(page, f"post_focus_{description.replace(' ', '_')}")
                    return True
        except Exception as e:
            logger.warning(f"  Focus error on '{selector}': {e}")
            continue

    logger.warning(f"Failed to focus: {description}")
    await save_debug_screenshot(page, f"failed_focus_{description.replace(' ', '_')}")
    return False


def _build_playwright_proxy(proxy_url: str) -> Dict[str, str]:
    """Build Playwright proxy config from URL."""
    from urllib.parse import urlparse, unquote

    if not proxy_url:
        return None

    parsed = urlparse(proxy_url)
    proxy: Dict[str, str] = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
    if parsed.username:
        proxy["username"] = unquote(parsed.username)
    if parsed.password:
        proxy["password"] = unquote(parsed.password)
    return proxy


async def detect_page_state(page: Page, elements: List[dict]) -> str:
    """
    Detect the current page state based on visible elements.

    Returns one of:
    - 'loading' - Page is loading (button shows Loading...)
    - 'error' - Error message on page (wrong password, locked, etc.)
    - 'signup_prompt' - Facebook signup page (need to click "I already have an account")
    - 'login_form' - Login page with email/password fields
    - '2fa_selection' - 2FA method selection screen
    - '2fa_code_input' - 2FA code entry screen
    - 'logged_in' - Successfully logged in
    - 'checkpoint' - Security checkpoint
    - 'unknown' - Unknown state
    """
    # Get current URL for state detection
    url = page.url

    # FIRST: Check for logged in state via URL (before any element checks)
    # This handles the homepage redirect case where elements=0 while page loads
    # Must be at the very top because blank page check would return 'loading' otherwise
    if 'm.facebook.com' in url and '/login' not in url and '/checkpoint' not in url:
        return 'logged_in'

    # Check for loading state - before any other checks
    # This prevents re-filling credentials while page is loading
    for el in elements:
        aria = el.get('ariaLabel', '').lower()
        if 'loading' in aria:
            return 'loading'

    # Check for blank/minimal page (loading/transition state)
    # If we have very few elements (0-2) and none have meaningful text, it's likely loading
    if len(elements) <= 2:
        has_meaningful_content = False
        for el in elements:
            text = el.get('text', '').strip()
            aria = el.get('ariaLabel', '').strip()
            if text or aria:
                has_meaningful_content = True
                break
        if not has_meaningful_content:
            return 'loading'

    # Check for error states - wrong password, account locked, etc.
    # Must check BEFORE login_form to avoid retrying with same wrong password
    error_keywords = [
        'wrong password',
        'incorrect password',
        'password you entered is incorrect',
        'password is incorrect',
        'too many attempts',
        'account has been locked',
        'account is disabled',
        'temporarily locked',
        'try again later',
        'unusual activity',
        'security check required',
        'please try again'
    ]
    for el in elements:
        text = el.get('text', '').lower()
        for keyword in error_keywords:
            if keyword in text:
                return 'error'

    # Check for signup/welcome prompt (Facebook sometimes shows this instead of login)
    # Must check BEFORE URL check because URL may still be /login
    signup_indicators = ['join facebook', 'get started', 'i already have an account', 'create new account']
    signup_count = 0
    has_email_input = False

    for el in elements:
        text = el.get('text', '').lower()
        aria = el.get('ariaLabel', '').lower()
        name = el.get('name', '')

        # Check for email input
        if name == 'email':
            has_email_input = True

        # Count signup indicators
        for ind in signup_indicators:
            if ind in text or ind in aria:
                signup_count += 1
                break  # Only count once per element

    # If we see multiple signup indicators but NO email input, it's a signup prompt
    if signup_count >= 2 and not has_email_input:
        return 'signup_prompt'

    # Check page URL
    url = page.url.lower()

    if '/checkpoint/' in url:
        return 'checkpoint'

    # IMPORTANT: Check for 2FA code input FIRST (before device_approval)
    # Because the 2FA code entry screen also has "Try another way" text
    for el in elements:
        name = el.get('name', '').lower()
        placeholder = el.get('placeholder', '').lower()
        text = el.get('text', '').lower()
        aria = el.get('ariaLabel', '').lower()
        el_type = el.get('type', '').lower()

        # Match input with aria-label="Code" (Facebook mobile 2FA)
        if aria == 'code' and el_type == 'text':
            return '2fa_code_input'
        if 'approvals_code' in name:
            return '2fa_code_input'
        if 'enter code' in placeholder or 'enter the 6' in text or '6-digit' in text:
            return '2fa_code_input'

    # Check for device approval / notification approval screen
    # This is when FB asks to approve on another device with "Try another way" option
    # But ONLY if we don't have a code input field (checked above)
    for el in elements:
        text = el.get('text', '').lower()
        aria = el.get('ariaLabel', '').lower()
        if 'waiting for approval' in text or 'waiting for approval' in aria:
            return 'device_approval'
        if 'check your notifications' in text or 'check your notifications' in aria:
            return 'device_approval'

    # Handle URLs like /login, /login/, /login#, /login?...
    # BUT only if we actually have email/password inputs (not device approval)
    url_path = url.split('?')[0].split('#')[0]  # Remove query and hash
    if '/login' in url_path:
        # Need to verify this is actually a login form with inputs
        has_email = False
        has_pass = False
        for el in elements:
            name = el.get('name', '').lower()
            el_type = el.get('type', '').lower()
            if name == 'email' or el_type == 'email':
                has_email = True
            if name == 'pass' or el_type == 'password':
                has_pass = True
        if has_email and has_pass:
            return 'login_form'

    # Check for login form indicators
    email_input_found = False
    password_input_found = False
    login_button_found = False

    for el in elements:
        name = el.get('name', '').lower()
        el_type = el.get('type', '').lower()
        text = el.get('text', '').lower()
        aria = el.get('ariaLabel', '').lower()

        if name == 'email' or el_type == 'email':
            email_input_found = True
        if name == 'pass' or el_type == 'password':
            password_input_found = True
        if 'log in' in text or 'log in' in aria:
            login_button_found = True

    if email_input_found and password_input_found:
        return 'login_form'

    # Check for 2FA selection screen (multiple verification options)
    # Look for clear heading indicator first
    for el in elements:
        text = el.get('text', '').lower()
        aria = el.get('ariaLabel', '').lower()
        if 'choose a way to confirm' in text or 'choose a way to confirm' in aria:
            return '2fa_selection'

    verification_options = []
    for el in elements:
        text = el.get('text', '').lower()
        aria = el.get('ariaLabel', '').lower()
        combined = text + ' ' + aria

        if any(keyword in combined for keyword in ['text message', 'sms', 'phone']):
            verification_options.append('sms')
        if any(keyword in combined for keyword in ['email', 'mail']):
            verification_options.append('email')
        if any(keyword in combined for keyword in ['authenticator', 'code generator', 'authentication app']):
            verification_options.append('authenticator')
        # Device notification is another verification method
        if 'notification on another device' in combined or 'approve' in combined:
            verification_options.append('notification')

    if len(verification_options) >= 2:
        return '2fa_selection'

    # NOTE: 2FA code input check has been moved to the top of this function
    # to prevent false device_approval detection on the 2FA code entry screen

    # Check for "Save your login info?" screen (appears after successful login)
    for el in elements:
        text = el.get('text', '').lower()
        aria = el.get('ariaLabel', '').lower()
        if 'save your login info' in text or 'save your login info' in aria:
            return 'save_device'

    # Check for logged in state via elements
    logged_in_indicators = ['create a post', 'notifications', 'what\'s on your mind']
    for el in elements:
        aria = el.get('ariaLabel', '').lower()
        text = el.get('text', '').lower()
        if any(ind in aria or ind in text for ind in logged_in_indicators):
            return 'logged_in'

    # NOTE: URL-based logged_in check is at the TOP of this function
    # to handle homepage redirect before blank page returns 'loading'

    # Check for checkpoint indicators
    checkpoint_keywords = ['secure your account', 'confirm your identity', 'security check']
    for el in elements:
        text = el.get('text', '').lower()
        if any(keyword in text for keyword in checkpoint_keywords):
            return 'checkpoint'

    return 'unknown'


async def handle_login_form(page: Page, email: str, password: str) -> Dict[str, Any]:
    """
    Handle the login form - fill email and password, click login.
    Uses fill() instead of keyboard.type() to REPLACE content (not append).
    """
    result = {"success": False, "step": "login_form"}

    try:
        # Use fill() which clears field first then types - prevents text appending
        email_locator = page.locator('input[name="email"]')
        await email_locator.fill(email)
        logger.info(f"Filled email: {email[:3]}***")

        # Verify email was entered correctly
        actual_email = await email_locator.input_value()
        logger.info(f"Email field value: {actual_email[:3]}***{actual_email[-3:]} ({len(actual_email)} chars)")
        if actual_email != email:
            logger.error(f"Email mismatch! Expected {len(email)} chars, got {len(actual_email)}")
            result["error"] = "Email entry verification failed"
            return result

        await asyncio.sleep(0.3)

        # Use fill() for password
        pass_locator = page.locator('input[name="pass"]')
        await pass_locator.fill(password)
        logger.info("Filled password: ****")

        # Verify password was entered correctly
        actual_pass = await pass_locator.input_value()
        logger.info(f"Password field contains: {len(actual_pass)} chars")
        if actual_pass != password:
            logger.error(f"Password mismatch! Expected {len(password)} chars, got {len(actual_pass)}")
            result["error"] = "Password entry verification failed"
            return result

    except Exception as e:
        logger.error(f"Failed to fill credentials: {e}")
        result["error"] = f"Failed to fill credentials: {e}"
        return result

    await asyncio.sleep(0.5)

    # Dump elements before clicking login
    await dump_interactive_elements(page, "BEFORE LOGIN CLICK")

    # Log URL before click
    logger.info(f"URL before login click: {page.url}")

    # Click login button
    if not await smart_click(page, LOGIN["login_button"], "Log in"):
        result["error"] = "Failed to click login button"
        return result

    # Wait and log URL after click to verify navigation
    await asyncio.sleep(2)
    logger.info(f"URL after login click: {page.url}")

    result["success"] = True
    return result


async def handle_2fa_selection(page: Page, elements: List[dict]) -> Dict[str, Any]:
    """
    Handle 2FA method selection - find and click "Authenticator App" option, then Continue.
    """
    result = {"success": False, "step": "2fa_selection"}

    logger.info("Looking for Authenticator App option...")

    # Build selectors for auth app option based on element dump
    # Facebook uses role="radio" for selection options
    auth_selectors = [
        'div[role="radio"][aria-label*="Authentication app"]',
        'div[role="radio"][aria-label*="Authenticator"]',
        'div[role="radio"]:has-text("Authentication app")',
        'div[role="button"]:has-text("Authenticator")',
        'div[role="button"]:has-text("authentication app")',
        'div[role="button"]:has-text("Code Generator")',
        'div:has-text("Authenticator"):visible',
        'div:has-text("Code Generator"):visible',
        'span:has-text("Authenticator")',
        'span:has-text("Code Generator")',
    ]

    # First try text-based search
    for keyword in ["Authentication app", "Authenticator", "Code Generator"]:
        try:
            locator = page.get_by_text(keyword, exact=False).first
            if await locator.count() > 0 and await locator.is_visible():
                await save_debug_screenshot(page, "pre_auth_app_click")
                await locator.click()
                logger.info(f"Clicked 2FA option with text: '{keyword}'")
                await save_debug_screenshot(page, "post_auth_app_click")

                # After selecting, need to click Continue button
                await asyncio.sleep(0.5)
                continue_selectors = [
                    'div[role="button"][aria-label="Continue"]',
                    'div[role="button"]:has-text("Continue")',
                    'button:has-text("Continue")',
                ]
                for cont_selector in continue_selectors:
                    try:
                        cont_locator = page.locator(cont_selector).first
                        if await cont_locator.count() > 0 and await cont_locator.is_visible():
                            await cont_locator.click()
                            logger.info("Clicked Continue button after 2FA selection")
                            break
                    except:
                        continue

                result["success"] = True
                return result
        except Exception as e:
            logger.debug(f"Failed to click '{keyword}': {e}")
            continue

    # Try CSS selectors
    for selector in auth_selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count() > 0 and await locator.is_visible():
                await save_debug_screenshot(page, "pre_auth_app_click")
                await locator.click()
                logger.info(f"Clicked 2FA option with selector: {selector}")

                # After selecting, need to click Continue button
                await asyncio.sleep(0.5)
                continue_selectors = [
                    'div[role="button"][aria-label="Continue"]',
                    'div[role="button"]:has-text("Continue")',
                    'button:has-text("Continue")',
                ]
                for cont_selector in continue_selectors:
                    try:
                        cont_locator = page.locator(cont_selector).first
                        if await cont_locator.count() > 0 and await cont_locator.is_visible():
                            await cont_locator.click()
                            logger.info("Clicked Continue button after 2FA selection")
                            break
                    except:
                        continue

                result["success"] = True
                return result
        except:
            continue

    # Search through elements for auth app option
    for i, el in enumerate(elements):
        text = el.get('text', '').lower()
        aria = el.get('ariaLabel', '').lower()
        combined = text + ' ' + aria

        if any(keyword in combined for keyword in ['authenticator', 'code generator', 'authentication app']):
            # Try to click by coordinates
            bounds = el['bounds']
            x = bounds['x'] + bounds['w'] // 2
            y = bounds['y'] + bounds['h'] // 2

            logger.info(f"Clicking auth app option at ({x}, {y})")
            await page.mouse.click(x, y)
            result["success"] = True
            return result

    result["error"] = "Could not find Authenticator App option"
    return result


async def handle_2fa_code(page: Page, totp_code: str) -> Dict[str, Any]:
    """
    Handle 2FA code entry - enter the TOTP code.
    """
    result = {"success": False, "step": "2fa_code"}

    logger.info(f"Entering 2FA code: {totp_code}")

    # Focus the code input
    code_selectors = TWO_FA["code_input"]
    if not await smart_focus(page, code_selectors, "2FA code input"):
        # Try text-based input
        try:
            placeholder_locator = page.get_by_placeholder("Enter code", exact=False)
            if await placeholder_locator.count() > 0:
                await placeholder_locator.fill(totp_code)
                logger.info("Filled 2FA code using placeholder")
            else:
                result["error"] = "Failed to find 2FA code input"
                return result
        except Exception as e:
            result["error"] = f"Failed to enter 2FA code: {e}"
            return result
    else:
        await page.keyboard.type(totp_code, delay=100)

    await asyncio.sleep(0.5)
    await dump_interactive_elements(page, "AFTER 2FA CODE ENTERED")

    # Click submit/continue button
    submit_selectors = TWO_FA["submit_button"]
    if not await smart_click(page, submit_selectors, "Continue"):
        result["error"] = "Failed to click submit button"
        return result

    result["success"] = True
    return result


async def handle_device_trust(page: Page) -> Dict[str, Any]:
    """
    Handle "Trust this device?" prompt if shown.
    """
    result = {"success": False, "step": "device_trust"}

    # Look for trust/remember options
    trust_keywords = ["Trust", "Remember", "Save browser", "Don't ask again", "Yes"]

    for keyword in trust_keywords:
        try:
            locator = page.get_by_text(keyword, exact=False).first
            if await locator.count() > 0 and await locator.is_visible():
                await locator.click()
                logger.info(f"Clicked trust option: '{keyword}'")
                result["success"] = True
                return result
        except:
            continue

    # Try checkbox if present
    trust_checkbox_selectors = TWO_FA.get("trust_device_checkbox", [])
    for selector in trust_checkbox_selectors:
        try:
            checkbox = page.locator(selector).first
            if await checkbox.count() > 0 and await checkbox.is_visible():
                await checkbox.check()
                logger.info("Checked trust device checkbox")
                result["success"] = True
                break
        except:
            continue

    # Click continue/submit if checkbox was checked or even if not found
    submit_selectors = TWO_FA["submit_button"]
    await smart_click(page, submit_selectors, "Continue")

    result["success"] = True
    return result


async def verify_logged_in(page: Page, extract_picture: bool = False, user_id: Optional[str] = None) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Verify that we're logged in by navigating to profile page and extract profile name.

    Args:
        page: Playwright page object
        extract_picture: If True, also extract profile picture (slower)
        user_id: If provided, navigate directly to profile URL (more reliable than /me/)

    Returns:
        Tuple of (is_logged_in: bool, profile_name: Optional[str], profile_picture_base64: Optional[str])
    """
    # Use direct profile URL if user_id is provided (more reliable)
    if user_id:
        profile_url = f"https://m.facebook.com/profile.php?id={user_id}"
        logger.info(f"Verifying login by navigating directly to profile: {profile_url}")
    else:
        profile_url = "https://m.facebook.com/me/"
        logger.info("Verifying login by navigating to /me/")
    profile_name = None

    try:
        # Use domcontentloaded instead of networkidle (Facebook never stops network activity)
        # Add retry logic for slow connections
        max_retries = 3
        for attempt in range(max_retries):
            try:
                await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
                break  # Success
            except Exception as nav_error:
                if attempt < max_retries - 1:
                    logger.warning(f"Navigation attempt {attempt + 1} failed, retrying: {nav_error}")
                    await asyncio.sleep(2)
                else:
                    raise nav_error  # Re-raise on final attempt

        # Wait for dynamic content to render (Facebook is JS-heavy)
        # Try to wait for profile-specific elements before proceeding
        content_loaded = False
        content_selectors = [
            'h1',  # Profile name heading
            '[aria-label*="profile picture"]',  # Profile picture
            '[aria-label="Edit profile"]',  # Edit profile button
            '[aria-label="About"]',  # About section
            '[role="heading"]',  # Any heading
        ]

        for selector in content_selectors:
            try:
                await page.wait_for_selector(selector, timeout=5000)
                content_loaded = True
                logger.info(f"Content loaded - found: {selector}")
                break
            except:
                continue

        if not content_loaded:
            # Fallback: wait longer for slow JS rendering
            logger.info("No profile content selectors found, waiting for JS render...")
            await asyncio.sleep(4)
        else:
            # Brief additional wait for any remaining content
            await asyncio.sleep(1)

        url = page.url.lower()
        logger.info(f"After profile navigation, URL is: {url}")

        # If we're redirected to login, not logged in
        if '/login' in url:
            logger.warning("Redirected to login page - not logged in")
            return False, None, None

        # Check for profile indicators and extract profile name
        elements = await dump_interactive_elements(page, "VERIFY LOGGED IN - /me/ page")

        # Check if we're on homepage (redirected from /me/) - need to click "Go to profile"
        # This happens when /me/ redirects to m.facebook.com/ instead of profile page
        is_homepage = url.rstrip('/').endswith('m.facebook.com') or url.rstrip('/').endswith('facebook.com')

        if is_homepage:
            logger.info("Redirected to homepage instead of profile - clicking 'Go to profile'")

            # First, dismiss any blocking dialogs (like "Get app" modal)
            dismiss_selectors = [
                '[aria-label="Not now"]',
                '[aria-label="Close"]',
                'div[role="button"]:has-text("Not now")',
                'div[role="button"]:has-text("Close")',
            ]
            for selector in dismiss_selectors:
                try:
                    locator = page.locator(selector).first
                    if await locator.count() > 0 and await locator.is_visible():
                        await locator.click()
                        logger.info(f"Dismissed dialog via: {selector}")
                        await asyncio.sleep(1)
                        break
                except:
                    continue

            # Re-fetch elements after dismissing dialog
            elements = await dump_interactive_elements(page, "AFTER DISMISSING DIALOG")

            # Find and click "Go to profile" button
            go_to_profile_clicked = False
            for el in elements:
                aria = el.get('ariaLabel', '').lower()
                text = el.get('text', '').lower()
                if 'go to profile' in aria or 'go to profile' in text:
                    # Click the element
                    try:
                        locator = page.locator(f'[aria-label="Go to profile"]').first
                        if await locator.count() > 0 and await locator.is_visible():
                            await locator.click()
                            go_to_profile_clicked = True
                            logger.info("Clicked 'Go to profile' button")
                            await asyncio.sleep(3)
                            break
                    except Exception as e:
                        logger.warning(f"Failed to click 'Go to profile': {e}")

            if not go_to_profile_clicked:
                # Try alternative selectors
                profile_selectors = [
                    '[aria-label="Go to profile"]',
                    'div[role="button"]:has-text("Go to profile")',
                    'a[href*="/me"]',
                ]
                for selector in profile_selectors:
                    try:
                        locator = page.locator(selector).first
                        if await locator.count() > 0 and await locator.is_visible():
                            await locator.click()
                            go_to_profile_clicked = True
                            logger.info(f"Clicked profile link via: {selector}")
                            await asyncio.sleep(3)
                            break
                    except:
                        continue

            if go_to_profile_clicked:
                # Wait for profile page to load
                await page.wait_for_load_state("networkidle", timeout=15000)
                await asyncio.sleep(2)
                # Re-fetch elements after navigation
                url = page.url.lower()
                logger.info(f"After clicking Go to profile, URL is: {url}")
                elements = await dump_interactive_elements(page, "AFTER GO TO PROFILE CLICK")

        # Try to extract profile name from page title first
        # Sometimes the title takes a moment to update from "Facebook" to the profile name
        page_title = None
        excluded_titles = ['facebook', 'log in', 'login', 'home', 'news feed', 'feed']

        for title_attempt in range(5):  # Try 5 times with short waits
            page_title = await page.title()
            logger.info(f"Page title (attempt {title_attempt + 1}): {page_title}")

            if page_title:
                # If it has " | " separator, take the first part
                if '|' in page_title:
                    name_part = page_title.split('|')[0].strip()
                else:
                    name_part = page_title.strip()

                # Validate it's actually a profile name, not a generic page title
                if name_part and name_part.lower() not in excluded_titles and len(name_part) > 1:
                    profile_name = name_part
                    logger.info(f"Extracted profile name from title: {profile_name}")
                    break

            # If still generic title, wait and retry
            if title_attempt < 4:
                await asyncio.sleep(1)

        # Once we have a good name from title, skip element-based extraction
        # to avoid picking up "Edit profile" button text

        # Strategy 2: Look for h1/h2 headings on the profile page
        if not profile_name:
            try:
                # Try to find the main profile name heading
                heading_selectors = [
                    'h1',
                    'h2',
                    '[role="heading"][aria-level="1"]',
                    '[role="heading"][aria-level="2"]',
                ]
                for selector in heading_selectors:
                    try:
                        locator = page.locator(selector).first
                        if await locator.count() > 0 and await locator.is_visible():
                            heading_text = await locator.text_content()
                            if heading_text:
                                heading_text = heading_text.strip()
                                # Skip generic headings
                                excluded = ['posts', 'about', 'friends', 'photos', 'videos', 'more',
                                           'edit profile', 'facebook', 'home', 'news feed']
                                if heading_text.lower() not in excluded and len(heading_text) > 1 and len(heading_text) < 50:
                                    profile_name = heading_text
                                    logger.info(f"Extracted profile name from heading ({selector}): {profile_name}")
                                    break
                    except:
                        continue
            except Exception as e:
                logger.warning(f"Error extracting from headings: {e}")

        # Strategy 3: Look for profile picture's aria-label which often contains the name
        if not profile_name:
            try:
                # Profile picture often has aria-label like "John Smith's profile picture"
                # or "Sangtraan Profile Picture, view story"
                profile_pic_selectors = [
                    'img[aria-label*="profile picture"]',
                    'img[alt*="profile picture"]',
                    'div[aria-label*="profile picture"]',
                    'div[aria-label*="Profile Picture"]',
                ]
                for selector in profile_pic_selectors:
                    try:
                        locator = page.locator(selector).first
                        if await locator.count() > 0:
                            aria_label = await locator.get_attribute('aria-label')
                            if not aria_label:
                                aria_label = await locator.get_attribute('alt')
                            if aria_label and 'profile picture' in aria_label.lower():
                                # Extract name from various formats:
                                # "John Smith's profile picture"
                                # "Sangtraan Profile Picture, view story"
                                # "Profile picture of John Smith"
                                name = aria_label
                                # Remove common suffixes
                                name = name.replace("'s profile picture", "").replace("'s profile photo", "")
                                name = name.replace("'s Profile Picture", "").replace("'s Profile Photo", "")
                                name = name.replace("Profile picture of ", "").replace("Profile Picture of ", "")
                                # Handle "Name Profile Picture, view story" format
                                if " Profile Picture" in name:
                                    name = name.split(" Profile Picture")[0]
                                if " profile picture" in name:
                                    name = name.split(" profile picture")[0]
                                name = name.replace("profile picture", "").replace("Profile Picture", "")
                                name = name.strip().strip(',')
                                if name and len(name) > 1 and len(name) < 50:
                                    profile_name = name
                                    logger.info(f"Extracted profile name from profile picture: {profile_name}")
                                    break
                    except:
                        continue
            except Exception as e:
                logger.warning(f"Error extracting from profile picture: {e}")

        # Strategy 4: Look for "Tap to open profile page" element which contains just the name
        if not profile_name:
            for el in elements:
                aria = el.get('ariaLabel', '').lower()
                text = el.get('text', '').strip()
                # "Tap to open profile page" button usually has just the name as text
                if 'tap to open profile' in aria and text:
                    # This element typically shows just the profile name
                    if len(text) > 1 and len(text) < 50:
                        excluded = ['profile', 'go to profile', 'edit profile', 'tap to open']
                        if text.lower() not in excluded:
                            profile_name = text
                            logger.info(f"Extracted profile name from 'Tap to open profile' element: {profile_name}")
                            break

        # Strategy 5: Use elements from dump - look for headings
        if not profile_name:
            for el in elements:
                text = el.get('text', '').strip()
                aria = el.get('ariaLabel', '').strip()
                role = el.get('role', '').lower()

                # Look for profile header element which often contains the name
                # On mobile FB, there's often a prominent text showing the profile name
                if role == 'heading' and text and len(text) > 1 and len(text) < 50:
                    # Avoid generic headings
                    excluded = ['posts', 'about', 'friends', 'photos', 'videos', 'more',
                               'edit profile', 'facebook', 'home', 'news feed']
                    if text.lower() not in excluded:
                        profile_name = text
                        logger.info(f"Extracted profile name from heading element: {profile_name}")
                        break

        # Strategy 6: Look for name in profile-related aria-labels
        if not profile_name:
            for el in elements:
                aria = el.get('ariaLabel', '')
                text = el.get('text', '').strip()

                # Check if aria-label contains profile picture reference
                if 'profile picture' in aria.lower() or 'profile photo' in aria.lower():
                    name = aria
                    # Remove common suffixes
                    name = name.replace("'s profile picture", "").replace("'s profile photo", "")
                    name = name.replace("'s Profile Picture", "").replace("'s Profile Photo", "")
                    name = name.replace("Profile picture of ", "").replace("Profile Picture of ", "")
                    # Handle "Name Profile Picture, view story" format
                    if " Profile Picture" in name:
                        name = name.split(" Profile Picture")[0]
                    if " profile picture" in name:
                        name = name.split(" profile picture")[0]
                    name = name.replace("profile picture", "").replace("Profile Picture", "")
                    name = name.strip().strip(',')
                    if name and len(name) > 1 and len(name) < 50 and name.lower() != 'profile picture':
                        profile_name = name
                        logger.info(f"Extracted profile name from aria-label: {profile_name}")
                        break

                # Try to get name from profile-related text elements
                if 'profile' in aria.lower() and text and len(text) > 1 and len(text) < 50:
                    # Clean up any icons or extra text
                    clean_name = text.split('󳂊')[0].strip()  # Remove FB icon
                    excluded = ['profile', 'go to profile', 'edit profile', 'view profile']
                    if clean_name and clean_name.lower() not in excluded:
                        profile_name = clean_name
                        logger.info(f"Extracted profile name from profile element: {profile_name}")
                        break

        # Look for profile-related elements to verify login
        is_logged_in = False
        for el in elements:
            text = el.get('text', '').lower()
            aria = el.get('ariaLabel', '').lower()
            if any(x in text or x in aria for x in ['edit profile', 'about', 'friends', 'timeline']):
                logger.info("Found profile indicators - logged in!")
                is_logged_in = True
                break

        # If not redirected to login, probably logged in
        if not is_logged_in and ('/me/' in url or 'profile' in url):
            logger.info("URL looks like profile page - assuming logged in")
            is_logged_in = True

        if not is_logged_in:
            is_logged_in = True  # If we got here without redirect, probably logged in

        # Extract profile picture if requested
        profile_picture = None
        if is_logged_in and extract_picture:
            profile_picture = await extract_profile_picture(page)

        return is_logged_in, profile_name, profile_picture

    except Exception as e:
        logger.error(f"Error verifying login: {e}")
        return False, None, None


async def refresh_session_profile_name(profile_name: str) -> Dict[str, Any]:
    """
    Refresh the profile name for an existing session by navigating to /me/.

    Args:
        profile_name: Current profile name (session file identifier)

    Returns:
        Dict with success, new_profile_name, old_profile_name
    """
    result = {
        "success": False,
        "old_profile_name": profile_name,
        "new_profile_name": None,
        "error": None
    }

    try:
        # Load existing session
        session = FacebookSession(profile_name)
        if not session.load():
            result["error"] = f"Session not found: {profile_name}"
            return result

        user_id = session.get_user_id()
        logger.info(f"Refreshing profile name for session {profile_name} (user_id: {user_id})")

        # Get session data (matching comment_bot.py gold standard)
        user_agent = session.get_user_agent() or DEFAULT_USER_AGENT
        viewport = session.get_viewport() or MOBILE_VIEWPORT
        proxy_url = session.get_proxy()
        device_fingerprint = session.get_device_fingerprint()

        logger.info(f"Refreshing profile with fingerprint: timezone={device_fingerprint['timezone']}, locale={device_fingerprint['locale']}")

        # Build context options (MUST match comment_bot.py exactly)
        context_options = {
            "user_agent": user_agent,
            "viewport": viewport,
            "ignore_https_errors": True,
            "device_scale_factor": 1,
            "timezone_id": device_fingerprint["timezone"],
            "locale": device_fingerprint["locale"],
        }

        if proxy_url:
            context_options["proxy"] = _build_playwright_proxy(proxy_url)
            logger.info(f"Using session proxy for refresh")

        # Launch browser with session
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-notifications", "--disable-geolocation"]
            )

            context = await browser.new_context(**context_options)

            # MANDATORY: Apply stealth mode for anti-detection
            await Stealth().apply_stealth_async(context)

            page = await context.new_page()

            # Apply cookies via helper function for consistency
            if not await apply_session_to_context(context, session):
                raise Exception("Failed to apply session cookies")

            # Navigate directly to profile page using user_id (more reliable than /me/)
            is_logged_in, extracted_name, profile_picture = await verify_logged_in(page, extract_picture=True, user_id=user_id)

            await browser.close()

            if not is_logged_in:
                result["error"] = "Session is no longer valid - redirected to login"
                return result

            if not extracted_name:
                result["error"] = "Could not extract profile name from Facebook"
                return result

            # Update session file with new name and/or picture
            needs_update = extracted_name != profile_name or profile_picture
            if needs_update:
                # Use extracted name if different, otherwise keep current
                final_name = extracted_name if extracted_name != profile_name else profile_name

                # Create new/updated session
                new_session = FacebookSession(final_name)
                new_session.data = {
                    "profile_name": final_name,
                    "extracted_at": datetime.now().isoformat(),
                    "cookies": session.get_cookies(),
                    "user_agent": session.get_user_agent(),
                    "viewport": session.get_viewport(),
                    "proxy": session.get_proxy(),
                    "device": device_fingerprint,  # Preserve device fingerprint for consistency
                }

                # Add profile picture if we got one
                if profile_picture:
                    new_session.data["profile_picture"] = profile_picture
                    logger.info("Added profile picture to session")

                new_session.save()

                # Delete old session file if name changed
                if extracted_name != profile_name and os.path.exists(session.session_file):
                    os.remove(session.session_file)
                    logger.info(f"Removed old session file: {session.session_file}")

                if extracted_name != profile_name:
                    logger.info(f"Renamed session from {profile_name} to {extracted_name}")

            result["success"] = True
            result["new_profile_name"] = extracted_name
            result["user_id"] = user_id
            result["profile_picture"] = profile_picture is not None

            # Update credential profile_name as well
            cred_manager = CredentialManager()
            if user_id and cred_manager.update_profile_name(user_id, extracted_name):
                logger.info(f"Updated credential profile_name for {user_id}")

            return result

    except Exception as e:
        logger.error(f"Error refreshing profile name: {e}")
        result["error"] = str(e)
        return result


async def login_facebook(
    uid: str,
    password: str,
    secret: Optional[str] = None,
    proxy: Optional[str] = None,
    profile_name: Optional[str] = None,
    broadcast_callback=None
) -> Dict[str, Any]:
    """
    Main login function - automates Facebook login with 2FA support.

    Args:
        uid: Facebook UID or email
        password: Facebook password
        secret: 2FA TOTP secret (base32)
        proxy: Proxy URL (optional)
        profile_name: Profile name for session file (defaults to fb_{last6_of_uid})
        broadcast_callback: Async function to broadcast progress updates

    Returns:
        Dict with success, session data, or error info
    """
    # Generate unique trace ID for this login attempt
    trace_id = str(uuid.uuid4())[:8]

    result = {
        "success": False,
        "profile_name": profile_name or f"fb_{uid[-6:]}",
        "step": "init",
        "error": None,
        "trace_id": trace_id  # Include in result for debugging
    }

    async def broadcast(step: str, status: str, details: dict = None):
        """Broadcast progress update."""
        if broadcast_callback:
            try:
                await broadcast_callback("login_progress", {
                    "uid": uid,
                    "step": step,
                    "status": status,
                    "trace_id": trace_id,
                    "details": details or {}
                })
            except:
                pass
        logger.info(f"[{trace_id}] LOGIN PROGRESS: {step} - {status}")

    await broadcast("init", "starting")
    logger.info(f"[{trace_id}] Starting login for {uid[:6]}***")

    # Generate device fingerprint for this new session
    # Use random USA timezone since we don't have user_id yet
    login_device_fingerprint = {
        "timezone": random.choice(USA_TIMEZONES),
        "locale": "en-US"
    }
    logger.info(f"[{trace_id}] Login with fingerprint: timezone={login_device_fingerprint['timezone']}")

    async with async_playwright() as p:
        # Build browser launch options
        launch_args = ["--disable-notifications", "--disable-geolocation"]

        browser = await p.chromium.launch(headless=True, args=launch_args)

        # Build context options (matching comment_bot.py gold standard)
        context_options = {
            "user_agent": DEFAULT_USER_AGENT,
            "viewport": MOBILE_VIEWPORT,
            "ignore_https_errors": True,
            "device_scale_factor": 1,
            "timezone_id": login_device_fingerprint["timezone"],
            "locale": login_device_fingerprint["locale"],
        }

        if proxy:
            context_options["proxy"] = _build_playwright_proxy(proxy)
            logger.info(f"Using proxy: {proxy[:30]}...")

        context = await browser.new_context(**context_options)

        # Apply stealth mode
        await Stealth().apply_stealth_async(context)

        page = await context.new_page()

        # Set up navigation event logging
        setup_navigation_logging(page, trace_id)

        try:
            # === STEP 1: Navigate to login page ===
            result["step"] = "navigate"
            await broadcast("navigate", "in_progress")

            async with log_timing("Navigate to login", trace_id):
                logger.info(f"[{trace_id}] Navigating to m.facebook.com/login")
                await page.goto("https://m.facebook.com/login", wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(2)

            # Log actual URL and page title after navigation
            actual_url = page.url
            logger.info(f"[{trace_id}] Requested: m.facebook.com/login → Landed on: {actual_url}")
            try:
                page_title = await page.title()
                logger.info(f"[{trace_id}] Page title: {page_title}")
            except:
                pass

            # Dump elements for audit trail
            elements = await dump_interactive_elements(page, "LOGIN PAGE LOADED")
            await save_debug_screenshot(page, "login_page")

            await broadcast("navigate", "complete", {"elements_found": len(elements)})

            # === STEP 2: Detect state and handle login form ===
            max_iterations = 10
            iteration = 0
            login_submitted = False  # Track if we've already submitted credentials

            while iteration < max_iterations:
                iteration += 1
                state = await detect_page_state(page, elements)
                logger.info(f"[{trace_id}] === ITERATION {iteration}: state={state}, elements={len(elements)}, url={page.url} ===")

                await broadcast(state, "in_progress")

                if state == "login_form":
                    # If we already submitted, don't re-fill - just wait for state change
                    if login_submitted:
                        logger.info(f"[{trace_id}] Already submitted login, waiting for page transition...")
                        await asyncio.sleep(2)
                        elements = await dump_interactive_elements(page, "WAITING FOR TRANSITION")
                        continue  # Re-check state

                    result["step"] = "login_form"
                    form_result = await handle_login_form(page, uid, password)

                    if not form_result["success"]:
                        result["error"] = form_result.get("error", "Login form failed")
                        await broadcast("login_form", "failed", {"error": result["error"]})
                        break

                    login_submitted = True  # Mark as submitted
                    await broadcast("login_form", "submitted")
                    await asyncio.sleep(3)
                    elements = await dump_interactive_elements(page, "AFTER LOGIN SUBMIT")

                elif state == "2fa_selection":
                    result["step"] = "2fa_selection"

                    if not secret:
                        result["error"] = "2FA required but no secret configured"
                        result["needs_attention"] = True
                        await broadcast("2fa_selection", "needs_attention", {"error": result["error"]})
                        break

                    selection_result = await handle_2fa_selection(page, elements)

                    if not selection_result["success"]:
                        result["error"] = selection_result.get("error", "2FA selection failed")
                        result["needs_attention"] = True
                        await broadcast("2fa_selection", "failed", {"error": result["error"]})
                        break

                    await broadcast("2fa_selection", "complete")
                    await asyncio.sleep(2)
                    elements = await dump_interactive_elements(page, "AFTER 2FA SELECTION")

                elif state == "2fa_code_input":
                    result["step"] = "2fa_code"

                    if not secret:
                        result["error"] = "2FA code required but no secret configured"
                        result["needs_attention"] = True
                        await broadcast("2fa_code", "needs_attention", {"error": result["error"]})
                        break

                    # Generate TOTP code - normalize secret (remove spaces, uppercase)
                    import pyotp
                    normalized_secret = secret.replace(" ", "").replace("-", "").upper()
                    totp = pyotp.TOTP(normalized_secret)
                    code = totp.now()

                    code_result = await handle_2fa_code(page, code)

                    if not code_result["success"]:
                        result["error"] = code_result.get("error", "2FA code entry failed")
                        await broadcast("2fa_code", "failed", {"error": result["error"]})
                        break

                    await broadcast("2fa_code", "submitted")
                    await asyncio.sleep(3)
                    elements = await dump_interactive_elements(page, "AFTER 2FA CODE")

                elif state == "save_device":
                    # "Save your login info?" screen - click Save to continue
                    result["step"] = "save_device"
                    logger.info("Save device screen detected, clicking 'Save'...")
                    await broadcast("save_device", "in_progress")

                    save_selectors = [
                        'div[role="button"][aria-label="Save"]',
                        'div[role="button"]:has-text("Save")',
                        'button[aria-label="Save"]',
                        'button:has-text("Save")',
                    ]

                    if await smart_click(page, save_selectors, "Save"):
                        logger.info("Clicked 'Save' button")
                        await asyncio.sleep(2)
                        elements = await dump_interactive_elements(page, "AFTER SAVE DEVICE")
                        # Continue loop - should now be logged in
                    else:
                        # Try "Not now" as fallback
                        not_now_selectors = [
                            'div[role="button"][aria-label="Not now"]',
                            'div[role="button"]:has-text("Not now")',
                        ]
                        if await smart_click(page, not_now_selectors, "Not now"):
                            logger.info("Clicked 'Not now' button")
                            await asyncio.sleep(2)
                            elements = await dump_interactive_elements(page, "AFTER NOT NOW")
                        else:
                            result["error"] = "Could not click Save or Not now"
                            result["needs_attention"] = True
                            await broadcast("save_device", "needs_attention", {"error": result["error"]})
                            break

                elif state == "logged_in":
                    result["step"] = "verify"
                    await broadcast("verify", "in_progress")

                    # Verify login and extract profile name + picture
                    is_logged_in, extracted_profile_name, profile_picture = await verify_logged_in(page, extract_picture=True)
                    if is_logged_in:
                        # Update profile name if we extracted a real name
                        if extracted_profile_name:
                            result["profile_name"] = extracted_profile_name
                            logger.info(f"Using extracted profile name: {extracted_profile_name}")

                        result["step"] = "extract"
                        await broadcast("extract", "in_progress")

                        # Extract session with the (potentially updated) profile name
                        session = FacebookSession(result["profile_name"])
                        await session.extract_from_page(page, proxy=proxy)

                        # Add profile picture if we got one
                        if profile_picture and session.data:
                            session.data["profile_picture"] = profile_picture
                            logger.info("Added profile picture to session")

                        # Add device fingerprint for session consistency
                        if session.data:
                            session.data["device"] = login_device_fingerprint
                            logger.info(f"Saved device fingerprint to session: {login_device_fingerprint['timezone']}")

                        # Validate essential cookies exist before saving
                        if not session.has_valid_cookies():
                            result["error"] = "Failed to extract essential cookies (c_user, xs)"
                            await broadcast("extract", "failed", {"error": result["error"]})
                            break

                        session.save()
                        logger.info(f"Session saved with valid cookies for {result['profile_name']}")

                        result["success"] = True
                        result["session_file"] = str(session.session_file)
                        result["user_id"] = session.get_user_id()

                        await broadcast("complete", "success", {
                            "profile_name": result["profile_name"],
                            "user_id": result["user_id"]
                        })

                        logger.info(f"Login successful! Session saved: {result['profile_name']}")
                    else:
                        result["error"] = "Login verification failed"
                        await broadcast("verify", "failed")

                    break

                elif state == "checkpoint":
                    result["step"] = "checkpoint"
                    result["error"] = "Security checkpoint requires manual intervention"
                    result["needs_attention"] = True
                    await save_debug_screenshot(page, "checkpoint")
                    await broadcast("checkpoint", "needs_attention", {
                        "error": result["error"],
                        "elements": [e.get("text", "")[:30] for e in elements[:10]]
                    })
                    break

                elif state == "loading":
                    # Page is loading - wait before checking again
                    logger.info("Page is loading, waiting...")
                    await broadcast("loading", "waiting")
                    await asyncio.sleep(3)
                    elements = await dump_interactive_elements(page, "AFTER LOADING WAIT")
                    # Continue loop - will re-check state

                elif state == "device_approval":
                    # Facebook is asking to approve on another device
                    # Click "Try another way" to get to authenticator code entry
                    result["step"] = "device_approval"
                    logger.info("Device approval screen detected, clicking 'Try another way'...")
                    await broadcast("device_approval", "handling")

                    try_another_selectors = [
                        'div[role="button"]:has-text("Try another way")',
                        'div[role="button"][aria-label*="Try another way"]',
                        'button:has-text("Try another way")',
                    ]

                    if await smart_click(page, try_another_selectors, "Try another way"):
                        await asyncio.sleep(2)
                        elements = await dump_interactive_elements(page, "AFTER TRY ANOTHER WAY")
                        # Continue loop - should now see 2FA selection or code input
                    else:
                        # Try text-based click
                        try:
                            locator = page.get_by_text("Try another way", exact=False).first
                            if await locator.count() > 0 and await locator.is_visible():
                                await locator.click()
                                logger.info("Clicked 'Try another way' via text match")
                                await asyncio.sleep(2)
                                elements = await dump_interactive_elements(page, "AFTER TRY ANOTHER WAY (TEXT)")
                            else:
                                result["error"] = "Could not find 'Try another way' button"
                                result["needs_attention"] = True
                                await broadcast("device_approval", "needs_attention", {"error": result["error"]})
                                break
                        except Exception as e:
                            result["error"] = f"Failed to click 'Try another way': {e}"
                            result["needs_attention"] = True
                            await broadcast("device_approval", "failed", {"error": result["error"]})
                            break

                elif state == "error":
                    # Found error message on page - extract and fail immediately
                    result["step"] = "error"
                    error_text = "Login failed"

                    # Find the actual error message
                    error_keywords = ['wrong', 'incorrect', 'locked', 'disabled', 'attempts', 'try again']
                    for el in elements:
                        text = el.get('text', '')
                        if any(kw in text.lower() for kw in error_keywords):
                            error_text = text[:150]  # First 150 chars
                            break

                    result["error"] = f"Facebook error: {error_text}"
                    await save_debug_screenshot(page, "login_error")
                    await broadcast("error", "failed", {"error": result["error"]})
                    break  # Exit loop - don't retry with same wrong password

                elif state == "signup_prompt":
                    # Facebook showed signup page instead of login - click "I already have an account"
                    logger.info("Detected signup prompt, clicking 'I already have an account'...")
                    await broadcast("signup_prompt", "handling")

                    if await smart_click(page, SIGNUP_PROMPT["already_have_account"], "I already have an account"):
                        await asyncio.sleep(2)
                        elements = await dump_interactive_elements(page, "AFTER SIGNUP PROMPT CLICK")
                        # Continue loop - should now see login form
                    else:
                        result["error"] = "Failed to click 'I already have an account' button"
                        await broadcast("signup_prompt", "failed", {"error": result["error"]})
                        break

                else:
                    # Unknown state - check if there's a trust device prompt
                    for el in elements:
                        text = el.get('text', '').lower()
                        if any(x in text for x in ['trust', 'remember', 'save browser']):
                            state = "device_trust"
                            break

                    if state == "device_trust":
                        result["step"] = "device_trust"
                        await handle_device_trust(page)
                        await asyncio.sleep(2)
                        elements = await dump_interactive_elements(page, "AFTER DEVICE TRUST")
                    else:
                        # Truly unknown state
                        logger.warning(f"Unknown page state on iteration {iteration}")
                        await save_debug_screenshot(page, f"unknown_state_{iteration}")

                        # Give more attempts before giving up on unknown state
                        if iteration >= 5:
                            result["error"] = f"Unknown page state after {iteration} iterations"
                            result["needs_attention"] = True
                            await broadcast("unknown", "needs_attention", {
                                "elements": [e.get("text", "")[:30] for e in elements[:10]]
                            })
                            break

                        await asyncio.sleep(2)
                        elements = await dump_interactive_elements(page, f"RETRY {iteration}")

        except Exception as e:
            logger.error(f"Login error: {e}")
            result["error"] = str(e)
            await save_debug_screenshot(page, "error")
            await broadcast("error", "failed", {"error": str(e)})

        finally:
            await browser.close()

    return result


async def create_session_from_credentials(
    credential_uid: str,
    proxy_url: Optional[str] = None,
    broadcast_callback=None
) -> Dict[str, Any]:
    """
    Create a session by logging in with stored credentials.

    Args:
        credential_uid: UID of the credential to use
        proxy_url: Optional proxy URL (overrides credential's assigned proxy)
        broadcast_callback: Async function to broadcast progress updates

    Returns:
        Result dict with success, session info, or error
    """
    # Load credential
    cred_manager = CredentialManager()
    credential = cred_manager.get_credential(credential_uid)

    if not credential:
        return {
            "success": False,
            "error": f"Credential not found: {credential_uid}"
        }

    uid = credential.get("uid")
    password = credential.get("password")
    secret = credential.get("secret")
    profile_name = credential.get("profile_name")

    if not password:
        return {
            "success": False,
            "error": "Credential has no password"
        }

    # Login
    result = await login_facebook(
        uid=uid,
        password=password,
        secret=secret,
        proxy=proxy_url,
        profile_name=profile_name,
        broadcast_callback=broadcast_callback
    )

    return result
