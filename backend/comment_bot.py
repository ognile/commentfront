import asyncio
import logging
import os
import re
from playwright.async_api import async_playwright, Page
# try-except import to prevent crash if stealth is missing during dev
try:
    from playwright_stealth import Stealth
except ImportError:
    Stealth = None

from typing import Optional, Dict, Any, List
from urllib.parse import urlparse, unquote

from fb_session import FacebookSession, apply_session_to_context

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
    """Save a screenshot for debugging. Returns the path."""
    try:
        path = os.path.join(DEBUG_DIR, f"{name}.png")
        await page.screenshot(path=path)
        latest_path = os.path.join(DEBUG_DIR, "latest.png")
        await page.screenshot(path=latest_path)
        logger.info(f"Saved debug screenshot: {path}")
        return path
    except Exception as e:
        logger.warning(f"Failed to save screenshot: {e}")
        return ""


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
                    logger.info(f"Vision low confidence ({location.confidence:.0%}), scrolling...")
                    await page.evaluate("window.scrollBy(0, 200)")
                    await asyncio.sleep(0.5)
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
    Scrolls into view and waits for visibility.
    """
    logger.info(f"smart_click: Looking for '{description}' with {len(selectors)} selectors")
    for selector in selectors:
        try:
            # Check count first to avoid waiting unnecessarily
            locator = page.locator(selector).first
            count = await locator.count()
            logger.info(f"  Selector '{selector}' → found {count} element(s)")
            if count > 0:
                # Scroll into view
                await locator.scroll_into_view_if_needed()
                await asyncio.sleep(0.5)
                
                # Snapshot before action for live view
                await save_debug_screenshot(page, f"pre_click_{description.replace(' ', '_')}")
                
                if await locator.is_visible():
                    await locator.dispatch_event('click')
                    logger.info(f"Clicked '{description}' using dispatch_event: {selector}")
                    # Snapshot after action
                    await save_debug_screenshot(page, f"post_click_{description.replace(' ', '_')}")
                    return True
        except Exception as e:
            continue
            
    # Fallback: Text search
    try:
        text_locator = page.get_by_text(description, exact=False).first
        if await text_locator.count() > 0 and await text_locator.is_visible():
            await text_locator.scroll_into_view_if_needed()
            await text_locator.dispatch_event('click')
            logger.info(f"Clicked '{description}' using text match dispatch_event")
            return True
    except:
        pass

    logger.warning(f"Failed to find/click: {description}")
    await save_debug_screenshot(page, f"failed_click_{description.replace(' ', '_')}")
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
    
    if not await smart_click(page, input_selectors, "Comment Input"):
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


async def verify_post_loaded(page: Page) -> bool:
    """Verify we're on a valid post page."""
    try:
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

        context_options = {"user_agent": user_agent, "viewport": viewport, "ignore_https_errors": True}
        if active_proxy:
            context_options["proxy"] = _build_playwright_proxy(active_proxy)
            logger.info(f"Using proxy: {context_options['proxy'].get('server')}")

        browser = await p.chromium.launch(headless=True, args=["--disable-notifications", "--disable-geolocation"])
        context = await browser.new_context(**context_options)

        if Stealth:
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

            # 2. Click Input (Vision + Fallback)
            input_selectors = ['div[role="textbox"]', '[contenteditable="true"]', 'textarea', 'div[aria-label="Write a comment"]']
            if use_vision:
                await vision_click(page, "comment_input", input_selectors, "Comment input")
            else:
                await smart_click(page, input_selectors, "Comment Input")

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

        context_options: Dict[str, Any] = {
            "user_agent": user_agent,
            "viewport": viewport,
            "ignore_https_errors": True,
        }
        if active_proxy:
            context_options["proxy"] = _build_playwright_proxy(active_proxy)

        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(**context_options)

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
