"""
Reddit mobile-web executor.
"""

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import httpx
from playwright.async_api import async_playwright

from browser_factory import apply_page_identity_overrides, create_browser_context
from comment_bot import dump_interactive_elements, save_debug_screenshot
from config import REDDIT_MOBILE_USER_AGENT
from reddit_login_bot import _dismiss_cookie_banner, _goto_with_retry
from reddit_selectors import COMMENT, HOME, POST, SUBREDDIT
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
REDDIT_HTTP_HEADERS = {"User-Agent": "commentfront-reddit-bot/1.0"}


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


def _extract_reddit_comment_id(target_comment_url: Optional[str]) -> Optional[str]:
    path = str(target_comment_url or "").split("?", 1)[0].strip().rstrip("/")
    if not path:
        return None
    parts = [segment for segment in path.split("/") if segment]
    if "comment" in parts:
        idx = parts.index("comment")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    if "comments" in parts and len(parts) >= 2:
        tail = parts[-1]
        if re.fullmatch(r"[a-z0-9]+", tail, flags=re.IGNORECASE):
            return tail
    return None


def _reddit_json_url(url: str) -> str:
    clean = str(url or "").split("?", 1)[0].strip().rstrip("/")
    return f"{clean}/.json?raw_json=1&limit=20"


def _find_comment_record(children: List[Dict[str, Any]], comment_id: str) -> Optional[Dict[str, Any]]:
    for child in list(children or []):
        if child.get("kind") != "t1":
            continue
        data = dict(child.get("data") or {})
        if str(data.get("id") or "").strip() == str(comment_id or "").strip():
            return data
        replies = data.get("replies")
        if isinstance(replies, dict):
            found = _find_comment_record(replies.get("data", {}).get("children") or [], comment_id)
            if found:
                return found
    return None


async def _load_target_comment_context(target_comment_url: str) -> Optional[Dict[str, Any]]:
    comment_id = _extract_reddit_comment_id(target_comment_url)
    if not comment_id:
        return None

    try:
        async with httpx.AsyncClient(
            headers=REDDIT_HTTP_HEADERS,
            follow_redirects=True,
            timeout=20.0,
        ) as client:
            response = await client.get(_reddit_json_url(target_comment_url))
            response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning(f"failed to fetch reddit comment context for {target_comment_url}: {exc}")
        return {
            "comment_id": comment_id,
            "comment_url": target_comment_url,
            "thread_url": None,
            "author": None,
            "body": None,
            "body_snippet": None,
            "title": None,
        }

    try:
        post = payload[0]["data"]["children"][0]["data"]
        comment = _find_comment_record(payload[1]["data"]["children"], comment_id) if len(payload) > 1 else None
    except Exception:
        post = {}
        comment = None

    body = str((comment or {}).get("body") or "").strip()
    snippet = body[:120] if body else None
    return {
        "comment_id": comment_id,
        "comment_url": target_comment_url,
        "thread_url": f"https://www.reddit.com{post.get('permalink', '')}" if post.get("permalink") else None,
        "author": (comment or {}).get("author"),
        "body": body or None,
        "body_snippet": snippet,
        "title": post.get("title"),
    }


def _pick_candidate(
    candidates: List[Dict[str, Any]],
    *,
    anchor_rect: Optional[Dict[str, float]] = None,
    max_vertical_gap: Optional[float] = None,
    require_below_anchor: bool = False,
    row_y: Optional[float] = None,
    left_of_x: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    best: Optional[Dict[str, Any]] = None
    best_score: Optional[float] = None
    for candidate in list(candidates or []):
        top = float(candidate.get("top") or 0.0)
        left = float(candidate.get("left") or 0.0)
        center_y = float(candidate.get("y") or 0.0)
        center_x = float(candidate.get("x") or 0.0)
        score = 0.0

        if row_y is not None:
            score += abs(center_y - float(row_y)) * 4
        if left_of_x is not None:
            if center_x >= float(left_of_x):
                continue
            score += max(0.0, float(left_of_x) - center_x)

        if anchor_rect:
            anchor_top = float(anchor_rect.get("top") or 0.0)
            anchor_bottom = float(anchor_rect.get("bottom") or anchor_top)
            anchor_left = float(anchor_rect.get("left") or 0.0)
            vertical_gap = top - anchor_bottom
            if require_below_anchor and vertical_gap < -8:
                continue
            score += abs(vertical_gap) * 5
            score += abs(left - anchor_left)
            if max_vertical_gap is not None and abs(vertical_gap) > max_vertical_gap:
                continue

        if best_score is None or score < best_score:
            best = candidate
            best_score = score
    return best


async def _collect_control_candidates(page, needles: List[str]) -> List[Dict[str, Any]]:
    try:
        result = await page.evaluate(
            """(needles) => {
                const normalize = (value) => String(value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
                const rootList = [];
                const visitRoot = (root) => {
                    if (!root || rootList.includes(root)) return;
                    rootList.push(root);
                    if (!root.querySelectorAll) return;
                    for (const el of Array.from(root.querySelectorAll('*'))) {
                        if (el.shadowRoot) visitRoot(el.shadowRoot);
                    }
                };
                visitRoot(document);
                const visible = (rect) => rect && rect.width >= 6 && rect.height >= 6 && rect.bottom >= 0 && rect.right >= 0 && rect.top <= (window.innerHeight || 873) && rect.left <= (window.innerWidth || 393);
                const results = [];
                const selector = 'button,[role=\"button\"],a,input,textarea,[aria-label],[placeholder]';
                for (const root of rootList) {
                    const nodes = root.querySelectorAll ? Array.from(root.querySelectorAll(selector)) : [];
                    for (const node of nodes) {
                        const rect = node.getBoundingClientRect();
                        if (!visible(rect)) continue;
                        const text = normalize(node.innerText || node.textContent);
                        const aria = normalize(node.getAttribute && node.getAttribute('aria-label'));
                        const placeholder = normalize(node.getAttribute && node.getAttribute('placeholder'));
                        const title = normalize(node.getAttribute && node.getAttribute('title'));
                        const combined = [text, aria, placeholder, title].filter(Boolean).join(' | ');
                        if (!needles.some((needle) => combined.includes(needle))) continue;
                        results.push({
                            x: Math.round(rect.left + rect.width / 2),
                            y: Math.round(rect.top + rect.height / 2),
                            left: rect.left,
                            top: rect.top,
                            right: rect.right,
                            bottom: rect.bottom,
                            width: rect.width,
                            height: rect.height,
                            text,
                            aria,
                            placeholder,
                            title,
                            combined,
                        });
                    }
                }
                return results;
            }""",
            [_normalize_text(needle) for needle in list(needles or []) if _normalize_text(needle)],
        )
    except Exception:
        return []
    return list(result or [])


async def _locate_text_anchor(page, needle: Optional[str], expected_title: Optional[str] = None) -> Optional[Dict[str, Any]]:
    normalized = _normalize_text(needle)
    if not normalized:
        return None
    try:
        result = await page.evaluate(
            """({ needle, expectedTitle }) => {
                const normalize = (value) => String(value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
                const viewportWidth = window.innerWidth || 393;
                const viewportHeight = window.innerHeight || 873;
                const visible = (rect) => rect && rect.width >= 6 && rect.height >= 6 && rect.bottom >= 0 && rect.right >= 0 && rect.top <= viewportHeight && rect.left <= viewportWidth;
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

                let titleRect = null;
                const titleNeedle = normalize(expectedTitle);
                if (titleNeedle) {
                    for (const root of roots) {
                        const headings = root.querySelectorAll ? Array.from(root.querySelectorAll('h1, h2, h3')) : [];
                        for (const heading of headings) {
                            const text = normalize(heading.innerText || heading.textContent);
                            if (!text) continue;
                            if (text === titleNeedle || text.includes(titleNeedle) || titleNeedle.includes(text)) {
                                const rect = heading.getBoundingClientRect();
                                if (visible(rect)) {
                                    titleRect = rect;
                                    break;
                                }
                            }
                        }
                        if (titleRect) break;
                    }
                }

                const candidates = [];
                const addCandidate = (rect, text) => {
                    if (!visible(rect)) return;
                    if (titleRect && rect.top <= titleRect.bottom - 6) return;
                    candidates.push({
                        left: rect.left,
                        top: rect.top,
                        right: rect.right,
                        bottom: rect.bottom,
                        width: rect.width,
                        height: rect.height,
                        x: Math.round(rect.left + rect.width / 2),
                        y: Math.round(rect.top + rect.height / 2),
                        text,
                        score: titleRect ? Math.max(0, rect.top - titleRect.bottom) : rect.top,
                    });
                };

                for (const root of roots) {
                    const elements = root.querySelectorAll ? Array.from(root.querySelectorAll('*')) : [];
                    for (const el of elements) {
                        const text = normalize(el.innerText || el.textContent);
                        if (text && (text.includes(needle) || needle.includes(text))) {
                            addCandidate(el.getBoundingClientRect(), text);
                        }
                    }
                }

                candidates.sort((a, b) => a.score - b.score || a.top - b.top);
                return candidates[0] || null;
            }""",
            {"needle": normalized, "expectedTitle": expected_title or ""},
        )
    except Exception:
        return None
    return result or None


async def _find_visible_text_region(
    page,
    *,
    needle: str,
    expected_title: Optional[str] = None,
    min_top: float = 0.0,
    max_top: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    normalized = _normalize_text(needle)
    if not normalized:
        return None
    try:
        result = await page.evaluate(
            """({ needle, expectedTitle, minTop, maxTop }) => {
                const normalize = (value) => String(value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
                const viewportWidth = window.innerWidth || 393;
                const viewportHeight = window.innerHeight || 873;
                const visibleRect = (rect) => rect && rect.width >= 6 && rect.height >= 6 && rect.bottom >= 0 && rect.right >= 0 && rect.top <= viewportHeight && rect.left <= viewportWidth;
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

                let titleRect = null;
                const titleNeedle = normalize(expectedTitle);
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
                const addCandidate = (rect, source, label) => {
                    if (!visibleRect(rect)) return;
                    if (titleRect && rect.top <= titleRect.bottom - 6) return;
                    if (rect.top < minTop) return;
                    if (typeof maxTop === 'number' && rect.top > maxTop) return;
                    const centerX = Math.round(Math.max(12, Math.min(viewportWidth - 12, rect.left + rect.width / 2)));
                    const centerY = Math.round(Math.max(12, Math.min(viewportHeight - 12, rect.top + rect.height / 2)));
                    const verticalOffset = titleRect ? Math.max(0, rect.top - titleRect.bottom) : rect.top;
                    candidates.push({
                        x: centerX,
                        y: centerY,
                        source,
                        label,
                        top: rect.top,
                        left: rect.left,
                        verticalOffset,
                    });
                };

                for (const root of roots) {
                    const elements = root.querySelectorAll ? Array.from(root.querySelectorAll('*')) : [];
                    for (const el of elements) {
                        const text = normalize(el.innerText || el.textContent);
                        const placeholder = normalize(el.getAttribute && el.getAttribute('placeholder'));
                        const aria = normalize(el.getAttribute && el.getAttribute('aria-label'));
                        const combined = [text, placeholder, aria].filter(Boolean).join(' | ');
                        if (!combined.includes(needle)) continue;
                        addCandidate(el.getBoundingClientRect(), 'element_text', combined);
                    }
                    if (!root.createTreeWalker) continue;
                    const walker = root.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
                        acceptNode(node) {
                            return normalize(node.textContent).includes(needle)
                                ? NodeFilter.FILTER_ACCEPT
                                : NodeFilter.FILTER_REJECT;
                        },
                    });
                    let node = walker.nextNode();
                    while (node) {
                        const range = document.createRange();
                        range.selectNodeContents(node);
                        for (const rect of Array.from(range.getClientRects())) {
                            addCandidate(rect, 'text_node', normalize(node.textContent));
                        }
                        node = walker.nextNode();
                    }
                }

                candidates.sort((a, b) => a.verticalOffset - b.verticalOffset || a.top - b.top || a.left - b.left);
                return candidates[0] || null;
            }""",
            {
                "needle": normalized,
                "expectedTitle": expected_title or "",
                "minTop": float(min_top or 0.0),
                "maxTop": None if max_top is None else float(max_top),
            },
        )
    except Exception:
        return None
    return result or None


async def _click_visible_text_region(
    page,
    *,
    needle: str,
    action_name: str,
    expected_title: Optional[str] = None,
    min_top: float = 0.0,
    max_top: Optional[float] = None,
) -> bool:
    candidate = await _find_visible_text_region(
        page,
        needle=needle,
        expected_title=expected_title,
        min_top=min_top,
        max_top=max_top,
    )
    if not candidate:
        return False
    await page.mouse.click(candidate["x"], candidate["y"])
    queue_current_event(
        "click",
        {
            "method": "visible_text_region",
            "action_name": action_name,
            "needle": needle,
            "x": candidate.get("x"),
            "y": candidate.get("y"),
            "source": candidate.get("source"),
            "label": candidate.get("label"),
        },
        phase="activation",
        source="reddit_bot",
    )
    await page.wait_for_timeout(700)
    return True


async def _click_named_control(
    page,
    *,
    action_name: str,
    needles: List[str],
    expected_title: Optional[str] = None,
    anchor_text: Optional[str] = None,
    max_vertical_gap: Optional[float] = None,
    require_below_anchor: bool = False,
    row_y: Optional[float] = None,
    left_of_x: Optional[float] = None,
) -> bool:
    candidates = await _collect_control_candidates(page, needles)
    anchor_rect = await _locate_text_anchor(page, anchor_text, expected_title=expected_title) if anchor_text else None
    target = _pick_candidate(
        candidates,
        anchor_rect=anchor_rect,
        max_vertical_gap=max_vertical_gap,
        require_below_anchor=require_below_anchor,
        row_y=row_y,
        left_of_x=left_of_x,
    )
    if not target:
        return False
    await page.mouse.click(target["x"], target["y"])
    queue_current_event(
        "click",
        {
            "method": "named_control",
            "action_name": action_name,
            "needles": needles,
            "x": target.get("x"),
            "y": target.get("y"),
            "anchor_text": anchor_text,
            "expected_title": expected_title,
            "matched": target.get("combined"),
        },
        phase="activation",
        source="reddit_bot",
    )
    await page.wait_for_timeout(700)
    return True


async def _verify_named_control_state(
    page,
    *,
    needles: List[str],
    expected_title: Optional[str] = None,
    anchor_text: Optional[str] = None,
    max_vertical_gap: Optional[float] = None,
    require_below_anchor: bool = False,
    row_y: Optional[float] = None,
    left_of_x: Optional[float] = None,
) -> bool:
    candidates = await _collect_control_candidates(page, needles)
    anchor_rect = await _locate_text_anchor(page, anchor_text, expected_title=expected_title) if anchor_text else None
    target = _pick_candidate(
        candidates,
        anchor_rect=anchor_rect,
        max_vertical_gap=max_vertical_gap,
        require_below_anchor=require_below_anchor,
        row_y=row_y,
        left_of_x=left_of_x,
    )
    return bool(target)


async def _keyboard_type_and_verify(page, text: str, *, reply: bool = False) -> bool:
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


async def _active_editable_present(page) -> bool:
    try:
        return bool(
            await page.evaluate(
                """() => {
                    const active = document.activeElement;
                    if (!active) return false;
                    const tag = String(active.tagName || '').toLowerCase();
                    return Boolean(
                        active.isContentEditable ||
                        tag === 'textarea' ||
                        (tag === 'input' && String(active.type || '').toLowerCase() !== 'hidden')
                    );
                }"""
            )
        )
    except Exception:
        return False


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


async def _visible_selector_exists(page, selectors) -> bool:
    return bool(await _first_visible_locator(page, selectors))


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
    viewport = getattr(page, "viewport_size", None) or {"height": 873}
    return await _click_visible_text_region(
        page,
        needle="Join the conversation",
        action_name="comment_composer_trigger",
        expected_title=expected_title,
        max_top=viewport["height"] - 40,
    )


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


async def _find_subreddit_header_action(page) -> Optional[Dict[str, Any]]:
    try:
        result = await page.evaluate(
            """() => {
                const viewportWidth = window.innerWidth || 393;
                const viewportHeight = window.innerHeight || 873;
                const visible = (rect) => rect && rect.width >= 20 && rect.height >= 20 && rect.bottom >= 0 && rect.right >= 0 && rect.top <= viewportHeight && rect.left <= viewportWidth;
                const section = Array.from(document.querySelectorAll('*')).find((node) => {
                    const aria = String(node.getAttribute && node.getAttribute('aria-label') || '').toLowerCase();
                    return aria.includes('community actions');
                });
                if (!section) return null;
                const rect = section.getBoundingClientRect();
                if (!visible(rect)) return null;
                return {
                    x: Math.round(Math.max(24, Math.min(viewportWidth - 24, rect.right - 58))),
                    y: Math.round(Math.max(24, Math.min(viewportHeight - 24, rect.top + 18))),
                    bounds: {
                        left: rect.left,
                        top: rect.top,
                        right: rect.right,
                        bottom: rect.bottom,
                        width: rect.width,
                        height: rect.height,
                    },
                };
            }"""
        )
    except Exception:
        return None
    return result or None


async def _comment_action_row(
    page,
    *,
    author: Optional[str],
    expected_title: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    normalized_author = _normalize_text(author)
    try:
        result = await page.evaluate(
            """({ author, expectedTitle }) => {
                const normalize = (value) => String(value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
                const viewportWidth = window.innerWidth || 393;
                const viewportHeight = window.innerHeight || 873;
                const visibleRect = (rect) => rect && rect.width >= 6 && rect.height >= 6 && rect.bottom >= 0 && rect.right >= 0 && rect.top <= viewportHeight && rect.left <= viewportWidth;
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

                let titleRect = null;
                const titleNeedle = normalize(expectedTitle);
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

                const replies = [];
                for (const root of roots) {
                    const nodes = root.querySelectorAll ? Array.from(root.querySelectorAll('button, a, div, span')) : [];
                    for (const node of nodes) {
                        const text = normalize(node.innerText || node.textContent);
                        const aria = normalize(node.getAttribute && node.getAttribute('aria-label'));
                        const rect = node.getBoundingClientRect();
                        if (!visibleRect(rect)) continue;
                        if (titleRect && rect.top <= titleRect.bottom + 18) continue;
                        if (!(text.includes('reply') || aria.includes('reply'))) continue;
                        replies.push({
                            left: rect.left,
                            top: rect.top,
                            right: rect.right,
                            width: rect.width,
                            height: rect.height,
                            x: Math.round(rect.left + rect.width / 2),
                            y: Math.round(rect.top + rect.height / 2),
                            text,
                            aria,
                        });
                    }
                }
                replies.sort((a, b) => a.top - b.top || a.left - b.left);
                const reply = replies[0];
                if (!reply) return null;

                let authorRect = null;
                const authors = [];
                for (const root of roots) {
                    const nodes = root.querySelectorAll ? Array.from(root.querySelectorAll('a, button, span, div')) : [];
                    for (const node of nodes) {
                        const text = normalize(node.innerText || node.textContent);
                        const aria = normalize(node.getAttribute && node.getAttribute('aria-label'));
                        const rect = node.getBoundingClientRect();
                        if (!visibleRect(rect)) continue;
                        if (titleRect && rect.top <= titleRect.bottom - 6) continue;
                        if (author) {
                            if (!text.includes(author) && !aria.includes(author)) continue;
                        } else if (!aria.includes("profile") && !text) {
                            continue;
                        }
                        if (rect.top > reply.top) continue;
                        const verticalGap = reply.top - rect.bottom;
                        if (verticalGap < 0 || verticalGap > 220) continue;
                        authors.push({
                            left: rect.left,
                            top: rect.top,
                            right: rect.right,
                            bottom: rect.bottom,
                            x: Math.round(rect.left + rect.width / 2),
                            y: Math.round(rect.top + rect.height / 2),
                            verticalGap,
                            text,
                            aria,
                        });
                    }
                }
                authors.sort((a, b) => a.verticalGap - b.verticalGap || a.top - b.top || a.left - b.left);
                authorRect = authors[0] || null;
                return {
                    author: authorRect,
                    reply,
                    vote: {
                        x: Math.round(Math.max(22, reply.left - 76)),
                        y: reply.y,
                    },
                };
            }""",
            {"author": normalized_author, "expectedTitle": expected_title or ""},
        )
    except Exception:
        return None
    return result or None


async def _capture_row_signature(page, *, row_y: float, max_x: float) -> List[str]:
    try:
        result = await page.evaluate(
            """({ rowY, maxX }) => {
                const viewportWidth = window.innerWidth || 393;
                const viewportHeight = window.innerHeight || 873;
                const visible = (rect) => rect && rect.width >= 4 && rect.height >= 4 && rect.bottom >= 0 && rect.right >= 0 && rect.top <= viewportHeight && rect.left <= viewportWidth;
                const normalize = (value) => String(value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
                const rows = [];
                for (const node of Array.from(document.querySelectorAll('*'))) {
                    const rect = node.getBoundingClientRect();
                    if (!visible(rect)) continue;
                    const centerY = rect.top + rect.height / 2;
                    if (Math.abs(centerY - rowY) > 28) continue;
                    if (rect.left > maxX) continue;
                    const text = normalize(node.innerText || node.textContent);
                    const aria = normalize(node.getAttribute && node.getAttribute('aria-label'));
                    const tag = normalize(node.tagName);
                    const cls = normalize(node.className);
                    rows.push(`${tag}|${text}|${aria}|${cls}|${Math.round(rect.left)}|${Math.round(rect.top)}|${Math.round(rect.width)}|${Math.round(rect.height)}`);
                }
                rows.sort();
                return rows;
            }""",
            {"rowY": float(row_y), "maxX": float(max_x)},
        )
    except Exception:
        return []
    return list(result or [])


async def _scroll_until_post_actions_visible(page, *, max_scrolls: int = 6) -> bool:
    for _ in range(max(1, max_scrolls)):
        if await _first_visible_locator(page, COMMENT["share_button"]):
            return True
        await page.mouse.wheel(0, 520)
        await page.wait_for_timeout(900)
    return bool(await _first_visible_locator(page, COMMENT["share_button"]))


async def _click_post_upvote_region(page, *, share_box: Dict[str, float]) -> bool:
    click_x = max(24, int(float(share_box["x"]) - 186))
    click_y = int(float(share_box["y"]) + (float(share_box["height"]) / 2))
    await page.mouse.click(click_x, click_y)
    queue_current_event(
        "click",
        {
            "method": "post_row_geometry",
            "action_name": "upvote_post",
            "x": click_x,
            "y": click_y,
            "share_box": share_box,
        },
        phase="activation",
        source="reddit_bot",
    )
    await page.wait_for_timeout(900)
    return True


async def _click_comment_upvote_region(page, *, row: Dict[str, Any]) -> bool:
    vote = dict(row.get("vote") or {})
    if not vote:
        return False
    await page.mouse.click(vote["x"], vote["y"])
    queue_current_event(
        "click",
        {
            "method": "comment_row_geometry",
            "action_name": "upvote_comment",
            "x": vote.get("x"),
            "y": vote.get("y"),
            "reply": row.get("reply"),
            "author": row.get("author"),
        },
        phase="activation",
        source="reddit_bot",
    )
    await page.wait_for_timeout(900)
    return True


async def _click_reply_row_button(page, *, row: Dict[str, Any]) -> bool:
    reply = dict(row.get("reply") or {})
    if not reply:
        return False
    await page.mouse.click(reply["x"], reply["y"])
    queue_current_event(
        "click",
        {
            "method": "comment_row_reply",
            "action_name": "reply_comment",
            "x": reply.get("x"),
            "y": reply.get("y"),
            "reply": reply,
            "author": row.get("author"),
        },
        phase="activation",
        source="reddit_bot",
    )
    await page.wait_for_timeout(900)
    return True


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


async def _fill_comment_input(
    page,
    text: str,
    *,
    reply: bool = False,
    expected_title: Optional[str] = None,
    allow_global_trigger: bool = True,
) -> bool:
    selectors = COMMENT["reply_input"] if reply else COMMENT["composer_input"]
    if await _fill_first(page, selectors, text):
        return True
    if await _active_editable_present(page):
        return await _keyboard_type_and_verify(page, text, reply=reply)
    if not allow_global_trigger:
        return False
    if not await _open_comment_composer(page, expected_title):
        return False
    await page.wait_for_timeout(400)
    if await _fill_first(page, selectors, text):
        return True
    return await _keyboard_type_and_verify(page, text, reply=reply)


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


async def _verify_text_visible(page, text: str) -> bool:
    snippet = _normalize_text(text)[:40]
    try:
        body = _normalize_text(await page.locator("body").inner_text())
    except Exception:
        return False
    return bool(snippet and snippet in body)


async def upvote_post(
    session: RedditSession,
    *,
    url: str,
    proxy_url: Optional[str] = None,
) -> Dict[str, Any]:
    async with _session_page(session, proxy_url) as (_browser, _context, page):
        try:
            await _goto(page, url)
            await _scroll_until_post_actions_visible(page)
            await dump_interactive_elements(page, "REDDIT UPVOTE POST")
            expected_title = await _current_thread_title(page)
            share_locator = await _first_visible_locator(page, COMMENT["share_button"])
            share_box = await share_locator.bounding_box() if share_locator else None
            share_y = float(share_box["y"] + (share_box["height"] / 2)) if share_box else None
            share_left = float(share_box["x"]) if share_box else None
            before_signature = (
                await _capture_row_signature(page, row_y=share_y, max_x=share_left)
                if share_y is not None and share_left is not None
                else []
            )

            if await _verify_named_control_state(
                page,
                needles=["remove upvote", "upvoted"],
                expected_title=expected_title,
                row_y=share_y,
                left_of_x=share_left,
            ):
                screenshot = await save_debug_screenshot(page, f"reddit_upvote_post_{session.profile_name}")
                return _result(
                    success=True,
                    action="upvote_post",
                    profile_name=session.profile_name,
                    screenshot=screenshot,
                    current_url=page.url,
                    verification="already_upvoted",
                )

            clicked = False
            if share_box:
                clicked = await _click_post_upvote_region(page, share_box=share_box)
            if not clicked:
                clicked = await _click_named_control(
                    page,
                    action_name="upvote_post",
                    needles=["upvote"],
                    expected_title=expected_title,
                    row_y=share_y,
                    left_of_x=share_left,
                )
            if not clicked:
                await _capture_reddit_failure_state(page, "REDDIT POST UPVOTE MISSING")
                return _result(
                    success=False,
                    action="upvote_post",
                    profile_name=session.profile_name,
                    error="Reddit post upvote control not found",
                )

            await page.wait_for_timeout(1500)
            screenshot = await save_debug_screenshot(page, f"reddit_upvote_post_{session.profile_name}")
            after_signature = (
                await _capture_row_signature(page, row_y=share_y, max_x=share_left)
                if share_y is not None and share_left is not None
                else []
            )
            success = await _verify_named_control_state(
                page,
                needles=["remove upvote", "upvoted"],
                expected_title=expected_title,
                row_y=share_y,
                left_of_x=share_left,
            )
            if not success:
                success = bool(before_signature and after_signature and before_signature != after_signature)
            return _result(
                success=success,
                action="upvote_post",
                profile_name=session.profile_name,
                screenshot=screenshot,
                current_url=page.url,
                error=None if success else "Reddit post upvote verification failed",
            )
        except Exception as exc:
            return _result(success=False, action="upvote_post", profile_name=session.profile_name, error=str(exc))


async def upvote_comment(
    session: RedditSession,
    *,
    target_comment_url: str,
    proxy_url: Optional[str] = None,
) -> Dict[str, Any]:
    target_context = await _load_target_comment_context(target_comment_url)
    expected_title = (target_context or {}).get("title") or None
    author = (target_context or {}).get("author") or None

    async with _session_page(session, proxy_url) as (_browser, _context, page):
        try:
            await _goto(page, target_comment_url)
            await dump_interactive_elements(page, "REDDIT UPVOTE COMMENT")
            row = await _comment_action_row(page, author=author, expected_title=expected_title)
            reply = dict((row or {}).get("reply") or {})
            before_signature = (
                await _capture_row_signature(page, row_y=float(reply.get("y")), max_x=float(reply.get("left")))
                if reply
                else []
            )

            if await _verify_named_control_state(
                page,
                needles=["remove upvote", "upvoted"],
                anchor_text=author,
                expected_title=expected_title,
                max_vertical_gap=220,
                require_below_anchor=True,
            ):
                screenshot = await save_debug_screenshot(page, f"reddit_upvote_comment_{session.profile_name}")
                return _result(
                    success=True,
                    action="upvote_comment",
                    profile_name=session.profile_name,
                    screenshot=screenshot,
                    current_url=page.url,
                    verification="already_upvoted",
                )

            clicked = await _click_named_control(
                page,
                action_name="upvote_comment",
                needles=["upvote"],
                anchor_text=author,
                expected_title=expected_title,
                max_vertical_gap=220,
                require_below_anchor=True,
            )
            if not clicked and row:
                clicked = await _click_comment_upvote_region(page, row=row)
            if not clicked:
                await _capture_reddit_failure_state(page, "REDDIT COMMENT UPVOTE MISSING")
                raise RuntimeError("Reddit comment upvote control not found")

            await page.wait_for_timeout(1500)
            screenshot = await save_debug_screenshot(page, f"reddit_upvote_comment_{session.profile_name}")
            after_signature = (
                await _capture_row_signature(page, row_y=float(reply.get("y")), max_x=float(reply.get("left")))
                if reply
                else []
            )
            success = await _verify_named_control_state(
                page,
                needles=["remove upvote", "upvoted"],
                anchor_text=author,
                expected_title=expected_title,
                max_vertical_gap=220,
                require_below_anchor=True,
            )
            if not success:
                success = bool(before_signature and after_signature and before_signature != after_signature)
            return _result(
                success=success,
                action="upvote_comment",
                profile_name=session.profile_name,
                screenshot=screenshot,
                current_url=page.url,
                error=None if success else "Reddit comment upvote verification failed",
            )
        except Exception as exc:
            return _result(success=False, action="upvote_comment", profile_name=session.profile_name, error=str(exc))


async def join_subreddit(
    session: RedditSession,
    *,
    url: str,
    proxy_url: Optional[str] = None,
) -> Dict[str, Any]:
    async with _session_page(session, proxy_url) as (_browser, _context, page):
        try:
            await _goto(page, url)
            await dump_interactive_elements(page, "REDDIT JOIN SUBREDDIT")

            if await _visible_selector_exists(page, SUBREDDIT["joined_button"]):
                screenshot = await save_debug_screenshot(page, f"reddit_join_subreddit_{session.profile_name}")
                return _result(
                    success=True,
                    action="join_subreddit",
                    profile_name=session.profile_name,
                    screenshot=screenshot,
                    current_url=page.url,
                    verification="already_joined",
                )

            clicked = await _click_first(page, SUBREDDIT["join_button"], timeout_ms=3000)
            if not clicked:
                clicked = await _click_visible_text_region(
                    page,
                    needle="Join",
                    action_name="join_subreddit",
                    min_top=60,
                    max_top=260,
                )
            if not clicked:
                header_target = await _find_subreddit_header_action(page)
                if header_target:
                    await page.mouse.click(header_target["x"], header_target["y"])
                    queue_current_event(
                        "click",
                        {
                            "method": "subreddit_header_geometry",
                            "action_name": "join_subreddit",
                            "x": header_target.get("x"),
                            "y": header_target.get("y"),
                            "bounds": header_target.get("bounds"),
                        },
                        phase="activation",
                        source="reddit_bot",
                    )
                    await page.wait_for_timeout(900)
                    clicked = True
            if not clicked:
                await _capture_reddit_failure_state(page, "REDDIT JOIN BUTTON MISSING")
                raise RuntimeError("Reddit join button not found")

            await page.wait_for_timeout(1500)
            screenshot = await save_debug_screenshot(page, f"reddit_join_subreddit_{session.profile_name}")
            success = bool(
                await _visible_selector_exists(page, SUBREDDIT["joined_button"])
                or await _verify_named_control_state(page, needles=["joined"])
            )
            return _result(
                success=success,
                action="join_subreddit",
                profile_name=session.profile_name,
                screenshot=screenshot,
                current_url=page.url,
                error=None if success else "Reddit join verification failed",
            )
        except Exception as exc:
            return _result(success=False, action="join_subreddit", profile_name=session.profile_name, error=str(exc))


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


async def _click_reply_submit(page, reply_text: str) -> bool:
    if await _click_first(page, COMMENT["reply_submit_button"], timeout_ms=4000):
        queue_current_event(
            "click",
            {"method": "selector", "target": "reply_submit_button"},
            phase="submit",
            source="reddit_bot",
        )
        await page.wait_for_timeout(500)
        return True
    return await _click_named_control(
        page,
        action_name="reply_submit",
        needles=["reply", "comment"],
        anchor_text=reply_text[:80],
        max_vertical_gap=140,
        require_below_anchor=True,
    )


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
            success = await _verify_text_visible(page, text)
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
    target_comment_url: str,
    text: str,
    proxy_url: Optional[str] = None,
) -> Dict[str, Any]:
    target_context = await _load_target_comment_context(target_comment_url)
    target_url = str((target_context or {}).get("thread_url") or target_comment_url)
    author = (target_context or {}).get("author") or None

    async with _session_page(session, proxy_url) as (_browser, _context, page):
        try:
            await _goto(page, target_url)
            await dump_interactive_elements(page, "REDDIT REPLY TO COMMENT")
            expected_title = await _current_thread_title(page)
            row = await _comment_action_row(page, author=author, expected_title=expected_title)

            clicked_reply = await _click_named_control(
                page,
                action_name="reply_comment",
                needles=["reply"],
                expected_title=expected_title,
                anchor_text=author,
                max_vertical_gap=220,
                require_below_anchor=True,
            )
            if not clicked_reply and row:
                clicked_reply = await _click_reply_row_button(page, row=row)
            if not clicked_reply:
                await _capture_reddit_failure_state(page, "REDDIT REPLY BUTTON MISSING")
                raise RuntimeError("Reddit Reply button not found")
            await page.wait_for_timeout(1000)

            if not await _fill_comment_input(
                page,
                text,
                reply=True,
                expected_title=expected_title,
                allow_global_trigger=False,
            ):
                await _capture_reddit_failure_state(page, "REDDIT REPLY INPUT MISSING")
                raise RuntimeError("Reddit reply input not found")

            if not await _click_reply_submit(page, text):
                await _capture_reddit_failure_state(page, "REDDIT REPLY SUBMIT MISSING")
                raise RuntimeError("Reddit reply submit button not found")

            await page.wait_for_timeout(4000)
            screenshot = await save_debug_screenshot(page, f"reddit_reply_{session.profile_name}")
            success = await _verify_text_visible(page, text)
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
    target_comment_url: Optional[str] = None,
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
            "target_comment_url": target_comment_url,
            "subreddit": subreddit,
            **((forensic_context or {}).get("metadata") or {}),
        },
    )
    recorder_token = set_current_forensic_recorder(recorder)
    result: Dict[str, Any]
    if normalized == "browse_feed":
        result = await browse_feed(session, proxy_url=proxy_url)
    elif normalized in {"upvote", "upvote_post"}:
        if not url:
            result = _result(success=False, action="upvote_post", profile_name=session.profile_name, error="url is required")
        else:
            result = await upvote_post(session, url=url, proxy_url=proxy_url)
    elif normalized == "upvote_comment":
        if not target_comment_url:
            result = _result(success=False, action=normalized, profile_name=session.profile_name, error="target_comment_url is required")
        else:
            result = await upvote_comment(session, target_comment_url=target_comment_url, proxy_url=proxy_url)
    elif normalized == "join_subreddit":
        if not url:
            result = _result(success=False, action=normalized, profile_name=session.profile_name, error="url is required")
        else:
            result = await join_subreddit(session, url=url, proxy_url=proxy_url)
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
        if not target_comment_url or not text:
            result = _result(success=False, action=normalized, profile_name=session.profile_name, error="target_comment_url and text are required")
        else:
            result = await reply_to_comment(session, target_comment_url=target_comment_url, text=text, proxy_url=proxy_url)
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
