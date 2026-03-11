from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List


RULE_MIRROR_ROOT = Path(__file__).resolve().parent / "rules" / "reddit"

RULE_SOURCE_SPECS = [
    {
        "name": "great-writing-patterns.md",
        "source_path": "/Users/nikitalienov/Documents/writing/.claude/rules/great-writing-patterns.md",
        "mirror_path": str(RULE_MIRROR_ROOT / "great-writing-patterns.md"),
    },
    {
        "name": "negative-patterns.md",
        "source_path": "/Users/nikitalienov/Documents/writing/.claude/rules/negative-patterns.md",
        "mirror_path": str(RULE_MIRROR_ROOT / "negative-patterns.md"),
    },
    {
        "name": "vocabulary-guidance.md",
        "source_path": "/Users/nikitalienov/Documents/writing/.claude/rules/vocabulary-guidance.md",
        "mirror_path": str(RULE_MIRROR_ROOT / "vocabulary-guidance.md"),
    },
]

WRITING_RULE_SOURCE_PATHS = [spec["source_path"] for spec in RULE_SOURCE_SPECS]

OPERATOR_META_PATTERNS = [
    "checking profile",
    "checking eligibility",
    "profile eligibility",
    "eligibility check",
    "test post",
    "testing post",
    "testing reply",
    "automation check",
    "bot check",
    "flight check",
    "debug post",
    "debugging this",
]

GENERIC_FILLER_PATTERNS = [
    "just wanted to share",
    "thought i would ask",
    "does anyone have advice",
    "any thoughts",
]


def _collapse_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_rule_phrase(value: str) -> str:
    cleaned = _collapse_whitespace(value).strip("`'\"").strip()
    cleaned = cleaned.replace("“", '"').replace("”", '"').replace("’", "'")
    return cleaned


def _extract_quoted_phrases(line: str) -> List[str]:
    results = []
    for match in re.findall(r'"([^"]+)"', line):
        phrase = _normalize_rule_phrase(match)
        if phrase:
            results.append(phrase)
    return results


def _read_rule_documents() -> Dict[str, Dict[str, str]]:
    documents: Dict[str, Dict[str, str]] = {}
    for spec in RULE_SOURCE_SPECS:
        mirror_path = Path(spec["mirror_path"])
        content = mirror_path.read_text(encoding="utf-8")
        documents[spec["name"]] = {
            "source_path": spec["source_path"],
            "mirror_path": str(mirror_path),
            "content": content,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
    return documents


def _parse_negative_patterns(content: str) -> List[str]:
    patterns: List[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if len(cells) >= 2 and cells[0] and not cells[0].startswith(":") and cells[0] != "AI Word":
                phrase = _normalize_rule_phrase(cells[0])
                if phrase:
                    patterns.append(phrase)
            continue
        if not line.startswith("-"):
            continue
        quoted = _extract_quoted_phrases(line)
        if quoted:
            patterns.extend(quoted)
            continue
        bullet = _normalize_rule_phrase(line.lstrip("-").strip())
        if bullet:
            bullet = bullet.split(" - (", 1)[0].strip()
            bullet = bullet.split("    - Example:", 1)[0].strip()
            if bullet and not bullet.lower().startswith("example"):
                patterns.append(bullet)
    seen = set()
    deduped = []
    for pattern in patterns:
        key = pattern.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(pattern)
    return deduped


def _parse_vocabulary_guidance(content: str) -> List[str]:
    items: List[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line.startswith("- "):
            continue
        value = _normalize_rule_phrase(line[2:])
        if value:
            items.append(value)
    return items


@lru_cache(maxsize=2)
def get_writing_rule_snapshot(include_contents: bool = False) -> Dict[str, Any]:
    documents = _read_rule_documents()
    negative = documents["negative-patterns.md"]["content"]
    great = documents["great-writing-patterns.md"]["content"]
    vocab = documents["vocabulary-guidance.md"]["content"]
    snapshot: Dict[str, Any] = {
        "source_paths": [spec["source_path"] for spec in RULE_SOURCE_SPECS],
        "mirror_paths": [spec["mirror_path"] for spec in RULE_SOURCE_SPECS],
        "rule_source_hashes": {
            name: documents[name]["sha256"]
            for name in ("great-writing-patterns.md", "negative-patterns.md", "vocabulary-guidance.md")
        },
        "banned_patterns": _parse_negative_patterns(negative),
        "banned_vocabulary": _parse_vocabulary_guidance(vocab),
        "operator_meta_patterns": list(OPERATOR_META_PATTERNS),
        "generic_filler_patterns": list(GENERIC_FILLER_PATTERNS),
        "style_requirements": {
            "mixed_case_supported": True,
            "no_em_dash": True,
            "human_scale": True,
            "subreddit_relevant": True,
        },
    }
    if include_contents:
        snapshot["rule_contents"] = {
            "great-writing-patterns.md": great,
            "negative-patterns.md": negative,
            "vocabulary-guidance.md": vocab,
        }
    return snapshot
