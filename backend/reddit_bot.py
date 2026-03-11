"""
Reddit mobile-web executor.
"""

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx
from playwright.async_api import async_playwright

from browser_factory import apply_page_identity_overrides, create_browser_context
from comment_bot import dump_interactive_elements, save_debug_screenshot
from config import REDDIT_MOBILE_USER_AGENT
from reddit_growth_generation import RedditGrowthContentGenerator
from reddit_login_bot import _dismiss_cookie_banner, _goto_with_retry
from reddit_selectors import COMMENT, HOME, POST, SUBREDDIT
from reddit_session import RedditSession
from reddit_subreddit_policies import normalize_subreddit_name
from forensics import (
    attach_current_json_artifact,
    build_generic_verdict,
    get_current_forensic_recorder,
    queue_current_event,
    reset_current_forensic_recorder,
    set_current_forensic_recorder,
    start_forensic_attempt,
)

logger = logging.getLogger("RedditBot")
REDDIT_HTTP_HEADERS = {"User-Agent": "commentfront-reddit-bot/1.0"}
SUBREDDIT_IDENTITY_GENERATOR = RedditGrowthContentGenerator()


class RedditCommunityBanError(RuntimeError):
    """Raised when Reddit shows a subreddit-level comment ban/banner."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


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


def _reddit_username_candidates(*values: Optional[str]) -> List[str]:
    candidates: List[str] = []
    seen = set()
    for value in values:
        normalized = _normalize_text(value)
        if not normalized:
            continue
        variants = [normalized]
        if normalized.startswith("u/"):
            variants.append(normalized[2:])
        if normalized.startswith("reddit_"):
            variants.append(normalized[len("reddit_"):])
        for variant in variants:
            variant = variant.strip()
            if not variant or variant in seen:
                continue
            seen.add(variant)
            candidates.append(variant)
    return candidates


async def _find_created_post_permalink_on_feed(
    page,
    *,
    title: str,
    body: Optional[str],
    actor_username: Optional[str],
    profile_name: Optional[str],
) -> Optional[str]:
    title_needle = _normalize_text(title)
    body_needle = _normalize_text(body)[:80]
    username_needles = _reddit_username_candidates(actor_username, profile_name)
    if not title_needle:
        return None
    try:
        return await page.evaluate(
            """({ titleNeedle, bodyNeedle, usernameNeedles }) => {
                const normalize = (value) =>
                    String(value || "")
                        .toLowerCase()
                        .replace(/\\s+/g, " ")
                        .trim();

                const authorMatches = (article) => {
                    if (!usernameNeedles.length) {
                        return true;
                    }
                    return Array.from(article.querySelectorAll("a, span, div")).some((node) => {
                        const text = normalize(node.innerText || "");
                        const aria = normalize(node.getAttribute("aria-label") || "");
                        return usernameNeedles.some((needle) =>
                            text === needle ||
                            text.includes(`u/${needle}`) ||
                            aria.includes(`author: u/${needle}`) ||
                            aria.includes(`u/${needle}`)
                        );
                    });
                };

                for (const article of Array.from(document.querySelectorAll("article"))) {
                    const articleText = normalize(article.innerText || "");
                    if (!articleText.includes(titleNeedle)) {
                        continue;
                    }
                    if (bodyNeedle && !articleText.includes(bodyNeedle)) {
                        continue;
                    }
                    if (!authorMatches(article)) {
                        continue;
                    }
                    const permalink = Array.from(article.querySelectorAll("a[href]"))
                        .map((node) => node.getAttribute("href") || "")
                        .find((href) => href.includes("/comments/"));
                    if (permalink) {
                        return new URL(permalink, window.location.origin).toString();
                    }
                }
                return null;
            }""",
            {
                "titleNeedle": title_needle,
                "bodyNeedle": body_needle,
                "usernameNeedles": username_needles,
            },
        )
    except Exception:
        return None


async def _detect_community_comment_ban(page) -> Optional[str]:
    try:
        body_text = _normalize_text(await page.locator("body").inner_text())
    except Exception:
        return None
    patterns = [
        ("you're currently banned from this community and can't comment on posts", "reddit community ban: can't comment on posts"),
        ("you are currently banned from this community and can't comment on posts", "reddit community ban: can't comment on posts"),
        ("you’re currently banned from this community and can’t comment on posts", "reddit community ban: can't comment on posts"),
        ("you are currently banned from this community and can’t comment on posts", "reddit community ban: can't comment on posts"),
        ("you've been banned from contributing to this community", "reddit community ban: can't contribute to community"),
        ("you have been banned from contributing to this community", "reddit community ban: can't contribute to community"),
        ("you’ve been banned from contributing to this community", "reddit community ban: can't contribute to community"),
        ("banned from contributing to this community", "reddit community ban: can't contribute to community"),
    ]
    for needle, reason in patterns:
        if needle in body_text:
            return reason
    return None


async def _raise_if_community_comment_banned(page, *, capture_context: str) -> None:
    reason = await _detect_community_comment_ban(page)
    if not reason:
        return
    await _capture_reddit_failure_state(page, capture_context)
    raise RedditCommunityBanError(reason)


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


def _set_query_params(url: str, **updates: Optional[str]) -> str:
    split = urlsplit(str(url or "").strip())
    query = {key: value for key, value in parse_qsl(split.query, keep_blank_values=True)}
    for key, value in updates.items():
        if value is None:
            query.pop(key, None)
        else:
            query[key] = str(value)
    return urlunsplit((split.scheme, split.netloc, split.path, urlencode(query), split.fragment))


def _canonical_reply_comment_url(target_comment_url: str, thread_url: Optional[str]) -> Optional[str]:
    comment_id = _extract_reddit_comment_id(target_comment_url)
    if not comment_id:
        return None
    base = str(thread_url or target_comment_url or "").split("?", 1)[0].strip().rstrip("/")
    if not base:
        return None
    return f"{base}/comment/{comment_id}/"


def _build_reply_target_surfaces(target_comment_url: str, thread_url: Optional[str]) -> List[str]:
    surfaces: List[str] = []
    seen = set()

    def add(value: Optional[str]) -> None:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        surfaces.append(normalized)

    comment_id = _extract_reddit_comment_id(target_comment_url)
    normalized_thread = str(thread_url or "").strip()
    add(target_comment_url)
    add(_canonical_reply_comment_url(target_comment_url, normalized_thread))
    if normalized_thread and comment_id:
        add(_set_query_params(normalized_thread, comment=comment_id, context="3"))
        split = urlsplit(normalized_thread)
        parts = [segment for segment in split.path.split("/") if segment]
        if "comments" in parts:
            idx = parts.index("comments")
            if idx + 1 < len(parts):
                post_id = parts[idx + 1]
                add(urlunsplit((split.scheme, split.netloc, f"/comments/{post_id}/_/comment/{comment_id}/", "context=3", "")))
    add(normalized_thread)
    return surfaces


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


async def _load_post_context(target_url: str) -> Optional[Dict[str, Any]]:
    try:
        async with httpx.AsyncClient(
            headers=REDDIT_HTTP_HEADERS,
            follow_redirects=True,
            timeout=20.0,
        ) as client:
            response = await client.get(_reddit_json_url(target_url))
            response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning(f"failed to fetch reddit post context for {target_url}: {exc}")
        return {"thread_url": target_url, "title": None}

    try:
        post = payload[0]["data"]["children"][0]["data"]
    except Exception:
        post = {}

    permalink = str(post.get("permalink") or "").strip()
    return {
        "thread_url": f"https://www.reddit.com{permalink}" if permalink else target_url,
        "title": str(post.get("title") or "").strip() or None,
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


async def _collect_control_candidates(
    page,
    needles: List[str],
    *,
    max_text_length: Optional[int] = None,
) -> List[Dict[str, Any]]:
    try:
        result = await page.evaluate(
            """(needles, maxTextLength) => {
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
                const selector = 'button,[role=\"button\"],[role=\"textbox\"],a,input,textarea,[contenteditable=\"true\"],[contenteditable=\"plaintext-only\"],[aria-label],[placeholder]';
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
                        if (maxTextLength && combined.length > maxTextLength) continue;
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
            int(max_text_length) if max_text_length else None,
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
    max_text_length: Optional[int] = None,
) -> bool:
    candidates = await _collect_control_candidates(page, needles, max_text_length=max_text_length)
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
    max_text_length: Optional[int] = None,
) -> bool:
    candidates = await _collect_control_candidates(page, needles, max_text_length=max_text_length)
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
        typed = bool(await _typed_text_visible(page, text))
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


async def _typed_text_visible(page, text: str) -> bool:
    try:
        return bool(
            await page.evaluate(
                """(needle) => {
                    const probe = (value) => (value || '').toLowerCase();
                    const target = probe(String(needle).slice(0, 40));
                    const active = document.activeElement;
                    if (active) {
                        const activeText = probe(active.value || active.textContent || active.innerText);
                        if (activeText.includes(target)) return true;
                    }
                    const bodyText = probe(document.body ? document.body.innerText : '');
                    return bodyText.includes(target);
                }""",
                text,
            )
        )
    except Exception:
        return False


async def _active_editable_present(page) -> bool:
    try:
        return bool(
            await page.evaluate(
                """() => {
                    const active = document.activeElement;
                    if (!active) return false;
                    const tag = String(active.tagName || '').toLowerCase();
                    const role = String((active.getAttribute && active.getAttribute('role')) || '').toLowerCase();
                    const contenteditable = String((active.getAttribute && active.getAttribute('contenteditable')) || '').toLowerCase();
                    return Boolean(
                        active.isContentEditable ||
                        role === 'textbox' ||
                        contenteditable === 'true' ||
                        contenteditable === 'plaintext-only' ||
                        tag === 'textarea' ||
                        (tag === 'input' && String(active.type || '').toLowerCase() !== 'hidden')
                    );
                }"""
            )
        )
    except Exception:
        return False


async def _fill_post_field_by_semantics(page, *, kind: str, value: str) -> bool:
    normalized_kind = _normalize_text(kind)
    if normalized_kind not in {"title", "body"}:
        return False
    try:
        result = await page.evaluate(
            """({ kind, value }) => {
                const normalize = (input) => String(input || '').toLowerCase().replace(/\\s+/g, ' ').trim();
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

                const visible = (rect) =>
                    rect &&
                    rect.width >= 8 &&
                    rect.height >= 8 &&
                    rect.bottom >= 0 &&
                    rect.right >= 0 &&
                    rect.top <= (window.innerHeight || 873) &&
                    rect.left <= (window.innerWidth || 393);
                const tokens = {
                    title: ['title', 'headline', 'subject'],
                    body: ['post body', 'body', 'content', 'text field', 'rich text', 'what are your thoughts'],
                };
                const rejects = {
                    title: ['body', 'content', 'comment', 'reply'],
                    body: ['title', 'headline', 'subject'],
                };
                const candidates = [];
                for (const root of roots) {
                    const nodes = root.querySelectorAll
                        ? Array.from(root.querySelectorAll('input, textarea, [role="textbox"], [contenteditable="true"], [contenteditable="plaintext-only"]'))
                        : [];
                    for (const node of nodes) {
                        const rect = node.getBoundingClientRect();
                        if (!visible(rect)) continue;
                        if (node.disabled || node.readOnly) continue;
                        const text = normalize(node.innerText || node.textContent || node.value);
                        const aria = normalize(node.getAttribute && node.getAttribute('aria-label'));
                        const placeholder = normalize(node.getAttribute && node.getAttribute('placeholder'));
                        const name = normalize(node.getAttribute && node.getAttribute('name'));
                        const role = normalize(node.getAttribute && node.getAttribute('role'));
                        const className = normalize(node.className);
                        const id = normalize(node.id);
                        const combined = [text, aria, placeholder, name, role, className, id].filter(Boolean).join(' | ');
                        let score = 0;
                        for (const token of tokens[kind] || []) {
                            if (combined.includes(token)) score += token === kind ? 8 : 5;
                        }
                        for (const token of rejects[kind] || []) {
                            if (combined.includes(token)) score -= 6;
                        }
                        if (node.isContentEditable) score += kind === 'body' ? 4 : 0;
                        if (role === 'textbox') score += 1;
                        if (node.tagName === 'TEXTAREA') score += 1;
                        candidates.push({ node, rect, score, combined, tag: String(node.tagName || '').toLowerCase() });
                    }
                }
                candidates.sort((a, b) => b.score - a.score || a.rect.top - b.rect.top || a.rect.left - b.rect.left);
                const target = candidates[0];
                if (!target) return null;
                const node = target.node;
                node.scrollIntoView({ block: 'center', inline: 'nearest' });
                if (node.focus) node.focus();
                const assignNativeValue = (element, nextValue) => {
                    if (element instanceof HTMLInputElement) {
                        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
                        if (setter) setter.call(element, nextValue);
                        else element.value = nextValue;
                        return true;
                    }
                    if (element instanceof HTMLTextAreaElement) {
                        const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
                        if (setter) setter.call(element, nextValue);
                        else element.value = nextValue;
                        return true;
                    }
                    return false;
                };
                if (assignNativeValue(node, value)) {
                    node.dispatchEvent(new Event('input', { bubbles: true }));
                    node.dispatchEvent(new Event('change', { bubbles: true }));
                } else if (node.isContentEditable) {
                    node.textContent = value;
                    node.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
                    node.dispatchEvent(new Event('change', { bubbles: true }));
                } else {
                    return null;
                }
                return {
                    kind,
                    combined: target.combined,
                    score: target.score,
                    tag: target.tag,
                };
            }""",
            {"kind": normalized_kind, "value": value},
        )
    except Exception:
        return False
    if not result:
        return False
    queue_current_event(
        "type",
        {
            "method": "semantic_editable_fill",
            "target": normalized_kind,
            "matched": result.get("combined"),
            "score": result.get("score"),
            "tag": result.get("tag"),
            "length": len(value or ""),
        },
        phase="typing",
        source="reddit_bot",
    )
    await page.wait_for_timeout(500)
    return bool(await _typed_text_visible(page, value))


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


async def _dismiss_reddit_open_app_sheet(page) -> bool:
    try:
        dismissal_payload = await page.evaluate(
                """() => {
                    const viewportHeight = window.innerHeight || 873;
                    const viewportWidth = window.innerWidth || 393;
                    const visible = (rect) => rect && rect.width >= 16 && rect.height >= 16 && rect.bottom >= 0 && rect.right >= 0 && rect.top <= viewportHeight;
                    const nodes = Array.from(document.querySelectorAll('button, a, [role="button"]'));
                    const openCta = nodes.find((node) => {
                        const text = String(node.innerText || node.textContent || node.getAttribute?.('aria-label') || '').trim().toLowerCase();
                        const rect = node.getBoundingClientRect();
                        return visible(rect) && rect.top >= viewportHeight - 120 && text === 'open';
                    });
                    if (!openCta) return { dismissed: false };

                    const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                    const candidateContainers = [];
                    let current = openCta.parentElement;
                    while (current) {
                        const rect = current.getBoundingClientRect();
                        const text = normalize(current.innerText || current.textContent || current.getAttribute?.('aria-label'));
                        const computed = window.getComputedStyle(current);
                        const looksLikeSheet = (
                            rect.width >= viewportWidth * 0.7 &&
                            rect.height >= 56 &&
                            rect.top >= viewportHeight - 220 &&
                            rect.bottom <= viewportHeight + 8 &&
                            (computed.position === 'fixed' || computed.position === 'sticky' || rect.bottom >= viewportHeight - 4) &&
                            text.includes('view in reddit app')
                        );
                        if (looksLikeSheet) {
                            candidateContainers.push(current);
                            break;
                        }
                        current = current.parentElement;
                    }
                    if (!candidateContainers.length) return { dismissed: false };
                    const container = candidateContainers[0];

                    const closeButton = nodes.find((node) => {
                        const text = String(node.innerText || node.textContent || node.getAttribute?.('aria-label') || '').trim().toLowerCase();
                        const rect = node.getBoundingClientRect();
                        if (!visible(rect) || rect.top < viewportHeight - 160 || rect.left > 72 || rect.width > 56 || rect.height > 56 || text) {
                            return false;
                        }
                        return container.contains(node);
                    });
                    if (!closeButton) return { dismissed: false };
                    closeButton.click();
                    return { dismissed: true };
                }"""
            )
    except Exception:
        dismissal_payload = {"dismissed": False}
    dismissed = bool(dismissal_payload if isinstance(dismissal_payload, bool) else (dismissal_payload or {}).get("dismissed"))
    if dismissed:
        queue_current_event(
            "click",
            {"method": "open_app_sheet_dismiss"},
            phase="activation",
            source="reddit_bot",
        )
        await page.wait_for_timeout(700)
    return dismissed


async def _fill_first(page, selectors, value: str) -> bool:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count() > 0 and await locator.is_visible():
                try:
                    await locator.fill(value)
                    return True
                except Exception:
                    await locator.click()
                    await page.wait_for_timeout(300)
                    if await _keyboard_type_and_verify(page, value):
                        return True
        except Exception:
            continue
    return False


async def _post_requires_flair(page) -> bool:
    try:
        body = (await page.locator("body").inner_text()).lower()
    except Exception:
        return False
    return "post must contain post flair" in body or "add post flair" in body


async def _click_first_post_flair_option(page) -> bool:
    try:
        target = await page.evaluate(
            """() => {
                const normalize = (value) => String(value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
                const viewportWidth = window.innerWidth || 393;
                const viewportHeight = window.innerHeight || 873;
                const visible = (rect) =>
                    rect &&
                    rect.width >= 18 &&
                    rect.height >= 18 &&
                    rect.bottom >= 0 &&
                    rect.right >= 0 &&
                    rect.top <= viewportHeight &&
                    rect.left <= viewportWidth;
                const banned = new Set([
                    'add flair and tags',
                    'add flair',
                    'apply',
                    'save',
                    'done',
                    'cancel',
                    'close',
                    'back',
                    'post',
                    'create post',
                    'drafts',
                    'text',
                    'images & video',
                    'link',
                    'ama',
                    'nsfw',
                    'spoiler',
                    'schedule',
                    'save draft',
                ]);
                const nodes = Array.from(document.querySelectorAll('button, [role="button"], [role="option"], label, a, div'));
                const candidates = [];
                for (const node of nodes) {
                    const rect = node.getBoundingClientRect();
                    if (!visible(rect)) continue;
                    const text = normalize(node.innerText || node.textContent);
                    const aria = normalize(node.getAttribute && node.getAttribute('aria-label'));
                    const combined = text || aria;
                    if (!combined || banned.has(combined)) continue;
                    if (combined.includes('add flair') || combined.includes('create post') || combined.includes('save draft')) continue;
                    if (rect.top < 120) continue;
                    let score = 0;
                    if (node.getAttribute && node.getAttribute('role') === 'option') score += 6;
                    if (node.tagName === 'BUTTON') score += 4;
                    if (rect.width >= 90 && rect.height >= 28) score += 3;
                    if (combined.length <= 40) score += 2;
                    if (combined.includes('advice') || combined.includes('question') || combined.includes('discussion')) score += 2;
                    candidates.push({
                        x: Math.round(rect.left + rect.width / 2),
                        y: Math.round(rect.top + rect.height / 2),
                        text: combined,
                        score,
                        top: rect.top,
                    });
                }
                candidates.sort((a, b) => b.score - a.score || a.top - b.top);
                return candidates[0] || null;
            }"""
        )
    except Exception:
        return False
    if not target:
        return False
    await page.mouse.click(float(target["x"]), float(target["y"]))
    queue_current_event(
        "click",
        {
            "method": "flair_option_geometry",
            "action_name": "create_post_flair_option",
            "x": target.get("x"),
            "y": target.get("y"),
            "matched": target.get("text"),
        },
        phase="activation",
        source="reddit_bot",
    )
    await page.wait_for_timeout(700)
    return True


async def _ensure_post_flair(page) -> bool:
    if not await _post_requires_flair(page):
        return True
    opened = await _click_first(page, POST["flair_button"], timeout_ms=4000)
    if not opened:
        opened = await _click_visible_text_region(
            page,
            needle="Add flair and tags",
            action_name="create_post_flair_open",
            min_top=120,
        )
    if not opened:
        return False
    await page.wait_for_timeout(900)
    selected = await _click_first_post_flair_option(page)
    if not selected:
        return False
    applied = await _click_first(page, POST["flair_apply_button"], timeout_ms=3000)
    if not applied:
        applied = await _click_visible_text_region(
            page,
            needle="Apply",
            action_name="create_post_flair_apply",
            min_top=120,
        )
    if not applied:
        applied = await _click_visible_text_region(
            page,
            needle="Save",
            action_name="create_post_flair_save",
            min_top=120,
        )
    await page.wait_for_timeout(1200)
    return not await _post_requires_flair(page)


def _box_in_viewport(page, box: Optional[Dict[str, float]]) -> bool:
    if not box:
        return False
    viewport = getattr(page, "viewport_size", None) or {"width": 393, "height": 873}
    left = float(box.get("x") or 0.0)
    top = float(box.get("y") or 0.0)
    width = float(box.get("width") or 0.0)
    height = float(box.get("height") or 0.0)
    right = left + width
    bottom = top + height
    return bool(width >= 6 and height >= 6 and bottom >= 0 and right >= 0 and top <= viewport["height"] and left <= viewport["width"])


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


async def _first_viewport_locator(page, selectors):
    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = await locator.count()
            for idx in range(min(count, 8)):
                candidate = locator.nth(idx)
                if not await candidate.is_visible():
                    continue
                box = await candidate.bounding_box()
                if _box_in_viewport(page, box):
                    return candidate
            if count > 0:
                candidate = locator.first
                if await candidate.is_visible():
                    await candidate.scroll_into_view_if_needed()
                    box = await candidate.bounding_box()
                    if _box_in_viewport(page, box):
                        return candidate
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


async def _ensure_thread_context(page, *, url: str, expected_title: Optional[str]) -> bool:
    if await _thread_context_present(page, expected_title):
        return True
    for _attempt in range(2):
        try:
            await _goto(page, url)
        except Exception:
            continue
        if await _thread_context_present(page, expected_title):
            return True
    return False


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


def _infer_subreddit_from_url(url: Optional[str]) -> Optional[str]:
    value = str(url or "").strip()
    match = re.search(r"/r/([^/]+)/", value, re.IGNORECASE)
    return str(match.group(1) or "").strip() if match else None


def _subreddit_root_url(subreddit: Optional[str]) -> Optional[str]:
    normalized = str(subreddit or "").strip().lstrip("r/").strip("/")
    if not normalized:
        return None
    return f"https://www.reddit.com/r/{quote(normalized)}/"


async def _body_mentions_user_flair_requirement(page) -> bool:
    try:
        body = str(await page.locator("body").inner_text()).lower()
    except Exception:
        return False
    signals = [
        "user flair",
        "change user flair",
        "choose user flair",
        "set your user flair",
        "must have user flair",
    ]
    return any(signal in body for signal in signals)


async def _open_subreddit_community_menu(page) -> bool:
    action = await _find_subreddit_header_action(page)
    if action:
        await page.mouse.click(float(action["x"]), float(action["y"]))
        queue_current_event(
            "click",
            {
                "method": "subreddit_header_action",
                "action_name": "subreddit_community_menu",
                "x": action.get("x"),
                "y": action.get("y"),
            },
            phase="activation",
            source="reddit_bot",
        )
        await page.wait_for_timeout(900)
        return True
    return await _click_named_control(
        page,
        action_name="subreddit_community_menu_fallback",
        needles=["community actions", "more actions", "more"],
    )


async def _open_user_flair_dialog(page) -> bool:
    if not await _open_subreddit_community_menu(page):
        return False
    opened = await _click_named_control(
        page,
        action_name="subreddit_open_user_flair",
        needles=["change user flair", "add user flair", "user flair", "edit user flair"],
        max_text_length=96,
    )
    if not opened:
        opened = await _click_visible_text_region(
            page,
            needle="user flair",
            action_name="subreddit_open_user_flair_text",
            min_top=80,
        )
    if not opened:
        return False
    await page.wait_for_timeout(900)
    if not await _verify_named_control_state(
        page,
        needles=["view all flair", "show my user flair in this community", "apply", "save", "done"],
        max_text_length=96,
    ):
        return False
    if await _verify_named_control_state(page, needles=["view all flair"], max_text_length=96):
        if not await _click_named_control(
            page,
            action_name="subreddit_view_all_flair",
            needles=["view all flair"],
            max_text_length=96,
        ):
            await _click_visible_text_region(
                page,
                needle="view all flair",
                action_name="subreddit_view_all_flair_text",
                min_top=80,
            )
        await page.wait_for_timeout(900)
    return True


async def _collect_user_flair_options(page) -> Dict[str, Any]:
    try:
        return await page.evaluate(
            """() => {
                const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                const lowered = (value) => normalize(value).toLowerCase();
                const viewportWidth = window.innerWidth || 393;
                const viewportHeight = window.innerHeight || 873;
                const visible = (rect) =>
                    rect && rect.width >= 18 && rect.height >= 18 && rect.bottom >= 0 && rect.right >= 0 && rect.top <= viewportHeight && rect.left <= viewportWidth;
                const banned = [
                    'add user flair',
                    'change user flair',
                    'edit user flair',
                    'view all flair',
                    'apply',
                    'save',
                    'done',
                    'cancel',
                    'close',
                    'back',
                    'show my user flair in this community',
                ];
                const nodes = Array.from(document.querySelectorAll('button, [role="button"], [role="radio"], [role="option"], label, li, div'));
                const options = [];
                const seen = new Set();
                let current = null;
                for (const node of nodes) {
                    const rect = node.getBoundingClientRect();
                    if (!visible(rect)) continue;
                    const text = normalize(node.innerText || node.textContent);
                    const aria = normalize(node.getAttribute && node.getAttribute('aria-label'));
                    const combined = text || aria;
                    const loweredCombined = lowered(combined);
                    if (!combined || banned.includes(loweredCombined)) continue;
                    if (loweredCombined.length < 2 || loweredCombined.length > 80) continue;
                    if (seen.has(loweredCombined)) continue;
                    const selected =
                        node.getAttribute('aria-checked') === 'true' ||
                        node.getAttribute('aria-selected') === 'true' ||
                        node.getAttribute('data-testid') === 'user-flair-selected';
                    if (selected) current = combined;
                    seen.add(loweredCombined);
                    options.push(combined);
                }
                return { options, current_flair: current };
            }"""
        )
    except Exception:
        return {"options": [], "current_flair": None}


async def _select_user_flair_option(page, option_text: str) -> bool:
    for attempt in range(2):
        if await _click_named_control(
            page,
            action_name="subreddit_select_user_flair",
            needles=[option_text],
            max_text_length=96,
        ):
            await page.wait_for_timeout(700)
            return True
        if await _click_visible_text_region(
            page,
            needle=option_text.lower(),
            action_name="subreddit_select_user_flair_text",
            min_top=80,
        ):
            await page.wait_for_timeout(700)
            return True
        if attempt == 0:
            await page.evaluate("window.scrollBy(0, window.innerHeight * 0.55)")
            await page.wait_for_timeout(600)
    return False


async def _ensure_user_flair_visibility_toggle(page) -> bool:
    try:
        toggled = await page.evaluate(
            """() => {
                const normalize = (value) => String(value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
                const label = Array.from(document.querySelectorAll('button, [role="button"], [role="switch"], label, div')).find((node) => {
                    const text = normalize(node.innerText || node.textContent);
                    const aria = normalize(node.getAttribute && node.getAttribute('aria-label'));
                    return text.includes('show my user flair in this community') || aria.includes('show my user flair in this community');
                });
                if (!label) return { found: false, changed: false };
                const target = label.closest('label, button, [role="button"], [role="switch"]') || label;
                const input = target.querySelector && target.querySelector('input[type="checkbox"]');
                const ariaChecked = target.getAttribute && target.getAttribute('aria-checked');
                const alreadyOn = (input && input.checked) || ariaChecked === 'true';
                if (alreadyOn) return { found: true, changed: false };
                target.click();
                return { found: true, changed: true };
            }"""
        )
    except Exception:
        return False
    await page.wait_for_timeout(500)
    return bool((toggled or {}).get("found"))


async def _confirm_user_flair_dialog(page) -> bool:
    if await _click_named_control(
        page,
        action_name="subreddit_apply_user_flair",
        needles=["apply", "save", "done"],
        max_text_length=48,
    ):
        return True
    return await _click_visible_text_region(
        page,
        needle="apply",
        action_name="subreddit_apply_user_flair_text",
        min_top=80,
    )


async def _ensure_subreddit_user_flair(
    page,
    session: RedditSession,
    *,
    subreddit: Optional[str],
    action: str,
    desired_flair: Optional[str] = None,
    auto_user_flair: bool = False,
    preferred_url: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_subreddit = str(subreddit or "").strip()
    if not normalized_subreddit or not auto_user_flair:
        return {
            "subreddit": normalized_subreddit or None,
            "action": action,
            "auto_user_flair": bool(auto_user_flair),
            "status": "skipped",
        }

    cached = session.get_subreddit_identity_state(normalized_subreddit) if hasattr(session, "get_subreddit_identity_state") else {}
    cached_flair = str((cached or {}).get("user_flair") or "").strip() or None
    if cached_flair and not desired_flair:
        return {
            "subreddit": normalized_subreddit,
            "action": action,
            "auto_user_flair": True,
            "status": "cached",
            "chosen_flair": cached_flair,
            "available_options": list((cached or {}).get("available_options") or []),
        }

    root_url = _subreddit_root_url(normalized_subreddit)
    if not root_url:
        return {"subreddit": normalized_subreddit, "action": action, "auto_user_flair": True, "status": "missing_subreddit"}
    attempted_urls: List[str] = []
    navigation_errors: List[str] = []
    entry_urls: List[str] = []
    normalized_preferred = str(preferred_url or "").strip()
    if normalized_preferred and normalize_subreddit_name(normalized_preferred).lower() == normalized_subreddit.lower():
        entry_urls.append(normalized_preferred)
    if root_url not in entry_urls:
        entry_urls.append(root_url)

    dialog_opened = False
    for entry_url in entry_urls:
        attempted_urls.append(entry_url)
        try:
            await _goto(page, entry_url)
            await page.wait_for_timeout(800)
        except Exception as exc:
            navigation_errors.append(f"{entry_url}: {exc}")
            continue
        if await _open_user_flair_dialog(page):
            dialog_opened = True
            break

    if not dialog_opened:
        if navigation_errors and len(navigation_errors) == len(attempted_urls):
            raise RuntimeError("; ".join(navigation_errors))
        return {
            "subreddit": normalized_subreddit,
            "action": action,
            "auto_user_flair": True,
            "status": "dialog_unavailable",
            "attempted_urls": attempted_urls,
            "navigation_errors": navigation_errors,
        }

    options_payload = await _collect_user_flair_options(page)
    available_options = list(options_payload.get("options") or [])
    current_flair = str(options_payload.get("current_flair") or "").strip() or cached_flair
    choice_bundle = None
    chosen_flair = str(desired_flair or "").strip() or current_flair
    if not chosen_flair and available_options:
        choice_bundle = await SUBREDDIT_IDENTITY_GENERATOR.choose_user_flair(
            profile_name=session.profile_name,
            subreddit=normalized_subreddit,
            available_options=available_options,
            current_flair=current_flair,
        )
        chosen_flair = str((choice_bundle or {}).get("choice") or "").strip()

    if not chosen_flair:
        return {
            "subreddit": normalized_subreddit,
            "action": action,
            "auto_user_flair": True,
            "status": "no_option",
            "available_options": available_options,
            "current_flair": current_flair,
        }

    selected = await _select_user_flair_option(page, chosen_flair)
    if not selected:
        return {
            "subreddit": normalized_subreddit,
            "action": action,
            "auto_user_flair": True,
            "status": "selection_failed",
            "available_options": available_options,
            "current_flair": current_flair,
            "chosen_flair": chosen_flair,
        }

    await _ensure_user_flair_visibility_toggle(page)
    applied = await _confirm_user_flair_dialog(page)
    await page.wait_for_timeout(1200)
    identity_evidence = {
        "subreddit": normalized_subreddit,
        "action": action,
        "auto_user_flair": True,
        "status": "applied" if applied else "apply_unconfirmed",
        "available_options": available_options,
        "current_flair": current_flair,
        "chosen_flair": chosen_flair,
        "reasoning": (choice_bundle or {}).get("reasoning"),
        "persona_id": ((choice_bundle or {}).get("persona_snapshot") or {}).get("persona_id"),
    }
    await attach_current_json_artifact(
        "subreddit_identity",
        "subreddit-identity.json",
        identity_evidence,
        metadata={"subreddit": normalized_subreddit, "action": action},
    )
    if hasattr(session, "update_subreddit_identity_state"):
        session.update_subreddit_identity_state(
            normalized_subreddit,
            {
                "user_flair": chosen_flair,
                "available_options": available_options,
                "updated_at": datetime.utcnow().isoformat(),
            },
        )
    return identity_evidence


async def _comment_action_row(
    page,
    *,
    target_comment_url: Optional[str] = None,
    author: Optional[str],
    expected_title: Optional[str] = None,
    body_snippet: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    normalized_author = _normalize_text(author)
    normalized_body_snippet = _normalize_text(body_snippet)
    comment_id = _extract_reddit_comment_id(target_comment_url)
    try:
        result = await page.evaluate(
            """({ commentId, author, expectedTitle, bodySnippet }) => {
                const normalize = (value) => String(value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
                const viewportWidth = window.innerWidth || 393;
                const viewportHeight = window.innerHeight || 873;
                const visibleRect = (rect) => rect && rect.width >= 6 && rect.height >= 6 && rect.bottom >= 0 && rect.right >= 0 && rect.top <= viewportHeight && rect.left <= viewportWidth;
                const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
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
                const preferredRoots = [];

                const commentNeedle = normalize(commentId);
                if (commentNeedle) {
                    const commentNodes = [];
                    for (const root of roots) {
                        const nodes = root.querySelectorAll ? Array.from(root.querySelectorAll('a, button, div, span, p, article, section')) : [];
                        for (const node of nodes) {
                            const href = normalize(node.getAttribute && node.getAttribute('href'));
                            const text = normalize(node.innerText || node.textContent);
                            const aria = normalize(node.getAttribute && node.getAttribute('aria-label'));
                            if (!href && !text && !aria) continue;
                            if (
                                (href && href.includes(commentNeedle)) ||
                                (text && text.includes(commentNeedle)) ||
                                (aria && aria.includes(commentNeedle))
                            ) {
                                commentNodes.push(node);
                            }
                        }
                    }
                    for (const node of commentNodes) {
                        let current = node;
                        while (current && current !== document.body) {
                            if (!current.querySelectorAll) {
                                current = current.parentElement;
                                continue;
                            }
                            const replyNodes = Array.from(current.querySelectorAll('button, a, div, span')).filter((candidate) => {
                                const tag = normalize(candidate.tagName);
                                const text = normalize(candidate.innerText || candidate.textContent);
                                const aria = normalize(candidate.getAttribute && candidate.getAttribute('aria-label'));
                                const rect = candidate.getBoundingClientRect();
                                if (!visibleRect(rect)) return false;
                                if (tag !== 'button' && !aria.includes('reply')) return false;
                                return text === 'reply' || aria === 'reply';
                            });
                            if (replyNodes.length) {
                                preferredRoots.push(current);
                                break;
                            }
                            current = current.parentElement;
                        }
                        if (preferredRoots.length) break;
                    }
                }

                const scopedRoots = preferredRoots.length ? preferredRoots : roots;

                let titleRect = null;
                const titleNeedle = normalize(expectedTitle);
                if (titleNeedle) {
                    for (const root of scopedRoots) {
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
                for (const root of scopedRoots) {
                    const nodes = root.querySelectorAll ? Array.from(root.querySelectorAll('button, a, div, span')) : [];
                    for (const node of nodes) {
                        const tag = normalize(node.tagName);
                        const text = normalize(node.innerText || node.textContent);
                        const aria = normalize(node.getAttribute && node.getAttribute('aria-label'));
                        const rect = node.getBoundingClientRect();
                        if (!visibleRect(rect)) continue;
                        if (titleRect && rect.top <= titleRect.bottom + 18) continue;
                        if (tag !== 'button' && !aria.includes('reply')) continue;
                        if (text !== 'reply' && aria !== 'reply') continue;
                        if (rect.width > 140 || rect.height > 44) continue;
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

                let authorRect = null;
                const authors = [];
                for (const root of scopedRoots) {
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
                let bodyRect = null;
                const snippetNeedle = normalize(bodySnippet);
                if (snippetNeedle) {
                    const bodyCandidates = [];
                    for (const root of scopedRoots) {
                        const nodes = root.querySelectorAll ? Array.from(root.querySelectorAll('p, div, span')) : [];
                        for (const node of nodes) {
                            const text = normalize(node.innerText || node.textContent);
                            const rect = node.getBoundingClientRect();
                            if (!visibleRect(rect) || !text) continue;
                            if (!text.includes(snippetNeedle) && !snippetNeedle.includes(text)) continue;
                            if (authorRect && rect.top < authorRect.bottom - 12) continue;
                            bodyCandidates.push({
                                left: rect.left,
                                top: rect.top,
                                right: rect.right,
                                bottom: rect.bottom,
                                width: rect.width,
                                height: rect.height,
                                x: Math.round(rect.left + rect.width / 2),
                                y: Math.round(rect.top + rect.height / 2),
                                text,
                            });
                        }
                    }
                    bodyCandidates.sort((a, b) => a.top - b.top || a.left - b.left);
                    bodyRect = bodyCandidates[0] || null;
                }
                const actionAnchor = bodyRect || authorRect;
                if (!reply && !actionAnchor) return null;
                const voteY = reply
                    ? reply.y
                    : Math.round(Math.max(24, Math.min(viewportHeight - 24, ((actionAnchor && actionAnchor.bottom) || 0) + 22)));
                const preferredVoteX = reply
                    ? Math.round(
                        authorRect
                            ? clamp(authorRect.left - 8, 28, 96)
                            : clamp(reply.left - 68, 28, 96)
                    )
                    : Math.round(Math.max(26, Math.min(64, (((actionAnchor && actionAnchor.left) || 0) - 10))));
                const voteCandidates = [];
                for (const candidateX of [
                    preferredVoteX,
                    reply ? Math.round(clamp(reply.left - 68, 28, 96)) : null,
                    reply ? Math.round(clamp(reply.left - 56, 28, 108)) : null,
                    reply ? Math.round(clamp(reply.left - 80, 28, 96)) : null,
                ]) {
                    if (typeof candidateX !== 'number') continue;
                    if (voteCandidates.some((entry) => Math.abs(entry.x - candidateX) <= 4)) continue;
                    voteCandidates.push({ x: candidateX, y: voteY });
                }
                return {
                    author: authorRect,
                    body: bodyRect,
                    reply,
                    vote: {
                        x: preferredVoteX,
                        y: voteY,
                    },
                    voteCandidates,
                };
            }""",
            {
                "commentId": comment_id or "",
                "author": normalized_author,
                "expectedTitle": expected_title or "",
                "bodySnippet": normalized_body_snippet or "",
            },
        )
    except Exception:
        return None
    return result or None


async def _scroll_target_comment_into_view(
    page,
    *,
    target_comment_url: str,
    author: Optional[str],
    expected_title: Optional[str] = None,
    body_snippet: Optional[str] = None,
    max_scrolls: int = 18,
) -> Optional[Dict[str, Any]]:
    row = await _comment_action_row(
        page,
        target_comment_url=target_comment_url,
        author=author,
        expected_title=expected_title,
        body_snippet=body_snippet,
    )
    if row:
        return row

    comment_id = _extract_reddit_comment_id(target_comment_url)
    normalized_author = _normalize_text(author)
    normalized_snippet = _normalize_text(body_snippet)
    for _ in range(max(1, max_scrolls)):
        try:
            located = await page.evaluate(
                """({ commentId, author, snippet }) => {
                    const normalize = (value) => String(value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
                    const viewportHeight = window.innerHeight || 873;
                    const visible = (rect) => rect && rect.width >= 6 && rect.height >= 6 && rect.bottom >= 0 && rect.right >= 0 && rect.top <= viewportHeight;
                    const nodes = Array.from(document.querySelectorAll('a[href], div, span, p, button'));
                    const match = nodes.find((node) => {
                        const href = normalize(node.getAttribute && node.getAttribute('href'));
                        const text = normalize(node.innerText || node.textContent);
                        const aria = normalize(node.getAttribute && node.getAttribute('aria-label'));
                        if (commentId && (href.includes(commentId) || text.includes(commentId) || aria.includes(commentId))) return true;
                        if (snippet && text.includes(snippet)) return true;
                        if (author && (text.includes(author) || aria.includes(author))) return true;
                        return false;
                    });
                    if (!match) return false;
                    match.scrollIntoView({ block: 'center', inline: 'nearest' });
                    return visible(match.getBoundingClientRect());
                }""",
                {
                    "commentId": comment_id or "",
                    "author": normalized_author or "",
                    "snippet": normalized_snippet or "",
                },
            )
        except Exception:
            located = False
        if located:
            await page.wait_for_timeout(900)
        row = await _comment_action_row(
            page,
            target_comment_url=target_comment_url,
            author=author,
            expected_title=expected_title,
            body_snippet=body_snippet,
        )
        if row:
            return row
        await page.mouse.wheel(0, 620)
        await page.wait_for_timeout(900)

    return await _comment_action_row(
        page,
        target_comment_url=target_comment_url,
        author=author,
        expected_title=expected_title,
        body_snippet=body_snippet,
    )


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
        if await _first_viewport_locator(page, COMMENT["share_button"]):
            return True
        await page.mouse.wheel(0, 520)
        await page.wait_for_timeout(900)
    return bool(await _first_viewport_locator(page, COMMENT["share_button"]))


async def _comment_surface_visible(page) -> bool:
    if await _active_editable_present(page):
        return True
    if await _visible_selector_exists(page, COMMENT["composer_trigger"]):
        return True
    if await _first_viewport_locator(page, COMMENT["share_button"]):
        return True
    if await _first_viewport_locator(page, COMMENT["search_comments_input"]):
        return True
    return False


async def _scroll_until_comment_surface_visible(page, *, max_scrolls: int = 6) -> bool:
    for _ in range(max(1, max_scrolls)):
        if await _comment_surface_visible(page):
            return True
        await page.mouse.wheel(0, 520)
        await page.wait_for_timeout(900)
    return await _comment_surface_visible(page)


async def _click_post_upvote_region(page, *, share_box: Dict[str, float]) -> bool:
    click_x = max(24, int(float(share_box["x"]) - 224))
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
    vote_candidates = list(row.get("voteCandidates") or [])
    vote = dict(row.get("vote") or {})
    if vote and not vote_candidates:
        vote_candidates = [vote]
    if not vote_candidates:
        return False
    recorder = get_current_forensic_recorder()
    for index, candidate in enumerate(vote_candidates):
        await page.mouse.click(candidate["x"], candidate["y"])
        queue_current_event(
            "click",
            {
                "method": "comment_row_geometry",
                "action_name": "upvote_comment",
                "candidate_index": index,
                "x": candidate.get("x"),
                "y": candidate.get("y"),
                "reply": row.get("reply"),
                "author": row.get("author"),
            },
            phase="activation",
            source="reddit_bot",
        )
        await page.wait_for_timeout(900)
        if await _vote_point_is_active(page, x=float(candidate.get("x")), y=float(candidate.get("y"))):
            return True
        if _network_has_vote_mutation(recorder, target_kind="comment"):
            return True
    return True


async def _vote_point_is_active(page, *, x: float, y: float) -> bool:
    try:
        return bool(
            await page.evaluate(
                """({ x, y }) => {
                    const activeColor = (value) => {
                        const match = String(value || '').match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/i);
                        if (!match) return false;
                        const r = Number(match[1]);
                        const g = Number(match[2]);
                        const b = Number(match[3]);
                        const warmActive = r >= 160 && g <= 140 && b <= 120;
                        const coolActive = b >= 130 && g >= 120 && r <= 130;
                        return warmActive || coolActive;
                    };
                    const samples = [
                        [x, y],
                        [x + 10, y],
                        [x + 18, y],
                        [x + 28, y],
                    ];
                    for (const [px, py] of samples) {
                        for (const node of Array.from(document.elementsFromPoint(px, py) || [])) {
                            const attrs = [
                                node.innerText || node.textContent || '',
                                node.getAttribute && node.getAttribute('aria-label'),
                                node.getAttribute && node.getAttribute('title'),
                                node.className || '',
                            ].join(' ').toLowerCase();
                            if (attrs.includes('remove upvote') || attrs.includes('upvoted')) return true;
                            const style = getComputedStyle(node);
                            if ([style.color, style.fill, style.stroke, style.backgroundColor].some(activeColor)) {
                                return true;
                            }
                        }
                    }
                    return false;
                }""",
                {"x": float(x), "y": float(y)},
            )
        )
    except Exception:
        return False


async def _vote_region_is_active(page, *, left: float, right: float, y: float) -> bool:
    try:
        return bool(
            await page.evaluate(
                """({ left, right, y }) => {
                    const activeColor = (value) => {
                        const match = String(value || '').match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/i);
                        if (!match) return false;
                        const r = Number(match[1]);
                        const g = Number(match[2]);
                        const b = Number(match[3]);
                        const warmActive = r >= 160 && g <= 140 && b <= 120;
                        const coolActive = b >= 130 && g >= 120 && r <= 130;
                        return warmActive || coolActive;
                    };
                    for (let px = left; px <= right; px += 12) {
                        for (let py = y - 10; py <= y + 10; py += 10) {
                            for (const node of Array.from(document.elementsFromPoint(px, py) || [])) {
                                const attrs = [
                                    node.innerText || node.textContent || '',
                                    node.getAttribute && node.getAttribute('aria-label'),
                                    node.getAttribute && node.getAttribute('title'),
                                    node.className || '',
                                ].join(' ').toLowerCase();
                                if (attrs.includes('remove upvote') || attrs.includes('upvoted')) return true;
                                const style = getComputedStyle(node);
                                if ([style.color, style.fill, style.stroke, style.backgroundColor].some(activeColor)) {
                                    return true;
                                }
                            }
                        }
                    }
                    return false;
                }""",
                {"left": float(left), "right": float(right), "y": float(y)},
            )
        )
    except Exception:
        return False


def _network_has_vote_mutation(
    recorder,
    *,
    target_kind: str,
    vote_state: str = "UP",
) -> bool:
    capture = getattr(recorder, "network_capture", None)
    events = list(getattr(capture, "events", []) or [])
    expected_key = '"postId"' if target_kind == "post" else '"commentId"'
    expected_vote = f'"voteState":"{vote_state}"'
    for event in events:
        if event.get("kind") != "request":
            continue
        if str(event.get("method") or "").upper() != "POST":
            continue
        if "graphql" not in str(event.get("url") or "").lower():
            continue
        excerpt = str(event.get("post_data_excerpt") or "")
        compact = excerpt.replace(" ", "")
        if expected_key in compact and expected_vote in compact:
            return True
    return False


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


async def _dom_click_at_point(page, *, x: float, y: float, action_name: str) -> bool:
    try:
        clicked = await page.evaluate(
            """({ x, y }) => {
                const elements = Array.from(document.elementsFromPoint(x, y) || []);
                const target = elements.find((node) => {
                    if (!(node instanceof HTMLElement)) return false;
                    const tag = String(node.tagName || '').toLowerCase();
                    const role = String(node.getAttribute('role') || '').toLowerCase();
                    return tag === 'button' || role === 'button';
                });
                if (!target) return null;
                for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
                    target.dispatchEvent(
                        new MouseEvent(type, {
                            bubbles: true,
                            cancelable: true,
                            view: window,
                            clientX: x,
                            clientY: y,
                        })
                    );
                }
                return {
                    text: String(target.innerText || target.textContent || '').trim(),
                    aria: String(target.getAttribute('aria-label') || '').trim(),
                    tag: String(target.tagName || '').toLowerCase(),
                };
            }""",
            {"x": float(x), "y": float(y)},
        )
    except Exception:
        clicked = None
    if not clicked:
        return False
    queue_current_event(
        "click",
        {
            "method": "dom_click_at_point",
            "action_name": action_name,
            "x": x,
            "y": y,
            "target": clicked,
        },
        phase="activation",
        source="reddit_bot",
    )
    await page.wait_for_timeout(700)
    return True


async def _reply_inline_box_present(
    page,
    *,
    author: Optional[str] = None,
    row: Optional[Dict[str, Any]] = None,
) -> bool:
    normalized_author = _normalize_text(author)
    row_y = None
    if row:
        reply = dict(row.get("reply") or {})
        author_box = dict(row.get("author") or {})
        row_y = reply.get("y") or author_box.get("y")
    return bool(
        await page.evaluate(
            """({ author, rowY }) => {
                const normalize = (value) => String(value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
                const viewportWidth = window.innerWidth || 393;
                const viewportHeight = window.innerHeight || 873;
                const visible = (rect) =>
                    rect &&
                    rect.width >= 12 &&
                    rect.height >= 12 &&
                    rect.bottom >= 0 &&
                    rect.right >= 0 &&
                    rect.top <= viewportHeight &&
                    rect.left <= viewportWidth;
                const inReplyBand = (rect) => {
                    if (!visible(rect)) return false;
                    if (typeof rowY !== 'number' || Number.isNaN(rowY)) return true;
                    const centerY = rect.top + rect.height / 2;
                    return Math.abs(centerY - rowY) <= 320 || rect.top >= rowY - 120;
                };
                const nodes = Array.from(document.querySelectorAll('button, input, textarea, div, span, [role="textbox"], [contenteditable="true"], [contenteditable="plaintext-only"]'));
                const replyNeedles = ['reply to u/', 'reply to'];
                if (author) {
                    replyNeedles.unshift(`reply to u/${author}`);
                    replyNeedles.unshift(`reply to ${author}`);
                }
                for (const node of nodes) {
                    const rect = node.getBoundingClientRect();
                    if (!inReplyBand(rect)) continue;
                    const text = normalize(node.innerText || node.textContent);
                    const aria = normalize(node.getAttribute && node.getAttribute('aria-label'));
                    const placeholder = normalize(node.getAttribute && node.getAttribute('placeholder'));
                    const combined = [text, aria, placeholder].filter(Boolean).join(' | ');
                    if (replyNeedles.some((needle) => combined.includes(needle))) {
                        return true;
                    }
                    const tag = normalize(node.tagName);
                    const role = normalize(node.getAttribute && node.getAttribute('role'));
                    const contenteditable = normalize(node.getAttribute && node.getAttribute('contenteditable'));
                    if (
                        document.activeElement === node &&
                        (
                            node.isContentEditable ||
                            role === 'textbox' ||
                            contenteditable === 'true' ||
                            contenteditable === 'plaintext-only' ||
                            tag === 'textarea' ||
                            tag === 'input'
                        )
                    ) {
                        return true;
                    }
                }

                const buttons = Array.from(document.querySelectorAll('button'));
                const cancel = buttons.filter((node) => {
                    const rect = node.getBoundingClientRect();
                    return inReplyBand(rect) && normalize(node.innerText || node.textContent) === 'cancel';
                });
                const comment = buttons.filter((node) => {
                    const rect = node.getBoundingClientRect();
                    return inReplyBand(rect) && normalize(node.innerText || node.textContent) === 'comment';
                });
                return cancel.some((left) => {
                    const leftRect = left.getBoundingClientRect();
                    return comment.some((right) => {
                        const rightRect = right.getBoundingClientRect();
                        return Math.abs(leftRect.top - rightRect.top) <= 24 && rightRect.left > leftRect.right - 12;
                    });
                });
            }""",
            {"author": normalized_author or "", "rowY": float(row_y) if row_y is not None else None},
        )
    )


async def _focus_reply_inline_box(page) -> bool:
    try:
        candidate = await page.evaluate(
            """() => {
                const normalize = (value) => String(value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
                const visible = (rect) => rect && rect.width >= 20 && rect.height >= 20 && rect.bottom >= 0 && rect.right >= 0;
                const buttons = Array.from(document.querySelectorAll('button'));
                const cancel = buttons.find((node) => normalize(node.innerText || node.textContent) === 'cancel');
                const comment = buttons.find((node) => normalize(node.innerText || node.textContent) === 'comment');
                if (!cancel || !comment) return null;
                const cancelRect = cancel.getBoundingClientRect();
                const commentRect = comment.getBoundingClientRect();
                if (!visible(cancelRect) || !visible(commentRect)) return null;
                const left = Math.min(cancelRect.left, commentRect.left) - 180;
                const right = Math.max(cancelRect.right, commentRect.right);
                const box = {
                    x: Math.max(24, Math.round((Math.max(24, left) + right) / 2)),
                    y: Math.max(60, Math.round(cancelRect.top - 70)),
                };
                return box;
            }"""
        )
    except Exception:
        candidate = None
    if not candidate:
        return False
    await page.mouse.click(candidate["x"], candidate["y"])
    queue_current_event(
        "click",
        {
            "method": "reply_inline_box_layout",
            "x": candidate.get("x"),
            "y": candidate.get("y"),
        },
        phase="activation",
        source="reddit_bot",
    )
    await page.wait_for_timeout(700)
    return True


async def _click_reply_inline_placeholder(page, *, author: Optional[str]) -> bool:
    needles: List[str] = []
    normalized_author = _normalize_text(author)
    if normalized_author:
        needles.append(f"reply to u/{normalized_author}")
    needles.extend(["reply to u/", "reply to"])
    seen = set()
    for needle in needles:
        normalized = _normalize_text(needle)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        if await _click_visible_text_region(
            page,
            needle=needle,
            action_name="reply_inline_placeholder",
            min_top=420,
        ):
            return True
    return False


async def _click_reply_inline_submit_button(page) -> bool:
    try:
        target = await page.evaluate(
            """() => {
                const normalize = (value) => String(value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
                const visible = (rect) => rect && rect.width >= 20 && rect.height >= 20 && rect.bottom >= 0 && rect.right >= 0;
                const viewportWidth = window.innerWidth || 393;
                const viewportHeight = window.innerHeight || 873;
                const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
                const buttons = Array.from(document.querySelectorAll('button'));
                const cancel = buttons.find((node) => normalize(node.innerText || node.textContent) === 'cancel');
                const commentCandidates = buttons.filter((node) => normalize(node.innerText || node.textContent) === 'comment');
                let picked = null;
                if (cancel) {
                    const cancelRect = cancel.getBoundingClientRect();
                    for (const node of commentCandidates) {
                        let rect = node.getBoundingClientRect();
                        if (!visible(rect)) continue;
                        if (Math.abs(rect.top - cancelRect.top) > 20) continue;
                        if (rect.left < cancelRect.right - 24) continue;
                        node.scrollIntoView({ block: 'center', inline: 'nearest' });
                        rect = node.getBoundingClientRect();
                        picked = {
                            x: Math.round(clamp(rect.left + rect.width / 2, 20, viewportWidth - 20)),
                            y: Math.round(clamp(rect.top + rect.height / 2, 20, viewportHeight - 20)),
                            text: 'comment',
                        };
                        break;
                    }
                }
                if (!picked) {
                    const fallback = commentCandidates
                        .map((node) => ({ node, rect: node.getBoundingClientRect() }))
                        .filter(({ rect }) => visible(rect))
                        .sort((a, b) => a.rect.top - b.rect.top || a.rect.left - b.rect.left)
                        .pop();
                    if (fallback) {
                        fallback.node.scrollIntoView({ block: 'center', inline: 'nearest' });
                        const rect = fallback.node.getBoundingClientRect();
                        picked = {
                            x: Math.round(clamp(rect.left + rect.width / 2, 20, viewportWidth - 20)),
                            y: Math.round(clamp(rect.top + rect.height / 2, 20, viewportHeight - 20)),
                            text: 'comment',
                        };
                    }
                }
                return picked;
            }"""
        )
    except Exception:
        target = None
    if not target:
        return False
    await page.mouse.click(target["x"], target["y"])
    queue_current_event(
        "click",
        {
            "method": "reply_inline_submit_geometry",
            "target": target,
        },
        phase="submit",
        source="reddit_bot",
    )
    await page.wait_for_timeout(500)
    return True


async def _ensure_reply_inline_box(
    page,
    *,
    row: Optional[Dict[str, Any]],
    author: Optional[str],
    expected_title: Optional[str],
) -> bool:
    if await _reply_inline_box_present(page, author=author, row=row):
        return True
    if row and await _click_reply_row_button(page, row=row):
        if await _reply_inline_box_present(page, author=author, row=row):
            return True
    reply = dict((row or {}).get("reply") or {})
    if reply and await _dom_click_at_point(
        page,
        x=float(reply.get("x")),
        y=float(reply.get("y")),
        action_name="reply_comment_dom_retry",
    ):
        if await _reply_inline_box_present(page, author=author, row=row):
            return True
    if await _click_named_control(
        page,
        action_name="reply_comment_retry",
        needles=["reply"],
        expected_title=expected_title,
        anchor_text=author,
        max_vertical_gap=220,
        require_below_anchor=True,
    ):
        if await _reply_inline_box_present(page, author=author, row=row):
            return True
    return False


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
    target_author: Optional[str] = None,
    allow_global_trigger: bool = True,
    thread_url: Optional[str] = None,
) -> bool:
    selectors = COMMENT["reply_input"] if reply else COMMENT["composer_input"]
    if await _fill_first(page, selectors, text):
        return True
    if await _active_editable_present(page):
        return await _keyboard_type_and_verify(page, text, reply=reply)
    if reply and await _reply_inline_box_present(page, author=target_author):
        if await _click_reply_inline_placeholder(page, author=target_author):
            if await _keyboard_type_and_verify(page, text, reply=reply):
                return True
        if await _focus_reply_inline_box(page):
            if await _active_editable_present(page):
                return await _keyboard_type_and_verify(page, text, reply=reply)
            return await _keyboard_type_and_verify(page, text, reply=reply)
    if not allow_global_trigger:
        return False
    opened = await _open_comment_composer(page, expected_title)
    if not opened and thread_url and not await _thread_context_present(page, expected_title):
        if await _ensure_thread_context(page, url=thread_url, expected_title=expected_title):
            opened = await _open_comment_composer(page, expected_title)
    if not opened:
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
            target_context = await _load_post_context(url)
            expected_title = (target_context or {}).get("title") or await _current_thread_title(page)
            if not await _ensure_thread_context(page, url=url, expected_title=expected_title):
                await _capture_reddit_failure_state(page, "REDDIT POST THREAD CONTEXT MISSING")
                return _result(
                    success=False,
                    action="upvote_post",
                    profile_name=session.profile_name,
                    error="Reddit target thread did not load",
                )
            await _scroll_until_post_actions_visible(page)
            await dump_interactive_elements(page, "REDDIT UPVOTE POST")
            share_locator = await _first_viewport_locator(page, COMMENT["share_button"])
            share_box = await share_locator.bounding_box() if share_locator else None
            share_y = float(share_box["y"] + (share_box["height"] / 2)) if share_box else None
            share_left = float(share_box["x"]) if share_box else None
            vote_x = max(24, int(float(share_box["x"]) - 186)) if share_box else None
            before_signature = (
                await _capture_row_signature(page, row_y=share_y, max_x=share_left)
                if share_y is not None and share_left is not None
                else []
            )
            if (
                vote_x is not None
                and share_y is not None
                and await _vote_region_is_active(page, left=max(18, vote_x - 16), right=max(72, share_left - 24), y=share_y)
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
            recorder = get_current_forensic_recorder()
            toggled_off_existing = _network_has_vote_mutation(recorder, target_kind="post", vote_state="NONE")
            success = await _verify_named_control_state(
                page,
                needles=["remove upvote", "upvoted"],
                expected_title=expected_title,
                row_y=share_y,
                left_of_x=share_left,
            )
            if not success:
                success = bool(
                    (before_signature and after_signature and before_signature != after_signature)
                    or _network_has_vote_mutation(recorder, target_kind="post")
                )
            if not success and toggled_off_existing and share_box:
                queue_current_event(
                    "recovery",
                    {
                        "action_name": "upvote_post",
                        "reason": "toggle_off_existing_upvote",
                    },
                    phase="verification",
                    source="reddit_bot",
                )
                await _click_post_upvote_region(page, share_box=share_box)
                await page.wait_for_timeout(1500)
                screenshot = await save_debug_screenshot(page, f"reddit_upvote_post_{session.profile_name}")
                success = await _verify_named_control_state(
                    page,
                    needles=["remove upvote", "upvoted"],
                    expected_title=expected_title,
                    row_y=share_y,
                    left_of_x=share_left,
                )
                if not success and vote_x is not None and share_y is not None:
                    success = await _vote_region_is_active(
                        page,
                        left=max(18, vote_x - 16),
                        right=max(72, share_left - 24),
                        y=share_y,
                    )
                if not success:
                    success = _network_has_vote_mutation(recorder, target_kind="post", vote_state="UP")
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
    body_snippet = (target_context or {}).get("body_snippet") or None
    thread_url = str((target_context or {}).get("thread_url") or "").strip()
    target_surfaces = [str(target_comment_url).strip()]
    if thread_url and thread_url not in target_surfaces:
        target_surfaces.append(thread_url)

    async with _session_page(session, proxy_url) as (_browser, _context, page):
        try:
            last_error = "Reddit comment upvote control not found"
            for idx, surface_url in enumerate(target_surfaces):
                await _goto(page, surface_url)
                if surface_url == thread_url:
                    if not await _ensure_thread_context(page, url=surface_url, expected_title=expected_title):
                        last_error = "Reddit target thread did not load"
                        continue
                await dump_interactive_elements(page, "REDDIT UPVOTE COMMENT")
                row = await _scroll_target_comment_into_view(
                    page,
                    target_comment_url=target_comment_url,
                    author=author,
                    expected_title=expected_title,
                    body_snippet=body_snippet,
                )
                if not row:
                    last_error = "Reddit target comment context not found"
                    if idx + 1 < len(target_surfaces):
                        continue
                    await _capture_reddit_failure_state(page, "REDDIT COMMENT TARGET MISSING")
                    raise RuntimeError(last_error)
                reply = dict((row or {}).get("reply") or {})
                vote = dict((row or {}).get("vote") or {})
                before_signature = (
                    await _capture_row_signature(page, row_y=float(reply.get("y")), max_x=float(reply.get("left")))
                    if reply
                    else []
                )
                if vote and await _vote_point_is_active(page, x=float(vote.get("x")), y=float(vote.get("y"))):
                    screenshot = await save_debug_screenshot(page, f"reddit_upvote_comment_{session.profile_name}")
                    return _result(
                        success=True,
                        action="upvote_comment",
                        profile_name=session.profile_name,
                        screenshot=screenshot,
                        current_url=page.url,
                        verification="already_upvoted",
                        target_url=thread_url or surface_url,
                        target_comment_url=target_comment_url,
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
                        target_url=thread_url or surface_url,
                        target_comment_url=target_comment_url,
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
                    last_error = "Reddit comment upvote control not found"
                    if idx + 1 < len(target_surfaces):
                        continue
                    await _capture_reddit_failure_state(page, "REDDIT COMMENT UPVOTE MISSING")
                    raise RuntimeError(last_error)

                await page.wait_for_timeout(1500)
                screenshot = await save_debug_screenshot(page, f"reddit_upvote_comment_{session.profile_name}")
                after_signature = (
                    await _capture_row_signature(page, row_y=float(reply.get("y")), max_x=float(reply.get("left")))
                    if reply
                    else []
                )
                recorder = get_current_forensic_recorder()
                toggled_off_existing = _network_has_vote_mutation(recorder, target_kind="comment", vote_state="NONE")
                success = await _verify_named_control_state(
                    page,
                    needles=["remove upvote", "upvoted"],
                    anchor_text=author,
                    expected_title=expected_title,
                    max_vertical_gap=220,
                    require_below_anchor=True,
                )
                if not success:
                    success = bool(
                        (before_signature and after_signature and before_signature != after_signature)
                        or _network_has_vote_mutation(recorder, target_kind="comment")
                    )
                if not success and toggled_off_existing:
                    queue_current_event(
                        "recovery",
                        {
                            "action_name": "upvote_comment",
                            "reason": "toggle_off_existing_upvote",
                        },
                        phase="verification",
                        source="reddit_bot",
                    )
                    recovered = await _click_comment_upvote_region(page, row=row)
                    if not recovered:
                        recovered = await _click_named_control(
                            page,
                            action_name="upvote_comment",
                            needles=["upvote"],
                            anchor_text=author,
                            expected_title=expected_title,
                            max_vertical_gap=220,
                            require_below_anchor=True,
                        )
                    if recovered:
                        await page.wait_for_timeout(1500)
                        screenshot = await save_debug_screenshot(page, f"reddit_upvote_comment_{session.profile_name}")
                        recovery_signature = (
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
                            success = bool(
                                (before_signature and recovery_signature and before_signature != recovery_signature)
                                or _network_has_vote_mutation(recorder, target_kind="comment", vote_state="UP")
                            )
                if success:
                    return _result(
                        success=True,
                        action="upvote_comment",
                        profile_name=session.profile_name,
                        screenshot=screenshot,
                        current_url=page.url,
                        target_url=thread_url or surface_url,
                        target_comment_url=target_comment_url,
                    )
                last_error = "Reddit comment upvote verification failed"
                if idx + 1 < len(target_surfaces):
                    continue
                return _result(
                    success=False,
                    action="upvote_comment",
                    profile_name=session.profile_name,
                    screenshot=screenshot,
                    current_url=page.url,
                    error=last_error,
                    target_url=thread_url or surface_url,
                    target_comment_url=target_comment_url,
                )
            raise RuntimeError(last_error)
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
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(500)
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
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(500)
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
    user_flair_hint: Optional[str] = None,
    auto_user_flair: bool = False,
) -> Dict[str, Any]:
    target_url = "https://www.reddit.com/submit"
    normalized = None
    if subreddit:
        normalized = subreddit.strip().lstrip("r/").strip("/")
        target_url = f"https://www.reddit.com/r/{quote(normalized)}/submit?type=TEXT"

    async with _session_page(session, proxy_url) as (_browser, _context, page):
        try:
            await _goto(page, target_url)
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(400)
            identity_evidence = await _ensure_subreddit_user_flair(
                page,
                session,
                subreddit=normalized,
                action="create_post",
                desired_flair=user_flair_hint,
                auto_user_flair=bool(auto_user_flair or user_flair_hint),
            )
            await _goto(page, target_url)
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(400)
            await dump_interactive_elements(page, "REDDIT CREATE POST")

            title_filled = await _fill_first(page, POST["title_input"], title)
            if not title_filled:
                title_filled = await _fill_post_field_by_semantics(page, kind="title", value=title)
            if not title_filled:
                await _capture_reddit_failure_state(page, "REDDIT POST TITLE MISSING")
                raise RuntimeError("Reddit post title input not found")

            if body:
                body_filled = await _fill_first(page, POST["body_input"], body)
                if not body_filled:
                    body_filled = await _fill_post_field_by_semantics(page, kind="body", value=body)
                if not body_filled:
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

            await _raise_if_community_comment_banned(page, capture_context="REDDIT POST COMMUNITY BAN")
            if not await _click_first(page, POST["post_button"], timeout_ms=5000):
                raise RuntimeError("Reddit Post button not found")

            await page.wait_for_timeout(1800)
            if await _post_requires_flair(page):
                if not await _ensure_post_flair(page):
                    await _capture_reddit_failure_state(page, "REDDIT POST FLAIR REQUIRED")
                    raise RuntimeError("Reddit post flair selection failed")
                if not await _click_first(page, POST["post_button"], timeout_ms=5000):
                    raise RuntimeError("Reddit Post button not found after flair selection")

            await page.wait_for_timeout(5000)
            screenshot = await save_debug_screenshot(page, f"reddit_create_post_{session.profile_name}")
            current_url = page.url
            actor_username = session.get_username() if hasattr(session, "get_username") else None
            created_post_url = current_url if "/comments/" in current_url else None
            if not created_post_url:
                created_post_url = await _find_created_post_permalink_on_feed(
                    page,
                    title=title,
                    body=body,
                    actor_username=actor_username,
                    profile_name=getattr(session, "profile_name", None),
                )
            success = bool(created_post_url)
            if not success:
                await _raise_if_community_comment_banned(page, capture_context="REDDIT POST COMMUNITY BAN")
                await dump_interactive_elements(page, "REDDIT POST VERIFY FAILED")
            return _result(
                success=success,
                action="create_post",
                profile_name=session.profile_name,
                screenshot=screenshot,
                current_url=current_url,
                target_url=created_post_url,
                identity_evidence=identity_evidence,
                error=None if success else "Reddit post submission verification failed",
            )
        except RedditCommunityBanError as exc:
            return _result(
                success=False,
                action="create_post",
                profile_name=session.profile_name,
                error=str(exc),
                failure_class="community_restricted",
                throttled=True,
                throttle_reason=str(exc),
                current_url=page.url,
                subreddit=subreddit,
            )
        except Exception as exc:
            return _result(success=False, action="create_post", profile_name=session.profile_name, error=str(exc))


async def _click_reply_submit(page, reply_text: str) -> bool:
    if await _click_reply_inline_submit_button(page):
        return True
    if await _click_named_control(
        page,
        action_name="reply_submit",
        needles=["comment"],
        anchor_text=reply_text[:80],
        max_vertical_gap=180,
        require_below_anchor=True,
    ):
        return True
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
    subreddit: Optional[str] = None,
    user_flair_hint: Optional[str] = None,
    auto_user_flair: bool = False,
) -> Dict[str, Any]:
    async with _session_page(session, proxy_url) as (_browser, _context, page):
        try:
            identity_evidence = await _ensure_subreddit_user_flair(
                page,
                session,
                subreddit=subreddit or _infer_subreddit_from_url(url),
                action="comment_post",
                desired_flair=user_flair_hint,
                auto_user_flair=bool(auto_user_flair or user_flair_hint),
                preferred_url=url,
            )
            await _goto(page, url)
            target_context = await _load_post_context(url)
            expected_title = (target_context or {}).get("title") or await _current_thread_title(page)
            if not await _ensure_thread_context(page, url=url, expected_title=expected_title):
                await _capture_reddit_failure_state(page, "REDDIT THREAD CONTEXT MISSING")
                raise RuntimeError("Reddit target thread did not load")
            await _scroll_until_comment_surface_visible(page, max_scrolls=6)
            await dump_interactive_elements(page, "REDDIT COMMENT ON POST")
            await _raise_if_community_comment_banned(page, capture_context="REDDIT COMMENT COMMUNITY BAN")

            if not await _fill_comment_input(page, text, expected_title=expected_title, thread_url=url):
                if not await _thread_context_present(page, expected_title):
                    await _ensure_thread_context(page, url=url, expected_title=expected_title)
                await _raise_if_community_comment_banned(page, capture_context="REDDIT COMMENT COMMUNITY BAN")
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
                identity_evidence=identity_evidence,
                error=None if success else "Reddit comment verification failed",
            )
        except RedditCommunityBanError as exc:
            return _result(
                success=False,
                action="comment_post",
                profile_name=session.profile_name,
                error=str(exc),
                failure_class="community_restricted",
                throttled=True,
                throttle_reason=str(exc),
                current_url=page.url,
            )
        except Exception as exc:
            return _result(success=False, action="comment_post", profile_name=session.profile_name, error=str(exc))


async def reply_to_comment(
    session: RedditSession,
    *,
    target_comment_url: str,
    text: str,
    proxy_url: Optional[str] = None,
    subreddit: Optional[str] = None,
    user_flair_hint: Optional[str] = None,
    auto_user_flair: bool = False,
) -> Dict[str, Any]:
    target_context = await _load_target_comment_context(target_comment_url)
    thread_url = str((target_context or {}).get("thread_url") or "").strip()
    target_surfaces = _build_reply_target_surfaces(target_comment_url, thread_url)
    author = (target_context or {}).get("author") or None
    body_snippet = (target_context or {}).get("body_snippet") or None
    expected_title = (target_context or {}).get("title") or None

    async with _session_page(session, proxy_url) as (_browser, _context, page):
        try:
            identity_evidence = await _ensure_subreddit_user_flair(
                page,
                session,
                subreddit=subreddit or _infer_subreddit_from_url(target_comment_url) or _infer_subreddit_from_url(thread_url),
                action="reply_comment",
                desired_flair=user_flair_hint,
                auto_user_flair=bool(auto_user_flair or user_flair_hint),
                preferred_url=thread_url or target_comment_url,
            )
            last_error = "Reddit Reply button not found"
            surface_errors: List[str] = []
            for idx, surface_url in enumerate(target_surfaces):
                await _goto(page, surface_url)
                if surface_url == thread_url and thread_url:
                    if not await _ensure_thread_context(page, url=surface_url, expected_title=expected_title):
                        last_error = "Reddit target thread did not load"
                        surface_errors.append(f"{surface_url}: {last_error}")
                        continue
                await dump_interactive_elements(page, "REDDIT REPLY TO COMMENT")
                await _raise_if_community_comment_banned(page, capture_context="REDDIT REPLY COMMUNITY BAN")
                row = await _scroll_target_comment_into_view(
                    page,
                    target_comment_url=target_comment_url,
                    author=author,
                    expected_title=expected_title,
                    body_snippet=body_snippet,
                )
                if not row:
                    last_error = "Reddit target comment context not found"
                    surface_errors.append(f"{surface_url}: {last_error}")
                    if idx + 1 < len(target_surfaces):
                        continue
                    await _capture_reddit_failure_state(page, "REDDIT REPLY TARGET MISSING")
                    raise RuntimeError(last_error)

                clicked_reply = await _click_reply_row_button(page, row=row) if row else False
                if not clicked_reply:
                    clicked_reply = await _click_named_control(
                        page,
                        action_name="reply_comment",
                        needles=["reply"],
                        expected_title=expected_title,
                        anchor_text=author,
                        max_vertical_gap=220,
                        require_below_anchor=True,
                    )
                if not clicked_reply:
                    last_error = "Reddit Reply button not found"
                    surface_errors.append(f"{surface_url}: {last_error}")
                    if idx + 1 < len(target_surfaces):
                        continue
                    await _raise_if_community_comment_banned(page, capture_context="REDDIT REPLY COMMUNITY BAN")
                    await _capture_reddit_failure_state(page, "REDDIT REPLY BUTTON MISSING")
                    raise RuntimeError(last_error)
                await page.wait_for_timeout(1000)
                await _dismiss_reddit_open_app_sheet(page)
                if not await _ensure_reply_inline_box(
                    page,
                    row=row,
                    author=author,
                    expected_title=expected_title,
                ):
                    await _dismiss_reddit_open_app_sheet(page)
                    await page.wait_for_timeout(500)
                    if not await _ensure_reply_inline_box(
                        page,
                        row=row,
                        author=author,
                        expected_title=expected_title,
                    ):
                        last_error = "Reddit reply box did not open"
                        if idx + 1 < len(target_surfaces):
                            surface_errors.append(f"{surface_url}: {last_error}")
                            continue
                        await _capture_reddit_failure_state(page, "REDDIT REPLY BOX MISSING")
                        raise RuntimeError(last_error)

                if not await _fill_comment_input(
                    page,
                    text,
                    reply=True,
                    expected_title=expected_title,
                    target_author=author,
                    allow_global_trigger=False,
                ):
                    last_error = "Reddit reply input not found"
                    surface_errors.append(f"{surface_url}: {last_error}")
                    if idx + 1 < len(target_surfaces):
                        continue
                    await _capture_reddit_failure_state(page, "REDDIT REPLY INPUT MISSING")
                    raise RuntimeError(last_error)

                if not await _click_reply_submit(page, text):
                    last_error = "Reddit reply submit button not found"
                    surface_errors.append(f"{surface_url}: {last_error}")
                    if idx + 1 < len(target_surfaces):
                        continue
                    await _capture_reddit_failure_state(page, "REDDIT REPLY SUBMIT MISSING")
                    raise RuntimeError(last_error)

                await page.wait_for_timeout(4000)
                screenshot = await save_debug_screenshot(page, f"reddit_reply_{session.profile_name}")
                success = await _verify_text_visible(page, text)
                if success:
                    return _result(
                        success=True,
                        action="reply_comment",
                        profile_name=session.profile_name,
                        screenshot=screenshot,
                        current_url=page.url,
                        target_url=thread_url or surface_url,
                        target_comment_url=target_comment_url,
                        identity_evidence=identity_evidence,
                    )
                last_error = "Reddit reply verification failed"
                surface_errors.append(f"{surface_url}: {last_error}")
                if idx + 1 < len(target_surfaces):
                    continue
                return _result(
                    success=False,
                    action="reply_comment",
                    profile_name=session.profile_name,
                    screenshot=screenshot,
                    current_url=page.url,
                    error=last_error,
                    target_url=thread_url or surface_url,
                    target_comment_url=target_comment_url,
                    identity_evidence=identity_evidence,
                )
            if surface_errors:
                raise RuntimeError(" | ".join(dict.fromkeys(surface_errors)))
            raise RuntimeError(last_error)
        except RedditCommunityBanError as exc:
            return _result(
                success=False,
                action="reply_comment",
                profile_name=session.profile_name,
                error=str(exc),
                failure_class="community_restricted",
                throttled=True,
                throttle_reason=str(exc),
                current_url=page.url,
                target_url=thread_url or target_comment_url,
                target_comment_url=target_comment_url,
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
    user_flair_hint: Optional[str] = None,
    auto_user_flair: bool = False,
    forensic_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized = str(action or "").strip().lower()
    metadata = dict(((forensic_context or {}).get("metadata") or {}))
    action_timeout_seconds = max(30, int(metadata.get("action_timeout_seconds") or 120))
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
            **metadata,
        },
    )
    recorder_token = set_current_forensic_recorder(recorder)
    generation_evidence = metadata.get("generation_evidence")
    if generation_evidence:
        await attach_current_json_artifact(
            "generation_bundle",
            "generation.json",
            generation_evidence,
            metadata={
                "kind": generation_evidence.get("kind"),
                "subreddit": generation_evidence.get("subreddit"),
            },
        )
    result: Optional[Dict[str, Any]] = None
    finalized = False

    async def _dispatch_action() -> Dict[str, Any]:
        if normalized == "browse_feed":
            return await browse_feed(session, proxy_url=proxy_url)
        if normalized in {"upvote", "upvote_post"}:
            if not url:
                return _result(success=False, action="upvote_post", profile_name=session.profile_name, error="url is required")
            return await upvote_post(session, url=url, proxy_url=proxy_url)
        if normalized == "upvote_comment":
            if not target_comment_url:
                return _result(success=False, action=normalized, profile_name=session.profile_name, error="target_comment_url is required")
            return await upvote_comment(session, target_comment_url=target_comment_url, proxy_url=proxy_url)
        if normalized == "join_subreddit":
            if not url:
                return _result(success=False, action=normalized, profile_name=session.profile_name, error="url is required")
            return await join_subreddit(session, url=url, proxy_url=proxy_url)
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
                user_flair_hint=user_flair_hint,
                auto_user_flair=auto_user_flair,
            )
        if normalized == "comment_post":
            if not url or not text:
                return _result(success=False, action=normalized, profile_name=session.profile_name, error="url and text are required")
            return await comment_on_post(
                session,
                url=url,
                text=text,
                proxy_url=proxy_url,
                subreddit=subreddit,
                user_flair_hint=user_flair_hint,
                auto_user_flair=auto_user_flair,
            )
        if normalized == "reply_comment":
            if not target_comment_url or not text:
                return _result(success=False, action=normalized, profile_name=session.profile_name, error="target_comment_url and text are required")
            return await reply_to_comment(
                session,
                target_comment_url=target_comment_url,
                text=text,
                proxy_url=proxy_url,
                subreddit=subreddit,
                user_flair_hint=user_flair_hint,
                auto_user_flair=auto_user_flair,
            )
        if normalized == "upload_media":
            if not image_path:
                return _result(success=False, action=normalized, profile_name=session.profile_name, error="image_path is required")
            return await upload_media_only(session, image_path=image_path, proxy_url=proxy_url)
        return _result(success=False, action=normalized, profile_name=session.profile_name, error=f"Unsupported Reddit action: {action}")

    try:
        result = await asyncio.wait_for(_dispatch_action(), timeout=action_timeout_seconds)
    except asyncio.TimeoutError:
        result = _result(
            success=False,
            action=normalized,
            profile_name=session.profile_name,
            error=f"Reddit action timeout after {action_timeout_seconds}s",
            failure_class="infrastructure",
        )
    except Exception as exc:
        result = _result(
            success=False,
            action=normalized,
            profile_name=session.profile_name,
            error=str(exc),
            failure_class="infrastructure" if is_infra_error_text(str(exc)) else None,
        )
    except asyncio.CancelledError:
        result = _result(
            success=False,
            action=normalized,
            profile_name=session.profile_name,
            error="Reddit action cancelled",
            failure_class="infrastructure",
        )
        if url and not result.get("target_url"):
            result["target_url"] = url
        if target_comment_url and not result.get("target_comment_url"):
            result["target_comment_url"] = target_comment_url
        if subreddit and not result.get("subreddit"):
            result["subreddit"] = subreddit
        result["attempt_id"] = recorder.attempt_id
        result["trace_id"] = recorder.trace_id
        verdict = build_generic_verdict(result, success_summary=f"reddit action '{normalized}' completed.")
        result["final_verdict"] = verdict.final_verdict
        result["evidence_summary"] = verdict.summary
        await recorder.finalize(verdict, metadata={"action": normalized})
        finalized = True
        reset_current_forensic_recorder(recorder_token)
        raise
    finally:
        if result is not None and normalized and not finalized:
            if url and not result.get("target_url"):
                result["target_url"] = url
            if target_comment_url and not result.get("target_comment_url"):
                result["target_comment_url"] = target_comment_url
            if subreddit and not result.get("subreddit"):
                result["subreddit"] = subreddit
            result["attempt_id"] = recorder.attempt_id
            result["trace_id"] = recorder.trace_id
            verdict = build_generic_verdict(result, success_summary=f"reddit action '{normalized}' completed.")
            result["final_verdict"] = verdict.final_verdict
            result["evidence_summary"] = verdict.summary
            await recorder.finalize(verdict, metadata={"action": normalized})
            finalized = True
            reset_current_forensic_recorder(recorder_token)
    return result
