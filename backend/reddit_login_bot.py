"""
Reddit login/session bootstrap for mobile-web execution.
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from playwright.async_api import Page, async_playwright

from browser_factory import create_browser_context
from config import MOBILE_VIEWPORT, REDDIT_MOBILE_USER_AGENT
from comment_bot import dump_interactive_elements, save_debug_screenshot
from credentials import CredentialManager
from reddit_selectors import COOKIE_BANNER, LOGIN
from reddit_session import RedditSession, verify_reddit_session_logged_in

logger = logging.getLogger("RedditLoginBot")

BroadcastFn = Optional[Callable[[str, dict], Awaitable[None]]]


async def _broadcast(callback: BroadcastFn, update_type: str, payload: dict):
    if callback:
        await callback(update_type, payload)


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


async def login_reddit(
    *,
    credential: dict,
    proxy_url: Optional[str] = None,
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
    # Reddit login: prefer username over email (email login may trigger CAPTCHA more often)
    login_identifier = credential.get("username") or credential.get("uid") or credential.get("email")
    password = credential.get("password")

    if not login_identifier or not password:
        result["error"] = "Reddit credential missing login identifier or password"
        return result

    async with async_playwright() as playwright:
        browser = None
        login_requests = []
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

            await _broadcast(
                broadcast_callback,
                "reddit_session_progress",
                {"profile_name": profile_name, "step": "opening_login"},
            )

            # Capture network requests during login for debugging
            def _capture_request(request):
                try:
                    url = request.url.lower()
                    if any(kw in url for kw in ("login", "auth", "captcha", "recaptcha", "svc/shreddit")):
                        login_requests.append({"url": request.url, "method": request.method, "post_data": (request.post_data or "")[:500]})
                except Exception:
                    pass

            page.on("request", _capture_request)

            logger.info(f"[{profile_name}] navigating to reddit.com/login")
            await _goto_with_retry(page, "https://www.reddit.com/login", profile_name=profile_name)
            await page.wait_for_timeout(2000)
            await _dismiss_cookie_banner(page)
            logger.info(f"[{profile_name}] reddit login page: {page.url}")
            await save_debug_screenshot(page, f"reddit_login_page_{profile_name}")

            await _broadcast(
                broadcast_callback,
                "reddit_session_progress",
                {"profile_name": profile_name, "step": "submitting_credentials"},
            )

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
                raise RuntimeError("Reddit login form inputs not found on www.reddit.com/login")

            await save_debug_screenshot(page, f"reddit_form_filled_{profile_name}")
            submit_clicked = await _click_first(page, LOGIN["submit_button"], timeout_ms=5000)
            if not submit_clicked:
                try:
                    await page.locator(LOGIN["password_input"][0]).press("Enter")
                except Exception:
                    pass
            logger.info(f"[{profile_name}] login form submitted (button={submit_clicked})")
            await page.wait_for_timeout(5000)
            await save_debug_screenshot(page, f"reddit_after_submit_{profile_name}")
            logger.info(f"[{profile_name}] after submit URL: {page.url}")

            otp_handled = await _handle_otp(page, credential)
            if otp_handled:
                logger.info(f"[{profile_name}] OTP submitted on reddit.com")
                await _wait_for_otp_resolution(page, context, profile_name=profile_name)
                await page.wait_for_timeout(3000)

            await _wait_for_authenticated_surface(page, context, profile_name=profile_name)
            await page.wait_for_timeout(1500)
            await _dismiss_cookie_banner(page)
            await save_debug_screenshot(page, f"reddit_after_login_{profile_name}")
            logger.info(f"[{profile_name}] post-login URL: {page.url}")
            await _log_auth_state(context, profile_name)

            try:
                await _click_first(page, LOGIN["modal_close"], timeout_ms=2000)
            except Exception:
                pass

            profile_url = credential.get("profile_url") or f"https://www.reddit.com/user/{credential.get('username')}/"
            logger.info(f"[{profile_name}] navigating to profile: {profile_url}")
            page = await _goto_in_authenticated_context(
                context,
                page,
                profile_url,
                profile_name=profile_name,
            )
            await page.wait_for_timeout(2500)
            logger.info(f"[{profile_name}] profile page URL: {page.url}")
            await save_debug_screenshot(page, f"reddit_profile_page_{profile_name}")

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

            verified = await verify_reddit_session_logged_in(page, session)
            if not verified:
                await save_debug_screenshot(page, f"reddit_session_verify_failed_{profile_name}")
                result["needs_attention"] = True
                raise RuntimeError("Reddit session failed authenticated destination verification on www.reddit.com")

            session.save()
            CredentialManager().set_linked_session_id(credential.get("credential_id") or credential.get("uid"), profile_name, platform="reddit")

            result.update(
                {
                    "success": True,
                    "profile_name": profile_name,
                    "username": session.get_username(),
                    "profile_url": session.get_profile_url(),
                }
            )
            await _broadcast(
                broadcast_callback,
                "reddit_session_progress",
                {"profile_name": profile_name, "step": "session_saved"},
            )
            return result
        except Exception as exc:
            result["error"] = str(exc)
            if login_requests:
                logger.error(f"[{profile_name}] reddit login requests: {login_requests[-10:]}")
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


async def create_session_from_credentials(
    credential_uid: str,
    proxy_url: Optional[str] = None,
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
        broadcast_callback=broadcast_callback,
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
