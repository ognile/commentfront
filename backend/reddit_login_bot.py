"""
Reddit login/session bootstrap for mobile-web execution.
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Literal, Optional

from playwright.async_api import Page, async_playwright

from browser_factory import create_browser_context
from config import DEFAULT_USER_AGENT, MOBILE_VIEWPORT, REDDIT_MOBILE_USER_AGENT
from comment_bot import dump_interactive_elements, save_debug_screenshot
from credentials import CredentialManager
from fb_session import FacebookSession, apply_session_to_context, list_saved_sessions
from proxy_manager import get_system_proxy
from reddit_login_audit import RedditLoginAudit, compare_reddit_audits, load_reddit_audit
from reddit_selectors import COOKIE_BANNER, LOGIN
from reddit_session import RedditSession, verify_reddit_session_logged_in

logger = logging.getLogger("RedditLoginBot")

BroadcastFn = Optional[Callable[[str, dict], Awaitable[None]]]
AttemptMode = Literal["reference_facebook_identity", "standalone_reddit_identity"]


async def _broadcast(callback: BroadcastFn, update_type: str, payload: dict):
    if callback:
        await callback(update_type, payload)


def _credential_label(credential: dict) -> str:
    return str(credential.get("username") or credential.get("uid") or credential.get("credential_id") or "reddit")


def _choose_reference_facebook_session(preferred_session_id: Optional[str] = None) -> str:
    if preferred_session_id:
        session = FacebookSession(preferred_session_id)
        if not session.load() or not session.has_valid_cookies():
            raise RuntimeError(f"Reference Facebook session '{preferred_session_id}' is unavailable or invalid")
        return preferred_session_id

    for item in list_saved_sessions():
        if item.get("has_valid_cookies"):
            return str(item.get("profile_name"))

    raise RuntimeError("No valid Facebook sessions available for Reddit reference audit")


async def _capture_checkpoint(
    audit: Optional[RedditLoginAudit],
    page: Page,
    context,
    name: str,
):
    if audit:
        await audit.capture_checkpoint(page, context, name)


def _mask_proxy(proxy_url: Optional[str]) -> Optional[str]:
    if not proxy_url:
        return None
    if "@" not in proxy_url:
        return proxy_url
    creds, host = proxy_url.rsplit("@", 1)
    return f"{creds.split(':', 1)[0]}:***@{host}"


async def _click_first(page: Page, selectors, *, timeout_ms: int = 2500) -> bool:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count() > 0 and await locator.is_visible():
                await locator.click(timeout=timeout_ms)
                return True
        except Exception:
            continue
    return False


async def _dismiss_cookie_banner(page: Page):
    # The Devvit wrapper intercepts normal pointer events; JS click works more reliably.
    try:
        await page.evaluate(
            """() => {
                const buttons = Array.from(document.querySelectorAll('button'));
                const reject = buttons.find((button) => /reject optional cookies/i.test(button.innerText || ''));
                const accept = buttons.find((button) => /accept all/i.test(button.innerText || ''));
                const target = reject || accept;
                if (target) {
                    target.click();
                    return true;
                }
                return false;
            }"""
        )
        await page.wait_for_timeout(600)
    except Exception:
        try:
            await _click_first(page, COOKIE_BANNER["reject"])
        except Exception:
            pass


async def _fill_first(page: Page, selectors, value: str) -> bool:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if await locator.count() > 0 and await locator.is_visible():
                await locator.fill(value)
                return True
        except Exception:
            continue
    return False


async def _log_auth_state(context, profile_name: str):
    cookies = await context.cookies()
    cookie_names = sorted({str(cookie.get("name") or "") for cookie in cookies})
    logger.info(f"[{profile_name}] auth cookie names: {cookie_names}")
    return cookie_names


async def _has_auth_cookies(context) -> bool:
    cookies = await context.cookies()
    names = {str(cookie.get("name") or "") for cookie in cookies}
    return bool({"token_v2", "reddit_session"} & names)


async def _login_inputs_present(page: Page) -> bool:
    try:
        return await page.locator('input[name="username"], input[name="password"]').count() > 0
    except Exception:
        return False


async def _otp_input_present(page: Page) -> bool:
    for selector in LOGIN["otp_input"]:
        try:
            locator = page.locator(selector).first
            if await locator.count() > 0 and await locator.is_visible():
                return True
        except Exception:
            continue
    return False


async def _goto_with_retry(
    page: Page,
    url: str,
    *,
    profile_name: str,
    wait_until: str = "domcontentloaded",
    timeout: int = 45000,
    attempts: int = 3,
):
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            await page.goto(url, wait_until=wait_until, timeout=timeout)
            return
        except Exception as exc:
            last_exc = exc
            logger.warning(f"[{profile_name}] navigation attempt {attempt}/{attempts} failed for {url}: {exc}")
            if attempt == attempts:
                raise
            await page.wait_for_timeout(1500 * attempt)
    raise last_exc


async def _wait_for_authenticated_surface(page: Page, context, *, profile_name: str, timeout_ms: int = 15000) -> bool:
    elapsed = 0
    step_ms = 1000

    while elapsed <= timeout_ms:
        current_url = page.url.lower()
        login_inputs = await _login_inputs_present(page)
        auth_cookies = await _has_auth_cookies(context)

        if auth_cookies and not login_inputs:
            logger.info(
                f"[{profile_name}] authenticated surface detected via cookies on {page.url}"
            )
            return True

        if "/login" not in current_url and not login_inputs:
            logger.info(
                f"[{profile_name}] authenticated surface detected via url transition to {page.url}"
            )
            return True

        await page.wait_for_timeout(step_ms)
        elapsed += step_ms

    logger.warning(f"[{profile_name}] post-login surface did not settle within {timeout_ms}ms")
    return False


async def _goto_in_authenticated_context(context, page: Page, url: str, *, profile_name: str) -> Page:
    try:
        await _goto_with_retry(page, url, profile_name=profile_name)
        return page
    except Exception as exc:
        if "ERR_EMPTY_RESPONSE" not in str(exc):
            raise

        logger.warning(
            f"[{profile_name}] navigation to {url} failed on current page after auth; retrying in fresh page"
        )
        fresh_page = await context.new_page()
        await _goto_with_retry(fresh_page, url, profile_name=profile_name)
        try:
            await page.close()
        except Exception:
            pass
        return fresh_page


async def _wait_for_otp_resolution(page: Page, context, *, profile_name: str, timeout_ms: int = 15000) -> bool:
    elapsed = 0
    step_ms = 1000

    while elapsed <= timeout_ms:
        if await _has_auth_cookies(context):
            logger.info(f"[{profile_name}] otp resolved via auth cookies")
            return True

        if not await _otp_input_present(page):
            logger.info(f"[{profile_name}] otp input cleared")
            return True

        await page.wait_for_timeout(step_ms)
        elapsed += step_ms

    logger.warning(f"[{profile_name}] otp input still visible after {timeout_ms}ms")
    return False


async def _handle_otp(page: Page, credential: dict) -> bool:
    if not await _otp_input_present(page):
        return False

    manager = CredentialManager()
    identifier = credential.get("credential_id") or credential.get("uid")
    otp_data = manager.generate_otp(identifier, platform="reddit")
    if not otp_data.get("valid") or not otp_data.get("code"):
        raise RuntimeError(f"Failed to generate Reddit OTP: {otp_data.get('error')}")

    filled = await _fill_first(page, LOGIN["otp_input"], otp_data["code"])
    if not filled:
        raise RuntimeError("Reddit OTP input detected but not fillable")

    if not await _click_first(page, LOGIN["otp_submit"], timeout_ms=4000):
        await page.keyboard.press("Enter")
    await page.wait_for_timeout(3000)
    return True


async def _run_reddit_login_flow(
    *,
    page: Page,
    context,
    credential: dict,
    profile_name: str,
    proxy_url: Optional[str],
    fingerprint: Dict[str, str],
    audit: Optional[RedditLoginAudit],
    persist_session: bool,
    session: RedditSession,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "success": False,
        "platform": "reddit",
        "credential_id": credential.get("credential_id"),
        "profile_name": profile_name,
        "error": None,
        "needs_attention": False,
    }

    login_identifier = credential.get("username") or credential.get("uid") or credential.get("email")
    password = credential.get("password")

    if not login_identifier or not password:
        raise RuntimeError("Reddit credential missing login identifier or password")

    logger.info(f"[{profile_name}] navigating to reddit.com/login")
    if audit:
        audit.record_event("goto_login_start", target_url="https://www.reddit.com/login")
    await _goto_with_retry(page, "https://www.reddit.com/login", profile_name=profile_name)
    await page.wait_for_timeout(2000)
    await _dismiss_cookie_banner(page)
    logger.info(f"[{profile_name}] reddit login page: {page.url}")
    await save_debug_screenshot(page, f"reddit_login_page_{profile_name}")
    await _capture_checkpoint(audit, page, context, "login_page_loaded")

    page_text = ""
    try:
        page_text = await page.locator("body").inner_text()
    except Exception:
        pass
    logger.info(f"[{profile_name}] page text preview: {page_text[:200]}")

    user_filled = await _fill_first(page, LOGIN["username_input"], str(login_identifier))
    pass_filled = await _fill_first(page, LOGIN["password_input"], str(password))
    logger.info(f"[{profile_name}] login form fill: user={user_filled} pass={pass_filled}")

    if not user_filled or not pass_filled:
        await dump_interactive_elements(page, f"REDDIT LOGIN FORM {profile_name}")
        await save_debug_screenshot(page, f"reddit_login_form_missing_{profile_name}")
        await _capture_checkpoint(audit, page, context, "login_form_missing")
        raise RuntimeError("Reddit login form inputs not found on www.reddit.com/login")

    await save_debug_screenshot(page, f"reddit_form_filled_{profile_name}")
    await _capture_checkpoint(audit, page, context, "credentials_filled")
    submit_clicked = await _click_first(page, LOGIN["submit_button"], timeout_ms=5000)
    if not submit_clicked:
        try:
            await page.locator(LOGIN["password_input"][0]).press("Enter")
        except Exception:
            pass
    logger.info(f"[{profile_name}] login form submitted (button={submit_clicked})")
    if audit:
        audit.record_event("credentials_submitted", button_clicked=submit_clicked)

    await page.wait_for_timeout(5000)
    await save_debug_screenshot(page, f"reddit_after_submit_{profile_name}")
    logger.info(f"[{profile_name}] after submit URL: {page.url}")
    await _capture_checkpoint(audit, page, context, "after_credential_submit")

    if await _otp_input_present(page):
        await _capture_checkpoint(audit, page, context, "otp_prompt")

    otp_handled = await _handle_otp(page, credential)
    if otp_handled:
        logger.info(f"[{profile_name}] OTP submitted on reddit.com")
        if audit:
            audit.record_event("otp_submitted")
        await _wait_for_otp_resolution(page, context, profile_name=profile_name)
        await page.wait_for_timeout(3000)
        await _capture_checkpoint(audit, page, context, "after_otp_submit")

    await _wait_for_authenticated_surface(page, context, profile_name=profile_name)
    await page.wait_for_timeout(1500)
    await _dismiss_cookie_banner(page)
    await save_debug_screenshot(page, f"reddit_after_login_{profile_name}")
    logger.info(f"[{profile_name}] post-login URL: {page.url}")
    await _log_auth_state(context, profile_name)
    await _capture_checkpoint(audit, page, context, "post_login_landing")

    try:
        await _click_first(page, LOGIN["modal_close"], timeout_ms=2000)
    except Exception:
        pass

    profile_url = credential.get("profile_url") or f"https://www.reddit.com/user/{credential.get('username')}/"
    logger.info(f"[{profile_name}] navigating to profile: {profile_url}")
    if audit:
        audit.record_event("goto_profile_start", target_url=profile_url)
    page = await _goto_in_authenticated_context(
        context,
        page,
        profile_url,
        profile_name=profile_name,
    )
    await page.wait_for_timeout(2500)
    logger.info(f"[{profile_name}] profile page URL: {page.url}")
    await save_debug_screenshot(page, f"reddit_profile_page_{profile_name}")
    await _capture_checkpoint(audit, page, context, "profile_page")

    await session.extract_from_context(
        context,
        page,
        username=credential.get("username") or credential.get("uid"),
        email=credential.get("email"),
        profile_url=profile_url,
        proxy=proxy_url,
        tags=list(credential.get("tags") or ["reddit"]),
        linked_credential_id=credential.get("credential_id"),
        display_name=credential.get("display_name") or credential.get("username"),
        fixture=bool(credential.get("fixture", False)),
        warmup_state={
            "stage": "new",
            "history": [],
        },
        device=fingerprint,
    )

    verified = await verify_reddit_session_logged_in(page, session, audit=audit)
    if not verified:
        await save_debug_screenshot(page, f"reddit_session_verify_failed_{profile_name}")
        await _capture_checkpoint(audit, page, context, "protected_destination_verify_failed")
        result["needs_attention"] = True
        raise RuntimeError("Reddit session failed authenticated destination verification on www.reddit.com")

    if persist_session:
        session.save()
        CredentialManager().set_linked_session_id(
            credential.get("credential_id") or credential.get("uid"),
            profile_name,
            platform="reddit",
        )

    result.update(
        {
            "success": True,
            "profile_name": profile_name,
            "username": session.get_username(),
            "profile_url": session.get_profile_url(),
        }
    )
    return result


def compare_attempts(reference_attempt_id: str, standalone_attempt_id: str) -> Dict[str, Any]:
    reference = load_reddit_audit(reference_attempt_id)
    standalone = load_reddit_audit(standalone_attempt_id)
    if not reference:
        raise RuntimeError(f"Reference audit not found: {reference_attempt_id}")
    if not standalone:
        raise RuntimeError(f"Standalone audit not found: {standalone_attempt_id}")
    return compare_reddit_audits(reference, standalone)


async def login_reddit(
    *,
    credential: dict,
    proxy_url: Optional[str] = None,
    proxy_source: str = "runtime",
    headless: bool = True,
    broadcast_callback: BroadcastFn = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "success": False,
        "platform": "reddit",
        "credential_id": credential.get("credential_id"),
        "profile_name": credential.get("profile_name"),
        "error": None,
        "needs_attention": False,
    }

    profile_name = credential.get("profile_name") or f"reddit_{credential.get('uid')}"
    session = RedditSession(profile_name)
    fingerprint = session.get_device_fingerprint()
    audit = RedditLoginAudit(
        mode="standalone_reddit_identity",
        credential_label=_credential_label(credential),
        session_id=profile_name,
        proxy_url=_mask_proxy(proxy_url),
        proxy_source=proxy_source,
        context_data={
            "user_agent": session.get_user_agent() or REDDIT_MOBILE_USER_AGENT,
            "viewport": session.get_viewport() or MOBILE_VIEWPORT,
            "is_mobile": True,
            "has_touch": True,
            "locale": fingerprint["locale"],
            "timezone_id": fingerprint["timezone"],
            "storage_state_present": False,
        },
    )

    async with async_playwright() as playwright:
        browser = None
        try:
            await _broadcast(
                broadcast_callback,
                "reddit_session_progress",
                {"profile_name": profile_name, "step": "launching_browser"},
            )
            browser, context = await create_browser_context(
                playwright,
                user_agent=session.get_user_agent() or REDDIT_MOBILE_USER_AGENT,
                viewport=session.get_viewport() or MOBILE_VIEWPORT,
                proxy_url=proxy_url,
                timezone_id=fingerprint["timezone"],
                locale=fingerprint["locale"],
                headless=headless,
                is_mobile=True,
                has_touch=True,
            )
            page = await context.new_page()
            audit.attach_page(page)
            audit.record_event("browser_context_created")

            await _broadcast(
                broadcast_callback,
                "reddit_session_progress",
                {"profile_name": profile_name, "step": "opening_login"},
            )
            await _broadcast(
                broadcast_callback,
                "reddit_session_progress",
                {"profile_name": profile_name, "step": "submitting_credentials"},
            )

            result = await _run_reddit_login_flow(
                page=page,
                context=context,
                credential=credential,
                profile_name=profile_name,
                proxy_url=proxy_url,
                fingerprint=fingerprint,
                audit=audit,
                persist_session=True,
                session=session,
            )
            await _broadcast(
                broadcast_callback,
                "reddit_session_progress",
                {"profile_name": profile_name, "step": "session_saved"},
            )
            result.update(
                audit.finalize(
                    success=True,
                    error=None,
                    extra={"persisted_session": True},
                )
            )
            return result
        except Exception as exc:
            result["error"] = str(exc)
            result.update(
                audit.finalize(
                    success=False,
                    error=str(exc),
                    extra={"persisted_session": False},
                )
            )
            logger.error(f"Reddit login failed for {profile_name}: {exc}")
            await _broadcast(
                broadcast_callback,
                "reddit_session_progress",
                {"profile_name": profile_name, "step": "error", "error": str(exc)},
            )
            return result
        finally:
            if browser:
                await browser.close()


async def login_reddit_from_reference_facebook_identity(
    *,
    credential: dict,
    reference_session_id: Optional[str] = None,
    headless: bool = True,
) -> Dict[str, Any]:
    chosen_session_id = _choose_reference_facebook_session(reference_session_id)
    reference_session = FacebookSession(chosen_session_id)
    if not reference_session.load() or not reference_session.has_valid_cookies():
        return {
            "success": False,
            "platform": "reddit",
            "error": f"Reference Facebook session invalid: {chosen_session_id}",
        }

    stored_proxy = reference_session.get_proxy()
    proxy_url = stored_proxy or get_system_proxy()
    if not proxy_url:
        return {
            "success": False,
            "platform": "reddit",
            "error": "Reference Facebook session has no proxy and no service proxy is configured",
        }

    fingerprint = reference_session.get_device_fingerprint()
    profile_name = f"reference_{_credential_label(credential)}_{chosen_session_id}"
    temp_session = RedditSession(profile_name)
    result: Dict[str, Any] = {
        "success": False,
        "platform": "reddit",
        "credential_id": credential.get("credential_id"),
        "profile_name": profile_name,
        "reference_session_id": chosen_session_id,
        "error": None,
    }
    audit = RedditLoginAudit(
        mode="reference_facebook_identity",
        credential_label=_credential_label(credential),
        session_id=chosen_session_id,
        proxy_url=_mask_proxy(proxy_url),
        proxy_source="session" if stored_proxy else "env",
        context_data={
            "user_agent": reference_session.get_user_agent() or DEFAULT_USER_AGENT,
            "viewport": reference_session.get_viewport() or MOBILE_VIEWPORT,
            "is_mobile": None,
            "has_touch": None,
            "locale": fingerprint["locale"],
            "timezone_id": fingerprint["timezone"],
            "storage_state_present": False,
        },
    )

    async with async_playwright() as playwright:
        browser = None
        try:
            browser, context = await create_browser_context(
                playwright,
                user_agent=reference_session.get_user_agent() or DEFAULT_USER_AGENT,
                viewport=reference_session.get_viewport() or MOBILE_VIEWPORT,
                proxy_url=proxy_url,
                timezone_id=fingerprint["timezone"],
                locale=fingerprint["locale"],
                headless=headless,
            )
            await apply_session_to_context(context, reference_session)
            page = await context.new_page()
            audit.attach_page(page)
            audit.record_event("browser_context_created", reference_session_id=chosen_session_id)

            result = await _run_reddit_login_flow(
                page=page,
                context=context,
                credential=credential,
                profile_name=profile_name,
                proxy_url=proxy_url,
                fingerprint=fingerprint,
                audit=audit,
                persist_session=False,
                session=temp_session,
            )
            result.update(
                {
                    "reference_session_id": chosen_session_id,
                    "persisted_session": False,
                }
            )
            result.update(audit.finalize(success=True, error=None, extra={"persisted_session": False}))
            return result
        except Exception as exc:
            result["error"] = str(exc)
            result.update(
                audit.finalize(
                    success=False,
                    error=str(exc),
                    extra={
                        "reference_session_id": chosen_session_id,
                        "persisted_session": False,
                    },
                )
            )
            logger.error(f"Reddit reference login failed for {profile_name}: {exc}")
            return result
        finally:
            if browser:
                await browser.close()


async def create_session_from_credentials(
    credential_uid: str,
    proxy_url: Optional[str] = None,
    proxy_source: str = "runtime",
    broadcast_callback: BroadcastFn = None,
) -> Dict[str, Any]:
    manager = CredentialManager()
    credential = manager.get_credential(credential_uid, platform="reddit")
    if not credential:
        return {
            "success": False,
            "platform": "reddit",
            "error": f"Reddit credential not found: {credential_uid}",
        }
    return await login_reddit(
        credential=credential,
        proxy_url=proxy_url,
        proxy_source=proxy_source,
        broadcast_callback=broadcast_callback,
    )


async def run_reference_login_from_credentials(
    credential_uid: str,
    *,
    reference_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    manager = CredentialManager()
    credential = manager.get_credential(credential_uid, platform="reddit")
    if not credential:
        return {
            "success": False,
            "platform": "reddit",
            "error": f"Reddit credential not found: {credential_uid}",
        }
    return await login_reddit_from_reference_facebook_identity(
        credential=credential,
        reference_session_id=reference_session_id,
    )


async def test_session(session: RedditSession, proxy_url: Optional[str] = None) -> Dict[str, Any]:
    if not session.load():
        return {"success": False, "error": f"Reddit session '{session.profile_name}' not found"}

    fingerprint = session.get_device_fingerprint()
    async with async_playwright() as playwright:
        browser = None
        try:
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
            page = await context.new_page()
            verified = await verify_reddit_session_logged_in(page, session)
            return {
                "success": verified,
                "platform": "reddit",
                "profile_name": session.profile_name,
                "error": None if verified else "Authenticated destination check failed",
            }
        except Exception as exc:
            return {
                "success": False,
                "platform": "reddit",
                "profile_name": session.profile_name,
                "error": str(exc),
            }
        finally:
            if browser:
                await browser.close()
