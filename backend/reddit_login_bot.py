"""
Reddit login/session bootstrap for mobile-web execution.
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from playwright.async_api import Page, async_playwright

from browser_factory import create_browser_context
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


async def _wait_for_auth_cookies(context, timeout_ms: int = 15000) -> bool:
    deadline = asyncio.get_event_loop().time() + (timeout_ms / 1000.0)
    while asyncio.get_event_loop().time() < deadline:
        cookies = await context.cookies()
        names = {str(cookie.get("name") or "") for cookie in cookies}
        if "token_v2" in names or "reddit_session" in names:
            return True
        await asyncio.sleep(0.5)
    return False


async def _handle_otp(page: Page, credential: dict) -> bool:
    otp_input_found = False
    for selector in LOGIN["otp_input"]:
        try:
            locator = page.locator(selector).first
            if await locator.count() > 0 and await locator.is_visible():
                otp_input_found = True
                break
        except Exception:
            continue

    if not otp_input_found:
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
        await page.locator(LOGIN["otp_input"][0]).press("Enter")
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
        try:
            await _broadcast(
                broadcast_callback,
                "reddit_session_progress",
                {"profile_name": profile_name, "step": "launching_browser"},
            )
            browser, context = await create_browser_context(
                playwright,
                user_agent=None,
                viewport=None,
                proxy_url=proxy_url,
                timezone_id=fingerprint["timezone"],
                locale=fingerprint["locale"],
                headless=headless,
            )
            page = await context.new_page()

            await _broadcast(
                broadcast_callback,
                "reddit_session_progress",
                {"profile_name": profile_name, "step": "opening_login"},
            )
            await page.goto("https://www.reddit.com/login/", wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(2000)
            await _dismiss_cookie_banner(page)
            logger.info(f"[{profile_name}] login page loaded: {page.url}")
            await save_debug_screenshot(page, f"reddit_login_page_{profile_name}")

            filled_user = await _fill_first(page, LOGIN["username_input"], str(login_identifier))
            filled_password = await _fill_first(page, LOGIN["password_input"], str(password))
            logger.info(f"[{profile_name}] form fill: user={filled_user} pass={filled_password} identifier={login_identifier[:3]}***")
            if not filled_user or not filled_password:
                await save_debug_screenshot(page, f"reddit_login_inputs_missing_{profile_name}")
                await dump_interactive_elements(page, "REDDIT LOGIN INPUTS NOT FOUND")
                raise RuntimeError("Failed to locate Reddit login inputs")

            await _broadcast(
                broadcast_callback,
                "reddit_session_progress",
                {"profile_name": profile_name, "step": "submitting_credentials"},
            )
            try:
                await page.locator(LOGIN["password_input"][0]).press("Enter")
            except Exception:
                await _click_first(page, LOGIN["submit_button"], timeout_ms=4000)

            await page.wait_for_timeout(4000)
            await save_debug_screenshot(page, f"reddit_after_submit_{profile_name}")
            current_url_after = page.url
            logger.info(f"[{profile_name}] after submit URL: {current_url_after}")

            # Check for login error messages
            try:
                body_text = await page.locator("body").inner_text()
                if "something went wrong" in body_text.lower() or "incorrect" in body_text.lower():
                    logger.warning(f"[{profile_name}] login error on page: {body_text[:300]}")
                    await dump_interactive_elements(page, f"REDDIT LOGIN ERROR for {profile_name}")
            except Exception:
                pass

            otp_handled = await _handle_otp(page, credential)
            if otp_handled:
                logger.info(f"[{profile_name}] OTP submitted, waiting for auth cookies")
                await save_debug_screenshot(page, f"reddit_after_otp_{profile_name}")
            else:
                logger.info(f"[{profile_name}] no OTP prompt detected")

            # Log all cookie names for debugging
            all_cookies = await context.cookies()
            cookie_names = [c.get("name") for c in all_cookies]
            logger.info(f"[{profile_name}] cookies after login: {cookie_names}")

            if not await _wait_for_auth_cookies(context, timeout_ms=15000):
                all_cookies2 = await context.cookies()
                cookie_names2 = [c.get("name") for c in all_cookies2]
                logger.error(f"[{profile_name}] auth cookies missing. all cookies: {cookie_names2}")
                await save_debug_screenshot(page, f"reddit_login_failed_{profile_name}")
                await dump_interactive_elements(page, "REDDIT LOGIN AUTH COOKIES MISSING")
                raise RuntimeError("Reddit auth cookies were not created after login submission")

            try:
                await _click_first(page, LOGIN["modal_close"], timeout_ms=2000)
            except Exception:
                pass

            profile_url = credential.get("profile_url") or f"https://www.reddit.com/user/{credential.get('username')}/"
            logger.info(f"[{profile_name}] navigating to profile: {profile_url}")
            await page.goto(profile_url, wait_until="domcontentloaded", timeout=45000)
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
            )

            verified = await verify_reddit_session_logged_in(page, session)
            if not verified:
                await save_debug_screenshot(page, f"reddit_session_verify_failed_{profile_name}")
                result["needs_attention"] = True
                raise RuntimeError("Reddit session created cookies but failed authenticated destination verification")

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
                user_agent=session.get_user_agent(),
                viewport=session.get_viewport(),
                proxy_url=proxy_url or session.get_proxy(),
                timezone_id=fingerprint["timezone"],
                locale=fingerprint["locale"],
                headless=True,
                storage_state=session.get_storage_state(),
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
