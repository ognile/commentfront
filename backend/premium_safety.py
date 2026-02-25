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
from typing import Dict, List, Optional, Tuple
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


def _dedupe_extracted_posts(posts: List[Dict], similarity_threshold: float = 0.96) -> List[Dict]:
    unique: List[Dict] = []
    for post in posts:
        if not isinstance(post, dict):
            continue
        text = str(post.get("text") or "").strip()
        if len(text) < 20:
            continue
        is_duplicate = False
        for existing in unique:
            existing_text = str(existing.get("text") or "")
            if near_duplicate_ratio(text, existing_text) >= float(similarity_threshold):
                is_duplicate = True
                break
        if is_duplicate:
            continue
        unique.append(
            {
                "permalink": post.get("permalink"),
                "text": text[:800],
                "author": post.get("author"),
            }
        )
    return unique


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


def _profile_candidate_urls(session: FacebookSession, profile_name: str) -> List[str]:
    candidates: List[str] = []
    seen = set()
    user_id = _session_user_id(session)
    slug = str(profile_name or "").strip().replace(" ", ".")

    def _add(url: str) -> None:
        value = str(url or "").strip()
        if not value or value in seen:
            return
        seen.add(value)
        candidates.append(value)

    if user_id:
        _add(f"https://mbasic.facebook.com/profile.php?id={user_id}&v=timeline")
        _add(f"https://mbasic.facebook.com/profile.php?id={user_id}")
        _add(f"https://m.facebook.com/profile.php?id={user_id}&v=timeline")
        _add(f"https://m.facebook.com/profile.php?id={user_id}")
    _add("https://mbasic.facebook.com/me/?v=timeline")
    _add("https://mbasic.facebook.com/me/")
    _add("https://m.facebook.com/me/?v=timeline")
    _add("https://m.facebook.com/me/")
    if slug:
        _add(f"https://mbasic.facebook.com/{slug}?v=timeline")
        _add(f"https://mbasic.facebook.com/{slug}")
        _add(f"https://m.facebook.com/{slug}?v=timeline")
        _add(f"https://m.facebook.com/{slug}")
    _add("https://mbasic.facebook.com/")
    _add("https://m.facebook.com/")
    return candidates


def _url_profile_hint(url: Optional[str], user_id: Optional[str]) -> bool:
    value = str(url or "").strip().lower()
    if not value:
        return False
    if "facebook.com/me" in value:
        return True
    if "profile.php" in value:
        return True
    if "v=timeline" in value and "facebook.com" in value:
        return True
    if user_id and f"id={user_id}" in value:
        return True
    return False


def _snapshot_score(snapshot: Dict, expected_profile_name: str, user_id: Optional[str]) -> Dict[str, object]:
    body_text = str(snapshot.get("body_text") or "")
    profile_name_seen = str(snapshot.get("profile_name_seen") or "").strip()
    posts_count = len(list(snapshot.get("posts") or []))
    profile_surface_detected = bool(snapshot.get("profile_surface_detected"))
    go_to_profile_visible = bool(snapshot.get("go_to_profile_visible"))
    final_url = str(snapshot.get("current_url") or "").strip()
    strict_name_match = _name_matches(expected_profile_name, profile_name_seen)
    token_name_match = _name_tokens_present(expected_profile_name, body_text)
    url_profile = _url_profile_hint(final_url, user_id)

    score = 0
    if strict_name_match:
        score += 40
    if token_name_match:
        score += 12
    if profile_surface_detected:
        score += 22
    if url_profile:
        score += 18
    if go_to_profile_visible:
        score -= 15
    score += min(posts_count, 10) * 6

    return {
        "score": score,
        "strict_name_match": strict_name_match,
        "token_name_match": token_name_match,
        "profile_surface_detected": profile_surface_detected,
        "go_to_profile_visible": go_to_profile_visible,
        "url_profile_hint": url_profile,
        "posts_count": posts_count,
        "final_url": final_url,
    }


async def _extract_profile_snapshot(page, expected_profile_name: str) -> Dict:
    return await page.evaluate(
        """(expectedProfileName) => {
            const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim();
            const expectedTokens = normalize(expectedProfileName).toLowerCase().split(" ").filter(Boolean);
            const hasExpectedAuthor = (text, author) => {
                if (!expectedTokens.length) return true;
                const lowerText = normalize(text).toLowerCase();
                const lowerAuthor = normalize(author).toLowerCase();
                return expectedTokens.every((token) => lowerText.includes(token) || lowerAuthor.includes(token));
            };
            const scoreName = (name) => {
                const value = normalize(name);
                if (!value || value.length < 3 || value.length > 90) return -1;
                const lower = value.toLowerCase();
                const blockedContains = [
                    "this browser is not supported",
                    "facebook",
                    "news feed",
                    "marketplace",
                    "notifications",
                    "messages",
                    "watch",
                    "menu",
                    "reels",
                    "groups",
                    "friends",
                    "safari",
                    "home"
                ];
                if (blockedContains.some((bad) => lower.includes(bad))) return -1;
                let score = 0;
                if (expectedTokens.length) {
                    const overlap = expectedTokens.filter((token) => lower.includes(token)).length;
                    score += overlap * 2;
                    if (overlap === expectedTokens.length) score += 10;
                }
                if (/^[a-z][a-z\\s'.-]+$/i.test(value)) score += 1;
                return score;
            };
            const absolutize = (href) => {
                const raw = (href || "").trim();
                if (!raw) return "";
                if (raw.startsWith("http://") || raw.startsWith("https://")) return raw;
                const origin = (window.location && window.location.origin) ? window.location.origin : "https://m.facebook.com";
                if (raw.startsWith("/")) return origin + raw;
                return raw;
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

            let profile_name_seen = "";
            let bestScore = -1;
            for (const candidate of profileNameCandidates) {
                const currentScore = scoreName(candidate);
                if (currentScore > bestScore) {
                    bestScore = currentScore;
                    profile_name_seen = normalize(candidate);
                }
            }
            if (bestScore < 0) profile_name_seen = "";

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

            const postAnchorSelector = [
                'a[href*="story_fbid="]',
                'a[href*="story.php"]',
                'a[href*="/posts/"]',
                'a[href*="permalink.php"]',
                'a[href*="/videos/"]',
                'a[href*="/reel/"]',
                'a[href*="/watch/?v="]',
                'a[href*="/share/p/"]',
                'a[href*="fbid="]'
            ].join(", ");
            const anchors = Array.from(document.querySelectorAll(postAnchorSelector));
            const seen = new Set();
            const posts = [];

            const articleNodes = Array.from(document.querySelectorAll("article, div[role='article'], div[data-ft]"));
            let fallback_container_hits = 0;
            let engagement_container_hits = 0;
            const extractAuthor = (node) => {
                const candidates = Array.from(
                    node.querySelectorAll("h3, h4, strong, a[role='link'], span[dir='auto'], div[dir='auto']")
                ).map((el) => normalize(el.innerText)).filter(Boolean).slice(0, 20);
                let best = "";
                let bestCandidateScore = -1;
                for (const candidate of candidates) {
                    const currentScore = scoreName(candidate);
                    if (currentScore > bestCandidateScore) {
                        bestCandidateScore = currentScore;
                        best = candidate;
                    }
                }
                return best;
            };
            const extractPermalink = (node) => {
                const anchor = node.querySelector(postAnchorSelector);
                if (!anchor) return "";
                return absolutize(anchor.getAttribute("href") || "");
            };
            const hasEngagementControls = (node) => {
                const roleButtons = node.querySelectorAll("div[role='button'], a[role='button']");
                if (roleButtons.length >= 3) return true;
                const tapTargets = node.querySelectorAll("a, button, div[role='button'], a[role='button'], div[tabindex], span[role='button']");
                if (tapTargets.length >= 6) return true;
                const controls = Array.from(node.querySelectorAll("div[role='button'], a[role='button'], a[role='link'], span")).slice(0, 160);
                let hasLike = false;
                let hasComment = false;
                let hasShare = false;
                let iconLikeCount = 0;
                for (const control of controls) {
                    const text = normalize(control.innerText).toLowerCase();
                    const aria = normalize(control.getAttribute("aria-label")).toLowerCase();
                    if (text === "like" || aria.startsWith("like")) hasLike = true;
                    if (text === "comment" || aria.includes("comment")) hasComment = true;
                    if (text === "share" || aria.includes("share")) hasShare = true;
                    if (!text && /like|comment|share|reacted/.test(aria)) iconLikeCount += 1;
                    if (text && text.length <= 3 && /[^\\w\\s]/.test(text)) iconLikeCount += 1;
                    if ((hasLike && hasComment) || (hasComment && hasShare) || (hasLike && hasShare)) return true;
                }
                if (iconLikeCount >= 3) return true;
                return false;
            };

            for (const node of articleNodes) {
                const text = normalize(node.innerText || "");
                if (!text || text.length < 24) continue;
                const author = extractAuthor(node);
                if (!hasExpectedAuthor(text, author)) continue;
                const permalink = extractPermalink(node);
                const key = `${permalink || "no_link"}::${text.slice(0, 160)}`;
                if (seen.has(key)) continue;
                seen.add(key);
                posts.push({ permalink: permalink || null, text: text.slice(0, 800), author: author || null });
                if (posts.length >= 25) break;
            }

            if (posts.length < 5) {
                for (const anchor of anchors) {
                    const href = absolutize(anchor.getAttribute("href") || "");
                    if (!href) continue;
                    const container =
                        anchor.closest("article") ||
                        anchor.closest('div[role="article"]') ||
                        anchor.closest('div[data-ft]') ||
                        anchor.closest("section") ||
                        anchor.parentElement;
                    if (!container) continue;
                    const text = normalize(container.innerText || "");
                    if (!text || text.length < 24) continue;
                    const author = extractAuthor(container);
                    if (!hasExpectedAuthor(text, author)) continue;
                    const key = `${href || "no_link"}::${text.slice(0, 160)}`;
                    if (seen.has(key)) continue;
                    seen.add(key);
                    posts.push({ permalink: href || null, text: text.slice(0, 800), author: author || null });
                    fallback_container_hits += 1;
                    if (posts.length >= 25) break;
                }
            }

            if (posts.length < 5) {
                const authorSeeds = Array.from(document.querySelectorAll("a, strong, h3, h4, span, div"))
                    .filter((el) => {
                        const text = normalize(el.innerText);
                        if (!text || text.length < 3 || text.length > 120) return false;
                        return hasExpectedAuthor(text, text);
                    })
                    .slice(0, 120);

                for (const seed of authorSeeds) {
                    let node = seed;
                    for (let depth = 0; depth < 10; depth++) {
                        node = node ? node.parentElement : null;
                        if (!node) break;
                        const text = normalize(node.innerText || "");
                        if (!text || text.length < 40 || text.length > 6000) continue;
                        const rect = node.getBoundingClientRect();
                        if (!rect || rect.height <= 0 || rect.height > (window.innerHeight * 2.5)) continue;
                        if (!hasEngagementControls(node)) continue;
                        const author = extractAuthor(node);
                        if (!hasExpectedAuthor(text, author)) continue;
                        const permalink = extractPermalink(node);
                        const key = `${permalink || "no_link"}::${text.slice(0, 160)}`;
                        if (seen.has(key)) continue;
                        seen.add(key);
                        posts.push({ permalink: permalink || null, text: text.slice(0, 800), author: author || null });
                        fallback_container_hits += 1;
                        break;
                    }
                    if (posts.length >= 25) break;
                }
            }

            if (posts.length < 5) {
                const engagementControls = Array.from(document.querySelectorAll([
                    "div[role='button'][aria-label*='comment' i]",
                    "div[role='button'][aria-label*='comments' i]",
                    "div[role='button'][aria-label*='share' i]",
                    "div[role='button'][aria-label*='like' i]",
                    "a[role='button'][aria-label*='comment' i]",
                    "a[role='button'][aria-label*='share' i]",
                    "a[role='button'][aria-label*='like' i]"
                ].join(", "))).slice(0, 280);

                for (const control of engagementControls) {
                    let node = control;
                    for (let depth = 0; depth < 10; depth++) {
                        node = node ? node.parentElement : null;
                        if (!node) break;
                        const rect = node.getBoundingClientRect();
                        if (!rect || rect.height < 70 || rect.height > (window.innerHeight * 3.0)) continue;
                        const text = normalize(node.innerText || "");
                        if (!text || text.length < 40 || text.length > 7000) continue;
                        if (!hasEngagementControls(node)) continue;

                        const author = extractAuthor(node);
                        const authorMatch = hasExpectedAuthor(text, author);
                        const inlineLabels = Array.from(
                            node.querySelectorAll("div[role='button'], a[role='link'], span, strong, h3, h4")
                        )
                            .map((el) =>
                                normalize(
                                    `${el.getAttribute("aria-label") || ""} ${el.innerText || ""}`
                                ).toLowerCase()
                            )
                            .filter(Boolean)
                            .slice(0, 90);
                        const weakAuthorMatch = expectedTokens.length
                            ? inlineLabels.some((label) => expectedTokens.every((token) => label.includes(token)))
                            : false;
                        if (!authorMatch && !weakAuthorMatch) continue;

                        const permalink = extractPermalink(node);
                        const key = `${permalink || "no_link"}::${text.slice(0, 160)}`;
                        if (seen.has(key)) continue;
                        seen.add(key);
                        posts.push({ permalink: permalink || null, text: text.slice(0, 800), author: author || null });
                        fallback_container_hits += 1;
                        engagement_container_hits += 1;
                        break;
                    }
                    if (posts.length >= 25) break;
                }
            }

            const tabTexts = Array.from(document.querySelectorAll('a, div[role="tab"], span'))
                .map((el) => normalize(el.innerText).toLowerCase())
                .filter(Boolean);
            const profileTabHits = ["posts", "about", "friends", "photos", "reels", "more"].filter((name) =>
                tabTexts.includes(name)
            ).length;
            const profile_surface_detected = profileTabHits >= 2;
            const go_to_profile_visible = Array.from(document.querySelectorAll('a, div[role="button"], span')).some((el) => {
                const text = normalize(el.innerText).toLowerCase();
                const aria = normalize(el.getAttribute("aria-label")).toLowerCase();
                return text.includes("go to profile") || aria.includes("go to profile");
            });

            return {
                profile_name_seen,
                profile_avatar_seen,
                body_text: normalize(document.body ? document.body.innerText : "").slice(0, 5000),
                posts,
                profile_surface_detected,
                go_to_profile_visible,
                profile_tab_hits: profileTabHits,
                article_nodes_count: articleNodes.length,
                anchor_candidates_count: anchors.length,
                fallback_container_hits,
                engagement_container_hits,
                candidate_names: profileNameCandidates.slice(0, 30),
                current_url: window.location.href || "",
                title: normalize(document.title || ""),
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


async def _open_posts_tab_if_available(page) -> bool:
    try:
        clicked = await page.evaluate(
            """() => {
                const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim().toLowerCase();
                const candidates = Array.from(document.querySelectorAll('a, div[role="tab"], div[role="button"], span'));
                for (const el of candidates) {
                    const text = normalize(el.innerText);
                    if (text !== "posts") continue;
                    const target =
                        el.closest('a, div[role="tab"], div[role="button"]') ||
                        el;
                    if (target && typeof target.click === "function") {
                        target.click();
                        return true;
                    }
                }
                return false;
            }"""
        )
        return bool(clicked)
    except Exception:
        return False


async def _open_go_to_profile_if_available(page) -> bool:
    try:
        clicked = await page.evaluate(
            """() => {
                const normalize = (value) => (value || "").replace(/\\s+/g, " ").trim().toLowerCase();
                const candidates = Array.from(document.querySelectorAll('a, div[role="button"], span'));
                for (const el of candidates) {
                    const text = normalize(el.innerText);
                    const aria = normalize(el.getAttribute("aria-label"));
                    if (text.includes("go to profile") || aria.includes("go to profile")) {
                        const target = el.closest('a, div[role="button"]') || el;
                        if (target && typeof target.click === "function") {
                            target.click();
                            return true;
                        }
                    }
                }
                return false;
            }"""
        )
        return bool(clicked)
    except Exception:
        return False


async def _collect_snapshot_with_scroll(page, expected_profile_name: str, required_posts: int, user_id: Optional[str]) -> Dict:
    best_snapshot: Dict = {}
    best_score = -9999

    for idx in range(3):
        snapshot = await _extract_profile_snapshot(page, expected_profile_name)
        score_data = _snapshot_score(snapshot, expected_profile_name, user_id)
        score = int(score_data.get("score", 0))
        if score > best_score:
            best_snapshot = snapshot
            best_score = score
        enough_posts = int(score_data.get("posts_count", 0)) >= int(required_posts)
        strict_name = bool(score_data.get("strict_name_match"))
        if enough_posts and strict_name:
            break
        if idx < 2:
            await page.mouse.wheel(0, 850)
            await asyncio.sleep(1.0)

    return best_snapshot or await _extract_profile_snapshot(page, expected_profile_name)


async def _navigate_to_best_profile_surface(page, session: FacebookSession, profile_name: str, required_posts: int) -> Tuple[Dict, str]:
    best_snapshot: Dict = {}
    best_url = _profile_url(session, profile_name)
    best_score = -9999
    user_id = _session_user_id(session)

    for candidate_url in _profile_candidate_urls(session, profile_name):
        try:
            await page.goto(candidate_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as nav_exc:
            if "ERR_TUNNEL_CONNECTION_FAILED" in str(nav_exc):
                logger.warning(f"precheck tunnel error at {candidate_url} for {profile_name}")
                continue
            logger.warning(f"precheck navigation failed at {candidate_url} for {profile_name}: {nav_exc}")
            continue

        await asyncio.sleep(2.5)
        if await _has_broken_link_banner(page):
            logger.warning(f"precheck broken-link banner at {candidate_url} for {profile_name}")
            continue

        opened_profile = await _open_go_to_profile_if_available(page)
        if opened_profile:
            await asyncio.sleep(2.0)
        posts_tab_clicked = await _open_posts_tab_if_available(page)
        if posts_tab_clicked:
            await asyncio.sleep(1.5)

        snapshot = await _collect_snapshot_with_scroll(page, profile_name, required_posts, user_id)
        snapshot["current_url"] = page.url
        score_data = _snapshot_score(snapshot, profile_name, user_id)
        score = int(score_data.get("score", 0))
        if score > best_score:
            best_snapshot = snapshot
            best_url = page.url
            best_score = score

        strict_name = bool(score_data.get("strict_name_match"))
        enough_posts = int(score_data.get("posts_count", 0)) >= int(required_posts)
        if strict_name and enough_posts:
            break

    if not best_snapshot:
        best_snapshot = await _extract_profile_snapshot(page, profile_name)
        best_snapshot["current_url"] = page.url
        best_url = page.url

    return best_snapshot, best_url


async def run_feed_safety_precheck(
    *,
    profile_name: str,
    caption: str,
    lookback_posts: int = 5,
    threshold: float = 0.90,
    run_id: Optional[str] = None,
    cycle_index: Optional[int] = None,
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
            snapshot, resolved_profile_url = await _navigate_to_best_profile_surface(
                page,
                session,
                profile_name,
                required_posts,
            )
            profile_page_url = resolved_profile_url

            screenshot_suffix = f"{str(run_id or 'adhoc').replace('-', '')[:12]}_{int(cycle_index or 0)}"
            before_screenshot = await save_debug_screenshot(page, f"premium_precheck_before_{screenshot_suffix}")
            await page.mouse.wheel(0, 300)
            await asyncio.sleep(0.7)
            refreshed_snapshot = await _extract_profile_snapshot(page, profile_name)
            refreshed_snapshot["current_url"] = page.url
            refreshed_score = _snapshot_score(refreshed_snapshot, profile_name, _session_user_id(session))
            existing_score = _snapshot_score(snapshot, profile_name, _session_user_id(session))
            if int(refreshed_score.get("score", 0)) >= int(existing_score.get("score", 0)):
                snapshot = refreshed_snapshot
            after_screenshot = await save_debug_screenshot(page, f"premium_precheck_after_{screenshot_suffix}")

            profile_name_seen = str(snapshot.get("profile_name_seen") or "").strip()
            profile_avatar_seen = str(snapshot.get("profile_avatar_seen") or "").strip()
            body_text = str(snapshot.get("body_text") or "")
            expected_avatar = str((session.data or {}).get("profile_picture") or "").strip()
            expected_avatar_ref = _canonical_avatar_ref(expected_avatar)
            seen_avatar_ref = _canonical_avatar_ref(profile_avatar_seen)

            user_id = _session_user_id(session)
            posts = _dedupe_extracted_posts(list(snapshot.get("posts") or []))
            strict_name_match = _name_matches(profile_name, profile_name_seen)
            token_name_match = _name_tokens_present(profile_name, body_text)
            profile_surface_detected = bool(snapshot.get("profile_surface_detected"))
            url_profile_hint = _url_profile_hint(snapshot.get("current_url") or profile_page_url, user_id)
            name_match = strict_name_match or (token_name_match and (profile_surface_detected or url_profile_hint) and len(posts) > 0)
            if (not profile_name_seen) and strict_name_match:
                profile_name_seen = profile_name
            avatar_similarity = _avatar_similarity(expected_avatar, profile_avatar_seen)
            avatar_required = bool(expected_avatar_ref and seen_avatar_ref)
            avatar_passed = avatar_similarity is not None and avatar_similarity >= 0.60
            identity_passed = bool(name_match and (avatar_passed or not avatar_required))
            posts = posts[:required_posts]

            top_similarity = 0.0
            matched_permalink = None
            for post in posts:
                ratio = near_duplicate_ratio(str(caption or ""), str(post.get("text") or ""))
                if ratio > top_similarity:
                    top_similarity = ratio
                    matched_permalink = post.get("permalink")

            duplicate_block = top_similarity >= float(threshold)
            insufficient_posts = len(posts) < required_posts
            no_posts = len(posts) == 0
            duplicate_passed = (not duplicate_block) and (not insufficient_posts) and (not no_posts)

            identity_check = {
                "profile_name_expected": profile_name,
                "profile_name_seen": profile_name_seen or None,
                "profile_avatar_expected_ref": expected_avatar_ref,
                "profile_avatar_seen_ref": seen_avatar_ref,
                "name_match": bool(name_match),
                "avatar_similarity": round(float(avatar_similarity), 4) if avatar_similarity is not None else None,
                "avatar_hash_match": bool(avatar_passed) if avatar_similarity is not None else None,
                "passed": identity_passed,
                "strict_name_match": bool(strict_name_match),
                "token_name_match": bool(token_name_match),
                "profile_surface_detected": bool(profile_surface_detected),
                "url_profile_hint": bool(url_profile_hint),
            }
            duplicate_precheck = {
                "checked_posts": len(posts),
                "threshold": float(threshold),
                "top_similarity": round(float(top_similarity), 4),
                "matched_post_permalink": matched_permalink if duplicate_block else None,
                "required_posts": required_posts,
                "insufficient_posts": insufficient_posts,
                "history_limited": bool(insufficient_posts and (not no_posts)),
                "passed": duplicate_passed,
                "posts": posts,
                "profile_tab_hits": int(snapshot.get("profile_tab_hits") or 0),
                "article_nodes_count": int(snapshot.get("article_nodes_count") or 0),
                "anchor_candidates_count": int(snapshot.get("anchor_candidates_count") or 0),
                "fallback_container_hits": int(snapshot.get("fallback_container_hits") or 0),
                "engagement_container_hits": int(snapshot.get("engagement_container_hits") or 0),
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
                    else (
                        "duplicate_precheck_no_posts"
                        if no_posts
                        else ("duplicate_precheck_insufficient_posts" if insufficient_posts else "duplicate_precheck_failed")
                    )
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
