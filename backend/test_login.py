import asyncio
import logging
import os
import sys

# Add current directory to path so we can import modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from playwright.async_api import async_playwright
from automation import login_to_facebook
from credentials import CredentialManager
from adspower import AdsPowerClient

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TestLogin")

async def test_adspower_login():
    # 1. Initialize Clients
    cm = CredentialManager("../accounts info.txt")
    adspower = AdsPowerClient()
    
    # 2. Find the specific profile "FB_Android_7"
    logger.info("Fetching AdsPower profiles...")
    profiles = adspower.get_profile_list(page_size=100)
    
    target_profile = None
    for p in profiles:
        # Loose matching for "FB_Android_7"
        name = p.get("name", "").lower()
        if "android_7" in name or "android 7" in name: 
            target_profile = p
            break
            
    if not target_profile:
        # Fallback to index 7 (Account #7)
        if len(profiles) >= 7:
             logger.warning("Could not find profile named 'FB_Android_7'. Trying the 7th profile in the list...")
             target_profile = profiles[6]
        else:
            logger.error("Could not find target profile 'FB_Android_7'")
            return

    logger.info(f"Targeting Profile: {target_profile['name']} (ID: {target_profile['user_id']})")

    # 3. Get Credentials for Account #7 (UID: 61571383800545)
    target_uid = "61571383800545" 
    creds = cm.get_credential(target_uid)
    
    if not creds:
        logger.error(f"Could not find credentials for UID {target_uid} in text file.")
        return

    logger.info(f"Loaded Credentials for {target_uid}")

    # 4. Launch AdsPower Profile
    logger.info("Launching Browser...")
    launch_data = adspower.start_profile(target_profile['user_id'])
    
    if launch_data.get("mock"):
        logger.error("AdsPower API not detected! Please open AdsPower.")
        return

    ws_endpoint = launch_data["ws_endpoint"]

    # 5. Run Automation
    async with async_playwright() as p:
        try:
            logger.info(f"Connecting to CDP: {ws_endpoint}")
            browser = await p.chromium.connect_over_cdp(ws_endpoint)
            context = browser.contexts[0]
            
            if context.pages:
                page = context.pages[0]
            else:
                page = await context.new_page()

            # Go to m.facebook.com
            logger.info("Navigating to m.facebook.com...")
            await page.goto("https://m.facebook.com/", wait_until="domcontentloaded")
            
            # Run the login logic
            success = await login_to_facebook(page, creds)
            
            if success:
                logger.info("✅ Login Logic PASSED on AdsPower!")
            else:
                logger.error("❌ Login Logic returned False")

        except Exception as e:
            logger.error(f"❌ Test Failed: {e}")
        
        finally:
            # Optional: Stop profile after test? 
            # Usually for debugging we might want to keep it open, but let's be clean.
            # adspower.stop_profile(target_profile['user_id'])
            pass

if __name__ == "__main__":
    asyncio.run(test_adspower_login())
