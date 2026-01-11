"""
Facebook Login - Working Version
Uses dispatch_event for clicks, keyboard.type for TOTP
"""

import asyncio
import pyotp
from playwright.async_api import Page
from typing import Tuple


async def click_dispatch(page: Page, selector: str, timeout: int = 3000) -> bool:
    """Click using dispatch_event - works on Facebook React components"""
    try:
        loc = page.locator(selector)
        await loc.wait_for(state="visible", timeout=timeout)
        await loc.dispatch_event('click')
        return True
    except:
        return False


async def click_text_dispatch(page: Page, text: str, timeout: int = 3000) -> bool:
    """Click element by exact text using dispatch_event"""
    try:
        loc = page.get_by_text(text, exact=True)
        await loc.wait_for(state="visible", timeout=timeout)
        await loc.dispatch_event('click')
        return True
    except:
        return False


async def login_facebook(
    page: Page,
    fb_id: str,
    fb_password: str,
    totp_secret: str,
) -> Tuple[bool, str]:
    """
    Fully automated Facebook login with 2FA.
    Returns (success, message)
    """
    try:
        # 1. Go to Facebook mobile
        await page.goto("https://m.facebook.com/", wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(0.5)

        # Check if already logged in
        content = await page.content()
        if 'name="email"' not in content and 'name="pass"' not in content:
            await click_text_dispatch(page, "Not now")
            return True, "Already logged in"

        # 2. Fill credentials
        await page.locator('input[name="email"]').fill(fb_id)
        await page.locator('input[name="pass"]').fill(fb_password)
        await asyncio.sleep(0.2)

        # 3. Click Login button
        await click_text_dispatch(page, "Log in")
        await asyncio.sleep(2)

        # 4. Handle 2FA method selection
        content = await page.content()

        # If "Try another way" appears, click it
        if 'try another way' in content.lower():
            await click_text_dispatch(page, "Try another way")
            await asyncio.sleep(0.5)

        # If on method selection page, select Authentication app
        content = await page.content()
        if 'authentication app' in content.lower():
            await click_dispatch(page, '[role="radio"]:has-text("Authentication")')
            await asyncio.sleep(0.3)
            await click_dispatch(page, '[role="button"]:has-text("Continue")')
            await asyncio.sleep(1)

        # 5. Enter TOTP code
        content = await page.content()
        if 'code' in content.lower() or '6-digit' in content.lower():
            code = pyotp.TOTP(totp_secret).now()

            # Focus input and type code
            await page.evaluate('document.querySelector("input")?.focus()')
            await page.keyboard.type(code, delay=40)
            await asyncio.sleep(0.5)

            # Wait for button to be enabled, then click
            await page.evaluate('''() => {
                return new Promise(resolve => {
                    const check = () => {
                        for (const el of document.querySelectorAll('[role="button"]')) {
                            if (el.textContent.trim() === 'Continue' && el.getAttribute('aria-disabled') !== 'true') {
                                resolve(true);
                                return;
                            }
                        }
                        setTimeout(check, 100);
                    };
                    check();
                    setTimeout(() => resolve(false), 3000);
                });
            }''')

            # Submit via dispatch_event
            await click_dispatch(page, '[role="button"]:has-text("Continue")')
            await asyncio.sleep(2)

        # 6. Handle save-device page
        if 'save-device' in page.url.lower():
            await click_text_dispatch(page, "Save")
            await asyncio.sleep(1)

        # 7. Dismiss any popups
        await click_text_dispatch(page, "Not now")

        # 8. Verify login
        url = page.url.lower()
        if 'checkpoint' in url:
            return False, "Account checkpoint"

        # Check for feed content
        has_feed = await page.evaluate('''() => {
            const text = document.body.innerText;
            return text.includes("What's on your mind") ||
                   text.includes("News Feed") ||
                   text.includes("Stories");
        }''')

        if has_feed or ('facebook.com' in url and 'login' not in url):
            return True, "Login successful"

        return False, "Unknown state"

    except Exception as e:
        return False, f"Error: {str(e)}"


async def login_profile(profile_id: str, fb_id: str, fb_password: str, totp_secret: str) -> dict:
    """
    Complete login for one profile - starts browser, logs in, returns result
    """
    import requests
    from playwright.async_api import async_playwright

    # Start AdsPower profile
    resp = requests.get(
        'http://local.adspower.net:50325/api/v1/browser/start',
        params={'user_id': profile_id}
    ).json()

    if resp['code'] != 0:
        return {'profile_id': profile_id, 'success': False, 'message': f"Failed to start: {resp}"}

    ws = resp['data']['ws']['puppeteer']
    await asyncio.sleep(5)  # Wait for browser to fully start

    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(ws)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = context.pages[0] if context.pages else await context.new_page()

            success, message = await login_facebook(page, fb_id, fb_password, totp_secret)

            return {'profile_id': profile_id, 'success': success, 'message': message}

        except Exception as e:
            return {'profile_id': profile_id, 'success': False, 'message': str(e)}
