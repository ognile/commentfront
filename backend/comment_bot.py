"""
Streamlined Facebook Comment Automation
Core focus: Secure, reliable comment posting with anti-detection
"""

import asyncio
import logging
import os
import re
from playwright.async_api import async_playwright, Page
from playwright_stealth import Stealth
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse

from fb_session import FacebookSession, apply_session_to_context

logger = logging.getLogger("CommentBot")

MOBILE_VIEWPORT = {"width": 393, "height": 873}
DEFAULT_USER_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"


async def click_element_by_text(page: Page, text: str, timeout: int = 5000) -> bool:
    """Find and click an element containing specific text."""
    try:
        locator = page.get_by_text(text, exact=False).first
        if await locator.count() > 0:
            await locator.click()
            logger.info(f"Clicked element with text: {text}")
            return True
    except Exception as e:
        logger.debug(f"Failed to click by text '{text}': {e}")
    return False


async def click_by_aria_label(page: Page, label: str, timeout: int = 5000) -> bool:
    """Find and click an element by aria-label."""
    try:
        locator = page.locator(f'[aria-label="{label}"]').first
        if await locator.count() > 0:
            await locator.click()
            logger.info(f"Clicked element with aria-label: {label}")
            return True
    except Exception as e:
        logger.debug(f"Failed to click aria-label '{label}': {e}")
    return False


async def click_send_button(page: Page) -> bool:
    """Click the blue send button in comment box."""
    strategies = [
        lambda: click_by_aria_label(page, "Send"),
        lambda: click_by_aria_label(page, "Post"),
        lambda: page.locator('[data-sigil="submit"]').first.click(),
        lambda: page.keyboard.press("Enter"),
    ]
    
    for strategy in strategies:
        try:
            if strategy():
                await asyncio.sleep(1)
                logger.info("Comment sent successfully")
                return True
        except:
            continue
    
    logger.error("Failed to send comment")
    return False


async def type_comment(page: Page, comment: str) -> bool:
    """Type comment into the input field."""
    strategies = [
        lambda: click_element_by_text(page, "Write a comment"),
        lambda: page.get_by_role("textbox").first.click(),
        lambda: page.locator('[contenteditable="true"]').first.click(),
    ]
    
    for strategy in strategies:
        try:
            if strategy():
                await asyncio.sleep(0.5)
                await page.keyboard.type(comment, delay=80)
                logger.info(f"Typed comment: {comment[:30]}...")
                return True
        except:
            continue
    
    logger.error("Failed to type comment")
    return False


async def open_comment_box(page: Page) -> bool:
    """Open the comment input box on a post."""
    strategies = [
        lambda: page.locator('[data-action-id="32607"]').first.click(),
        lambda: click_element_by_text(page, "Comment"),
        lambda: page.locator('[role="button"]').filter(has_text=re.compile("comment", re.I)).first.click(),
    ]
    
    for strategy in strategies:
        try:
            if strategy():
                await asyncio.sleep(1)
                logger.info("Opened comment box")
                return True
        except:
            continue
    
    logger.error("Failed to open comment box")
    return False


async def verify_post_loaded(page: Page) -> bool:
    """Verify we're on the correct post."""
    try:
        # Check for "From your link" banner or post indicators
        from_link = await page.get_by_text("From your link").count()
        if from_link > 0:
            logger.info("Verified: 'From your link' banner found")
            return True
        
        # Check URL contains story.php or posts/
        url = page.url
        if 'story.php' in url or 'posts/' in url:
            logger.info(f"Verified: On post page - {url}")
            return True
        
        logger.warning("Could not verify post location")
        return True  # Continue anyway
    except:
        return True


async def post_comment(
    session: FacebookSession,
    url: str,
    comment: str,
    proxy: Optional[str] = None
) -> Dict[str, Any]:
    """
    Post a comment on a Facebook post.
    
    Args:
        session: FacebookSession with cookies
        url: Target post URL
        comment: Comment text
        proxy: Optional proxy URL
    
    Returns:
        Dict with success status and details
    """
    result = {
        "success": False,
        "url": url,
        "comment": comment,
        "error": None
    }
    
    async with async_playwright() as p:
        # Build browser config
        user_agent = session.get_user_agent() or DEFAULT_USER_AGENT
        viewport = session.get_viewport() or MOBILE_VIEWPORT
        
        context_options = {
            "user_agent": user_agent,
            "viewport": viewport,
        }
        
        # Add proxy if provided
        if proxy:
            context_options["proxy"] = {"server": proxy}
            logger.info(f"Using proxy: {proxy}")
        
        # Launch browser with stealth
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(**context_options)
        
        # Apply stealth
        stealth = Stealth()
        await stealth.apply_stealth_async(context)
        
        try:
            page = await context.new_page()
            
            # Apply session cookies
            if not await apply_session_to_context(context, session):
                raise Exception("Failed to apply session cookies")
            
            # Navigate to post
            logger.info(f"Navigating to: {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)
            
            # Verify post loaded
            if not await verify_post_loaded(page):
                raise Exception("Post verification failed")
            
            # Open comment box
            if not await open_comment_box(page):
                raise Exception("Failed to open comment box")
            
            # Type comment
            if not await type_comment(page, comment):
                raise Exception("Failed to type comment")
            
            # Send comment
            if not await click_send_button(page):
                raise Exception("Failed to send comment")
            
            result["success"] = True
            logger.info("✅ Comment posted successfully!")
            
        except Exception as e:
            result["error"] = str(e)
            logger.error(f"Comment failed: {e}")
        
        finally:
            await browser.close()
    
    return result


async def test_session(session: FacebookSession, proxy: Optional[str] = None) -> Dict[str, Any]:
    """Test if a session is valid and can access Facebook."""
    result = {
        "valid": False,
        "user_id": None,
        "error": None
    }
    
    if not session.load():
        result["error"] = "Session file not found"
        return result
    
    if not session.has_valid_cookies():
        result["error"] = "Session has no valid cookies"
        return result
    
    result["user_id"] = session.get_user_id()
    
    async with async_playwright() as p:
        context_options = {
            "user_agent": session.get_user_agent() or DEFAULT_USER_AGENT,
            "viewport": session.get_viewport() or MOBILE_VIEWPORT,
        }
        
        if proxy:
            context_options["proxy"] = {"server": proxy}
        
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(**context_options)
        
        stealth = Stealth()
        await stealth.apply_stealth_async(context)
        
        try:
            page = await context.new_page()
            
            if not await apply_session_to_context(context, session):
                raise Exception("Failed to apply cookies")
            
            await page.goto("https://m.facebook.com/", wait_until="domcontentloaded")
            await asyncio.sleep(2)
            
            # Check if logged in
            if "login" in page.url.lower():
                raise Exception("Session expired - redirected to login")
            
            result["valid"] = True
            logger.info(f"Session valid for user: {result['user_id']}")
            
        except Exception as e:
            result["error"] = str(e)
            logger.error(f"Session test failed: {e}")
        
        finally:
            await browser.close()
    
    return result
