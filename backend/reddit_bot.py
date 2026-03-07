"""
Reddit mobile-web executor.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote

from playwright.async_api import async_playwright

from browser_factory import create_browser_context
from comment_bot import dump_interactive_elements, save_debug_screenshot
from config import REDDIT_MOBILE_USER_AGENT
from reddit_login_bot import _dismiss_cookie_banner, _goto_with_retry
from reddit_selectors import COMMENT, HOME, POST
from reddit_session import RedditSession

logger = logging.getLogger("RedditBot")


def _result(
    *,
    success: bool,
    action: str,
    profile_name: str,
    error: Optional[str] = None,
    **extra,
) -> Dict[str, Any]:
    return {
        "success": success,
        "platform": "reddit",
        "action": action,
        "profile_name": profile_name,
        "error": error,
        **extra,
    }


@asynccontextmanager
async def _session_page(session: RedditSession, proxy_url: Optional[str] = None):
    fingerprint = session.get_device_fingerprint()
    async with async_playwright() as playwright:
        browser, context = await create_browser_context(
            playwright,
            user_agent=session.get_user_agent() or REDDIT_MOBILE_USER_AGENT,
            viewport=session.get_viewport(),
            proxy_url=proxy_url or session.get_proxy(),
            timezone_id=fingerprint["timezone"],
            locale=fingerprint["locale"],
            headless=True,
            storage_state=session.get_storage_state(),
            is_mobile=True,
            has_touch=True,
        )
        try:
            page = await context.new_page()
            yield browser, context, page
        finally:
            await browser.close()


async def _goto(page, url: str):
    await _goto_with_retry(page, url, profile_name="reddit_action")
    await page.wait_for_timeout(2500)
    await _dismiss_cookie_banner(page)
    await page.wait_for_timeout(500)


async def _fill_first(page, selectors, value: str) -> bool:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count() > 0 and await locator.is_visible():
                await locator.fill(value)
                return True
        except Exception:
            continue
    return False


async def _click_first(page, selectors, *, timeout_ms: int = 4000) -> bool:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count() > 0 and await locator.is_visible():
                await locator.click(timeout=timeout_ms)
                return True
        except Exception:
            continue
    return False


async def _first_visible_comment_link(page) -> Optional[str]:
    for selector in HOME["comment_link"]:
        try:
            locator = page.locator(selector)
            count = await locator.count()
            for idx in range(min(count, 8)):
                candidate = locator.nth(idx)
                if await candidate.is_visible():
                    href = await candidate.get_attribute("href")
                    if href and "/comments/" in href:
                        return href if href.startswith("http") else f"https://www.reddit.com{href}"
        except Exception:
            continue
    return None


async def browse_feed(session: RedditSession, proxy_url: Optional[str] = None, scrolls: int = 3) -> Dict[str, Any]:
    async with _session_page(session, proxy_url) as (_browser, _context, page):
        try:
            await _goto(page, "https://www.reddit.com/")
            await dump_interactive_elements(page, "REDDIT BROWSE FEED")
            for _ in range(max(1, scrolls)):
                await page.mouse.wheel(0, 500)
                await page.wait_for_timeout(1200)
            screenshot = await save_debug_screenshot(page, f"reddit_browse_{session.profile_name}")
            return _result(
                success=True,
                action="browse_feed",
                profile_name=session.profile_name,
                screenshot=screenshot,
                current_url=page.url,
            )
        except Exception as exc:
            return _result(success=False, action="browse_feed", profile_name=session.profile_name, error=str(exc))


async def upvote_random_post(session: RedditSession, proxy_url: Optional[str] = None) -> Dict[str, Any]:
    async with _session_page(session, proxy_url) as (_browser, _context, page):
        try:
            await _goto(page, "https://www.reddit.com/")
            clicked = await page.evaluate(
                """() => {
                    const articles = Array.from(document.querySelectorAll('article'));
                    const findButton = (root) => {
                        const buttons = Array.from(root.querySelectorAll('button, [role="button"]'));
                        return buttons.find((button) => {
                            const aria = (button.getAttribute('aria-label') || '').toLowerCase();
                            const text = (button.innerText || '').toLowerCase();
                            return (aria.includes('upvote') || text.includes('upvote')) && !aria.includes('remove');
                        });
                    };
                    const targetArticle = articles.find((article) => !/promoted/i.test(article.innerText || '')) || articles[0];
                    if (!targetArticle) return {ok: false, reason: 'no_article'};
                    const button = findButton(targetArticle);
                    if (!button) return {ok: false, reason: 'no_upvote_button'};
                    button.click();
                    return {ok: true, articleText: (targetArticle.innerText || '').slice(0, 200)};
                }"""
            )
            await page.wait_for_timeout(1500)
            screenshot = await save_debug_screenshot(page, f"reddit_upvote_{session.profile_name}")
            if not clicked.get("ok"):
                await dump_interactive_elements(page, "REDDIT UPVOTE FAILED")
                return _result(
                    success=False,
                    action="upvote",
                    profile_name=session.profile_name,
                    error=clicked.get("reason", "Unknown upvote failure"),
                    screenshot=screenshot,
                )
            return _result(
                success=True,
                action="upvote",
                profile_name=session.profile_name,
                screenshot=screenshot,
                article_excerpt=clicked.get("articleText"),
            )
        except Exception as exc:
            return _result(success=False, action="upvote", profile_name=session.profile_name, error=str(exc))


async def open_post_target(session: RedditSession, url: str, proxy_url: Optional[str] = None) -> Dict[str, Any]:
    async with _session_page(session, proxy_url) as (_browser, _context, page):
        try:
            await _goto(page, url)
            screenshot = await save_debug_screenshot(page, f"reddit_open_target_{session.profile_name}")
            return _result(
                success=True,
                action="open_target",
                profile_name=session.profile_name,
                screenshot=screenshot,
                current_url=page.url,
            )
        except Exception as exc:
            return _result(success=False, action="open_target", profile_name=session.profile_name, error=str(exc))


async def create_post(
    session: RedditSession,
    *,
    title: str,
    body: Optional[str] = None,
    subreddit: Optional[str] = None,
    image_path: Optional[str] = None,
    proxy_url: Optional[str] = None,
) -> Dict[str, Any]:
    target_url = "https://www.reddit.com/submit"
    if subreddit:
        normalized = subreddit.strip().lstrip("r/").strip("/")
        target_url = f"https://www.reddit.com/r/{quote(normalized)}/submit"

    async with _session_page(session, proxy_url) as (_browser, _context, page):
        try:
            await _goto(page, target_url)
            await dump_interactive_elements(page, "REDDIT CREATE POST")

            if not await _fill_first(page, POST["title_input"], title):
                raise RuntimeError("Reddit post title input not found")

            if body:
                if not await _fill_first(page, POST["body_input"], body):
                    await page.keyboard.type(body, delay=15)

            if image_path:
                upload_path = str(Path(image_path).expanduser().resolve())
                uploaded = False
                for selector in POST["media_input"]:
                    try:
                        locator = page.locator(selector).first
                        if await locator.count() > 0:
                            await locator.set_input_files(upload_path)
                            uploaded = True
                            break
                    except Exception:
                        continue
                if not uploaded:
                    raise RuntimeError("Reddit media upload input not found")
                await page.wait_for_timeout(2500)

            if not await _click_first(page, POST["post_button"], timeout_ms=5000):
                raise RuntimeError("Reddit Post button not found")

            await page.wait_for_timeout(5000)
            screenshot = await save_debug_screenshot(page, f"reddit_create_post_{session.profile_name}")
            current_url = page.url
            success = "/comments/" in current_url or "posted" in (await page.locator("body").inner_text()).lower()
            if not success:
                await dump_interactive_elements(page, "REDDIT POST VERIFY FAILED")
            return _result(
                success=success,
                action="create_post",
                profile_name=session.profile_name,
                screenshot=screenshot,
                current_url=current_url,
                error=None if success else "Reddit post submission verification failed",
            )
        except Exception as exc:
            return _result(success=False, action="create_post", profile_name=session.profile_name, error=str(exc))


async def comment_on_post(
    session: RedditSession,
    *,
    url: str,
    text: str,
    proxy_url: Optional[str] = None,
) -> Dict[str, Any]:
    async with _session_page(session, proxy_url) as (_browser, _context, page):
        try:
            await _goto(page, url)
            await dump_interactive_elements(page, "REDDIT COMMENT ON POST")

            if not await _fill_first(page, COMMENT["composer_input"], text):
                raise RuntimeError("Reddit comment composer not found")

            if not await _click_first(page, COMMENT["submit_button"], timeout_ms=4000):
                raise RuntimeError("Reddit Comment button not found")

            await page.wait_for_timeout(4000)
            screenshot = await save_debug_screenshot(page, f"reddit_comment_{session.profile_name}")
            body = (await page.locator("body").inner_text()).lower()
            success = text[:40].lower() in body
            return _result(
                success=success,
                action="comment_post",
                profile_name=session.profile_name,
                screenshot=screenshot,
                current_url=page.url,
                error=None if success else "Reddit comment verification failed",
            )
        except Exception as exc:
            return _result(success=False, action="comment_post", profile_name=session.profile_name, error=str(exc))


async def reply_to_comment(
    session: RedditSession,
    *,
    url: str,
    text: str,
    proxy_url: Optional[str] = None,
) -> Dict[str, Any]:
    async with _session_page(session, proxy_url) as (_browser, _context, page):
        try:
            await _goto(page, url)
            await dump_interactive_elements(page, "REDDIT REPLY TO COMMENT")

            if not await _click_first(page, COMMENT["reply_button"], timeout_ms=4000):
                raise RuntimeError("Reddit Reply button not found")
            await page.wait_for_timeout(1000)

            if not await _fill_first(page, COMMENT["reply_input"], text):
                raise RuntimeError("Reddit reply input not found")

            if not await _click_first(page, COMMENT["submit_button"], timeout_ms=4000):
                raise RuntimeError("Reddit reply submit button not found")

            await page.wait_for_timeout(4000)
            screenshot = await save_debug_screenshot(page, f"reddit_reply_{session.profile_name}")
            body = (await page.locator("body").inner_text()).lower()
            success = text[:40].lower() in body
            return _result(
                success=success,
                action="reply_comment",
                profile_name=session.profile_name,
                screenshot=screenshot,
                current_url=page.url,
                error=None if success else "Reddit reply verification failed",
            )
        except Exception as exc:
            return _result(success=False, action="reply_comment", profile_name=session.profile_name, error=str(exc))


async def upload_media_only(
    session: RedditSession,
    *,
    image_path: str,
    proxy_url: Optional[str] = None,
) -> Dict[str, Any]:
    return await create_post(
        session,
        title="media upload verification",
        body="",
        image_path=image_path,
        proxy_url=proxy_url,
    )


async def run_reddit_action(
    session: RedditSession,
    *,
    action: str,
    proxy_url: Optional[str] = None,
    url: Optional[str] = None,
    text: Optional[str] = None,
    title: Optional[str] = None,
    body: Optional[str] = None,
    subreddit: Optional[str] = None,
    image_path: Optional[str] = None,
) -> Dict[str, Any]:
    normalized = str(action or "").strip().lower()
    if normalized == "browse_feed":
        return await browse_feed(session, proxy_url=proxy_url)
    if normalized == "upvote":
        return await upvote_random_post(session, proxy_url=proxy_url)
    if normalized == "open_target":
        if not url:
            return _result(success=False, action=normalized, profile_name=session.profile_name, error="url is required")
        return await open_post_target(session, url, proxy_url=proxy_url)
    if normalized == "create_post":
        if not title:
            return _result(success=False, action=normalized, profile_name=session.profile_name, error="title is required")
        return await create_post(
            session,
            title=title,
            body=body,
            subreddit=subreddit,
            image_path=image_path,
            proxy_url=proxy_url,
        )
    if normalized == "comment_post":
        if not url or not text:
            return _result(success=False, action=normalized, profile_name=session.profile_name, error="url and text are required")
        return await comment_on_post(session, url=url, text=text, proxy_url=proxy_url)
    if normalized == "reply_comment":
        if not url or not text:
            return _result(success=False, action=normalized, profile_name=session.profile_name, error="url and text are required")
        return await reply_to_comment(session, url=url, text=text, proxy_url=proxy_url)
    if normalized == "upload_media":
        if not image_path:
            return _result(success=False, action=normalized, profile_name=session.profile_name, error="image_path is required")
        return await upload_media_only(session, image_path=image_path, proxy_url=proxy_url)
    return _result(success=False, action=normalized, profile_name=session.profile_name, error=f"Unsupported Reddit action: {action}")
