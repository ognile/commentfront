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

from browser_factory import apply_page_identity_overrides, create_browser_context
from comment_bot import dump_interactive_elements, save_debug_screenshot
from config import REDDIT_MOBILE_USER_AGENT
from reddit_login_bot import _dismiss_cookie_banner, _goto_with_retry
from reddit_selectors import COMMENT, HOME, POST
from reddit_session import RedditSession
from forensics import (
    build_generic_verdict,
    get_current_forensic_recorder,
    queue_current_event,
    reset_current_forensic_recorder,
    set_current_forensic_recorder,
    start_forensic_attempt,
)

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


def _normalize_text(value: Optional[str]) -> str:
    return " ".join(str(value or "").strip().lower().split())


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
            await apply_page_identity_overrides(
                context,
                page,
                user_agent=session.get_user_agent() or REDDIT_MOBILE_USER_AGENT,
                locale=fingerprint["locale"],
            )
            recorder = get_current_forensic_recorder()
            if recorder:
                await recorder.attach_page(page, context)
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


async def _first_visible_locator(page, selectors):
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count() > 0 and await locator.is_visible():
                return locator
        except Exception:
            continue
    return None


async def _current_thread_title(page) -> Optional[str]:
    for selector in ("h1", "main h1", "article h1"):
        try:
            locator = page.locator(selector).first
            if await locator.count() > 0 and await locator.is_visible():
                text = (await locator.inner_text()).strip()
                if text:
                    return text
        except Exception:
            continue
    return None


async def _thread_context_present(page, expected_title: Optional[str]) -> bool:
    normalized_title = _normalize_text(expected_title)
    if not normalized_title:
        return True
    current_title = _normalize_text(await _current_thread_title(page))
    return bool(
        current_title
        and (
            current_title == normalized_title
            or current_title in normalized_title
            or normalized_title in current_title
        )
    )


async def _click_composer_text_region(page, expected_title: Optional[str] = None) -> bool:
    try:
        candidate = await page.evaluate(
            """({ needle, expectedTitle }) => {
                const normalize = (value) => String(value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
                const viewportWidth = window.innerWidth || 393;
                const viewportHeight = window.innerHeight || 873;
                const phrase = normalize(needle);
                const titleNeedle = normalize(expectedTitle);
                const roots = [];
                const visitRoot = (root) => {
                    if (!root || roots.includes(root)) return;
                    roots.push(root);
                    if (!root.querySelectorAll) return;
                    for (const el of Array.from(root.querySelectorAll('*'))) {
                        if (el.shadowRoot) visitRoot(el.shadowRoot);
                    }
                };
                visitRoot(document);

                const visibleRect = (rect) => {
                    if (!rect) return false;
                    if (rect.width < 6 || rect.height < 6) return false;
                    if (rect.bottom < 0 || rect.right < 0) return false;
                    if (rect.top > viewportHeight || rect.left > viewportWidth) return false;
                    return true;
                };

                let titleRect = null;
                if (titleNeedle) {
                    for (const root of roots) {
                        const headings = root.querySelectorAll ? Array.from(root.querySelectorAll('h1, h2, h3')) : [];
                        for (const heading of headings) {
                            const text = normalize(heading.innerText || heading.textContent);
                            if (!text) continue;
                            if (text === titleNeedle || text.includes(titleNeedle) || titleNeedle.includes(text)) {
                                const rect = heading.getBoundingClientRect();
                                if (visibleRect(rect)) {
                                    titleRect = rect;
                                    break;
                                }
                            }
                        }
                        if (titleRect) break;
                    }
                }

                const candidates = [];
                const addCandidate = (rect, clickTarget, source, label) => {
                    if (!visibleRect(rect)) return;
                    if (titleRect && rect.top <= titleRect.bottom - 6) return;
                    if (rect.top >= viewportHeight - 40) return;
                    const centerX = Math.round(Math.max(12, Math.min(viewportWidth - 12, rect.left + rect.width / 2)));
                    const centerY = Math.round(Math.max(12, Math.min(viewportHeight - 12, rect.top + rect.height / 2)));
                    const verticalOffset = titleRect ? Math.max(0, rect.top - titleRect.bottom) : rect.top;
                    candidates.push({
                        x: centerX,
                        y: centerY,
                        source,
                        label,
                        top: rect.top,
                        verticalOffset,
                        clickTarget,
                    });
                };

                for (const root of roots) {
                    const elements = root.querySelectorAll ? Array.from(root.querySelectorAll('*')) : [];
                    for (const el of elements) {
                        const text = normalize(el.innerText || el.textContent);
                        const placeholder = normalize(el.getAttribute && el.getAttribute('placeholder'));
                        const aria = normalize(el.getAttribute && el.getAttribute('aria-label'));
                        if (text.includes(phrase) || placeholder.includes(phrase) || aria.includes(phrase)) {
                            addCandidate(el.getBoundingClientRect(), el, 'element_text', text || placeholder || aria);
                        }
                    }
                    if (!root.createTreeWalker) continue;
                    const walker = root.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
                        acceptNode(node) {
                            return normalize(node.textContent).includes(phrase)
                                ? NodeFilter.FILTER_ACCEPT
                                : NodeFilter.FILTER_REJECT;
                        },
                    });
                    let node = walker.nextNode();
                    while (node) {
                        const range = document.createRange();
                        range.selectNodeContents(node);
                        const rects = Array.from(range.getClientRects());
                        for (const rect of rects) {
                            addCandidate(rect, node.parentElement || node.parentNode, 'text_node', normalize(node.textContent));
                        }
                        node = walker.nextNode();
                    }
                }

                candidates.sort((a, b) => {
                    if (a.verticalOffset !== b.verticalOffset) return a.verticalOffset - b.verticalOffset;
                    return a.top - b.top;
                });

                const best = candidates[0];
                if (!best) return { ok: false };
                let target = best.clickTarget;
                if (target && target.nodeType === Node.TEXT_NODE) target = target.parentElement;
                if (!target || !target.click) {
                    target = document.elementFromPoint(best.x, best.y);
                }
                if (target && target.click) {
                    target.click();
                }
                return {
                    ok: true,
                    x: best.x,
                    y: best.y,
                    source: best.source,
                    label: best.label,
                };
            }""",
            {"needle": "Join the conversation", "expectedTitle": expected_title or ""},
        )
    except Exception:
        candidate = None

    if not candidate or not candidate.get("ok"):
        return False

    queue_current_event(
        "click",
        {
            "method": "visible_text_region",
            "target": "comment_composer_trigger",
            "x": candidate.get("x"),
            "y": candidate.get("y"),
            "source": candidate.get("source"),
            "label": candidate.get("label"),
            "expected_title": expected_title,
        },
        phase="activation",
        source="reddit_bot",
    )
    await page.wait_for_timeout(600)
    return True


async def _click_composer_region_from_layout(page, expected_title: Optional[str] = None) -> bool:
    if not await _thread_context_present(page, expected_title):
        return False
    share_locator = await _first_visible_locator(page, COMMENT["share_button"])
    if not share_locator:
        return False
    try:
        share_box = await share_locator.bounding_box()
    except Exception:
        share_box = None
    if not share_box:
        return False

    viewport = page.viewport_size or {"width": 393, "height": 873}
    click_x = viewport["width"] / 2
    click_y = share_box["y"] + share_box["height"] + 18

    search_locator = await _first_visible_locator(page, COMMENT["search_comments_input"])
    if search_locator:
        try:
            search_box = await search_locator.bounding_box()
        except Exception:
            search_box = None
        if search_box:
            click_y = (share_box["y"] + share_box["height"] + search_box["y"]) / 2

    click_y = max(40, min(viewport["height"] - 40, click_y))
    await page.mouse.click(click_x, click_y)
    queue_current_event(
        "click",
        {
            "method": "layout_region_fallback",
            "target": "join_the_conversation_region",
            "x": click_x,
            "y": click_y,
            "share_box": share_box,
        },
        phase="activation",
        source="reddit_bot",
    )
    await page.wait_for_timeout(800)
    return await _thread_context_present(page, expected_title)


async def _open_comment_composer(page, expected_title: Optional[str] = None) -> bool:
    opened = await _click_first(page, COMMENT["composer_trigger"], timeout_ms=3000)
    if opened:
        queue_current_event(
            "click",
            {"method": "selector", "target": "comment_composer_trigger"},
            phase="activation",
            source="reddit_bot",
        )
        await page.wait_for_timeout(600)
        return await _thread_context_present(page, expected_title)

    if not opened:
        try:
            opened = bool(
                await page.evaluate(
                    """() => {
                        const candidates = Array.from(document.querySelectorAll('button, input, textarea, div'));
                        const probe = (value) => (value || '').toLowerCase().trim();
                        for (const node of candidates) {
                            const text = probe(node.innerText || node.textContent);
                            const placeholder = probe(node.getAttribute('placeholder'));
                            const aria = probe(node.getAttribute('aria-label'));
                            if (
                                text.includes('join the conversation') ||
                                placeholder.includes('join the conversation') ||
                                aria.includes('join the conversation')
                            ) {
                                node.click();
                                return true;
                            }
                        }
                        return false;
                    }"""
                )
            )
        except Exception:
            opened = False
    if opened:
        queue_current_event(
            "click",
            {"method": "dom_probe", "target": "comment_composer_trigger"},
            phase="activation",
            source="reddit_bot",
        )
        await page.wait_for_timeout(600)
        return await _thread_context_present(page, expected_title)

    if not opened:
        try:
            opened = await _click_composer_text_region(page, expected_title)
        except Exception:
            opened = False
    if opened:
        return await _thread_context_present(page, expected_title)

    if not opened:
        try:
            opened = await _click_composer_region_from_layout(page, expected_title)
        except Exception:
            opened = False
    return opened


async def _fill_comment_input(page, text: str, *, reply: bool = False, expected_title: Optional[str] = None) -> bool:
    selectors = COMMENT["reply_input"] if reply else COMMENT["composer_input"]
    if await _fill_first(page, selectors, text):
        return True
    if not await _open_comment_composer(page, expected_title):
        return False
    await page.wait_for_timeout(400)
    if await _fill_first(page, selectors, text):
        return True
    try:
        await page.keyboard.type(text, delay=25)
        await page.wait_for_timeout(500)
        typed = bool(
            await page.evaluate(
                """(needle) => {
                    const probe = (value) => (value || '').toLowerCase();
                    const target = probe(String(needle).slice(0, 40));
                    const active = document.activeElement;
                    if (!active) return false;
                    const activeText = probe(active.value || active.textContent || active.innerText);
                    if (activeText.includes(target)) return true;
                    const bodyText = probe(document.body ? document.body.innerText : '');
                    return bodyText.includes(target);
                }""",
                text,
            )
        )
        if typed:
            queue_current_event(
                "type",
                {"method": "keyboard_fallback", "length": len(text), "reply": reply},
                phase="typing",
                source="reddit_bot",
            )
            return True
    except Exception:
        return False
    return False


async def _capture_reddit_failure_state(page, label: str) -> None:
    try:
        await dump_interactive_elements(page, label)
    except Exception:
        pass
    try:
        await save_debug_screenshot(page, label.lower().replace(" ", "_"))
    except Exception:
        pass


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
            expected_title = await _current_thread_title(page)

            if not await _fill_comment_input(page, text, expected_title=expected_title):
                await _capture_reddit_failure_state(page, "REDDIT COMMENT COMPOSER MISSING")
                raise RuntimeError("Reddit comment composer not found")

            if not await _click_first(page, COMMENT["submit_button"], timeout_ms=4000):
                await _capture_reddit_failure_state(page, "REDDIT COMMENT SUBMIT MISSING")
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
            expected_title = await _current_thread_title(page)

            if not await _click_first(page, COMMENT["reply_button"], timeout_ms=4000):
                await _capture_reddit_failure_state(page, "REDDIT REPLY BUTTON MISSING")
                raise RuntimeError("Reddit Reply button not found")
            await page.wait_for_timeout(1000)

            if not await _fill_comment_input(page, text, reply=True, expected_title=expected_title):
                await _capture_reddit_failure_state(page, "REDDIT REPLY INPUT MISSING")
                raise RuntimeError("Reddit reply input not found")

            if not await _click_first(page, COMMENT["submit_button"], timeout_ms=4000):
                await _capture_reddit_failure_state(page, "REDDIT REPLY SUBMIT MISSING")
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
    forensic_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized = str(action or "").strip().lower()
    recorder = await start_forensic_attempt(
        platform="reddit",
        engine=(forensic_context or {}).get("engine", f"reddit_{normalized}"),
        profile_name=session.profile_name,
        campaign_id=(forensic_context or {}).get("campaign_id"),
        job_id=(forensic_context or {}).get("job_id"),
        session_id=session.profile_name,
        parent_attempt_id=(forensic_context or {}).get("parent_attempt_id"),
        run_id=(forensic_context or {}).get("run_id"),
        trace_id=(forensic_context or {}).get("trace_id"),
        metadata={
            "action": normalized,
            "url": url,
            "subreddit": subreddit,
            **((forensic_context or {}).get("metadata") or {}),
        },
    )
    recorder_token = set_current_forensic_recorder(recorder)
    result: Dict[str, Any]
    if normalized == "browse_feed":
        result = await browse_feed(session, proxy_url=proxy_url)
    elif normalized == "upvote":
        result = await upvote_random_post(session, proxy_url=proxy_url)
    elif normalized == "open_target":
        if not url:
            result = _result(success=False, action=normalized, profile_name=session.profile_name, error="url is required")
        else:
            result = await open_post_target(session, url, proxy_url=proxy_url)
    elif normalized == "create_post":
        if not title:
            result = _result(success=False, action=normalized, profile_name=session.profile_name, error="title is required")
        else:
            result = await create_post(
                session,
                title=title,
                body=body,
                subreddit=subreddit,
                image_path=image_path,
                proxy_url=proxy_url,
            )
    elif normalized == "comment_post":
        if not url or not text:
            result = _result(success=False, action=normalized, profile_name=session.profile_name, error="url and text are required")
        else:
            result = await comment_on_post(session, url=url, text=text, proxy_url=proxy_url)
    elif normalized == "reply_comment":
        if not url or not text:
            result = _result(success=False, action=normalized, profile_name=session.profile_name, error="url and text are required")
        else:
            result = await reply_to_comment(session, url=url, text=text, proxy_url=proxy_url)
    elif normalized == "upload_media":
        if not image_path:
            result = _result(success=False, action=normalized, profile_name=session.profile_name, error="image_path is required")
        else:
            result = await upload_media_only(session, image_path=image_path, proxy_url=proxy_url)
    else:
        result = _result(success=False, action=normalized, profile_name=session.profile_name, error=f"Unsupported Reddit action: {action}")
    result["attempt_id"] = recorder.attempt_id
    result["trace_id"] = recorder.trace_id
    verdict = build_generic_verdict(result, success_summary=f"reddit action '{normalized}' completed.")
    result["final_verdict"] = verdict.final_verdict
    result["evidence_summary"] = verdict.summary
    await recorder.finalize(verdict, metadata={"action": normalized})
    reset_current_forensic_recorder(recorder_token)
    return result
