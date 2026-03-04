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
import math
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
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

_LOCAL_RULES_DIR = Path(__file__).resolve().parent / "rules"
DEFAULT_NEGATIVE_PATTERNS_PATH = str(_LOCAL_RULES_DIR / "campaign-ai-negative-patterns.md")
DEFAULT_VOCAB_GUIDANCE_PATH = str(_LOCAL_RULES_DIR / "campaign-ai-vocabulary-guidance.md")

AI_COMMENT_MIN = 10
AI_COMMENT_MAX = 50

DEFAULT_STYLE_PROFILE_PATH = str(_LOCAL_RULES_DIR / "campaign-ai-style-profile.json")
STYLE_PROFILE_MIN_SAMPLE_SIZE = int(os.getenv("CAMPAIGN_AI_STYLE_PROFILE_MIN_SAMPLE_SIZE", "200"))
STYLE_CACHE_TTL_SECONDS = int(os.getenv("CAMPAIGN_AI_STYLE_CACHE_TTL_SECONDS", "1800"))
NORMAL_CASE_RATIO_FLOOR = 0.20
MIN_NORMAL_CASE_RATIO = float(os.getenv("CAMPAIGN_AI_MIN_NORMAL_CASE_RATIO", "0.2"))
BRAND_RECOMMENDATION_RATIO = float(os.getenv("CAMPAIGN_AI_BRAND_RECOMMENDATION_RATIO", "0.35"))
BRAND_JUSTIFICATION_RATIO = float(os.getenv("CAMPAIGN_AI_BRAND_JUSTIFICATION_RATIO", "0.6"))

_STYLE_PROFILE_CACHE: Dict[str, Any] = {
    "profile": None,
    "loaded_at_ts": 0.0,
    "source_path": "",
    "source_mtime": 0.0,
}

_ANCHOR_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "have",
    "has",
    "had",
    "are",
    "was",
    "were",
    "just",
    "only",
    "really",
    "very",
    "what",
    "why",
    "when",
    "where",
    "which",
    "who",
    "how",
    "does",
    "did",
    "can",
    "could",
    "would",
    "should",
    "been",
    "your",
    "you",
    "yours",
    "about",
    "into",
    "over",
    "under",
    "after",
    "before",
    "because",
    "than",
    "then",
    "also",
    "some",
    "any",
    "more",
    "less",
    "same",
    "help",
    "helped",
    "havent",
    "haven't",
}

_MENTION_FALLBACK_LAST_NAMES = (
    "Lobb",
    "Nair",
    "Lopez",
    "Miller",
    "Ramos",
    "Silva",
    "Khan",
    "Carter",
    "Bennett",
    "Dawson",
)


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


def _word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9']+", str(text or "")))


def _length_bucket(text: str) -> str:
    words = _word_count(text)
    if words <= 4:
        return "short"
    if words >= 25:
        return "long"
    return "medium"


def _first_alpha_char(text: str) -> str:
    for ch in str(text or ""):
        if ch.isalpha():
            return ch
    return ""


def _ending_bucket(text: str) -> str:
    stripped = str(text or "").rstrip()
    if stripped.endswith("."):
        return "period"
    if stripped.endswith("?"):
        return "question"
    if stripped.endswith("!"):
        return "exclaim"
    return "none"


def _infer_archetype(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return "supportive"

    words = _word_count(raw)
    if words <= 3:
        return "reaction"

    if re.search(r"\b(i|i'm|ive|i've|me|my|we|our|us|myself)\b", raw, flags=re.IGNORECASE):
        return "testimonial"

    if "?" in raw or re.search(r"\b(anyone|how|what|why|where|when|does|did|can|could)\b", raw, flags=re.IGNORECASE):
        return "question"

    if re.search(r"\b(try|instead|another|personally|you could|what helped|alternative)\b", raw, flags=re.IGNORECASE):
        return "alternative"

    return "supportive"


def _is_contrarian_comment(text: str) -> bool:
    low = str(text or "").strip().lower()
    if not low:
        return False
    # Mild contrarian/rage-bait signals that spark discussion without attacking the OP.
    patterns = [
        r"\b(not buying|sounds like placebo|placebo|overhyped|idk about this|too good to be true|doubt)\b",
        r"\b(doesn't work for everyone|not for everyone|did nothing for me|didn't work for me)\b",
        r"\b(you sure|are we sure|how is this different)\b",
    ]
    if low.startswith(("nah", "idk", "hot take")):
        return True
    return any(re.search(pattern, low) for pattern in patterns)


def _comment_lane(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return "supportive"
    if _is_contrarian_comment(normalized):
        return "contrarian"
    archetype = _infer_archetype(normalized)
    if archetype == "testimonial":
        return "testimonial"
    if archetype == "alternative":
        return "alternative"
    return "supportive"


def _canonical_brand(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    lowered = raw.lower().replace(" ", "")
    if lowered in {"mynuora", "nuora"}:
        return "Nuora"
    return raw


def _detect_primary_brand(context_snapshot: Dict[str, Any], intent: str) -> Optional[str]:
    override = _canonical_brand(os.getenv("CAMPAIGN_AI_BRAND_OVERRIDE", ""))
    if override:
        return override

    texts: List[str] = [str(intent or "")]
    op_post = context_snapshot.get("op_post") or {}
    texts.append(str(op_post.get("text") or ""))
    for item in context_snapshot.get("supporting_comments") or []:
        texts.append(str((item or {}).get("text") or ""))

    merged = "\n".join(texts)
    lowered = merged.lower()
    if "nuora" in lowered or "mynuora" in lowered:
        return "Nuora"

    match = re.search(
        r"\bbrand\s+we\s+recommend\s+is\s+([A-Za-z][A-Za-z0-9_-]{2,30})\b",
        merged,
        flags=re.IGNORECASE,
    )
    if match:
        return _canonical_brand(match.group(1))
    return None


def _mentions_brand(text: str, brand: Optional[str]) -> bool:
    brand_value = _canonical_brand(brand or "")
    if not brand_value:
        return False
    tokens = [re.escape(brand_value.lower())]
    if brand_value.lower() == "nuora":
        tokens.append("mynuora")
    return bool(re.search(rf"\b({'|'.join(tokens)})\b", str(text or "").lower()))


def _has_nonorganic_brand_discovery(text: str, brand: Optional[str]) -> bool:
    brand_value = _canonical_brand(brand or "")
    if not brand_value:
        return False
    lowered = str(text or "").lower()
    brand_pattern = r"(?:nuora|mynuora)" if brand_value.lower() == "nuora" else re.escape(brand_value.lower())
    patterns = [
        rf"\b(switch(?:ed|ing)? to|swapped to|moved to)\s+{brand_pattern}\b",
        rf"\b(found|discovered|came across|saw|ordered|bought|got)\s+{brand_pattern}\b",
        rf"\b(link|comments?|thread|above)\b[\w\s,.-]{{0,40}}{brand_pattern}\b",
        rf"\b{brand_pattern}\b[\w\s,.-]{{0,40}}\b(link|comments?|thread|above)\b",
    ]
    return any(re.search(pattern, lowered) for pattern in patterns)


def _is_engagement_bait_cta(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    patterns = [
        r"\btag a friend\b",
        r"\btag someone\b",
        r"\bshare (this|it)\b",
        r"\bdrop (a|your)\b",
        r"\bcomment below\b",
        r"\btagging\b",
        r"\bfollow\b",
        r"\bdm me\b",
        r"\blink in bio\b",
        r"\bsmash\b",
        r"\bsubscribe\b",
    ]
    return any(re.search(pattern, lowered) for pattern in patterns)


def _normalize_handle_to_full_name(raw_handle: str) -> str:
    handle = str(raw_handle or "").strip()
    if not handle:
        return ""

    normalized = re.sub(r"[@._-]+", " ", handle)
    parts = [token for token in re.findall(r"[A-Za-z]+", normalized) if token]
    if not parts:
        return ""

    parts = parts[:2]
    if len(parts) == 1:
        digest = hashlib.sha1(parts[0].lower().encode("utf-8")).hexdigest()
        idx = int(digest[:8], 16) % len(_MENTION_FALLBACK_LAST_NAMES)
        parts.append(_MENTION_FALLBACK_LAST_NAMES[idx])

    titled = [token[:1].upper() + token[1:].lower() for token in parts]
    return " ".join(titled)


def _normalize_name_mentions(text: str) -> str:
    normalized = str(text or "").strip()
    if not normalized:
        return ""

    def _replace_handle(match: re.Match[str]) -> str:
        first = str(match.group(1) or "").strip()
        second = str(match.group(2) or "").strip()
        joined = " ".join([part for part in (first, second) if part])
        if first.lower() in {"nuora", "mynuora"}:
            return "Nuora"
        converted = _normalize_handle_to_full_name(joined)
        return converted or joined or first

    normalized = re.sub(
        r"@([A-Za-z][A-Za-z0-9._-]{0,31})(?:\s+([A-Za-z][A-Za-z0-9._-]{0,31}))?",
        _replace_handle,
        normalized,
    )
    # Convert "tagging X Y" style into direct name mention.
    normalized = re.sub(
        r"\btagging\s+([A-Z][a-z]{1,24}\s+[A-Z][a-z]{1,24})\b",
        r"\1",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"\btag\s+([A-Z][a-z]{1,24}\s+[A-Z][a-z]{1,24})\b",
        r"\1",
        normalized,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _has_name_style_mention(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    if "@" in raw:
        return True
    return bool(re.search(r"\b[A-Z][a-z]{1,24}\s+[A-Z][a-z]{1,24}\b", raw))


def _tokenize_anchor_words(text: str) -> List[str]:
    tokens: List[str] = []
    for raw in re.findall(r"[A-Za-z0-9']+", str(text or "").lower()):
        token = raw.strip("'")
        if len(token) < 3:
            continue
        if token in _ANCHOR_STOPWORDS:
            continue
        tokens.append(token)
    return tokens


def _op_anchor_tokens(op_text: str) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for token in _tokenize_anchor_words(op_text):
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _is_op_anchored(comment_text: str, op_anchors: List[str]) -> bool:
    if not op_anchors:
        return True
    lower = str(comment_text or "").lower()
    for token in op_anchors:
        if re.search(rf"\b{re.escape(token)}\b", lower):
            return True
    return False


def _has_mechanism_explanation(text: str) -> bool:
    return bool(
        re.search(
            (
                r"\b(because|since|due to|which means|this is why|the reason|mechanism|root cause|"
                r"biofilm|bacteria|pH|microbiome|enzyme|explains|works by)\b"
            ),
            str(text or ""),
            flags=re.IGNORECASE,
        )
    )


def _brand_targets(
    comment_count: int,
    *,
    brand: Optional[str],
) -> Dict[str, Any]:
    if not brand or comment_count <= 0:
        return {
            "brand": None,
            "recommendation_target": 0,
            "justification_target": 0,
        }

    recommendation_target = int(round(comment_count * BRAND_RECOMMENDATION_RATIO))
    if comment_count >= 10:
        recommendation_target = max(recommendation_target, 3)
    recommendation_target = min(comment_count, max(1, recommendation_target))

    justification_target = int(round(recommendation_target * BRAND_JUSTIFICATION_RATIO))
    justification_target = min(recommendation_target, max(1, justification_target))

    return {
        "brand": brand,
        "recommendation_target": recommendation_target,
        "justification_target": justification_target,
    }


def _brand_counts(comments: List[str], brand: Optional[str]) -> Dict[str, int]:
    recommendation_count = 0
    justification_count = 0
    for text in comments:
        if not _mentions_brand(text, brand):
            continue
        recommendation_count += 1
        if _has_mechanism_explanation(text):
            justification_count += 1
    return {
        "recommendation_count": recommendation_count,
        "justification_count": justification_count,
    }


def _missing_brand_targets(
    comments: List[str],
    brand_plan: Dict[str, Any],
) -> Dict[str, int]:
    brand = brand_plan.get("brand")
    if not brand:
        return {
            "recommendation": 0,
            "justification": 0,
        }
    counts = _brand_counts(comments, brand)
    return {
        "recommendation": max(
            0,
            int(brand_plan.get("recommendation_target", 0)) - int(counts.get("recommendation_count", 0)),
        ),
        "justification": max(
            0,
            int(brand_plan.get("justification_target", 0)) - int(counts.get("justification_count", 0)),
        ),
    }


def _clamp_ratio(value: Any, default: float) -> float:
    try:
        numeric = float(value)
    except Exception:
        numeric = float(default)
    return max(0.0, min(1.0, numeric))


def _normalize_distribution(
    raw: Any,
    *,
    keys: List[str],
    default: Dict[str, float],
) -> Dict[str, float]:
    source = raw if isinstance(raw, dict) else {}
    out: Dict[str, float] = {}
    total = 0.0
    for key in keys:
        value = _clamp_ratio(source.get(key), default.get(key, 0.0))
        out[key] = value
        total += value

    if total <= 0:
        out = {k: float(default.get(k, 0.0)) for k in keys}
        total = sum(out.values())

    if total <= 0:
        return {k: 1.0 / len(keys) for k in keys}

    return {k: out[k] / total for k in keys}


def _allocate_targets(
    total: int,
    *,
    weights: Dict[str, float],
    minima: Optional[Dict[str, int]] = None,
) -> Dict[str, int]:
    keys = list(weights.keys())
    if total <= 0:
        return {k: 0 for k in keys}

    minima = minima or {}
    normalized_weights = _normalize_distribution(
        weights,
        keys=keys,
        default={k: 1.0 / max(1, len(keys)) for k in keys},
    )

    targets = {k: max(0, int(minima.get(k, 0))) for k in keys}
    assigned = sum(targets.values())

    if assigned > total:
        # Trim low-priority buckets first if minima over-allocate.
        for key in sorted(keys, key=lambda k: normalized_weights[k]):
            while assigned > total and targets[key] > 0:
                targets[key] -= 1
                assigned -= 1

    remaining = total - assigned
    if remaining <= 0:
        return targets

    fractional: List[Tuple[float, str]] = []
    for key in keys:
        exact = remaining * normalized_weights[key]
        base = int(exact)
        targets[key] += base
        fractional.append((exact - base, key))

    assigned = sum(targets.values())
    leftover = total - assigned
    if leftover > 0:
        for _, key in sorted(fractional, reverse=True):
            if leftover <= 0:
                break
            targets[key] += 1
            leftover -= 1
    return targets


def _default_style_profile() -> Dict[str, Any]:
    return {
        "source": "defaults",
        "sample_size": 0,
        "length_distribution": {"short": 0.35, "medium": 0.45, "long": 0.20},
        "endings": {"none": 0.50, "period": 0.25, "question": 0.15, "exclaim": 0.10},
        "first_char_lower_ratio": 0.08,
        "mention_ratio": 0.05,
        "testimonial_ratio": 0.30,
        "archetype_distribution": {
            "reaction": 0.18,
            "supportive": 0.30,
            "question": 0.20,
            "testimonial": 0.22,
            "alternative": 0.10,
        },
        "examples": [],
        "source_meta": {},
    }


def _normalize_style_profile(raw: Any) -> Dict[str, Any]:
    profile = _default_style_profile()
    if not isinstance(raw, dict):
        return profile

    sample_size = 0
    try:
        sample_size = max(0, int(raw.get("sample_size") or 0))
    except Exception:
        sample_size = 0

    normalized = {
        "source": str(raw.get("source") or profile["source"]),
        "sample_size": sample_size,
        "length_distribution": _normalize_distribution(
            raw.get("length_distribution"),
            keys=["short", "medium", "long"],
            default=profile["length_distribution"],
        ),
        "endings": _normalize_distribution(
            raw.get("endings"),
            keys=["none", "period", "question", "exclaim"],
            default=profile["endings"],
        ),
        "first_char_lower_ratio": _clamp_ratio(
            raw.get("first_char_lower_ratio"),
            profile["first_char_lower_ratio"],
        ),
        "mention_ratio": _clamp_ratio(raw.get("mention_ratio"), profile["mention_ratio"]),
        "testimonial_ratio": _clamp_ratio(
            raw.get("testimonial_ratio"),
            profile["testimonial_ratio"],
        ),
        "archetype_distribution": _normalize_distribution(
            raw.get("archetype_distribution"),
            keys=["reaction", "supportive", "question", "testimonial", "alternative"],
            default=profile["archetype_distribution"],
        ),
        "examples": [],
        "source_meta": raw.get("source_meta") if isinstance(raw.get("source_meta"), dict) else {},
    }

    examples: List[str] = []
    for item in raw.get("examples") or []:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        if not text:
            continue
        examples.append(text)
        if len(examples) >= 24:
            break
    normalized["examples"] = examples
    return normalized


def _build_style_profile_from_comments(
    comments: List[str],
    *,
    source: str,
    source_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    profile = _default_style_profile()
    profile["source"] = source
    profile["source_meta"] = dict(source_meta or {})

    cleaned: List[str] = []
    for raw in comments:
        text = re.sub(r"\s+", " ", str(raw or "")).strip()
        if not text:
            continue
        if _word_count(text) == 0:
            continue
        cleaned.append(text)

    if not cleaned:
        return profile

    total = len(cleaned)
    length_counts = {"short": 0, "medium": 0, "long": 0}
    ending_counts = {"none": 0, "period": 0, "question": 0, "exclaim": 0}
    mention_count = 0
    testimonial_count = 0
    first_char_lower_count = 0
    archetype_counts = {
        "reaction": 0,
        "supportive": 0,
        "question": 0,
        "testimonial": 0,
        "alternative": 0,
    }

    for text in cleaned:
        bucket = _length_bucket(text)
        length_counts[bucket] += 1

        ending_counts[_ending_bucket(text)] += 1

        if _has_name_style_mention(text):
            mention_count += 1
        if re.search(r"\b(i|i'm|ive|i've|me|my|we|our|us|myself)\b", text, flags=re.IGNORECASE):
            testimonial_count += 1

        first = _first_alpha_char(text)
        if first and first.islower():
            first_char_lower_count += 1

        archetype_counts[_infer_archetype(text)] += 1

    examples: List[str] = []
    for bucket in ("short", "medium", "long"):
        for text in cleaned:
            if _length_bucket(text) != bucket:
                continue
            examples.append(text)
            if len(examples) >= 12:
                break
        if len(examples) >= 12:
            break

    profile.update(
        {
            "sample_size": total,
            "length_distribution": {
                "short": length_counts["short"] / total,
                "medium": length_counts["medium"] / total,
                "long": length_counts["long"] / total,
            },
            "endings": {
                "none": ending_counts["none"] / total,
                "period": ending_counts["period"] / total,
                "question": ending_counts["question"] / total,
                "exclaim": ending_counts["exclaim"] / total,
            },
            "first_char_lower_ratio": first_char_lower_count / total,
            "mention_ratio": mention_count / total,
            "testimonial_ratio": testimonial_count / total,
            "archetype_distribution": {
                "reaction": archetype_counts["reaction"] / total,
                "supportive": archetype_counts["supportive"] / total,
                "question": archetype_counts["question"] / total,
                "testimonial": archetype_counts["testimonial"] / total,
                "alternative": archetype_counts["alternative"] / total,
            },
            "examples": examples,
        }
    )
    return _normalize_style_profile(profile)


def _style_profile_path() -> str:
    return str(
        os.getenv("CAMPAIGN_AI_STYLE_PROFILE_PATH", DEFAULT_STYLE_PROFILE_PATH)
    ).strip() or DEFAULT_STYLE_PROFILE_PATH


def _load_style_profile_from_disk(path: str) -> Optional[Dict[str, Any]]:
    style_path = Path(path)
    if not style_path.exists():
        return None
    try:
        raw = json.loads(style_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Campaign AI style profile load failed (%s): %s", path, exc)
        return None
    return _normalize_style_profile(raw)


def _style_mix_targets(comment_count: int, style_profile: Dict[str, Any]) -> Dict[str, int]:
    dist = style_profile.get("length_distribution") or {}
    minima = {"short": 0, "medium": 0, "long": 0}
    if comment_count >= 4:
        minima["short"] = 1
    if comment_count >= 6:
        minima["long"] = 1
    if comment_count >= 10:
        minima["short"] = 2
        minima["long"] = 2

    return _allocate_targets(
        comment_count,
        weights={
            "short": float(dist.get("short", 0.35)),
            "medium": float(dist.get("medium", 0.45)),
            "long": float(dist.get("long", 0.20)),
        },
        minima=minima,
    )


def _style_surface_targets(comment_count: int, style_profile: Dict[str, Any]) -> Dict[str, Any]:
    endings = style_profile.get("endings") or {}
    archetypes = style_profile.get("archetype_distribution") or {}

    ending_minima = {"none": 0, "period": 0, "question": 0, "exclaim": 0}
    if comment_count >= 8:
        ending_minima["none"] = 2
        ending_minima["question"] = 1
    if comment_count >= 10:
        ending_minima["none"] = 3
        ending_minima["exclaim"] = 1

    endings_target = _allocate_targets(
        comment_count,
        weights={
            "none": float(endings.get("none", 0.5)),
            "period": float(endings.get("period", 0.25)),
            "question": float(endings.get("question", 0.15)),
            "exclaim": float(endings.get("exclaim", 0.10)),
        },
        minima=ending_minima,
    )

    reaction_target = int(round(comment_count * float(archetypes.get("reaction", 0.18))))
    testimonial_target = int(round(comment_count * float(style_profile.get("testimonial_ratio", 0.30))))
    question_target = int(round(comment_count * float(archetypes.get("question", 0.20))))
    alternative_target = int(round(comment_count * float(archetypes.get("alternative", 0.10))))
    mention_target = int(round(comment_count * float(style_profile.get("mention_ratio", 0.05))))
    lowercase_target = int(round(comment_count * float(style_profile.get("first_char_lower_ratio", 0.08))))

    if comment_count >= 8:
        reaction_target = max(reaction_target, 1)
        testimonial_target = max(testimonial_target, 1)
        question_target = max(question_target, 1)
        alternative_target = max(alternative_target, 1)
    if comment_count >= 10:
        reaction_target = max(reaction_target, 2)
        testimonial_target = max(testimonial_target, 2)
        mention_target = max(mention_target, 1)
        lowercase_target = max(lowercase_target, 1)

    effective_normal_case_ratio = max(float(MIN_NORMAL_CASE_RATIO), float(NORMAL_CASE_RATIO_FLOOR))
    uppercase_min = int(math.ceil(max(0.0, effective_normal_case_ratio) * comment_count))
    if comment_count >= 10:
        uppercase_min = max(2, uppercase_min)
    elif comment_count > 0:
        uppercase_min = max(1, uppercase_min)

    return {
        "endings": endings_target,
        "reaction": max(0, reaction_target),
        "testimonial": max(0, testimonial_target),
        "question": max(0, question_target),
        "alternative": max(0, alternative_target),
        "mention": max(0, mention_target),
        "lowercase_start": max(0, lowercase_target),
        "uppercase_start_min": max(0, min(comment_count, uppercase_min)),
    }


def _missing_mix_targets(
    accepted: List[str],
    target_mix: Dict[str, int],
) -> Dict[str, int]:
    counts = {"short": 0, "medium": 0, "long": 0}
    for text in accepted:
        counts[_length_bucket(text)] += 1
    return {
        "short": max(0, int(target_mix.get("short", 0)) - counts["short"]),
        "medium": max(0, int(target_mix.get("medium", 0)) - counts["medium"]),
        "long": max(0, int(target_mix.get("long", 0)) - counts["long"]),
    }


def _style_counters(comments: List[str]) -> Dict[str, Any]:
    endings = {"none": 0, "period": 0, "question": 0, "exclaim": 0}
    counters = {
        "reaction": 0,
        "testimonial": 0,
        "question": 0,
        "alternative": 0,
        "mention": 0,
        "lowercase_start": 0,
    }
    for text in comments:
        normalized = str(text or "").strip()
        if not normalized:
            continue
        endings[_ending_bucket(normalized)] += 1
        archetype = _infer_archetype(normalized)
        if archetype in counters:
            counters[archetype] += 1
        if _has_name_style_mention(normalized):
            counters["mention"] += 1
        first = _first_alpha_char(normalized)
        if first and first.islower():
            counters["lowercase_start"] += 1
        if "?" in normalized:
            counters["question"] += 1
        if re.search(r"\b(try|instead|another|personally|you could|what helped|alternative)\b", normalized, flags=re.IGNORECASE):
            counters["alternative"] += 1
        if re.search(r"\b(i|i'm|ive|i've|me|my|we|our|us|myself)\b", normalized, flags=re.IGNORECASE):
            counters["testimonial"] += 1
        if _word_count(normalized) <= 3:
            counters["reaction"] += 1
    counters["endings"] = endings
    return counters


def _lane_targets(comment_count: int, intent: str) -> Dict[str, int]:
    base = {
        "testimonial": 0,
        "alternative": 0,
        "contrarian": 0,
        "supportive": 0,
    }
    if comment_count <= 0:
        return base

    base["testimonial"] = max(1, int(round(comment_count * 0.25))) if comment_count >= 4 else 0
    base["alternative"] = max(1, int(round(comment_count * 0.15))) if comment_count >= 6 else 0

    lowered_intent = str(intent or "").lower()
    wants_debate = any(
        token in lowered_intent
        for token in (
            "rage bait",
            "ragebait",
            "contrarian",
            "debate",
            "hot take",
            "polariz",
            "controvers",
        )
    )
    if wants_debate:
        base["contrarian"] = 2 if comment_count >= 10 else 1
        if comment_count >= 20:
            base["contrarian"] = 3
    elif comment_count >= 15:
        base["contrarian"] = 1

    allocated = base["testimonial"] + base["alternative"] + base["contrarian"]
    if allocated > comment_count:
        overflow = allocated - comment_count
        for key in ("alternative", "contrarian", "testimonial"):
            while overflow > 0 and base[key] > 0:
                base[key] -= 1
                overflow -= 1

    base["supportive"] = max(0, comment_count - (base["testimonial"] + base["alternative"] + base["contrarian"]))
    return base


def _missing_lane_targets(
    accepted: List[str],
    targets: Dict[str, int],
) -> Dict[str, int]:
    counts = {"supportive": 0, "testimonial": 0, "alternative": 0, "contrarian": 0}
    for text in accepted:
        lane = _comment_lane(text)
        counts[lane] = counts.get(lane, 0) + 1
    return {key: max(0, int(targets.get(key, 0)) - int(counts.get(key, 0))) for key in counts}


async def fetch_campaign_style_profile(context_snapshot: Dict[str, Any]) -> Dict[str, Any]:
    now = time.time()
    profile_path = _style_profile_path()
    source_mtime = 0.0
    try:
        source_mtime = Path(profile_path).stat().st_mtime
    except Exception:
        source_mtime = 0.0

    cached_profile = _STYLE_PROFILE_CACHE.get("profile")
    cache_still_valid = (
        isinstance(cached_profile, dict)
        and _STYLE_PROFILE_CACHE.get("source_path") == profile_path
        and float(_STYLE_PROFILE_CACHE.get("source_mtime", 0.0)) == source_mtime
        and (now - float(_STYLE_PROFILE_CACHE.get("loaded_at_ts", 0.0))) <= STYLE_CACHE_TTL_SECONDS
    )
    if cache_still_valid:
        return cached_profile

    loaded = _load_style_profile_from_disk(profile_path)
    if loaded and int(loaded.get("sample_size") or 0) >= STYLE_PROFILE_MIN_SAMPLE_SIZE:
        _STYLE_PROFILE_CACHE.update(
            {
                "profile": loaded,
                "loaded_at_ts": now,
                "source_path": profile_path,
                "source_mtime": source_mtime,
            }
        )
        return loaded

    support = context_snapshot.get("supporting_comments") or []
    fallback_comments = [
        str((item or {}).get("text") or "").strip()
        for item in support
        if str((item or {}).get("text") or "").strip()
    ]
    profile = _build_style_profile_from_comments(
        fallback_comments,
        source="context_fallback",
        source_meta={
            "reason": "style_profile_unavailable_or_small",
            "profile_path": profile_path,
        },
    )
    if int(profile.get("sample_size") or 0) == 0:
        profile = _default_style_profile()
        profile["source_meta"] = {"reason": "no_style_samples_available", "profile_path": profile_path}

    _STYLE_PROFILE_CACHE.update(
        {
            "profile": profile,
            "loaded_at_ts": now,
            "source_path": profile_path,
            "source_mtime": source_mtime,
        }
    )
    return profile


def _missing_surface_targets(
    accepted: List[str],
    target_surface: Dict[str, Any],
) -> Dict[str, Any]:
    counters = _style_counters(accepted)
    ending_missing = {
        key: max(0, int((target_surface.get("endings") or {}).get(key, 0)) - int((counters.get("endings") or {}).get(key, 0)))
        for key in ("none", "period", "question", "exclaim")
    }
    accepted_count = len([x for x in accepted if str(x or "").strip()])
    uppercase_count = max(0, accepted_count - int(counters.get("lowercase_start", 0)))
    return {
        "endings": ending_missing,
        "reaction": max(0, int(target_surface.get("reaction", 0)) - int(counters.get("reaction", 0))),
        "testimonial": max(0, int(target_surface.get("testimonial", 0)) - int(counters.get("testimonial", 0))),
        "question": max(0, int(target_surface.get("question", 0)) - int(counters.get("question", 0))),
        "alternative": max(0, int(target_surface.get("alternative", 0)) - int(counters.get("alternative", 0))),
        "mention": max(0, int(target_surface.get("mention", 0)) - int(counters.get("mention", 0))),
        "lowercase_start": max(0, int(target_surface.get("lowercase_start", 0)) - int(counters.get("lowercase_start", 0))),
        "uppercase_start_min": max(0, int(target_surface.get("uppercase_start_min", 0)) - uppercase_count),
    }


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

    # Handle fenced payloads.
    payload_text = re.sub(r"^```(?:json)?\s*", "", payload_text, flags=re.IGNORECASE)
    payload_text = re.sub(r"\s*```$", "", payload_text).strip()

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
        # Last-chance fallback: parse line-oriented plain text/bullets.
        candidates: List[str] = []
        for line in payload_text.splitlines():
            text = str(line or "").strip()
            text = re.sub(r"^[\-\*\d\)\.\s]+", "", text).strip()
            if not text:
                continue
            if text.lower() in {"comments", "comments:", "output", "output:"}:
                continue
            if len(text) < 2:
                continue
            candidates.append(text)
        if candidates:
            return candidates
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
    target_mix: Dict[str, int],
    target_surface: Dict[str, Any],
    lane_targets: Dict[str, int],
    brand_plan: Dict[str, Any],
    brand_missing: Dict[str, int],
    op_anchors: List[str],
    max_unanchored_reactions: int,
) -> List[str]:
    out: List[str] = []
    seen_base = [str(x).strip() for x in accepted + existing_comments if str(x).strip()]
    accepted_counts = {"short": 0, "medium": 0, "long": 0}
    for item in accepted:
        accepted_counts[_length_bucket(item)] += 1
    surface_counts = _style_counters(accepted)
    ending_targets = target_surface.get("endings") or {}
    lane_counts = {"supportive": 0, "testimonial": 0, "alternative": 0, "contrarian": 0}
    for item in accepted:
        lane = _comment_lane(item)
        lane_counts[lane] = lane_counts.get(lane, 0) + 1
    brand_name = str(brand_plan.get("brand") or "").strip() or None
    brand_counts = _brand_counts(accepted, brand_name)
    brand_reco_target = int(brand_plan.get("recommendation_target", 0) or 0)
    unanchored_reactions = 0
    for item in accepted:
        if _is_op_anchored(item, op_anchors):
            continue
        if _infer_archetype(item) == "reaction" and _word_count(item) <= 3:
            unanchored_reactions += 1

    for raw in candidates:
        text = str(raw or "").strip()
        if not text:
            continue

        if _is_engagement_bait_cta(text):
            continue

        sanitized = sanitize_text_against_rules(text, rules_snapshot)
        sanitized = re.sub(r"\s+", " ", sanitized).strip()
        sanitized = _normalize_name_mentions(sanitized)
        sanitized = re.sub(r"\s+", " ", sanitized).strip()
        if not sanitized:
            continue

        validation = validate_text_against_rules(sanitized, rules_snapshot)
        if not validation.get("ok"):
            continue

        if _is_near_duplicate(sanitized, seen_base + out):
            continue

        bucket = _length_bucket(sanitized)
        # Soft control: avoid one bucket consuming almost the entire set.
        if accepted_counts[bucket] >= int(target_mix.get(bucket, 0)) + 2:
            continue

        ending_key = _ending_bucket(sanitized)
        if int(surface_counts["endings"].get(ending_key, 0)) >= int(ending_targets.get(ending_key, 0)) + 2:
            continue

        lane = _comment_lane(sanitized)
        if int(lane_counts.get(lane, 0)) >= int(lane_targets.get(lane, 0)) + 2:
            continue

        if brand_name and _has_nonorganic_brand_discovery(sanitized, brand_name):
            continue

        anchored = _is_op_anchored(sanitized, op_anchors)
        is_reaction = _infer_archetype(sanitized) == "reaction" and _word_count(sanitized) <= 3
        if not anchored:
            if not is_reaction:
                continue
            if unanchored_reactions >= max_unanchored_reactions:
                continue

        # Soft brand guardrail: when we still need brand recommendations,
        # reject a few non-brand candidates early in the attempt.
        if brand_name and int(brand_missing.get("recommendation", 0)) > 0:
            is_brand = _mentions_brand(sanitized, brand_name)
            still_needed = brand_reco_target - int(brand_counts.get("recommendation_count", 0))
            if not is_brand and still_needed > 0 and len(out) < still_needed:
                continue

        out.append(sanitized)
        accepted_counts[bucket] += 1
        surface_counts["endings"][ending_key] = int(surface_counts["endings"].get(ending_key, 0)) + 1
        lane_counts[lane] = int(lane_counts.get(lane, 0)) + 1
        if not anchored and is_reaction:
            unanchored_reactions += 1
        if brand_name and _mentions_brand(sanitized, brand_name):
            brand_counts["recommendation_count"] = int(brand_counts.get("recommendation_count", 0)) + 1
            if _has_mechanism_explanation(sanitized):
                brand_counts["justification_count"] = int(brand_counts.get("justification_count", 0)) + 1

    return out


def _build_generation_prompt(
    *,
    context_snapshot: Dict,
    intent: str,
    comment_count: int,
    existing_comments: List[str],
    rules_snapshot: Dict,
    remaining_attempt: int,
    style_profile: Dict[str, Any],
    mix_targets: Dict[str, int],
    mix_missing: Dict[str, int],
    surface_missing: Dict[str, Any],
    lane_targets: Dict[str, int],
    lane_missing: Dict[str, int],
    brand_plan: Dict[str, Any],
    brand_missing: Dict[str, int],
    op_anchors: List[str],
) -> str:
    op_post = context_snapshot.get("op_post") or {}

    op_text = str(op_post.get("text") or "").strip()

    negative_patterns = rules_snapshot.get("negative_patterns") or []
    vocab_patterns = rules_snapshot.get("vocabulary_guidance") or []

    forbidden_lines = []
    for phrase in negative_patterns[:120]:
        forbidden_lines.append(f"- {phrase}")
    for phrase in vocab_patterns[:120]:
        forbidden_lines.append(f"- {phrase}")

    existing_lines = [f"- {item}" for item in existing_comments[:80]]
    style_examples = [f"- {item}" for item in (style_profile.get("examples") or [])[:12]]
    endings = style_profile.get("endings") or {}
    archetypes = style_profile.get("archetype_distribution") or {}
    ending_none = float(endings.get("none", 0.50))
    ending_period = float(endings.get("period", 0.25))
    ending_question = float(endings.get("question", 0.15))
    ending_exclaim = float(endings.get("exclaim", 0.10))
    first_char_lower_ratio = float(style_profile.get("first_char_lower_ratio", 0.08))
    testimonial_ratio = float(style_profile.get("testimonial_ratio", 0.30))
    mention_ratio = float(style_profile.get("mention_ratio", 0.05))
    reaction_ratio = float(archetypes.get("reaction", 0.18))
    supportive_ratio = float(archetypes.get("supportive", 0.30))
    question_ratio = float(archetypes.get("question", 0.20))
    alternative_ratio = float(archetypes.get("alternative", 0.10))
    style_source = str(style_profile.get("source") or "unknown")
    style_sample_size = int(style_profile.get("sample_size") or 0)
    brand_name = str(brand_plan.get("brand") or "").strip()
    brand_target = int(brand_plan.get("recommendation_target", 0) or 0)
    brand_just_target = int(brand_plan.get("justification_target", 0) or 0)
    op_anchor_lines = [f"- {token}" for token in op_anchors[:20]]

    return f"""
You must generate exactly {comment_count} Facebook comments as strict JSON.

Output format (must be valid JSON only, no markdown):
{{"comments": ["comment one", "comment two"]}}

Rules:
- Return exactly {comment_count} unique comments.
- Keep comments relevant to the OP post and user intent.
- Match real organic Facebook style from ad comments. Avoid uniform writing.
- the batch must feel like an ecosystem written by different people, not one author.
- Keep messy human variance: some fragments, some lowercase starts, not every comment polished.
- Every output is an isolated TOP-LEVEL comment on the OP post.
- Never write as a reply to another comment. no "as someone said above", no "this thread explained", no direct back-and-forth.
- Only use information available from OP text + user intent. Do not inject extra specifics from hidden context.
- Think of the writer as someone who just read the OP and clicked comment.
- Keep each comment anchored to OP topic words or symptoms. no off-topic detours.
- Do not introduce random new conditions/treatments not implied by OP.
- Vary lengths aggressively:
  - short (<=4 words): target {mix_missing.get("short", 0)} still needed (overall target {mix_targets.get("short", 0)})
  - medium (5-24 words): target {mix_missing.get("medium", 0)} still needed (overall target {mix_targets.get("medium", 0)})
  - long (>=25 words): target {mix_missing.get("long", 0)} still needed (overall target {mix_targets.get("long", 0)})
- top-level lane mix (still needed this attempt):
  - supportive/validation comments: need {lane_missing.get("supportive", 0)} (overall target {lane_targets.get("supportive", 0)})
  - testimonial comments: need {lane_missing.get("testimonial", 0)} (overall target {lane_targets.get("testimonial", 0)})
  - alternative-solution comments: need {lane_missing.get("alternative", 0)} (overall target {lane_targets.get("alternative", 0)})
  - mild contrarian/rage-bait comments: need {lane_missing.get("contrarian", 0)} (overall target {lane_targets.get("contrarian", 0)})
- ecosystem role mix (still needed this attempt):
  - reaction micro-comments (1-3 words): need {surface_missing.get("reaction", 0)}
  - testimonial comments (first-person experience): need {surface_missing.get("testimonial", 0)}
  - question comments: need {surface_missing.get("question", 0)}
  - alternative-solution comments (e.g. "try X", "what helped me"): need {surface_missing.get("alternative", 0)}
  - name-mention comments: need {surface_missing.get("mention", 0)} (if used, mention as "First Last" only)
- punctuation/casing mix (still needed this attempt):
  - no end punctuation: need {(surface_missing.get("endings") or {}).get("none", 0)}
  - period ending: need {(surface_missing.get("endings") or {}).get("period", 0)}
  - question ending: need {(surface_missing.get("endings") or {}).get("question", 0)}
  - exclamation ending: need {(surface_missing.get("endings") or {}).get("exclaim", 0)}
  - lowercase starts: need {surface_missing.get("lowercase_start", 0)}
  - normal case starts (uppercase first letter): need {surface_missing.get("uppercase_start_min", 0)}
- Do not make everything lowercase. keep visible normal-case voices.
- hard floor: at least {max(1, int(math.ceil(max(float(MIN_NORMAL_CASE_RATIO), float(NORMAL_CASE_RATIO_FLOOR)) * comment_count)))} comments must start with normal case (uppercase first letter).
- if you mention a person, write plain name only like "Wanda Lobb". never use "@", "tag", or "tagging".
- brand recommendation targets:
  - brand in play: {brand_name if brand_name else "(none inferred)"}
  - explicit brand recommendations still needed: {brand_missing.get("recommendation", 0)} (overall target {brand_target})
  - brand recommendations with mechanism/justification still needed: {brand_missing.get("justification", 0)} (overall target {brand_just_target})
- For brand recommendations, include WHY it helped ("because", mechanism, root cause, reasoning), not only hype lines.
- Do not use non-organic discovery phrasing like: "switched to {brand_name}", "found {brand_name}", "ordered {brand_name}", "link above".
- No engagement-bait CTAs. never write lines like "tag a friend", "share this", "comment below", "follow", "dm me".
- observed style baseline from corpus:
  - approx {int(round(ending_none * 100))}% no end punctuation
  - approx {int(round(ending_period * 100))}% period ending
  - approx {int(round(ending_question * 100))}% question ending
  - approx {int(round(ending_exclaim * 100))}% exclamation ending
  - approx {int(round(first_char_lower_ratio * 100))}% start with lowercase first letter
  - approx {int(round(testimonial_ratio * 100))}% first-person experiential comments
  - approx {int(round(mention_ratio * 100))}% include a direct name-mention style
  - approx {int(round(reaction_ratio * 100))}% reaction micro-comments
  - approx {int(round(supportive_ratio * 100))}% supportive comments
  - approx {int(round(question_ratio * 100))}% question comments
  - approx {int(round(alternative_ratio * 100))}% alternative-solution comments
- Never make all comments sentence-perfect.
- If using contrarian/rage-bait comments, keep them mild and engagement-oriented, not abusive.
- Avoid policy-banned wording listed below.
- Do not include numbering or labels.
- Do not repeat or paraphrase too closely to existing comments.
- Never include markdown.

OP post context:
{op_text or "(no OP message available)"}

Supporting comments:
(intentionally not provided: outputs must be isolated top-level OP comments)

OP anchor terms (must stay close to these themes):
{chr(10).join(op_anchor_lines) if op_anchor_lines else "(none)"}

User intent:
{intent}

Existing comments to avoid (exact/near duplicates):
{chr(10).join(existing_lines) if existing_lines else "(none)"}

Real ad comment style examples (learn cadence, inconsistency, roughness):
{chr(10).join(style_examples) if style_examples else "(none)"}

Style profile source: {style_source}
Style profile sample size: {style_sample_size}

Forbidden words/patterns:
{chr(10).join(forbidden_lines) if forbidden_lines else "(none)"}

Attempt: {remaining_attempt}
""".strip()


def _build_brand_topup_prompt(
    *,
    context_snapshot: Dict[str, Any],
    intent: str,
    brand: str,
    comment_count: int,
    justification_count: int,
    existing_comments: List[str],
) -> str:
    op_post = context_snapshot.get("op_post") or {}
    op_text = str(op_post.get("text") or "").strip()
    existing_lines = [f"- {item}" for item in existing_comments[:120]]
    return f"""
Generate exactly {comment_count} isolated top-level Facebook comments as strict JSON.

Output format:
{{"comments": ["...", "..."]}}

Hard requirements:
- Every comment must be a top-level comment to the OP post, not a reply to other comments.
- Every comment must explicitly mention brand "{brand}".
- At least {justification_count} comments must include mechanism/justification language ("because", root cause, why it helps).
- Keep comments varied in length and voice.
- Avoid duplicates or near-duplicates of existing comments.
- no engagement-bait CTAs ("tag a friend", "share this", "comment below", "follow", "dm me").
- if mentioning a person, use plain "First Last" text only; never use "@" or "tagging".
- No markdown, no numbering.

OP post context:
{op_text or "(no OP message available)"}

Intent:
{intent}

Existing comments to avoid:
{chr(10).join(existing_lines) if existing_lines else "(none)"}
""".strip()


def _prepare_brand_topup_pool(
    *,
    candidates: List[str],
    existing_comments: List[str],
    rules_snapshot: Dict,
    brand: str,
    recommendation_target: int,
    justification_target: int,
) -> List[str]:
    out: List[str] = []
    seen = [str(x).strip() for x in existing_comments if str(x).strip()]
    recommendation_count = 0
    justification_count = 0

    for raw in candidates:
        text = str(raw or "").strip()
        if not text:
            continue
        if _is_engagement_bait_cta(text):
            continue
        sanitized = sanitize_text_against_rules(text, rules_snapshot)
        sanitized = re.sub(r"\s+", " ", sanitized).strip()
        sanitized = _normalize_name_mentions(sanitized)
        sanitized = re.sub(r"\s+", " ", sanitized).strip()
        if not sanitized:
            continue
        validation = validate_text_against_rules(sanitized, rules_snapshot)
        if not validation.get("ok"):
            continue
        if not _mentions_brand(sanitized, brand):
            continue
        if _is_near_duplicate(sanitized, seen + out):
            continue

        has_justification = _has_mechanism_explanation(sanitized)
        if justification_count < justification_target and not has_justification and len(out) < justification_target:
            continue

        out.append(sanitized)
        recommendation_count += 1
        if has_justification:
            justification_count += 1
        if recommendation_count >= recommendation_target:
            break

    return out


def _brand_replacement_indexes(comments: List[str], brand: str) -> List[int]:
    non_brand = []
    brand_without_why = []
    for idx, text in enumerate(comments):
        normalized = str(text or "").strip()
        if not normalized:
            non_brand.append(idx)
            continue
        if not _mentions_brand(normalized, brand):
            non_brand.append(idx)
            continue
        if not _has_mechanism_explanation(normalized):
            brand_without_why.append(idx)
    return non_brand + brand_without_why


def _strip_terminal_punctuation(text: str) -> str:
    stripped = re.sub(r"[.!?]+$", "", str(text or "").strip()).strip()
    return stripped or str(text or "").strip()


def _lowercase_first_alpha(text: str) -> str:
    raw = str(text or "")
    chars = list(raw)
    for idx, ch in enumerate(chars):
        if ch.isalpha():
            chars[idx] = ch.lower()
            break
    return "".join(chars)


def _uppercase_first_alpha(text: str) -> str:
    raw = str(text or "")
    chars = list(raw)
    for idx, ch in enumerate(chars):
        if ch.isalpha():
            chars[idx] = ch.upper()
            break
    return "".join(chars)


def _apply_surface_variability(
    comments: List[str],
    *,
    target_surface: Dict[str, Any],
) -> List[str]:
    if not comments:
        return comments

    out = [str(x).strip() for x in comments if str(x).strip()]
    if not out:
        return comments

    counters = _style_counters(out)
    ending_targets = target_surface.get("endings") or {}

    need_none = max(0, int(ending_targets.get("none", 0)) - int((counters.get("endings") or {}).get("none", 0)))
    if need_none > 0:
        ranked_indexes = sorted(
            [idx for idx, text in enumerate(out) if _ending_bucket(text) in {"period", "question", "exclaim"}],
            key=lambda i: _word_count(out[i]),
        )
        for idx in ranked_indexes:
            if need_none <= 0:
                break
            candidate = _strip_terminal_punctuation(out[idx])
            if not candidate:
                continue
            if candidate in out[:idx] + out[idx + 1 :]:
                continue
            out[idx] = candidate
            need_none -= 1

    counters = _style_counters(out)
    need_lower = max(
        0,
        int(target_surface.get("lowercase_start", 0)) - int(counters.get("lowercase_start", 0)),
    )
    if need_lower > 0:
        for idx, text in enumerate(out):
            if need_lower <= 0:
                break
            first = _first_alpha_char(text)
            if not first or first.islower():
                continue
            candidate = _lowercase_first_alpha(text)
            if not candidate:
                continue
            if candidate in out[:idx] + out[idx + 1 :]:
                continue
            out[idx] = candidate
            need_lower -= 1

    counters = _style_counters(out)
    uppercase_min = int(target_surface.get("uppercase_start_min", 0) or 0)
    lowercase_count = int(counters.get("lowercase_start", 0))
    uppercase_count = max(0, len(out) - lowercase_count)
    need_upper = max(0, uppercase_min - uppercase_count)
    if need_upper > 0:
        ranked_indexes = sorted(
            [idx for idx, text in enumerate(out) if (_first_alpha_char(text) or "").islower()],
            key=lambda i: _word_count(out[i]),
            reverse=True,
        )
        for idx in ranked_indexes:
            if need_upper <= 0:
                break
            candidate = _uppercase_first_alpha(out[idx])
            if not candidate:
                continue
            if candidate in out[:idx] + out[idx + 1 :]:
                continue
            out[idx] = candidate
            need_upper -= 1

    return out


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
    style_profile = await fetch_campaign_style_profile(context_snapshot)
    op_text = str((context_snapshot.get("op_post") or {}).get("text") or "").strip()
    op_anchors = _op_anchor_tokens(op_text)
    max_unanchored_reactions = 2 if count >= 10 else (1 if count >= 5 else 0)
    target_mix = _style_mix_targets(count, style_profile)
    target_surface = _style_surface_targets(count, style_profile)
    lane_targets = _lane_targets(count, normalized_intent)
    primary_brand = _detect_primary_brand(context_snapshot, normalized_intent)
    brand_plan = _brand_targets(count, brand=primary_brand)

    max_attempts = 5
    for attempt in range(1, max_attempts + 1):
        remaining = count - len(accepted)
        if remaining <= 0:
            break
        mix_missing = _missing_mix_targets(accepted, target_mix)
        surface_missing = _missing_surface_targets(accepted, target_surface)
        lane_missing = _missing_lane_targets(accepted, lane_targets)
        brand_missing = _missing_brand_targets(accepted, brand_plan)

        prompt = _build_generation_prompt(
            context_snapshot=context_snapshot,
            intent=normalized_intent,
            comment_count=remaining,
            existing_comments=existing + accepted,
            rules_snapshot=rules_snapshot,
            remaining_attempt=attempt,
            style_profile=style_profile,
            mix_targets=target_mix,
            mix_missing=mix_missing,
            surface_missing=surface_missing,
            lane_targets=lane_targets,
            lane_missing=lane_missing,
            brand_plan=brand_plan,
            brand_missing=brand_missing,
            op_anchors=op_anchors,
        )
        response_text = await _call_claude(prompt)
        candidates = _extract_json_comments(response_text)
        approved = _prepare_comment_pool(
            candidates=candidates,
            accepted=accepted,
            existing_comments=existing,
            rules_snapshot=rules_snapshot,
            target_mix=target_mix,
            target_surface=target_surface,
            lane_targets=lane_targets,
            brand_plan=brand_plan,
            brand_missing=brand_missing,
            op_anchors=op_anchors,
            max_unanchored_reactions=max_unanchored_reactions,
        )
        accepted.extend(approved)

        logger.info(
            (
                "Campaign AI generation attempt %s: received=%s approved=%s accumulated=%s "
                "target=%s mix=%s surface=%s brand=%s brand_missing=%s style_source=%s style_samples=%s"
            ),
            attempt,
            len(candidates),
            len(approved),
            len(accepted),
            count,
            target_mix,
            target_surface,
            brand_plan.get("brand"),
            brand_missing,
            style_profile.get("source"),
            style_profile.get("sample_size"),
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

    # Targeted brand top-up: if a brand is inferred (e.g. Nuora), ensure enough explicit
    # recommendations with justification without forcing this for brand-less contexts.
    brand_name = str(brand_plan.get("brand") or "").strip()
    if brand_name:
        missing_brand = _missing_brand_targets(final_comments, brand_plan)
        remediation_attempts = 2
        for _ in range(remediation_attempts):
            if int(missing_brand.get("recommendation", 0)) <= 0:
                break
            topup_prompt = _build_brand_topup_prompt(
                context_snapshot=context_snapshot,
                intent=normalized_intent,
                brand=brand_name,
                comment_count=int(missing_brand.get("recommendation", 0)),
                justification_count=int(missing_brand.get("justification", 0)),
                existing_comments=existing + final_comments,
            )
            topup_text = await _call_claude(topup_prompt)
            topup_candidates = _extract_json_comments(topup_text)
            replacements = _prepare_brand_topup_pool(
                candidates=topup_candidates,
                existing_comments=existing + final_comments,
                rules_snapshot=rules_snapshot,
                brand=brand_name,
                recommendation_target=int(missing_brand.get("recommendation", 0)),
                justification_target=int(missing_brand.get("justification", 0)),
            )
            if not replacements:
                break

            replace_indexes = _brand_replacement_indexes(final_comments, brand_name)
            replace_cursor = 0
            for replacement in replacements:
                if replace_cursor >= len(replace_indexes):
                    break
                idx = replace_indexes[replace_cursor]
                replace_cursor += 1
                if replacement in final_comments[:idx] + final_comments[idx + 1 :]:
                    continue
                final_comments[idx] = replacement

            missing_brand = _missing_brand_targets(final_comments, brand_plan)

    return _apply_surface_variability(final_comments, target_surface=target_surface)
