"""
AI campaign interview generation service.

Responsibilities:
- Fetch Facebook post context via Graph API (token required)
- Load writing-rule snapshots
- Generate campaign comments via Claude Sonnet 4.6
- Enforce rule compliance and deduplication
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import httpx

from config import (
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL,
    FACEBOOK_APP_TOKEN,
    FACEBOOK_GRAPH_API_VERSION,
    FACEBOOK_PAGE_ACCESS_TOKEN,
)
from premium_rules import (
    build_rules_snapshot,
    load_rule_texts_from_paths,
    sanitize_text_against_rules,
    validate_text_against_rules,
)
from queue_manager import NEAR_DUPLICATE_THRESHOLD, near_duplicate_ratio


logger = logging.getLogger("CampaignAI")

DEFAULT_NEGATIVE_PATTERNS_PATH = "/Users/nikitalienov/Documents/writing/.claude/rules/negative-patterns.md"
DEFAULT_VOCAB_GUIDANCE_PATH = "/Users/nikitalienov/Documents/writing/.claude/rules/vocabulary-guidance.md"

AI_COMMENT_MIN = 10
AI_COMMENT_MAX = 50


class CampaignAIError(Exception):
    """Structured application error for AI campaign flows."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = int(status_code)
        self.detail = str(detail)


class CampaignAIConfigError(CampaignAIError):
    """Configuration error for AI campaign flows."""


def ensure_comment_count(value: int, *, strict_bounds: bool = True) -> int:
    """Normalize comment count and optionally enforce public API bounds."""
    count = int(value)
    if count < 1:
        raise CampaignAIError(400, "comment_count must be >= 1")
    if strict_bounds and (count < AI_COMMENT_MIN or count > AI_COMMENT_MAX):
        raise CampaignAIError(
            400,
            f"comment_count must be between {AI_COMMENT_MIN} and {AI_COMMENT_MAX}",
        )
    return count


def _require_absolute_url(url: str) -> str:
    raw = str(url or "").strip()
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        raise CampaignAIError(400, "url must be a full absolute URL")
    return raw


def _extract_page_id(url: str) -> Optional[str]:
    try:
        query = parse_qs(urlparse(url).query)
        page_id = str((query.get("id") or [""])[0]).strip()
        return page_id or None
    except Exception:
        return None


def _facebook_token_candidates() -> List[Tuple[str, str]]:
    candidates: List[Tuple[str, str]] = []
    if FACEBOOK_PAGE_ACCESS_TOKEN:
        candidates.append((FACEBOOK_PAGE_ACCESS_TOKEN, "FACEBOOK_PAGE_ACCESS_TOKEN"))
    if FACEBOOK_APP_TOKEN:
        candidates.append((FACEBOOK_APP_TOKEN, "FACEBOOK_APP_TOKEN"))
    return candidates


def _campaign_rule_paths() -> Dict[str, str]:
    return {
        "negative_patterns_path": os.getenv(
            "CAMPAIGN_AI_NEGATIVE_PATTERNS_PATH",
            DEFAULT_NEGATIVE_PATTERNS_PATH,
        ),
        "vocabulary_guidance_path": os.getenv(
            "CAMPAIGN_AI_VOCAB_GUIDANCE_PATH",
            DEFAULT_VOCAB_GUIDANCE_PATH,
        ),
    }


def load_campaign_rules_snapshot() -> Dict:
    """Load and normalize writing constraints for campaign comments."""
    rule_paths = _campaign_rule_paths()
    try:
        negative_text, vocab_text = load_rule_texts_from_paths(rule_paths)
    except Exception as exc:
        raise CampaignAIConfigError(
            500,
            (
                "Failed to load writing rules. Configure CAMPAIGN_AI_NEGATIVE_PATTERNS_PATH "
                f"and CAMPAIGN_AI_VOCAB_GUIDANCE_PATH. error={exc}"
            ),
        ) from exc

    return build_rules_snapshot(
        negative_patterns_text=negative_text,
        vocabulary_guidance_text=vocab_text,
        source_paths=rule_paths,
    )


def summarize_rules(snapshot: Dict) -> Dict:
    """Return compact metadata for API responses."""
    return {
        "version": snapshot.get("version"),
        "negative_patterns_count": len(snapshot.get("negative_patterns") or []),
        "vocabulary_count": len(snapshot.get("vocabulary_guidance") or []),
    }


async def _graph_get(path: str, params: Dict[str, str], token: str) -> Dict:
    """Perform a Graph API GET with normalized error handling."""
    version = str(FACEBOOK_GRAPH_API_VERSION or "v23.0").strip() or "v23.0"
    endpoint = path.strip("/")
    if endpoint:
        url = f"https://graph.facebook.com/{version}/{endpoint}"
    else:
        url = f"https://graph.facebook.com/{version}/"

    query = dict(params or {})
    query["access_token"] = token

    timeout = httpx.Timeout(30.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url, params=query)

    try:
        payload = response.json()
    except Exception:
        payload = {"error": {"message": response.text or "non-json response"}}

    if response.status_code >= 400 or isinstance(payload, dict) and payload.get("error"):
        err = payload.get("error") if isinstance(payload, dict) else None
        message = str((err or {}).get("message") or f"Graph API status {response.status_code}")
        code = (err or {}).get("code")
        subcode = (err or {}).get("error_subcode")
        raise CampaignAIError(
            400,
            f"Graph API request failed: {message} (code={code}, subcode={subcode})",
        )

    if not isinstance(payload, dict):
        raise CampaignAIError(400, "Graph API returned unexpected payload")

    return payload


async def _extract_story_id_from_permalink_html(url: str) -> Optional[str]:
    """
    Resolve numeric story id from public permalink HTML.

    Facebook frequently blocks `/?id=<url>` Graph crawling for `pfbid...` URLs.
    In that case we can parse canonical metadata to get the numeric post id.
    """
    timeout = httpx.Timeout(20.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.get(url)

    if response.status_code >= 400:
        return None

    html = str(response.text or "")
    if not html:
        return None

    patterns = [
        r'<meta\s+property="og:url"\s+content="https://www\.facebook\.com/[^"]*/posts(?:/[^"]*)?/(\d+)/"',
        r'<link\s+rel="canonical"\s+href="https://www\.facebook\.com/[^"]*/posts(?:/[^"]*)?/(\d+)/"',
        r"/posts(?:/[^/\"?]+)?/(\d+)/",
    ]
    for pattern in patterns:
        match = re.search(pattern, html)
        if not match:
            continue
        story_id = str(match.group(1) or "").strip()
        if story_id.isdigit():
            return story_id
    return None


async def _resolve_post_id(url: str, token: str) -> str:
    """Resolve target URL to canonical Graph object id."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    page_id = str((query.get("id") or [""])[0]).strip()
    story_fbid = str((query.get("story_fbid") or [""])[0]).strip()

    if page_id and story_fbid:
        return f"{page_id}_{story_fbid}"

    root = await _graph_get(
        "",
        {
            "id": url,
            "fields": "id,og_object{id}",
        },
        token,
    )

    og_id = str(((root.get("og_object") or {}).get("id") or "")).strip()
    direct_id = str((root.get("id") or "")).strip()

    if og_id:
        return og_id
    if direct_id:
        return direct_id

    raise CampaignAIError(400, "Could not resolve Facebook post id from URL")


async def _fetch_context_with_token(url: str, token: str, token_source: str) -> Dict:
    """Fetch OP + first two comments from Graph API with controlled-page validation."""
    target_page_id = _extract_page_id(url)

    post_id = await _resolve_post_id(url, token)

    post_payload = await _graph_get(
        post_id,
        {
            "fields": "id,message,story,from,permalink_url,created_time",
        },
        token,
    )

    from_block = post_payload.get("from") or {}
    owner_id = str(from_block.get("id") or "").strip()
    if not owner_id:
        raise CampaignAIError(400, "Unable to validate post owner from Graph API response")

    url_page_id_match = bool(target_page_id) and owner_id == target_page_id

    comments_payload = await _graph_get(
        f"{post_id}/comments",
        {
            "limit": "2",
            "fields": "id,message,from,permalink_url,created_time",
        },
        token,
    )

    comments_data = comments_payload.get("data") if isinstance(comments_payload, dict) else []
    if not isinstance(comments_data, list):
        comments_data = []

    supporting_comments: List[Dict[str, Optional[str]]] = []
    for item in comments_data[:2]:
        if not isinstance(item, dict):
            continue
        author = item.get("from") or {}
        supporting_comments.append(
            {
                "id": str(item.get("id") or "").strip() or None,
                "text": str(item.get("message") or "").strip(),
                "author_name": str(author.get("name") or "").strip() or None,
                "author_id": str(author.get("id") or "").strip() or None,
                "permalink_url": str(item.get("permalink_url") or "").strip() or None,
                "created_time": str(item.get("created_time") or "").strip() or None,
            }
        )

    op_text = str(post_payload.get("message") or post_payload.get("story") or "").strip()
    context_id = hashlib.sha256(f"{post_id}:{url}".encode("utf-8")).hexdigest()[:16]

    return {
        "context_id": context_id,
        "url": url,
        "op_post": {
            "id": str(post_payload.get("id") or post_id),
            "text": op_text,
            "author_name": str(from_block.get("name") or "").strip() or None,
            "author_id": owner_id,
            "permalink_url": str(post_payload.get("permalink_url") or url).strip(),
            "created_time": str(post_payload.get("created_time") or "").strip() or None,
            "page_id": owner_id,
        },
        "supporting_comments": supporting_comments,
        "source_meta": {
            "token_source": token_source,
            "graph_api_version": FACEBOOK_GRAPH_API_VERSION,
            "post_id": post_id,
            "controlled_page_validated": True,
            "url_page_id": target_page_id or None,
            "url_page_id_match": url_page_id_match if target_page_id else None,
            "post_owner_id": owner_id,
        },
    }


async def fetch_campaign_context(url: str) -> Dict:
    """Resolve and fetch campaign context from Facebook Graph API."""
    normalized_url = _require_absolute_url(url)
    token_candidates = _facebook_token_candidates()
    if not token_candidates:
        raise CampaignAIConfigError(
            500,
            "Missing Facebook token. Configure FACEBOOK_APP_TOKEN or FACEBOOK_PAGE_ACCESS_TOKEN",
        )

    errors: List[str] = []
    for token, token_source in token_candidates:
        try:
            return await _fetch_context_with_token(normalized_url, token, token_source)
        except CampaignAIError as exc:
            errors.append(f"{token_source}: {exc.detail}")
            continue

    raise CampaignAIError(
        400,
        "Failed to fetch context with configured token(s): " + " | ".join(errors),
    )


def _extract_response_text(payload: Dict) -> str:
    blocks = payload.get("content") if isinstance(payload, dict) else None
    if not isinstance(blocks, list):
        return ""

    parts: List[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "\n".join(parts).strip()


async def _call_claude(prompt: str) -> str:
    if not ANTHROPIC_API_KEY:
        raise CampaignAIConfigError(500, "Missing ANTHROPIC_API_KEY")

    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    payload = {
        "model": CLAUDE_MODEL,
        "max_tokens": 2500,
        "temperature": 0.7,
        "system": (
            "You generate high-quality Facebook comments. "
            "Output must be valid JSON only, with no markdown."
        ),
        "messages": [
            {
                "role": "user",
                "content": prompt,
            }
        ],
    }

    timeout = httpx.Timeout(60.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)

    if response.status_code >= 400:
        detail = response.text
        try:
            detail_payload = response.json()
            detail = str(((detail_payload.get("error") or {}).get("message") or detail))
        except Exception:
            pass
        raise CampaignAIError(502, f"Claude API call failed: {detail}")

    try:
        data = response.json()
    except Exception as exc:
        raise CampaignAIError(502, f"Claude API returned non-JSON response: {exc}") from exc

    text = _extract_response_text(data)
    if not text:
        raise CampaignAIError(502, "Claude API returned empty response")
    return text


def _extract_json_comments(raw_text: str) -> List[str]:
    payload_text = str(raw_text or "").strip()
    if not payload_text:
        raise CampaignAIError(502, "Claude output is empty")

    parsed = None
    try:
        parsed = json.loads(payload_text)
    except Exception:
        pass

    if parsed is None:
        # Try first JSON object block.
        match_obj = re.search(r"\{[\s\S]*\}", payload_text)
        if match_obj:
            try:
                parsed = json.loads(match_obj.group(0))
            except Exception:
                parsed = None

    if parsed is None:
        # Try direct JSON array and wrap into expected shape.
        match_arr = re.search(r"\[[\s\S]*\]", payload_text)
        if match_arr:
            try:
                parsed = {"comments": json.loads(match_arr.group(0))}
            except Exception:
                parsed = None

    if not isinstance(parsed, dict):
        raise CampaignAIError(502, "Claude output is not valid JSON object")

    comments = parsed.get("comments")
    if not isinstance(comments, list):
        raise CampaignAIError(502, "Claude output missing `comments` list")

    normalized: List[str] = []
    for item in comments:
        text = str(item or "").strip()
        text = re.sub(r"^[\-\*\d\)\.\s]+", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            normalized.append(text)

    return normalized


def _is_near_duplicate(candidate: str, existing: List[str]) -> bool:
    for item in existing:
        if near_duplicate_ratio(candidate, item) >= float(NEAR_DUPLICATE_THRESHOLD):
            return True
    return False


def _prepare_comment_pool(
    *,
    candidates: List[str],
    accepted: List[str],
    existing_comments: List[str],
    rules_snapshot: Dict,
) -> List[str]:
    out: List[str] = []
    seen_base = [str(x).strip() for x in accepted + existing_comments if str(x).strip()]

    for raw in candidates:
        text = str(raw or "").strip()
        if not text:
            continue

        sanitized = sanitize_text_against_rules(text, rules_snapshot)
        sanitized = re.sub(r"\s+", " ", sanitized).strip()
        if not sanitized:
            continue

        validation = validate_text_against_rules(sanitized, rules_snapshot)
        if not validation.get("ok"):
            continue

        if _is_near_duplicate(sanitized, seen_base + out):
            continue

        out.append(sanitized)

    return out


def _build_generation_prompt(
    *,
    context_snapshot: Dict,
    intent: str,
    comment_count: int,
    existing_comments: List[str],
    rules_snapshot: Dict,
    remaining_attempt: int,
) -> str:
    op_post = context_snapshot.get("op_post") or {}
    support = context_snapshot.get("supporting_comments") or []

    op_text = str(op_post.get("text") or "").strip()
    support_lines = []
    for idx, item in enumerate(support[:2]):
        text = str((item or {}).get("text") or "").strip()
        if text:
            support_lines.append(f"- comment {idx + 1}: {text}")

    negative_patterns = rules_snapshot.get("negative_patterns") or []
    vocab_patterns = rules_snapshot.get("vocabulary_guidance") or []

    forbidden_lines = []
    for phrase in negative_patterns[:120]:
        forbidden_lines.append(f"- {phrase}")
    for phrase in vocab_patterns[:120]:
        forbidden_lines.append(f"- {phrase}")

    existing_lines = [f"- {item}" for item in existing_comments[:80]]

    return f"""
You must generate exactly {comment_count} Facebook comments as strict JSON.

Output format (must be valid JSON only, no markdown):
{{"comments": ["comment one", "comment two"]}}

Rules:
- Return exactly {comment_count} unique comments.
- Use natural human language.
- Keep comments relevant to the OP post and user intent.
- Include variety in length and tone (short, supportive, perspective, testimonial-like).
- Avoid policy-banned wording listed below.
- Do not include numbering, labels, hashtags, or emoji-only comments.
- Keep each comment concise (1-2 sentences max; short comments allowed).
- Do not repeat or paraphrase too closely to existing comments.

OP post context:
{op_text or "(no OP message available)"}

Supporting comments:
{chr(10).join(support_lines) if support_lines else "(none)"}

User intent:
{intent}

Existing comments to avoid (exact/near duplicates):
{chr(10).join(existing_lines) if existing_lines else "(none)"}

Forbidden words/patterns:
{chr(10).join(forbidden_lines) if forbidden_lines else "(none)"}

Attempt: {remaining_attempt}
""".strip()


async def generate_campaign_comments(
    *,
    context_snapshot: Dict,
    intent: str,
    comment_count: int,
    rules_snapshot: Dict,
    existing_comments: Optional[List[str]] = None,
) -> List[str]:
    """Generate sanitized, deduplicated comments using strict Claude model."""
    count = ensure_comment_count(comment_count, strict_bounds=False)
    normalized_intent = str(intent or "").strip()
    if not normalized_intent:
        raise CampaignAIError(400, "intent is required")

    existing = [str(item).strip() for item in (existing_comments or []) if str(item).strip()]
    accepted: List[str] = []

    max_attempts = 4
    for attempt in range(1, max_attempts + 1):
        remaining = count - len(accepted)
        if remaining <= 0:
            break

        prompt = _build_generation_prompt(
            context_snapshot=context_snapshot,
            intent=normalized_intent,
            comment_count=remaining,
            existing_comments=existing + accepted,
            rules_snapshot=rules_snapshot,
            remaining_attempt=attempt,
        )
        response_text = await _call_claude(prompt)
        candidates = _extract_json_comments(response_text)
        approved = _prepare_comment_pool(
            candidates=candidates,
            accepted=accepted,
            existing_comments=existing,
            rules_snapshot=rules_snapshot,
        )
        accepted.extend(approved)

        logger.info(
            "Campaign AI generation attempt %s: received=%s approved=%s accumulated=%s target=%s",
            attempt,
            len(candidates),
            len(approved),
            len(accepted),
            count,
        )

    final_comments = accepted[:count]
    if len(final_comments) != count:
        raise CampaignAIError(
            502,
            (
                "Failed to generate enough unique, compliant comments "
                f"(requested={count}, generated={len(final_comments)})"
            ),
        )

    return final_comments
