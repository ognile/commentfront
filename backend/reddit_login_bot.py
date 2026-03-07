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

            # Use old.reddit.com API login to bypass reCAPTCHA on modern login page
            logger.info(f"[{profile_name}] using old.reddit.com API login")
            await page.goto("https://old.reddit.com/", wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(1500)
            await _dismiss_cookie_banner(page)

            await _broadcast(
                broadcast_callback,
                "reddit_session_progress",
                {"profile_name": profile_name, "step": "submitting_credentials"},
            )

            # Generate OTP upfront since these accounts have 2FA
            otp_code = ""
            try:
                manager = CredentialManager()
                otp_data = manager.generate_otp(
                    credential.get("credential_id") or credential.get("uid"),
                    platform="reddit",
                )
                if otp_data.get("valid") and otp_data.get("code"):
                    otp_code = otp_data["code"]
                    logger.info(f"[{profile_name}] generated OTP for login")
            except Exception as otp_err:
                logger.warning(f"[{profile_name}] OTP generation failed (will try without): {otp_err}")

            # Call Reddit's JSON login API directly from page context
            api_result = await page.evaluate(
                """async (data) => {
                    try {
                        const form = new URLSearchParams();
                        form.append('user', data.username);
                        form.append('passwd', data.password);
                        form.append('api_type', 'json');
                        form.append('rem', 'true');
                        if (data.otp) form.append('otp', data.otp);
                        const resp = await fetch('/api/login/' + data.username, {
                            method: 'POST',
                            headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                            body: form.toString(),
                            credentials: 'include',
                        });
                        const text = await resp.text();
                        let json = null;
                        try { json = JSON.parse(text); } catch(e) {}
                        return {ok: resp.ok, status: resp.status, statusText: resp.statusText, body: text.substring(0, 1000), data: json};
                    } catch (e) {
                        return {ok: false, error: e.message};
                    }
                }""",
                {"username": str(login_identifier), "password": str(password), "otp": otp_code},
            )
            logger.info(f"[{profile_name}] API login response: status={api_result.get('status')} ok={api_result.get('ok')} body={str(api_result.get('body', ''))[:500]}")
            if api_result.get("error"):
                logger.error(f"[{profile_name}] API fetch error: {api_result['error']}")

            api_json = (api_result.get("data") or {}).get("json", {})
            api_errors = api_json.get("errors", [])
            api_errors_str = str(api_errors)

            # Check if 2FA is required (Reddit returns WRONG_OTP or similar when 2FA is enabled)
            needs_otp = any(
                any(kw in str(err).upper() for kw in ("WRONG_OTP", "TWO_FA", "BAD_OTP", "OTP", "2FA"))
                for err in api_errors
            )
            if api_errors and not needs_otp:
                error_msg = "; ".join(str(e) for e in api_errors)
                logger.error(f"[{profile_name}] Reddit API login errors: {error_msg}")
                await save_debug_screenshot(page, f"reddit_api_login_error_{profile_name}")
                raise RuntimeError(f"Reddit API login failed: {error_msg}")

            if needs_otp:
                logger.info(f"[{profile_name}] 2FA required, generating OTP")
                manager = CredentialManager()
                otp_data = manager.generate_otp(
                    credential.get("credential_id") or credential.get("uid"),
                    platform="reddit",
                )
                if not otp_data.get("valid") or not otp_data.get("code"):
                    raise RuntimeError(f"Failed to generate Reddit OTP: {otp_data.get('error')}")
                # Retry login with OTP
                api_result = await page.evaluate(
                    """async (data) => {
                        try {
                            const form = new URLSearchParams();
                            form.append('user', data.username);
                            form.append('passwd', data.password);
                            form.append('api_type', 'json');
                            form.append('rem', 'true');
                            form.append('otp', data.otp);
                            const resp = await fetch('/api/login/' + data.username, {
                                method: 'POST',
                                headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                                body: form.toString(),
                                credentials: 'include',
                            });
                            const json = await resp.json();
                            return {ok: resp.ok, status: resp.status, data: json};
                        } catch (e) {
                            return {ok: false, error: e.message};
                        }
                    }""",
                    {"username": str(login_identifier), "password": str(password), "otp": otp_data["code"]},
                )
                logger.info(f"[{profile_name}] API login+OTP response: status={api_result.get('status')} errors={api_result.get('data', {}).get('json', {}).get('errors', [])}")
                otp_errors = (api_result.get("data") or {}).get("json", {}).get("errors", [])
                if otp_errors:
                    raise RuntimeError(f"Reddit API login+OTP failed: {otp_errors}")

            # Log cookies after API login
            all_cookies = await context.cookies()
            cookie_names = [c.get("name") for c in all_cookies]
            logger.info(f"[{profile_name}] cookies after API login: {cookie_names}")

            # Navigate to new reddit to pick up cross-domain session
            await page.goto("https://www.reddit.com/", wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(2500)
            await _dismiss_cookie_banner(page)
            await save_debug_screenshot(page, f"reddit_after_login_{profile_name}")
            logger.info(f"[{profile_name}] new reddit home URL: {page.url}")

            # Final cookie check
            all_cookies = await context.cookies()
            cookie_names = [c.get("name") for c in all_cookies]
            logger.info(f"[{profile_name}] cookies after new reddit: {cookie_names}")
            auth_names = {"token_v2", "reddit_session"}
            if not (auth_names & set(cookie_names)):
                await save_debug_screenshot(page, f"reddit_login_failed_{profile_name}")
                raise RuntimeError(f"Reddit auth cookies missing after API login. cookies: {cookie_names}")

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
