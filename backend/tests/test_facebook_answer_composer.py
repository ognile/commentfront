import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fb_selectors
import gemini_vision


def test_answer_composer_is_treated_as_comment_input_variant():
    selectors = " ".join(fb_selectors.COMMENT["comment_input"]).lower()

    assert "write an answer" in selectors
    assert "answer" in gemini_vision.ELEMENT_PROMPTS["comment_input"].lower()
    assert "write an answer" in gemini_vision.VERIFICATION_PROMPTS["comments_opened"].lower()
