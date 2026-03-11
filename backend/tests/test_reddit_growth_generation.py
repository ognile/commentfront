from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reddit_growth_generation import validate_generated_text
from reddit_persona_registry import get_reddit_persona_snapshot
from reddit_writing_rules import get_writing_rule_snapshot


def test_validate_generated_text_rejects_banned_pattern_and_em_dash():
    result = validate_generated_text(
        "but here's the thing: this isn't just helpful — it's magic.",
        persona_snapshot=get_reddit_persona_snapshot("reddit_amy_schaefera"),
        writing_rule_snapshot=get_writing_rule_snapshot(),
    )

    assert result["ok"] is False
    assert any("banned pattern" in item for item in result["violations"])
    assert any("em dash" in item for item in result["violations"])


def test_validate_generated_text_rejects_duplicate_generation():
    result = validate_generated_text(
        "this helped me ask better follow-up questions at my appointment.",
        recent_texts=["this helped me ask better follow-up questions at my appointment."],
        persona_snapshot=get_reddit_persona_snapshot("reddit_mary_miaby"),
        writing_rule_snapshot=get_writing_rule_snapshot(),
    )

    assert result["ok"] is False
    assert any("duplicates prior generated text" in item for item in result["violations"])


def test_validate_generated_text_rejects_operator_meta_language():
    result = validate_generated_text(
        "checking profile eligibility before posting this.",
        persona_snapshot=get_reddit_persona_snapshot("reddit_connor_esla"),
        writing_rule_snapshot=get_writing_rule_snapshot(),
    )

    assert result["ok"] is False
    assert any("operator/meta language" in item for item in result["violations"])


def test_validate_generated_text_rejects_nearby_duplicate_and_requires_context_overlap():
    result = validate_generated_text(
        "boric acid timing question after my period",
        nearby_texts=[
            "boric acid timing question after my period",
            "has anyone waited a few days after their period before using boric acid again?",
        ],
        require_context_overlap=True,
        persona_snapshot=get_reddit_persona_snapshot("reddit_amy_schaefera"),
        writing_rule_snapshot=get_writing_rule_snapshot(),
    )

    assert result["ok"] is False
    assert any("nearby subreddit content" in item for item in result["violations"])


def test_validate_generated_text_flags_missing_context_overlap():
    result = validate_generated_text(
        "i am trying to decide what gym shoes to buy this month",
        nearby_texts=[
            "my biopsy recovery has been more crampy than i expected",
            "did anyone else bleed for a couple days after the biopsy?",
        ],
        require_context_overlap=True,
        persona_snapshot=get_reddit_persona_snapshot("reddit_cloudia_merra"),
        writing_rule_snapshot=get_writing_rule_snapshot(),
    )

    assert result["ok"] is False
    assert any("local conversation" in item for item in result["violations"])


def test_validate_generated_text_allows_mixed_case_for_proper_case_persona():
    result = validate_generated_text(
        "Open fissures after fluconazole would make me stop assuming uncomplicated yeast.",
        persona_snapshot=get_reddit_persona_snapshot("reddit_catherine_emmar"),
        writing_rule_snapshot=get_writing_rule_snapshot(),
    )

    assert result["ok"] is True
    assert result["word_count"] >= 10


def test_validate_generated_text_rejects_wrong_case_style_for_lowercase_persona():
    result = validate_generated_text(
        "This should stay lowercase for Amy.",
        persona_snapshot=get_reddit_persona_snapshot("reddit_amy_schaefera"),
        writing_rule_snapshot=get_writing_rule_snapshot(),
    )

    assert result["ok"] is False
    assert any("case style" in item for item in result["violations"])


def test_validate_generated_text_rejects_same_thread_semantic_clone():
    result = validate_generated_text(
        "i had one stretch where i was pouring lukewarm water from a cup every single time i peed because even air felt mean.",
        same_thread_texts=[
            "i had one flare where i was standing in the shower crying because even air felt rude and warm water during peeing helped."
        ],
        persona_snapshot=get_reddit_persona_snapshot("reddit_cloudia_merra"),
        writing_rule_snapshot=get_writing_rule_snapshot(),
    )

    assert result["ok"] is False
    assert any("thread" in item or "opening move" in item for item in result["violations"])


def test_writing_rule_snapshot_contains_exact_hashes_and_paths():
    snapshot = get_writing_rule_snapshot()

    assert snapshot["source_paths"] == [
        "/Users/nikitalienov/Documents/writing/.claude/rules/great-writing-patterns.md",
        "/Users/nikitalienov/Documents/writing/.claude/rules/negative-patterns.md",
        "/Users/nikitalienov/Documents/writing/.claude/rules/vocabulary-guidance.md",
    ]
    assert snapshot["rule_source_hashes"]["great-writing-patterns.md"] == "5b8cb44fceb640b2224a04c950a2f79ebebef863e9ac5b43401e73d3123df94f"
    assert snapshot["rule_source_hashes"]["negative-patterns.md"] == "0676cd003f0d8c9382378c364a26e99327c21272aad65bd95bee67a3e78e18a2"
    assert snapshot["rule_source_hashes"]["vocabulary-guidance.md"] == "74a10459bc0d72cfddb6ed26cfa4727069904c39e13888b0e9c3cc94396b0bc5"


def test_writing_rule_snapshot_does_not_ban_recommended_replacement_words():
    snapshot = get_writing_rule_snapshot()

    assert "this" not in [item.lower() for item in snapshot["banned_patterns"]]
    assert "that [describing something]" in [item.lower() for item in snapshot["banned_patterns"]]
