"""
Test click approaches on Profile 8
"""
import requests
import asyncio
from playwright.async_api import async_playwright
import time

async def test_click_workaround():
    # Start Profile 8
    resp = requests.get(
        'http://local.adspower.net:50325/api/v1/browser/start',
        params={'user_id': 'k18q0lab'}
    ).json()

    if resp['code'] != 0:
        print('Failed to start browser:', resp)
        return

    ws = resp['data']['ws']['puppeteer']
    print('Browser started, WS:', ws)
    print('Waiting 5s for browser to fully initialize...')
    await asyncio.sleep(5)

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(ws)

        # Wait for context
        for _ in range(10):
            if browser.contexts:
                break
            await asyncio.sleep(0.5)

        context = browser.contexts[0] if browser.contexts else await browser.new_context()

        # Wait for page
        for _ in range(10):
            if context.pages:
                break
            await asyncio.sleep(0.5)

        # Create new page to avoid stale page issues
        page = await context.new_page()

        print('Navigating to Facebook...')
        await page.goto('https://m.facebook.com/', wait_until='domcontentloaded', timeout=15000)
        await asyncio.sleep(0.5)

        content = await page.content()
        if 'name="email"' not in content:
            print('Not on login page, may be logged in')
            return

        print('Filling credentials...')
        await page.locator('input[name="email"]').fill('61571382945843')
        await page.locator('input[name="pass"]').fill('XlPOSouthardSOctovia')
        await asyncio.sleep(0.3)

        print('Test 1: Click with short timeout...')
        try:
            await page.locator('[role="button"]:has-text("Log in")').click(timeout=500, force=True)
            print('Click completed!')
        except Exception as e:
            print(f'Click timed out: {str(e)[:100]}')

        await asyncio.sleep(2)
        content = await page.content()
        print(f'URL after: {page.url}')
        if 'email' not in content:
            print('SUCCESS! Page changed!')
            return

        print('Test 2: dispatch_event on button...')
        await page.locator('[role="button"]:has-text("Log in")').dispatch_event('click')
        await asyncio.sleep(2)
        content = await page.content()
        print(f'URL after dispatch: {page.url}')
        if 'email' not in content:
            print('dispatch_event worked!')
            return

        print('Test 3: Focus password and Enter...')
        await page.locator('input[name="pass"]').focus()
        await page.keyboard.press('Enter')
        await asyncio.sleep(2)
        content = await page.content()
        print(f'URL after Enter: {page.url}')
        if 'email' not in content:
            print('Enter key worked!')
            return

        print('All tests failed - click issue persists')

if __name__ == '__main__':
    asyncio.run(test_click_workaround())
