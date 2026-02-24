import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from premium_rules import (
    build_rules_snapshot,
    parse_negative_patterns,
    parse_vocabulary_guidance,
    sanitize_text_against_rules,
    validate_text_against_rules,
)


NEGATIVE_SAMPLE = """
## This is a strict list
- "The best part? Y."
- "But here's the thing:"
| ❌ AI Word | ✅ Human Alternatives |
| :--- | :--- |
| Leverage | Use |
"""

VOCAB_SAMPLE = """
- free balling
- just ride through it
"""


def test_rule_parsing_and_validation_detects_violations():
    snapshot = build_rules_snapshot(
        negative_patterns_text=NEGATIVE_SAMPLE,
        vocabulary_guidance_text=VOCAB_SAMPLE,
        source_paths={"negative_patterns_path": "/tmp/a", "vocabulary_guidance_path": "/tmp/b"},
    )

    negative = parse_negative_patterns(NEGATIVE_SAMPLE)
    vocab = parse_vocabulary_guidance(VOCAB_SAMPLE)

    assert any("The best part?" in p for p in negative)
    assert any("Leverage" in p for p in negative)
    assert "free balling" in vocab

    text = "The best part? free balling wins every time."
    validation = validate_text_against_rules(text, snapshot)
    assert validation["ok"] is False
    assert len(validation["violations"]) >= 2


def test_sanitize_removes_disallowed_phrases():
    snapshot = build_rules_snapshot(
        negative_patterns_text=NEGATIVE_SAMPLE,
        vocabulary_guidance_text=VOCAB_SAMPLE,
        source_paths={},
    )

    text = "The best part? free balling wins every time."
    sanitized = sanitize_text_against_rules(text, snapshot)
    assert "best part" not in sanitized.lower()
    assert "free balling" not in sanitized.lower()
