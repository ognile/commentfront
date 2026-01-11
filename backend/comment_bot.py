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


async def save_debug_screenshot(page: Page, name: str):
    """Save a screenshot for debugging."""
    try:
        # Save specific step
        path = os.path.join(DEBUG_DIR, f"{name}.png")
        await page.screenshot(path=path)
        
        # Save latest for live view
        latest_path = os.path.join(DEBUG_DIR, "latest.png")
        await page.screenshot(path=latest_path)
        
        logger.info(f"Saved debug screenshot: {path}")
    except Exception as e:
        logger.warning(f"Failed to save screenshot: {e}")


async def smart_click(page: Page, selectors: List[str], description: str) -> bool:
    """
    Try to click an element using multiple selectors.
    Scrolls into view and waits for visibility.
    """
    for selector in selectors:
        try:
            # Check count first to avoid waiting unnecessarily
            locator = page.locator(selector).first
            if await locator.count() > 0:
                # Scroll into view
                await locator.scroll_into_view_if_needed()
                await asyncio.sleep(0.5)
                
                # Snapshot before action for live view
                await save_debug_screenshot(page, f"pre_click_{description.replace(' ', '_')}")
                
                if await locator.is_visible():
                    await locator.click()
                    logger.info(f"Clicked '{description}' using selector: {selector}")
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
            await text_locator.click()
            logger.info(f"Clicked '{description}' using text match")
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
        'div[aria-label="Post"]',
        '[data-sigil="touchable submit-comment"]',
        'div[role="button"]:has-text("Post")'
    ]
    
    if await smart_click(page, send_selectors, "Send Button"):
        return True
        
    # Fallback: Enter key
    try:
        await page.keyboard.press("Enter")
        logger.info("Pressed Enter fallback")
        return True
    except:
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
    proxy: Optional[str] = None
) -> Dict[str, Any]:
    """
    Post a comment using a saved session.
    """
    result = {
        "success": False,
        "url": url,
        "comment": comment,
        "error": None
    }
    
    async with async_playwright() as p:
        user_agent = session.get_user_agent() or DEFAULT_USER_AGENT
        viewport = session.get_viewport() or MOBILE_VIEWPORT
        
        # PRIORITIZE SESSION PROXY
        # If session has a proxy saved, use it. Otherwise fall back to global arg.
        session_proxy = session.get_proxy()
        active_proxy = session_proxy if session_proxy else proxy
        
        context_options = {
            "user_agent": user_agent,
            "viewport": viewport,
            "ignore_https_errors": True
        }
        
        if active_proxy:
            proxy_cfg = _build_playwright_proxy(active_proxy)
            context_options["proxy"] = proxy_cfg
            logger.info(f"Using proxy server: {proxy_cfg.get('server')}")
        
        # Launch options
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-notifications", "--disable-geolocation"]
        )
        
        context = await browser.new_context(**context_options)
        
        if Stealth:
            stealth = Stealth()
            await stealth.apply_stealth_async(context)
        
        try:
            page = await context.new_page()
            
            # Apply cookies
            if not await apply_session_to_context(context, session):
                raise Exception("Failed to apply cookies")
            
            # Navigate
            logger.info(f"Navigating to {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await save_debug_screenshot(page, "navigated")
            await asyncio.sleep(3)
            
            # 1. Verify
            if not await verify_post_loaded(page):
                logger.warning("Could not verify post loaded, but trying anyway...")
            
            # 2. Open Comment Box
            if not await open_comment_box(page):
                raise Exception("Could not find 'Comment' button")
            
            await asyncio.sleep(1)
            
            # 3. Type
            if not await type_comment(page, comment):
                raise Exception("Could not type in comment box")
            
            await save_debug_screenshot(page, "typed_comment")
            await asyncio.sleep(1)
            
            # 4. Send
            if not await click_send_button(page):
                raise Exception("Could not find 'Send' button")
            
            await asyncio.sleep(3)
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
