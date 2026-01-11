import asyncio
import logging
import os
import sys
from playwright.async_api import async_playwright

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from adspower import AdsPowerClient
from credentials import CredentialManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Inspector")

async def inspect_page():
    # 1. Setup
    adspower = AdsPowerClient()
    cm = CredentialManager("../accounts info.txt")
    
    # Target Profile 7
    target_uid = "61571383800545" 
    creds = cm.get_credential(target_uid)
    
    # Find profile
    profiles = adspower.get_profile_list(page_size=100)
    target_profile = next((p for p in profiles if "android_7" in p.get("name", "").lower() or "android 7" in p.get("name", "").lower()), None)
    
    if not target_profile:
        target_profile = profiles[6]
        
    logger.info(f"Inspecting Profile: {target_profile['name']}")

    # 2. Launch
    launch_data = adspower.start_profile(target_profile['user_id'])
    ws_endpoint = launch_data["ws_endpoint"]
    
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(ws_endpoint)
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()

        # 3. Navigate & Login
        logger.info("Navigating to m.facebook.com...")
        await page.goto("https://m.facebook.com/", wait_until="networkidle")
        
        # Fill Creds
        try:
            await page.locator('input[name="email"]').evaluate(f"el => el.value = '{creds['uid']}'")
            await page.locator('input[name="email"]').evaluate("el => el.dispatchEvent(new Event('input', { bubbles: true }))")
            
            await page.locator('input[name="pass"]').evaluate(f"el => el.value = '{creds['password']}'")
            await page.locator('input[name="pass"]').evaluate("el => el.dispatchEvent(new Event('input', { bubbles: true }))")
        except Exception as e:
            logger.error(f"Failed to fill creds: {e}")

        # Click Login
        logger.info("Clicking Login...")
        try:
            target = page.get_by_text("Log in", exact=True).first
            await target.evaluate("el => el.click()")
            await target.locator("..").evaluate("el => el.click()")
            await target.locator("../..").evaluate("el => el.click()")
        except Exception as e:
             logger.warning(f"JS Click failed: {e}")

        logger.info("Waiting 10s for trap page...")
        await asyncio.sleep(10)
        
        # 4. CAPTURE TRAP PAGE
        logger.info("📸 Capturing Trap Page...")
        
        await page.screenshot(path="debug_trap.png")
        
        # Dump ALL text
        text = await page.evaluate("document.body.innerText")
        logger.info("--- PAGE TEXT DUMP ---")
        logger.info(text)
        logger.info("----------------------")


if __name__ == "__main__":
    asyncio.run(inspect_page())