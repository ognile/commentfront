"""
Diagnose Profile 8 - Check hydration state and click behavior
"""
import asyncio
from playwright.async_api import async_playwright

async def diagnose():
    ws = 'ws://127.0.0.1:60756/devtools/browser/5a1d5093-73a9-411d-a4b2-19ea1853b55c'

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(ws)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = context.pages[0] if context.pages else await context.new_page()

        print('1. Navigating to m.facebook.com...')
        await page.goto('https://m.facebook.com/', wait_until='domcontentloaded', timeout=15000)
        await asyncio.sleep(0.5)

        # Check if login page
        content = await page.content()
        if 'name="email"' not in content:
            print('   Not on login page - may be logged in already')
            print('   Current URL:', page.url)
            return

        print('2. Filling credentials...')
        await page.locator('input[name="email"]').fill('61571382945843')
        await page.locator('input[name="pass"]').fill('XlPOSouthardSOctovia')
        await asyncio.sleep(0.3)
        print('   Done')

        print('3. Checking Login button hydration state...')
        hydration = await page.evaluate('''() => {
            const buttons = [...document.querySelectorAll('[role="button"]')];
            const results = [];

            for (const btn of buttons) {
                const text = btn.textContent.trim();
                const keys = Object.keys(btn);
                const reactFiber = keys.find(k => k.startsWith("__reactFiber"));
                const reactProps = keys.find(k => k.startsWith("__reactProps"));
                const reactEvents = keys.find(k => k.startsWith("__reactEvents"));

                results.push({
                    text: text.substring(0, 30),
                    hasReactFiber: !!reactFiber,
                    hasReactProps: !!reactProps,
                    hasReactEvents: !!reactEvents,
                    allKeys: keys.filter(k => k.startsWith("__")).join(", ")
                });
            }
            return results;
        }''')

        for item in hydration:
            print(f'   Button: "{item["text"]}"')
            print(f'      Fiber: {item["hasReactFiber"]}, Props: {item["hasReactProps"]}, Events: {item["hasReactEvents"]}')
            print(f'      Keys: {item["allKeys"]}')

        print('4. Waiting 3 more seconds and checking again...')
        await asyncio.sleep(3)

        hydration2 = await page.evaluate('''() => {
            const btn = [...document.querySelectorAll('[role="button"]')]
                .find(b => b.textContent.trim() === "Log in");
            if (!btn) return {found: false};
            const keys = Object.keys(btn);
            return {
                found: true,
                hasReactFiber: keys.some(k => k.startsWith("__reactFiber")),
                hasReactProps: keys.some(k => k.startsWith("__reactProps")),
                allKeys: keys.filter(k => k.startsWith("__")).join(", ")
            };
        }''')
        print(f'   Login button after 3s: {hydration2}')

        print('5. Testing dispatch_event click on Login button...')
        try:
            loc = page.get_by_text('Log in', exact=True)
            await loc.wait_for(state='visible', timeout=2000)
            await loc.dispatch_event('click')
            print('   dispatch_event fired')
            await asyncio.sleep(2)

            # Check if page changed
            new_url = page.url
            new_content = await page.content()
            print(f'   URL after click: {new_url}')
            if 'checkpoint' in new_url or 'two_step' in new_url or 'Try another way' in new_content:
                print('   SUCCESS - Login button worked!')
            elif 'name="email"' in new_content:
                print('   FAILED - Still on login page')
            else:
                print('   Changed - checking content...')
        except Exception as e:
            print(f'   Error: {e}')

        print('6. Current page state:')
        state = await page.evaluate('''() => {
            return {
                url: location.href,
                hasEmailField: !!document.querySelector('input[name="email"]'),
                has2FA: document.body.innerText.includes("Try another way") ||
                        document.body.innerText.includes("two-factor") ||
                        document.body.innerText.includes("code"),
                bodySnippet: document.body.innerText.substring(0, 500)
            };
        }''')
        print(f'   URL: {state["url"]}')
        print(f'   Has email field: {state["hasEmailField"]}')
        print(f'   Has 2FA indicators: {state["has2FA"]}')
        print(f'   Body: {state["bodySnippet"][:200]}...')

asyncio.run(diagnose())
