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
