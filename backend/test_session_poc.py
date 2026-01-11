#!/usr/bin/env python3
"""
Facebook Session PoC Test Script

This script tests the session persistence approach:
1. Extract cookies from an existing logged-in AdsPower profile
2. Save session to JSON file
3. Test loading session in a fresh Playwright context
4. Verify we're still logged in without re-authentication

Usage:
    python test_session_poc.py extract <profile_name_or_id>  # Extract from AdsPower profile
    python test_session_poc.py test <profile_name>           # Test saved session
    python test_session_poc.py list                          # List saved sessions
    python test_session_poc.py list-profiles                 # List AdsPower profiles
"""

import asyncio
import argparse
import logging
import sys
from playwright.async_api import async_playwright

# Add parent directory to path for imports
sys.path.insert(0, str(__file__).rsplit("/", 1)[0])

from adspower import AdsPowerClient
from fb_session import (
    FacebookSession,
    apply_session_to_context,
    verify_session_logged_in,
    list_saved_sessions,
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("SessionPoC")


async def extract_session(profile_identifier: str) -> bool:
    """
    Extract session from an AdsPower profile.

    Args:
        profile_identifier: Profile name or AdsPower user_id

    Returns:
        True if extraction successful
    """
    adspower = AdsPowerClient()

    # Check AdsPower is running
    if not adspower.check_status():
        logger.error("AdsPower is not running! Please start AdsPower first.")
        return False

    # Get profile list
    profiles = adspower.get_profile_list(page_size=100)
    if not profiles:
        logger.error("No profiles found in AdsPower")
        return False

    # Find the profile
    target_profile = None
    for p in profiles:
        if p.get("user_id") == profile_identifier or p.get("name") == profile_identifier:
            target_profile = p
            break

    # Try partial match
    if not target_profile:
        for p in profiles:
            if profile_identifier.lower() in p.get("name", "").lower():
                target_profile = p
                break

    if not target_profile:
        logger.error(f"Profile '{profile_identifier}' not found")
        logger.info("Available profiles:")
        for p in profiles:
            logger.info(f"  - {p.get('name')} (ID: {p.get('user_id')})")
        return False

    profile_name = target_profile.get("name")
    profile_id = target_profile.get("user_id")
    proxy_info = target_profile.get("remark", "No proxy info")

    logger.info(f"Found profile: {profile_name} (ID: {profile_id})")
    logger.info(f"Proxy info: {proxy_info}")

    # Start the profile
    logger.info("Starting AdsPower profile...")
    try:
        launch_data = adspower.start_profile(profile_id)
        if launch_data.get("mock"):
            logger.error("Got mock data - AdsPower not properly connected")
            return False
        ws_endpoint = launch_data["ws_endpoint"]
        logger.info(f"Profile started, connecting via CDP...")
    except Exception as e:
        logger.error(f"Failed to start profile: {e}")
        return False

    # Connect via Playwright
    try:
        async with async_playwright() as p:
            # Wait a moment for browser to fully initialize
            await asyncio.sleep(3)

            browser = await p.chromium.connect_over_cdp(ws_endpoint)
            logger.info("Connected to browser")

            # Wait for browser to be ready
            await asyncio.sleep(2)

            # Get the default context (AdsPower creates one)
            contexts = browser.contexts
            if not contexts:
                logger.error("No browser contexts found")
                return False

            context = contexts[0]
            pages = context.pages
            if not pages:
                page = await context.new_page()
            else:
                page = pages[0]

            # Wait for page to be ready
            await asyncio.sleep(1)

            # First, check if we already have Facebook cookies (profile might already be on FB)
            logger.info("Checking existing cookies...")
            cookies = await context.cookies()
            cookie_names = [c.get("name") for c in cookies]

            if "c_user" not in cookie_names:
                # No Facebook cookies yet, navigate to Facebook
                logger.info("No FB cookies found, navigating to Facebook...")
                try:
                    await page.goto("https://m.facebook.com/", wait_until="domcontentloaded", timeout=30000)
                    await asyncio.sleep(2)  # Wait for any redirects/cookie updates
                    cookies = await context.cookies()  # Refresh cookies
                    cookie_names = [c.get("name") for c in cookies]
                except Exception as nav_error:
                    logger.warning(f"Navigation failed: {nav_error}")
                    logger.info("Will try to extract cookies from current state...")

            # Check if logged in
            if "c_user" not in cookie_names:
                logger.warning("Profile doesn't appear to be logged in (no c_user cookie)")
                logger.warning("Please log in manually first, then try extraction again")
            else:
                logger.info(f"Found {len(cookies)} cookies including c_user")

            # Create session and extract
            session = FacebookSession(profile_name)
            await session.extract_from_page(page, adspower_id=profile_id, proxy=proxy_info)

            # Save session
            if session.save():
                logger.info("=" * 50)
                logger.info("SESSION EXTRACTED SUCCESSFULLY!")
                logger.info(f"Profile: {profile_name}")
                logger.info(f"User ID: {session.get_user_id()}")
                logger.info(f"Cookies: {len(session.get_cookies())}")
                logger.info(f"User Agent: {session.get_user_agent()[:50]}...")
                logger.info(f"Saved to: {session.session_file}")
                logger.info("=" * 50)
            else:
                logger.error("Failed to save session")
                return False

    except Exception as e:
        logger.error(f"Error during extraction: {e}")
        return False
    finally:
        # Stop the profile
        logger.info("Stopping AdsPower profile...")
        adspower.stop_profile(profile_id)

    return True


async def test_session(profile_name: str, headless: bool = False) -> bool:
    """
    Test a saved session by loading it into a fresh Playwright context.

    Args:
        profile_name: Name of the profile (session file name)
        headless: Run browser in headless mode

    Returns:
        True if session is valid and working
    """
    # Load session
    session = FacebookSession(profile_name)
    if not session.load():
        logger.error(f"Could not load session for '{profile_name}'")
        sessions = list_saved_sessions()
        if sessions:
            logger.info("Available sessions:")
            for s in sessions:
                logger.info(f"  - {s.get('profile_name')} (user: {s.get('user_id')})")
        return False

    if not session.has_valid_cookies():
        logger.error("Session doesn't have valid Facebook cookies")
        return False

    user_agent = session.get_user_agent()
    viewport = session.get_viewport()
    proxy = session.get_proxy()

    logger.info(f"Loaded session for: {profile_name}")
    logger.info(f"User ID: {session.get_user_id()}")
    logger.info(f"User Agent: {user_agent[:50]}...")
    logger.info(f"Viewport: {viewport}")
    logger.info(f"Proxy: {proxy}")

    # Start fresh Playwright (NOT through AdsPower)
    logger.info("=" * 50)
    logger.info("Starting FRESH Playwright browser (not AdsPower)...")
    logger.info("=" * 50)

    async with async_playwright() as p:
        # Configure browser context
        context_options = {
            "user_agent": user_agent,
            "viewport": viewport,
        }

        # Add proxy if available
        if proxy and proxy != "No proxy info" and proxy != "No Proxy Info":
            # Try to parse proxy
            # Format might be: "host:port" or "http://user:pass@host:port"
            if "://" in proxy:
                context_options["proxy"] = {"server": proxy}
            else:
                context_options["proxy"] = {"server": f"http://{proxy}"}
            logger.info(f"Using proxy: {context_options['proxy']}")

        # Launch browser
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(**context_options)

        # Apply cookies
        logger.info("Applying saved cookies...")
        if not await apply_session_to_context(context, session):
            logger.error("Failed to apply cookies")
            await browser.close()
            return False

        # Create page and test
        page = await context.new_page()

        logger.info("Navigating to Facebook...")
        is_logged_in = await verify_session_logged_in(page)

        if is_logged_in:
            logger.info("=" * 50)
            logger.info("SUCCESS! Session is valid!")
            logger.info("You are logged in without re-authentication!")
            logger.info("=" * 50)

            # Show current URL
            logger.info(f"Current URL: {page.url}")

            # Try to get some page info
            try:
                title = await page.title()
                logger.info(f"Page title: {title}")
            except:
                pass

            # Wait so user can see
            if not headless:
                logger.info("Browser will stay open for 30 seconds for inspection...")
                logger.info("Press Ctrl+C to close earlier")
                try:
                    await asyncio.sleep(30)
                except KeyboardInterrupt:
                    pass

        else:
            logger.info("=" * 50)
            logger.error("FAILED! Session is not valid or expired.")
            logger.info("The cookies may have expired or been invalidated.")
            logger.info("=" * 50)

            # Wait for inspection
            if not headless:
                logger.info("Browser will stay open for inspection...")
                try:
                    await asyncio.sleep(30)
                except KeyboardInterrupt:
                    pass

        await browser.close()
        return is_logged_in


def list_profiles_cmd():
    """List available AdsPower profiles."""
    adspower = AdsPowerClient()

    if not adspower.check_status():
        logger.error("AdsPower is not running!")
        return

    profiles = adspower.get_profile_list(page_size=100)
    if not profiles:
        logger.info("No profiles found")
        return

    logger.info(f"Found {len(profiles)} profiles:")
    logger.info("-" * 60)
    for p in profiles:
        name = p.get("name", "Unknown")
        uid = p.get("user_id", "Unknown")
        proxy = p.get("remark", "No proxy")
        logger.info(f"  {name}")
        logger.info(f"    ID: {uid}")
        logger.info(f"    Proxy: {proxy}")
        logger.info("")


def list_sessions_cmd():
    """List saved sessions."""
    sessions = list_saved_sessions()
    if not sessions:
        logger.info("No saved sessions found")
        return

    logger.info(f"Found {len(sessions)} saved sessions:")
    logger.info("-" * 60)
    for s in sessions:
        logger.info(f"  {s.get('profile_name')}")
        logger.info(f"    File: {s.get('file')}")
        logger.info(f"    FB User: {s.get('user_id')}")
        logger.info(f"    Extracted: {s.get('extracted_at')}")
        logger.info(f"    Valid cookies: {s.get('has_valid_cookies')}")
        logger.info("")


def main():
    parser = argparse.ArgumentParser(
        description="Facebook Session PoC Test Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # List AdsPower profiles
    python test_session_poc.py list-profiles

    # Extract session from a profile (must be logged in already)
    python test_session_poc.py extract "FB Android One"

    # List saved sessions
    python test_session_poc.py list

    # Test a saved session
    python test_session_poc.py test "FB Android One"

    # Test in headless mode
    python test_session_poc.py test "FB Android One" --headless
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Extract command
    extract_parser = subparsers.add_parser("extract", help="Extract session from AdsPower profile")
    extract_parser.add_argument("profile", help="Profile name or AdsPower user_id")

    # Test command
    test_parser = subparsers.add_parser("test", help="Test saved session")
    test_parser.add_argument("profile", help="Profile name (from saved sessions)")
    test_parser.add_argument("--headless", action="store_true", help="Run in headless mode")

    # List sessions command
    subparsers.add_parser("list", help="List saved sessions")

    # List profiles command
    subparsers.add_parser("list-profiles", help="List AdsPower profiles")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    if args.command == "extract":
        success = asyncio.run(extract_session(args.profile))
        sys.exit(0 if success else 1)

    elif args.command == "test":
        success = asyncio.run(test_session(args.profile, headless=args.headless))
        sys.exit(0 if success else 1)

    elif args.command == "list":
        list_sessions_cmd()

    elif args.command == "list-profiles":
        list_profiles_cmd()


if __name__ == "__main__":
    main()
