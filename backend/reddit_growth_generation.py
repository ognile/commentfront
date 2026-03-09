"""
reddit growth-program content generation and validation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from config import GEMINI_API_KEY, GEMINI_MODEL

try:
    from google import genai
    from google.genai import types
except Exception:  # pragma: no cover - optional import in tests
    genai = None
    types = None


logger = logging.getLogger("RedditGrowthGeneration")

WRITING_RULE_SOURCE_PATHS = [
    "/Users/nikitalienov/Documents/writing/.claude/rules/great-writing-patterns.md",
    "/Users/nikitalienov/Documents/writing/.claude/rules/negative-patterns.md",
    "/Users/nikitalienov/Documents/writing/.claude/rules/vocabulary-guidance.md",
]

GOOD_WRITING_PRINCIPLES = [
    "write in lowercase and keep the tone human, direct, and grounded",
    "use concrete details instead of vague abstractions",
    "keep emotional velocity, but stay believable and human-scale",
    "let each sentence pull naturally into the next",
    "prefer 'this' over 'that' when it fits",
    "keep replies helpful, specific, and relevant to the thread or subreddit",
    "avoid over-selling, preachiness, or generic motivational language",
]

BANNED_PATTERNS = [
    "the result?",
    "here's the thing:",
    "the best part?",
    "isn't just",
    "no x, no y, just z",
    "it's not just",
    "it's not about",
    "not because",
    "that's not",
    "where x meets y",
    "but here's the thing:",
    "and honestly?",
    "here's what they don't tell you",
    "they don't want you to know",
    "real talk:",
    "here's the deal:",
    "here’s the kicker:",
]

BANNED_VOCABULARY = [
    "free balling",
    "just ride through it",
    "stuff",
    "hubby",
    "put on weight",
    "randomly",
    "tolerating",
    "massive",
    "riding the wave",
    "feels like i'm falling apart",
    "but what i'm learning is",
    "lots of things",
    "everything seemed great at first",
    "quit cold turkey",
    "achey",
    "extremely pissed off",
    "get the cold shoulder",
    "listen to your body",
]


def _collapse_whitespace(value: Optional[str]) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_text(value: Optional[str]) -> str:
    return _collapse_whitespace(value).lower()


def get_writing_rule_snapshot() -> Dict[str, Any]:
    return {
        "source_paths": list(WRITING_RULE_SOURCE_PATHS),
        "positive_principles": list(GOOD_WRITING_PRINCIPLES),
        "banned_patterns": list(BANNED_PATTERNS),
        "banned_vocabulary": list(BANNED_VOCABULARY),
        "style_requirements": {
            "lowercase_only": True,
            "no_em_dash": True,
            "human_scale": True,
            "subreddit_relevant": True,
        },
    }


def validate_generated_text(text: str, *, recent_texts: Optional[List[str]] = None) -> Dict[str, Any]:
    normalized = _normalize_text(text)
    violations: List[str] = []

    if not normalized:
        violations.append("text is empty")
    if "—" in str(text or ""):
        violations.append("contains em dash")

    for phrase in BANNED_PATTERNS:
        phrase_norm = _normalize_text(phrase)
        if phrase_norm and phrase_norm in normalized:
            violations.append(f"contains banned pattern: {phrase}")

    for phrase in BANNED_VOCABULARY:
        phrase_norm = _normalize_text(phrase)
        if phrase_norm and phrase_norm in normalized:
            violations.append(f"contains banned vocabulary: {phrase}")

    for previous in list(recent_texts or []):
        if normalized and normalized == _normalize_text(previous):
            violations.append("duplicates prior generated text in this program")
            break

    lowered_source = str(text or "")
    if lowered_source != lowered_source.lower():
        violations.append("text is not fully lowercase")

    return {
        "ok": not violations,
        "violations": violations,
        "normalized_text": normalized,
    }


def _response_text(response: Any) -> str:
    text = str(getattr(response, "text", "") or "").strip()
    if text:
        return text
    candidates = getattr(response, "candidates", None) or []
    for candidate in candidates:
        parts = getattr(getattr(candidate, "content", None), "parts", None) or []
        chunks = [str(getattr(part, "text", "") or "").strip() for part in parts]
        text = "\n".join(chunk for chunk in chunks if chunk).strip()
        if text:
            return text
    return ""


def _extract_json_object(raw_text: str) -> Dict[str, Any]:
    cleaned = str(raw_text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        cleaned = cleaned[start : end + 1]
    return json.loads(cleaned)


def summarize_style_samples(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    excerpts = []
    for sample in samples[:3]:
        excerpts.append(
            {
                "url": sample.get("target_url") or sample.get("target_comment_url"),
                "title": _collapse_whitespace(sample.get("title")),
                "excerpt": _collapse_whitespace(sample.get("body_excerpt") or sample.get("body") or sample.get("selftext")),
                "score": sample.get("score"),
                "comment_count": sample.get("comment_count"),
            }
        )
    return {
        "sample_count": len(samples),
        "samples": excerpts,
    }


@dataclass
class GenerationResult:
    success: bool
    kind: str
    text: Optional[str] = None
    title: Optional[str] = None
    body: Optional[str] = None
    raw_response: Optional[str] = None
    validation: Optional[Dict[str, Any]] = None
    style_summary: Optional[Dict[str, Any]] = None
    sample_urls: Optional[List[str]] = None
    error: Optional[str] = None


class RedditGrowthContentGenerator:
    def __init__(self, *, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = str(api_key or GEMINI_API_KEY or "").strip()
        self.model = str(model or os.getenv("REDDIT_PROGRAM_GENERATION_MODEL") or GEMINI_MODEL or "gemini-3-flash-preview").strip()
        self.enabled = bool(self.api_key and genai and types)
        self.client = genai.Client(api_key=self.api_key) if self.enabled else None

    async def _generate_json(self, prompt: str) -> str:
        if not self.enabled or not self.client:
            raise RuntimeError("reddit content generation is unavailable")
        response = await asyncio.to_thread(
            self.client.models.generate_content,
            model=self.model,
            contents=[prompt],
            config=types.GenerateContentConfig(
                temperature=0.9,
                top_p=0.95,
                response_mime_type="application/json",
            ),
        )
        result_text = _response_text(response)
        if not result_text:
            raise RuntimeError("generation response was empty")
        return result_text

    def _reply_prompt(
        self,
        *,
        subreddit: str,
        target_excerpt: str,
        target_author: Optional[str],
        keywords: List[str],
        style_samples: List[Dict[str, Any]],
        recent_texts: List[str],
    ) -> str:
        return f"""
you are writing a single reddit reply for r/{subreddit}.

hard rules:
- output valid json only
- all text must be lowercase
- no em dash
- do not use any banned pattern or banned vocabulary
- make it sound like a normal, thoughtful person
- do not repeat exact wording from prior generations
- keep the reply relevant to the target comment and subreddit
- 1 to 3 sentences max

positive writing principles:
{json.dumps(GOOD_WRITING_PRINCIPLES, ensure_ascii=True, indent=2)}

banned patterns:
{json.dumps(BANNED_PATTERNS, ensure_ascii=True, indent=2)}

banned vocabulary:
{json.dumps(BANNED_VOCABULARY, ensure_ascii=True, indent=2)}

topic keywords:
{json.dumps(keywords, ensure_ascii=True)}

target comment:
{json.dumps({"author": target_author, "excerpt": target_excerpt}, ensure_ascii=True, indent=2)}

style samples from high-traction posts/comments:
{json.dumps(summarize_style_samples(style_samples), ensure_ascii=True, indent=2)}

prior generated texts to avoid duplicating:
{json.dumps(recent_texts[-12:], ensure_ascii=True, indent=2)}

return json with this exact shape:
{{
  "text": "the reply text",
  "reasoning": "one short sentence on why it fits"
}}
""".strip()

    def _post_prompt(
        self,
        *,
        subreddit: str,
        keywords: List[str],
        style_samples: List[Dict[str, Any]],
        recent_texts: List[str],
    ) -> str:
        return f"""
you are writing a new reddit post for r/{subreddit}.

hard rules:
- output valid json only
- all text must be lowercase
- no em dash
- do not use any banned pattern or banned vocabulary
- make it sound like a normal user posting in a women's-health-related subreddit
- do not repeat exact wording from prior generations
- keep it helpful, believable, and human-scale
- title should be specific and natural
- body can be empty or 1 to 4 short sentences

positive writing principles:
{json.dumps(GOOD_WRITING_PRINCIPLES, ensure_ascii=True, indent=2)}

banned patterns:
{json.dumps(BANNED_PATTERNS, ensure_ascii=True, indent=2)}

banned vocabulary:
{json.dumps(BANNED_VOCABULARY, ensure_ascii=True, indent=2)}

topic keywords:
{json.dumps(keywords, ensure_ascii=True)}

style samples from high-traction posts/comments:
{json.dumps(summarize_style_samples(style_samples), ensure_ascii=True, indent=2)}

prior generated texts to avoid duplicating:
{json.dumps(recent_texts[-12:], ensure_ascii=True, indent=2)}

return json with this exact shape:
{{
  "title": "post title",
  "body": "optional post body",
  "reasoning": "one short sentence on why it fits"
}}
""".strip()

    async def generate_reply(
        self,
        *,
        subreddit: str,
        target_excerpt: str,
        target_author: Optional[str],
        keywords: List[str],
        style_samples: List[Dict[str, Any]],
        recent_texts: List[str],
        max_attempts: int = 3,
    ) -> GenerationResult:
        style_summary = summarize_style_samples(style_samples)
        sample_urls = [str(sample.get("target_url") or sample.get("target_comment_url") or "").strip() for sample in style_samples if str(sample.get("target_url") or sample.get("target_comment_url") or "").strip()]
        last_error = "generation failed"
        for _attempt in range(max_attempts):
            raw = await self._generate_json(
                self._reply_prompt(
                    subreddit=subreddit,
                    target_excerpt=target_excerpt,
                    target_author=target_author,
                    keywords=keywords,
                    style_samples=style_samples,
                    recent_texts=recent_texts,
                )
            )
            try:
                payload = _extract_json_object(raw)
            except Exception as exc:
                last_error = f"generation returned invalid json: {exc}"
                continue
            text = _collapse_whitespace(payload.get("text"))
            validation = validate_generated_text(text, recent_texts=recent_texts)
            if validation["ok"]:
                return GenerationResult(
                    success=True,
                    kind="reply_comment",
                    text=text,
                    raw_response=raw,
                    validation=validation,
                    style_summary=style_summary,
                    sample_urls=sample_urls,
                )
            last_error = "; ".join(validation["violations"])
            recent_texts = list(recent_texts) + [text]
        return GenerationResult(
            success=False,
            kind="reply_comment",
            raw_response=None,
            validation={"ok": False, "violations": [last_error]},
            style_summary=style_summary,
            sample_urls=sample_urls,
            error=last_error,
        )

    async def generate_post(
        self,
        *,
        subreddit: str,
        keywords: List[str],
        style_samples: List[Dict[str, Any]],
        recent_texts: List[str],
        max_attempts: int = 3,
    ) -> GenerationResult:
        style_summary = summarize_style_samples(style_samples)
        sample_urls = [str(sample.get("target_url") or sample.get("target_comment_url") or "").strip() for sample in style_samples if str(sample.get("target_url") or sample.get("target_comment_url") or "").strip()]
        last_error = "generation failed"
        for _attempt in range(max_attempts):
            raw = await self._generate_json(
                self._post_prompt(
                    subreddit=subreddit,
                    keywords=keywords,
                    style_samples=style_samples,
                    recent_texts=recent_texts,
                )
            )
            try:
                payload = _extract_json_object(raw)
            except Exception as exc:
                last_error = f"generation returned invalid json: {exc}"
                continue
            title = _collapse_whitespace(payload.get("title"))
            body = _collapse_whitespace(payload.get("body"))
            combined = f"{title}\n{body}".strip()
            validation = validate_generated_text(combined, recent_texts=recent_texts)
            if title and validation["ok"]:
                return GenerationResult(
                    success=True,
                    kind="create_post",
                    title=title,
                    body=body,
                    raw_response=raw,
                    validation=validation,
                    style_summary=style_summary,
                    sample_urls=sample_urls,
                )
            last_error = "; ".join(validation["violations"] or ["title is required"])
            recent_texts = list(recent_texts) + [combined]
        return GenerationResult(
            success=False,
            kind="create_post",
            raw_response=None,
            validation={"ok": False, "violations": [last_error]},
            style_summary=style_summary,
            sample_urls=sample_urls,
            error=last_error,
        )
