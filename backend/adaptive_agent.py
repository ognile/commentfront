"""
Adaptive Agent Module - DOM-Based Agent for Facebook Automation

Gemini decides WHAT to click, Playwright finds WHERE from DOM.
No coordinate hallucination - uses DOM element matching instead.
"""

import asyncio
import logging
import os
import re
from typing import Dict, List, Optional, Any

from playwright.async_api import async_playwright, Page, BrowserContext
from playwright_stealth import Stealth
from google.genai import types

from fb_session import FacebookSession, apply_session_to_context
from gemini_vision import get_vision_client, set_observation_context
from comment_bot import save_debug_screenshot, dump_interactive_elements, _build_playwright_proxy

logger = logging.getLogger(__name__)

# Mobile viewport dimensions
MOBILE_VIEWPORT = {"width": 393, "height": 873}


def is_element_visible(el: dict, viewport_height: int = 873) -> bool:
    """Check if element is within visible viewport."""
    bounds = el.get('bounds', {})
    if not bounds:
        return False
    y = bounds.get('y', -1)
    h = bounds.get('h', 0)
    # Element must be at least partially visible (y > 0 and top edge below viewport)
    # Allow elements that are partially visible at top (y > -h/2)
    return y > -(h // 2) and y < viewport_height


async def find_element_by_description(description: str, elements: list, log_prefix: str = "[ADAPTIVE]") -> Optional[dict]:
    """Match Gemini's description to a DOM element. Only matches VISIBLE elements."""
    desc_lower = description.lower()

    # Filter to only visible elements first
    visible_elements = [el for el in elements if is_element_visible(el)]
    logger.info(f"{log_prefix} Matching '{description}' against {len(visible_elements)} visible elements (filtered from {len(elements)} total)")

    # Check if description is an icon character (non-ASCII single char or short string with special chars)
    is_icon_search = len(description) <= 3 and any(ord(c) > 127 for c in description)
    if is_icon_search:
        logger.info(f"{log_prefix} Icon search detected: '{description}'")

    # Priority 0: For icon searches, find element with EXACTLY that icon as text (back button, etc.)
    if is_icon_search:
        for el in visible_elements:
            el_text = el.get('text', '').strip()
            # Match if the element's text IS the icon (for single-icon buttons like back arrow)
            if el_text == description:
                logger.info(f"{log_prefix} Matched icon by exact text: '{el_text}' aria-label={el.get('ariaLabel', '')}")
                return el

    # Priority 1: Exact aria-label match
    for el in visible_elements:
        if el.get('ariaLabel', '').lower() == desc_lower:
            logger.info(f"{log_prefix} Matched by exact aria-label: {el.get('ariaLabel')}")
            return el

    # Priority 2: Partial aria-label match
    for el in visible_elements:
        if desc_lower in el.get('ariaLabel', '').lower():
            logger.info(f"{log_prefix} Matched by partial aria-label (desc in aria): {el.get('ariaLabel')}")
            return el
        if el.get('ariaLabel', '').lower() in desc_lower:
            logger.info(f"{log_prefix} Matched by partial aria-label (aria in desc): {el.get('ariaLabel')}")
            return el

    # Priority 3: Text content match (but skip very short text matches for longer descriptions)
    for el in visible_elements:
        el_text = el.get('text', '').lower()
        # For icon searches, require exact match (already handled above)
        if is_icon_search:
            continue
        if desc_lower in el_text:
            logger.info(f"{log_prefix} Matched by text content (desc in text): {el.get('text', '')[:30]}")
            return el
        # Only match if element text is meaningful (not just a short word in a longer description)
        if len(el_text) > 2 and el_text in desc_lower:
            logger.info(f"{log_prefix} Matched by text content (text in desc): {el.get('text', '')[:30]}")
            return el

    # Priority 4: Role-based matching for common elements
    if 'back' in desc_lower or 'close' in desc_lower or 'dismiss' in desc_lower:
        for el in visible_elements:
            aria = el.get('ariaLabel', '').lower()
            if 'back' in aria or 'close' in aria or aria == 'x':
                logger.info(f"{log_prefix} Matched back/close button: {el.get('ariaLabel')}")
                return el

    if 'comment' in desc_lower:
        for el in visible_elements:
            aria = el.get('ariaLabel', '').lower()
            if 'comment' in aria:
                logger.info(f"{log_prefix} Matched comment element: {el.get('ariaLabel')}")
                return el

    if 'like' in desc_lower:
        for el in visible_elements:
            aria = el.get('ariaLabel', '').lower()
            if 'like' in aria and 'unlike' not in aria:
                logger.info(f"{log_prefix} Matched like element: {el.get('ariaLabel')}")
                return el

    if 'see why' in desc_lower:
        for el in visible_elements:
            if 'see why' in el.get('text', '').lower():
                logger.info(f"{log_prefix} Matched 'see why' element")
                return el

    # Priority 5: For icons, try to find element that STARTS with the icon
    if is_icon_search:
        for el in visible_elements:
            el_text = el.get('text', '').strip()
            if el_text.startswith(description):
                logger.info(f"{log_prefix} Matched icon by text prefix: '{el_text[:20]}' aria-label={el.get('ariaLabel', '')}")
                return el

    logger.warning(f"{log_prefix} No visible element matched for: {description}")
    return None


class AdaptiveAgent:
    """
    DOM-Based Adaptive Agent for Facebook automation.

    Gemini decides WHAT to do based on screenshots,
    Playwright finds WHERE to click from DOM elements.
    """

    def __init__(
        self,
        profile_name: str,
        task: str,
        max_steps: int = 15,
        start_url: str = "https://m.facebook.com"
    ):
        self.profile_name = profile_name
        self.task = task
        self.max_steps = max_steps
        self.start_url = start_url
        self.log_prefix = f"[ADAPTIVE:{profile_name}]"

        self.results: Dict[str, Any] = {
            "profile_name": profile_name,
            "task": task,
            "steps": [],
            "screenshots": [],
            "errors": [],
            "final_status": "unknown"
        }

        self.session: Optional[FacebookSession] = None
        self.vision = None
        self.page: Optional[Page] = None
        self.action_history: List[Dict] = []

    def _build_prompt(self, step_num: int, elements_summary: List[str]) -> str:
        """Build the Gemini prompt for decision making."""
        # Build action history summary for loop detection
        recent_actions = self.action_history[-5:] if self.action_history else []
        history_text = "\n".join([f"  Step {a['step']}: {a['action']}" for a in recent_actions]) if recent_actions else "  (none yet)"

        # Detect repeated actions
        loop_warning = ""
        if len(self.action_history) >= 3:
            last_3 = [a['action'] for a in self.action_history[-3:]]
            if len(set(last_3)) == 1:  # All 3 are the same
                loop_warning = f"\n\u26a0\ufe0f WARNING: You have repeated '{last_3[0]}' 3 times with no progress. TRY SOMETHING DIFFERENT!"

        return f"""You are an AI agent controlling a Facebook mobile browser.

TASK: {self.task}

CURRENT STEP: {step_num} of {self.max_steps}

RECENT ACTION HISTORY:
{history_text}{loop_warning}

INTERACTIVE ELEMENTS ON PAGE (from DOM):
{chr(10).join(elements_summary) if elements_summary else "No elements found"}

Analyze the screenshot and DOM elements. Decide the NEXT ACTION.

AVAILABLE ACTIONS:
1. SCROLL direction=<up|down> - Scroll to see more content
2. CLICK element="<description>" - Click an element (describe it by text/label, NOT coordinates)
3. TYPE text="<text to type>" - Type text into active input field
4. WAIT reason="<why>" - Wait for content to load
5. DONE reason="<why task is complete>" - Task completed
6. FAILED reason="<why task cannot be completed>" - Task failed

IMPORTANT RULES:
- For CLICK: describe the element by its TEXT or LABEL, not coordinates
- Example: CLICK element="Comment" or CLICK element="Back" or CLICK element="See why"
- If you see a "We removed your comment" notification, click "Back" or the back arrow to dismiss
- To comment on a post, first CLICK the post or its Comment button, then TYPE your comment
- Make comments contextual to the post content (not generic)
- CRITICAL: If an action didn't change the page, DO NOT repeat it! Try a DIFFERENT action.
- If a button doesn't work after clicking, try scrolling or clicking other elements
- If stuck in a loop, use FAILED to report that the task cannot be completed

RESPONSE FORMAT:
ACTION: <action_type> <parameters>
REASONING: <brief explanation>

Examples:
ACTION: SCROLL direction=down
REASONING: Looking for an interesting post to comment on

ACTION: CLICK element="Comment"
REASONING: Opening comments on the NFL post to write a comment

ACTION: CLICK element="Back"
REASONING: Dismissing the notification to return to feed

ACTION: TYPE text="What a great play! The defense really stepped up."
REASONING: Writing a contextual comment about the football post

ACTION: DONE reason="Successfully commented on a post"
REASONING: Comment was submitted"""

    async def _click_element(self, target_el: dict, element_desc: str) -> str:
        """Click an element using multiple strategies. Returns description of how it was clicked."""
        aria_label = target_el.get('ariaLabel', '')
        clicked_via = None

        # Try native Playwright tap/click (mobile mode enabled)
        if aria_label:
            try:
                locator = self.page.locator(f'[aria-label="{aria_label}"]').first
                if await locator.count() > 0:
                    # Scroll into view first
                    await locator.scroll_into_view_if_needed()
                    await asyncio.sleep(0.3)

                    # Get bounding box for logging
                    bbox = await locator.bounding_box()
                    if bbox:
                        center_x = bbox['x'] + bbox['width'] / 2
                        center_y = bbox['y'] + bbox['height'] / 2
                        logger.info(f"{self.log_prefix} Element {aria_label} at ({center_x:.0f}, {center_y:.0f})")

                        # Re-get bounding box after scroll
                        await locator.scroll_into_view_if_needed()
                        await asyncio.sleep(0.3)
                        bbox = await locator.bounding_box()
                        if bbox:
                            center_x = bbox['x'] + bbox['width'] / 2
                            center_y = bbox['y'] + bbox['height'] / 2

                        # For "Request review" button, try multiple click strategies
                        is_request_review = 'request review' in aria_label.lower()

                        # Try Playwright's touchscreen.tap() for trusted touch events
                        try:
                            await self.page.touchscreen.tap(center_x, center_y)
                            clicked_via = f"TOUCHSCREEN_TAP [aria-label=\"{aria_label}\"] at ({center_x:.0f},{center_y:.0f})"
                            logger.info(f"{self.log_prefix} Touchscreen tap: {aria_label}")

                            # For request review button, try multiple strategies
                            if is_request_review:
                                await asyncio.sleep(0.3)
                                # Strategy: Deep click on MComponent children
                                try:
                                    deepest = self.page.locator('[aria-label="Request review"] [data-mcomponent="ServerTextArea"]').first
                                    if await deepest.count() > 0:
                                        await deepest.tap(timeout=5000)
                                        logger.info(f"{self.log_prefix} Request review: tapped ServerTextArea")
                                    else:
                                        container = self.page.locator('[aria-label="Request review"] [data-mcomponent="MContainer"]').first
                                        if await container.count() > 0:
                                            await container.tap(timeout=5000)
                                            logger.info(f"{self.log_prefix} Request review: tapped MContainer")
                                except Exception as e:
                                    logger.warning(f"{self.log_prefix} Deep click failed: {e}")

                                await asyncio.sleep(0.5)
                                # Strategy 2: PointerEvent sequence
                                await locator.evaluate("""(el) => {
                                    const rect = el.getBoundingClientRect();
                                    const x = rect.left + rect.width / 2;
                                    const y = rect.top + rect.height / 2;
                                    ['pointerdown', 'pointerup'].forEach(type => {
                                        el.dispatchEvent(new PointerEvent(type, {
                                            bubbles: true, cancelable: true,
                                            pointerType: 'touch', isPrimary: true,
                                            clientX: x, clientY: y, pointerId: 1
                                        }));
                                    });
                                }""")
                                logger.info(f"{self.log_prefix} Request review: PointerEvent sequence")

                                await asyncio.sleep(0.5)
                                # Strategy 3: CDP touch events
                                try:
                                    cdp = await self.page.context.new_cdp_session(self.page)
                                    await cdp.send('Input.dispatchTouchEvent', {
                                        'type': 'touchStart',
                                        'touchPoints': [{'x': center_x, 'y': center_y}]
                                    })
                                    await asyncio.sleep(0.1)
                                    await cdp.send('Input.dispatchTouchEvent', {
                                        'type': 'touchEnd',
                                        'touchPoints': []
                                    })
                                    logger.info(f"{self.log_prefix} Request review: CDP touch events")
                                except Exception as e:
                                    logger.warning(f"{self.log_prefix} CDP touch failed: {e}")

                                clicked_via += " + MULTI_STRATEGY"
                        except Exception as touch_err:
                            logger.warning(f"{self.log_prefix} Touch events failed: {touch_err}, trying tap()")
                            try:
                                await locator.tap(timeout=5000)
                                clicked_via = f"TAP [aria-label=\"{aria_label}\"]"
                            except Exception as tap_err:
                                logger.warning(f"{self.log_prefix} tap() failed: {tap_err}, trying mouse.click")
                                if bbox:
                                    await self.page.mouse.click(center_x, center_y)
                                    clicked_via = f"mouse.click [aria-label=\"{aria_label}\"] at ({center_x:.0f},{center_y:.0f})"
                                else:
                                    await locator.click(timeout=5000, force=True)
                                    clicked_via = f"CLICK force [aria-label=\"{aria_label}\"]"
                    else:
                        # No bbox, try locator.tap()
                        await locator.tap(timeout=5000)
                        clicked_via = f"LOCATOR_TAP [aria-label=\"{aria_label}\"]"
            except Exception as e:
                logger.warning(f"{self.log_prefix} Native click failed: {e}, falling back to coordinates")

        # Fallback to coordinate click if native failed
        if not clicked_via:
            bounds = target_el.get('bounds', {})
            x = bounds['x'] + bounds['w'] // 2
            y = bounds['y'] + bounds['h'] // 2
            await self.page.mouse.click(x, y)
            clicked_via = f"coordinates ({x},{y})"
            logger.info(f"{self.log_prefix} Clicked via coordinates: ({x},{y})")

        return clicked_via

    async def _fallback_click_known_buttons(self, visible_elements: List[dict], step_num: int, screenshot_path: str) -> bool:
        """Try to click known buttons when Gemini fails. Returns True if clicked."""
        # Priority order for restriction flow buttons
        priority_buttons = ['request review', 'see why', 'ok', 'done', 'continue', 'close']

        for button_name in priority_buttons:
            for el in visible_elements:
                aria = el.get('ariaLabel', '').lower()
                text = el.get('text', '').lower()
                original_aria = el.get('ariaLabel', '')

                # For "see why" require exact match
                is_match = False
                if button_name == 'see why':
                    if aria == 'see why' or text.strip() == 'see why':
                        is_match = True
                elif button_name in aria or button_name in text:
                    is_match = True

                if is_match:
                    bounds = el.get('bounds', {})
                    if bounds.get('y', 0) > 0:
                        logger.info(f"{self.log_prefix} Found button '{button_name}' with aria='{original_aria}'")
                        try:
                            if original_aria:
                                locator = self.page.locator(f'[aria-label="{original_aria}"]').first
                            else:
                                locator = self.page.locator(f'text="{el.get("text", "")}"').first

                            if await locator.count() > 0:
                                await locator.scroll_into_view_if_needed()
                                await asyncio.sleep(0.3)

                                # Dispatch touch events
                                await locator.evaluate("""(el) => {
                                    const rect = el.getBoundingClientRect();
                                    const centerX = rect.left + rect.width / 2;
                                    const centerY = rect.top + rect.height / 2;
                                    const touch = new Touch({
                                        identifier: Date.now(),
                                        target: el,
                                        clientX: centerX, clientY: centerY,
                                        pageX: centerX, pageY: centerY
                                    });
                                    el.dispatchEvent(new TouchEvent('touchstart', {
                                        bubbles: true, cancelable: true,
                                        touches: [touch], targetTouches: [touch], changedTouches: [touch]
                                    }));
                                    el.dispatchEvent(new TouchEvent('touchend', {
                                        bubbles: true, cancelable: true,
                                        touches: [], targetTouches: [], changedTouches: [touch]
                                    }));
                                    el.click();
                                }""")
                                logger.info(f"{self.log_prefix} Fallback touch for '{button_name}'")
                                await asyncio.sleep(2)
                                self.results["steps"].append({
                                    "step": step_num,
                                    "action_taken": f"FALLBACK_TOUCH '{button_name}'",
                                    "screenshot": screenshot_path
                                })
                                return True
                        except Exception as e:
                            logger.warning(f"{self.log_prefix} Fallback touch failed for '{button_name}': {e}")
                            # Fallback to coordinate click
                            x = bounds['x'] + bounds['w'] // 2
                            y = bounds['y'] + bounds['h'] // 2
                            await self.page.mouse.click(x, y)
                            await asyncio.sleep(2)
                            logger.info(f"{self.log_prefix} Fallback mouse click '{button_name}' at ({x},{y})")
                            self.results["steps"].append({
                                "step": step_num,
                                "action_taken": f"FALLBACK_CLICK '{button_name}' at ({x},{y})",
                                "screenshot": screenshot_path
                            })
                            return True

        return False

    async def run(self) -> Dict[str, Any]:
        """Run the adaptive agent task. Returns results dict."""
        # Load session
        self.session = FacebookSession(self.profile_name)
        if not self.session.load():
            return {"error": f"Failed to load session for {self.profile_name}"}

        # Get vision client
        self.vision = get_vision_client()
        if not self.vision:
            return {"error": "Vision client not available"}

        # Set context for Gemini logging
        set_observation_context(profile_name=self.profile_name, campaign_id="adaptive_agent")

        logger.info(f"{self.log_prefix} Starting task: {self.task}")
        logger.info(f"{self.log_prefix} Max steps: {self.max_steps}")

        async with async_playwright() as p:
            # Build context options
            fingerprint = self.session.get_device_fingerprint()
            context_options = {
                "user_agent": self.session.get_user_agent(),
                "viewport": self.session.get_viewport() or MOBILE_VIEWPORT,
                "ignore_https_errors": True,
                "device_scale_factor": 1,
                "timezone_id": fingerprint["timezone"],
                "locale": fingerprint["locale"],
                "has_touch": True,
                "is_mobile": True,
            }

            # Add proxy if session has one
            proxy = self.session.get_proxy()
            if proxy:
                context_options["proxy"] = _build_playwright_proxy(proxy)
                logger.info(f"{self.log_prefix} Using proxy: {proxy[:30]}...")

            # Launch browser
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-notifications", "--disable-gpu"]
            )
            context = await browser.new_context(**context_options)

            # Apply stealth
            await Stealth().apply_stealth_async(context)

            # Create page and apply session cookies
            self.page = await context.new_page()
            await apply_session_to_context(context, self.session)

            try:
                # Step 0: Navigate to start URL
                logger.info(f"{self.log_prefix} Navigating to {self.start_url}...")
                await self.page.goto(self.start_url, wait_until="domcontentloaded", timeout=60000)
                await asyncio.sleep(5)

                # Check if page loaded
                initial_elements = await dump_interactive_elements(self.page, "step_0_check")
                if len(initial_elements) == 0:
                    logger.warning(f"{self.log_prefix} Page didn't load, trying reload...")
                    await self.page.reload(wait_until="domcontentloaded", timeout=60000)
                    await asyncio.sleep(5)
                    initial_elements = await dump_interactive_elements(self.page, "step_0_retry")

                screenshot_path = await save_debug_screenshot(self.page, "adaptive_step_0")
                if not screenshot_path:
                    await asyncio.sleep(5)
                    screenshot_path = await save_debug_screenshot(self.page, "adaptive_step_0_retry")
                    if not screenshot_path:
                        self.results["errors"].append("Step 0: Screenshot failed twice")

                if screenshot_path:
                    self.results["screenshots"].append(screenshot_path)
                self.results["steps"].append({
                    "step": 0,
                    "action": "navigate",
                    "target": self.start_url,
                    "url": self.page.url,
                    "screenshot": screenshot_path or "failed",
                    "elements_found": len(initial_elements)
                })

                # Adaptive loop
                for step_num in range(1, self.max_steps + 1):
                    logger.info(f"{self.log_prefix} Step {step_num}/{self.max_steps}")

                    # Take screenshot
                    screenshot_path = await save_debug_screenshot(self.page, f"adaptive_step_{step_num}")
                    if not screenshot_path:
                        await asyncio.sleep(5)
                        screenshot_path = await save_debug_screenshot(self.page, f"adaptive_step_{step_num}_retry")
                        if not screenshot_path:
                            self.results["errors"].append(f"Step {step_num}: Screenshot failed twice")
                            continue

                    # Dump DOM elements
                    elements = await dump_interactive_elements(self.page, f"step_{step_num}")
                    visible_elements = [el for el in elements if is_element_visible(el)]
                    logger.info(f"{self.log_prefix} Found {len(elements)} total elements, {len(visible_elements)} visible")

                    # Format elements for Gemini
                    elements_summary = []
                    for i, el in enumerate(visible_elements[:30]):
                        text = el.get('text', '')[:40] or el.get('ariaLabel', '')[:40] or el.get('placeholder', '')[:40]
                        bounds = el.get('bounds', {})
                        if text:
                            elements_summary.append(f"[{i}] {el['tag']} \"{text}\" (y={bounds.get('y', '?')})")

                    # Read screenshot
                    with open(screenshot_path, "rb") as f:
                        image_data = f.read()

                    # Build prompt
                    prompt = self._build_prompt(step_num, elements_summary)
                    image_part = types.Part.from_bytes(data=image_data, mime_type="image/png")

                    # Safety settings
                    safety_settings = [
                        types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
                        types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
                        types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
                        types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE"),
                    ]

                    try:
                        response = await asyncio.to_thread(
                            self.vision.client.models.generate_content,
                            model=self.vision.model,
                            contents=[prompt, image_part],
                            config=types.GenerateContentConfig(safety_settings=safety_settings)
                        )

                        result_text = response.text
                        if not result_text:
                            logger.warning(f"{self.log_prefix} Gemini empty response, retrying...")
                            await asyncio.sleep(3)
                            response = await asyncio.to_thread(
                                self.vision.client.models.generate_content,
                                model=self.vision.model,
                                contents=[prompt, image_part]
                            )
                            result_text = response.text

                        if not result_text:
                            # Fallback: try known buttons
                            logger.info(f"{self.log_prefix} Gemini empty - trying DOM fallback")
                            fallback_clicked = await self._fallback_click_known_buttons(visible_elements, step_num, screenshot_path)

                            if not fallback_clicked:
                                # Try scrolling and re-scanning
                                await self.page.mouse.wheel(0, 500)
                                await asyncio.sleep(2)
                                elements_after_scroll = await dump_interactive_elements(self.page, f"scroll_fallback_{step_num}")
                                visible_after = [e for e in elements_after_scroll if is_element_visible(e)]
                                fallback_clicked = await self._fallback_click_known_buttons(visible_after, step_num, screenshot_path)

                            if not fallback_clicked:
                                self.results["errors"].append(f"Step {step_num}: Gemini returned empty response")
                            continue

                        result_text = result_text.strip()
                        logger.info(f"{self.log_prefix} Gemini response:\n{result_text}")

                    except Exception as e:
                        logger.error(f"{self.log_prefix} Gemini API error: {e}")
                        self.results["errors"].append(f"Step {step_num}: Gemini API error - {e}")
                        continue

                    step_result = {
                        "step": step_num,
                        "gemini_response": result_text,
                        "screenshot": screenshot_path,
                        "elements_count": len(elements),
                        "action_taken": None,
                        "url": self.page.url
                    }

                    # Parse the action
                    action_match = re.search(r'ACTION:\s*(\w+)\s*(.*)', result_text, re.IGNORECASE)
                    reasoning_match = re.search(r'REASONING:\s*(.*)', result_text, re.IGNORECASE | re.DOTALL)

                    if not action_match:
                        step_result["action_taken"] = "could_not_parse_action"
                        self.results["steps"].append(step_result)
                        self.results["errors"].append(f"Step {step_num}: Could not parse action")
                        continue

                    action_type = action_match.group(1).upper()
                    action_params = action_match.group(2).strip()
                    reasoning = reasoning_match.group(1).strip() if reasoning_match else ""
                    step_result["reasoning"] = reasoning

                    # Execute the action
                    if action_type == "DONE":
                        step_result["action_taken"] = f"DONE: {action_params}"
                        self.results["steps"].append(step_result)
                        self.results["final_status"] = "task_completed"
                        logger.info(f"{self.log_prefix} Task completed: {action_params}")
                        break

                    elif action_type == "FAILED":
                        step_result["action_taken"] = f"FAILED: {action_params}"
                        self.results["steps"].append(step_result)
                        self.results["final_status"] = "task_failed"
                        logger.info(f"{self.log_prefix} Task failed: {action_params}")
                        break

                    elif action_type == "SCROLL":
                        direction_match = re.search(r'direction=(\w+)', action_params)
                        direction = direction_match.group(1) if direction_match else "down"
                        delta_y = 400 if direction == "down" else -400
                        await self.page.mouse.wheel(0, delta_y)
                        await asyncio.sleep(1.5)
                        step_result["action_taken"] = f"SCROLL {direction}"
                        logger.info(f"{self.log_prefix} Scrolled {direction}")

                    elif action_type == "CLICK":
                        element_match = re.search(r'element="([^"]+)"', action_params)
                        if element_match:
                            element_desc = element_match.group(1)
                            target_el = await find_element_by_description(element_desc, elements, self.log_prefix)

                            if target_el:
                                clicked_via = await self._click_element(target_el, element_desc)

                                # Wait and check for page change
                                url_before = self.page.url
                                await asyncio.sleep(2)
                                url_after = self.page.url
                                page_changed = url_before != url_after

                                step_result["action_taken"] = f"CLICK \"{element_desc}\" via {clicked_via}"
                                step_result["page_changed"] = page_changed
                                step_result["matched_element"] = {
                                    "tag": target_el.get('tag'),
                                    "ariaLabel": target_el.get('ariaLabel', ''),
                                    "text": target_el.get('text', '')[:30]
                                }
                            else:
                                # Fallback selectors
                                clicked = False
                                fallback_selectors = [
                                    f'[aria-label*="{element_desc}" i]',
                                    f'text="{element_desc}"',
                                    f'button:has-text("{element_desc}")',
                                    f'div[role="button"]:has-text("{element_desc}")',
                                ]
                                for selector in fallback_selectors:
                                    try:
                                        locator = self.page.locator(selector).first
                                        if await locator.count() > 0:
                                            await locator.click()
                                            await asyncio.sleep(2)
                                            step_result["action_taken"] = f"CLICK \"{element_desc}\" via selector {selector}"
                                            clicked = True
                                            break
                                    except Exception:
                                        continue

                                if not clicked:
                                    step_result["action_taken"] = f"CLICK_FAILED: Could not find \"{element_desc}\""
                                    self.results["errors"].append(f"Step {step_num}: Element not found: {element_desc}")
                        else:
                            step_result["action_taken"] = "CLICK_PARSE_ERROR"
                            self.results["errors"].append(f"Step {step_num}: Could not parse element description")

                    elif action_type == "TYPE":
                        text_match = re.search(r'text="([^"]+)"', action_params)
                        if text_match:
                            text = text_match.group(1)

                            # Try to find and focus a visible input
                            input_focused = False
                            for el in visible_elements:
                                if el.get('contentEditable') == 'true' or el['tag'] in ['INPUT', 'TEXTAREA']:
                                    bounds = el.get('bounds', {})
                                    if bounds and bounds.get('y', -1) > 0:
                                        x = bounds['x'] + bounds['w'] // 2
                                        y = bounds['y'] + bounds['h'] // 2
                                        await self.page.mouse.click(x, y)
                                        await asyncio.sleep(0.5)
                                        input_focused = True
                                        break

                            await self.page.keyboard.type(text, delay=50)
                            await asyncio.sleep(1)
                            step_result["action_taken"] = f"TYPE: {text[:50]}..."
                            step_result["input_focused"] = input_focused
                        else:
                            step_result["action_taken"] = "TYPE_PARSE_ERROR"

                    elif action_type == "WAIT":
                        await asyncio.sleep(2)
                        step_result["action_taken"] = "WAIT 2s"

                    else:
                        step_result["action_taken"] = f"UNKNOWN: {action_type}"
                        self.results["errors"].append(f"Step {step_num}: Unknown action: {action_type}")

                    self.results["steps"].append(step_result)
                    if screenshot_path:
                        self.results["screenshots"].append(screenshot_path)

                    # Track action for loop detection
                    if step_result.get("action_taken"):
                        self.action_history.append({
                            "step": step_num,
                            "action": step_result["action_taken"]
                        })

                else:
                    self.results["final_status"] = "max_steps_reached"

                # Take final screenshot
                final_screenshot = await save_debug_screenshot(self.page, "adaptive_final")
                if final_screenshot:
                    self.results["screenshots"].append(final_screenshot)
                self.results["final_url"] = self.page.url

            except Exception as e:
                logger.error(f"{self.log_prefix} Error: {e}")
                self.results["errors"].append(str(e))
                self.results["final_status"] = "error"
            finally:
                await browser.close()

        logger.info(f"{self.log_prefix} Complete: {self.results['final_status']}")
        return self.results


async def run_adaptive_task(
    profile_name: str,
    task: str,
    max_steps: int = 15,
    start_url: str = "https://m.facebook.com"
) -> Dict[str, Any]:
    """
    Convenience function to run an adaptive agent task.

    Args:
        profile_name: Name of the Facebook session profile
        task: Natural language task description
        max_steps: Maximum number of agent steps
        start_url: Starting URL for the agent

    Returns:
        Dict with results including steps, screenshots, errors, final_status
    """
    agent = AdaptiveAgent(
        profile_name=profile_name,
        task=task,
        max_steps=max_steps,
        start_url=start_url
    )
    return await agent.run()
