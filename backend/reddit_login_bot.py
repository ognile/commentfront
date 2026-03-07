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

            # Capture network requests during login for debugging
            login_requests = []

            def _capture_request(request):
                try:
                    url = request.url.lower()
                    if any(kw in url for kw in ("login", "auth", "captcha", "recaptcha", "svc/shreddit")):
                        login_requests.append({"url": request.url, "method": request.method, "post_data": (request.post_data or "")[:500]})
                except Exception:
                    pass

            page.on("request", _capture_request)

            logger.info(f"[{profile_name}] starting reddit login with human-like interaction")
            await page.goto("https://www.reddit.com/login/", wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(2000)
            await _dismiss_cookie_banner(page)
            logger.info(f"[{profile_name}] login page loaded: {page.url}")
            await save_debug_screenshot(page, f"reddit_login_page_{profile_name}")

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
                logger.warning(f"[{profile_name}] OTP generation failed: {otp_err}")

            # Call Reddit's shreddit login API directly to bypass reCAPTCHA
            # The CSRF token is set as a cookie when the login page loads
            login_result = await page.evaluate(
                """async (data) => {
                    try {
                        // Extract CSRF token from cookie
                        const csrf = document.cookie.split(';')
                            .map(c => c.trim().split('='))
                            .find(([k]) => k === 'csrf_token');
                        const csrfToken = csrf ? csrf[1] : '';

                        // First try login without captcha
                        const form = new URLSearchParams();
                        form.append('username', data.username);
                        form.append('password', data.password);
                        form.append('csrf_token', csrfToken);
                        if (data.otp) form.append('otp', data.otp);

                        const resp = await fetch('/svc/shreddit/account/login', {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/x-www-form-urlencoded',
                            },
                            body: form.toString(),
                            credentials: 'include',
                        });
                        const text = await resp.text();
                        return {
                            ok: resp.ok,
                            status: resp.status,
                            body: text.substring(0, 2000),
                            csrf: csrfToken.substring(0, 10) + '...',
                            redirected: resp.redirected,
                            url: resp.url,
                        };
                    } catch (e) {
                        return {ok: false, error: e.message};
                    }
                }""",
                {"username": str(login_identifier), "password": str(password), "otp": otp_code},
            )
            logger.info(f"[{profile_name}] shreddit login: status={login_result.get('status')} ok={login_result.get('ok')} url={login_result.get('url')} body={str(login_result.get('body', ''))[:500]}")

            if login_result.get("error"):
                logger.error(f"[{profile_name}] shreddit fetch error: {login_result['error']}")
                raise RuntimeError(f"Reddit login API error: {login_result['error']}")

            # Check if login succeeded (200/302 to home) or needs OTP
            status = login_result.get("status", 0)
            body = str(login_result.get("body", ""))

            if status == 400 and "TWO_FA" in body.upper():
                # Need OTP - generate and retry
                if not otp_code:
                    logger.info(f"[{profile_name}] 2FA required, generating OTP")
                    manager = CredentialManager()
                    otp_data = manager.generate_otp(
                        credential.get("credential_id") or credential.get("uid"),
                        platform="reddit",
                    )
                    if otp_data.get("valid") and otp_data.get("code"):
                        otp_code = otp_data["code"]
                    else:
                        raise RuntimeError(f"OTP generation failed: {otp_data}")
                    # Retry with OTP
                    login_result = await page.evaluate(
                        """async (data) => {
                            try {
                                const csrf = document.cookie.split(';')
                                    .map(c => c.trim().split('='))
                                    .find(([k]) => k === 'csrf_token');
                                const csrfToken = csrf ? csrf[1] : '';
                                const form = new URLSearchParams();
                                form.append('username', data.username);
                                form.append('password', data.password);
                                form.append('csrf_token', csrfToken);
                                form.append('otp', data.otp);
                                const resp = await fetch('/svc/shreddit/account/login', {
                                    method: 'POST',
                                    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                                    body: form.toString(),
                                    credentials: 'include',
                                });
                                const text = await resp.text();
                                return {ok: resp.ok, status: resp.status, body: text.substring(0, 2000), url: resp.url};
                            } catch (e) {
                                return {ok: false, error: e.message};
                            }
                        }""",
                        {"username": str(login_identifier), "password": str(password), "otp": otp_code},
                    )
                    logger.info(f"[{profile_name}] shreddit login+OTP: status={login_result.get('status')} body={str(login_result.get('body', ''))[:500]}")

            # Reload page to pick up session cookies
            await page.goto("https://www.reddit.com/", wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(2500)
            await _dismiss_cookie_banner(page)
            await save_debug_screenshot(page, f"reddit_after_login_{profile_name}")
            logger.info(f"[{profile_name}] after login home URL: {page.url}")

            # Check for auth cookies
            all_cookies = await context.cookies()
            cookie_names = [c.get("name") for c in all_cookies]
            logger.info(f"[{profile_name}] cookies after login: {cookie_names}")

            auth_names = {"reddit_session"}
            if not (auth_names & set(cookie_names)):
                # token_v2 might be the auth cookie on new reddit
                if "token_v2" not in cookie_names:
                    await save_debug_screenshot(page, f"reddit_login_failed_{profile_name}")
                    raise RuntimeError(f"Reddit auth cookies missing. cookies: {cookie_names}")

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
