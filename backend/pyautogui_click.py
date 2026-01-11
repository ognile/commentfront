"""
Use pyautogui for OS-level mouse clicks - bypasses all Playwright/CDP issues
"""
import requests
import asyncio
import pyautogui
import time
from playwright.async_api import async_playwright

async def get_button_screen_position():
    """Get the screen coordinates of the Login button"""
    # Check if Profile 8 is running
    resp = requests.get(
        'http://local.adspower.net:50325/api/v1/browser/active',
        params={'user_id': 'k18q0lab'}
    ).json()

    if resp.get('data', {}).get('status') != 'Active':
        # Start it
        resp = requests.get(
            'http://local.adspower.net:50325/api/v1/browser/start',
            params={'user_id': 'k18q0lab'}
        ).json()
        if resp['code'] != 0:
            print('Failed to start browser:', resp)
            return None
        await asyncio.sleep(5)
        resp = requests.get(
            'http://local.adspower.net:50325/api/v1/browser/active',
            params={'user_id': 'k18q0lab'}
        ).json()

    ws = resp['data']['ws']['puppeteer']
    print('Using browser WS:', ws)

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(ws)
        context = browser.contexts[0]

        # Find or create a page
        page = None
        for pg in context.pages:
            if 'facebook.com' in pg.url or pg.url.startswith('about:'):
                page = pg
                break
        if not page:
            page = await context.new_page()

        # Navigate to login
        print('Navigating to Facebook login...')
        await page.goto('https://m.facebook.com/', wait_until='domcontentloaded')
        await asyncio.sleep(0.5)

        content = await page.content()
        if 'name="email"' not in content:
            print('Not on login page!')
            return None

        # Fill credentials
        print('Filling credentials...')
        await page.locator('input[name="email"]').fill('61571382945843')
        await page.locator('input[name="pass"]').fill('XlPOSouthardSOctovia')
        await asyncio.sleep(0.3)

        # Get button viewport position
        btn = page.locator('[role="button"]:has-text("Log in")')
        box = await btn.bounding_box()
        if not box:
            print('Could not get button position')
            return None

        print(f'Button viewport position: x={box["x"]}, y={box["y"]}, w={box["width"]}, h={box["height"]}')

        # Get window position and viewport offset
        # This is tricky - we need the browser window's screen position
        window_info = await page.evaluate('''() => ({
            screenX: window.screenX,
            screenY: window.screenY,
            innerWidth: window.innerWidth,
            innerHeight: window.innerHeight,
            outerWidth: window.outerWidth,
            outerHeight: window.outerHeight,
            devicePixelRatio: window.devicePixelRatio
        })''')
        print(f'Window info: {window_info}')

        # Calculate screen position
        # The viewport starts below the browser chrome (tabs, address bar)
        # Approximate chrome height
        chrome_height = window_info['outerHeight'] - window_info['innerHeight']

        screen_x = window_info['screenX'] + box['x'] + box['width'] / 2
        screen_y = window_info['screenY'] + chrome_height + box['y'] + box['height'] / 2

        # Account for device pixel ratio on retina displays
        dpr = window_info['devicePixelRatio']
        if dpr > 1:
            # On retina, coordinates might need adjustment
            # macOS reports coordinates in points, not pixels
            pass  # Usually no adjustment needed for pyautogui on macOS

        print(f'Calculated screen position: ({screen_x}, {screen_y})')

        return {
            'x': int(screen_x),
            'y': int(screen_y),
            'page': page,
            'browser': browser
        }


async def click_with_pyautogui():
    result = await get_button_screen_position()
    if not result:
        print('Failed to get button position')
        return

    x, y = result['x'], result['y']
    page = result['page']

    print(f'\nClicking at screen position ({x}, {y}) using pyautogui...')

    # First, let's verify we can see the current mouse position
    current_pos = pyautogui.position()
    print(f'Current mouse position: {current_pos}')

    # Move to position first (so user can see where we're clicking)
    pyautogui.moveTo(x, y, duration=0.3)
    print('Moved mouse to button position')

    time.sleep(0.5)

    # Click
    pyautogui.click()
    print('Clicked!')

    # Wait and check result
    time.sleep(2)

    content = await page.content()
    url = page.url
    print(f'\nURL after click: {url}')

    if 'email' not in content:
        print('SUCCESS! Page changed!')
        if 'another way' in content.lower() or 'code' in content.lower():
            print('On 2FA page!')
    else:
        print('Still on login page - pyautogui click may have missed')
        print('Try adjusting the coordinates or check browser window position')


if __name__ == '__main__':
    asyncio.run(click_with_pyautogui())
