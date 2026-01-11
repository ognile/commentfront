"""
Login Bot - Automated Facebook login with 2FA support
Uses the same audit trail pattern as comment_bot.py
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Dict, List, Optional, Any
from playwright.async_api import async_playwright, Page, BrowserContext
from playwright_stealth import Stealth

from fb_session import FacebookSession
from fb_selectors import LOGIN, TWO_FA, PAGE_STATE
from credentials import CredentialManager

# Setup logging
logger = logging.getLogger("LoginBot")

# Mobile viewport (same as comment_bot)
MOBILE_VIEWPORT = {"width": 393, "height": 873}
DEFAULT_USER_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"

# Debug directory
DEBUG_DIR = os.path.join(os.path.dirname(__file__), "debug")
os.makedirs(DEBUG_DIR, exist_ok=True)


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
    - 'login_form' - Login page with email/password fields
    - '2fa_selection' - 2FA method selection screen
    - '2fa_code_input' - 2FA code entry screen
    - 'logged_in' - Successfully logged in
    - 'checkpoint' - Security checkpoint
    - 'error' - Error state
    - 'unknown' - Unknown state
    """
    # Check page URL first
    url = page.url.lower()

    if '/checkpoint/' in url:
        return 'checkpoint'

    if '/login/' in url or url.endswith('/login'):
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

    if len(verification_options) >= 2:
        return '2fa_selection'

    # Check for 2FA code input
    for el in elements:
        name = el.get('name', '').lower()
        placeholder = el.get('placeholder', '').lower()
        text = el.get('text', '').lower()

        if 'approvals_code' in name:
            return '2fa_code_input'
        if 'enter code' in placeholder or 'enter the 6' in text or '6-digit' in text:
            return '2fa_code_input'

    # Check for logged in state
    logged_in_indicators = ['create a post', 'notifications', 'what\'s on your mind']
    for el in elements:
        aria = el.get('ariaLabel', '').lower()
        text = el.get('text', '').lower()
        if any(ind in aria or ind in text for ind in logged_in_indicators):
            return 'logged_in'

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
    Handle 2FA method selection - find and click "Authenticator App" option.
    """
    result = {"success": False, "step": "2fa_selection"}

    logger.info("Looking for Authenticator App option...")

    # Build selectors for auth app option based on element dump
    auth_selectors = [
        'div[role="button"]:has-text("Authenticator")',
        'div[role="button"]:has-text("authentication app")',
        'div[role="button"]:has-text("Code Generator")',
        'div:has-text("Authenticator"):visible',
        'div:has-text("Code Generator"):visible',
        'span:has-text("Authenticator")',
        'span:has-text("Code Generator")',
    ]

    # First try text-based search
    for keyword in ["Authenticator", "Code Generator", "authentication app"]:
        try:
            locator = page.get_by_text(keyword, exact=False).first
            if await locator.count() > 0 and await locator.is_visible():
                await save_debug_screenshot(page, "pre_auth_app_click")
                await locator.click()
                logger.info(f"Clicked 2FA option with text: '{keyword}'")
                await save_debug_screenshot(page, "post_auth_app_click")
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
                await locator.click()
                logger.info(f"Clicked 2FA option with selector: {selector}")
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


async def verify_logged_in(page: Page) -> bool:
    """
    Verify that we're logged in by navigating to /me/.
    """
    logger.info("Verifying login by navigating to /me/")

    try:
        await page.goto("https://m.facebook.com/me/", wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)

        url = page.url.lower()
        logger.info(f"After /me/ navigation, URL is: {url}")

        # If we're redirected to login, not logged in
        if '/login' in url:
            logger.warning("Redirected to login page - not logged in")
            return False

        # Check for profile indicators
        elements = await dump_interactive_elements(page, "VERIFY LOGGED IN - /me/ page")

        # Look for profile-related elements
        for el in elements:
            text = el.get('text', '').lower()
            aria = el.get('ariaLabel', '').lower()
            if any(x in text or x in aria for x in ['edit profile', 'about', 'friends', 'timeline']):
                logger.info("Found profile indicators - logged in!")
                return True

        # If not redirected to login, probably logged in
        if '/me/' in url or 'profile' in url:
            logger.info("URL looks like profile page - assuming logged in")
            return True

        return True  # If we got here without redirect, probably logged in

    except Exception as e:
        logger.error(f"Error verifying login: {e}")
        return False


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
    result = {
        "success": False,
        "profile_name": profile_name or f"fb_{uid[-6:]}",
        "step": "init",
        "error": None
    }

    async def broadcast(step: str, status: str, details: dict = None):
        """Broadcast progress update."""
        if broadcast_callback:
            try:
                await broadcast_callback("login_progress", {
                    "uid": uid,
                    "step": step,
                    "status": status,
                    "details": details or {}
                })
            except:
                pass
        logger.info(f"LOGIN PROGRESS: {step} - {status}")

    await broadcast("init", "starting")

    async with async_playwright() as p:
        # Build browser launch options
        launch_args = ["--disable-notifications", "--disable-geolocation"]

        browser = await p.chromium.launch(headless=True, args=launch_args)

        # Build context options
        context_options = {
            "user_agent": DEFAULT_USER_AGENT,
            "viewport": MOBILE_VIEWPORT,
            "ignore_https_errors": True,
            "device_scale_factor": 1,
        }

        if proxy:
            context_options["proxy"] = _build_playwright_proxy(proxy)
            logger.info(f"Using proxy: {proxy[:30]}...")

        context = await browser.new_context(**context_options)

        # Apply stealth mode
        await Stealth().apply_stealth_async(context)

        page = await context.new_page()

        try:
            # === STEP 1: Navigate to login page ===
            result["step"] = "navigate"
            await broadcast("navigate", "in_progress")

            logger.info("Navigating to m.facebook.com/login")
            await page.goto("https://m.facebook.com/login", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)

            # Dump elements for audit trail
            elements = await dump_interactive_elements(page, "LOGIN PAGE LOADED")
            await save_debug_screenshot(page, "login_page")

            await broadcast("navigate", "complete", {"elements_found": len(elements)})

            # === STEP 2: Detect state and handle login form ===
            max_iterations = 10
            iteration = 0

            while iteration < max_iterations:
                iteration += 1
                state = await detect_page_state(page, elements)
                logger.info(f"=== ITERATION {iteration}: Detected state = {state} ===")

                await broadcast(state, "in_progress")

                if state == "login_form":
                    result["step"] = "login_form"
                    form_result = await handle_login_form(page, uid, password)

                    if not form_result["success"]:
                        result["error"] = form_result.get("error", "Login form failed")
                        await broadcast("login_form", "failed", {"error": result["error"]})
                        break

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

                    # Generate TOTP code
                    import pyotp
                    totp = pyotp.TOTP(secret)
                    code = totp.now()

                    code_result = await handle_2fa_code(page, code)

                    if not code_result["success"]:
                        result["error"] = code_result.get("error", "2FA code entry failed")
                        await broadcast("2fa_code", "failed", {"error": result["error"]})
                        break

                    await broadcast("2fa_code", "submitted")
                    await asyncio.sleep(3)
                    elements = await dump_interactive_elements(page, "AFTER 2FA CODE")

                elif state == "logged_in":
                    result["step"] = "verify"
                    await broadcast("verify", "in_progress")

                    # Verify login
                    if await verify_logged_in(page):
                        result["step"] = "extract"
                        await broadcast("extract", "in_progress")

                        # Extract session
                        session = FacebookSession(result["profile_name"])
                        await session.extract_from_page(page, proxy=proxy)
                        session.save()

                        result["success"] = True
                        result["session_file"] = session.file_path
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

                        if iteration >= 3:
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
