import asyncio
import logging
import os
import sys
import pyotp
from playwright.async_api import async_playwright

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from adspower import AdsPowerClient
from credentials import CredentialManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Solver")

async def interactive_solve():
    adspower = AdsPowerClient()
    cm = CredentialManager("../accounts info.txt")
    target_uid = "61571383800545" 
    creds = cm.get_credential(target_uid)
    
    profiles = adspower.get_profile_list(page_size=100)
    target_profile = next((p for p in profiles if "android_7" in p.get("name", "").lower() or "android 7" in p.get("name", "").lower()), None)
    if not target_profile: target_profile = profiles[6]
        
    logger.info(f"Target: {target_profile['name']} | Creds: {target_uid}")
    launch_data = adspower.start_profile(target_profile['user_id'])
    ws_endpoint = launch_data["ws_endpoint"]
    
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(ws_endpoint)
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()
        
        # Analyze
        logger.info("--- CURRENT STATE ANALYSIS ---")
        try:
            visible_text = await page.evaluate("document.body.innerText")
            content = await page.content()
        except:
            visible_text = ""
            content = ""
            
        logger.info(f"URL: {page.url}")
        logger.info(f"Snippet: {visible_text[:200].replace(chr(10), ' | ')}")
        await page.screenshot(path="solver_state.png")
        
        # --- DECISION MATRIX ---
        
        # 1. METHOD SELECTION
        if "Choose a way" in visible_text or "Authentication app" in visible_text:
            logger.info("STATE DETECTED: Method Selection")
            html_before = await page.content()
            
            option = page.locator('text=/Authentication app/i').first
            if await option.count() > 0:
                logger.info("Clicking 'Authentication app' row...")
                # Click Row (Grandparent)
                await option.locator("../..").evaluate("el => el.click()")
                await asyncio.sleep(1)
                
                # Check change
                if html_before == await page.content():
                    logger.warning("⚠️ NO DOM CHANGE. Retrying Text Click...")
                    await option.evaluate("el => el.click()")
                    await asyncio.sleep(1)
                else:
                    logger.info("✅ DOM Change detected!")

                # Continue
                cont = page.locator('button:has-text("Continue"), button[type="submit"]')
                if await cont.count() > 0:
                    logger.info("Clicking Continue...")
                    await cont.first.evaluate("el => el.click()")
                    
                    try:
                        await page.wait_for_function("() => document.body.innerText.includes('Code') || document.body.innerText.includes('digit')", timeout=8000)
                        logger.info("✅ Verified transition to Code Entry.")
                    except:
                        logger.warning("Timed out waiting for Code Entry.")
            else:
                logger.warning("'Authentication app' not found.")

        # 2. TRAP ("Check notifications")
        elif ("notifications" in visible_text.lower() or "approval" in visible_text.lower()) and "Log in" not in visible_text:
            logger.info("STATE DETECTED: 2FA Notification Trap")
            link = page.locator('text=/Try another way/i').first
            if await link.count() > 0:
                logger.info("Clicking 'Try another way'...")
                await link.evaluate("el => el.click()")
                await asyncio.sleep(3)
            else:
                logger.warning("Link not found.")

        # 3. CODE ENTRY
        elif "Code Generator" in visible_text or "digit code" in visible_text or "approvals_code" in content:
            logger.info("STATE DETECTED: Code Entry")
            code_input = page.locator('input[type="number"], input[name="approvals_code"]')
            if await code_input.count() > 0:
                totp = pyotp.TOTP(creds['secret'].replace(" ", ""))
                code = totp.now()
                logger.info(f"Code: {code}")
                await code_input.fill(code)
                await asyncio.sleep(0.5)
                # Continue
                cont = page.locator('button:has-text("Continue"), button[type="submit"]')
                if await cont.count() > 0:
                    await cont.first.evaluate("el => el.click()")
                else:
                    await page.press('input[name="approvals_code"]', 'Enter')
                await asyncio.sleep(5)

        # 4. SAVE BROWSER
        elif "Save" in visible_text and "browser" in visible_text:
            logger.info("STATE DETECTED: Save Browser")
            btn = page.locator('button:has-text("Save"), button:has-text("OK")')
            if await btn.count() > 0:
                await btn.first.click()
                await asyncio.sleep(5)

        # 5. FEED
        elif "What's on your mind" in visible_text:
            logger.info("STATE DETECTED: Feed")
            logger.info("✅ SUCCESS")

        else:
            logger.warning("Unknown State or Login Page.")

        logger.info("--- END ---")

if __name__ == "__main__":
    asyncio.run(interactive_solve())