"""
Gemini Image Generation Module

Generates AI profile photos using Gemini 2.5 Flash Image model.
Optimized for realistic, candid iPhone-style selfies.
"""

import asyncio
import base64
import io
import logging
import os
import random
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

from PIL import Image
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# Configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
IMAGE_MODEL = "gemini-3-pro-image-preview"  # Gemini 3 image generation model

# Output directory for generated images
IMAGE_OUTPUT_DIR = Path(os.getenv("IMAGE_OUTPUT_DIR", "/tmp/profile_photos"))
IMAGE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Pose variations for profile photo regeneration
POSE_VARIATIONS: List[Dict[str, str]] = [
    {
        "name": "beach",
        "prompt": "Casual beach selfie with ocean waves visible in background, sun-kissed natural skin, messy beach hair from the wind, wearing a casual summer top or bikini strap visible, squinting slightly from the sun, authentic vacation vibe"
    },
    {
        "name": "gym_mirror",
        "prompt": "Gym mirror selfie in casual workout clothes (tank top or sports bra), visible gym equipment in reflection, slight sheen of sweat, confident but relaxed expression, phone held at chest level, good fluorescent gym lighting"
    },
    {
        "name": "coffee_shop",
        "prompt": "Cozy coffee shop selfie holding a latte or coffee cup, warm indoor lighting, wearing a cozy sweater or casual top, slight smile, coffee shop interior blurred in background, morning vibes"
    },
    {
        "name": "car",
        "prompt": "Casual car selfie from driver seat, seatbelt visible across chest, natural daylight streaming through windows, wearing casual clothes, relaxed expression, car interior visible, parked car setting"
    },
    {
        "name": "kitchen",
        "prompt": "Relaxed home selfie in kitchen, morning golden light through windows, wearing casual home clothes (t-shirt, robe, or casual top), coffee mug or kitchen items visible in background, freshly woken up natural look"
    },
    {
        "name": "living_room",
        "prompt": "Selfie on couch or armchair, cozy blanket or throw pillow visible, soft evening lamp lighting, wearing comfortable lounge clothes, relaxed happy expression, living room decor in background"
    },
    {
        "name": "outdoor_walk",
        "prompt": "Walking selfie in park or neighborhood sidewalk, sunny day with trees or houses in background, slightly windswept hair, wearing casual outdoor clothes (jacket, hoodie, or t-shirt), natural smile, bright natural lighting"
    },
    {
        "name": "with_pet",
        "prompt": "Selfie with a dog or cat visible in frame, happy excited expression, pet partially visible (face, ear, or paw), home setting, warm loving expression, casual home clothes, genuine joy"
    },
    {
        "name": "restaurant",
        "prompt": "Nice restaurant or dinner selfie, evening attire (nice top or blouse), subtle romantic restaurant lighting, background slightly blurred showing restaurant interior, dressed up but casual, confident expression"
    },
    {
        "name": "bathroom_mirror",
        "prompt": "Classic bathroom mirror selfie, good vanity lighting, casual everyday outfit visible, clean modern bathroom in reflection, natural pose, phone clearly visible in mirror, getting ready vibe"
    },
    {
        "name": "hiking",
        "prompt": "Outdoor hiking trail selfie, nature background with trees or mountains, wearing athletic hiking clothes, slight sweat or exertion visible, happy accomplished expression, bright natural daylight, adventure vibe"
    },
    {
        "name": "pool_backyard",
        "prompt": "Backyard selfie by pool or patio, summer casual clothes or swimwear, bright sunny lighting, pool water or patio furniture visible, relaxed vacation vibe, sunglasses pushed up on head or nearby"
    },
]


def get_image_client():
    """Get Gemini client configured for image generation."""
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY environment variable not set")
    return genai.Client(api_key=GEMINI_API_KEY)


def build_selfie_prompt(
    gender: str = "woman",
    age_range: str = "30-40",
    ethnicity: str = "caucasian",
    extra_details: str = ""
) -> str:
    """
    Build a highly specific prompt for realistic iPhone selfie generation.

    The goal is a photo that looks like a real person took it with their phone -
    not a professional photo, not AI-generated looking.
    """
    prompt = f"""Generate a hyper-realistic iPhone selfie photo of a {ethnicity} {gender} in their {age_range}s.

CRITICAL REQUIREMENTS FOR REALISM:
- Shot on iPhone 15 Pro, front camera
- Candid, casual expression - slight natural smile or neutral
- Natural indoor lighting (living room, kitchen, or office)
- Slight imperfections: a few flyaway hairs, minor skin texture
- NO professional lighting, NO studio backdrop
- NO heavy makeup, NO perfect symmetry
- Eye contact with camera (selfie pose)
- One hand may be slightly visible holding phone
- Background should be slightly out of focus (natural phone depth)
- Clothing: casual everyday clothes (t-shirt, sweater, or blouse)

ANTI-AI TELLS TO AVOID:
- No waxy or overly smooth skin
- No perfectly symmetrical features
- No unnatural eye reflections
- No blurred or melted ears/jewelry
- No extra fingers or distorted hands
- No text or watermarks
- No uncanny valley expressions

ASPECT RATIO: 1:1 square (for profile picture use)
STYLE: Raw, unedited, authentic social media selfie

{extra_details}"""

    return prompt


async def generate_profile_photo(
    gender: str = "woman",
    age_range: str = "30-40",
    ethnicity: str = "caucasian",
    extra_details: str = "",
    profile_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Generate a realistic AI profile photo.

    Args:
        gender: "woman" or "man"
        age_range: e.g., "25-35", "40-50"
        ethnicity: e.g., "caucasian", "african american", "asian", "latina"
        extra_details: Additional prompt details (hair color, style, etc.)
        profile_name: Optional profile name for filename

    Returns:
        Dict with:
            - success: bool
            - image_path: str (path to saved image)
            - error: str (if failed)
    """
    try:
        client = get_image_client()
        prompt = build_selfie_prompt(gender, age_range, ethnicity, extra_details)

        logger.info(f"[IMAGE_GEN] Generating profile photo: {gender}, {age_range}, {ethnicity}")
        logger.debug(f"[IMAGE_GEN] Full prompt: {prompt}")

        # Generate image
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=IMAGE_MODEL,
            contents=[prompt],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
            )
        )

        # Extract image from response
        image_data = None
        for part in response.parts:
            if part.inline_data is not None:
                image_data = part.inline_data.data
                break

        if not image_data:
            logger.error("[IMAGE_GEN] No image in response")
            return {
                "success": False,
                "error": "No image generated in response"
            }

        # Save image
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename_base = profile_name or f"profile_{uuid.uuid4().hex[:8]}"
        filename = f"{filename_base}_{timestamp}.png"
        image_path = IMAGE_OUTPUT_DIR / filename

        # Decode and save
        if isinstance(image_data, str):
            image_bytes = base64.b64decode(image_data)
        else:
            image_bytes = image_data

        with open(image_path, "wb") as f:
            f.write(image_bytes)

        logger.info(f"[IMAGE_GEN] Saved profile photo: {image_path}")

        return {
            "success": True,
            "image_path": str(image_path),
            "filename": filename,
            "prompt_used": prompt[:200] + "..."  # Truncate for logging
        }

    except Exception as e:
        logger.error(f"[IMAGE_GEN] Generation failed: {e}")
        return {
            "success": False,
            "error": str(e)
        }


async def generate_profile_photo_for_persona(persona_description: str, profile_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Generate a profile photo based on a natural language persona description.

    Args:
        persona_description: e.g., "middle-aged white woman, brunette, friendly"
        profile_name: Optional profile name for filename

    Returns:
        Dict with success status and image path
    """
    # Parse persona into structured attributes
    desc_lower = persona_description.lower()

    # Detect gender
    if any(w in desc_lower for w in ["woman", "female", "lady", "girl", "she"]):
        gender = "woman"
    elif any(w in desc_lower for w in ["man", "male", "guy", "he"]):
        gender = "man"
    else:
        gender = "woman"  # Default

    # Detect age range
    if any(w in desc_lower for w in ["young", "20s", "twenties"]):
        age_range = "25-32"
    elif any(w in desc_lower for w in ["middle-aged", "middle aged", "40s", "forties"]):
        age_range = "40-50"
    elif any(w in desc_lower for w in ["older", "senior", "50s", "60s"]):
        age_range = "55-65"
    else:
        age_range = "30-40"  # Default

    # Detect ethnicity
    if any(w in desc_lower for w in ["black", "african"]):
        ethnicity = "african american"
    elif any(w in desc_lower for w in ["asian", "chinese", "japanese", "korean"]):
        ethnicity = "asian"
    elif any(w in desc_lower for w in ["latina", "hispanic", "mexican"]):
        ethnicity = "latina"
    elif any(w in desc_lower for w in ["indian", "south asian"]):
        ethnicity = "south asian"
    else:
        ethnicity = "caucasian"  # Default

    # Pass the full description as extra details for hair, style, etc.
    return await generate_profile_photo(
        gender=gender,
        age_range=age_range,
        ethnicity=ethnicity,
        extra_details=f"Additional details from persona: {persona_description}",
        profile_name=profile_name
    )


async def generate_profile_photo_with_reference(
    reference_image_base64: str,
    pose_prompt: str,
    profile_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Generate a new photo of the same person in a different pose/setting.
    Uses the reference image to preserve the person's identity/face.

    Args:
        reference_image_base64: The current profile picture (base64 PNG/JPEG)
        pose_prompt: Description of the new pose/setting
        profile_name: Optional profile name for filename

    Returns:
        Dict with:
            - success: bool
            - image_path: str (path to saved image)
            - base64_image: str (base64 encoded result for session storage)
            - error: str (if failed)
    """
    try:
        client = get_image_client()

        # Decode base64 to PIL Image
        # Handle both raw base64 and data URL format
        if reference_image_base64.startswith("data:"):
            # Strip data URL prefix (e.g., "data:image/png;base64,")
            base64_data = reference_image_base64.split(",", 1)[1]
        else:
            base64_data = reference_image_base64

        image_bytes = base64.b64decode(base64_data)
        reference_image = Image.open(io.BytesIO(image_bytes))

        # Build the prompt for identity-preserving generation
        full_prompt = f"""Generate a realistic iPhone selfie photo of this EXACT same person.

IDENTITY PRESERVATION (CRITICAL - HIGHEST PRIORITY):
- This MUST be the EXACT same person as in the reference image
- Preserve their face shape, eye color, skin tone, hair color, and all distinctive features
- The generated image should look like a different photo of the SAME person, not a different person
- Do NOT change their ethnicity, age, or fundamental facial features

NEW POSE/SETTING:
{pose_prompt}

PHOTO STYLE REQUIREMENTS:
- iPhone 15 Pro front camera selfie
- Natural, candid, casual expression with genuine emotion
- Slight imperfections: flyaway hairs, real skin texture, natural pores
- NO professional studio lighting
- NO heavy filters or editing
- Appropriate clothing for the setting

ANTI-AI TELLS TO AVOID:
- No waxy or overly smooth skin
- No perfectly symmetrical features
- No uncanny valley expressions
- No blurred or melted ears/jewelry/fingers
- No text or watermarks
- No unnatural eye reflections

ASPECT RATIO: 1:1 square (for profile picture use)
OUTPUT: Single photo of the same person in the new setting"""

        logger.info(f"[IMAGE_GEN] Generating reference-based photo for: {profile_name}")
        logger.debug(f"[IMAGE_GEN] Pose prompt: {pose_prompt}")

        # Generate image with reference
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=IMAGE_MODEL,
            contents=[full_prompt, reference_image],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
            )
        )

        # Debug: Log the raw response structure
        logger.info(f"[IMAGE_GEN] Response received, checking structure...")

        # Check if response has candidates with content
        if hasattr(response, 'candidates') and response.candidates:
            candidate = response.candidates[0]
            if hasattr(candidate, 'finish_reason'):
                logger.info(f"[IMAGE_GEN] Finish reason: {candidate.finish_reason}")
            if hasattr(candidate, 'safety_ratings'):
                logger.info(f"[IMAGE_GEN] Safety ratings: {candidate.safety_ratings}")

        # Check for prompt feedback (policy blocks)
        if hasattr(response, 'prompt_feedback'):
            logger.warning(f"[IMAGE_GEN] Prompt feedback: {response.prompt_feedback}")

        # Extract image from response
        image_data = None
        parts = getattr(response, 'parts', None)
        if parts is None:
            # Try to get parts from candidates
            if hasattr(response, 'candidates') and response.candidates:
                candidate = response.candidates[0]
                if hasattr(candidate, 'content') and candidate.content:
                    parts = getattr(candidate.content, 'parts', [])

        if parts:
            for part in parts:
                if hasattr(part, 'inline_data') and part.inline_data is not None:
                    image_data = part.inline_data.data
                    break

        if not image_data:
            # Check for text response (might be an error message)
            text_response = ""
            if parts:
                for part in parts:
                    if hasattr(part, 'text') and part.text:
                        text_response = part.text
                        break

            error_msg = f"No image generated. Response: {text_response[:200] if text_response else 'Empty response'}"
            logger.error(f"[IMAGE_GEN] {error_msg}")
            return {
                "success": False,
                "error": error_msg
            }

        # Save image
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename_base = profile_name or f"profile_{uuid.uuid4().hex[:8]}"
        filename = f"{filename_base}_{timestamp}.png"
        image_path = IMAGE_OUTPUT_DIR / filename

        # Decode if needed and save
        if isinstance(image_data, str):
            image_bytes_out = base64.b64decode(image_data)
            base64_result = image_data
        else:
            image_bytes_out = image_data
            base64_result = base64.b64encode(image_data).decode("utf-8")

        with open(image_path, "wb") as f:
            f.write(image_bytes_out)

        logger.info(f"[IMAGE_GEN] Saved reference-based photo: {image_path}")

        return {
            "success": True,
            "image_path": str(image_path),
            "filename": filename,
            "base64_image": base64_result,
            "pose_prompt": pose_prompt[:100] + "..." if len(pose_prompt) > 100 else pose_prompt
        }

    except Exception as e:
        logger.error(f"[IMAGE_GEN] Reference-based generation failed: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def get_random_pose() -> Dict[str, str]:
    """Get a random pose from the variations pool."""
    return random.choice(POSE_VARIATIONS)


def get_pose_by_name(name: str) -> Optional[Dict[str, str]]:
    """Get a specific pose by name."""
    for pose in POSE_VARIATIONS:
        if pose["name"] == name:
            return pose
    return None


# Convenience function for direct testing
if __name__ == "__main__":
    import sys

    async def test():
        result = await generate_profile_photo_for_persona(
            persona_description="friendly middle-aged white woman with brown hair",
            profile_name="test_profile"
        )
        print(f"Result: {result}")

    asyncio.run(test())
