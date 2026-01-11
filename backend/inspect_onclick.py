"""
Inspect onClick function to understand what it expects
"""
import asyncio
from playwright.async_api import async_playwright

async def inspect_onclick():
    ws = 'ws://127.0.0.1:60756/devtools/browser/5a1d5093-73a9-411d-a4b2-19ea1853b55c'

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(ws)
        context = browser.contexts[0]
        page = context.pages[0]

        print('1. Inspect onClick function...')
        onclick_code = await page.evaluate('''() => {
            const el = document.querySelector("[data-login-target=true]");
            if (!el) return "no element";

            const propsKey = Object.keys(el).find(k => k.startsWith("__reactProps"));
            if (!propsKey) return "no props";

            const onClick = el[propsKey].onClick;
            if (typeof onClick !== "function") return "no onClick";

            return onClick.toString().substring(0, 1000);
        }''')
        print(f'   onClick code: {onclick_code}')

        print()
        print('2. Try different event simulation...')

        # First refresh page
        await page.goto('https://m.facebook.com/', wait_until='domcontentloaded')
        await asyncio.sleep(0.5)
        await page.locator('input[name="email"]').fill('61571382945843')
        await page.locator('input[name="pass"]').fill('XlPOSouthardSOctovia')
        await asyncio.sleep(0.3)

        # Mark parent again
        await page.evaluate('''() => {
            const btn = [...document.querySelectorAll("[role=button]")]
                .find(b => b.textContent.trim() === "Log in");
            if (!btn) return null;

            let el = btn;
            for (let i = 0; i < 10; i++) {
                const propsKey = Object.keys(el).find(k => k.startsWith("__reactProps"));
                if (propsKey && typeof el[propsKey].onClick === "function") {
                    el.setAttribute("data-login-target", "true");
                    return true;
                }
                el = el.parentElement;
                if (!el) break;
            }
            return false;
        }''')

        print('3. Try dispatchEvent with trusted-looking event...')
        result = await page.evaluate('''() => {
            const el = document.querySelector("[data-login-target=true]");
            if (!el) return "no element";

            // Create event that matches what React expects
            const event = new MouseEvent("click", {
                bubbles: true,
                cancelable: true,
                view: window,
                detail: 1,
                screenX: 0,
                screenY: 0,
                clientX: 100,
                clientY: 500,
                ctrlKey: false,
                altKey: false,
                shiftKey: false,
                metaKey: false,
                button: 0,
                buttons: 0,
                relatedTarget: null
            });

            el.dispatchEvent(event);
            return "dispatched";
        }''')
        print(f'   Result: {result}')
        await asyncio.sleep(2)

        content = await page.content()
        print(f'   URL: {page.url}')
        if 'email' not in content:
            print('   SUCCESS!')
        else:
            print('   Still on login - checking React event system')

            # Check if there's something intercepting
            info = await page.evaluate('''() => {
                const el = document.querySelector("[data-login-target=true]");
                const propsKey = Object.keys(el).find(k => k.startsWith("__reactProps"));
                const props = el[propsKey];

                // Check ALL props for any handlers
                const handlers = {};
                for (const key of Object.keys(props)) {
                    if (typeof props[key] === "function") {
                        handlers[key] = props[key].name || "anonymous";
                    }
                }
                return handlers;
            }''')
            print(f'   All handlers on element: {info}')

asyncio.run(inspect_onclick())
