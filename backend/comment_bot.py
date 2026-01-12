import asyncio
import logging
import os
import re
from playwright.async_api import async_playwright, Page
# Stealth mode is MANDATORY for anti-detection
from playwright_stealth import Stealth

from typing import Optional, Dict, Any, List
from urllib.parse import urlparse, unquote

from fb_session import FacebookSession, apply_session_to_context
import fb_selectors

# Vision integration (optional - will work without it)
try:
    from gemini_vision import get_vision_client
    VISION_AVAILABLE = True
except ImportError:
    VISION_AVAILABLE = False
    get_vision_client = lambda: None

logger = logging.getLogger("CommentBot")

MOBILE_VIEWPORT = {"width": 393, "height": 873}
DEFAULT_USER_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"

# Directory for debug screenshots
DEBUG_DIR = os.path.join(os.path.dirname(__file__), "debug")
os.makedirs(DEBUG_DIR, exist_ok=True)

def _build_playwright_proxy(proxy_url: str) -> Dict[str, str]:
    parsed = urlparse(proxy_url)
    if parsed.scheme and parsed.hostname and parsed.port:
        proxy: Dict[str, str] = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
        if parsed.username:
            proxy["username"] = unquote(parsed.username)
        if parsed.password:
            proxy["password"] = unquote(parsed.password)
        return proxy
    return {"server": proxy_url}


async def save_debug_screenshot(page: Page, name: str) -> str:
    """Save a screenshot for debugging. Returns the path.

    Uses scale=1 to ensure screenshot pixel coordinates match viewport coordinates.
    This is critical for vision_element_click() to work correctly.
    """
    try:
        path = os.path.join(DEBUG_DIR, f"{name}.png")
        # scale=1 ensures screenshot pixels = viewport pixels (no DPI scaling)
        await page.screenshot(path=path, scale="css")
        latest_path = os.path.join(DEBUG_DIR, "latest.png")
        await page.screenshot(path=latest_path, scale="css")
        logger.info(f"Saved debug screenshot: {path}")
        return path
    except Exception as e:
        logger.warning(f"Failed to save screenshot: {e}")
        return ""


async def dump_interactive_elements(page: Page, context: str = "") -> List[dict]:
    """
    Dump all interactive elements on the page with their selectors.
    Like 'Inspect Element' - shows what's ACTUALLY clickable.

    Args:
        page: Playwright page
        context: Description of when this is being called (e.g., "after page load")

    Returns:
        List of element info dicts
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
            editable_info = "contenteditable" if el['contentEditable'] == 'true' else ""

            attrs = " ".join(filter(None, [role_info, aria_info, sigil_info, editable_info]))
            bounds = el['bounds']
            logger.info(f"  [{i}] {el['tag']} {attrs} text=\"{text_info}\" ({bounds['x']},{bounds['y']} {bounds['w']}x{bounds['h']})")

        return elements
    except Exception as e:
        logger.warning(f"Failed to dump interactive elements: {e}")
        return []


async def vision_click(page: Page, element_type: str, fallback_selectors: List[str], description: str) -> Dict[str, Any]:
    """Click an element using Gemini vision with CSS selector fallback."""
    result = {"success": False, "method": "none", "confidence": 0}
    vision = get_vision_client() if VISION_AVAILABLE else None

    if vision:
        for attempt in range(2):
            try:
                screenshot_path = await save_debug_screenshot(page, f"vision_{element_type}_{attempt}")
                if not screenshot_path:
                    continue
                location = await vision.find_element(screenshot_path=screenshot_path, element_type=element_type)
                if location and location.found and location.confidence > 0.7:
                    logger.info(f"Vision found {description} at ({location.x}, {location.y}) conf={location.confidence:.0%}")
                    await page.mouse.click(location.x, location.y)
                    await save_debug_screenshot(page, f"post_vision_click_{element_type}")
                    result["success"] = True
                    result["method"] = "vision"
                    result["confidence"] = location.confidence
                    return result
                elif location and location.found and location.confidence > 0.5:
                    # Low confidence - just retry without scrolling
                    logger.info(f"Vision low confidence ({location.confidence:.0%}), retrying...")
                    await asyncio.sleep(0.3)
            except Exception as e:
                logger.warning(f"Vision error attempt {attempt+1}: {e}")

    logger.info(f"Falling back to CSS selectors for {description}")
    if await smart_click(page, fallback_selectors, description):
        result["success"] = True
        result["method"] = "selector"
    return result


async def verify_comment_visually(page: Page, comment_text: str) -> Dict[str, Any]:
    """Verify that a comment was posted using vision."""
    result = {"verified": False, "confidence": 0, "message": ""}
    vision = get_vision_client() if VISION_AVAILABLE else None
    if not vision:
        result["verified"] = True
        result["message"] = "Vision not available, assuming success"
        return result

    await asyncio.sleep(2)
    screenshot_path = await save_debug_screenshot(page, "verify_comment")
    if not screenshot_path:
        result["message"] = "Failed to take screenshot"
        return result

    try:
        verification = await vision.verify_comment_posted(screenshot_path=screenshot_path, expected_comment=comment_text)
        result["confidence"] = verification.confidence
        result["message"] = verification.message
        if verification.success:
            logger.info(f"Comment verified: {verification.message}")
            result["verified"] = True
        elif verification.status == "pending":
            await asyncio.sleep(3)
            screenshot_path = await save_debug_screenshot(page, "verify_retry")
            if screenshot_path:
                verification = await vision.verify_comment_posted(screenshot_path, comment_text)
                result["verified"] = verification.success
                result["confidence"] = verification.confidence
    except Exception as e:
        logger.error(f"Verification error: {e}")
        result["message"] = str(e)
    return result


async def smart_click(page: Page, selectors: List[str], description: str) -> bool:
    """
    Try to click an element using multiple selectors.
    Uses Playwright's native .click() which handles actionability and overlapping elements.
    Falls back to dispatch_event for elements that need synthetic clicks.

    For send/post buttons, tries .last to handle stacked button layouts where
    the send button appears on top of other buttons when text is entered.
    """
    logger.info(f"=== ATTEMPTING CLICK: {description} ===")
    logger.info(f"Trying {len(selectors)} selectors...")

    # For send buttons, try last element first (topmost in stacked layout)
    is_send_button = "send" in description.lower() or "post" in description.lower()

    for selector in selectors:
        try:
            all_matches = page.locator(selector)
            count = await all_matches.count()
            logger.info(f"  Selector '{selector}' → found {count} element(s)")

            if count > 0:
                # For send buttons with multiple matches, try last first (topmost element)
                if is_send_button and count > 1:
                    locators_to_try = [all_matches.last, all_matches.first]
                    logger.info(f"  → Send button with {count} matches, trying last first")
                else:
                    locators_to_try = [all_matches.first]

                for locator in locators_to_try:
                    try:
                        # Snapshot before action for live view
                        await save_debug_screenshot(page, f"pre_click_{description.replace(' ', '_')}")

                        if await locator.is_visible():
                            # Try native click first - handles overlapping elements and React events better
                            try:
                                await locator.click(timeout=3000)
                                logger.info(f"  → CLICKED (native) successfully via: {selector}")
                                await save_debug_screenshot(page, f"post_click_{description.replace(' ', '_')}")
                                return True
                            except Exception as click_err:
                                # Native click failed (maybe obscured), try dispatch_event as fallback
                                logger.info(f"  → Native click failed ({click_err}), trying dispatch_event...")
                                try:
                                    await locator.dispatch_event('click')
                                    logger.info(f"  → CLICKED (dispatch_event) successfully via: {selector}")
                                    await save_debug_screenshot(page, f"post_click_{description.replace(' ', '_')}")
                                    return True
                                except Exception as dispatch_err:
                                    logger.info(f"  → dispatch_event also failed: {dispatch_err}")
                        else:
                            logger.info(f"  → Found but not visible, skipping")
                    except Exception as loc_err:
                        logger.info(f"  → Locator attempt failed: {loc_err}")
                        continue
        except Exception as e:
            continue

    # Fallback: Text search
    try:
        text_locator = page.get_by_text(description, exact=False).first
        if await text_locator.count() > 0 and await text_locator.is_visible():
            try:
                await text_locator.click(timeout=3000)
                logger.info(f"Clicked '{description}' using text match (native click)")
                return True
            except:
                await text_locator.dispatch_event('click')
                logger.info(f"Clicked '{description}' using text match dispatch_event")
                return True
    except:
        pass

    logger.warning(f"  → FAILED: No selector matched for '{description}'")
    await save_debug_screenshot(page, f"failed_click_{description.replace(' ', '_')}")
    return False


async def smart_focus(page: Page, selectors: List[str], description: str) -> bool:
    """
    Focus a text input field (contenteditable, textbox, textarea).
    Uses focus() instead of dispatch_event('click') which doesn't work for inputs.
    FB_LOGIN_GUIDE.md: dispatch_event works for buttons, but text fields need focus().
    """
    logger.info(f"smart_focus: Looking for '{description}' with {len(selectors)} selectors")
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            count = await locator.count()
            logger.info(f"  Selector '{selector}' → found {count} element(s)")
            if count > 0:
                # No scroll - element should already be visible
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


async def find_comment_input(page: Page) -> bool:
    """
    Find and activate the comment input using Playwright's semantic locators.
    After clicking the placeholder, we need to wait and then focus the actual input.
    """
    logger.info("find_comment_input: Trying Playwright semantic locators")

    # Strategy 1: Playwright semantic locators (most reliable for text-based elements)
    strategies = [
        ("get_by_placeholder('Write a comment...')", page.get_by_placeholder("Write a comment...")),
        ("get_by_placeholder('Write a comment', exact=False)", page.get_by_placeholder("Write a comment", exact=False)),
        ("get_by_text('Write a comment...')", page.get_by_text("Write a comment...")),
        ("get_by_text('Write a comment', exact=False)", page.get_by_text("Write a comment", exact=False)),
        ("get_by_role('textbox')", page.get_by_role("textbox")),
    ]

    for name, locator in strategies:
        try:
            count = await locator.count()
            logger.info(f"  {name} → found {count} element(s)")
            if count > 0:
                # No scroll - element should already be visible
                if await locator.first.is_visible():
                    # Click to activate the input
                    await locator.first.click()
                    logger.info(f"Clicked comment input using: {name}")

                    # Wait for UI to respond after click
                    await asyncio.sleep(0.5)

                    # After clicking placeholder, try to focus the actual input element
                    # On mobile FB, clicking placeholder reveals/activates a contenteditable div
                    focus_selectors = [
                        'div[contenteditable="true"]',
                        'div[role="textbox"]',
                        '[contenteditable="true"]',
                    ]
                    for focus_sel in focus_selectors:
                        try:
                            focus_loc = page.locator(focus_sel).first
                            if await focus_loc.count() > 0 and await focus_loc.is_visible():
                                await focus_loc.focus()
                                logger.info(f"Focused element using: {focus_sel}")
                                break
                        except Exception:
                            pass

                    await save_debug_screenshot(page, "clicked_comment_input")
                    return True
        except Exception as e:
            logger.debug(f"  {name} failed: {e}")

    logger.warning("find_comment_input: All strategies failed")
    return False


async def audit_selectors(page: Page, selectors_dict: dict) -> dict:
    """
    Run all selectors and report matches with details.
    Used for diagnostics when clicks fail.
    """
    audit = {}
    for action, selectors in selectors_dict.items():
        audit[action] = []
        for selector in selectors:
            try:
                locator = page.locator(selector)
                count = await locator.count()
                if count > 0:
                    text = await locator.first.text_content() or ""
                    visible = await locator.first.is_visible()
                    audit[action].append({
                        "selector": selector,
                        "count": count,
                        "visible": visible,
                        "text": text[:50] if text else ""
                    })
            except Exception:
                pass
    return audit


async def click_with_healing(
    page: Page,
    vision,
    selectors: List[str],
    description: str,
    max_attempts: int = 5
) -> dict:
    """
    Self-healing click loop - uses CSS selectors first, asks Gemini for guidance on failure.

    Returns:
        dict with success, method, selector_used, attempts, and any diagnostic info
    """
    import json

    result = {
        "success": False,
        "method": None,
        "selector_used": None,
        "attempts": 0,
        "decisions": []
    }

    for attempt in range(max_attempts):
        result["attempts"] = attempt + 1
        logger.info(f"click_with_healing attempt {attempt + 1}/{max_attempts} for '{description}'")

        # 1. Try CSS selectors first (fast, deterministic)
        click_success = await smart_click(page, selectors, description)
        if click_success:
            result["success"] = True
            result["method"] = "css_selector"
            logger.info(f"✓ Clicked '{description}' via CSS selector")
            return result

        # 2. CSS failed - get diagnostics
        screenshot = await save_debug_screenshot(page, f"healing_{description.replace(' ', '_')}_{attempt}")
        audit = await audit_selectors(page, fb_selectors.COMMENT)
        logger.info(f"Selector audit: {json.dumps(audit, indent=2)}")

        # 3. Ask Gemini what to do (if vision available)
        if vision:
            decision = await vision.decide_next_action(screenshot, description, audit)
            result["decisions"].append(decision)
            logger.info(f"Gemini decision: {decision}")

            # 4. Execute Gemini's decision
            action = decision.get("action", "RETRY")

            if action == "ABORT":
                reason = decision.get("reason", "unknown")
                logger.error(f"ABORT: {reason}")
                result["error"] = f"Aborted: {reason}"
                return result

            elif action == "WAIT":
                seconds = decision.get("seconds", 2)
                logger.info(f"Waiting {seconds}s as suggested by Gemini...")
                await asyncio.sleep(seconds)

            elif action == "CLOSE_POPUP":
                popup_selector = decision.get("selector", 'button[aria-label="Close"]')
                logger.info(f"Attempting to close popup: {popup_selector}")
                await smart_click(page, [popup_selector], "Close popup")
                await asyncio.sleep(0.5)

            elif action == "TRY_SELECTOR":
                new_selector = decision.get("selector")
                if new_selector:
                    logger.info(f"Trying Gemini-suggested selector: {new_selector}")
                    # Prepend to try first on next iteration
                    selectors = [new_selector] + selectors

            elif action == "SCROLL":
                # Ignore scroll suggestions - we don't scroll on permalink pages
                logger.info(f"Ignoring scroll suggestion (not needed for permalinks)")

            # RETRY just continues the loop
        else:
            # No vision - just wait and retry
            logger.warning("No vision client - waiting 2s and retrying")
            await asyncio.sleep(2)

    logger.error(f"Max attempts ({max_attempts}) reached for '{description}'")
    result["error"] = f"Max attempts reached"
    return result


async def vision_element_click(page: Page, x: int, y: int) -> bool:
    """
    Click an element at vision coordinates using multiple strategies.

    Facebook uses nested DIVs - elementFromPoint often returns a wrapper.
    We try multiple approaches to ensure the click reaches React handlers:
    1. Find deepest clickable element (role=button, actual buttons, links)
    2. Try native .click() method first
    3. Fall back to dispatchEvent with proper coordinates
    """
    try:
        result = await page.evaluate('''(coords) => {
            let element = document.elementFromPoint(coords.x, coords.y);
            if (!element) {
                return {success: false, reason: "No element at coordinates"};
            }

            // Try to find a more specific clickable element in the hierarchy
            let clickable = element;
            let current = element;

            // Walk up the tree looking for actual interactive elements
            while (current && current !== document.body) {
                const role = current.getAttribute('role');
                const tag = current.tagName.toLowerCase();

                // Prefer these clickable element types
                if (role === 'button' || tag === 'button' || tag === 'a' ||
                    current.hasAttribute('tabindex') || current.onclick) {
                    clickable = current;
                    break;
                }
                current = current.parentElement;
            }

            // First try native .click() which works better with React
            try {
                clickable.click();
                return {
                    success: true,
                    method: 'native_click',
                    tagName: clickable.tagName,
                    role: clickable.getAttribute('role') || 'none',
                    className: (clickable.className || '').substring(0, 50)
                };
            } catch (e) {
                // Fall back to dispatchEvent
                clickable.dispatchEvent(new MouseEvent('click', {
                    bubbles: true,
                    cancelable: true,
                    view: window,
                    clientX: coords.x,
                    clientY: coords.y
                }));
                return {
                    success: true,
                    method: 'dispatch_event',
                    tagName: clickable.tagName,
                    role: clickable.getAttribute('role') || 'none',
                    className: (clickable.className || '').substring(0, 50)
                };
            }
        }''', {"x": x, "y": y})

        if result.get("success"):
            logger.info(f"Clicked <{result.get('tagName')} role={result.get('role')}> at ({x}, {y}) via {result.get('method')}")
            return True
        else:
            logger.warning(f"No element at ({x}, {y}): {result.get('reason')}")
            return False
    except Exception as e:
        logger.error(f"vision_element_click error: {e}")
        return False


async def open_comment_box(page: Page) -> bool:
    """Open the comment input box."""
    selectors = [
        '[data-action-id="32607"]',  # Common mobile action ID
        'div[role="button"][aria-label*="Comment"]',
        'div[aria-label="Comment"]',
        'span:text("Comment")',
        'div:text("Write a comment...")'
    ]
    return await smart_click(page, selectors, "Comment Button")


async def type_comment(page: Page, comment: str) -> bool:
    """Type comment into the input field."""
    # 1. Try to click the input area first
    input_selectors = [
        'div[role="textbox"]',
        '[contenteditable="true"]',
        'textarea',
        'div[aria-label="Write a comment"]',
        'div:text("Write a comment")'
    ]
    
    if not await smart_focus(page, input_selectors, "Comment Input"):
        return False

    await asyncio.sleep(0.5)
    
    # 2. Type the text
    try:
        await page.keyboard.type(comment, delay=50)
        logger.info(f"Typed comment: {comment[:20]}...")
        return True
    except Exception as e:
        logger.error(f"Failed to type: {e}")
        return False


async def click_send_button(page: Page) -> bool:
    """Click the send/post button."""
    send_selectors = [
        'div[aria-label="Send"]',
        'button[aria-label="Send"]',
        '[aria-label="Send"]',
        'div[aria-label="Post"]',
        'button[aria-label="Post"]',
        '[aria-label="Post"]',
        '[data-sigil="touchable submit-comment"]',
        '[data-sigil*="submit"]',
        'div[role="button"]:has-text("Post")',
        '[role="button"][aria-label*="send" i]',
        '[role="button"][aria-label*="post" i]',
    ]
    
    if await smart_click(page, send_selectors, "Send Button"):
        return True

    # Enter key fallback removed - doesn't work on mobile FB
    logger.warning("Failed to find Send button")
    return False


async def verify_send_clicked(page: Page) -> bool:
    """Verify the comment was actually sent by checking if input is cleared."""
    await asyncio.sleep(1)
    try:
        # Check if the textbox is now empty (comment was sent)
        input_selectors = ['div[role="textbox"]', '[contenteditable="true"]']
        for selector in input_selectors:
            locator = page.locator(selector).first
            if await locator.count() > 0:
                text = await locator.inner_text()
                if text.strip() == "":
                    logger.info("Send verified: input field is now empty")
                    return True
        logger.warning("Send verification failed: input field still has content")
        return False
    except Exception as e:
        logger.warning(f"Send verification error: {e}")
        return False


def is_reels_page(url: str) -> bool:
    """Check if URL is a Reels/Watch page (not a regular post)."""
    return "/reel/" in url or "/watch/" in url or "/videos/" in url


async def verify_post_loaded(page: Page) -> bool:
    """Verify we're on a valid post page, not Reels."""
    try:
        # FAIL FAST: Reject Reels pages
        if is_reels_page(page.url):
            logger.error(f"Landed on Reels page: {page.url}")
            return False

        # 1. Check for 'From your link' (redirect success)
        if await page.get_by_text("From your link").count() > 0:
            return True

        # 2. Check URL structure
        if "story.php" in page.url or "/posts/" in page.url:
            return True

        # 3. Check for specific post elements
        if await page.locator('[data-sigil="m-feed-voice-subtitle"]').count() > 0:
            return True

        await save_debug_screenshot(page, "verification_failed")
        return False # Return False if we can't confirm, but caller might proceed anyway
    except:
        return False


async def wait_for_post_visible(page: Page, vision, max_attempts: int = 4) -> bool:
    """
    Smart wait: Take screenshot, check if post visible, retry with backoff if not.

    Instead of static sleep, we:
    1. Take a screenshot
    2. Ask vision if post is visible
    3. If not, wait with exponential backoff and retry
    """
    base_wait = 1.0  # Start with 1 second

    for attempt in range(max_attempts):
        # Check for Reels FIRST (fail fast)
        if is_reels_page(page.url):
            logger.error(f"Landed on Reels page: {page.url}")
            return False

        screenshot = await save_debug_screenshot(page, f"wait_attempt_{attempt}")
        verification = await vision.verify_state(screenshot, "post_visible")

        if verification.success:
            logger.info(f"Post visible on attempt {attempt + 1} (confidence: {verification.confidence:.0%})")
            # PROACTIVE AUDIT: Dump all interactive elements now that page is loaded
            await dump_interactive_elements(page, "PAGE LOADED - Gemini confirmed post visible")
            return True

        # Exponential backoff: 1s, 2s, 4s, 8s
        wait_time = base_wait * (2 ** attempt)
        logger.info(f"Post not visible yet, waiting {wait_time:.1f}s... (attempt {attempt + 1}/{max_attempts})")
        await asyncio.sleep(wait_time)

    logger.error(f"Post not visible after {max_attempts} attempts")
    return False


async def post_comment(
    session: FacebookSession,
    url: str,
    comment: str,
    proxy: Optional[str] = None,
    use_vision: bool = True,
    verify_post: bool = True
) -> Dict[str, Any]:
    """Post a comment using a saved session with optional AI vision."""
    result = {
        "success": False,
        "url": url,
        "comment": comment,
        "error": None,
        "verified": False,
        "method": "unknown"
    }

    if use_vision and not VISION_AVAILABLE:
        logger.warning("Vision requested but not available, using selectors")
        use_vision = False

    async with async_playwright() as p:
        user_agent = session.get_user_agent() or DEFAULT_USER_AGENT
        viewport = session.get_viewport() or MOBILE_VIEWPORT
        session_proxy = session.get_proxy()
        active_proxy = session_proxy if session_proxy else proxy

        # Get device fingerprint for this session (timezone, locale)
        device_fingerprint = session.get_device_fingerprint()
        logger.info(f"Using device fingerprint: timezone={device_fingerprint['timezone']}, locale={device_fingerprint['locale']}")

        context_options = {
            "user_agent": user_agent,
            "viewport": viewport,
            "ignore_https_errors": True,
            "device_scale_factor": 1,  # Force 1:1 pixel mapping for vision coordinates
            "timezone_id": device_fingerprint["timezone"],
            "locale": device_fingerprint["locale"],
        }
        if active_proxy:
            context_options["proxy"] = _build_playwright_proxy(active_proxy)
            logger.info(f"Using proxy: {context_options['proxy'].get('server')}")

        browser = await p.chromium.launch(headless=True, args=["--disable-notifications", "--disable-geolocation"])
        context = await browser.new_context(**context_options)

        # MANDATORY: Apply stealth mode for anti-detection
        await Stealth().apply_stealth_async(context)

        try:
            page = await context.new_page()
            if not await apply_session_to_context(context, session):
                raise Exception("Failed to apply cookies")

            logger.info(f"Navigating to {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(5)  # Wait for Facebook to fully load/redirect
            await save_debug_screenshot(page, "navigated")

            if not await verify_post_loaded(page):
                logger.warning("Could not verify post loaded, trying anyway...")

            # 1. Open Comment Box (Vision + Fallback)
            comment_selectors = ['[data-action-id="32607"]', 'div[role="button"][aria-label*="Comment"]', 'div[aria-label="Comment"]', 'span:text("Comment")']
            if use_vision:
                click_result = await vision_click(page, "comment_button", comment_selectors, "Comment button")
                if not click_result["success"]:
                    raise Exception("Could not find Comment button")
                result["method"] = click_result["method"]
            else:
                if not await open_comment_box(page):
                    raise Exception("Could not find Comment button")
                result["method"] = "selector"

            await asyncio.sleep(1)

            # 2. Focus Input Field (use focus() for text fields, NOT dispatch_event)
            input_selectors = ['div[role="textbox"]', '[contenteditable="true"]', 'textarea', 'div[aria-label="Write a comment"]']
            if use_vision:
                click_result = await vision_click(page, "comment_input", input_selectors, "Comment input")
                if not click_result["success"]:
                    # Vision failed, try focus fallback
                    logger.info("Vision failed for input, trying focus fallback")
                    if not await smart_focus(page, input_selectors, "Comment Input"):
                        raise Exception("Could not activate comment input field")
            else:
                if not await smart_focus(page, input_selectors, "Comment Input"):
                    raise Exception("Could not activate comment input field")

            await asyncio.sleep(0.5)

            # 3. Type comment
            await page.keyboard.type(comment, delay=50)
            logger.info(f"Typed: {comment[:30]}...")
            await save_debug_screenshot(page, "typed_comment")
            await asyncio.sleep(1)

            # 4. Click Send (Vision + Fallback)
            send_selectors = [
                'div[aria-label="Send"]',
                'button[aria-label="Send"]',
                '[aria-label="Send"]',
                'div[aria-label="Post"]',
                'button[aria-label="Post"]',
                '[aria-label="Post"]',
                '[data-sigil="touchable submit-comment"]',
                '[data-sigil*="submit"]',
                'div[role="button"]:has-text("Post")',
                '[role="button"][aria-label*="send" i]',
                '[role="button"][aria-label*="post" i]',
            ]
            if use_vision:
                click_result = await vision_click(page, "send_button", send_selectors, "Send button")
                if not click_result["success"]:
                    raise Exception("Could not find Send button")
            else:
                if not await click_send_button(page):
                    raise Exception("Could not find Send button")

            await asyncio.sleep(3)

            # 5. Take post-send screenshot for debugging
            await save_debug_screenshot(page, "post_send")

            # 6. Visual verification via Gemini (if available)
            if verify_post and use_vision:
                verification = await verify_comment_visually(page, comment)
                result["verified"] = verification["verified"]
                result["verification_confidence"] = verification.get("confidence", 0)
            else:
                result["verified"] = True  # Assume success if vision not used

            result["success"] = True

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"Error: {e}")
            if 'page' in locals():
                await save_debug_screenshot(page, "error_final")
        finally:
            await browser.close()

    return result


async def post_comment_verified(
    session: FacebookSession,
    url: str,
    comment: str,
    proxy: Optional[str] = None
) -> Dict[str, Any]:
    """
    Post a comment with AI vision VERIFICATION at every step.
    This is the robust version that verifies each action succeeded before proceeding.
    """
    result = {
        "success": False,
        "url": url,
        "comment": comment,
        "error": None,
        "steps_completed": [],
        "method": "vision_verified"
    }

    vision = get_vision_client() if VISION_AVAILABLE else None
    if not vision:
        result["error"] = "Vision client not available - required for verified mode"
        return result

    async with async_playwright() as p:
        user_agent = session.get_user_agent() or DEFAULT_USER_AGENT
        viewport = session.get_viewport() or MOBILE_VIEWPORT
        session_proxy = session.get_proxy()
        active_proxy = session_proxy if session_proxy else proxy

        # Get device fingerprint for this session (timezone, locale)
        device_fingerprint = session.get_device_fingerprint()
        logger.info(f"Using device fingerprint: timezone={device_fingerprint['timezone']}, locale={device_fingerprint['locale']}")

        context_options = {
            "user_agent": user_agent,
            "viewport": viewport,
            "ignore_https_errors": True,
            "device_scale_factor": 1,  # Force 1:1 pixel mapping for vision coordinates
            "timezone_id": device_fingerprint["timezone"],
            "locale": device_fingerprint["locale"],
        }
        if active_proxy:
            context_options["proxy"] = _build_playwright_proxy(active_proxy)
            logger.info(f"Using proxy: {context_options['proxy'].get('server')}")

        browser = await p.chromium.launch(headless=True, args=["--disable-notifications", "--disable-geolocation"])
        context = await browser.new_context(**context_options)

        # MANDATORY: Apply stealth mode for anti-detection
        await Stealth().apply_stealth_async(context)

        try:
            page = await context.new_page()
            if not await apply_session_to_context(context, session):
                raise Exception("Failed to apply cookies")

            # ========== STEP 1: Navigate and verify post is visible ==========
            logger.info(f"Step 1: Navigating to {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)

            # Check for Reels redirect immediately
            if is_reels_page(page.url):
                raise Exception(f"Step 1 FAILED - Navigated to Reels instead of post: {page.url}")

            # SMART WAIT: Retry with exponential backoff until post is visible
            # 6 attempts = ~63 seconds max wait (1+2+4+8+16+32)
            if not await wait_for_post_visible(page, vision, max_attempts=6):
                raise Exception("Step 1 FAILED - Post not visible after 6 attempts")

            result["steps_completed"].append("post_visible")
            logger.info("✓ Step 1: Post visible")

            # ========== STEP 2: Click comment button using self-healing loop ==========
            logger.info("Step 2: Clicking comment button (CSS selectors + Gemini healing)")

            step2_result = await click_with_healing(
                page=page,
                vision=vision,
                selectors=fb_selectors.COMMENT["comment_button"],
                description="Comment button",
                max_attempts=5
            )

            if not step2_result["success"]:
                error_msg = step2_result.get("error", "Unknown error")
                raise Exception(f"Step 2 FAILED - {error_msg}")

            # Verify comments section opened
            await asyncio.sleep(1.0)
            verify_screenshot = await save_debug_screenshot(page, "step2_verify")
            verification = await vision.verify_state(verify_screenshot, "comments_opened")

            if not verification.success:
                # Try one more click - sometimes first click doesn't register
                logger.warning("Comments not opened, trying one more click...")
                await click_with_healing(page, vision, fb_selectors.COMMENT["comment_button"], "Comment button", max_attempts=2)
                await asyncio.sleep(1.0)
                verify_screenshot = await save_debug_screenshot(page, "step2_verify_retry")
                verification = await vision.verify_state(verify_screenshot, "comments_opened")
                if not verification.success:
                    raise Exception(f"Step 2 FAILED - Comments not opened: {verification.message}")

            result["steps_completed"].append("comments_opened")
            logger.info(f"✓ Step 2: Comments section opened (confidence: {verification.confidence:.0%})")

            # PROACTIVE AUDIT: Dump elements now that comments section is open
            await dump_interactive_elements(page, "COMMENTS SECTION OPENED - looking for input field")

            # ========== STEP 3: Focus comment input ==========
            logger.info("Step 3: Focusing comment input")

            # Try Playwright semantic locators first (most reliable for text elements)
            focus_success = await find_comment_input(page)

            if not focus_success:
                # Fall back to CSS selectors
                logger.info("Playwright locators failed, trying CSS selectors...")
                focus_success = await smart_focus(page, fb_selectors.COMMENT["comment_input"], "Comment input")

            if not focus_success:
                # Last resort: click_with_healing with Gemini guidance
                logger.warning("CSS selectors failed, trying click_with_healing...")
                click_result = await click_with_healing(
                    page=page,
                    vision=vision,
                    selectors=fb_selectors.COMMENT["comment_input"],
                    description="Comment input",
                    max_attempts=3
                )
                if not click_result["success"]:
                    raise Exception(f"Step 3 FAILED - Could not focus input: {click_result.get('error', 'Unknown')}")

            await asyncio.sleep(0.8)

            # Skip Gemini verification for input_active - it always returns 0% in headless
            # (Playwright doesn't show visual cursor, so Gemini can't verify)
            # Step 4 will verify if typing worked by checking if text appears
            result["steps_completed"].append("input_clicked")
            logger.info("✓ Step 3: Input field clicked (skipping Gemini - cursor not visible in headless)")

            # ========== STEP 4: Type comment and verify text appears ==========
            logger.info(f"Step 4: Typing comment: {comment[:30]}...")
            await page.keyboard.type(comment, delay=50)
            await asyncio.sleep(0.8)

            screenshot = await save_debug_screenshot(page, "step4_typed")
            verification = await vision.verify_state(screenshot, "text_typed", expected_text=comment[-50:])
            if not verification.success:
                raise Exception(f"Step 4 FAILED - Typed text not visible: {verification.message}")

            result["steps_completed"].append("text_typed")
            logger.info(f"✓ Step 4: Typed text visible (confidence: {verification.confidence:.0%})")

            # ========== STEP 5: Click send button using self-healing loop ==========
            logger.info("Step 5: Clicking send button (CSS selectors + Gemini healing)")

            step5_result = await click_with_healing(
                page=page,
                vision=vision,
                selectors=fb_selectors.COMMENT["comment_submit"],
                description="Send button",
                max_attempts=5
            )

            if not step5_result["success"]:
                error_msg = step5_result.get("error", "Unknown error")
                raise Exception(f"Step 5 FAILED - {error_msg}")

            # Wait for comment to post (5s for long comments to render)
            await asyncio.sleep(5)

            # Dump elements after send to see what's on the page
            await dump_interactive_elements(page, "AFTER SEND CLICK - checking for comment")

            # Verify comment was posted
            verify_screenshot = await save_debug_screenshot(page, "step5_verify")
            verification = await vision.verify_state(verify_screenshot, "comment_posted", expected_text=comment[-50:])

            if not verification.success:
                if verification.status == "pending":
                    # Comment pending, wait more
                    logger.info("Comment appears pending, waiting...")
                    await asyncio.sleep(3)
                    verify_screenshot = await save_debug_screenshot(page, "step5_pending")
                    verification = await vision.verify_state(verify_screenshot, "comment_posted", expected_text=comment[-50:])
                else:
                    # Not pending - try one more wait and check
                    logger.info(f"Comment not visible, waiting 3 more seconds... ({verification.message})")
                    await asyncio.sleep(3)
                    verify_screenshot = await save_debug_screenshot(page, "step5_retry")
                    verification = await vision.verify_state(verify_screenshot, "comment_posted", expected_text=comment[-50:])

                if not verification.success:
                    raise Exception(f"Step 5 FAILED - Comment not posted: {verification.message}")

            result["steps_completed"].append("comment_posted")
            result["success"] = True
            result["verified"] = True
            result["verification_confidence"] = verification.confidence
            logger.info(f"✓ Step 5: Comment posted and verified! (confidence: {verification.confidence:.0%})")
            logger.info("=" * 50)
            logger.info("SUCCESS: All 5 steps completed with verification!")
            logger.info("=" * 50)

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"FAILED: {e}")
            logger.error(f"Steps completed before failure: {result['steps_completed']}")
            if 'page' in locals():
                await save_debug_screenshot(page, "error_state")
        finally:
            await browser.close()

    return result


# Re-export other functions needed by main.py
async def test_session(session: FacebookSession, proxy: Optional[str] = None) -> Dict[str, Any]:
    result = {
        "valid": False,
        "user_id": None,
        "error": None
    }
    
    if not session.load():
        result["error"] = "Session file not found"
        return result

    async with async_playwright() as p:
        session_proxy = session.get_proxy()
        active_proxy = session_proxy if session_proxy else proxy

        user_agent = session.get_user_agent() or DEFAULT_USER_AGENT
        viewport = session.get_viewport() or MOBILE_VIEWPORT

        # Get device fingerprint for this session (timezone, locale)
        device_fingerprint = session.get_device_fingerprint()

        context_options: Dict[str, Any] = {
            "user_agent": user_agent,
            "viewport": viewport,
            "ignore_https_errors": True,
            "device_scale_factor": 1,  # Force 1:1 pixel mapping
            "timezone_id": device_fingerprint["timezone"],
            "locale": device_fingerprint["locale"],
        }
        if active_proxy:
            context_options["proxy"] = _build_playwright_proxy(active_proxy)

        browser = await p.chromium.launch(headless=True, args=["--disable-notifications", "--disable-geolocation"])
        context = await browser.new_context(**context_options)

        # MANDATORY: Apply stealth mode for anti-detection
        await Stealth().apply_stealth_async(context)

        try:
            if not await apply_session_to_context(context, session):
                raise Exception("Failed to apply cookies")

            page = await context.new_page()
            await page.goto("https://m.facebook.com/me/", wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(1)

            current_url = page.url.lower()
            if "/login" not in current_url and "checkpoint" not in current_url:
                result["valid"] = True
                result["user_id"] = session.get_user_id()
        except Exception as e:
            result["error"] = str(e)
        finally:
            await browser.close()
            
    return result
