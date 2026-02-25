import sys
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from premium_content import _caption_similarity, _dedupe_caption_candidates, _strip_prompt_tail, generate_post_bundle


def test_strip_prompt_tail_removes_persona_leakage():
    caption = "spent some time outside today and it felt good to reset. supportive woman in menopause community."
    assert _strip_prompt_tail(caption) == "spent some time outside today and it felt good to reset."


def test_dedupe_caption_candidates_prefers_non_recent_option():
    base = "kept things simple today and that honestly helped a lot."
    pool = [
        "kept things simple today and that honestly helped a lot.",
        "slow morning, warm coffee, and a better mood after a long week.",
    ]
    recent = [base]
    selected = _dedupe_caption_candidates(base, pool, recent)
    assert selected == "slow morning, warm coffee, and a better mood after a long week."


def test_dedupe_caption_candidates_avoids_near_duplicate_option():
    base = "slow morning, warm coffee, and a better mood after a long week."
    pool = [
        "slow morning warm coffee and a better mood after a long week",
        "gave myself a slower start and felt more balanced by lunch.",
    ]
    recent = ["slow morning, warm coffee, and a better mood after a long week."]
    selected = _dedupe_caption_candidates(base, pool, recent, threshold=0.90)
    assert selected == "gave myself a slower start and felt more balanced by lunch."
    assert _caption_similarity(recent[0], pool[0]) >= 0.90


def test_generate_post_bundle_skips_image_when_not_required():
    result = asyncio.run(
        generate_post_bundle(
            profile_name="Wanda Lobb",
            profile_config={"character_profile": {}, "content_policy": {}, "execution_policy": {}},
            post_kind="character",
            cycle_index=1,
            rules_snapshot={"negative_patterns": [], "vocabulary_guidance": []},
            require_image=False,
        )
    )
    assert result["success"] is True
    assert result["image_path"] is None
    assert result["image_generation"]["skipped"] is True
