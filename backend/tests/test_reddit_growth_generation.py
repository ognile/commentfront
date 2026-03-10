from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reddit_growth_generation import validate_generated_text


def test_validate_generated_text_rejects_banned_pattern_and_em_dash():
    result = validate_generated_text("here's the thing: this isn't just helpful — it's magic.")

    assert result["ok"] is False
    assert any("banned pattern" in item for item in result["violations"])
    assert any("em dash" in item for item in result["violations"])


def test_validate_generated_text_rejects_duplicate_generation():
    result = validate_generated_text(
        "this helped me ask better follow-up questions at my appointment.",
        recent_texts=["this helped me ask better follow-up questions at my appointment."],
    )

    assert result["ok"] is False
    assert any("duplicates prior generated text" in item for item in result["violations"])


def test_validate_generated_text_rejects_operator_meta_language():
    result = validate_generated_text("checking profile eligibility before posting this.")

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
    )

    assert result["ok"] is False
    assert any("local conversation" in item for item in result["violations"])
