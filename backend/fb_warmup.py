"""
Facebook Profile Warming Module
Simulates natural user behavior to warm up profiles before automation tasks
"""

import asyncio
import random
import logging
from playwright.async_api import Page
from typing import Optional
from fb_selectors import FEED, REELS, NOTIFICATIONS, NAV

logger = logging.getLogger("FBWarmup")


def random_delay(min_sec: float = 0.5, max_sec: float = 2.0) -> float:
    """Generate human-like random delay"""
    return random.uniform(min_sec, max_sec)


async def scroll_feed(page: Page, duration_sec: int = 30) -> int:
    """
    Scroll through the news feed naturally.
    Returns number of scrolls performed.
    """
    logger.info("Starting feed scroll...")
    scrolls = 0
    start_time = asyncio.get_event_loop().time()

    while asyncio.get_event_loop().time() - start_time < duration_sec:
        # Random scroll distance (like human thumb scroll)
        scroll_distance = random.randint(200, 500)

        await page.evaluate(f'window.scrollBy(0, {scroll_distance})')
        scrolls += 1

        # Random pause to "read" content
        await asyncio.sleep(random_delay(1.0, 3.0))

        # Occasionally scroll back up a bit (like re-reading)
        if random.random() < 0.15:
            await page.evaluate(f'window.scrollBy(0, -{random.randint(50, 150)})')
            await asyncio.sleep(random_delay(0.5, 1.5))

    logger.info(f"Feed scroll complete: {scrolls} scrolls")
    return scrolls


async def watch_reels(page: Page, num_reels: int = 3) -> int:
    """
    Watch short video reels.
    Returns number of reels watched.
    """
    logger.info(f"Starting reels watching ({num_reels} reels)...")
    watched = 0

    try:
        # Navigate to reels
        await page.goto("https://m.facebook.com/reels/", wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(2)

        for i in range(num_reels):
            # Watch for random duration (5-15 seconds per reel)
            watch_time = random.uniform(5, 15)
            logger.info(f"Watching reel {i+1} for {watch_time:.1f}s...")
            await asyncio.sleep(watch_time)
            watched += 1

            if i < num_reels - 1:
                # Swipe to next reel (scroll down)
                await page.evaluate('window.scrollBy(0, window.innerHeight)')
                await asyncio.sleep(random_delay(0.5, 1.5))

    except Exception as e:
        logger.warning(f"Reels watching error: {e}")

    logger.info(f"Reels complete: watched {watched}")
    return watched


async def like_random_posts(page: Page, max_likes: int = 2) -> int:
    """
    Like random posts in the feed.
    Returns number of likes performed.
    """
    logger.info(f"Starting random likes (max {max_likes})...")
    likes = 0

    try:
        # Go to home feed
        await page.goto("https://m.facebook.com/", wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(2)

        # Scroll down a bit to load posts
        for _ in range(3):
            await page.evaluate(f'window.scrollBy(0, {random.randint(300, 500)})')
            await asyncio.sleep(random_delay(1, 2))

        # Find like buttons
        like_buttons = await page.query_selector_all('div[aria-label="Like"], span:has-text("Like")')

        if like_buttons:
            # Randomly select some to like
            to_like = random.sample(like_buttons, min(max_likes, len(like_buttons)))

            for btn in to_like:
                try:
                    # Scroll button into view
                    await btn.scroll_into_view_if_needed()
                    await asyncio.sleep(random_delay(0.5, 1))

                    await btn.click()
                    likes += 1
                    logger.info(f"Liked post {likes}")

                    # Pause after liking
                    await asyncio.sleep(random_delay(2, 4))
                except:
                    continue

    except Exception as e:
        logger.warning(f"Like error: {e}")

    logger.info(f"Likes complete: {likes} posts liked")
    return likes


async def check_notifications(page: Page) -> int:
    """
    Check notifications tab.
    Returns number of notifications viewed.
    """
    logger.info("Checking notifications...")
    viewed = 0

    try:
        # Navigate to notifications
        await page.goto("https://m.facebook.com/notifications/", wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(2)

        # Scroll through notifications
        for _ in range(random.randint(2, 5)):
            await page.evaluate(f'window.scrollBy(0, {random.randint(200, 400)})')
            viewed += 1
            await asyncio.sleep(random_delay(1, 2))

    except Exception as e:
        logger.warning(f"Notifications error: {e}")

    logger.info(f"Notifications check complete: {viewed} scrolls")
    return viewed


async def view_random_profile(page: Page) -> bool:
    """
    Click on a random profile from the feed.
    """
    logger.info("Viewing random profile...")

    try:
        # Go to feed first
        await page.goto("https://m.facebook.com/", wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(2)

        # Scroll to load content
        await page.evaluate('window.scrollBy(0, 400)')
        await asyncio.sleep(1)

        # Find profile links (avatars, names)
        profile_links = await page.query_selector_all('a[href*="/profile.php"], a[href*="facebook.com/"][href*="?"]')

        if profile_links and len(profile_links) > 2:
            # Pick a random profile (skip first few which might be own profile)
            link = random.choice(profile_links[2:min(10, len(profile_links))])

            await link.click()
            await asyncio.sleep(random_delay(3, 6))

            # Scroll their profile a bit
            for _ in range(2):
                await page.evaluate(f'window.scrollBy(0, {random.randint(200, 400)})')
                await asyncio.sleep(random_delay(1, 2))

            logger.info("Profile view complete")
            return True

    except Exception as e:
        logger.warning(f"Profile view error: {e}")

    return False


async def warm_profile(
    page: Page,
    duration_minutes: float = 2.5,
    activities: list = None
) -> dict:
    """
    Run warming behaviors for specified duration.
    Randomly picks activities to simulate real user.

    Args:
        page: Playwright page instance
        duration_minutes: How long to warm (default 2.5 min)
        activities: List of activity names to run. Default: all activities

    Returns:
        dict with stats of what was done
    """
    if activities is None:
        activities = ['scroll_feed', 'watch_reels', 'like_posts', 'check_notifications']

    stats = {
        'duration_minutes': duration_minutes,
        'scrolls': 0,
        'reels_watched': 0,
        'likes': 0,
        'notifications_checked': 0,
        'profiles_viewed': 0,
    }

    duration_sec = duration_minutes * 60
    start_time = asyncio.get_event_loop().time()

    logger.info(f"Starting warm-up for {duration_minutes} minutes...")
    logger.info(f"Activities: {activities}")

    # Shuffle activities for randomness
    activity_queue = activities.copy()
    random.shuffle(activity_queue)

    activity_index = 0

    while asyncio.get_event_loop().time() - start_time < duration_sec:
        # Get next activity (cycle through)
        activity = activity_queue[activity_index % len(activity_queue)]
        activity_index += 1

        remaining_time = duration_sec - (asyncio.get_event_loop().time() - start_time)

        if remaining_time < 10:
            break  # Not enough time for another activity

        try:
            if activity == 'scroll_feed':
                scroll_time = min(30, remaining_time / 2)
                stats['scrolls'] += await scroll_feed(page, int(scroll_time))

            elif activity == 'watch_reels':
                num_reels = random.randint(2, 4)
                stats['reels_watched'] += await watch_reels(page, num_reels)

            elif activity == 'like_posts':
                stats['likes'] += await like_random_posts(page, max_likes=random.randint(1, 2))

            elif activity == 'check_notifications':
                stats['notifications_checked'] += await check_notifications(page)

            elif activity == 'view_profile':
                if await view_random_profile(page):
                    stats['profiles_viewed'] += 1

        except Exception as e:
            logger.warning(f"Activity {activity} failed: {e}")

        # Brief pause between activities
        await asyncio.sleep(random_delay(1, 3))

    elapsed = asyncio.get_event_loop().time() - start_time
    logger.info(f"Warm-up complete in {elapsed:.1f}s")
    logger.info(f"Stats: {stats}")

    return stats


async def batch_warm(
    adspower_client,
    profile_ids: list,
    duration_minutes: float = 2.5,
    parallel: bool = True
) -> list:
    """
    Warm multiple profiles.

    Args:
        adspower_client: AdsPowerClient instance
        profile_ids: List of profile IDs to warm
        duration_minutes: Duration per profile
        parallel: Run warming in parallel

    Returns:
        List of results
    """
    from playwright.async_api import async_playwright

    results = []

    async def warm_single(profile_id):
        try:
            # Start profile
            browser_info = adspower_client.start_profile(profile_id)
            ws_endpoint = browser_info['ws_endpoint']

            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(ws_endpoint)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = context.pages[0] if context.pages else await context.new_page()

                stats = await warm_profile(page, duration_minutes)

                return {
                    'profile_id': profile_id,
                    'success': True,
                    'stats': stats
                }
        except Exception as e:
            return {
                'profile_id': profile_id,
                'success': False,
                'error': str(e)
            }

    if parallel:
        tasks = [warm_single(pid) for pid in profile_ids]
        results = await asyncio.gather(*tasks)
    else:
        for profile_id in profile_ids:
            result = await warm_single(profile_id)
            results.append(result)

    return results
