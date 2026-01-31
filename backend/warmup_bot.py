"""
Warm-up Bot Module
Performs human-like activity before commenting to reduce throttling.

Warm-up actions:
1. Navigate to Facebook home feed
2. Scroll through feed (3-7 times with random delays)
3. Like 2-5 random posts
4. Final scroll before returning to normal flow
"""

import asyncio
import logging
import random
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

from playwright.async_api import Page, Browser, BrowserContext

from fb_selectors import FEED

logger = logging.getLogger("WarmupBot")


@dataclass
class WarmupResult:
    """Result of warm-up activity."""
    success: bool
    scroll_count: int = 0
    likes_count: int = 0
    profiles_visited: int = 0
    duration_seconds: float = 0.0
    error: Optional[str] = None
    actions: List[str] = None

    def __post_init__(self):
        if self.actions is None:
            self.actions = []


async def human_delay(min_seconds: float = 1.0, max_seconds: float = 3.0):
    """Add a human-like random delay."""
    delay = random.uniform(min_seconds, max_seconds)
    await asyncio.sleep(delay)


async def scroll_feed(page: Page, direction: str = "down", amount: int = 300) -> bool:
    """
    Scroll the feed in a human-like way.

    Args:
        page: Playwright page object
        direction: "down" or "up"
        amount: Pixels to scroll (with random variation)

    Returns:
        True if scroll was successful
    """
    try:
        # Add variation to scroll amount
        scroll_amount = amount + random.randint(-50, 100)
        if direction == "up":
            scroll_amount = -scroll_amount

        # Use mouse wheel for more natural scrolling
        await page.mouse.wheel(0, scroll_amount)
        logger.info(f"Scrolled {direction} by ~{abs(scroll_amount)}px")
        return True

    except Exception as e:
        logger.error(f"Scroll failed: {e}")
        return False


async def find_like_buttons(page: Page, max_count: int = 10) -> List[Any]:
    """
    Find visible like buttons on the current page.

    Returns:
        List of like button element handles
    """
    like_buttons = []

    for selector in FEED["like_button"]:
        try:
            elements = await page.query_selector_all(selector)
            for el in elements:
                # Check if element is visible
                is_visible = await el.is_visible()
                if is_visible:
                    like_buttons.append(el)
                    if len(like_buttons) >= max_count:
                        return like_buttons
        except Exception as e:
            logger.debug(f"Error finding like buttons with {selector}: {e}")

    return like_buttons


async def like_random_post(page: Page) -> bool:
    """
    Like a random post on the feed.

    Returns:
        True if like was successful
    """
    try:
        like_buttons = await find_like_buttons(page, max_count=20)

        if not like_buttons:
            logger.warning("No like buttons found on page")
            return False

        # Pick a random like button
        button = random.choice(like_buttons)

        # Click the like button
        await button.click()
        logger.info("Clicked like button on a post")

        # Small delay after liking
        await human_delay(0.5, 1.5)
        return True

    except Exception as e:
        logger.error(f"Failed to like post: {e}")
        return False


async def navigate_to_feed(page: Page) -> bool:
    """
    Navigate to Facebook home feed.

    Returns:
        True if navigation successful
    """
    try:
        current_url = page.url

        # If already on feed, just scroll to refresh
        if "facebook.com" in current_url and ("/home" in current_url or current_url.endswith("facebook.com/")):
            logger.info("Already on feed, refreshing...")
            await page.reload()
            await page.wait_for_load_state("networkidle", timeout=15000)
            return True

        # Navigate to home feed
        await page.goto("https://m.facebook.com/", timeout=30000)
        await page.wait_for_load_state("networkidle", timeout=15000)

        # Wait for feed to appear
        feed_found = False
        for selector in FEED["feed_container"]:
            try:
                await page.wait_for_selector(selector, timeout=5000)
                feed_found = True
                break
            except Exception:
                continue

        if not feed_found:
            logger.warning("Feed container not found after navigation")

        logger.info("Navigated to Facebook home feed")
        return True

    except Exception as e:
        logger.error(f"Failed to navigate to feed: {e}")
        return False


async def perform_warmup(
    page: Page,
    min_scrolls: int = 3,
    max_scrolls: int = 7,
    min_likes: int = 2,
    max_likes: int = 5,
    skip_navigation: bool = False
) -> WarmupResult:
    """
    Perform warm-up activity to make profile look more human.

    Args:
        page: Playwright page object (already logged in)
        min_scrolls: Minimum number of scroll actions
        max_scrolls: Maximum number of scroll actions
        min_likes: Minimum number of likes
        max_likes: Maximum number of likes
        skip_navigation: If True, skip navigating to feed (already there)

    Returns:
        WarmupResult with stats about what was done
    """
    import time
    start_time = time.time()

    result = WarmupResult(
        success=False,
        actions=[]
    )

    try:
        # Step 1: Navigate to feed (unless skipped)
        if not skip_navigation:
            logger.info("Warm-up: Navigating to feed...")
            nav_success = await navigate_to_feed(page)
            if not nav_success:
                result.error = "Failed to navigate to feed"
                return result
            result.actions.append("navigated_to_feed")
            await human_delay(2.0, 4.0)

        # Determine random counts for this session
        scroll_target = random.randint(min_scrolls, max_scrolls)
        like_target = random.randint(min_likes, max_likes)

        logger.info(f"Warm-up plan: {scroll_target} scrolls, {like_target} likes")

        # Step 2: Scroll and like
        likes_done = 0
        scrolls_done = 0

        for i in range(scroll_target):
            # Scroll down
            scroll_success = await scroll_feed(page, "down", random.randint(200, 500))
            if scroll_success:
                scrolls_done += 1
                result.actions.append(f"scroll_{i+1}")

            # Wait for content to load
            await human_delay(1.5, 3.5)

            # Maybe like a post (random chance, more likely at beginning)
            if likes_done < like_target:
                like_probability = 0.7 if likes_done < like_target // 2 else 0.4
                if random.random() < like_probability:
                    like_success = await like_random_post(page)
                    if like_success:
                        likes_done += 1
                        result.actions.append(f"liked_post_{likes_done}")

            # Random short pause
            await human_delay(0.5, 2.0)

        # Step 3: Final scroll - longer scroll session before exiting
        logger.info("Warm-up: Final scroll session...")
        final_scroll_count = random.randint(2, 4)
        for _ in range(final_scroll_count):
            await scroll_feed(page, "down", random.randint(300, 600))
            await human_delay(2.0, 4.0)
            scrolls_done += 1

        result.actions.append("final_scroll_session")

        # Calculate duration
        end_time = time.time()
        result.duration_seconds = end_time - start_time
        result.scroll_count = scrolls_done
        result.likes_count = likes_done
        result.success = True

        logger.info(f"Warm-up complete: {scrolls_done} scrolls, {likes_done} likes in {result.duration_seconds:.1f}s")
        return result

    except Exception as e:
        logger.error(f"Warm-up failed: {e}")
        result.error = str(e)
        result.duration_seconds = time.time() - start_time
        return result


async def perform_quick_warmup(page: Page) -> WarmupResult:
    """
    Perform a quick warm-up (fewer actions, faster).
    Good for when time is limited.
    """
    return await perform_warmup(
        page,
        min_scrolls=2,
        max_scrolls=4,
        min_likes=1,
        max_likes=3
    )


async def perform_extended_warmup(page: Page) -> WarmupResult:
    """
    Perform an extended warm-up (more actions, more human-like).
    Good for fresh profiles or after a long break.
    """
    return await perform_warmup(
        page,
        min_scrolls=5,
        max_scrolls=10,
        min_likes=3,
        max_likes=7
    )
