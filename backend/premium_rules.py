"""
Rule snapshot management and text policy checks for premium automation.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _strip_wrapping_quotes(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and ((text[0] == '"' and text[-1] == '"') or (text[0] == "'" and text[-1] == "'")):
        return text[1:-1].strip()
    return text


def _normalize_rule_entry(value: str) -> str:
    text = value.strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _phrase_variants(phrase: str) -> List[str]:
    """
    Build match variants from rule phrase by removing common placeholder tokens.
    Example: 'The best part? Y.' -> ['the best part? y.', 'the best part?']
    """
    variants: List[str] = []
    raw = _normalize_rule_entry(phrase)
    if raw:
        variants.append(raw)

    simplified = re.sub(r"\b[XYZ]\b\.?", "", raw, flags=re.IGNORECASE)
    simplified = re.sub(r"\s+", " ", simplified).strip(" .-")
    if simplified:
        variants.append(simplified)

    dedup = []
    seen = set()
    for variant in variants:
        key = variant.lower()
        if key in seen:
            continue
        seen.add(key)
        dedup.append(variant)
    return dedup


def parse_negative_patterns(text: str) -> List[str]:
    """
    Parse .md content into disallowed pattern list.
    Supports bullet points and markdown tables.
    """
    entries: List[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Skip headers
        if line.startswith("#"):
            continue

        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not cells:
                continue
            first = cells[0].replace("❌", "").strip()
            if not first or first.lower() in {":---", "ai word"}:
                continue
            entries.append(_normalize_rule_entry(_strip_wrapping_quotes(first)))
            continue

        if line.startswith("-"):
            phrase = line[1:].strip()
            if not phrase:
                continue
            # Remove inline explanatory suffixes when clear delimiter exists.
            if " - (" in phrase:
                phrase = phrase.split(" - (", 1)[0].strip()
            if phrase.lower().startswith("example:"):
                continue
            phrase = _strip_wrapping_quotes(phrase)
            phrase = _normalize_rule_entry(phrase)
            if phrase:
                entries.append(phrase)
            continue

    # Deduplicate while preserving order.
    seen = set()
    result = []
    for item in entries:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def parse_vocabulary_guidance(text: str) -> List[str]:
    """
    Parse vocabulary guidance markdown bullet list into disallowed phrase list.
    """
    entries: List[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-"):
            phrase = _normalize_rule_entry(_strip_wrapping_quotes(line[1:].strip()))
            if phrase:
                entries.append(phrase)

    seen = set()
    result = []
    for item in entries:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def load_rule_texts_from_paths(source_paths: Dict[str, str]) -> Tuple[str, str]:
    """
    Load rule texts from provided local filesystem paths.
    """
    negative_path = source_paths.get("negative_patterns_path")
    vocab_path = source_paths.get("vocabulary_guidance_path")

    if not negative_path or not vocab_path:
        raise ValueError("source_paths must include negative_patterns_path and vocabulary_guidance_path")

    negative_text = Path(negative_path).read_text()
    vocab_text = Path(vocab_path).read_text()
    return negative_text, vocab_text


def _compute_source_sha(negative_text: str, vocabulary_text: str) -> str:
    payload = f"NEGATIVE\n{negative_text}\nVOCAB\n{vocabulary_text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_rules_snapshot(
    *,
    negative_patterns_text: str,
    vocabulary_guidance_text: str,
    source_paths: Optional[Dict[str, str]] = None,
    source_sha: Optional[str] = None,
) -> Dict:
    """
    Build normalized rule snapshot payload stored in premium state.
    """
    computed_sha = source_sha or _compute_source_sha(negative_patterns_text, vocabulary_guidance_text)
    return {
        "version": computed_sha[:12],
        "source_sha": computed_sha,
        "source_paths": source_paths or {},
        "synced_at": _utc_iso(),
        "negative_patterns": parse_negative_patterns(negative_patterns_text),
        "vocabulary_guidance": parse_vocabulary_guidance(vocabulary_guidance_text),
        "raw": {
            "negative_patterns_text": negative_patterns_text,
            "vocabulary_guidance_text": vocabulary_guidance_text,
        },
    }


def enforce_casing_mode(text: str, mode: str) -> str:
    if mode == "strict_lowercase":
        return text.lower()
    if mode == "mostly_lowercase":
        # Keep proper nouns if present but suppress all-caps/noisy text.
        if sum(1 for ch in text if ch.isupper()) > max(6, len(text) // 3):
            return text.lower()
        return text
    return text


def validate_text_against_rules(text: str, snapshot: Optional[Dict]) -> Dict:
    """
    Return policy compliance diagnostics for generated text.
    """
    if not snapshot:
        return {
            "ok": False,
            "errors": ["rules snapshot missing"],
            "violations": [],
        }

    lowered = text.lower()
    violations: List[Dict[str, str]] = []

    for phrase in snapshot.get("negative_patterns", []):
        p = str(phrase).strip()
        if len(p) < 3:
            continue
        for variant in _phrase_variants(p):
            if len(variant) < 3:
                continue
            if variant.lower() in lowered:
                violations.append({"category": "negative_patterns", "phrase": p, "matched_variant": variant})
                break

    for phrase in snapshot.get("vocabulary_guidance", []):
        p = str(phrase).strip()
        if len(p) < 3:
            continue
        for variant in _phrase_variants(p):
            if len(variant) < 3:
                continue
            if variant.lower() in lowered:
                violations.append({"category": "vocabulary_guidance", "phrase": p, "matched_variant": variant})
                break

    return {
        "ok": len(violations) == 0,
        "errors": [],
        "violations": violations,
    }


def sanitize_text_against_rules(text: str, snapshot: Optional[Dict]) -> str:
    """
    Best-effort sanitization by removing direct disallowed phrase matches.
    """
    if not snapshot:
        return text

    sanitized = text
    candidates = list(snapshot.get("negative_patterns", [])) + list(snapshot.get("vocabulary_guidance", []))
    for phrase in candidates:
        p = str(phrase).strip()
        if len(p) < 3:
            continue
        for variant in _phrase_variants(p):
            if len(variant) < 3:
                continue
            sanitized = re.sub(re.escape(variant), "", sanitized, flags=re.IGNORECASE)

    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    return sanitized
