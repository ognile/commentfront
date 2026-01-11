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


# Prompts for different element types
ELEMENT_PROMPTS = {
    "comment_button": """Analyze this Facebook mobile screenshot. Find the Comment button or comment icon.
The comment button is usually:
- A speech bubble icon below the post
- Text saying "Comment"
- Located in the reaction bar (Like, Comment, Share)

Return the EXACT center coordinates where I should click.
Format: FOUND x=XXX y=YYY confidence=0.XX
Or: NOT_FOUND confidence=0.XX reason=description

Only return coordinates if you're confident the element exists and is clickable.""",

    "comment_input": """Analyze this Facebook mobile screenshot. Find the comment input field.
The comment input is usually:
- A text field saying "Write a comment..." or similar
- Located at the bottom of the screen
- Has a profile picture next to it
- May have a placeholder text

Return the EXACT center coordinates where I should click to start typing.
Format: FOUND x=XXX y=YYY confidence=0.XX
Or: NOT_FOUND confidence=0.XX reason=description""",

    "send_button": """Analyze this Facebook mobile screenshot. Find the Send or Post button for comments.
The send button is usually:
- An arrow icon pointing right (paper plane style)
- A "Post" text button
- Located to the right of the comment input
- May be blue or highlighted when text is entered

Return the EXACT center coordinates where I should click to send the comment.
Format: FOUND x=XXX y=YYY confidence=0.XX
Or: NOT_FOUND confidence=0.XX reason=description"""
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
