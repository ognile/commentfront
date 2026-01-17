"""
Gemini Vision Module for Facebook Comment Bot
Uses Gemini 3 Flash for visual element detection and comment verification.
"""

import asyncio
import base64
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List

from google import genai
from google.genai import types

# Import the persistent observations store
from gemini_observations_store import get_observations_store

logger = logging.getLogger("GeminiVision")

# =============================================================================
# GEMINI OBSERVATION LOGGING
# Uses the persistent GeminiObservationsStore for storage
# =============================================================================


def log_gemini_observation(
    screenshot_name: str,
    operation_type: str,
    prompt_type: str,
    full_response: str,
    parsed_result: Dict[str, Any],
    profile_name: Optional[str] = None,
    campaign_id: Optional[str] = None
) -> None:
    """
    Log a full Gemini observation for later debugging.
    This is called BEFORE parsing so we preserve the full AI response.
    Now uses persistent storage instead of in-memory deque.
    """
    get_observations_store().add_observation(
        screenshot_name=screenshot_name,
        operation_type=operation_type,
        prompt_type=prompt_type,
        full_response=full_response,
        parsed_result=parsed_result,
        profile_name=profile_name,
        campaign_id=campaign_id
    )


def get_recent_observations(limit: int = 20) -> List[Dict[str, Any]]:
    """Get recent Gemini observations for debugging/analytics."""
    return get_observations_store().get_recent(limit=limit)


def clear_observations() -> int:
    """Clear all stored observations. Returns count cleared."""
    return get_observations_store().clear()


# Context for current operation (set by caller, used in logging)
_current_context: Dict[str, str] = {}


def set_observation_context(profile_name: Optional[str] = None, campaign_id: Optional[str] = None):
    """Set context for subsequent Gemini observations."""
    global _current_context
    _current_context = {
        "profile_name": profile_name,
        "campaign_id": campaign_id
    }


def get_observation_context() -> Dict[str, str]:
    """Get current observation context."""
    return _current_context.copy()

# Configuration from environment
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
CONFIDENCE_THRESHOLD = float(os.getenv("VISION_CONFIDENCE_THRESHOLD", "0.7"))


@dataclass
class ElementLocation:
    """Result of element detection."""
    found: bool
    x: int = 0
    y: int = 0
    confidence: float = 0.0
    description: str = ""


@dataclass
class VerificationResult:
    """Result of comment verification."""
    success: bool
    confidence: float = 0.0
    message: str = ""
    status: str = "unknown"  # "posted", "pending", "failed", "unknown"


# Prompts for finding elements - SPECIFIC with exclusions
# IMPORTANT: Image is 393x873 pixels (mobile viewport)
ELEMENT_PROMPTS = {
    "comment_button": """Find the Comment button on this Facebook mobile screenshot.

IMAGE SIZE: 393 pixels wide, 873 pixels tall.
Coordinates must be: x between 0-393, y between 0-873.

The Comment button is:
- A SPEECH BUBBLE icon (looks like a chat bubble)
- Located in the MIDDLE/CENTER of the reaction bar (between Like and Share)
- The reaction bar layout from left to right is: Like | Comment | Share
- The reaction bar is DIRECTLY BELOW the post image, BEFORE any "Reels" section
- Expected coordinates: x=150-220 (center), y=420-500 (below post, above Reels)
- May show a number next to it (like "1 Comment")

CRITICAL LOCATION CONSTRAINTS:
- X coordinate: 150-220 (center of screen, NOT left like=50-120, NOT right share=280-350)
- Y coordinate: 420-500 (reaction bar row, NOT the Reels section below)
- The "Reels" section starts around y=500+ with video thumbnails - DO NOT click there!

DO NOT click on:
- The Share button (arrow icon on the RIGHT side, x > 280)
- The Like button (thumbs up on the LEFT side, x < 120)
- Reels video thumbnails (y > 500)
- Profile pictures
- The "..." more options button

Return the CENTER coordinates of the Comment button (speech bubble in the MIDDLE of the reaction bar).
Format: FOUND x=XXX y=YYY confidence=0.XX
Or: NOT_FOUND confidence=0.XX reason=description

IMPORTANT: x must be 150-220, y must be 420-500 for the reaction bar.""",

    "comment_input": """Find the comment input text field on this Facebook mobile screenshot.

IMAGE SIZE: 393 pixels wide, 873 pixels tall.
Coordinates must be: x between 0-393, y between 0-873.

The input field is:
- A text box saying "Write a comment..." or similar placeholder
- Located at the bottom of the screen
- May have a small profile picture to the LEFT of it
- Has a white/light background with gray placeholder text

DO NOT return:
- The search bar at the top of the page
- Profile pictures (circular photos)
- Any buttons or icons
- The post content area

Return the CENTER coordinates of the text input field.
Format: FOUND x=XXX y=YYY confidence=0.XX
Or: NOT_FOUND confidence=0.XX reason=description

IMPORTANT: x must be 0-393, y must be 0-873.""",

    "send_button": """Find the Send/Post button for comments on this Facebook mobile screenshot.

IMAGE SIZE: 393 pixels wide, 873 pixels tall.
Coordinates must be: x between 0-393, y between 0-873.

The send button is:
- A blue arrow icon (paper plane style) OR blue "Post" text
- Located to the RIGHT of the comment input field
- Usually blue/highlighted color when active
- Only visible when text has been entered in the comment field

DO NOT return:
- The Share button (located elsewhere)
- Profile pictures
- Any grayed out or inactive elements
- Navigation arrows

Return the CENTER coordinates of the send button.
Format: FOUND x=XXX y=YYY confidence=0.XX
Or: NOT_FOUND confidence=0.XX reason=description

IMPORTANT: x must be 0-393, y must be 0-873."""
}

# Prompts for STATE VERIFICATION after actions
VERIFICATION_PROMPTS = {
    "post_visible": """Analyze this Facebook mobile screenshot.

Check if you can see a Facebook POST (not just any page):
1. Is there post content visible (text, image, or video)?
2. Can you see reaction buttons below (Like, Comment, Share icons)?
3. Is the Comment button specifically visible (speech bubble icon)?

IMPORTANT: You MUST respond with ONLY one of these exact formats:
VERIFIED confidence=0.XX
NOT_VERIFIED reason=your reason here

Do NOT write anything else. Just one line in the format above.""",

    "comments_opened": """Analyze this Facebook mobile screenshot.

Check if the COMMENTS SECTION is now open:
1. Can you see a "Write a comment..." input field?
2. Is there a text input area ready for typing?

IMPORTANT: You MUST respond with ONLY one of these exact formats:
VERIFIED confidence=0.XX
NOT_VERIFIED reason=your reason here

Do NOT write anything else. Just one line in the format above.""",

    "input_active": """Analyze this Facebook mobile screenshot.

Check if the comment input field appears ACTIVE/FOCUSED:
1. Does the input field look selected or highlighted?
2. Is there a cursor or text area ready for typing?

IMPORTANT: You MUST respond with ONLY one of these exact formats:
VERIFIED confidence=0.XX
NOT_VERIFIED reason=your reason here

Do NOT write anything else. Just one line in the format above.""",

    "text_typed": """Analyze this Facebook mobile screenshot.

Expected text snippet: "{expected_text}"

Check if the comment input field CONTAINS text that INCLUDES this snippet.
This is a partial match check - the snippet may appear anywhere in the typed text (beginning, middle, or end).
If you can see text in the input field that contains these words/characters, verify it.

IMPORTANT: You MUST respond with ONLY one of these exact formats:
VERIFIED confidence=0.XX
NOT_VERIFIED reason=your reason here

Do NOT write anything else. Just one line in the format above.""",

    "comment_posted": """Analyze this Facebook mobile screenshot.

Expected comment snippet: "{expected_text}"

Check if a comment was SUCCESSFULLY POSTED that CONTAINS this snippet.
This is a partial match check - the snippet may appear anywhere in the posted comment.
Look in the comments section (not the input field) for text that includes these words/characters.

IMPORTANT: You MUST respond with ONLY one of these exact formats:
VERIFIED confidence=0.XX
PENDING confidence=0.XX
NOT_VERIFIED reason=your reason here

Do NOT write anything else. Just one line in the format above.""",

    "check_restriction": """Analyze this Facebook mobile screenshot.

Check if there are ANY signs that this account is RESTRICTED from commenting:
1. "You can't comment right now" or "You can't comment for X days/hours"
2. "Action blocked" or "Action temporarily blocked"
3. "Try again later" warning
4. "We removed your comment" moderation notice
5. "Something went wrong" with commenting
6. "You're temporarily blocked" or "You've been restricted"
7. Any popup/dialog indicating comment restrictions

IMPORTANT: You MUST respond with ONLY one of these exact formats:
RESTRICTED reason=exact message or description of restriction
NOT_RESTRICTED confidence=0.XX

Do NOT write anything else. Just one line in the format above."""
}

VERIFICATION_PROMPT = """Analyze this Facebook mobile screenshot. Check if a comment was successfully posted.

Expected comment text: "{comment}"

Look for:
1. The exact comment text appearing in the comments section
2. The comment showing the user's name/profile
3. Any "posting..." or pending indicators
4. Error messages like "couldn't post" or "try again"

Return your assessment:
Format: STATUS=[posted|pending|failed|unknown] confidence=0.XX message=description

- posted: Comment is visible in the comments section
- pending: Comment appears to be processing/uploading
- failed: Error message visible or comment clearly not posted
- unknown: Cannot determine status"""


class GeminiVisionClient:
    """Client for Gemini Vision API calls."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or GEMINI_API_KEY
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY not set")
        self.client = genai.Client(api_key=self.api_key)
        self.model = GEMINI_MODEL
        logger.info(f"Initialized Gemini Vision with model: {self.model}")

    async def find_element(
        self,
        screenshot_path: str,
        element_type: str,
        additional_context: str = ""
    ) -> Optional[ElementLocation]:
        """
        Find an element in a screenshot using Gemini vision.

        Args:
            screenshot_path: Path to the screenshot image
            element_type: One of "comment_button", "comment_input", "send_button"
            additional_context: Optional additional context for the prompt

        Returns:
            ElementLocation with coordinates and confidence, or None on error
        """
        if element_type not in ELEMENT_PROMPTS:
            logger.error(f"Unknown element type: {element_type}")
            return None

        try:
            # Read and encode the image
            with open(screenshot_path, "rb") as f:
                image_data = f.read()

            # Build the prompt
            prompt = ELEMENT_PROMPTS[element_type]
            if additional_context:
                prompt += f"\n\nAdditional context: {additional_context}"

            # Create the image part
            image_part = types.Part.from_bytes(
                data=image_data,
                mime_type="image/png"
            )

            # Make the API call
            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=self.model,
                contents=[prompt, image_part]
            )

            # Parse the response
            result_text = response.text.strip()
            logger.debug(f"Gemini response for {element_type}: {result_text}")

            # Parse the response
            parsed = self._parse_element_response(result_text)

            # Log the FULL observation before returning (for debugging)
            screenshot_name = os.path.basename(screenshot_path)
            context = get_observation_context()
            log_gemini_observation(
                screenshot_name=screenshot_name,
                operation_type="find_element",
                prompt_type=element_type,
                full_response=result_text,
                parsed_result={
                    "found": parsed.found,
                    "x": parsed.x,
                    "y": parsed.y,
                    "confidence": parsed.confidence
                },
                profile_name=context.get("profile_name"),
                campaign_id=context.get("campaign_id")
            )

            return parsed

        except Exception as e:
            logger.error(f"Gemini vision error: {e}")
            return None

    async def verify_state(
        self,
        screenshot_path: str,
        verification_type: str,
        **kwargs
    ) -> VerificationResult:
        """
        Verify the current page state matches expectations.

        Args:
            screenshot_path: Path to the screenshot image
            verification_type: One of "post_visible", "comments_opened", "input_active", "text_typed", "comment_posted"
            **kwargs: Additional context (e.g., expected_text for text_typed)

        Returns:
            VerificationResult with verified status and confidence
        """
        if verification_type not in VERIFICATION_PROMPTS:
            logger.error(f"Unknown verification type: {verification_type}")
            return VerificationResult(
                success=False,
                confidence=0.0,
                message=f"Unknown verification type: {verification_type}",
                status="unknown"
            )

        try:
            with open(screenshot_path, "rb") as f:
                image_data = f.read()

            # Get and format the prompt
            prompt = VERIFICATION_PROMPTS[verification_type]
            if kwargs:
                prompt = prompt.format(**kwargs)

            image_part = types.Part.from_bytes(
                data=image_data,
                mime_type="image/png"
            )

            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=self.model,
                contents=[prompt, image_part]
            )

            result_text = response.text.strip()
            logger.info(f"Gemini verify_state ({verification_type}): {result_text}")

            # Parse the response
            parsed = self._parse_state_verification_response(result_text)

            # Log the FULL observation before returning (for debugging)
            screenshot_name = os.path.basename(screenshot_path)
            context = get_observation_context()
            log_gemini_observation(
                screenshot_name=screenshot_name,
                operation_type="verify_state",
                prompt_type=verification_type,
                full_response=result_text,
                parsed_result={
                    "success": parsed.success,
                    "confidence": parsed.confidence,
                    "status": parsed.status,
                    "message": parsed.message[:200] if parsed.message else ""
                },
                profile_name=context.get("profile_name"),
                campaign_id=context.get("campaign_id")
            )

            return parsed

        except Exception as e:
            logger.error(f"Gemini verify_state error: {e}")
            return VerificationResult(
                success=False,
                confidence=0.0,
                message=str(e),
                status="unknown"
            )

    async def check_restriction(
        self,
        screenshot_path: str
    ) -> dict:
        """
        Check if the current screenshot shows any restriction/throttling warnings.

        Returns:
            dict with keys:
            - restricted: bool
            - reason: str (the restriction message/reason if restricted)
            - confidence: float
        """
        try:
            with open(screenshot_path, "rb") as f:
                image_data = f.read()

            prompt = VERIFICATION_PROMPTS["check_restriction"]

            image_part = types.Part.from_bytes(
                data=image_data,
                mime_type="image/png"
            )

            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=self.model,
                contents=[prompt, image_part]
            )

            result_text = response.text.strip()
            logger.info(f"Gemini check_restriction: {result_text}")

            # Parse the response
            result = {
                "restricted": False,
                "reason": None,
                "confidence": 0.0
            }

            if result_text.startswith("RESTRICTED"):
                result["restricted"] = True
                # Extract reason from "RESTRICTED reason=..."
                if "reason=" in result_text:
                    result["reason"] = result_text.split("reason=", 1)[1].strip()
                else:
                    result["reason"] = "Unknown restriction"
                result["confidence"] = 0.95  # High confidence if explicitly detected
            elif result_text.startswith("NOT_RESTRICTED"):
                result["restricted"] = False
                # Extract confidence from "NOT_RESTRICTED confidence=0.XX"
                if "confidence=" in result_text:
                    try:
                        conf_str = result_text.split("confidence=")[1].split()[0]
                        result["confidence"] = float(conf_str)
                    except:
                        result["confidence"] = 0.8

            # Log the observation
            screenshot_name = os.path.basename(screenshot_path)
            context = get_observation_context()
            log_gemini_observation(
                screenshot_name=screenshot_name,
                operation_type="check_restriction",
                prompt_type="check_restriction",
                full_response=result_text,
                parsed_result=result,
                profile_name=context.get("profile_name"),
                campaign_id=context.get("campaign_id")
            )

            return result

        except Exception as e:
            logger.error(f"Gemini check_restriction error: {e}")
            return {
                "restricted": False,
                "reason": None,
                "confidence": 0.0,
                "error": str(e)
            }

    async def verify_comment_posted(
        self,
        screenshot_path: str,
        expected_comment: str
    ) -> VerificationResult:
        """
        Verify if a comment was successfully posted.

        Args:
            screenshot_path: Path to the screenshot image
            expected_comment: The comment text that should appear

        Returns:
            VerificationResult with status and confidence
        """
        try:
            with open(screenshot_path, "rb") as f:
                image_data = f.read()

            prompt = VERIFICATION_PROMPT.format(comment=expected_comment[:100])

            image_part = types.Part.from_bytes(
                data=image_data,
                mime_type="image/png"
            )

            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=self.model,
                contents=[prompt, image_part]
            )

            result_text = response.text.strip()
            logger.info(f"Gemini verification response: {result_text}")

            # Parse the response
            parsed = self._parse_verification_response(result_text)

            # Log the FULL observation before returning (for debugging)
            screenshot_name = os.path.basename(screenshot_path)
            context = get_observation_context()
            log_gemini_observation(
                screenshot_name=screenshot_name,
                operation_type="verify_comment",
                prompt_type="comment_posted",
                full_response=result_text,
                parsed_result={
                    "success": parsed.success,
                    "confidence": parsed.confidence,
                    "status": parsed.status,
                    "message": parsed.message[:200] if parsed.message else ""
                },
                profile_name=context.get("profile_name"),
                campaign_id=context.get("campaign_id")
            )

            return parsed

        except Exception as e:
            logger.error(f"Gemini verification error: {e}")
            return VerificationResult(
                success=False,
                confidence=0.0,
                message=str(e),
                status="unknown"
            )

    async def decide_next_action(
        self,
        screenshot_path: str,
        action_attempted: str,
        selector_audit: dict
    ) -> dict:
        """
        Ask Gemini to decide what to do next when an action fails.
        Returns structured decision for autonomous self-healing.

        Decisions:
        - ABORT: Stop trying (wrong page, logged out, etc.)
        - WAIT: Page loading, retry after delay
        - CLOSE_POPUP: Modal blocking, close it first
        - TRY_SELECTOR: Suggest alternative CSS selector
        - SCROLL: Element off-screen
        - RETRY: Just try again
        """
        import json

        try:
            with open(screenshot_path, "rb") as f:
                image_data = f.read()

            prompt = f"""Analyze this Facebook mobile screenshot. I tried to click "{action_attempted}" but the CSS selectors failed.

SELECTOR AUDIT (what matched in the DOM):
{json.dumps(selector_audit, indent=2)}

Based on the screenshot AND selector audit, decide what I should do next.

You MUST respond with EXACTLY ONE of these actions:
- ABORT reason=<why> (wrong page type, logged out, content removed, Reels page)
- WAIT seconds=<1-5> (page still loading, spinner visible)
- CLOSE_POPUP selector=<css> (modal/dialog blocking the view)
- TRY_SELECTOR selector=<css> (suggest a CSS selector you can see might work)
- SCROLL direction=<up|down> (element might be off-screen)
- RETRY (transient issue, just try again)

Format: ACTION param=value
Examples:
ABORT reason=This is a Reels page not a regular post
WAIT seconds=2
TRY_SELECTOR selector=div[role="button"]:has-text("Comment")
SCROLL direction=down"""

            image_part = types.Part.from_bytes(
                data=image_data,
                mime_type="image/png"
            )

            response = await asyncio.to_thread(
                self.client.models.generate_content,
                model=self.model,
                contents=[prompt, image_part]
            )

            result_text = response.text.strip()
            logger.info(f"Gemini decision: {result_text}")

            # Parse the decision
            parsed = self._parse_decision(result_text)

            # Log the FULL observation before returning (for debugging)
            screenshot_name = os.path.basename(screenshot_path)
            context = get_observation_context()
            log_gemini_observation(
                screenshot_name=screenshot_name,
                operation_type="decide_action",
                prompt_type=action_attempted,
                full_response=result_text,
                parsed_result=parsed,
                profile_name=context.get("profile_name"),
                campaign_id=context.get("campaign_id")
            )

            return parsed

        except Exception as e:
            logger.error(f"Gemini decide_next_action error: {e}")
            return {"action": "RETRY", "error": str(e)}

    def _parse_decision(self, response: str) -> dict:
        """Parse Gemini's decision into actionable dict."""
        import re

        # Get first line only (ignore extra explanation)
        first_line = response.strip().split('\n')[0].strip()

        if first_line.upper().startswith("ABORT"):
            reason = first_line.split("=", 1)[1].strip() if "=" in first_line else "unknown"
            return {"action": "ABORT", "reason": reason}

        elif first_line.upper().startswith("WAIT"):
            match = re.search(r'(\d+)', first_line)
            seconds = int(match.group(1)) if match else 2
            return {"action": "WAIT", "seconds": min(seconds, 5)}

        elif first_line.upper().startswith("CLOSE_POPUP"):
            selector = first_line.split("=", 1)[1].strip() if "=" in first_line else 'button[aria-label="Close"]'
            return {"action": "CLOSE_POPUP", "selector": selector}

        elif first_line.upper().startswith("TRY_SELECTOR"):
            selector = first_line.split("=", 1)[1].strip() if "=" in first_line else None
            return {"action": "TRY_SELECTOR", "selector": selector}

        elif first_line.upper().startswith("SCROLL"):
            direction = "down" if "down" in first_line.lower() else "up"
            return {"action": "SCROLL", "direction": direction}

        else:
            return {"action": "RETRY"}

    def _parse_element_response(self, response: str) -> ElementLocation:
        """Parse Gemini response for element location."""
        response_upper = response.upper()

        # Viewport bounds (mobile viewport)
        MAX_X = 393
        MAX_Y = 873
        MARGIN = 10  # Pixels from edge

        if "FOUND" in response_upper and "NOT_FOUND" not in response_upper:
            try:
                # Extract coordinates
                x = self._extract_number(response, "x=")
                y = self._extract_number(response, "y=")
                confidence = self._extract_float(response, "confidence=")

                if x > 0 and y > 0:
                    # VALIDATE & CLIP BOUNDS
                    original_x, original_y = x, y
                    x = min(max(x, MARGIN), MAX_X - MARGIN)
                    y = min(max(y, MARGIN), MAX_Y - MARGIN)

                    if original_x != x or original_y != y:
                        logger.warning(f"Vision coords clipped: ({original_x},{original_y}) → ({x},{y})")

                    return ElementLocation(
                        found=True,
                        x=x,
                        y=y,
                        confidence=confidence,
                        description=response
                    )
            except Exception as e:
                logger.warning(f"Failed to parse coordinates: {e}")

        # Not found or parse error
        confidence = self._extract_float(response, "confidence=")
        return ElementLocation(
            found=False,
            confidence=confidence,
            description=response
        )

    def _parse_state_verification_response(self, response: str) -> VerificationResult:
        """Parse Gemini response for state verification (VERIFIED/NOT_VERIFIED/PENDING)."""
        response_upper = response.upper()
        response_lower = response.lower()

        # Determine if verified
        verified = False
        status = "unknown"

        if "VERIFIED" in response_upper and "NOT_VERIFIED" not in response_upper:
            verified = True
            status = "verified"
        elif "NOT_VERIFIED" in response_upper:
            verified = False
            status = "not_verified"
        elif "PENDING" in response_upper:
            verified = False
            status = "pending"

        confidence = self._extract_float(response, "confidence=")

        # Extract reason if present
        reason = response
        if "reason=" in response_lower:
            try:
                reason_start = response_lower.index("reason=") + 7
                reason = response[reason_start:].strip()
            except:
                pass

        return VerificationResult(
            success=verified,
            confidence=confidence,
            message=reason,
            status=status
        )

    def _parse_verification_response(self, response: str) -> VerificationResult:
        """Parse Gemini response for verification."""
        response_lower = response.lower()

        # Extract status
        status = "unknown"
        if "status=posted" in response_lower or "status = posted" in response_lower:
            status = "posted"
        elif "status=pending" in response_lower or "status = pending" in response_lower:
            status = "pending"
        elif "status=failed" in response_lower or "status = failed" in response_lower:
            status = "failed"

        confidence = self._extract_float(response, "confidence=")

        # Extract message
        message = response
        if "message=" in response_lower:
            try:
                msg_start = response_lower.index("message=") + 8
                message = response[msg_start:].strip()
            except:
                pass

        return VerificationResult(
            success=(status == "posted"),
            confidence=confidence,
            message=message,
            status=status
        )

    def _extract_number(self, text: str, prefix: str) -> int:
        """Extract an integer after a prefix."""
        text_lower = text.lower()
        prefix_lower = prefix.lower()

        if prefix_lower in text_lower:
            start = text_lower.index(prefix_lower) + len(prefix_lower)
            num_str = ""
            for char in text[start:]:
                if char.isdigit():
                    num_str += char
                elif num_str:
                    break
            if num_str:
                return int(num_str)
        return 0

    def _extract_float(self, text: str, prefix: str) -> float:
        """Extract a float after a prefix."""
        text_lower = text.lower()
        prefix_lower = prefix.lower()

        if prefix_lower in text_lower:
            start = text_lower.index(prefix_lower) + len(prefix_lower)
            num_str = ""
            for char in text[start:]:
                if char.isdigit() or char == '.':
                    num_str += char
                elif num_str:
                    break
            if num_str:
                try:
                    return float(num_str)
                except:
                    pass
        return 0.0


# Singleton instance
_vision_client: Optional[GeminiVisionClient] = None


def get_vision_client() -> Optional[GeminiVisionClient]:
    """Get or create the Gemini vision client singleton."""
    global _vision_client

    if _vision_client is None:
        try:
            _vision_client = GeminiVisionClient()
        except Exception as e:
            logger.warning(f"Failed to initialize vision client: {e}")
            return None

    return _vision_client
