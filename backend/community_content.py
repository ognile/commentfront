"""Warmup content generation for community simulation personas."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import random
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("CommunityContent")

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

IMAGE_OUTPUT_DIR = Path(os.getenv("DEBUG_DIR", "/data/debug"))

TOPIC_POOL = [
    "morning routine — what you did first thing today",
    "gratitude — one specific thing you're thankful for",
    "small win — something that went right today",
    "nature — something beautiful you noticed outside",
    "cooking — a meal you made or are craving",
    "family — a memory or moment with someone close",
    "health journey — how your body feels today",
    "afternoon walk — what you saw, heard, or thought about",
    "reading — something you read that stuck with you",
    "evening reflection — how the day landed",
    "a friend you reconnected with recently",
    "something you're learning for the first time",
    "a place you visited that surprised you",
    "a recipe you tried and how it turned out",
    "your morning coffee or tea ritual",
]

# Content rules from /Users/nikitalienov/Documents/writing/.claude/rules/
CONTENT_RULES = """
STRICT RULES for writing style:
- NEVER use em dashes (—). Use periods or commas instead.
- NEVER use these AI words: leverage, utilize, navigate, landscape, delve, foster, resonate, elevate, empower, unlock, harness, curate, pivotal, cutting-edge, game-changer, realm, embark, testament, paradigm shift, synergy, streamline, multifaceted, spearhead, underscores
- NEVER use these patterns: "Here's the thing:", "The result? Y.", "The best part? Y.", "X isn't just Y, it's Z.", "Where X meets Y.", "But here's the kicker:", "Real talk:", "Here's the deal:", "And honestly? X."
- NEVER use cliché phrases: "Life is good!", "Listen to your body", "riding the wave", "changed everything", "from the inside out", "feels like i'm falling apart", "but what i'm learning is..."
- Use "this" not "that" in most cases
- Casual, authentic tone. lowercase is fine. these are real women sharing their lives.
- 1-3 sentences max. short and genuine.
- No hashtags. No emojis unless it's a single natural one.
- Every sentence should feel like it sets up curiosity for the next.
- Be specific: use real details (a name, a time, a place, a number).
"""


async def generate_warmup_post(
    persona: Dict[str, Any],
    day_index: int,
    recent_captions: Optional[List[str]] = None,
    force_image: Optional[bool] = None,
) -> Dict[str, Any]:
    """Generate a warmup post (text + optional image) for a persona.

    Returns: {"text": str, "image_path": str|None, "topic": str}
    """
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key or not genai or not types:
        return {"text": "", "image_path": None, "error": "gemini unavailable"}

    topic = random.choice(TOPIC_POOL)
    persona_prompt = persona.get("persona_prompt", "a woman in her 50s")
    age = persona.get("age", 50)

    recent_text = ""
    if recent_captions:
        last_few = recent_captions[-5:]
        recent_text = f"\nDo NOT repeat or rephrase any of these recent posts:\n" + "\n".join(f"- {c}" for c in last_few)

    prompt = f"""You are {persona_prompt}, age {age}. Write a short Facebook post about: {topic}

{CONTENT_RULES}
{recent_text}

Write ONLY the post text. Nothing else. No quotes around it."""

    client = genai.Client(api_key=api_key)
    model = os.getenv("COMMUNITY_TEXT_MODEL", "gemini-2.5-flash")

    try:
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=model,
            contents=[prompt],
        )
        text = (response.text or "").strip().strip('"').strip("'")
        if not text:
            return {"text": "", "image_path": None, "error": "empty response from gemini"}
    except Exception as exc:
        logger.error(f"warmup text generation failed for {persona.get('profile_name')}: {exc}")
        return {"text": "", "image_path": None, "error": str(exc)}

    # Generate image: force_image=True always, force_image=False never, None = 50% random
    image_path = None
    should_generate_image = force_image if force_image is not None else (random.random() < 0.5)
    if should_generate_image:
        image_result = await _generate_warmup_image(persona, topic)
        if image_result.get("success"):
            image_path = image_result["image_path"]

    return {"text": text, "image_path": image_path, "topic": topic}


async def _generate_warmup_image(persona: Dict[str, Any], topic: str) -> Dict[str, Any]:
    """Generate a lifestyle image for a warmup post."""
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key or not genai or not types:
        return {"success": False, "error": "gemini unavailable"}

    client = genai.Client(api_key=api_key)
    model = os.getenv("PREMIUM_IMAGE_MODEL", "gemini-3-pro-image-preview")

    # Mix selfies and scenery
    is_selfie = random.random() < 0.4
    persona_prompt = persona.get("persona_prompt", "a woman in her 50s")
    hints = persona.get("image_style_hints", [])
    hint_text = f" Style hints: {', '.join(hints)}." if hints else ""

    if is_selfie:
        image_prompt = (
            f"Realistic casual iPhone selfie of {persona_prompt}. "
            f"Context: {topic}. "
            f"Natural lighting, no makeup or minimal, candid expression. "
            f"No text overlays, no watermarks, no AI artifacts.{hint_text}"
        )
    else:
        image_prompt = (
            f"Realistic candid lifestyle photo for social media. "
            f"Scene: {topic}. No people visible. "
            f"Natural lighting, ordinary everyday setting. "
            f"No text overlays, no watermarks, no AI artifacts.{hint_text}"
        )

    try:
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=model,
            contents=[image_prompt],
            config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
        )

        image_data = None
        parts = getattr(response, "parts", None)
        if parts is None and getattr(response, "candidates", None):
            candidate = response.candidates[0]
            parts = getattr(getattr(candidate, "content", None), "parts", None)

        if parts:
            for part in parts:
                inline = getattr(part, "inline_data", None)
                if inline is not None:
                    image_data = inline.data
                    break

        if not image_data:
            return {"success": False, "error": "no image in response"}

        if isinstance(image_data, str):
            image_bytes = base64.b64decode(image_data)
        else:
            image_bytes = image_data

        profile_name = persona.get("profile_name", "unknown")
        filename = f"warmup_{profile_name}_{uuid.uuid4().hex[:8]}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.png"
        path = IMAGE_OUTPUT_DIR / filename
        path.write_bytes(image_bytes)

        return {"success": True, "image_path": str(path)}
    except Exception as exc:
        logger.error(f"warmup image generation failed: {exc}")
        return {"success": False, "error": str(exc)}
