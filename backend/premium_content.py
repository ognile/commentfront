"""
Content generation for premium feed/group posts.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import random
import re
import uuid
from difflib import SequenceMatcher
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from fb_session import FacebookSession
from gemini_image_gen import generate_profile_photo_with_reference
from premium_rules import (
    enforce_casing_mode,
    sanitize_text_against_rules,
    validate_text_against_rules,
)

logger = logging.getLogger("PremiumContent")

try:
    from google import genai
    from google.genai import types
except Exception:  # pragma: no cover - optional import in tests
    genai = None
    types = None


IMAGE_OUTPUT_DIR = Path(os.getenv("PREMIUM_IMAGE_OUTPUT_DIR", "/tmp/premium_media"))
IMAGE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


CHARACTER_CAPTION_TEMPLATES = [
    "got outside for a short walk and it helped me clear my head.",
    "slow start today, tea in hand, and i finally felt settled again.",
    "focused on one task at a time and that made the day lighter.",
    "kept my plans simple and ended up feeling more grounded.",
    "chose rest over rushing and my energy thanked me for it.",
    "a quick stretch and fresh air changed the whole mood of my afternoon.",
    "finished a few small chores and felt proud of that consistency.",
    "took a quiet break between errands and it helped me reset.",
    "simple meals, calm music, and a steadier rhythm today.",
    "gave myself permission to move slower and it worked.",
    "a little sunshine and a shorter to-do list made a real difference.",
    "kept my morning gentle and the rest of the day flowed better.",
    "prioritized sleep last night and noticed the difference today.",
    "chose a shorter walk and still felt more balanced afterward.",
    "stayed off the noise for a bit and felt more present.",
    "did less, but did it with intention, and that felt right.",
    "small routine wins stacked up and helped my mood stay steady.",
    "ended the day calmer than i started, which feels like progress.",
]

AMBIENT_CAPTION_TEMPLATES = [
    "this small scene felt calmer than i expected today.",
    "a quiet detail from my day that made me pause for a moment.",
    "something about this light made everything feel softer.",
    "sharing a peaceful little moment from today.",
    "this view felt simple and grounding so i wanted to post it.",
    "a quiet snapshot that matched my mood today.",
    "just a gentle part of the day that stayed with me.",
    "not dramatic, just one calm moment i appreciated.",
    "this tiny everyday scene felt surprisingly comforting.",
    "a soft moment that helped me slow down for a second.",
    "captured this because it felt steady and peaceful.",
    "an ordinary moment that felt good enough to share.",
]


async def _generate_ambient_image(prompt: str, profile_name: str) -> Dict:
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key or not genai or not types:
        return {"success": False, "error": "gemini image generation unavailable"}

    client = genai.Client(api_key=api_key)
    model = os.getenv("PREMIUM_IMAGE_MODEL", "gemini-3-pro-image-preview")

    full_prompt = (
        "Generate a realistic candid lifestyle photo for social media. "
        "No text overlays, no watermarks, no AI artifacts. "
        "Scene should feel natural and ordinary. "
        f"Context: {prompt}"
    )

    try:
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=model,
            contents=[full_prompt],
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
            return {"success": False, "error": "no ambient image in response"}

        if isinstance(image_data, str):
            image_bytes = base64.b64decode(image_data)
        else:
            image_bytes = image_data

        filename = f"ambient_{profile_name.replace(' ', '_').lower()}_{uuid.uuid4().hex[:8]}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.png"
        path = IMAGE_OUTPUT_DIR / filename
        path.write_bytes(image_bytes)

        return {"success": True, "image_path": str(path)}
    except Exception as exc:
        logger.error(f"Ambient image generation failed: {exc}")
        return {"success": False, "error": str(exc)}


def _build_caption(post_kind: str, cycle_index: int) -> str:
    pool = CHARACTER_CAPTION_TEMPLATES if post_kind == "character" else AMBIENT_CAPTION_TEMPLATES
    rng = random.Random(f"{cycle_index}:{post_kind}:v2")
    return rng.choice(pool)


def _strip_prompt_tail(caption: str) -> str:
    """
    Remove obvious prompt/policy leakage tails before posting.
    """
    cleaned = str(caption or "").strip()
    tail_patterns = [
        r"\s*supportive middle-aged woman navigating menopause with practical optimism\.?\s*$",
        r"\s*supportive woman in menopause community\.?\s*$",
        r"\s*persona[:\-].*$",
        r"\s*prompt[:\-].*$",
    ]
    for pattern in tail_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _normalize_caption(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def _caption_similarity(left: str, right: str) -> float:
    a = _normalize_caption(left)
    b = _normalize_caption(right)
    if not a or not b:
        return 0.0
    return float(SequenceMatcher(None, a, b).ratio())


def _dedupe_caption_candidates(base: str, pool: List[str], recent_captions: List[str], *, threshold: float = 0.90) -> str:
    """
    Avoid repeating a recently used caption when alternatives exist.
    """
    recent = [str(item).strip() for item in recent_captions if str(item).strip()]
    if not recent:
        return base

    def _is_safe(candidate: str) -> bool:
        candidate_text = str(candidate or "").strip()
        if not candidate_text:
            return False
        for previous in recent:
            if _caption_similarity(candidate_text, previous) >= float(threshold):
                return False
        return True

    if _is_safe(base):
        return base

    for candidate in pool:
        if _is_safe(candidate):
            return candidate
    return base


async def generate_post_bundle(
    *,
    profile_name: str,
    profile_config: Dict,
    post_kind: str,
    cycle_index: int,
    rules_snapshot: Optional[Dict],
    require_image: bool = False,
) -> Dict:
    """
    Generate text and image for one cycle post bundle.
    """
    character_profile = profile_config.get("character_profile", {})
    execution_policy = profile_config.get("execution_policy", {})
    content_policy = profile_config.get("content_policy", {})

    casing_mode = str(content_policy.get("casing_mode", "natural_mixed"))
    recent_captions = character_profile.get("recent_captions") or []

    caption_pool = CHARACTER_CAPTION_TEMPLATES if post_kind == "character" else AMBIENT_CAPTION_TEMPLATES
    caption = _build_caption(post_kind=post_kind, cycle_index=cycle_index)
    caption = _dedupe_caption_candidates(caption, caption_pool, recent_captions)
    caption = _strip_prompt_tail(caption)
    caption = enforce_casing_mode(caption, casing_mode)
    validation = validate_text_against_rules(caption, rules_snapshot)

    if not validation.get("ok"):
        sanitized = sanitize_text_against_rules(caption, rules_snapshot)
        sanitized = enforce_casing_mode(sanitized, casing_mode)
        validation_after = validate_text_against_rules(sanitized, rules_snapshot)
        caption = sanitized
        validation = {
            **validation_after,
            "auto_sanitized": True,
        }

    if not validation.get("ok"):
        return {
            "success": False,
            "error": "generated text violates writing rules",
            "caption": caption,
            "rules_validation": validation,
            "post_kind": post_kind,
        }

    if not require_image:
        return {
            "success": True,
            "post_kind": post_kind,
            "caption": caption,
            "rules_validation": validation,
            "image_path": None,
            "image_generation": {"skipped": True, "reason": "image_not_required"},
            "text_only_fallback": True,
        }

    image_result: Dict

    if post_kind == "character":
        session = FacebookSession(profile_name)
        if not session.load():
            return {
                "success": False,
                "error": f"session not found for {profile_name}",
                "caption": caption,
                "rules_validation": validation,
                "post_kind": post_kind,
            }

        reference_mode = character_profile.get("reference_image_mode", "session_profile_picture")
        if reference_mode == "manual_reference":
            reference_image = character_profile.get("manual_reference_image_base64")
        else:
            reference_image = session.data.get("profile_picture") if session.data else None

        if not reference_image:
            return {
                "success": False,
                "error": "reference image missing for character post",
                "caption": caption,
                "rules_validation": validation,
                "post_kind": post_kind,
            }

        hints = character_profile.get("character_prompt_hints") or ["at home with natural light"]
        pose_prompt = random.choice(hints)
        image_result = await generate_profile_photo_with_reference(
            reference_image_base64=reference_image,
            pose_prompt=pose_prompt,
            profile_name=profile_name.replace(" ", "_").lower(),
        )
    else:
        hints = character_profile.get("ambient_prompt_hints") or [
            "calm lifestyle scene in a women-focused support context",
        ]
        prompt = random.choice(hints)
        image_result = await _generate_ambient_image(prompt=prompt, profile_name=profile_name)

    if not image_result.get("success"):
        if execution_policy.get("allow_text_only_if_image_fails"):
            return {
                "success": True,
                "post_kind": post_kind,
                "caption": caption,
                "rules_validation": validation,
                "image_path": None,
                "image_generation": image_result,
                "text_only_fallback": True,
            }

        return {
            "success": False,
            "error": f"image generation failed: {image_result.get('error')}",
            "post_kind": post_kind,
            "caption": caption,
            "rules_validation": validation,
            "image_generation": image_result,
        }

    return {
        "success": True,
        "post_kind": post_kind,
        "caption": caption,
        "rules_validation": validation,
        "image_path": image_result.get("image_path"),
        "image_generation": image_result,
    }


def cleanup_generated_image(path: Optional[str]) -> None:
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        pass
