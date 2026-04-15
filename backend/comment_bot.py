import asyncio
import logging
import os
import re
import tempfile
from pathlib import Path
from playwright.async_api import async_playwright, Page
# Stealth mode is MANDATORY for anti-detection
from playwright_stealth import Stealth

from typing import Optional, Dict, Any, List, Callable, Awaitable
from urllib.parse import urlparse, unquote, parse_qs, urlencode, urlunparse

from fb_session import FacebookSession, apply_session_to_context
import fb_selectors
from config import MOBILE_VIEWPORT, DEFAULT_USER_AGENT, DEBUG_DIR
from browser_factory import build_playwright_proxy
from forensics import (
    attach_current_file_artifact,
    attach_current_json_artifact,
    build_comment_verdict,
    record_current_event,
    reset_current_forensic_recorder,
    set_current_forensic_recorder,
    start_forensic_attempt,
)

# Vision integration (optional - will work without it)
try:
    from gemini_vision import get_vision_client
    VISION_AVAILABLE = True
except ImportError:
    VISION_AVAILABLE = False
    get_vision_client = lambda: None

logger = logging.getLogger("CommentBot")

AUTH_HEALTH_HEALTHY = "healthy"
AUTH_HEALTH_LOGGED_OUT = "logged_out"
AUTH_HEALTH_CHECKPOINT = "checkpoint"
AUTH_HEALTH_HUMAN_VERIFICATION = "human_verification"
AUTH_HEALTH_VIDEO_SELFIE = "video_selfie"
AUTH_HEALTH_NEEDS_ATTENTION = "needs_attention"
AUTH_HEALTH_INFRA_BLOCKED = "infra_blocked"

AUTH_HEALTH_BLOCKING_STATES = {
    AUTH_HEALTH_LOGGED_OUT,
    AUTH_HEALTH_CHECKPOINT,
    AUTH_HEALTH_HUMAN_VERIFICATION,
    AUTH_HEALTH_VIDEO_SELFIE,
    AUTH_HEALTH_NEEDS_ATTENTION,
}


def _brief(e: Exception) -> str:
    """Truncate Playwright errors to first line (full call logs can be 60+ lines)."""
    return str(e).split("\n")[0]


os.makedirs(DEBUG_DIR, exist_ok=True)


def cleanup_old_screenshots(max_keep: int = 100):
    """Remove old screenshots, keeping only the most recent."""
    try:
        if not os.path.exists(DEBUG_DIR):
            return

        # Get all png files with timestamps
        files = []
        for f in os.listdir(DEBUG_DIR):
            if f.endswith('.png') and f != 'latest.png':
                path = os.path.join(DEBUG_DIR, f)
                files.append((path, os.path.getmtime(path)))

        # Sort by modification time (oldest first)
        files.sort(key=lambda x: x[1])

        # Remove oldest files if over limit
        removed = 0
        while len(files) > max_keep:
            oldest = files.pop(0)
            os.remove(oldest[0])
            removed += 1

        if removed > 0:
            logger.debug(f"Cleaned up {removed} old screenshots, keeping {len(files)}")
    except Exception as e:
        logger.error(f"Failed to cleanup screenshots: {e}")


def parse_comment_id_from_url(target_comment_url: str) -> Optional[str]:
    """Extract comment_id from Facebook URL query string."""
    if not target_comment_url:
        return None
    try:
        parsed = urlparse(str(target_comment_url).strip())
        query = parse_qs(parsed.query)
        comment_id = query.get("comment_id", [None])[0]
        if comment_id:
            return str(comment_id).strip()
    except Exception:
        return None
    return None


def _set_query_param(url: str, key: str, value: str) -> Optional[str]:
    """Return URL with key=value set in query string."""
    try:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        query[key] = [value]
        return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
    except Exception:
        return None


def _build_target_navigation_candidates(
    target_comment_url: str,
    current_url: str,
    target_comment_id: str,
) -> List[str]:
    """
    Build deterministic fallback URLs that preserve comment_id after FB redirect.
    """
    candidates: List[str] = []
    seen = set()

    def add(url: Optional[str]) -> None:
        if not url:
            return
        norm = str(url).strip()
        if not norm or norm in seen:
            return
        seen.add(norm)
        candidates.append(norm)

    add(target_comment_url)

    # Include current URL only when it is still a post/permalink style URL.
    current_url_l = str(current_url or "").lower()
    if "facebook.com" in current_url_l and ("story.php" in current_url_l or "permalink.php" in current_url_l):
        add(current_url)

    bases = [target_comment_url]
    if current_url and current_url in candidates:
        bases.append(current_url)

    for base in bases:
        if not base:
            continue
        add(_set_query_param(base, "comment_id", target_comment_id))

        try:
            parsed = urlparse(base)
            query = parse_qs(parsed.query)
            story_fbid = (query.get("story_fbid") or [None])[0]
            page_id = (query.get("id") or [None])[0]
            if not story_fbid or not page_id:
                continue

            for host in ["www.facebook.com", "m.facebook.com"]:
                story_query = urlencode(
                    {
                        "story_fbid": story_fbid,
                        "id": page_id,
                        "comment_id": target_comment_id,
                    }
                )
                add(f"https://{host}/story.php?{story_query}")
                add(f"https://{host}/permalink.php?{story_query}")
        except Exception:
            continue

    return candidates


def _prepare_reply_image_for_upload(image_path: str) -> str:
    """
    Convert WEBP to a temporary JPEG for upload compatibility when needed.
    Returns original path on failure or when conversion is not needed.
    """
    if Path(image_path).suffix.lower() != ".webp":
        return image_path

    tmp_path = ""
    try:
        from PIL import Image

        fd, tmp_path = tempfile.mkstemp(prefix="fb_reply_", suffix=".jpg")
        os.close(fd)
        with Image.open(image_path) as src:
            src.convert("RGB").save(tmp_path, format="JPEG", quality=95)
        logger.info(f"Prepared JPEG fallback for WEBP reply image: {tmp_path}")
        return tmp_path
    except Exception as e:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        logger.warning(f"WEBP->JPEG reply image conversion failed, using original: {e}")
        return image_path


def _has_strong_reply_submission_evidence(evidence: Dict[str, Any]) -> bool:
    """
    Treat submit flow as successful when we have strong local evidence even if
    remote verify navigation is flaky.
    """
    return all(
        [
            bool(evidence.get("submit_clicked")),
            bool(evidence.get("image_attached")),
            bool(evidence.get("text_after_attach_verified")),
            bool(evidence.get("posting_indicator_seen") or evidence.get("local_comment_text_seen")),
        ]
    )


def _normalize_submission_text(value: str) -> str:
    """Normalize visible text before comment snippet matching."""
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _has_local_typed_text_evidence(local_presence: Optional[Dict[str, Any]]) -> bool:
    local_presence = local_presence or {}
    return bool(
        local_presence.get("composerTextVisible")
        or local_presence.get("activeElementContainsText")
    )


async def _collect_typed_text_presence(page: Page, comment_text: str) -> Dict[str, Any]:
    snippet = str(comment_text or "")[-120:]
    return await page.evaluate(
        """(snippet) => {
            const norm = (value) => (value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
            const snippetNorm = norm(snippet);
            const selectors = [
                'textarea',
                'input',
                '[contenteditable="true"]',
                '[role="textbox"]',
                '[role="combobox"]'
            ];
            const nodes = [];
            for (const selector of selectors) {
                for (const node of document.querySelectorAll(selector)) {
                    if (!nodes.includes(node)) {
                        nodes.push(node);
                    }
                }
            }

            const readValue = (node) => norm(node && (node.value || node.innerText || node.textContent || ''));
            const composerTextVisible = nodes.some((node) => {
                if (!snippetNorm) {
                    return false;
                }
                return readValue(node).includes(snippetNorm);
            });

            const activeValue = readValue(document.activeElement);

            return {
                composerTextVisible,
                activeElementContainsText: Boolean(snippetNorm) && activeValue.includes(snippetNorm),
                activeElementTag: document.activeElement ? document.activeElement.tagName || '' : '',
                activeElementRole: document.activeElement ? document.activeElement.getAttribute('role') || '' : '',
            };
        }""",
        snippet,
    )


def _is_composer_element(element: Dict[str, Any]) -> bool:
    """Best-effort composer detection for element-dump entries."""
    tag = str(element.get("tag") or "").lower()
    role = str(element.get("role") or "").lower()
    aria_label = str(element.get("ariaLabel") or "").lower()
    placeholder = str(element.get("placeholder") or "").lower()
    text = str(element.get("text") or "").lower()
    haystack = " ".join([tag, role, aria_label, placeholder, text])
    return any(
        marker in haystack
        for marker in ["write a comment", "textbox", "combobox", "textarea", "contenteditable"]
    )


async def _collect_comment_submission_evidence(
    page: Page,
    comment_text: str,
    interactive_elements: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Collect local post-submit evidence before falling back to screenshot verification."""
    snippet = str(comment_text or "")[-120:]
    snippet_norm = _normalize_submission_text(snippet)

    local_state = await page.evaluate(
        """(snippet) => {
            const norm = (value) => (value || '').toLowerCase().replace(/\\s+/g, ' ').trim();
            const snippetNorm = norm(snippet);
            const bodyText = norm(document.body ? document.body.innerText : '');
            const composerSelectors = [
                'textarea',
                'input',
                '[contenteditable="true"]',
                '[role="textbox"]',
                '[role="combobox"]'
            ];
            const composerNodes = Array.from(document.querySelectorAll(composerSelectors.join(',')));
            const composerTextStillPresent = composerNodes.some((node) => {
                const value = norm(node.value || node.innerText || node.textContent || '');
                return snippetNorm && value.includes(snippetNorm);
            });

            return {
                bodyTextContainsSnippet: Boolean(snippetNorm) && bodyText.includes(snippetNorm),
                composerTextStillPresent,
                composerCleared: !composerTextStillPresent,
                postingIndicatorSeen: bodyText.includes('posting...')
            };
        }""",
        snippet,
    )

    interactive_text_seen = False
    if snippet_norm and interactive_elements:
        for element in interactive_elements:
            if _is_composer_element(element):
                continue
            haystack = _normalize_submission_text(
                f"{element.get('text') or ''} {element.get('ariaLabel') or ''}"
            )
            if haystack and snippet_norm in haystack:
                interactive_text_seen = True
                break

    local_comment_text_seen = bool(
        local_state.get("bodyTextContainsSnippet") and not local_state.get("composerTextStillPresent")
    )

    return {
        "submit_clicked": True,
        "posting_indicator_seen": bool(local_state.get("postingIndicatorSeen")),
        "composer_cleared": bool(local_state.get("composerCleared")),
        "composer_text_still_present": bool(local_state.get("composerTextStillPresent")),
        "local_comment_text_seen": local_comment_text_seen,
        "interactive_text_seen": interactive_text_seen,
    }


def _has_strong_comment_submission_evidence(evidence: Dict[str, Any]) -> bool:
    """Recognize submit-like evidence that warrants recovery instead of reposting."""
    if not evidence.get("submit_clicked"):
        return False
    if evidence.get("local_comment_text_seen") or evidence.get("interactive_text_seen"):
        return True
    return bool(evidence.get("posting_indicator_seen") and evidence.get("composer_cleared"))


def _escape_css_attr_value(value: str) -> str:
    """Escape quotes/backslashes for safe CSS attribute selectors."""
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _selector_candidates_from_dump(elements: List[dict], intent: str) -> List[str]:
    """
    Derive selector candidates from live element dump, so we adapt to real UI attrs.
    """
    intent_keywords = {
        "reply_button": ["reply"],
        "reply_attach": ["photo", "image", "attach", "camera", "gallery"],
    }
    keywords = intent_keywords.get(intent, [])
    if not keywords:
        return []

    selectors: List[str] = []
    seen = set()

    def add(selector: str) -> None:
        if selector and selector not in seen:
            seen.add(selector)
            selectors.append(selector)

    for el in elements:
        aria_label = str(el.get("ariaLabel") or "").strip()
        text = str(el.get("text") or "").strip()
        role = str(el.get("role") or "").strip()
        sigil = str(el.get("sigil") or "").strip()
        haystack = f"{aria_label} {text}".lower()

        if not any(keyword in haystack for keyword in keywords):
            continue

        if aria_label:
            esc_aria = _escape_css_attr_value(aria_label)
            add(f'[aria-label="{esc_aria}"]')
            add(f'button[aria-label="{esc_aria}"]')
            if role:
                esc_role = _escape_css_attr_value(role)
                add(f'[role="{esc_role}"][aria-label="{esc_aria}"]')
        if sigil:
            esc_sigil = _escape_css_attr_value(sigil)
            add(f'[data-sigil="{esc_sigil}"]')

    return selectors


async def _is_target_comment_context_present(page: Page, target_comment_id: str) -> bool:
    """
    Strict target-id gate:
    The page must contain context that references the exact comment_id.
    """
    if not target_comment_id:
        return False
    try:
        return await page.evaluate(
            """(commentId) => {
                const directNeedle = `comment_id=${commentId}`;
                const encodedNeedle = `comment_id%3D${encodeURIComponent(commentId)}`;
                const doubleEncodedNeedle = `comment_id%253D${encodeURIComponent(commentId)}`;
                const hrefNeedles = [directNeedle, encodedNeedle, doubleEncodedNeedle];

                const currentHref = window.location && window.location.href ? window.location.href : "";
                if (hrefNeedles.some((needle) => currentHref.includes(needle))) return true;

                const selectorParts = [
                    `[href*="${directNeedle}"]`,
                    `[href*="${encodedNeedle}"]`,
                    `[href*="${doubleEncodedNeedle}"]`,
                    `[data-ft*="${commentId}"]`,
                    `[id*="${commentId}"]`,
                ];
                if (document.querySelector(selectorParts.join(", "))) return true;

                const all = document.querySelectorAll("a[href], div, span");
                for (const el of all) {
                    const href = el.getAttribute && el.getAttribute("href");
                    if (href && hrefNeedles.some((needle) => href.includes(needle))) return true;
                    if (href && href.includes(commentId)) return true;
                    const text = (el.textContent || "").slice(0, 400);
                    if (text.includes(commentId)) return true;
                }
                return false;
            }""",
            target_comment_id,
        )
    except Exception as e:
        logger.warning(f"Failed strict target comment context check: {e}")
        return False


async def _click_reply_button_for_target(page: Page, target_comment_id: str) -> bool:
    """Try to click the reply action nearest to the target comment context."""
    if not target_comment_id:
        return False

    # 1) Target-aware DOM lookup by comment link context.
    try:
        clicked = await page.evaluate(
            """(commentId) => {
                const directNeedle = `comment_id=${commentId}`;
                const encodedNeedle = `comment_id%3D${encodeURIComponent(commentId)}`;
                const doubleEncodedNeedle = `comment_id%253D${encodeURIComponent(commentId)}`;
                const needles = [directNeedle, encodedNeedle, doubleEncodedNeedle];

                const links = Array.from(document.querySelectorAll('a[href]'));
                let target = links.find((a) => {
                    const href = a.getAttribute("href") || "";
                    return needles.some((needle) => href.includes(needle)) || href.includes(commentId);
                });
                if (!target) {
                    const all = Array.from(document.querySelectorAll("[id], [data-ft]"));
                    target = all.find((el) => {
                        const id = el.getAttribute("id") || "";
                        const ft = el.getAttribute("data-ft") || "";
                        return id.includes(commentId) || ft.includes(commentId);
                    });
                }
                if (!target) return false;

                let scope = target.closest('[role="article"], li, div') || target.parentElement;
                let depth = 0;
                while (scope && depth < 5) {
                    const candidates = Array.from(scope.querySelectorAll('[aria-label], [role="button"], button, a'));
                    const replyButton = candidates.find((el) => {
                        const aria = (el.getAttribute("aria-label") || "").toLowerCase();
                        const text = (el.textContent || "").toLowerCase();
                        return aria.includes("reply") || text.trim() === "reply";
                    });
                    if (replyButton) {
                        replyButton.click();
                        return true;
                    }
                    scope = scope.parentElement;
                    depth += 1;
                }
                return false;
            }""",
            target_comment_id,
        )
        if clicked:
            logger.info(f"Clicked reply button near target comment_id={target_comment_id}")
            return True
    except Exception as e:
        logger.debug(f"Target-aware reply click failed: {e}")

    # 2) Fallback selectors discovered from live dump + static selector set.
    discovered = []
    try:
        elements = await dump_interactive_elements(page, "REPLY BUTTON SELECTOR DISCOVERY")
        discovered = _selector_candidates_from_dump(elements, "reply_button")
    except Exception:
        discovered = []

    selector_pool = discovered + fb_selectors.REPLY["reply_button"]
    return await smart_click(page, selector_pool, "Reply button")


async def _attach_image_to_reply(page: Page, image_path: str) -> bool:
    """Attach image to reply composer. Returns False if image wasn't attached."""
    if not image_path or not Path(image_path).exists():
        logger.warning(f"Image path missing for attachment: {image_path}")
        return False
    upload_image_path = _prepare_reply_image_for_upload(image_path)
    cleanup_upload_path = upload_image_path if upload_image_path != image_path else None
    logger.info(f"Reply image attach source: {upload_image_path}")

    try:
        # Trigger attachment UI if present, using discovered selectors first.
        discovered_attach = []
        try:
            elements = await dump_interactive_elements(page, "REPLY ATTACH SELECTOR DISCOVERY")
            discovered_attach = _selector_candidates_from_dump(elements, "reply_attach")
        except Exception:
            discovered_attach = []

        attach_pool = discovered_attach + fb_selectors.REPLY["reply_attach_button"]

        # First try native file-chooser flow from attach icon click.
        try:
            async with page.expect_file_chooser(timeout=3000) as chooser_info:
                await smart_click(page, attach_pool, "Reply attach image")
            file_chooser = await chooser_info.value
            await file_chooser.set_files(upload_image_path)
            await asyncio.sleep(1.5)
        except Exception:
            # Fallback to direct input assignment.
            await smart_click(page, attach_pool, "Reply attach image")
            await asyncio.sleep(0.4)

        file_input_selectors = [
            'input[type="file"]',
            'input[accept*="image"]',
            'input[name*="photo"]',
        ]

        async def _read_upload_state() -> Dict[str, Any]:
            return await page.evaluate(
                """() => {
                    const norm = (s) => (s || '').toLowerCase().replace(/\\s+/g, ' ').trim();
                    const bodyText = norm(document.body ? document.body.innerText : '');

                    const interactiveText = Array.from(
                        document.querySelectorAll('button, [role="button"], h1, h2, h3, span, div')
                    )
                        .map((el) => norm(el.innerText || el.textContent))
                        .filter(Boolean);

                    const hasUploadPhotoButton = interactiveText.some((t) => t === 'upload photo' || t.includes('upload photo'));
                    const hasPreviewHeader = interactiveText.some((t) => t === 'preview');
                    const hasProcessingText = bodyText.includes('processing') || bodyText.includes('%');

                    const hasReplyComposer = !!document.querySelector(
                        'textarea, input[placeholder*="reply" i], textarea[placeholder*="reply" i], [contenteditable="true"][role="textbox"], [aria-label*="reply" i]'
                    );
                    const hasReplyHeader = interactiveText.some((t) => t === 'replies' || t.includes('write a reply'));

                    const hasImageThumb = !!document.querySelector(
                        'img[src^="blob:"], img[data-imgperflogname], [data-visualcompletion="media-vc-image"], [aria-label*="Remove photo" i], [aria-label*="remove" i]'
                    );

                    return {
                        href: window.location.href,
                        hasUploadPhotoButton,
                        hasPreviewHeader,
                        hasProcessingText,
                        hasReplyComposer,
                        hasReplyHeader,
                        hasImageThumb,
                    };
                }"""
            )

        for selector in file_input_selectors:
            try:
                locator = page.locator(selector).first
                if await locator.count() == 0:
                    continue
                await locator.set_input_files(upload_image_path)
                await asyncio.sleep(1.8)

                attached = await page.evaluate(
                    """() => {
                        const preview = document.querySelector(
                            'img[src^="blob:"], img[data-imgperflogname], [aria-label*="Remove"], [aria-label*="remove"], [data-visualcompletion="media-vc-image"]'
                        );
                        if (preview) return true;
                        const inputs = Array.from(document.querySelectorAll('input[type="file"]'));
                        return inputs.some((el) => el.files && el.files.length > 0);
                    }"""
                )
                if attached:
                    # Some FB flows require explicit "Upload photo" confirmation.
                    upload_selectors = [
                        'button:has-text("Upload photo")',
                        'div[role="button"]:has-text("Upload photo")',
                        'button:has-text("Upload")',
                        'div[role="button"]:has-text("Upload")',
                    ]

                    # Kick off upload if we are on preview flow.
                    state = await _read_upload_state()
                    if state.get("hasUploadPhotoButton") or state.get("hasPreviewHeader"):
                        await smart_click(page, upload_selectors, "Upload photo confirm")
                        await asyncio.sleep(2.0)

                    # Wait for preview/upload state to fully resolve.
                    upload_completed = False
                    last_state: Dict[str, Any] = {}
                    for i in range(120):
                        state = await _read_upload_state()
                        last_state = state

                        # Success = back on replies UI with composer and no preview/upload processing blockers.
                        if (
                            (state.get("hasReplyComposer") or state.get("hasReplyHeader"))
                            and not state.get("hasPreviewHeader")
                            and not state.get("hasUploadPhotoButton")
                            and not state.get("hasProcessingText")
                        ):
                            upload_completed = True
                            break

                        # If still on preview/upload screen, periodically tap Upload again.
                        if (state.get("hasUploadPhotoButton") or state.get("hasPreviewHeader")) and i % 10 == 0:
                            await smart_click(page, upload_selectors, "Upload photo confirm")
                        await asyncio.sleep(1.0)

                    if not upload_completed:
                        await save_debug_screenshot(page, "reply_upload_wait_timeout")
                        logger.warning(f"Image upload did not complete in preview flow: {last_state}")
                        return False
                    await save_debug_screenshot(page, "reply_image_attached")
                    logger.info("Image attachment confirmed in composer")
                    return True
            except Exception as e:
                logger.debug(f"Attach attempt failed for selector {selector}: {e}")
    finally:
        if cleanup_upload_path and os.path.exists(cleanup_upload_path):
            try:
                os.remove(cleanup_upload_path)
            except Exception as cleanup_error:
                logger.debug(f"Failed to clean up temp upload file {cleanup_upload_path}: {cleanup_error}")

    logger.warning("Failed to attach image to reply")
    return False

def _build_playwright_proxy(proxy_url: str) -> Dict[str, str]:
    """Wrapper for backward compatibility — delegates to browser_factory."""
    return build_playwright_proxy(proxy_url) or {"server": proxy_url}


async def save_debug_screenshot(page: Page, name: str) -> str:
    """Save a screenshot for debugging. Returns the path.

    Uses scale=1 to ensure screenshot pixel coordinates match viewport coordinates.
    This is critical for vision_element_click() to work correctly.
    """
    try:
        path = os.path.join(DEBUG_DIR, f"{name}.png")
        # scale=1 ensures screenshot pixels = viewport pixels (no DPI scaling)
        # timeout=10s to avoid hanging on slow pages waiting for fonts
        await page.screenshot(path=path, scale="css", timeout=10000)
        latest_path = os.path.join(DEBUG_DIR, "latest.png")
        await page.screenshot(path=latest_path, scale="css", timeout=10000)
        logger.info(f"Saved debug screenshot: {path}")
        await attach_current_file_artifact(
            "screenshot",
            path,
            metadata={"name": name, "page_url": page.url},
        )
        return path
    except Exception as e:
        logger.warning(f"Failed to save screenshot: {e}")
        return ""


async def dump_interactive_elements(page: Page, context: str = "") -> List[dict]:
    """
    Dump all interactive elements on the page with their selectors.
    Like 'Inspect Element' - shows what's ACTUALLY clickable.

    Args:
        page: Playwright page
        context: Description of when this is being called (e.g., "after page load")

    Returns:
        List of element info dicts
    """
    try:
        elements = await page.evaluate('''() => {
            const elements = [];
            const seen = new Set();

            const addElement = (el) => {
                const rect = el.getBoundingClientRect();
                // Only include visible elements in viewport
                if (rect.width > 0 && rect.height > 0 && rect.top < window.innerHeight && rect.top > -100) {
                    const textValue = (el.innerText || '').slice(0, 30).replace(/\\n/g, ' ');
                    const key = [
                        Math.round(rect.x),
                        Math.round(rect.y),
                        Math.round(rect.width),
                        Math.round(rect.height),
                        (el.getAttribute('aria-label') || '').slice(0, 40),
                        textValue.slice(0, 40),
                    ].join('|');
                    if (seen.has(key)) {
                        return;
                    }
                    seen.add(key);
                    elements.push({
                        tag: el.tagName,
                        text: textValue,
                        ariaLabel: el.getAttribute('aria-label') || '',
                        role: el.getAttribute('role') || '',
                        sigil: el.getAttribute('data-sigil') || '',
                        placeholder: el.getAttribute('placeholder') || '',
                        contentEditable: el.getAttribute('contenteditable') || '',
                        bounds: {x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height)}
                    });
                }
            };

            // Core interactive candidates.
            document.querySelectorAll(
                'button, [role="button"], a[href], input, textarea, ' +
                '[contenteditable="true"], [data-sigil], [aria-label]'
            ).forEach((el) => addElement(el));

            // Composer hints can be visible text nodes without explicit interactive attributes.
            // Include these so adaptive matching can legally target them.
            const composerPattern = /^(write something|create public post|what's on your mind\\??|share something|discuss something)/i;
            document.querySelectorAll('div, span').forEach((el) => {
                const text = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                if (!text || text.length > 80) return;
                if (!composerPattern.test(text)) return;
                addElement(el);
            });

            // Group-card hints can be rendered as plain text containers.
            // Include compact candidates so adaptive discovery can click them.
            document.querySelectorAll('a[href], div, span').forEach((el) => {
                const text = (el.innerText || '').replace(/\\s+/g, ' ').trim();
                if (!text || text.length > 140) return;
                const lowered = text.toLowerCase();
                const mentionsGroup = lowered.includes('group');
                const hasGroupSignals =
                    lowered.includes('member') ||
                    lowered.includes('join') ||
                    lowered.includes('view group') ||
                    lowered.includes('public group') ||
                    lowered.includes('private group') ||
                    lowered.includes('your group');
                if (!mentionsGroup || !hasGroupSignals) return;
                addElement(el);
            });
            return elements;
        }''')

        # Log the elements
        if context:
            logger.info(f"=== {context.upper()} ===")
        logger.info(f"Found {len(elements)} interactive elements:")
        for i, el in enumerate(elements):
            text_info = el.get('text', '')[:20] or el.get('ariaLabel', '')[:20] or el.get('placeholder', '')[:20]
            role_info = f"role=\"{el['role']}\"" if el['role'] else ""
            aria_info = f"aria-label=\"{el['ariaLabel']}\"" if el['ariaLabel'] else ""
            sigil_info = f"data-sigil=\"{el['sigil']}\"" if el['sigil'] else ""
            editable_info = "contenteditable" if el['contentEditable'] == 'true' else ""

            attrs = " ".join(filter(None, [role_info, aria_info, sigil_info, editable_info]))
            bounds = el['bounds']
            logger.info(f"  [{i}] {el['tag']} {attrs} text=\"{text_info}\" ({bounds['x']},{bounds['y']} {bounds['w']}x{bounds['h']})")

        await attach_current_json_artifact(
            "dom_snapshot",
            f"{context.lower().replace(' ', '_').replace('/', '_')[:80] or 'dom_snapshot'}.json",
            {"context": context, "page_url": page.url, "elements": elements},
            metadata={"context": context, "element_count": len(elements)},
        )
        return elements
    except Exception as e:
        logger.warning(f"Failed to dump interactive elements: {e}")
        return []


async def vision_click(page: Page, element_type: str, fallback_selectors: List[str], description: str) -> Dict[str, Any]:
    """Click an element using Gemini vision with CSS selector fallback."""
    result = {"success": False, "method": "none", "confidence": 0}
    vision = get_vision_client() if VISION_AVAILABLE else None

    if vision:
        for attempt in range(2):
            try:
                screenshot_path = await save_debug_screenshot(page, f"vision_{element_type}_{attempt}")
                if not screenshot_path:
                    continue
                location = await vision.find_element(screenshot_path=screenshot_path, element_type=element_type)
                if location and location.found and location.confidence > 0.7:
                    logger.info(f"Vision found {description} at ({location.x}, {location.y}) conf={location.confidence:.0%}")
                    await page.mouse.click(location.x, location.y)
                    await save_debug_screenshot(page, f"post_vision_click_{element_type}")
                    result["success"] = True
                    result["method"] = "vision"
                    result["confidence"] = location.confidence
                    return result
                elif location and location.found and location.confidence > 0.5:
                    # Low confidence - just retry without scrolling
                    logger.info(f"Vision low confidence ({location.confidence:.0%}), retrying...")
                    await asyncio.sleep(0.3)
            except Exception as e:
                logger.warning(f"Vision error attempt {attempt+1}: {e}")

    logger.info(f"Falling back to CSS selectors for {description}")
    if await smart_click(page, fallback_selectors, description):
        result["success"] = True
        result["method"] = "selector"
    return result


async def verify_comment_visually(page: Page, comment_text: str) -> Dict[str, Any]:
    """Verify that a comment was posted using vision."""
    result = {"verified": False, "confidence": 0, "message": ""}
    vision = get_vision_client() if VISION_AVAILABLE else None
    if not vision:
        result["verified"] = False
        result["verification_skipped"] = True
        result["message"] = "Vision not available, verification skipped"
        return result

    await asyncio.sleep(2)
    screenshot_path = await save_debug_screenshot(page, "verify_comment")
    if not screenshot_path:
        result["message"] = "Failed to take screenshot"
        return result

    try:
        verification = await vision.verify_comment_posted(screenshot_path=screenshot_path, expected_comment=comment_text)
        result["confidence"] = verification.confidence
        result["message"] = verification.message
        if verification.success:
            logger.info(f"Comment verified: {verification.message}")
            result["verified"] = True
        elif verification.status == "pending":
            await asyncio.sleep(3)
            screenshot_path = await save_debug_screenshot(page, "verify_retry")
            if screenshot_path:
                verification = await vision.verify_comment_posted(screenshot_path, comment_text)
                result["verified"] = verification.success
                result["confidence"] = verification.confidence
    except Exception as e:
        logger.error(f"Verification error: {e}")
        result["message"] = str(e)
    return result


async def smart_click(page: Page, selectors: List[str], description: str) -> bool:
    """
    Try to click an element using multiple selectors.
    Uses Playwright's native .click() which handles actionability and overlapping elements.
    Falls back to dispatch_event for elements that need synthetic clicks.

    For send/post buttons, tries .last to handle stacked button layouts where
    the send button appears on top of other buttons when text is entered.
    """
    logger.info(f"=== ATTEMPTING CLICK: {description} ===")
    logger.info(f"Trying {len(selectors)} selectors...")

    # For send buttons, try last element first (topmost in stacked layout)
    is_send_button = "send" in description.lower() or "post" in description.lower()

    for selector in selectors:
        try:
            all_matches = page.locator(selector)
            count = await all_matches.count()
            logger.info(f"  Selector '{selector}' → found {count} element(s)")

            if count > 0:
                # For send buttons with multiple matches, try last first (topmost element)
                if is_send_button and count > 1:
                    locators_to_try = [all_matches.last, all_matches.first]
                    logger.info(f"  → Send button with {count} matches, trying last first")
                else:
                    locators_to_try = [all_matches.first]

                for locator in locators_to_try:
                    try:
                        # Snapshot before action for live view
                        await save_debug_screenshot(page, f"pre_click_{description.replace(' ', '_')}")

                        if await locator.is_visible():
                            # Try native click first - handles overlapping elements and React events better
                            try:
                                await locator.click(timeout=3000)
                                logger.info(f"  → CLICKED (native) successfully via: {selector}")
                                await save_debug_screenshot(page, f"post_click_{description.replace(' ', '_')}")
                                return True
                            except Exception as click_err:
                                # Native click failed (maybe obscured), try dispatch_event as fallback
                                logger.info(f"  → Native click failed ({click_err}), trying dispatch_event...")
                                try:
                                    await locator.dispatch_event('click')
                                    logger.info(f"  → CLICKED (dispatch_event) successfully via: {selector}")
                                    await save_debug_screenshot(page, f"post_click_{description.replace(' ', '_')}")
                                    return True
                                except Exception as dispatch_err:
                                    logger.info(f"  → dispatch_event also failed: {dispatch_err}")
                        else:
                            logger.info(f"  → Found but not visible, skipping")
                    except Exception as loc_err:
                        logger.info(f"  → Locator attempt failed: {loc_err}")
                        continue
        except Exception as e:
            continue

    # Fallback: Text search
    try:
        text_locator = page.get_by_text(description, exact=False).first
        if await text_locator.count() > 0 and await text_locator.is_visible():
            try:
                await text_locator.click(timeout=3000)
                logger.info(f"Clicked '{description}' using text match (native click)")
                return True
            except Exception as e:
                logger.debug(f"Native click failed for text match '{description}': {e}")
                await text_locator.dispatch_event('click')
                logger.info(f"Clicked '{description}' using text match dispatch_event")
                return True
    except Exception as e:
        logger.debug(f"Text match fallback failed for '{description}': {e}")

    logger.warning(f"  → FAILED: No selector matched for '{description}'")
    await save_debug_screenshot(page, f"failed_click_{description.replace(' ', '_')}")
    return False


async def smart_focus(page: Page, selectors: List[str], description: str) -> bool:
    """
    Focus a text input field (contenteditable, textbox, textarea).
    Uses focus() instead of dispatch_event('click') which doesn't work for inputs.
    FB_LOGIN_GUIDE.md: dispatch_event works for buttons, but text fields need focus().
    """
    logger.info(f"smart_focus: Looking for '{description}' with {len(selectors)} selectors")
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            count = await locator.count()
            logger.info(f"  Selector '{selector}' → found {count} element(s)")
            if count > 0:
                # No scroll - element should already be visible
                await save_debug_screenshot(page, f"pre_focus_{description.replace(' ', '_')}")

                if await locator.is_visible():
                    await locator.focus()
                    logger.info(f"Focused '{description}' using: {selector}")
                    await save_debug_screenshot(page, f"post_focus_{description.replace(' ', '_')}")
                    return True
        except Exception as e:
            logger.warning(f"  Focus error on '{selector}': {_brief(e)}")
            continue

    logger.warning(f"Failed to focus: {description}")
    await save_debug_screenshot(page, f"failed_focus_{description.replace(' ', '_')}")
    return False


async def find_comment_input(page: Page) -> bool:
    """
    Find and activate the comment input using Playwright's semantic locators.
    After clicking the placeholder, we need to wait and then focus the actual input.
    """
    logger.info("find_comment_input: Trying Playwright semantic locators")

    # Strategy 1: Playwright semantic locators (most reliable for text-based elements)
    strategies = [
        ("get_by_placeholder('Write a comment...')", page.get_by_placeholder("Write a comment...")),
        ("get_by_placeholder('Write a comment', exact=False)", page.get_by_placeholder("Write a comment", exact=False)),
        ("get_by_text('Write a comment...')", page.get_by_text("Write a comment...")),
        ("get_by_text('Write a comment', exact=False)", page.get_by_text("Write a comment", exact=False)),
        ("get_by_role('textbox')", page.get_by_role("textbox")),
    ]

    for name, locator in strategies:
        try:
            count = await locator.count()
            logger.info(f"  {name} → found {count} element(s)")
            if count > 0:
                # No scroll - element should already be visible
                if await locator.first.is_visible():
                    # Click to activate the input
                    await locator.first.click()
                    logger.info(f"Clicked comment input using: {name}")

                    # Wait for UI to respond after click
                    await asyncio.sleep(0.5)

                    editable_locator, editable_selector = await _resolve_comment_input_locator(page)
                    if editable_locator is None:
                        logger.info("Clicked comment placeholder but no editable composer appeared yet")
                        continue

                    try:
                        await editable_locator.focus()
                    except Exception:
                        try:
                            await editable_locator.click()
                        except Exception:
                            pass

                    logger.info(f"Focused editable comment composer using: {editable_selector}")
                    await save_debug_screenshot(page, "clicked_comment_input")
                    return True
        except Exception as e:
            logger.debug(f"  {name} failed: {e}")

    logger.warning("find_comment_input: All strategies failed")
    return False


async def _resolve_comment_input_locator(page: Page):
    selectors = [
        'div[contenteditable="true"]',
        '[contenteditable="true"]',
        'textarea',
        'input:not([type]), input[type="text"], input[type="search"]',
    ]

    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = await locator.count()
            if count <= 0:
                continue

            candidates = [locator.last] if count > 1 else [locator.first]
            if count > 1:
                candidates.append(locator.first)

            for candidate in candidates:
                if await candidate.is_visible():
                    return candidate, selector
        except Exception:
            continue

    return None, None


async def _type_comment_into_active_input(page: Page, comment: str) -> Dict[str, Optional[str]]:
    locator, selector = await _resolve_comment_input_locator(page)
    if locator is not None:
        try:
            await locator.click()
        except Exception:
            pass

        try:
            await locator.fill(comment)
            return {"method": "locator.fill", "selector": selector}
        except Exception as fill_error:
            logger.info(f"locator.fill failed for comment input ({selector}): {fill_error}")

        try:
            await locator.type(comment, delay=50)
            return {"method": "locator.type", "selector": selector}
        except Exception as type_error:
            logger.info(f"locator.type failed for comment input ({selector}): {type_error}")

    await page.keyboard.type(comment, delay=50)
    return {"method": "page.keyboard.type", "selector": selector}


async def audit_selectors(page: Page, selectors_dict: dict) -> dict:
    """
    Run all selectors and report matches with details.
    Used for diagnostics when clicks fail.
    """
    audit = {}
    for action, selectors in selectors_dict.items():
        audit[action] = []
        for selector in selectors:
            try:
                locator = page.locator(selector)
                count = await locator.count()
                if count > 0:
                    text = await locator.first.text_content() or ""
                    visible = await locator.first.is_visible()
                    audit[action].append({
                        "selector": selector,
                        "count": count,
                        "visible": visible,
                        "text": text[:50] if text else ""
                    })
            except Exception:
                pass
    return audit


async def click_with_healing(
    page: Page,
    vision,
    selectors: List[str],
    description: str,
    max_attempts: int = 5
) -> dict:
    """
    Self-healing click loop - uses CSS selectors first, asks Gemini for guidance on failure.

    Returns:
        dict with success, method, selector_used, attempts, and any diagnostic info
    """
    import json

    result = {
        "success": False,
        "method": None,
        "selector_used": None,
        "attempts": 0,
        "decisions": []
    }

    for attempt in range(max_attempts):
        result["attempts"] = attempt + 1
        logger.info(f"click_with_healing attempt {attempt + 1}/{max_attempts} for '{description}'")

        # 1. Try CSS selectors first (fast, deterministic)
        click_success = await smart_click(page, selectors, description)
        if click_success:
            result["success"] = True
            result["method"] = "css_selector"
            logger.info(f"✓ Clicked '{description}' via CSS selector")
            return result

        # 2. CSS failed - get diagnostics
        screenshot = await save_debug_screenshot(page, f"healing_{description.replace(' ', '_')}_{attempt}")
        audit = await audit_selectors(page, fb_selectors.COMMENT)
        logger.info(f"Selector audit: {json.dumps(audit, indent=2)}")

        # 3. Ask Gemini what to do (if vision available)
        if vision:
            decision = await vision.decide_next_action(screenshot, description, audit)
            result["decisions"].append(decision)
            logger.info(f"Gemini decision: {decision}")

            # 4. Execute Gemini's decision
            action = decision.get("action", "RETRY")

            if action == "ABORT":
                reason = decision.get("reason", "unknown")
                logger.error(f"ABORT: {reason}")
                result["error"] = f"Aborted: {reason}"
                return result

            elif action == "WAIT":
                seconds = decision.get("seconds", 2)
                logger.info(f"Waiting {seconds}s as suggested by Gemini...")
                await asyncio.sleep(seconds)

            elif action == "CLOSE_POPUP":
                popup_selector = decision.get("selector", 'button[aria-label="Close"]')
                logger.info(f"Attempting to close popup: {popup_selector}")
                await smart_click(page, [popup_selector], "Close popup")
                await asyncio.sleep(0.5)

            elif action == "TRY_SELECTOR":
                new_selector = decision.get("selector")
                if new_selector:
                    logger.info(f"Trying Gemini-suggested selector: {new_selector}")
                    # Prepend to try first on next iteration
                    selectors = [new_selector] + selectors

            elif action == "SCROLL":
                # Ignore scroll suggestions - we don't scroll on permalink pages
                logger.info(f"Ignoring scroll suggestion (not needed for permalinks)")

            # RETRY just continues the loop
        else:
            # No vision - just wait and retry
            logger.warning("No vision client - waiting 2s and retrying")
            await asyncio.sleep(2)

    logger.error(f"Max attempts ({max_attempts}) reached for '{description}'")
    result["error"] = f"Max attempts reached"
    return result


async def vision_element_click(page: Page, x: int, y: int) -> bool:
    """
    Click an element at vision coordinates using multiple strategies.

    Facebook uses nested DIVs - elementFromPoint often returns a wrapper.
    We try multiple approaches to ensure the click reaches React handlers:
    1. Find deepest clickable element (role=button, actual buttons, links)
    2. Try native .click() method first
    3. Fall back to dispatchEvent with proper coordinates
    """
    try:
        result = await page.evaluate('''(coords) => {
            let element = document.elementFromPoint(coords.x, coords.y);
            if (!element) {
                return {success: false, reason: "No element at coordinates"};
            }

            // Try to find a more specific clickable element in the hierarchy
            let clickable = element;
            let current = element;

            // Walk up the tree looking for actual interactive elements
            while (current && current !== document.body) {
                const role = current.getAttribute('role');
                const tag = current.tagName.toLowerCase();

                // Prefer these clickable element types
                if (role === 'button' || tag === 'button' || tag === 'a' ||
                    current.hasAttribute('tabindex') || current.onclick) {
                    clickable = current;
                    break;
                }
                current = current.parentElement;
            }

            // First try native .click() which works better with React
            try {
                clickable.click();
                return {
                    success: true,
                    method: 'native_click',
                    tagName: clickable.tagName,
                    role: clickable.getAttribute('role') || 'none',
                    className: (clickable.className || '').substring(0, 50)
                };
            } catch (e) {
                // Fall back to dispatchEvent
                clickable.dispatchEvent(new MouseEvent('click', {
                    bubbles: true,
                    cancelable: true,
                    view: window,
                    clientX: coords.x,
                    clientY: coords.y
                }));
                return {
                    success: true,
                    method: 'dispatch_event',
                    tagName: clickable.tagName,
                    role: clickable.getAttribute('role') || 'none',
                    className: (clickable.className || '').substring(0, 50)
                };
            }
        }''', {"x": x, "y": y})

        if result.get("success"):
            logger.info(f"Clicked <{result.get('tagName')} role={result.get('role')}> at ({x}, {y}) via {result.get('method')}")
            return True
        else:
            logger.warning(f"No element at ({x}, {y}): {result.get('reason')}")
            return False
    except Exception as e:
        logger.error(f"vision_element_click error: {_brief(e)}")
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
    
    if not await smart_focus(page, input_selectors, "Comment Input"):
        return False

    await asyncio.sleep(0.5)
    
    # 2. Type the text
    try:
        await page.keyboard.type(comment, delay=50)
        logger.info(f"Typed comment: {comment[:20]}...")
        return True
    except Exception as e:
        logger.error(f"Failed to type: {_brief(e)}")
        return False


async def click_send_button(page: Page) -> bool:
    """Click the send/post button."""
    send_selectors = [
        'div[aria-label="Send"]',
        'button[aria-label="Send"]',
        '[aria-label="Send"]',
        'div[aria-label="Post"]',
        'button[aria-label="Post"]',
        '[aria-label="Post"]',
        '[data-sigil="touchable submit-comment"]',
        '[data-sigil*="submit"]',
        'div[role="button"]:has-text("Post")',
        '[role="button"][aria-label*="send" i]',
        '[role="button"][aria-label*="post" i]',
    ]
    
    if await smart_click(page, send_selectors, "Send Button"):
        return True

    # Enter key fallback removed - doesn't work on mobile FB
    logger.warning("Failed to find Send button")
    return False


async def verify_send_clicked(page: Page) -> bool:
    """Verify the comment was actually sent by checking if input is cleared."""
    await asyncio.sleep(1)
    try:
        # Check if the textbox is now empty (comment was sent)
        input_selectors = ['div[role="textbox"]', '[contenteditable="true"]']
        for selector in input_selectors:
            locator = page.locator(selector).first
            if await locator.count() > 0:
                text = await locator.inner_text()
                if text.strip() == "":
                    logger.info("Send verified: input field is now empty")
                    return True
        logger.warning("Send verification failed: input field still has content")
        return False
    except Exception as e:
        logger.warning(f"Send verification error: {e}")
        return False


def is_reels_page(url: str) -> bool:
    """Check if URL is a Reels/Watch page (not a regular post)."""
    return "/reel/" in url or "/watch/" in url or "/videos/" in url


async def classify_facebook_auth_state(page: Page) -> Dict[str, Any]:
    """Classify the current facebook shell into a real auth-health state."""
    result = {
        "health_status": AUTH_HEALTH_NEEDS_ATTENTION,
        "health_reason": "state unknown",
        "current_url": str(page.url or ""),
        "authenticated": False,
    }
    try:
        current_url = str(page.url or "").lower()
        body_text = ""
        try:
            body_text = (await page.locator("body").inner_text(timeout=3000)).lower()
        except Exception:
            body_text = (await page.text_content("body") or "").lower()
        body_text = " ".join(body_text.split())

        if any(token in current_url for token in ["neterror", "chrome-error", "about:blank"]):
            result["health_status"] = AUTH_HEALTH_INFRA_BLOCKED
            result["health_reason"] = f"infrastructure shell: {current_url or 'blank page'}"
            return result

        if any(
            token in body_text
            for token in [
                "video selfie",
                "record a video selfie",
                "take a video selfie",
                "submit a video selfie",
            ]
        ):
            result["health_status"] = AUTH_HEALTH_VIDEO_SELFIE
            result["health_reason"] = "facebook requested a video selfie"
            return result

        if (
            "checkpoint" in current_url
            or any(
                token in body_text
                for token in [
                    "confirm your identity",
                    "we need more information",
                    "review recent login",
                    "check your notifications on another device",
                    "secure your account",
                    "identity confirmation",
                ]
            )
        ):
            result["health_status"] = AUTH_HEALTH_CHECKPOINT
            result["health_reason"] = "facebook checkpoint or identity challenge"
            return result

        if any(
            token in body_text
            for token in [
                "confirm you're human",
                "confirm you’re human",
                "confirm you are human",
                "complete the security check",
                "verify that you're human",
                "verify that you’re human",
            ]
        ):
            result["health_status"] = AUTH_HEALTH_HUMAN_VERIFICATION
            result["health_reason"] = "facebook human verification challenge"
            return result

        login_form_present = False
        try:
            login_form_present = (
                await page.locator('input[name="email"], input[name="pass"], form[action*="login"]').count()
            ) > 0
        except Exception:
            login_form_present = False

        if (
            "/login" in current_url
            or login_form_present
            or (
                " log in " in f" {body_text} "
                and any(token in body_text for token in ["open app", "forgot password", "password"])
            )
        ):
            result["health_status"] = AUTH_HEALTH_LOGGED_OUT
            result["health_reason"] = "facebook login shell is visible"
            return result

        if any(
            token in body_text
            for token in [
                "loading...",
                "tap to refresh",
                "something went wrong",
                "temporarily blocked",
            ]
        ):
            result["health_status"] = AUTH_HEALTH_NEEDS_ATTENTION
            result["health_reason"] = "facebook returned a loading or error shell"
            return result

        result["health_status"] = AUTH_HEALTH_HEALTHY
        result["health_reason"] = "authenticated facebook shell confirmed"
        result["authenticated"] = True
        return result
    except Exception as exc:
        error_text = str(exc).lower()
        if any(token in error_text for token in ["timeout", "proxy", "connection", "network", "net::err", "tunnel"]):
            result["health_status"] = AUTH_HEALTH_INFRA_BLOCKED
            result["health_reason"] = _brief(exc)
            return result
        result["health_reason"] = _brief(exc)
        return result


async def verify_post_loaded(page: Page) -> bool:
    """Verify we're on a valid post page, not Reels."""
    try:
        # FAIL FAST: Reject Reels pages
        if is_reels_page(page.url):
            logger.error(f"Landed on Reels page: {page.url}")
            return False

        auth_state = await classify_facebook_auth_state(page)
        if auth_state["health_status"] in AUTH_HEALTH_BLOCKING_STATES:
            logger.warning(
                f"Rejecting unauthenticated post shell for {page.url}: "
                f"{auth_state['health_status']} ({auth_state['health_reason']})"
            )
            return False

        # 1. Check for 'From your link' (redirect success)
        if await page.get_by_text("From your link").count() > 0:
            return True

        # 2. Check for specific post elements
        if await page.locator('[data-sigil="m-feed-voice-subtitle"]').count() > 0:
            return True

        authenticated_post_selectors = [
            '[role="article"]',
            '[data-ft]',
            'a[href*="comment_id="]',
            '[aria-label*="like" i]',
            '[aria-label*="comment" i]',
        ]
        for selector in authenticated_post_selectors:
            if await page.locator(selector).count() > 0:
                return True

        # 3. Accept visible comment UI as post context (reaction row may be off-screen).
        comment_ui_selectors = [
            'textarea[aria-label*="comment" i]',
            'textarea[placeholder*="comment" i]',
            '[aria-label*="post a comment" i]',
            '[aria-label*="comments" i]',
        ]
        for selector in comment_ui_selectors:
            if await page.locator(selector).count() > 0:
                return True

        await save_debug_screenshot(page, "verification_failed")
        return False # Return False if we can't confirm, but caller might proceed anyway
    except Exception as e:
        logger.warning(f"Post verification error: {e}")
        return False


async def wait_for_post_visible(page: Page, vision, max_attempts: int = 4) -> bool:
    """
    Smart wait: Take screenshot, check if post visible, retry with backoff if not.

    Instead of static sleep, we:
    1. Take a screenshot
    2. Ask vision if post is visible
    3. If not, wait with exponential backoff and retry
    """
    base_wait = 1.0  # Start with 1 second

    for attempt in range(max_attempts):
        # Check for Reels FIRST (fail fast)
        if is_reels_page(page.url):
            logger.error(f"Landed on Reels page: {page.url}")
            return False

        # Deterministic fallback: accept known post/permalink indicators.
        if await verify_post_loaded(page):
            logger.info(f"Post visible via deterministic fallback on attempt {attempt + 1}")
            await dump_interactive_elements(page, "PAGE LOADED - deterministic post fallback")
            return True

        screenshot = await save_debug_screenshot(page, f"wait_attempt_{attempt}")
        verification = await vision.verify_state(screenshot, "post_visible")

        if verification.success:
            auth_state = await classify_facebook_auth_state(page)
            if auth_state["health_status"] not in AUTH_HEALTH_BLOCKING_STATES:
                logger.info(f"Post visible on attempt {attempt + 1} (confidence: {verification.confidence:.0%})")
                # PROACTIVE AUDIT: Dump all interactive elements now that page is loaded
                await dump_interactive_elements(page, "PAGE LOADED - Gemini confirmed post visible")
                return True
            logger.warning(
                f"Gemini post-visible hit rejected by auth gate: "
                f"{auth_state['health_status']} ({auth_state['health_reason']})"
            )

        # Exponential backoff: 1s, 2s, 4s, 8s
        wait_time = base_wait * (2 ** attempt)
        logger.info(f"Post not visible yet, waiting {wait_time:.1f}s... (attempt {attempt + 1}/{max_attempts})")
        await asyncio.sleep(wait_time)

    # Final non-vision fallback before hard fail.
    if await verify_post_loaded(page):
        logger.info("Post visible via deterministic fallback after vision retries exhausted")
        await dump_interactive_elements(page, "PAGE LOADED - deterministic fallback after retries")
        return True

    logger.error(f"Post not visible after {max_attempts} attempts")
    return False


async def post_comment(
    session: FacebookSession,
    url: str,
    comment: str,
    proxy: Optional[str] = None,
    use_vision: bool = True,
    verify_post: bool = True
) -> Dict[str, Any]:
    """Post a comment using a saved session with optional AI vision."""
    result = {
        "success": False,
        "url": url,
        "comment": comment,
        "error": None,
        "verified": False,
        "method": "unknown"
    }

    if use_vision and not VISION_AVAILABLE:
        logger.warning("Vision requested but not available, using selectors")
        use_vision = False

    async with async_playwright() as p:
        user_agent = session.get_user_agent() or DEFAULT_USER_AGENT
        viewport = session.get_viewport() or MOBILE_VIEWPORT
        # System proxy only — no session proxy fallback
        active_proxy = proxy
        if not active_proxy:
            raise Exception("No proxy available — cannot launch browser without proxy")

        # Get device fingerprint for this session (timezone, locale)
        device_fingerprint = session.get_device_fingerprint()
        logger.info(f"Using device fingerprint: timezone={device_fingerprint['timezone']}, locale={device_fingerprint['locale']}")

        context_options = {
            "user_agent": user_agent,
            "viewport": viewport,
            "ignore_https_errors": True,
            "device_scale_factor": 1,  # Force 1:1 pixel mapping for vision coordinates
            "timezone_id": device_fingerprint["timezone"],
            "locale": device_fingerprint["locale"],
        }
        context_options["proxy"] = _build_playwright_proxy(active_proxy)
        logger.info(f"Using proxy: {context_options['proxy'].get('server')}")

        browser = await p.chromium.launch(headless=True, args=["--disable-notifications", "--disable-geolocation"])
        context = await browser.new_context(**context_options)

        # MANDATORY: Apply stealth mode for anti-detection
        await Stealth().apply_stealth_async(context)

        try:
            page = await context.new_page()
            if not await apply_session_to_context(context, session):
                raise Exception("Failed to apply cookies")

            logger.info(f"Navigating to {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(5)  # Wait for Facebook to fully load/redirect
            await save_debug_screenshot(page, "navigated")

            if not await verify_post_loaded(page):
                logger.warning("Could not verify post loaded, trying anyway...")

            # 1. Open Comment Box (Vision + Fallback)
            comment_selectors = ['[data-action-id="32607"]', 'div[role="button"][aria-label*="Comment"]', 'div[aria-label="Comment"]', 'span:text("Comment")']
            if use_vision:
                click_result = await vision_click(page, "comment_button", comment_selectors, "Comment button")
                if not click_result["success"]:
                    raise Exception("Could not find Comment button")
                result["method"] = click_result["method"]
            else:
                if not await open_comment_box(page):
                    raise Exception("Could not find Comment button")
                result["method"] = "selector"

            await asyncio.sleep(1)

            # 2. Focus Input Field (use focus() for text fields, NOT dispatch_event)
            input_selectors = ['div[role="textbox"]', '[contenteditable="true"]', 'textarea', 'div[aria-label="Write a comment"]']
            if use_vision:
                click_result = await vision_click(page, "comment_input", input_selectors, "Comment input")
                if not click_result["success"]:
                    # Vision failed, try focus fallback
                    logger.info("Vision failed for input, trying focus fallback")
                    if not await smart_focus(page, input_selectors, "Comment Input"):
                        raise Exception("Could not activate comment input field")
            else:
                if not await smart_focus(page, input_selectors, "Comment Input"):
                    raise Exception("Could not activate comment input field")

            await asyncio.sleep(0.5)

            # 3. Type comment
            await page.keyboard.type(comment, delay=50)
            logger.info(f"Typed: {comment[:30]}...")
            await save_debug_screenshot(page, "typed_comment")
            await asyncio.sleep(1)

            # 4. Click Send (Vision + Fallback)
            send_selectors = [
                'div[aria-label="Send"]',
                'button[aria-label="Send"]',
                '[aria-label="Send"]',
                'div[aria-label="Post"]',
                'button[aria-label="Post"]',
                '[aria-label="Post"]',
                '[data-sigil="touchable submit-comment"]',
                '[data-sigil*="submit"]',
                'div[role="button"]:has-text("Post")',
                '[role="button"][aria-label*="send" i]',
                '[role="button"][aria-label*="post" i]',
            ]
            if use_vision:
                click_result = await vision_click(page, "send_button", send_selectors, "Send button")
                if not click_result["success"]:
                    raise Exception("Could not find Send button")
            else:
                if not await click_send_button(page):
                    raise Exception("Could not find Send button")

            await asyncio.sleep(3)

            # 5. Take post-send screenshot for debugging
            await save_debug_screenshot(page, "post_send")

            # 6. Visual verification via Gemini (if available)
            if verify_post and use_vision:
                verification = await verify_comment_visually(page, comment)
                result["verified"] = verification["verified"]
                result["verification_confidence"] = verification.get("confidence", 0)
            else:
                result["verified"] = True  # Assume success if vision not used

            result["success"] = True

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"Error: {e}")
            if 'page' in locals():
                await save_debug_screenshot(page, "error_final")
        finally:
            await browser.close()

    return result


async def post_comment_verified(
    session: FacebookSession,
    url: str,
    comment: str,
    proxy: Optional[str] = None,
    enable_warmup: bool = False,
    phase_callback: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None,
    forensic_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Post a comment with AI vision VERIFICATION at every step.
    This is the robust version that verifies each action succeeded before proceeding.

    Args:
        session: FacebookSession with cookies
        url: Target post URL
        comment: Comment text to post
        proxy: Optional proxy URL
        enable_warmup: If True, perform warm-up activity before commenting
    """
    result = {
        "success": False,
        "url": url,
        "comment": comment,
        "error": None,
        "steps_completed": [],
        "method": "vision_verified"
    }

    vision = get_vision_client() if VISION_AVAILABLE else None
    if not vision:
        result["error"] = "Vision client not available - required for verified mode"
        return result

    recorder = await start_forensic_attempt(
        platform="facebook",
        engine=(forensic_context or {}).get("engine", "comment_post"),
        profile_name=session.profile_name,
        campaign_id=(forensic_context or {}).get("campaign_id"),
        job_id=(forensic_context or {}).get("job_id"),
        session_id=session.profile_name,
        parent_attempt_id=(forensic_context or {}).get("parent_attempt_id"),
        run_id=(forensic_context or {}).get("run_id"),
        trace_id=(forensic_context or {}).get("trace_id"),
        metadata={
            "url": url,
            "comment_excerpt": _brief(comment),
            "enable_warmup": enable_warmup,
            **((forensic_context or {}).get("metadata") or {}),
        },
    )
    result["attempt_id"] = recorder.attempt_id
    result["trace_id"] = recorder.trace_id
    recorder_token = set_current_forensic_recorder(recorder)

    async with async_playwright() as p:
        user_agent = session.get_user_agent() or DEFAULT_USER_AGENT
        viewport = session.get_viewport() or MOBILE_VIEWPORT
        # System proxy only — no session proxy fallback
        active_proxy = proxy
        if not active_proxy:
            raise Exception("No proxy available — cannot launch browser without proxy")

        # Get device fingerprint for this session (timezone, locale)
        device_fingerprint = session.get_device_fingerprint()
        logger.info(f"Using device fingerprint: timezone={device_fingerprint['timezone']}, locale={device_fingerprint['locale']}")

        context_options = {
            "user_agent": user_agent,
            "viewport": viewport,
            "ignore_https_errors": True,
            "device_scale_factor": 1,  # Force 1:1 pixel mapping for vision coordinates
            "timezone_id": device_fingerprint["timezone"],
            "locale": device_fingerprint["locale"],
        }
        context_options["proxy"] = _build_playwright_proxy(active_proxy)
        logger.info(f"Using proxy: {context_options['proxy'].get('server')}")

        browser = await p.chromium.launch(headless=True, args=["--disable-notifications", "--disable-geolocation"])
        context = await browser.new_context(**context_options)

        # MANDATORY: Apply stealth mode for anti-detection
        await Stealth().apply_stealth_async(context)

        try:
            page = await context.new_page()
            await recorder.attach_page(page, context)
            if not await apply_session_to_context(context, session):
                raise Exception("Failed to apply cookies")

            # ========== WARM-UP PHASE (Optional) ==========
            if enable_warmup:
                from warmup_bot import perform_warmup
                logger.info("=== WARM-UP PHASE: Browsing feed before commenting ===")
                warmup_result = await perform_warmup(page)
                if warmup_result.success:
                    logger.info(f"✓ Warm-up complete: {warmup_result.scroll_count} scrolls, {warmup_result.likes_count} likes in {warmup_result.duration_seconds:.1f}s")
                    result["warmup"] = {
                        "success": True,
                        "scrolls": warmup_result.scroll_count,
                        "likes": warmup_result.likes_count,
                        "duration": warmup_result.duration_seconds
                    }
                else:
                    logger.warning(f"Warm-up failed: {warmup_result.error} - continuing anyway")
                    result["warmup"] = {"success": False, "error": warmup_result.error}

            # ========== STEP 1: Navigate and verify post is visible ==========
            logger.info(f"Step 1: Navigating to {url}")
            await record_current_event("navigate", {"url": url}, phase="navigate", source="post_comment_verified")
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)

            # Check for Reels redirect immediately
            if is_reels_page(page.url):
                raise Exception(f"Step 1 FAILED - Navigated to Reels instead of post: {page.url}")

            # SMART WAIT: Retry with exponential backoff until post is visible
            # 6 attempts = ~63 seconds max wait (1+2+4+8+16+32)
            if not await wait_for_post_visible(page, vision, max_attempts=6):
                raise Exception("Step 1 FAILED - Post not visible after 6 attempts")

            result["steps_completed"].append("post_visible")
            await record_current_event("verification", {"step": "post_visible", "success": True}, phase="verify", source="post_comment_verified")
            logger.info("✓ Step 1: Post visible")

            # ========== STEP 2: Click comment button using self-healing loop ==========
            logger.info("Step 2: Clicking comment button (CSS selectors + Gemini healing)")

            step2_result = await click_with_healing(
                page=page,
                vision=vision,
                selectors=fb_selectors.COMMENT["comment_button"],
                description="Comment button",
                max_attempts=5
            )

            if not step2_result["success"]:
                error_msg = step2_result.get("error", "Unknown error")
                raise Exception(f"Step 2 FAILED - {error_msg}")

            # Verify comments section opened
            await asyncio.sleep(1.0)
            verify_screenshot = await save_debug_screenshot(page, "step2_verify")
            verification = await vision.verify_state(verify_screenshot, "comments_opened")

            if not verification.success:
                # Try one more click - sometimes first click doesn't register
                logger.warning("Comments not opened, trying one more click...")
                await click_with_healing(page, vision, fb_selectors.COMMENT["comment_button"], "Comment button", max_attempts=2)
                await asyncio.sleep(1.0)
                verify_screenshot = await save_debug_screenshot(page, "step2_verify_retry")
                verification = await vision.verify_state(verify_screenshot, "comments_opened")
                if not verification.success:
                    raise Exception(f"Step 2 FAILED - Comments not opened: {verification.message}")

            result["steps_completed"].append("comments_opened")
            await record_current_event("click", {"target": "comment_button", "success": True}, phase="engage", source="post_comment_verified")
            logger.info(f"✓ Step 2: Comments section opened (confidence: {verification.confidence:.0%})")

            # PROACTIVE AUDIT: Dump elements now that comments section is open
            await dump_interactive_elements(page, "COMMENTS SECTION OPENED - looking for input field")

            # ========== STEP 3: Focus comment input ==========
            logger.info("Step 3: Focusing comment input")

            # Try Playwright semantic locators first (most reliable for text elements)
            focus_success = await find_comment_input(page)

            if not focus_success:
                # Fall back to CSS selectors
                logger.info("Playwright locators failed, trying CSS selectors...")
                focus_success = await smart_focus(page, fb_selectors.COMMENT["comment_input"], "Comment input")

            if not focus_success:
                # Last resort: click_with_healing with Gemini guidance
                logger.warning("CSS selectors failed, trying click_with_healing...")
                click_result = await click_with_healing(
                    page=page,
                    vision=vision,
                    selectors=fb_selectors.COMMENT["comment_input"],
                    description="Comment input",
                    max_attempts=3
                )
                if not click_result["success"]:
                    raise Exception(f"Step 3 FAILED - Could not focus input: {click_result.get('error', 'Unknown')}")

            await asyncio.sleep(0.8)

            editable_locator, editable_selector = await _resolve_comment_input_locator(page)
            if editable_locator is None:
                raise Exception("Step 3 FAILED - Could not activate editable comment composer")

            # Skip Gemini verification for input_active - it always returns 0% in headless
            # (Playwright doesn't show visual cursor, so Gemini can't verify)
            # Step 4 will verify if typing worked by checking if text appears
            result["steps_completed"].append("input_clicked")
            await record_current_event("click", {"target": "comment_input", "success": True}, phase="compose", source="post_comment_verified")
            logger.info("✓ Step 3: Input field clicked (skipping Gemini - cursor not visible in headless)")

            # ========== STEP 4: Type comment and verify text appears ==========
            logger.info(f"Step 4: Typing comment: {comment[:30]}...")
            typing_result = await _type_comment_into_active_input(page, comment)
            await asyncio.sleep(0.8)

            screenshot = await save_debug_screenshot(page, "step4_typed")
            local_typed_presence = await _collect_typed_text_presence(page, comment)
            if _has_local_typed_text_evidence(local_typed_presence):
                logger.info(
                    "✓ Step 4: Typed text confirmed from composer DOM "
                    f"(typing_method={typing_result.get('method')}, "
                    f"selector={typing_result.get('selector')}, "
                    f"tag={local_typed_presence.get('activeElementTag')}, "
                    f"role={local_typed_presence.get('activeElementRole')})"
                )
            else:
                verification = await vision.verify_state(screenshot, "text_typed", expected_text=comment[-100:])
                if not verification.success:
                    raise Exception(f"Step 4 FAILED - Typed text not visible: {verification.message}")
                logger.info(f"✓ Step 4: Typed text visible (confidence: {verification.confidence:.0%})")

            result["steps_completed"].append("text_typed")
            await record_current_event(
                "type",
                {"length": len(comment), "success": True},
                phase="compose",
                source="post_comment_verified",
            )

            # ========== STEP 5: Click send button using self-healing loop ==========
            logger.info("Step 5: Clicking send button (CSS selectors + Gemini healing)")

            step5_result = await click_with_healing(
                page=page,
                vision=vision,
                selectors=fb_selectors.COMMENT["comment_submit"],
                description="Send button",
                max_attempts=5
            )

            if not step5_result["success"]:
                error_msg = step5_result.get("error", "Unknown error")
                raise Exception(f"Step 5 FAILED - {error_msg}")

            if phase_callback:
                await phase_callback(
                    "submit_clicked",
                    {"source": "post_comment_verified", "step": "comment_submit_clicked"},
                )
            await record_current_event("submit", {"success": True}, phase="submit", source="post_comment_verified")

            # Wait for comment to post (5s for long comments to render)
            await asyncio.sleep(5)

            # Dump elements after send to see what's on the page
            post_submit_elements = await dump_interactive_elements(page, "AFTER SEND CLICK - checking for comment")
            submission_evidence = await _collect_comment_submission_evidence(
                page,
                comment,
                interactive_elements=post_submit_elements,
            )

            # Verify comment was posted
            if phase_callback:
                await phase_callback("verifying", {"source": "post_comment_verified"})
            if submission_evidence.get("local_comment_text_seen") or submission_evidence.get("interactive_text_seen"):
                result["steps_completed"].append("comment_posted")
                result["success"] = True
                result["verified"] = True
                result["method"] = "hybrid_verified"
                result["verification_confidence"] = 1.0
                result["submission_evidence"] = submission_evidence
                await record_current_event(
                    "verification",
                    {"step": "comment_posted", "success": True, "method": "hybrid_verified", "submission_evidence": submission_evidence},
                    phase="verify",
                    source="post_comment_verified",
                )
                logger.info("✓ Step 5: Comment posted with local DOM confirmation before Gemini fallback")
            else:
                verify_screenshot = await save_debug_screenshot(page, "step5_verify")
                verification = await vision.verify_state(verify_screenshot, "comment_posted", expected_text=comment[-100:])

                if not verification.success:
                    if verification.status == "pending":
                        logger.info("Comment appears pending, waiting 7 more seconds...")
                        await asyncio.sleep(7)
                        post_submit_elements = await dump_interactive_elements(page, "STEP 5 PENDING RETRY")
                        submission_evidence = await _collect_comment_submission_evidence(
                            page,
                            comment,
                            interactive_elements=post_submit_elements,
                        )
                        if submission_evidence.get("local_comment_text_seen") or submission_evidence.get("interactive_text_seen"):
                            verification.success = True
                            verification.confidence = max(float(getattr(verification, "confidence", 0.0) or 0.0), 0.95)
                        else:
                            verify_screenshot = await save_debug_screenshot(page, "step5_pending")
                            verification = await vision.verify_state(verify_screenshot, "comment_posted", expected_text=comment[-100:])
                    else:
                        logger.info(f"Comment not visible, waiting 3 more seconds... ({verification.message})")
                        await asyncio.sleep(3)
                        post_submit_elements = await dump_interactive_elements(page, "STEP 5 FINAL RETRY")
                        submission_evidence = await _collect_comment_submission_evidence(
                            page,
                            comment,
                            interactive_elements=post_submit_elements,
                        )
                        if submission_evidence.get("local_comment_text_seen") or submission_evidence.get("interactive_text_seen"):
                            verification.success = True
                            verification.confidence = max(float(getattr(verification, "confidence", 0.0) or 0.0), 0.9)
                        else:
                            verify_screenshot = await save_debug_screenshot(page, "step5_retry")
                            verification = await vision.verify_state(verify_screenshot, "comment_posted", expected_text=comment[-100:])

                    if not verification.success and _has_strong_comment_submission_evidence(submission_evidence):
                        result["method"] = "verification_inconclusive"
                        result["error"] = f"Step 5 INCONCLUSIVE - Comment submission evidence is strong but visual confirmation failed: {verification.message}"
                        result["submission_evidence"] = submission_evidence
                        await record_current_event(
                            "verification",
                            {
                                "step": "comment_posted",
                                "success": False,
                                "status": "inconclusive",
                                "message": verification.message,
                                "submission_evidence": submission_evidence,
                            },
                            phase="verify",
                            source="post_comment_verified",
                        )
                        logger.warning(result["error"])
                        verdict = build_comment_verdict(result)
                        result["final_verdict"] = verdict.final_verdict
                        result["evidence_summary"] = verdict.summary
                        await recorder.finalize(verdict, metadata={"result_error": result.get("error")})
                        reset_current_forensic_recorder(recorder_token)
                        return result

                    if not verification.success:
                        raise Exception(f"Step 5 FAILED - Comment not posted: {verification.message}")

                result["steps_completed"].append("comment_posted")
                result["success"] = True
                result["verified"] = True
                result["verification_confidence"] = verification.confidence
                result["submission_evidence"] = submission_evidence
                await record_current_event(
                    "verification",
                    {
                        "step": "comment_posted",
                        "success": True,
                        "confidence": verification.confidence,
                        "submission_evidence": submission_evidence,
                    },
                    phase="verify",
                    source="post_comment_verified",
                )
                logger.info(f"✓ Step 5: Comment posted and verified! (confidence: {verification.confidence:.0%})")
            logger.info("=" * 50)
            logger.info("SUCCESS: All 5 steps completed with verification!")
            logger.info("=" * 50)

        except Exception as e:
            result["error"] = _brief(e)
            logger.error(f"FAILED: {_brief(e)}")
            logger.error(f"Steps completed before failure: {result['steps_completed']}")
            if 'page' in locals():
                auth_state = await classify_facebook_auth_state(page)
                result["session_health_status"] = auth_state.get("health_status")
                result["session_health_reason"] = auth_state.get("health_reason")
                error_screenshot = await save_debug_screenshot(page, "error_state")

                # Check for restriction/throttling when there's a failure
                # Layer 1: Skip vision check if error is clearly infrastructure (proxy/timeout/network)
                # Blank/error screenshots make Gemini hallucinate "RESTRICTED" → false positive
                error_str = str(e).lower()
                is_infra_error = any(kw in error_str for kw in [
                    "timeout", "proxy", "connection", "network", "net::err", "tunnel",
                    "err_tunnel", "err_connection", "err_proxy", "econnrefused", "econnreset"
                ])

                if is_infra_error:
                    result["session_health_status"] = AUTH_HEALTH_INFRA_BLOCKED
                    result["session_health_reason"] = result.get("error")
                    result["throttled"] = False
                    logger.info(f"Skipping restriction check — infrastructure error: {str(e)[:100]}")
                elif auth_state.get("health_status") in {
                    AUTH_HEALTH_CHECKPOINT,
                    AUTH_HEALTH_HUMAN_VERIFICATION,
                    AUTH_HEALTH_VIDEO_SELFIE,
                }:
                    result["throttled"] = True
                    result["throttle_reason"] = auth_state.get("health_reason")
                    logger.warning(
                        f"AUTH CHALLENGE DETECTED: {auth_state.get('health_status')} "
                        f"({auth_state.get('health_reason')})"
                    )
                elif auth_state.get("health_status") == AUTH_HEALTH_LOGGED_OUT:
                    result["throttled"] = False
                    logger.warning("Logged-out facebook shell detected during posting flow")
                elif vision and error_screenshot:
                    try:
                        restriction_check = await vision.check_restriction(error_screenshot)
                        await record_current_event(
                            "restriction_check",
                            restriction_check,
                            phase="verify",
                            source="post_comment_verified",
                        )
                        if restriction_check.get("restricted"):
                            result["throttled"] = True
                            result["throttle_reason"] = restriction_check.get("reason", "Unknown restriction")
                            logger.warning(f"RESTRICTION DETECTED: {result['throttle_reason']}")
                        else:
                            result["throttled"] = False
                    except Exception as check_err:
                        logger.error(f"Failed to check for restriction: {check_err}")
        finally:
            await browser.close()

    # Cleanup old screenshots after each run (keep last 100)
    cleanup_old_screenshots(max_keep=100)

    verdict = build_comment_verdict(result)
    result["final_verdict"] = verdict.final_verdict
    result["evidence_summary"] = verdict.summary
    await recorder.finalize(verdict, metadata={"result_error": result.get("error")})
    reset_current_forensic_recorder(recorder_token)

    return result


async def reply_to_comment_verified(
    session: FacebookSession,
    url: str,
    target_comment_url: str,
    target_comment_id: str,
    reply_text: str,
    image_path: str,
    proxy: Optional[str] = None,
    enable_warmup: bool = False,
    phase_callback: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None,
) -> Dict[str, Any]:
    """
    Reply to a specific target comment and attach an image.

    Critical rules:
    - Requires parseable target comment id
    - Reply text is lowercased before typing
    - If image attach fails, fail job (no text-only fallback)
    """
    normalized_reply = str(reply_text or "").strip().lower()
    parsed_from_url = parse_comment_id_from_url(target_comment_url)

    submission_evidence: Dict[str, Any] = {
        "text_typed_before_attach": False,
        "text_after_attach_verified": False,
        "image_attached": False,
        "submit_clicked": False,
        "posting_indicator_seen": False,
        "local_comment_text_seen": False,
        "local_comment_image_seen": False,
    }

    result: Dict[str, Any] = {
        "success": False,
        "url": url,
        "target_comment_url": target_comment_url,
        "target_comment_id": target_comment_id,
        "reply_text": normalized_reply,
        "image_path": image_path,
        "error": None,
        "steps_completed": [],
        "method": "reply_verified",
        "verified": False,
        "submission_evidence": submission_evidence,
    }

    if not parsed_from_url or str(parsed_from_url) != str(target_comment_id):
        result["error"] = "target_comment_url does not contain matching parseable comment_id"
        return result

    if not normalized_reply:
        result["error"] = "reply_text is empty"
        return result

    if not image_path or not Path(image_path).exists():
        result["error"] = f"Image file not found: {image_path}"
        return result

    vision = get_vision_client() if VISION_AVAILABLE else None
    if not vision:
        result["error"] = "Vision client not available - required for verified mode"
        return result

    async with async_playwright() as p:
        user_agent = session.get_user_agent() or DEFAULT_USER_AGENT
        viewport = session.get_viewport() or MOBILE_VIEWPORT
        active_proxy = proxy
        if not active_proxy:
            raise Exception("No proxy available — cannot launch browser without proxy")

        device_fingerprint = session.get_device_fingerprint()
        context_options = {
            "user_agent": user_agent,
            "viewport": viewport,
            "ignore_https_errors": True,
            "device_scale_factor": 1,
            "timezone_id": device_fingerprint["timezone"],
            "locale": device_fingerprint["locale"],
            "proxy": _build_playwright_proxy(active_proxy),
        }
        logger.info(f"Using proxy: {context_options['proxy'].get('server')}")

        browser = await p.chromium.launch(headless=True, args=["--disable-notifications", "--disable-geolocation"])
        context = await browser.new_context(**context_options)
        await Stealth().apply_stealth_async(context)

        try:
            page = await context.new_page()
            if not await apply_session_to_context(context, session):
                raise Exception("Failed to apply cookies")

            if enable_warmup:
                from warmup_bot import perform_warmup
                warmup_result = await perform_warmup(page)
                result["warmup"] = {
                    "success": warmup_result.success,
                    "scrolls": warmup_result.scroll_count,
                    "likes": warmup_result.likes_count,
                    "duration": warmup_result.duration_seconds,
                    "error": warmup_result.error,
                }

            # Always navigate to target comment permalink for strict context.
            await page.goto(target_comment_url, wait_until="domcontentloaded", timeout=45000)
            result["landing_url"] = page.url
            await save_debug_screenshot(page, "reply_target_initial")
            if not await wait_for_post_visible(page, vision, max_attempts=6):
                raise Exception("Target page not visible")
            result["steps_completed"].append("target_page_visible")

            target_context_found = False
            navigation_candidates = _build_target_navigation_candidates(
                target_comment_url=target_comment_url,
                current_url=page.url,
                target_comment_id=target_comment_id,
            )

            for candidate in navigation_candidates:
                if candidate != page.url:
                    try:
                        await page.goto(candidate, wait_until="domcontentloaded", timeout=45000)
                        if not await wait_for_post_visible(page, vision, max_attempts=3):
                            continue
                    except Exception:
                        continue

                if await _is_target_comment_context_present(page, target_comment_id):
                    target_context_found = True
                    break

            if target_context_found:
                result["steps_completed"].append("target_comment_context_detected")
            else:
                logger.warning(
                    "Target comment_id context not detectable; continuing with visual post/reply flow"
                )
                result["steps_completed"].append("target_comment_context_not_detected")

            # Open comments area (if needed) so reply controls are visible.
            comments_open_result = await click_with_healing(
                page=page,
                vision=vision,
                selectors=fb_selectors.COMMENT["comment_button"],
                description="Comment button (reply flow)",
                max_attempts=3,
            )
            if comments_open_result.get("success"):
                await asyncio.sleep(1.0)
                result["steps_completed"].append("comments_opened_for_reply")

            # Open reply composer for the target comment (or visually-nearest reply control).
            if not await _click_reply_button_for_target(page, target_comment_id):
                # One more attempt after opening comments again.
                await click_with_healing(
                    page=page,
                    vision=vision,
                    selectors=fb_selectors.COMMENT["comment_button"],
                    description="Comment button (reply flow)",
                    max_attempts=3,
                )
                await asyncio.sleep(1.0)
            if not await _click_reply_button_for_target(page, target_comment_id):
                raise Exception("Could not open reply composer for target comment")
            await asyncio.sleep(0.9)
            result["steps_completed"].append("reply_clicked")

            # Focus reply input.
            focused = await smart_focus(page, fb_selectors.REPLY["reply_input"], "Reply input")
            if not focused:
                # Fallback to semantic comment input finder.
                focused = await find_comment_input(page)
            if not focused:
                await dump_interactive_elements(page, "REPLY INPUT FOCUS FAILED - selector discovery dump")
                logger.warning("Could not explicitly focus reply input; attempting direct typing with visual verification")
            await asyncio.sleep(0.5)
            if focused:
                result["steps_completed"].append("reply_input_focused")

            # Type lowercase reply text and verify typed state (source of truth).
            await page.keyboard.type(normalized_reply, delay=40)
            await asyncio.sleep(0.8)
            typed_shot = await save_debug_screenshot(page, "reply_typed")
            typed_verification = await vision.verify_state(
                typed_shot,
                "text_typed",
                expected_text=normalized_reply[-100:],
            )
            if not typed_verification.success:
                raise Exception(f"Reply text not visible after typing: {typed_verification.message}")
            submission_evidence["text_typed_before_attach"] = True
            if not focused:
                result["steps_completed"].append("reply_input_inferred_from_typed_text")
            result["steps_completed"].append("reply_text_typed")

            # Attach image (strict requirement: fail if attach fails).
            attached = await _attach_image_to_reply(page, image_path)
            if not attached:
                raise Exception("Image attach failed (strict mode, no text-only fallback)")
            submission_evidence["image_attached"] = True
            result["steps_completed"].append("image_attached")

            # Some FB upload flows reset composer text; enforce text presence again before submit.
            post_attach_shot = await save_debug_screenshot(page, "reply_after_attach")
            post_attach_typed = await vision.verify_state(
                post_attach_shot,
                "text_typed",
                expected_text=normalized_reply[-100:],
            )
            if not post_attach_typed.success:
                focused_after_attach = await smart_focus(page, fb_selectors.REPLY["reply_input"], "Reply input after attach")
                if not focused_after_attach:
                    focused_after_attach = await find_comment_input(page)
                if not focused_after_attach:
                    raise Exception("Could not focus reply input after image attach")
                await page.keyboard.type(normalized_reply, delay=40)
                await asyncio.sleep(0.8)

                retyped_shot = await save_debug_screenshot(page, "reply_retyped_after_attach")
                retyped_verification = await vision.verify_state(
                    retyped_shot,
                    "text_typed",
                    expected_text=normalized_reply[-100:],
                )
                if not retyped_verification.success:
                    raise Exception(
                        f"Reply text not visible after retyping post-attach: {retyped_verification.message}"
                    )
                submission_evidence["text_after_attach_verified"] = True
                result["steps_completed"].append("reply_text_retyped_after_attach")
            else:
                submission_evidence["text_after_attach_verified"] = True
                result["steps_completed"].append("reply_text_preserved_after_attach")

            # Submit reply.
            submitted = False
            if await smart_click(page, fb_selectors.REPLY["reply_submit"], "Reply submit"):
                submitted = True
            elif await smart_click(page, fb_selectors.COMMENT["comment_submit"], "Send button"):
                submitted = True
            else:
                # Composer UIs often submit on Enter even when send icon has no selector.
                try:
                    await page.keyboard.press("Enter")
                    submitted = True
                except Exception:
                    submitted = False
            if not submitted:
                raise Exception("Could not click reply submit button")
            submission_evidence["submit_clicked"] = True
            if phase_callback:
                await phase_callback(
                    "submit_clicked",
                    {"source": "reply_to_comment_verified", "step": "reply_submit_clicked"},
                )

            # Wait for FB "Posting..." transient state to settle before evidence screenshot.
            posting_seen = False
            for _ in range(25):
                posting_state = await page.evaluate(
                    """() => {
                        const text = (document.body && document.body.innerText ? document.body.innerText : '').toLowerCase();
                        return text.includes('posting...');
                    }"""
                )
                if posting_state:
                    posting_seen = True
                if not posting_state:
                    break
                await asyncio.sleep(1.0)
            submission_evidence["posting_indicator_seen"] = posting_seen
            await save_debug_screenshot(page, "reply_post_submit")

            local_submission_state = await page.evaluate(
                """(snippet) => {
                    const norm = (s) => (s || '').toLowerCase().replace(/\\s+/g, ' ').trim();
                    const snippetNorm = norm(snippet);
                    const bodyText = norm(document.body ? document.body.innerText : '');
                    let localCommentTextSeen = false;
                    let localCommentImageSeen = false;

                    const blocks = Array.from(document.querySelectorAll('div, li, article'));
                    for (const block of blocks) {
                        const blockText = norm(block.innerText || block.textContent);
                        if (!snippetNorm || !blockText.includes(snippetNorm)) continue;
                        localCommentTextSeen = true;

                        const imgs = Array.from(block.querySelectorAll('img'));
                        if (imgs.some((img) => (img.naturalWidth || 0) >= 60 && (img.naturalHeight || 0) >= 60)) {
                            localCommentImageSeen = true;
                        }
                        break;
                    }

                    return {
                        localCommentTextSeen,
                        localCommentImageSeen,
                        postingIndicatorSeen: bodyText.includes('posting...')
                    };
                }""",
                normalized_reply[-120:],
            )
            submission_evidence["local_comment_text_seen"] = bool(local_submission_state.get("localCommentTextSeen"))
            submission_evidence["local_comment_image_seen"] = bool(local_submission_state.get("localCommentImageSeen"))
            submission_evidence["posting_indicator_seen"] = bool(
                submission_evidence["posting_indicator_seen"] or local_submission_state.get("postingIndicatorSeen")
            )

            result["steps_completed"].append("reply_submitted")

            # Verify through target permalink candidates (avoid feed-only reload verification).
            posted = None
            verify_error = "verification did not run"
            if phase_callback:
                await phase_callback("verifying", {"source": "reply_to_comment_verified"})
            verify_candidates = _build_target_navigation_candidates(
                target_comment_url=target_comment_url,
                current_url=page.url,
                target_comment_id=target_comment_id,
            )

            for idx, candidate in enumerate(verify_candidates):
                try:
                    if candidate != page.url:
                        await page.goto(candidate, wait_until="domcontentloaded", timeout=45000)
                    if not await wait_for_post_visible(page, vision, max_attempts=3):
                        continue
                    await click_with_healing(
                        page=page,
                        vision=vision,
                        selectors=fb_selectors.COMMENT["comment_button"],
                        description=f"Comment button (verify pass {idx + 1})",
                        max_attempts=2,
                    )
                    await asyncio.sleep(1.0)
                    await _click_reply_button_for_target(page, target_comment_id)
                    await asyncio.sleep(1.0)

                    verify_shot = await save_debug_screenshot(page, f"reply_verify_{idx + 1}")
                    posted = await vision.verify_state(
                        verify_shot,
                        "comment_posted",
                        expected_text=normalized_reply[-100:],
                    )
                    verify_error = posted.message
                    if posted.success:
                        break
                except Exception as verify_exc:
                    verify_error = str(verify_exc)
                    continue

            hard_verified = bool(posted and posted.success)
            strong_submission_evidence = _has_strong_reply_submission_evidence(submission_evidence)

            if hard_verified:
                result["steps_completed"].append("reply_verified")
                result["success"] = True
                result["verified"] = True
                result["verification_confidence"] = posted.confidence
            elif strong_submission_evidence:
                # Long-term robust behavior: avoid false negatives when submit evidence is strong.
                result["success"] = True
                result["verified"] = False
                result["verification_warning"] = verify_error
                result["method"] = "reply_submission_evidence"
                result["steps_completed"].append("reply_submitted_evidence_accepted")
                logger.warning(
                    f"Reply verification inconclusive but submission evidence is strong; accepting success. verify_error={verify_error}"
                )
            else:
                raise Exception(f"Reply post verification failed: {verify_error}")

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"reply_to_comment_verified failed: {e}")
            if "page" in locals():
                await save_debug_screenshot(page, "reply_error")
        finally:
            await browser.close()

    cleanup_old_screenshots(max_keep=100)
    return result


async def reconcile_comment_submission(
    session: FacebookSession,
    url: str,
    comment_text: str,
    proxy: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Best-effort read-only reconciliation for an inflight submission.

    Returns:
        {
          "found": True|False|None,   # None means inconclusive
          "confidence": float,
          "reason": str,
        }
    """
    if not comment_text:
        return {"found": None, "confidence": 0.0, "reason": "empty comment text"}

    vision = get_vision_client() if VISION_AVAILABLE else None
    if not vision:
        return {"found": None, "confidence": 0.0, "reason": "vision unavailable"}

    user_agent = session.get_user_agent() or DEFAULT_USER_AGENT
    viewport = session.get_viewport() or MOBILE_VIEWPORT
    active_proxy = proxy
    if not active_proxy:
        return {"found": None, "confidence": 0.0, "reason": "proxy unavailable"}

    try:
        async with async_playwright() as p:
            device_fingerprint = session.get_device_fingerprint()
            context_options = {
                "user_agent": user_agent,
                "viewport": viewport,
                "ignore_https_errors": True,
                "device_scale_factor": 1,
                "timezone_id": device_fingerprint["timezone"],
                "locale": device_fingerprint["locale"],
                "proxy": _build_playwright_proxy(active_proxy),
            }
            browser = await p.chromium.launch(headless=True, args=["--disable-notifications", "--disable-geolocation"])
            context = await browser.new_context(**context_options)
            await Stealth().apply_stealth_async(context)
            try:
                page = await context.new_page()
                if not await apply_session_to_context(context, session):
                    return {"found": None, "confidence": 0.0, "reason": "session cookies unavailable"}

                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                if not await wait_for_post_visible(page, vision, max_attempts=3):
                    return {"found": None, "confidence": 0.0, "reason": "post not visible during reconciliation"}

                # Open comments section if possible to increase detection reliability.
                await click_with_healing(
                    page=page,
                    vision=vision,
                    selectors=fb_selectors.COMMENT["comment_button"],
                    description="Comment button (reconciliation)",
                    max_attempts=2,
                )
                await asyncio.sleep(1.0)

                elements = await dump_interactive_elements(page, "RECONCILIATION CHECK")
                submission_evidence = await _collect_comment_submission_evidence(
                    page,
                    comment_text,
                    interactive_elements=elements,
                )
                if submission_evidence.get("local_comment_text_seen") or submission_evidence.get("interactive_text_seen"):
                    return {
                        "found": True,
                        "confidence": 1.0,
                        "reason": "comment text verified from local DOM evidence",
                    }

                shot = await save_debug_screenshot(page, "reconcile_verify")
                verification = await vision.verify_state(shot, "comment_posted", expected_text=comment_text[-100:])
                if verification.success:
                    return {
                        "found": True,
                        "confidence": float(verification.confidence or 0.0),
                        "reason": "comment text verified on page",
                    }

                status = str(getattr(verification, "status", "") or "").lower()
                if status == "not_verified":
                    return {
                        "found": False,
                        "confidence": float(verification.confidence or 0.0),
                        "reason": verification.message or "comment text not visible",
                    }

                if _has_strong_comment_submission_evidence(submission_evidence):
                    return {
                        "found": None,
                        "confidence": max(float(verification.confidence or 0.0), 0.5),
                        "reason": verification.message or "verification inconclusive with strong submit evidence",
                    }

                return {
                    "found": None,
                    "confidence": float(verification.confidence or 0.0),
                    "reason": verification.message or "verification inconclusive",
                }
            finally:
                await browser.close()
    except Exception as exc:
        logger.warning(f"Reconciliation check failed: {exc}")
        return {"found": None, "confidence": 0.0, "reason": str(exc)}


# Re-export other functions needed by main.py
async def test_session(session: FacebookSession, proxy: Optional[str] = None) -> Dict[str, Any]:
    result = {
        "valid": False,
        "user_id": None,
        "error": None,
        "health_status": AUTH_HEALTH_NEEDS_ATTENTION,
        "health_reason": None,
    }
    
    if not session.load():
        result["error"] = "Session file not found"
        return result

    async with async_playwright() as p:
        # System proxy only — no session proxy fallback
        active_proxy = proxy
        if not active_proxy:
            raise Exception("No proxy available — cannot launch browser without proxy")

        user_agent = session.get_user_agent() or DEFAULT_USER_AGENT
        viewport = session.get_viewport() or MOBILE_VIEWPORT

        # Get device fingerprint for this session (timezone, locale)
        device_fingerprint = session.get_device_fingerprint()

        context_options: Dict[str, Any] = {
            "user_agent": user_agent,
            "viewport": viewport,
            "ignore_https_errors": True,
            "device_scale_factor": 1,  # Force 1:1 pixel mapping
            "timezone_id": device_fingerprint["timezone"],
            "locale": device_fingerprint["locale"],
        }
        if active_proxy:
            context_options["proxy"] = _build_playwright_proxy(active_proxy)

        browser = await p.chromium.launch(headless=True, args=["--disable-notifications", "--disable-geolocation"])
        context = await browser.new_context(**context_options)

        # MANDATORY: Apply stealth mode for anti-detection
        await Stealth().apply_stealth_async(context)

        try:
            if not await apply_session_to_context(context, session):
                raise Exception("Failed to apply cookies")

            page = await context.new_page()
            await page.goto("https://m.facebook.com/me/", wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(1)

            auth_state = await classify_facebook_auth_state(page)
            result["health_status"] = auth_state.get("health_status")
            result["health_reason"] = auth_state.get("health_reason")
            current_url = page.url.lower()
            if auth_state.get("health_status") == AUTH_HEALTH_HEALTHY and "/login" not in current_url and "checkpoint" not in current_url:
                result["valid"] = True
                result["user_id"] = session.get_user_id()
        except Exception as e:
            result["error"] = str(e)
            if not result.get("health_reason"):
                result["health_status"] = AUTH_HEALTH_INFRA_BLOCKED if any(
                    token in str(e).lower()
                    for token in ["timeout", "proxy", "connection", "network", "net::err", "tunnel"]
                ) else AUTH_HEALTH_NEEDS_ATTENTION
                result["health_reason"] = _brief(e)
        finally:
            await browser.close()
            
    return result
