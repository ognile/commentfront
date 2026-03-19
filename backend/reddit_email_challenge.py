"""
Reddit email verification helpers backed by Outlook Web.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from browser_factory import build_playwright_proxy

logger = logging.getLogger("RedditEmailChallenge")


OUTLOOK_LOGIN = {
    "email_input": [
        'input[type="email"]',
        'input[name="loginfmt"]',
    ],
    "email_submit": [
        'input[type="submit"]',
        'button[type="submit"]',
        '#idSIButton9',
    ],
    "password_input": [
        'input[type="password"]',
        'input[name="passwd"]',
    ],
    "password_submit": [
        'input[type="submit"]',
        'button[type="submit"]',
        '#idSIButton9',
    ],
    "decline_stay_signed_in": [
        '#declineButton',
        '#idBtn_Back',
        'button:has-text("No")',
        'button:has-text("Not now")',
    ],
}

OUTLOOK_MAIL = {
    "search_input": [
        'input[type="search"]',
        'input[aria-label*="Search" i]',
        'input[placeholder*="Search" i]',
    ],
    "message_row": [
        '[role="option"]:has-text("Reddit")',
        '[role="row"]:has-text("Reddit")',
        'button:has-text("Reddit")',
        'div:has-text("Reddit")',
    ],
}

REDDIT_CHALLENGE_CODE_INPUT = [
    'input[name="code"]',
    'input[name="otp"]',
    'input[autocomplete="one-time-code"]',
    'input[inputmode="numeric"]',
]

REDDIT_CHALLENGE_SUBMIT = [
    'button[type="submit"]',
    'button:has-text("Continue")',
    'button:has-text("Verify")',
    'button:has-text("Submit")',
]

REDDIT_VERIFICATION_LINK_HINTS = (
    "reddit.com/verification",
    "reddit.com/verify",
    "redditmail.com",
    "click.redditmail.com",
)

REDDIT_CHALLENGE_HINTS = (
    "verify your email",
    "verification email",
    "check your email",
    "we sent an email",
    "enter the code",
    "verification code",
    "confirm your email",
)

CODE_PATTERN = re.compile(r"\b(\d{6})\b")


async def _first_visible_locator(page, selectors):
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count() > 0 and await locator.is_visible():
                return locator
        except Exception:
            continue
    return None


async def _click_first(page, selectors, *, timeout_ms: int = 5000) -> bool:
    locator = await _first_visible_locator(page, selectors)
    if not locator:
        return False
    await locator.click(timeout=timeout_ms)
    return True


async def _fill_first(page, selectors, value: str) -> bool:
    locator = await _first_visible_locator(page, selectors)
    if not locator:
        return False
    await locator.click()
    await locator.fill("")
    await locator.fill(value)
    return True


async def _type_into_first(page, selectors, value: str) -> bool:
    locator = await _first_visible_locator(page, selectors)
    if not locator:
        return False
    await locator.click()
    await locator.fill("")
    await locator.type(value, delay=45)
    return True


async def _body_text(page) -> str:
    try:
        return await page.locator("body").inner_text()
    except Exception:
        return ""


def classify_reddit_email_challenge(*, url: Optional[str], body_text: Optional[str]) -> Optional[Dict[str, Any]]:
    current_url = str(url or "").strip()
    text = str(body_text or "")
    lowered = f"{current_url}\n{text}".lower()

    if not any(hint in lowered for hint in REDDIT_CHALLENGE_HINTS):
        return None

    code_match = CODE_PATTERN.search(text)
    mode = "code" if ("code" in lowered or code_match) else "link"
    return {
        "kind": "reddit_email_verification",
        "mode": mode,
        "url": current_url,
        "code_hint": code_match.group(1) if code_match else None,
    }


async def detect_reddit_email_challenge(page) -> Optional[Dict[str, Any]]:
    return classify_reddit_email_challenge(url=getattr(page, "url", ""), body_text=await _body_text(page))


async def _login_outlook(page, *, email: str, password: str) -> None:
    await page.goto("https://login.live.com/", wait_until="domcontentloaded", timeout=45000)
    await page.wait_for_timeout(2000)

    email_ready = await _fill_first(page, OUTLOOK_LOGIN["email_input"], email)
    if not email_ready:
        raise RuntimeError("email_login_failed: outlook email input not found")
    if not await _click_first(page, OUTLOOK_LOGIN["email_submit"]):
        raise RuntimeError("email_login_failed: outlook email submit not found")

    await page.wait_for_timeout(2500)
    password_ready = await _type_into_first(page, OUTLOOK_LOGIN["password_input"], password)
    if not password_ready:
        raise RuntimeError("email_login_failed: outlook password input not found")
    if not await _click_first(page, OUTLOOK_LOGIN["password_submit"]):
        raise RuntimeError("email_login_failed: outlook password submit not found")

    await page.wait_for_timeout(3500)
    await _click_first(page, OUTLOOK_LOGIN["decline_stay_signed_in"])


async def _search_reddit_mail(page) -> None:
    locator = await _first_visible_locator(page, OUTLOOK_MAIL["search_input"])
    if locator:
        await locator.click()
        await locator.fill("from:reddit")
        await locator.press("Enter")
        await page.wait_for_timeout(2500)


async def _open_reddit_message(page) -> bool:
    return await _click_first(page, OUTLOOK_MAIL["message_row"], timeout_ms=4000)


async def _extract_reddit_verification_artifacts(page) -> Dict[str, Any]:
    payload = await page.evaluate(
        """() => {
            const links = Array.from(document.querySelectorAll('a[href]')).map((anchor) => ({
                href: anchor.href,
                text: (anchor.innerText || anchor.textContent || '').trim(),
            }));
            const text = document.body ? (document.body.innerText || '') : '';
            return { links, text };
        }"""
    )

    text = str(payload.get("text") or "")
    links = list(payload.get("links") or [])
    verification_link = None
    for link in links:
        href = str((link or {}).get("href") or "").strip()
        lowered = href.lower()
        if href and any(hint in lowered for hint in REDDIT_VERIFICATION_LINK_HINTS):
            verification_link = href
            break

    code_match = CODE_PATTERN.search(text)
    return {
        "link": verification_link,
        "code": code_match.group(1) if code_match else None,
        "text_preview": text[:1000],
    }


async def fetch_reddit_verification_from_outlook(
    browser,
    *,
    email: str,
    email_password: str,
    proxy_url: Optional[str],
    fingerprint: Dict[str, str],
    poll_timeout_ms: int = 120000,
) -> Dict[str, Any]:
    context_options: Dict[str, Any] = {
        "viewport": {"width": 1440, "height": 1024},
        "ignore_https_errors": True,
        "locale": str(fingerprint.get("locale") or "en-US"),
        "timezone_id": str(fingerprint.get("timezone") or "America/New_York"),
    }
    proxy = build_playwright_proxy(proxy_url or "")
    if proxy:
        context_options["proxy"] = proxy

    context = await browser.new_context(**context_options)
    page = await context.new_page()
    try:
        await _login_outlook(page, email=email, password=email_password)
        await page.goto("https://outlook.live.com/mail/0/inbox", wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(5000)

        deadline_ms = max(10000, poll_timeout_ms)
        elapsed = 0
        while elapsed <= deadline_ms:
            await _search_reddit_mail(page)
            opened = await _open_reddit_message(page)
            if opened:
                await page.wait_for_timeout(2500)
                artifacts = await _extract_reddit_verification_artifacts(page)
                if artifacts.get("link") or artifacts.get("code"):
                    return {"success": True, **artifacts}

            await page.goto("https://outlook.live.com/mail/0/inbox", wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(8000)
            elapsed += 8000

        raise RuntimeError("mailbox_timeout: reddit verification email not found before timeout")
    finally:
        await context.close()


async def resolve_reddit_email_challenge(
    page,
    browser,
    *,
    credential: Dict[str, Any],
    proxy_url: Optional[str],
    fingerprint: Dict[str, str],
) -> Dict[str, Any]:
    challenge = await detect_reddit_email_challenge(page)
    if not challenge:
        return {"challenge_present": False, "handled": False}

    email = str(credential.get("email") or "").strip()
    email_password = str(credential.get("email_password") or "").strip()
    if not email or not email_password:
        raise RuntimeError("email_login_failed: reddit email challenge requires stored email and email_password")

    mailbox_result = await fetch_reddit_verification_from_outlook(
        browser,
        email=email,
        email_password=email_password,
        proxy_url=proxy_url,
        fingerprint=fingerprint,
    )

    verification_link = str(mailbox_result.get("link") or "").strip()
    verification_code = str(mailbox_result.get("code") or "").strip()

    if challenge.get("mode") == "code":
        if not verification_code:
            raise RuntimeError("verification_mail_missing: reddit verification code not found in mailbox")
        if not await _fill_first(page, REDDIT_CHALLENGE_CODE_INPUT, verification_code):
            raise RuntimeError("challenge_submit_failed: reddit verification code input not found")
        if not await _click_first(page, REDDIT_CHALLENGE_SUBMIT):
            raise RuntimeError("challenge_submit_failed: reddit verification submit button not found")
        await page.wait_for_timeout(4000)
    else:
        if not verification_link:
            raise RuntimeError("verification_mail_missing: reddit verification link not found in mailbox")
        logger.info("navigating reddit page to verification link from outlook mailbox")
        await page.goto(verification_link, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(4000)

    return {
        "challenge_present": True,
        "handled": True,
        "mode": challenge.get("mode"),
        "mailbox_link_used": bool(verification_link),
        "mailbox_code_used": bool(verification_code and challenge.get("mode") == "code"),
    }
