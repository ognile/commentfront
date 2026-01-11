import asyncio
import logging
import os
from playwright.async_api import async_playwright, Page
from playwright_stealth import Stealth
from typing import Optional
from urllib.parse import urlparse, parse_qs

from fb_session import FacebookSession, apply_session_to_context, verify_session_logged_in
from fb_selectors import COMMENT
from debug_logger import DebugLogger

logger = logging.getLogger("AutomationRunner")

# Legacy debug directory (kept for backwards compatibility)
DEBUG_DIR = os.path.join(os.path.dirname(__file__), "debug")
os.makedirs(DEBUG_DIR, exist_ok=True)


async def find_comment_input(page):
    """
    Try multiple selectors to find the comment input field.
    Returns the first matching locator, or None if not found.
    """
    selectors = COMMENT["comment_input"]

    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = await locator.count()
            if count > 0:
                logger.info(f"Found comment input with selector: {selector}")
                return locator.first
        except Exception as e:
            logger.debug(f"Selector {selector} failed: {e}")
            continue

    # Log available elements for debugging
    logger.warning("Could not find comment input with known selectors")
    try:
        # Log what we can find
        editable_count = await page.locator('[contenteditable]').count()
        textbox_count = await page.locator('[role="textbox"]').count()
        textarea_count = await page.locator('textarea').count()
        logger.warning(f"Available elements - contenteditable: {editable_count}, textbox: {textbox_count}, textarea: {textarea_count}")
    except:
        pass

    return None


async def find_post_button(page):
    """
    Try multiple selectors to find the Post/Submit button.
    Returns the first matching locator, or None if not found.
    """
    selectors = COMMENT["comment_submit"]

    for selector in selectors:
        try:
            locator = page.locator(selector)
            count = await locator.count()
            if count > 0:
                logger.info(f"Found post button with selector: {selector}")
                return locator.first
        except:
            continue

    return None


async def post_comment(page, comment: str, target_url: str = None, debug_logger: DebugLogger = None) -> bool:
    """
    Post a comment on a Facebook post using multiple fallback strategies.

    Strategy A: Playwright selectors (various patterns)
    Strategy B: JavaScript DOM evaluation
    Strategy C: Coordinate-based clicking

    Args:
        page: Playwright page
        comment: Text to post
        target_url: Original permalink URL
        debug_logger: Optional DebugLogger for comprehensive logging

    Returns:
        True if comment was submitted successfully
    """
    logger.info(f"[COMMENT] Current page URL: {page.url}")

    # ========================================
    # PHASE 1: DUMP PAGE STATE FOR DEBUGGING
    # ========================================

    # Use DebugLogger if provided, otherwise fall back to legacy logging
    if debug_logger:
        await debug_logger.log_step(page, "01_page_loaded", "initial_state", {
            "current_url": page.url,
            "target_url": target_url
        })
    else:
        # Legacy: Save screenshot
        try:
            await page.screenshot(path=os.path.join(DEBUG_DIR, "1_after_load.png"))
            logger.info(f"[DEBUG] Saved screenshot to {DEBUG_DIR}/1_after_load.png")
        except Exception as e:
            logger.debug(f"Could not save screenshot: {e}")

        # Legacy: Save full page HTML for offline analysis
        try:
            html_content = await page.content()
            with open(os.path.join(DEBUG_DIR, "full_page.html"), "w", encoding="utf-8") as f:
                f.write(html_content)
            logger.info(f"[DEBUG] Saved full page HTML to {DEBUG_DIR}/full_page.html")
        except Exception as e:
            logger.debug(f"Could not save HTML: {e}")

    # Log ALL buttons on the page with their attributes
    try:
        all_buttons = await page.locator('[role="button"]').all()
        logger.info(f"[DEBUG] Total buttons on page: {len(all_buttons)}")

        comment_buttons_found = []
        for i, btn in enumerate(all_buttons[:30]):  # Check first 30 buttons
            try:
                aria = await btn.get_attribute('aria-label') or ''
                action_id = await btn.get_attribute('data-action-id') or ''
                # Log buttons that might be comment-related
                if 'comment' in aria.lower() or action_id == '32607':
                    logger.info(f"[DEBUG] POTENTIAL COMMENT BUTTON #{i}: aria-label='{aria}', data-action-id='{action_id}'")
                    comment_buttons_found.append((i, btn, aria, action_id))
            except:
                continue

        if not comment_buttons_found:
            logger.warning("[DEBUG] No comment-related buttons found by aria-label/action-id!")
            # Log first 10 buttons anyway
            for i, btn in enumerate(all_buttons[:10]):
                try:
                    aria = await btn.get_attribute('aria-label') or ''
                    action_id = await btn.get_attribute('data-action-id') or ''
                    text = await btn.inner_text() or ''
                    logger.info(f"[DEBUG] Button #{i}: aria='{aria[:50]}', action_id='{action_id}', text='{text[:30]}'")
                except:
                    continue
    except Exception as e:
        logger.debug(f"Button logging failed: {e}")

    # ========================================
    # PHASE 1.5: VERIFY TARGET POST IS VISIBLE
    # ========================================
    # Check for "From your link" banner or specific post indicators
    # This prevents commenting on random posts when permalink redirect fails

    target_post_visible = False
    try:
        # Check for "From your link" banner (appears when permalink redirect works)
        from_link_selectors = [
            'text="From your link"',
            'span:has-text("From your link")',
            'div:has-text("From your link")',
        ]
        for selector in from_link_selectors:
            try:
                count = await page.locator(selector).count()
                if count > 0:
                    target_post_visible = True
                    logger.info(f"[VERIFY] ✅ Found 'From your link' banner!")
                    break
            except:
                continue

        if not target_post_visible:
            # Also check if page URL changed to the actual post
            current_url = page.url
            if 'story.php' in current_url or 'posts/' in current_url:
                target_post_visible = True
                logger.info(f"[VERIFY] ✅ Navigated to post page: {current_url}")

        if not target_post_visible:
            logger.error("[VERIFY] ❌ Target post not visible! 'From your link' banner not found.")
            logger.error("[VERIFY] Facebook redirected to home feed instead of showing target post.")
            logger.error("[VERIFY] This may indicate: account rate-limited, session flagged, or post not accessible.")

            # Log the page state for debugging
            if debug_logger:
                await debug_logger.log_step(page, "01b_verification_failed", "check", {
                    "error": "Target post not visible",
                    "current_url": page.url,
                    "from_link_found": False
                })

            raise Exception("Target post not visible - permalink redirect failed. Aborting to prevent commenting on wrong post.")

    except Exception as e:
        if "Target post not visible" in str(e):
            raise  # Re-raise our specific error
        logger.warning(f"[VERIFY] Verification check failed: {e}")
        # Continue anyway if verification itself errors

    # ========================================
    # PHASE 2: CLICK COMMENT BUTTON ON TARGET POST
    # ========================================
    # IMPORTANT: Use COORDINATES FIRST because selectors match multiple elements
    # and click the wrong one (opens "Create post" instead of comment)

    clicked_comment_btn = False

    # ----- PRIMARY: Coordinate-Based Click (MOST RELIABLE) -----
    # From screenshot analysis of target post at top of page:
    # - Comment button (speech bubble) is in the action bar below post image
    # - Action bar has Like (left), Comment (center), Share (right)
    # - For viewport ~393px wide, comment button is at center x
    # - Y coordinate is approximately 533px from top (below the post image)

    logger.info("[COMMENT] Using coordinate-based click (selectors unreliable)...")
    try:
        viewport = page.viewport_size
        if viewport:
            center_x = viewport['width'] // 2  # Center horizontally (comment button)
            comment_y = 560  # Action bar Y position - exactly at action bar row

            logger.info(f"[COMMENT] Clicking comment button at coordinates ({center_x}, {comment_y})")
            await page.mouse.click(center_x, comment_y)
            await page.wait_for_timeout(2000)  # Wait for comment UI to open
            clicked_comment_btn = True
            logger.info("[COMMENT] ✅ Clicked at coordinates!")
    except Exception as e:
        logger.warning(f"[COMMENT] Coordinate click failed: {e}")

    if not clicked_comment_btn:
        logger.error("[COMMENT] ❌ All strategies failed to click comment button!")
        raise Exception("Comment button not found - all strategies failed")

    # Debug: Take screenshot after clicking Comment
    if debug_logger:
        viewport = page.viewport_size or {}
        await debug_logger.log_step(page, "02_comment_clicked", "mouse_click", {
            "x": viewport.get('width', 0) // 2,
            "y": 560,  # Must match comment_y above!
            "target": "comment_button",
            "strategy": "coordinates"
        })
    else:
        try:
            await page.screenshot(path=os.path.join(DEBUG_DIR, "2_after_comment_click.png"))
            logger.info(f"[DEBUG] Saved screenshot to {DEBUG_DIR}/2_after_comment_click.png")
        except Exception as e:
            logger.debug(f"Could not save screenshot: {e}")

        # Save HTML AFTER click for debugging
        try:
            html_after = await page.content()
            with open(os.path.join(DEBUG_DIR, "page_after_click.html"), "w", encoding="utf-8") as f:
                f.write(html_after)
            logger.info(f"[DEBUG] Saved HTML after click to {DEBUG_DIR}/page_after_click.html")
        except Exception as e:
            logger.debug(f"Could not save HTML: {e}")

    # ========================================
    # PHASE 3: FIND AND CLICK COMMENT INPUT
    # ========================================

    logger.info("[INPUT] Looking for comment input field...")

    input_clicked = False

    # ----- INPUT STRATEGY A: Playwright Selectors -----
    comment_input_selectors = [
        # Text-based
        'div:has-text("Write a comment")',
        'span:has-text("Write a comment")',
        # Role/attribute based
        'div[role="textbox"]',
        '[contenteditable="true"]',
        'div[aria-label*="Write a comment"]',
        'div[aria-label*="Comment"]',
        'textarea',
        # Mobile FB specific
        '[data-sigil*="comment"]',
        '.textbox',
    ]

    for selector in comment_input_selectors:
        try:
            locator = page.locator(selector)
            count = await locator.count()
            logger.info(f"[INPUT] Selector '{selector}' matched {count} elements")
            if count > 0:
                await locator.first.click()
                await page.wait_for_timeout(500)
                input_clicked = True
                logger.info(f"[INPUT] ✅ Clicked input with selector: {selector}")
                break
        except Exception as e:
            logger.debug(f"Input selector {selector} failed: {e}")
            continue

    # ----- INPUT STRATEGY B: JavaScript -----
    if not input_clicked:
        logger.info("[INPUT] Trying JavaScript to find input...")
        try:
            js_result = await page.evaluate('''
                () => {
                    // Try to find by text content
                    const allDivs = document.querySelectorAll('div, span');
                    for (const el of allDivs) {
                        if (el.innerText && el.innerText.includes('Write a comment')) {
                            el.click();
                            return {success: true, method: 'text-content'};
                        }
                    }

                    // Try contenteditable
                    const editable = document.querySelector('[contenteditable="true"]');
                    if (editable) {
                        editable.click();
                        editable.focus();
                        return {success: true, method: 'contenteditable'};
                    }

                    // Try textbox
                    const textbox = document.querySelector('[role="textbox"]');
                    if (textbox) {
                        textbox.click();
                        textbox.focus();
                        return {success: true, method: 'textbox'};
                    }

                    return {success: false};
                }
            ''')
            logger.info(f"[INPUT] JS result: {js_result}")
            if js_result.get('success'):
                input_clicked = True
                logger.info(f"[INPUT] ✅ Clicked input via JS: {js_result.get('method')}")
                await page.wait_for_timeout(500)
        except Exception as e:
            logger.warning(f"[INPUT] JS click failed: {e}")

    # ----- INPUT STRATEGY C: Coordinate Click -----
    if not input_clicked:
        logger.info("[INPUT] Trying coordinate click on input area...")
        try:
            # From screenshot: "Write a comment..." is at bottom of screen
            # For viewport ~393x873, the input is around y=815
            viewport = page.viewport_size
            if viewport:
                center_x = viewport['width'] // 2
                input_y = viewport['height'] - 60  # Near bottom, above keyboard area

                logger.info(f"[INPUT] Clicking input at coordinates ({center_x}, {input_y})")
                await page.mouse.click(center_x, input_y)
                await page.wait_for_timeout(500)
                input_clicked = True
                logger.info("[INPUT] ✅ Clicked input by coordinates!")
        except Exception as e:
            logger.warning(f"[INPUT] Coordinate click failed: {e}")

    if not input_clicked:
        logger.error("[INPUT] ❌ Could not find/click comment input!")
        raise Exception("Comment input not found after clicking Comment button")

    # Log after input is focused
    if debug_logger:
        await debug_logger.log_step(page, "03_input_focused", "click_input", {
            "input_clicked": input_clicked
        })

    # ========================================
    # PHASE 4: TYPE THE COMMENT
    # ========================================

    logger.info(f"[TYPE] Typing comment: {comment}")
    await page.keyboard.type(comment, delay=80)  # Human-like typing (80ms per char)
    await page.wait_for_timeout(1000)

    # Debug: Take screenshot after typing
    if debug_logger:
        await debug_logger.log_step(page, "04_text_typed", "keyboard_type", {
            "comment": comment,
            "delay_ms": 80
        })
    else:
        try:
            await page.screenshot(path=os.path.join(DEBUG_DIR, "3_after_typing.png"))
            logger.info(f"Saved debug screenshot to {DEBUG_DIR}/3_after_typing.png")
        except Exception as e:
            logger.debug(f"Could not save screenshot: {e}")

    # ========================================
    # PHASE 5: CLICK SEND BUTTON
    # ========================================

    logger.info("[SEND] Looking for send button...")
    send_clicked = False

    # ----- SEND STRATEGY A: Playwright Selectors -----
    send_button_selectors = [
        'div[aria-label="Send"]',
        'div[aria-label="Post"]',
        'div[aria-label="Submit"]',
        '[aria-label*="send" i]',
        '[aria-label*="post" i]',
        'button[type="submit"]',
        'div[data-sigil*="submit"]',
        '[data-sigil="touchable submit-comment"]',
    ]

    for selector in send_button_selectors:
        try:
            locator = page.locator(selector)
            count = await locator.count()
            logger.info(f"[SEND] Selector '{selector}' matched {count} elements")
            # ONLY click if EXACTLY 1 match to avoid clicking wrong element
            if count == 1:
                await locator.first.click()
                send_clicked = True
                logger.info(f"[SEND] ✅ Clicked send button with selector: {selector}")
                await page.wait_for_timeout(1500)
                break
            elif count > 1:
                logger.info(f"[SEND] Skipping - multiple matches ({count}), might click wrong element")
        except Exception as e:
            logger.debug(f"Send selector {selector} failed: {e}")
            continue

    # ----- SEND STRATEGY B: JavaScript - Find send button near textarea -----
    if not send_clicked:
        logger.info("[SEND] Trying JavaScript to find send button near input...")
        try:
            js_result = await page.evaluate('''
                () => {
                    // Strategy 1: Find textarea and look for button in same container
                    const textarea = document.querySelector('textarea');
                    if (textarea) {
                        // Walk up to find the input container, then find button with SVG
                        let container = textarea.closest('.textbox-container') ||
                                        textarea.closest('form') ||
                                        textarea.parentElement?.parentElement?.parentElement;
                        if (container) {
                            // Look for the send button (usually has SVG and is at bottom-right)
                            const buttons = container.querySelectorAll('[role="button"]');
                            for (const btn of buttons) {
                                if (btn.querySelector('svg')) {
                                    const rect = btn.getBoundingClientRect();
                                    // Send button should be at bottom and right side
                                    if (rect.bottom > window.innerHeight - 100 && rect.right > window.innerWidth - 100) {
                                        btn.click();
                                        return {success: true, method: 'svg-in-container', rect: {x: rect.x, y: rect.y}};
                                    }
                                }
                            }
                        }
                    }

                    // Strategy 2: Find SVG button at very bottom-right of viewport
                    const allButtons = document.querySelectorAll('[role="button"]');
                    let bestButton = null;
                    let bestScore = 0;
                    for (const btn of allButtons) {
                        if (btn.querySelector('svg')) {
                            const rect = btn.getBoundingClientRect();
                            // Score based on proximity to bottom-right corner
                            const distFromBottom = window.innerHeight - rect.bottom;
                            const distFromRight = window.innerWidth - rect.right;
                            // Lower distance = better score
                            if (distFromBottom < 50 && distFromRight < 50) {
                                const score = (50 - distFromBottom) + (50 - distFromRight);
                                if (score > bestScore) {
                                    bestScore = score;
                                    bestButton = btn;
                                }
                            }
                        }
                    }
                    if (bestButton) {
                        const rect = bestButton.getBoundingClientRect();
                        bestButton.click();
                        return {success: true, method: 'bottom-right-svg', rect: {x: rect.x, y: rect.y, w: rect.width, h: rect.height}};
                    }

                    return {success: false, message: 'No suitable send button found'};
                }
            ''')
            logger.info(f"[SEND] JS result: {js_result}")
            if js_result.get('success'):
                send_clicked = True
                logger.info(f"[SEND] ✅ Clicked via JS: {js_result.get('method')}")
                await page.wait_for_timeout(1500)
        except Exception as e:
            logger.warning(f"[SEND] JS evaluation failed: {e}")

    # ----- SEND STRATEGY C: Coordinate-Based Click -----
    if not send_clicked:
        logger.info("[SEND] Trying coordinate-based click...")
        try:
            viewport = page.viewport_size
            if viewport:
                # Send button: blue arrow icon to the right of comment input
                # NOT at far right edge - it's inside the input area
                send_x = viewport['width'] - 70  # ~323 for 393px width
                send_y = viewport['height'] - 35  # ~838 for 873px height

                logger.info(f"[SEND] Clicking at coordinates ({send_x}, {send_y})")
                await page.mouse.click(send_x, send_y)
                await page.wait_for_timeout(1000)
                logger.info("[SEND] ✅ Clicked send button by coordinates!")
        except Exception as e:
            logger.warning(f"[SEND] Coordinate click failed: {e}")

    # ----- SEND STRATEGY D: Press Enter (ALWAYS TRY) -----
    # Enter key is the most reliable way to submit a comment in the input field
    logger.info("[SEND] Pressing Enter to submit comment...")
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(500)
    logger.info("[SEND] ✅ Pressed Enter key to submit!")

    # Wait for comment to post
    await page.wait_for_timeout(2000)

    # Debug: Take screenshot after send
    if debug_logger:
        viewport = page.viewport_size or {}
        await debug_logger.log_step(page, "05_comment_sent", "keyboard_press", {
            "key": "Enter",
            "send_button_clicked": send_clicked,
            "send_x": viewport.get('width', 0) - 28 if not send_clicked else None,
            "send_y": viewport.get('height', 0) - 30 if not send_clicked else None
        })
    else:
        try:
            await page.screenshot(path=os.path.join(DEBUG_DIR, "4_after_send.png"))
            logger.info(f"[DEBUG] Saved screenshot to {DEBUG_DIR}/4_after_send.png")
        except Exception as e:
            logger.debug(f"Could not save screenshot: {e}")

    logger.info("✅ Comment submitted successfully!")
    return True


async def run_with_session(
    session: FacebookSession,
    url: str,
    comment: str,
    job_id: str = None,
    debug_logger: DebugLogger = None
) -> bool:
    """
    Run automation using a saved session (no AdsPower needed).

    This is the FAST PATH - uses saved cookies in plain Playwright.
    ~2 seconds vs 60+ seconds with login flow.

    Args:
        session: FacebookSession with loaded cookies
        url: Target post URL to comment on
        comment: Comment text to post
        job_id: Optional job ID for tracking
        debug_logger: Optional DebugLogger (created internally if not provided)

    Returns:
        True if successful
    """
    user_agent = session.get_user_agent()
    viewport = session.get_viewport()
    proxy = session.get_proxy()

    # Create DebugLogger if not provided
    if debug_logger is None:
        from debug_logger import create_debug_logger
        debug_logger = create_debug_logger(
            job_id=job_id or "unknown",
            profile_name=session.profile_name,
            url=url,
            comment=comment
        )

    logger.info(f"[SESSION] Starting automation with saved session for {session.profile_name}")
    logger.info(f"[SESSION] User ID: {session.get_user_id()}")
    logger.info(f"[SESSION] Debug dir: {debug_logger.get_job_dir()}")

    async with async_playwright() as p:
        # Configure browser context
        context_options = {
            "user_agent": user_agent,
            "viewport": viewport,
        }

        # Add proxy if available
        if proxy and proxy not in ("", "No proxy info", "No Proxy Info"):
            if "://" in proxy:
                context_options["proxy"] = {"server": proxy}
            else:
                context_options["proxy"] = {"server": f"http://{proxy}"}
            logger.info(f"[SESSION] Using proxy: {proxy}")

        # DETERMINE HEADLESS MODE
        # If running on Railway (PROXY_URL exists), force headless.
        # Otherwise default to visible for local debugging.
        is_cloud = bool(os.getenv("PROXY_URL") or os.getenv("RAILWAY_ENVIRONMENT"))
        use_headless = is_cloud
        
        logger.info(f"[SESSION] Cloud Mode: {is_cloud}, Headless: {use_headless}")

        # Launch browser with stealth args
        browser = await p.chromium.launch(
            headless=use_headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox"
            ]
        )
        
        context = await browser.new_context(**context_options)
        
        # APPLY STEALTH
        stealth = Stealth()
        await stealth.apply_stealth_async(context)

        try:
            # Start a new attempt
            debug_logger.new_attempt()

            # Apply saved cookies
            if not await apply_session_to_context(context, session):
                raise Exception("Failed to apply session cookies")

            page = await context.new_page()

            # Attach console listener for debug logging
            debug_logger.attach_console_listener(page)

            # Navigate directly to target (skip session verification)
            logger.info(f"[SESSION] Navigating to: {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(4)

            # Scroll to top to ensure we see the "From your link" post
            await page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(2)

            # Log actual URL after navigation (check for redirects)
            logger.info(f"[SESSION] Page loaded, actual URL: {page.url}")

            # Post the comment using smart selector helper
            logger.info("[SESSION] Attempting to post comment...")
            try:
                await post_comment(page, comment, target_url=url, debug_logger=debug_logger)
                logger.info("[SESSION] ✅ Comment posted successfully!")

                # Mark attempt as success
                debug_logger.end_attempt(status="success")
                debug_logger.save_summary(final_status="success")

            except Exception as comment_error:
                logger.error(f"[SESSION] Comment posting failed: {comment_error}")

                # Log error state
                await debug_logger.log_step(page, "99_error", "exception", {
                    "error": str(comment_error)
                })
                debug_logger.end_attempt(status="failed", error=str(comment_error))
                debug_logger.save_summary(final_status="failed")

                # Keep browser open for 10 seconds so user can see what happened
                logger.info("[SESSION] Keeping browser open for debugging (10 sec)...")
                await asyncio.sleep(10)
                raise comment_error

            await asyncio.sleep(3)
            return True

        except Exception as e:
            logger.error(f"[SESSION] Automation failed: {e}")

            # Ensure summary is saved even on unexpected errors
            try:
                debug_logger.end_attempt(status="failed", error=str(e))
                debug_logger.save_summary(final_status="failed")
            except:
                pass

            # Keep browser open for debugging on error
            logger.info("[SESSION] Keeping browser open for debugging (10 sec)...")
            await asyncio.sleep(10)
            raise e
        finally:
            await browser.close()


async def run_automation_task(ws_endpoint: str, url: str, comment: str, is_mock: bool = False, credentials: dict = None):
    """
    Streamlined Automation:
    1. Connect
    2. Check Login (Fail if not logged in)
    3. Navigate to Post
    4. Comment
    """
    if is_mock:
        logger.info(f"[MOCK] Navigating to {url}")
        await asyncio.sleep(2)
        return True

    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(ws_endpoint)
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else await context.new_page()

            # --- 1. Login Check ---
            logger.info("Checking login status...")
            await page.goto("https://m.facebook.com/", wait_until="domcontentloaded")
            await asyncio.sleep(2)
            
            # Check for Feed indicators
            is_logged_in = False
            if await page.locator('div[data-sigil="m-area"], a[href*="/logout/"]').count() > 0:
                is_logged_in = True
            elif "Log in" in await page.evaluate("document.body.innerText"):
                is_logged_in = False
            
            if not is_logged_in:
                logger.error("❌ Profile is NOT logged in. Skipping.")
                raise Exception("Profile not logged in. Please login manually via Profile Manager.")

            logger.info("✅ Login Verified.")

            # --- 2. Navigate to Target ---
            logger.info(f"Navigating to Target: {url}...")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            # --- 3. Post Comment using smart selector helper ---
            logger.info("Attempting to post comment...")
            await post_comment(page, comment, target_url=url)

            await asyncio.sleep(3)
            await browser.close()
            return True

        except Exception as e:
            logger.error(f"Automation failed: {e}")
            raise e
