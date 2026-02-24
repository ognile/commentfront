"""
Safety checks for premium feed posting.

Includes:
- profile identity verification on creator profile page
- duplicate/near-duplicate precheck against recent authored feed posts
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
from difflib import SequenceMatcher
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from comment_bot import _build_playwright_proxy, save_debug_screenshot
from config import MOBILE_VIEWPORT
from fb_session import FacebookSession, apply_session_to_context
from queue_manager import near_duplicate_ratio

logger = logging.getLogger("PremiumSafety")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_name(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip().lower()
    return cleaned


def _name_matches(expected: str, seen: str) -> bool:
    expected_norm = _normalize_name(expected)
    seen_norm = _normalize_name(seen)
    if not expected_norm or not seen_norm:
        return False
    if expected_norm == seen_norm:
        return True
    expected_tokens = [t for t in expected_norm.split(" ") if t]
    return bool(expected_tokens) and all(token in seen_norm for token in expected_tokens)


def _name_tokens_present(expected: str, body_text: str) -> bool:
    expected_norm = _normalize_name(expected)
    body_norm = _normalize_name(body_text)
    if not expected_norm or not body_norm:
        return False
    expected_tokens = [t for t in expected_norm.split(" ") if t]
    return bool(expected_tokens) and all(token in body_norm for token in expected_tokens)


def _canonical_avatar_ref(value: Optional[str]) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None

    def _looks_base64_blob(candidate: str) -> bool:
        if len(candidate) < 128:
            return False
        if len(candidate) % 4 != 0:
            return False
        return bool(re.fullmatch(r"[A-Za-z0-9+/=]+", candidate))

    if text.startswith("data:image"):
        try:
            _, payload = text.split(",", 1)
            blob = base64.b64decode(payload)
            return "data:" + hashlib.sha256(blob).hexdigest()
        except Exception:
            return None

    if text.startswith("http://") or text.startswith("https://"):
        parsed = urlparse(text)
        if parsed.netloc.lower().endswith("fbcdn.net") and (parsed.path or "").startswith("/rsrc.php"):
            return None
        path = parsed.path or ""
        if not path:
            return None
        return f"url:{parsed.netloc.lower()}{path}"

    if _looks_base64_blob(text):
        try:
            blob = base64.b64decode(text)
            return "data:" + hashlib.sha256(blob).hexdigest()
        except Exception:
            return None

    return text.lower()


def _avatar_similarity(expected_ref: Optional[str], seen_ref: Optional[str]) -> Optional[float]:
    left = _canonical_avatar_ref(expected_ref)
    right = _canonical_avatar_ref(seen_ref)
    if not left or not right:
        return None
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def _to_public_screenshot_url(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    name = Path(path).name
    if not name:
        return None
    return f"/screenshots/{name}"


def _session_user_id(session: FacebookSession) -> Optional[str]:
    data = session.data or {}
    user_id = data.get("user_id")
    if user_id:
        return str(user_id)
    cookies = data.get("cookies") or []
    if isinstance(cookies, list):
        for item in cookies:
            if not isinstance(item, dict):
                continue
            if str(item.get("name") or "").strip() != "c_user":
                continue
            val = str(item.get("value") or "").strip()
            if val:
                return val
    return None


def _profile_url(session: FacebookSession, profile_name: str) -> str:
    user_id = _session_user_id(session)
    if user_id:
        return f"https://m.facebook.com/profile.php?id={user_id}"
    normalized = str(profile_name or "").strip().replace(" ", ".")
    return f"https://m.facebook.com/{normalized}"


async def _extract_profile_snapshot(page, expected_profile_name: str) -> Dict:
    return await page.evaluate(
        """(expectedProfileName) => {
            const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim();
            const expectedTokens = normalize(expectedProfileName).toLowerCase().split(" ").filter(Boolean);
            const hasExpectedAuthor = (text) => {
                if (!expectedTokens.length) return true;
                const lower = normalize(text).toLowerCase();
                return expectedTokens.every((token) => lower.includes(token));
            };

            const profileNameCandidates = [];
            const h1 = document.querySelector("h1");
            if (h1 && h1.innerText) profileNameCandidates.push(normalize(h1.innerText));
            const h2 = document.querySelector("h2");
            if (h2 && h2.innerText) profileNameCandidates.push(normalize(h2.innerText));
            if (document.title) profileNameCandidates.push(normalize(document.title));
            const topStrong = Array.from(document.querySelectorAll("strong, span, div"))
                .map((el) => normalize(el.innerText))
                .filter((txt) => txt && txt.length >= 3 && txt.length <= 120)
                .slice(0, 50);
            profileNameCandidates.push(...topStrong);

            const blocked = ["this browser is not supported", "facebook"];
            const profile_name_seen = profileNameCandidates.find((item) => {
                if (!item || item.length < 3) return false;
                const lower = item.toLowerCase();
                return !blocked.some((bad) => lower.includes(bad));
            }) || "";

            let profile_avatar_seen = "";
            const imgs = Array.from(document.querySelectorAll("img"));
            for (const img of imgs) {
                const rect = img.getBoundingClientRect();
                const src = img.getAttribute("src") || "";
                const alt = normalize(img.getAttribute("alt") || "");
                if (!src) continue;
                if (rect.top > 420) continue;
                const altMatch =
                    (profile_name_seen && alt.toLowerCase().includes(profile_name_seen.toLowerCase())) ||
                    alt.toLowerCase().includes("profile");
                const sizeMatch = rect.width >= 36 && rect.height >= 36;
                if (altMatch || sizeMatch) {
                    profile_avatar_seen = src;
                    break;
                }
            }

            const anchors = Array.from(
                document.querySelectorAll('a[href*="story_fbid="], a[href*="/posts/"], a[href*=\"permalink.php\"], a[href*=\"story.php\"], a[href*=\"/videos/\"]')
            );
            const seen = new Set();
            const posts = [];

            for (const anchor of anchors) {
                let href = anchor.getAttribute("href") || "";
                if (!href) continue;
                if (href.startsWith("/")) href = "https://m.facebook.com" + href;
                const container =
                    anchor.closest("article") ||
                    anchor.closest('div[role="article"]') ||
                    anchor.closest('div[data-ft]') ||
                    anchor.closest("section") ||
                    anchor.parentElement;
                const text = normalize(container ? container.innerText : "");
                if (!text || text.length < 12) continue;
                if (!hasExpectedAuthor(text)) continue;
                const key = `${href}::${text.slice(0, 160)}`;
                if (seen.has(key)) continue;
                seen.add(key);
                posts.push({ permalink: href, text: text.slice(0, 800) });
                if (posts.length >= 25) break;
            }

            if (posts.length < 5) {
                const articleNodes = Array.from(document.querySelectorAll("article, div[role='article'], div[data-ft]"));
                for (const node of articleNodes) {
                    const text = normalize(node.innerText || "");
                    if (!text || text.length < 24) continue;
                    if (!hasExpectedAuthor(text)) continue;
                    let href = "";
                    const anchor = node.querySelector('a[href*="story_fbid="], a[href*="/posts/"], a[href*="permalink.php"], a[href*="story.php"], a[href*="/videos/"]');
                    if (anchor) {
                        href = anchor.getAttribute("href") || "";
                    }
                    if (href.startsWith("/")) href = "https://m.facebook.com" + href;
                    const key = `${href || "no_link"}::${text.slice(0, 160)}`;
                    if (seen.has(key)) continue;
                    seen.add(key);
                    posts.push({ permalink: href || null, text: text.slice(0, 800) });
                    if (posts.length >= 25) break;
                }
            }

            return {
                profile_name_seen,
                profile_avatar_seen,
                body_text: normalize(document.body ? document.body.innerText : "").slice(0, 5000),
                posts,
            };
        }""",
        expected_profile_name,
    )


async def _has_broken_link_banner(page) -> bool:
    try:
        return await page.evaluate(
            """() => {
                const text = (document.body && document.body.innerText) ? document.body.innerText : "";
                return text.includes("The link you followed may be broken");
            }"""
        )
    except Exception:
        return False


async def run_feed_safety_precheck(
    *,
    profile_name: str,
    caption: str,
    lookback_posts: int = 5,
    threshold: float = 0.90,
) -> Dict:
    """
    Verify creator identity and block duplicate captions before feed posting.
    """
    session = FacebookSession(profile_name)
    if not session.load():
        return {
            "success": False,
            "error": f"session not found for {profile_name}",
            "identity_check": {
                "profile_name_expected": profile_name,
                "profile_name_seen": None,
                "profile_avatar_expected_ref": None,
                "profile_avatar_seen_ref": None,
                "name_match": False,
                "avatar_similarity": None,
                "avatar_hash_match": None,
                "passed": False,
            },
            "duplicate_precheck": {
                "checked_posts": 0,
                "threshold": float(threshold),
                "top_similarity": 0.0,
                "matched_post_permalink": None,
                "required_posts": max(1, int(lookback_posts)),
                "insufficient_posts": True,
                "passed": False,
            },
        }

    from proxy_manager import get_system_proxy

    proxy = get_system_proxy()
    if not proxy:
        return {
            "success": False,
            "error": "no proxy available for safety precheck",
            "identity_check": {
                "profile_name_expected": profile_name,
                "profile_name_seen": None,
                "profile_avatar_expected_ref": None,
                "profile_avatar_seen_ref": None,
                "name_match": False,
                "avatar_similarity": None,
                "avatar_hash_match": None,
                "passed": False,
            },
            "duplicate_precheck": {
                "checked_posts": 0,
                "threshold": float(threshold),
                "top_similarity": 0.0,
                "matched_post_permalink": None,
                "required_posts": max(1, int(lookback_posts)),
                "insufficient_posts": True,
                "passed": False,
            },
        }

    before_screenshot = None
    after_screenshot = None
    profile_page_url = _profile_url(session, profile_name)

    async with async_playwright() as p:
        browser = None
        try:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-notifications", "--disable-geolocation"],
            )
            context = await browser.new_context(
                user_agent=session.get_user_agent(),
                viewport=session.get_viewport() or MOBILE_VIEWPORT,
                ignore_https_errors=True,
                device_scale_factor=1,
                timezone_id=session.get_device_fingerprint()["timezone"],
                locale=session.get_device_fingerprint()["locale"],
                proxy=_build_playwright_proxy(proxy),
            )
            await Stealth().apply_stealth_async(context)
            page = await context.new_page()
            await apply_session_to_context(context, session)

            required_posts = max(1, int(lookback_posts))
            await page.goto(profile_page_url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(3)
            if await _has_broken_link_banner(page):
                logger.warning(f"precheck detected broken-link banner for {profile_name}; switching to /me")
                await page.goto("https://m.facebook.com/me", wait_until="domcontentloaded", timeout=60000)
                profile_page_url = "https://m.facebook.com/me"
                await asyncio.sleep(3)
            await page.mouse.wheel(0, 600)
            await asyncio.sleep(1)

            before_screenshot = await save_debug_screenshot(page, "premium_precheck_before")
            snapshot = await _extract_profile_snapshot(page, profile_name)

            primary_body_text = str(snapshot.get("body_text") or "")
            primary_posts = list(snapshot.get("posts") or [])
            primary_name_seen = str(snapshot.get("profile_name_seen") or "").strip()
            primary_name_match = _name_matches(profile_name, primary_name_seen) or _name_tokens_present(profile_name, primary_body_text)

            if (not primary_name_match) or (len(primary_posts) < required_posts):
                try:
                    await page.goto("https://m.facebook.com/me", wait_until="domcontentloaded", timeout=60000)
                    await asyncio.sleep(3)
                    if await _has_broken_link_banner(page):
                        await page.goto("https://m.facebook.com/", wait_until="domcontentloaded", timeout=60000)
                        await asyncio.sleep(2)
                    await page.mouse.wheel(0, 600)
                    await asyncio.sleep(1)
                    alt_snapshot = await _extract_profile_snapshot(page, profile_name)
                    alt_body_text = str(alt_snapshot.get("body_text") or "")
                    alt_posts = list(alt_snapshot.get("posts") or [])
                    alt_name_seen = str(alt_snapshot.get("profile_name_seen") or "").strip()
                    alt_name_match = _name_matches(profile_name, alt_name_seen) or _name_tokens_present(profile_name, alt_body_text)
                    if alt_name_match or len(alt_posts) > len(primary_posts):
                        snapshot = alt_snapshot
                        profile_page_url = "https://m.facebook.com/me"
                except Exception as nav_exc:
                    logger.warning(f"fallback /me navigation failed for {profile_name}: {nav_exc}")

            await asyncio.sleep(0.5)
            after_screenshot = await save_debug_screenshot(page, "premium_precheck_after")

            profile_name_seen = str(snapshot.get("profile_name_seen") or "").strip()
            profile_avatar_seen = str(snapshot.get("profile_avatar_seen") or "").strip()
            body_text = str(snapshot.get("body_text") or "")
            expected_avatar = str((session.data or {}).get("profile_picture") or "").strip()
            expected_avatar_ref = _canonical_avatar_ref(expected_avatar)
            seen_avatar_ref = _canonical_avatar_ref(profile_avatar_seen)

            name_match = _name_matches(profile_name, profile_name_seen) or _name_tokens_present(profile_name, body_text)
            if not profile_name_seen and name_match:
                profile_name_seen = profile_name
            avatar_similarity = _avatar_similarity(expected_avatar, profile_avatar_seen)
            avatar_required = bool(expected_avatar_ref and seen_avatar_ref)
            avatar_passed = avatar_similarity is not None and avatar_similarity >= 0.60
            identity_passed = bool(name_match and (avatar_passed or not avatar_required))
            posts = list(snapshot.get("posts") or [])[:required_posts]

            top_similarity = 0.0
            matched_permalink = None
            for post in posts:
                ratio = near_duplicate_ratio(str(caption or ""), str(post.get("text") or ""))
                if ratio > top_similarity:
                    top_similarity = ratio
                    matched_permalink = post.get("permalink")

            duplicate_block = top_similarity >= float(threshold)
            insufficient_posts = len(posts) < required_posts
            duplicate_passed = (not duplicate_block) and (not insufficient_posts)

            identity_check = {
                "profile_name_expected": profile_name,
                "profile_name_seen": profile_name_seen or None,
                "profile_avatar_expected_ref": expected_avatar_ref,
                "profile_avatar_seen_ref": seen_avatar_ref,
                "name_match": bool(name_match),
                "avatar_similarity": round(float(avatar_similarity), 4) if avatar_similarity is not None else None,
                "avatar_hash_match": bool(avatar_passed) if avatar_similarity is not None else None,
                "passed": identity_passed,
            }
            duplicate_precheck = {
                "checked_posts": len(posts),
                "threshold": float(threshold),
                "top_similarity": round(float(top_similarity), 4),
                "matched_post_permalink": matched_permalink if duplicate_block else None,
                "required_posts": required_posts,
                "insufficient_posts": insufficient_posts,
                "passed": duplicate_passed,
                "posts": posts,
            }

            return {
                "success": bool(identity_passed and duplicate_passed),
                "identity_check": identity_check,
                "duplicate_precheck": duplicate_precheck,
                "profile_url": profile_page_url,
                "before_screenshot": before_screenshot,
                "after_screenshot": after_screenshot,
                "screenshot_urls": {
                    "before": _to_public_screenshot_url(before_screenshot),
                    "after": _to_public_screenshot_url(after_screenshot),
                },
                "error": None
                if (identity_passed and duplicate_passed)
                else (
                    "identity_verification_failed"
                    if not identity_passed
                    else ("duplicate_precheck_insufficient_posts" if insufficient_posts else "duplicate_precheck_failed")
                ),
                "checked_at": _utc_iso(),
            }
        except Exception as exc:
            logger.error(f"safety precheck failed for {profile_name}: {exc}")
            return {
                "success": False,
                "identity_check": {
                    "profile_name_expected": profile_name,
                    "profile_name_seen": None,
                    "profile_avatar_expected_ref": None,
                    "profile_avatar_seen_ref": None,
                    "name_match": False,
                    "avatar_similarity": None,
                    "avatar_hash_match": None,
                    "passed": False,
                },
                "duplicate_precheck": {
                    "checked_posts": 0,
                    "threshold": float(threshold),
                    "top_similarity": 0.0,
                    "matched_post_permalink": None,
                    "required_posts": max(1, int(lookback_posts)),
                    "insufficient_posts": True,
                    "passed": False,
                },
                "profile_url": profile_page_url,
                "before_screenshot": before_screenshot,
                "after_screenshot": after_screenshot,
                "screenshot_urls": {
                    "before": _to_public_screenshot_url(before_screenshot),
                    "after": _to_public_screenshot_url(after_screenshot),
                },
                "error": str(exc),
                "checked_at": _utc_iso(),
            }
        finally:
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
