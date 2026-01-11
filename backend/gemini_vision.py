"""
Gemini Vision Module for Facebook Comment Bot
Uses Gemini 3 Flash for visual element detection and comment verification.
"""

import asyncio
import base64
import logging
import os
from dataclasses import dataclass
from typing import Optional

from google import genai
from google.genai import types

logger = logging.getLogger("GeminiVision")

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
ELEMENT_PROMPTS = {
    "comment_button": """Find the Comment button on this Facebook post.

The Comment button is:
- A speech bubble icon OR the text "Comment"
- Located in the reaction bar below the post (same row as Like, Share)
- Usually gray/dark icon

DO NOT click on:
- Profile pictures (circular photos of people)
- The post author's name or profile
- Any other icons outside the reaction bar
- The "..." more options button
- Share or Like buttons

Return ONLY if you're confident this is the Comment button.
Format: FOUND x=XXX y=YYY confidence=0.XX
Or: NOT_FOUND confidence=0.XX reason=description""",

    "comment_input": """Find the comment input text field.

The input field is:
- A text box saying "Write a comment..." or similar placeholder
- Located at the bottom of the comments section
- May have a small profile picture to the LEFT of it
- Has a white/light background with gray placeholder text
- Is a rectangular input area, NOT a button

DO NOT return:
- The search bar at the top of the page
- Profile pictures (circular photos)
- Any buttons or icons
- The post content area

Return ONLY the exact center of the text input field.
Format: FOUND x=XXX y=YYY confidence=0.XX
Or: NOT_FOUND confidence=0.XX reason=description""",

    "send_button": """Find the Send/Post button for comments.

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

Return ONLY if you can see the send button clearly.
Format: FOUND x=XXX y=YYY confidence=0.XX
Or: NOT_FOUND confidence=0.XX reason=description"""
}

# Prompts for STATE VERIFICATION after actions
VERIFICATION_PROMPTS = {
    "post_visible": """Analyze this Facebook mobile screenshot.

Check if you can see a Facebook POST (not just any page):
1. Is there post content visible (text, image, or video)?
2. Can you see reaction buttons below (Like, Comment, Share icons)?
3. Is the Comment button specifically visible (speech bubble icon)?

Return: VERIFIED confidence=0.XX if this is a Facebook post with visible Comment button
Return: NOT_VERIFIED reason=description if something is missing or this is a different page""",

    "comments_opened": """Analyze this Facebook mobile screenshot.

Check if the COMMENTS SECTION is now open:
1. Can you see a "Write a comment..." input field at the bottom?
2. Is there a text input area ready for typing?
3. Are existing comments visible above the input (if any exist)?
4. Is there a small profile picture next to the input field?

Return: VERIFIED confidence=0.XX if comments section is open and input field is visible
Return: NOT_VERIFIED reason=description if the input field is not visible""",

    "input_active": """Analyze this Facebook mobile screenshot.

Check if the comment input field appears ACTIVE/FOCUSED:
1. Does the input field look selected or highlighted?
2. Is there a cursor blinking or visible in the field?
3. Has the placeholder text ("Write a comment...") disappeared or changed?
4. Does the field appear ready to receive text?

Return: VERIFIED confidence=0.XX if input appears active/focused
Return: NOT_VERIFIED reason=description if input looks inactive""",

    "text_typed": """Analyze this Facebook mobile screenshot.

Expected text: "{expected_text}"

Check if this EXACT text (or very similar) is visible in the comment input field:
1. Can you read text in the comment input area?
2. Does the visible text match or closely match the expected text?
3. Is the text in the input field, NOT in the comments list above?

Return: VERIFIED confidence=0.XX if the expected text is visible in the input
Return: NOT_VERIFIED reason=description if text is not visible or doesn't match""",

    "comment_posted": """Analyze this Facebook mobile screenshot.

Expected comment: "{expected_text}"

Check if the comment was SUCCESSFULLY POSTED:
1. Is the comment visible in the comments SECTION (not the input field)?
2. Does the comment text match what was expected?
3. Is the input field now EMPTY (ready for new comment)?
4. Is there any error message visible?
5. Is there a "posting..." or loading indicator?

Return: VERIFIED confidence=0.XX if comment appears in the comments section
Return: PENDING confidence=0.XX if still loading/processing
Return: NOT_VERIFIED reason=description if comment not visible or error occurred"""
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

            return self._parse_element_response(result_text)

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
            logger.debug(f"Gemini verify_state ({verification_type}): {result_text}")

            return self._parse_state_verification_response(result_text)

        except Exception as e:
            logger.error(f"Gemini verify_state error: {e}")
            return VerificationResult(
                success=False,
                confidence=0.0,
                message=str(e),
                status="unknown"
            )

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
            logger.debug(f"Gemini verification response: {result_text}")

            return self._parse_verification_response(result_text)

        except Exception as e:
            logger.error(f"Gemini verification error: {e}")
            return VerificationResult(
                success=False,
                confidence=0.0,
                message=str(e),
                status="unknown"
            )

    def _parse_element_response(self, response: str) -> ElementLocation:
        """Parse Gemini response for element location."""
        response_upper = response.upper()

        if "FOUND" in response_upper and "NOT_FOUND" not in response_upper:
            try:
                # Extract coordinates
                x = self._extract_number(response, "x=")
                y = self._extract_number(response, "y=")
                confidence = self._extract_float(response, "confidence=")

                if x > 0 and y > 0:
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
