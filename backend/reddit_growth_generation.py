"""
reddit growth-program content generation and validation.
"""

from __future__ import annotations

import asyncio
import collections
import difflib
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from config import GEMINI_API_KEY, GEMINI_MODEL
from reddit_persona_registry import get_reddit_persona_snapshot
from reddit_writing_rules import WRITING_RULE_SOURCE_PATHS, get_writing_rule_snapshot

try:
    from google import genai
    from google.genai import types
except Exception:  # pragma: no cover - optional import in tests
    genai = None
    types = None


logger = logging.getLogger("RedditGrowthGeneration")

STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "by",
    "for",
    "from",
    "have",
    "how",
    "i",
    "if",
    "in",
    "is",
    "it",
    "me",
    "my",
    "of",
    "on",
    "or",
    "so",
    "that",
    "the",
    "this",
    "to",
    "was",
    "with",
    "you",
    "your",
}


def _collapse_whitespace(value: Optional[str]) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_text(value: Optional[str]) -> str:
    return _collapse_whitespace(value).lower()


def _word_count(value: Optional[str]) -> int:
    return len(re.findall(r"\b[\w']+\b", str(value or "")))


def _meaningful_tokens(value: Optional[str]) -> List[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9']+", _normalize_text(value))
        if len(token) >= 3 and token not in STOPWORDS
    ]


def _lead_tokens(value: Optional[str], *, limit: int = 3) -> List[str]:
    return re.findall(r"[a-z0-9']+", _normalize_text(value))[:limit]


def _term_frequencies(texts: List[str]) -> Dict[str, int]:
    counter: collections.Counter[str] = collections.Counter()
    for text in texts:
        counter.update(_meaningful_tokens(text))
    return dict(counter)


def _top_context_terms(texts: List[str], limit: int = 10) -> List[str]:
    frequencies = _term_frequencies(texts)
    return [term for term, _count in sorted(frequencies.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def _context_anchor_terms(texts: List[str], limit: int = 16) -> List[str]:
    frequencies = _term_frequencies(texts)
    return [term for term, _count in sorted(frequencies.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def _token_overlap(left: str, right: str) -> float:
    left_tokens = set(_meaningful_tokens(left))
    right_tokens = set(_meaningful_tokens(right))
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    return intersection / max(1, min(len(left_tokens), len(right_tokens)))


def _ngram_overlap(left: str, right: str, *, size: int = 3) -> float:
    left_tokens = re.findall(r"[a-z0-9']+", _normalize_text(left))
    right_tokens = re.findall(r"[a-z0-9']+", _normalize_text(right))
    if len(left_tokens) < size or len(right_tokens) < size:
        return 0.0
    left_ngrams = {" ".join(left_tokens[index:index + size]) for index in range(len(left_tokens) - size + 1)}
    right_ngrams = {" ".join(right_tokens[index:index + size]) for index in range(len(right_tokens) - size + 1)}
    if not left_ngrams or not right_ngrams:
        return 0.0
    return len(left_ngrams & right_ngrams) / max(1, min(len(left_ngrams), len(right_ngrams)))


def _sequence_ratio(left: str, right: str) -> float:
    return difflib.SequenceMatcher(None, _normalize_text(left), _normalize_text(right)).ratio()


def _shared_opening(left: str, right: str, *, size: int = 2) -> bool:
    left_lead = _lead_tokens(left, limit=size)
    right_lead = _lead_tokens(right, limit=size)
    return len(left_lead) == size and left_lead == right_lead


def summarize_conversation_context(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    texts: List[str] = []
    summary_samples: List[Dict[str, Any]] = []
    for sample in list(samples or [])[:4]:
        title = _collapse_whitespace(sample.get("title"))
        excerpt = _collapse_whitespace(
            sample.get("body_excerpt")
            or sample.get("body")
            or sample.get("selftext")
            or sample.get("excerpt")
        )
        author = _collapse_whitespace(sample.get("author"))
        combined = "\n".join(part for part in [title, excerpt] if part).strip()
        if combined:
            texts.append(combined)
        summary_samples.append(
            {
                "url": sample.get("target_url") or sample.get("target_comment_url") or sample.get("thread_url"),
                "title": title,
                "excerpt": excerpt,
                "author": author or None,
                "type": sample.get("type") or None,
            }
        )
    return {
        "sample_count": len(summary_samples),
        "top_terms": _top_context_terms(texts),
        "samples": summary_samples,
    }


def _case_style_violation(text: str, case_style: str) -> Optional[str]:
    if case_style == "lowercase":
        if str(text or "") != str(text or "").lower():
            return "text does not match lowercase persona case style"
        return None
    if case_style == "proper_case":
        alpha = [char for char in str(text or "") if char.isalpha()]
        if alpha and not any(char.isupper() for char in alpha):
            return "text does not match proper_case persona case style"
    return None


def _best_similarity(text: str, scope_texts: List[str]) -> Dict[str, Any]:
    best = {
        "matched_text": None,
        "sequence_ratio": 0.0,
        "token_overlap": 0.0,
        "ngram_overlap": 0.0,
        "opening_overlap": False,
        "exact_duplicate": False,
    }
    normalized = _normalize_text(text)
    for previous in list(scope_texts or []):
        previous_text = str(previous or "").strip()
        if not previous_text:
            continue
        candidate = {
            "matched_text": previous_text,
            "sequence_ratio": _sequence_ratio(text, previous_text),
            "token_overlap": _token_overlap(text, previous_text),
            "ngram_overlap": _ngram_overlap(text, previous_text),
            "opening_overlap": _shared_opening(text, previous_text),
            "exact_duplicate": normalized == _normalize_text(previous_text),
        }
        if (
            candidate["exact_duplicate"]
            or candidate["sequence_ratio"] > best["sequence_ratio"]
            or candidate["token_overlap"] > best["token_overlap"]
            or candidate["ngram_overlap"] > best["ngram_overlap"]
        ):
            best = candidate
    return best


def _scope_similarity_violation(scope: str, metrics: Dict[str, Any]) -> Optional[str]:
    if metrics["exact_duplicate"]:
        return {
            "same_program": "duplicates prior generated text in this program",
            "same_thread": "duplicates prior generated text in this thread",
            "same_profile": "duplicates prior generated text for this profile",
            "nearby_context": "duplicates nearby subreddit content",
        }.get(scope, "duplicates prior generated text")
    if scope == "same_thread":
        if metrics["opening_overlap"] and metrics["token_overlap"] >= 0.45:
            return "reuses the same opening move inside this thread"
        if metrics["sequence_ratio"] >= 0.76 or metrics["token_overlap"] >= 0.62 or metrics["ngram_overlap"] >= 0.45:
            return "is too similar to nearby generated text in this thread"
        return None
    if scope == "same_profile":
        if metrics["opening_overlap"]:
            return "reuses the same opening move for this profile"
        if metrics["sequence_ratio"] >= 0.8 or metrics["token_overlap"] >= 0.66 or metrics["ngram_overlap"] >= 0.5:
            return "is too similar to prior generated text for this profile"
        return None
    if scope == "same_program":
        if metrics["opening_overlap"] and metrics["token_overlap"] >= 0.5:
            return "reuses the same opening move in this program"
        if metrics["sequence_ratio"] >= 0.84 or metrics["token_overlap"] >= 0.72 or metrics["ngram_overlap"] >= 0.56:
            return "is too similar to prior generated text in this program"
        return None
    if scope == "nearby_context":
        if metrics["opening_overlap"] and metrics["token_overlap"] >= 0.45:
            return "reuses the same opening move as nearby subreddit content"
        if metrics["sequence_ratio"] >= 0.8 or metrics["token_overlap"] >= 0.68 or metrics["ngram_overlap"] >= 0.52:
            return "is too similar to nearby subreddit content"
    return None


def validate_generated_text(
    text: str,
    *,
    recent_texts: Optional[List[str]] = None,
    nearby_texts: Optional[List[str]] = None,
    same_thread_texts: Optional[List[str]] = None,
    same_profile_texts: Optional[List[str]] = None,
    context_anchor_texts: Optional[List[str]] = None,
    require_context_overlap: bool = False,
    persona_snapshot: Optional[Dict[str, Any]] = None,
    writing_rule_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    normalized = _normalize_text(text)
    violations: List[str] = []
    rule_snapshot = writing_rule_snapshot or get_writing_rule_snapshot()
    persona_snapshot = dict(persona_snapshot or {})
    nearby_texts = [str(item or "").strip() for item in list(nearby_texts or []) if str(item or "").strip()]
    same_thread_texts = [str(item or "").strip() for item in list(same_thread_texts or []) if str(item or "").strip()]
    same_profile_texts = [str(item or "").strip() for item in list(same_profile_texts or []) if str(item or "").strip()]
    recent_texts = [str(item or "").strip() for item in list(recent_texts or []) if str(item or "").strip()]
    context_anchor_texts = [str(item or "").strip() for item in list(context_anchor_texts or []) if str(item or "").strip()]

    if not normalized:
        violations.append("text is empty")
    if "—" in str(text or ""):
        violations.append("contains em dash")

    for phrase in list(rule_snapshot.get("banned_patterns") or []):
        phrase_norm = _normalize_text(phrase)
        if phrase_norm and phrase_norm in normalized:
            violations.append(f"contains banned pattern: {phrase}")

    for phrase in list(rule_snapshot.get("banned_vocabulary") or []):
        phrase_norm = _normalize_text(phrase)
        if phrase_norm and phrase_norm in normalized:
            violations.append(f"contains banned vocabulary: {phrase}")

    for phrase in list(rule_snapshot.get("operator_meta_patterns") or []):
        phrase_norm = _normalize_text(phrase)
        if phrase_norm and phrase_norm in normalized:
            violations.append(f"contains operator/meta language: {phrase}")

    for phrase in list(rule_snapshot.get("generic_filler_patterns") or []):
        phrase_norm = _normalize_text(phrase)
        if phrase_norm and phrase_norm in normalized:
            violations.append(f"contains generic filler: {phrase}")

    case_violation = _case_style_violation(text, str(persona_snapshot.get("case_style") or "").strip())
    if case_violation:
        violations.append(case_violation)

    word_count = _word_count(text)
    length_band = dict(persona_snapshot.get("length_band") or {})
    min_words = int(length_band.get("min_words", 0) or 0)
    max_words = int(length_band.get("max_words", 0) or 0)
    if min_words and word_count < min_words:
        violations.append(f"text is too short for persona length band ({word_count} < {min_words})")
    if max_words and word_count > max_words:
        violations.append(f"text is too long for persona length band ({word_count} > {max_words})")

    similarity_scopes = {
        "same_program": _best_similarity(text, recent_texts),
        "same_thread": _best_similarity(text, same_thread_texts),
        "same_profile": _best_similarity(text, same_profile_texts),
        "nearby_context": _best_similarity(text, nearby_texts),
    }
    for scope, metrics in similarity_scopes.items():
        violation = _scope_similarity_violation(scope, metrics)
        if violation:
            violations.append(violation)

    context_overlap_terms: List[str] = []
    context_anchor_terms: List[str] = []
    if require_context_overlap and (nearby_texts or context_anchor_texts):
        context_terms = set(_top_context_terms(nearby_texts, limit=12))
        context_anchor_terms = _context_anchor_terms(context_anchor_texts, limit=18)
        overlap = sorted(set(_meaningful_tokens(text)) & (context_terms | set(context_anchor_terms)))
        context_overlap_terms = overlap
        if not overlap:
            violations.append("does not reference the local conversation strongly enough")

    return {
        "ok": not violations,
        "violations": violations,
        "normalized_text": normalized,
        "word_count": word_count,
        "nearby_duplicate": bool(similarity_scopes["nearby_context"]["exact_duplicate"]),
        "context_overlap_terms": context_overlap_terms,
        "context_anchor_terms": context_anchor_terms,
        "similarity_checks": similarity_scopes,
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


def _normalize_flair_option(value: Optional[str]) -> str:
    return _collapse_whitespace(value).lower()


def _option_hash_score(profile_name: str, subreddit: str, option: str) -> int:
    seed = f"{profile_name}:{subreddit}:{_normalize_flair_option(option)}"
    return int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16)


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
    conversation_summary: Optional[Dict[str, Any]] = None
    sample_urls: Optional[List[str]] = None
    persona_snapshot: Optional[Dict[str, Any]] = None
    writing_rule_snapshot: Optional[Dict[str, Any]] = None
    word_count: int = 0
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
                temperature=0.95,
                top_p=0.95,
                response_mime_type="application/json",
            ),
        )
        result_text = _response_text(response)
        if not result_text:
            raise RuntimeError("generation response was empty")
        return result_text

    def _shared_prompt_block(
        self,
        *,
        persona_snapshot: Dict[str, Any],
        writing_rule_snapshot: Dict[str, Any],
        recent_texts: List[str],
        same_thread_texts: List[str],
        same_profile_texts: List[str],
    ) -> str:
        rule_contents = dict(writing_rule_snapshot.get("rule_contents") or {})
        length_band = dict(persona_snapshot.get("length_band") or {})
        case_style = persona_snapshot.get("case_style")
        return f"""
locked production methodology:
- approved scenario: scenario_b / harder role spread with visible social friction
- do not sound like a generic helper clone
- this persona must stay distinct in social role, opening move, casing, and length
- match this persona contract exactly

persona contract:
{json.dumps(persona_snapshot, ensure_ascii=True, indent=2)}

hard output requirements:
- output valid json only
- no em dash
- follow the persona case style exactly: {case_style}
- stay inside the persona length band: {json.dumps(length_band, ensure_ascii=True)}
- keep the opening move distinct from recent program text
- do not reuse nearby thread phrasing or social framing
- do not sound like an operator, tester, bot, or automation system

exact rule file contents:

[great-writing-patterns.md]
{rule_contents.get("great-writing-patterns.md", "").strip()}

[negative-patterns.md]
{rule_contents.get("negative-patterns.md", "").strip()}

[vocabulary-guidance.md]
{rule_contents.get("vocabulary-guidance.md", "").strip()}

recent program text to avoid:
{json.dumps(recent_texts[-12:], ensure_ascii=True, indent=2)}

recent same-thread text to avoid:
{json.dumps(same_thread_texts[-8:], ensure_ascii=True, indent=2)}

recent same-profile text to avoid:
{json.dumps(same_profile_texts[-8:], ensure_ascii=True, indent=2)}
""".strip()

    def _reply_prompt(
        self,
        *,
        subreddit: str,
        target_excerpt: str,
        target_author: Optional[str],
        keywords: List[str],
        style_samples: List[Dict[str, Any]],
        conversation_context: List[Dict[str, Any]],
        recent_texts: List[str],
        same_thread_texts: List[str],
        same_profile_texts: List[str],
        persona_snapshot: Dict[str, Any],
        writing_rule_snapshot: Dict[str, Any],
    ) -> str:
        return f"""
you are writing a single reddit reply for r/{subreddit}.

{self._shared_prompt_block(
    persona_snapshot=persona_snapshot,
    writing_rule_snapshot=writing_rule_snapshot,
    recent_texts=recent_texts,
    same_thread_texts=same_thread_texts,
    same_profile_texts=same_profile_texts,
)}

topic keywords:
{json.dumps(keywords, ensure_ascii=True)}

target comment:
{json.dumps({"author": target_author, "excerpt": target_excerpt}, ensure_ascii=True, indent=2)}

style samples from the subreddit:
{json.dumps(summarize_style_samples(style_samples), ensure_ascii=True, indent=2)}

local thread context:
{json.dumps(summarize_conversation_context(conversation_context), ensure_ascii=True, indent=2)}

reply requirements:
- respond to at least one concrete detail from the target comment or local thread context
- do not float above the conversation as generic support

return json with this exact shape:
{{
  "text": "the reply text",
  "reasoning": "one short sentence on why it fits the persona and thread"
}}
""".strip()

    def _post_prompt(
        self,
        *,
        subreddit: str,
        keywords: List[str],
        style_samples: List[Dict[str, Any]],
        conversation_context: List[Dict[str, Any]],
        recent_texts: List[str],
        same_profile_texts: List[str],
        persona_snapshot: Dict[str, Any],
        writing_rule_snapshot: Dict[str, Any],
    ) -> str:
        return f"""
you are writing a new reddit post for r/{subreddit}.

{self._shared_prompt_block(
    persona_snapshot=persona_snapshot,
    writing_rule_snapshot=writing_rule_snapshot,
    recent_texts=recent_texts,
    same_thread_texts=[],
    same_profile_texts=same_profile_texts,
)}

topic keywords:
{json.dumps(keywords, ensure_ascii=True)}

style samples from the subreddit:
{json.dumps(summarize_style_samples(style_samples), ensure_ascii=True, indent=2)}

nearby subreddit conversation context:
{json.dumps(summarize_conversation_context(conversation_context), ensure_ascii=True, indent=2)}

post requirements:
- title should be specific and natural
- body can be empty or a few short sentences
- the post must feel like this persona, not generic subreddit copy
- it should add a useful angle or a genuine question without shadowing nearby posts

return json with this exact shape:
{{
  "title": "post title",
  "body": "optional post body",
  "reasoning": "one short sentence on why it fits the persona and subreddit"
}}
""".strip()

    def _comment_prompt(
        self,
        *,
        subreddit: str,
        thread_title: str,
        thread_excerpt: str,
        thread_author: Optional[str],
        keywords: List[str],
        style_samples: List[Dict[str, Any]],
        conversation_context: List[Dict[str, Any]],
        recent_texts: List[str],
        same_thread_texts: List[str],
        same_profile_texts: List[str],
        persona_snapshot: Dict[str, Any],
        writing_rule_snapshot: Dict[str, Any],
        retry_feedback: Optional[Dict[str, Any]] = None,
        validation_feedback: Optional[str] = None,
    ) -> str:
        anchor_terms = _context_anchor_terms([thread_title, thread_excerpt, *keywords], limit=10)
        retry_feedback = dict(retry_feedback or {})
        repair_lines: List[str] = []
        if validation_feedback:
            repair_lines.append(f"- previous draft failed validation with: {validation_feedback}")
        if str(retry_feedback.get("mode") or "").strip() == "submit_rejected":
            repair_lines.extend(
                [
                    "- the previous draft was rejected by reddit after submit",
                    "- make this version plainer, shorter, and less abstract",
                    "- favor direct thread language over institutional or diagnostic framing",
                    "- keep the persona intact, but make the comment easier to post and easier to read at a glance",
                ]
            )
        repair_block = "\n".join(repair_lines) if repair_lines else "- no prior repair feedback"
        return f"""
you are writing a single top-level reddit comment for a post in r/{subreddit}.

{self._shared_prompt_block(
    persona_snapshot=persona_snapshot,
    writing_rule_snapshot=writing_rule_snapshot,
    recent_texts=recent_texts,
    same_thread_texts=same_thread_texts,
    same_profile_texts=same_profile_texts,
)}

topic keywords:
{json.dumps(keywords, ensure_ascii=True)}

target thread:
{json.dumps({"title": thread_title, "excerpt": thread_excerpt, "author": thread_author}, ensure_ascii=True, indent=2)}

anchor terms from the thread and subreddit keywords:
{json.dumps(anchor_terms, ensure_ascii=True)}

style samples from the subreddit:
{json.dumps(summarize_style_samples(style_samples), ensure_ascii=True, indent=2)}

local thread context:
{json.dumps(summarize_conversation_context(conversation_context), ensure_ascii=True, indent=2)}

comment requirements:
- this is a top-level comment on the post, not a reply to another commenter
- it should sound organic for this persona and community
- it must address the thread directly, not float as generic empathy
- it must latch onto at least one concrete detail from the thread title or body
- it should naturally use at least one anchor term or very close concrete equivalent when that fits the thread
- avoid repeating the thread title language too literally

repair guidance:
{repair_block}

return json with this exact shape:
{{
  "text": "the top-level comment text",
  "reasoning": "one short sentence on why it fits the persona and thread"
}}
""".strip()

    def _flair_choice_prompt(
        self,
        *,
        profile_name: str,
        subreddit: str,
        available_options: List[str],
        current_flair: Optional[str],
    ) -> str:
        persona_snapshot = get_reddit_persona_snapshot(profile_name)
        return f"""
choose the single most organic reddit user flair for this profile in r/{subreddit}.

persona contract:
{json.dumps(persona_snapshot, ensure_ascii=True, indent=2)}

hard rules:
- return valid json only
- choose exactly one option from the provided list
- do not invent or rewrite flair text
- prefer a flair that feels plausible for the profile and subreddit
- prefer a flair that is specific enough to look intentional but not absurd
- if there is a current flair and it is already a good fit, you may keep it

current flair:
{json.dumps(current_flair, ensure_ascii=True)}

available flair options:
{json.dumps(available_options, ensure_ascii=True, indent=2)}

return json with this exact shape:
{{
  "choice": "exact flair option text",
  "reasoning": "one short sentence on why this option fits"
}}
""".strip()

    def _fallback_flair_choice(
        self,
        *,
        profile_name: str,
        subreddit: str,
        available_options: List[str],
        current_flair: Optional[str],
    ) -> Dict[str, Any]:
        current_norm = _normalize_flair_option(current_flair)
        for option in available_options:
            if _normalize_flair_option(option) == current_norm and current_norm:
                return {"choice": option, "reasoning": "kept the existing matching flair"}

        banned_terms = {
            "none",
            "no flair",
            "add flair",
            "change user flair",
            "edit flair",
            "view all flair",
            "apply",
            "save",
        }
        filtered = [
            option for option in available_options
            if not any(term in _normalize_flair_option(option) for term in banned_terms)
        ] or list(available_options)
        ranked = sorted(
            filtered,
            key=lambda option: (
                len(_normalize_flair_option(option).split()) == 1,
                _option_hash_score(profile_name, subreddit, option),
            ),
            reverse=True,
        )
        choice = ranked[0] if ranked else (available_options[0] if available_options else "")
        return {"choice": choice, "reasoning": "fallback stable flair selection based on profile and subreddit hash"}

    async def generate_reply(
        self,
        *,
        profile_name: str,
        subreddit: str,
        target_excerpt: str,
        target_author: Optional[str],
        keywords: List[str],
        style_samples: List[Dict[str, Any]],
        conversation_context: List[Dict[str, Any]],
        recent_texts: List[str],
        same_thread_texts: Optional[List[str]] = None,
        same_profile_texts: Optional[List[str]] = None,
        retry_feedback: Optional[Dict[str, Any]] = None,
        max_attempts: int = 3,
    ) -> GenerationResult:
        persona_snapshot = get_reddit_persona_snapshot(profile_name)
        writing_rule_snapshot = get_writing_rule_snapshot(include_contents=True)
        style_summary = summarize_style_samples(style_samples)
        conversation_summary = summarize_conversation_context(conversation_context)
        sample_urls = [
            str(sample.get("target_url") or sample.get("target_comment_url") or "").strip()
            for sample in style_samples
            if str(sample.get("target_url") or sample.get("target_comment_url") or "").strip()
        ]
        nearby_texts = [
            "\n".join(part for part in [sample.get("title"), sample.get("excerpt"), sample.get("body_excerpt"), sample.get("body")] if part).strip()
            for sample in list(conversation_context or [])
        ]
        same_thread_texts = list(same_thread_texts or [])
        same_profile_texts = list(same_profile_texts or [])
        last_error = "generation failed"
        for _attempt in range(max_attempts):
            raw = await self._generate_json(
                self._reply_prompt(
                    subreddit=subreddit,
                    target_excerpt=target_excerpt,
                    target_author=target_author,
                    keywords=keywords,
                    style_samples=style_samples,
                    conversation_context=conversation_context,
                    recent_texts=recent_texts,
                    same_thread_texts=same_thread_texts,
                    same_profile_texts=same_profile_texts,
                    persona_snapshot=persona_snapshot,
                    writing_rule_snapshot=writing_rule_snapshot,
                )
            )
            try:
                payload = _extract_json_object(raw)
            except Exception as exc:
                last_error = f"generation returned invalid json: {exc}"
                continue
            text = _collapse_whitespace(payload.get("text"))
            validation = validate_generated_text(
                text,
                recent_texts=recent_texts,
                nearby_texts=nearby_texts,
                same_thread_texts=same_thread_texts,
                same_profile_texts=same_profile_texts,
                context_anchor_texts=[target_excerpt, *keywords],
                require_context_overlap=True,
                persona_snapshot=persona_snapshot,
                writing_rule_snapshot=writing_rule_snapshot,
            )
            if validation["ok"]:
                return GenerationResult(
                    success=True,
                    kind="reply_comment",
                    text=text,
                    raw_response=raw,
                    validation=validation,
                    style_summary=style_summary,
                    conversation_summary=conversation_summary,
                    sample_urls=sample_urls,
                    persona_snapshot=persona_snapshot,
                    writing_rule_snapshot=get_writing_rule_snapshot(),
                    word_count=validation.get("word_count") or _word_count(text),
                )
            last_error = "; ".join(validation["violations"])
            recent_texts = list(recent_texts) + [text]
        return GenerationResult(
            success=False,
            kind="reply_comment",
            raw_response=None,
            validation={"ok": False, "violations": [last_error]},
            style_summary=style_summary,
            conversation_summary=conversation_summary,
            sample_urls=sample_urls,
            persona_snapshot=persona_snapshot,
            writing_rule_snapshot=get_writing_rule_snapshot(),
            error=last_error,
        )

    async def generate_comment(
        self,
        *,
        profile_name: str,
        subreddit: str,
        thread_title: str,
        thread_excerpt: str,
        thread_author: Optional[str],
        keywords: List[str],
        style_samples: List[Dict[str, Any]],
        conversation_context: List[Dict[str, Any]],
        recent_texts: List[str],
        same_thread_texts: Optional[List[str]] = None,
        same_profile_texts: Optional[List[str]] = None,
        max_attempts: int = 3,
    ) -> GenerationResult:
        persona_snapshot = get_reddit_persona_snapshot(profile_name)
        writing_rule_snapshot = get_writing_rule_snapshot(include_contents=True)
        style_summary = summarize_style_samples(style_samples)
        conversation_summary = summarize_conversation_context(conversation_context)
        sample_urls = [
            str(sample.get("target_url") or sample.get("target_comment_url") or "").strip()
            for sample in style_samples
            if str(sample.get("target_url") or sample.get("target_comment_url") or "").strip()
        ]
        nearby_texts = [
            "\n".join(part for part in [sample.get("title"), sample.get("excerpt"), sample.get("body_excerpt"), sample.get("body")] if part).strip()
            for sample in list(conversation_context or [])
        ]
        same_thread_texts = list(same_thread_texts or [])
        same_profile_texts = list(same_profile_texts or [])
        last_error = "generation failed"
        validation_feedback: Optional[str] = None
        for _attempt in range(max_attempts):
            raw = await self._generate_json(
                self._comment_prompt(
                    subreddit=subreddit,
                    thread_title=thread_title,
                    thread_excerpt=thread_excerpt,
                    thread_author=thread_author,
                    keywords=keywords,
                    style_samples=style_samples,
                    conversation_context=conversation_context,
                    recent_texts=recent_texts,
                    same_thread_texts=same_thread_texts,
                    same_profile_texts=same_profile_texts,
                    persona_snapshot=persona_snapshot,
                    writing_rule_snapshot=writing_rule_snapshot,
                    retry_feedback=retry_feedback,
                    validation_feedback=validation_feedback,
                )
            )
            try:
                payload = _extract_json_object(raw)
            except Exception as exc:
                last_error = f"generation returned invalid json: {exc}"
                continue
            text = _collapse_whitespace(payload.get("text"))
            validation = validate_generated_text(
                text,
                recent_texts=recent_texts,
                nearby_texts=nearby_texts,
                same_thread_texts=same_thread_texts,
                same_profile_texts=same_profile_texts,
                context_anchor_texts=[thread_title, thread_excerpt, *keywords],
                require_context_overlap=True,
                persona_snapshot=persona_snapshot,
                writing_rule_snapshot=writing_rule_snapshot,
            )
            if validation["ok"]:
                return GenerationResult(
                    success=True,
                    kind="comment_post",
                    text=text,
                    raw_response=raw,
                    validation=validation,
                    style_summary=style_summary,
                    conversation_summary=conversation_summary,
                    sample_urls=sample_urls,
                    persona_snapshot=persona_snapshot,
                    writing_rule_snapshot=get_writing_rule_snapshot(),
                    word_count=validation.get("word_count") or _word_count(text),
                )
            last_error = "; ".join(validation["violations"])
            validation_feedback = last_error
            recent_texts = list(recent_texts) + [text]
        return GenerationResult(
            success=False,
            kind="comment_post",
            raw_response=None,
            validation={"ok": False, "violations": [last_error]},
            style_summary=style_summary,
            conversation_summary=conversation_summary,
            sample_urls=sample_urls,
            persona_snapshot=persona_snapshot,
            writing_rule_snapshot=get_writing_rule_snapshot(),
            error=last_error,
        )

    async def generate_post(
        self,
        *,
        profile_name: str,
        subreddit: str,
        keywords: List[str],
        style_samples: List[Dict[str, Any]],
        conversation_context: List[Dict[str, Any]],
        recent_texts: List[str],
        same_profile_texts: Optional[List[str]] = None,
        max_attempts: int = 3,
    ) -> GenerationResult:
        persona_snapshot = get_reddit_persona_snapshot(profile_name)
        writing_rule_snapshot = get_writing_rule_snapshot(include_contents=True)
        style_summary = summarize_style_samples(style_samples)
        conversation_summary = summarize_conversation_context(conversation_context)
        sample_urls = [
            str(sample.get("target_url") or sample.get("target_comment_url") or "").strip()
            for sample in style_samples
            if str(sample.get("target_url") or sample.get("target_comment_url") or "").strip()
        ]
        nearby_texts = [
            "\n".join(part for part in [sample.get("title"), sample.get("excerpt"), sample.get("body_excerpt"), sample.get("body")] if part).strip()
            for sample in list(conversation_context or [])
        ]
        same_profile_texts = list(same_profile_texts or [])
        last_error = "generation failed"
        for _attempt in range(max_attempts):
            raw = await self._generate_json(
                self._post_prompt(
                    subreddit=subreddit,
                    keywords=keywords,
                    style_samples=style_samples,
                    conversation_context=conversation_context,
                    recent_texts=recent_texts,
                    same_profile_texts=same_profile_texts,
                    persona_snapshot=persona_snapshot,
                    writing_rule_snapshot=writing_rule_snapshot,
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
            validation = validate_generated_text(
                combined,
                recent_texts=recent_texts,
                nearby_texts=nearby_texts,
                same_thread_texts=[],
                same_profile_texts=same_profile_texts,
                context_anchor_texts=list(keywords),
                require_context_overlap=True,
                persona_snapshot=persona_snapshot,
                writing_rule_snapshot=writing_rule_snapshot,
            )
            if title and validation["ok"]:
                return GenerationResult(
                    success=True,
                    kind="create_post",
                    title=title,
                    body=body,
                    raw_response=raw,
                    validation=validation,
                    style_summary=style_summary,
                    conversation_summary=conversation_summary,
                    sample_urls=sample_urls,
                    persona_snapshot=persona_snapshot,
                    writing_rule_snapshot=get_writing_rule_snapshot(),
                    word_count=validation.get("word_count") or _word_count(combined),
                )
            last_error = "; ".join(validation["violations"] or ["title is required"])
            recent_texts = list(recent_texts) + [combined]
        return GenerationResult(
            success=False,
            kind="create_post",
            raw_response=None,
            validation={"ok": False, "violations": [last_error]},
            style_summary=style_summary,
            conversation_summary=conversation_summary,
            sample_urls=sample_urls,
            persona_snapshot=persona_snapshot,
            writing_rule_snapshot=get_writing_rule_snapshot(),
            error=last_error,
        )

    async def choose_user_flair(
        self,
        *,
        profile_name: str,
        subreddit: str,
        available_options: List[str],
        current_flair: Optional[str] = None,
    ) -> Dict[str, Any]:
        cleaned_options = [_collapse_whitespace(option) for option in list(available_options or []) if _collapse_whitespace(option)]
        if not cleaned_options:
            return {"choice": None, "reasoning": "no flair options were available", "persona_snapshot": get_reddit_persona_snapshot(profile_name)}

        fallback = self._fallback_flair_choice(
            profile_name=profile_name,
            subreddit=subreddit,
            available_options=cleaned_options,
            current_flair=current_flair,
        )
        raw_response = None
        if self.enabled and self.client:
            try:
                raw_response = await self._generate_json(
                    self._flair_choice_prompt(
                        profile_name=profile_name,
                        subreddit=subreddit,
                        available_options=cleaned_options,
                        current_flair=current_flair,
                    )
                )
                payload = _extract_json_object(raw_response)
                choice = _collapse_whitespace(payload.get("choice"))
                if choice and choice in cleaned_options:
                    return {
                        "choice": choice,
                        "reasoning": _collapse_whitespace(payload.get("reasoning")) or "model-selected flair option",
                        "raw_response": raw_response,
                        "persona_snapshot": get_reddit_persona_snapshot(profile_name),
                    }
            except Exception as exc:
                logger.warning(f"reddit flair choice generation failed for {profile_name} in r/{subreddit}: {exc}")

        return {
            "choice": fallback.get("choice"),
            "reasoning": fallback.get("reasoning"),
            "raw_response": raw_response,
            "persona_snapshot": get_reddit_persona_snapshot(profile_name),
        }
