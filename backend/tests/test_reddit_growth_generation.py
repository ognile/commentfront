import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reddit_growth_generation import RedditGrowthContentGenerator, validate_generated_text
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


def test_validate_generated_text_allows_anchor_context_overlap_when_nearby_top_terms_do_not_match():
    result = validate_generated_text(
        "Tell the nurse ahead of time that pelvic floor guarding is a concern so they slow down before the speculum part.",
        nearby_texts=[
            "how do you pick a gyn you can trust when you have been putting it off for years?",
            "i finally booked but i am already tense about the whole exam.",
        ],
        context_anchor_texts=[
            "pelvic floor tension makes exams harder",
            "they may need to slow down before the speculum part",
        ],
        require_context_overlap=True,
        persona_snapshot=get_reddit_persona_snapshot("reddit_catherine_emmar"),
        writing_rule_snapshot=get_writing_rule_snapshot(),
    )

    assert result["ok"] is True
    assert "pelvic" in result["context_overlap_terms"]


def test_comment_prompt_includes_anchor_terms_and_retry_feedback():
    generator = RedditGrowthContentGenerator()
    prompt = generator._comment_prompt(
        subreddit="women",
        thread_title="confused if men actually like their partners at all",
        thread_excerpt="his partner finished a masters and is job hunting while he resents doing chores.",
        thread_author="deadtracts",
        keywords=["relationship", "partner", "chores"],
        style_samples=[],
        conversation_context=[],
        recent_texts=[],
        same_thread_texts=[],
        same_profile_texts=[],
        persona_snapshot=get_reddit_persona_snapshot("reddit_catherine_emmar"),
        writing_rule_snapshot=get_writing_rule_snapshot(include_contents=True),
        retry_feedback={"mode": "submit_rejected", "last_error": "unable to create comment"},
        validation_feedback="does not reference the local conversation strongly enough",
    )

    assert "anchor terms from the thread and subreddit keywords" in prompt
    assert "rejected by reddit after submit" in prompt
    assert "does not reference the local conversation strongly enough" in prompt


def test_validate_generated_text_allows_mixed_case_for_proper_case_persona():
    result = validate_generated_text(
        "Open fissures after fluconazole would make me stop assuming uncomplicated yeast.",
        persona_snapshot=get_reddit_persona_snapshot("reddit_catherine_emmar"),
        writing_rule_snapshot=get_writing_rule_snapshot(),
    )

    assert result["ok"] is True
    assert result["word_count"] >= 10


def test_validate_generated_text_rejects_headline_title_case_for_sentence_comment():
    result = validate_generated_text(
        "Stop The Calls To Your Mother And Build An Inventory Of Legal Protections For This Relationship Status.",
        persona_snapshot=get_reddit_persona_snapshot("reddit_connor_esla"),
        writing_rule_snapshot=get_writing_rule_snapshot(),
    )

    assert result["ok"] is False
    assert any("title case" in item for item in result["violations"])


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


def test_choose_user_flair_falls_back_to_stable_visible_option():
    generator = RedditGrowthContentGenerator(api_key="")

    result = asyncio.run(
        generator.choose_user_flair(
            profile_name="reddit_catherine_emmar",
            subreddit="AskWomenOver40",
            available_options=["Single", "Divorced", "Married"],
            current_flair=None,
        )
    )

    assert result["choice"] in {"Single", "Divorced", "Married"}
    assert result["persona_snapshot"]["persona_id"] == "catherine_authority_frame"


def test_choose_user_flair_keeps_current_flair_when_it_still_matches():
    generator = RedditGrowthContentGenerator(api_key="")

    result = asyncio.run(
        generator.choose_user_flair(
            profile_name="reddit_amy_schaefera",
            subreddit="AskWomenOver40",
            available_options=["Single", "Divorced", "Married"],
            current_flair="Divorced",
        )
    )

    assert result["choice"] == "Divorced"


def test_generate_comment_retries_with_validation_feedback(monkeypatch):
    generator = RedditGrowthContentGenerator(api_key="")
    prompts = []
    responses = iter(
        [
            '{"text":"This sounds painful.","reasoning":"first pass"}',
            '{"text":"This relationship turns sour fast when one partner is job hunting and the other turns chores into a scorecard.","reasoning":"second pass"}',
        ]
    )

    async def fake_generate_json(prompt):
        prompts.append(prompt)
        return next(responses)

    monkeypatch.setattr(generator, "_generate_json", fake_generate_json)

    result = asyncio.run(
        generator.generate_comment(
            profile_name="reddit_catherine_emmar",
            subreddit="women",
            thread_title="confused if men actually like their partners at all",
            thread_excerpt="his partner finished a masters and is job hunting while he resents doing chores.",
            thread_author="deadtracts",
            keywords=["relationship", "partner", "chores"],
            style_samples=[],
            conversation_context=[],
            recent_texts=[],
            same_thread_texts=[],
            same_profile_texts=[],
        )
    )

    assert result.success is True
    assert len(prompts) == 2
    assert "previous draft failed validation" in prompts[1]


def test_preflight_manual_comment_passes_unchanged_when_aligned(monkeypatch):
    generator = RedditGrowthContentGenerator(api_key="")

    async def fake_generate_json(_prompt):
        raise RuntimeError("reddit content generation is unavailable")

    monkeypatch.setattr(generator, "_generate_json", fake_generate_json)

    result = asyncio.run(
        generator.preflight_manual_content(
            action_kind="comment_post",
            profile_name="reddit_catherine_emmar",
            subreddit="women",
            keywords=["relationship", "partner", "chores"],
            style_samples=[],
            conversation_context=[],
            recent_texts=[],
            same_thread_texts=[],
            same_profile_texts=[],
            text="This relationship turns sour fast when one partner is job hunting and the other turns chores into a scorecard.",
            thread_title="confused if men actually like their partners at all",
            thread_excerpt="his partner finished a masters and is job hunting while he resents doing chores.",
            thread_author="deadtracts",
            policy_metadata={"subreddit": "women"},
        )
    )

    assert result["ok"] is True
    assert result["repair_applied"] is False
    assert result["effective_params"]["text"].startswith("This relationship turns sour fast")


def test_preflight_manual_post_repairs_operator_meta_title(monkeypatch):
    generator = RedditGrowthContentGenerator(api_key="")

    async def fake_generate_json(_prompt):
        return """{
          "ok": false,
          "violations": ["operator/meta language", "community fit miss"],
          "repair_applied": true,
          "effective_params": {
            "title": "Does boric acid sting more for anyone if they start it right after their period?",
            "body": ""
          },
          "reasoning": "the repaired version asks a normal subreddit-fit question"
        }"""

    monkeypatch.setattr(generator, "_generate_json", fake_generate_json)

    result = asyncio.run(
        generator.preflight_manual_content(
            action_kind="create_post",
            profile_name="reddit_catherine_emmar",
            subreddit="Healthyhooha",
            keywords=["boric acid", "period"],
            style_samples=[],
            conversation_context=[
                {"title": "has anyone waited a few days after their period before using boric acid again?"},
                {"body_excerpt": "i'm trying to time it without making things worse"},
            ],
            recent_texts=[],
            same_profile_texts=[],
            title="image post api verification retry",
            body="",
            policy_metadata={"subreddit": "Healthyhooha"},
        )
    )

    assert result["ok"] is True
    assert result["repair_applied"] is True
    assert result["original_params"]["title"] == "image post api verification retry"
    assert result["effective_params"]["title"].startswith("Does boric acid sting more")


def test_preflight_manual_comment_ignores_unsolicited_review_rewrite(monkeypatch):
    generator = RedditGrowthContentGenerator(api_key="")

    async def fake_generate_json(_prompt):
        return """{
          "ok": true,
          "violations": [],
          "repair_applied": true,
          "effective_params": {
            "text": "This whole system is broken when an endocrinologist gets to act disgusted by your symptoms while pretending the condition is fake."
          },
          "reasoning": "unnecessary rewrite"
        }"""

    monkeypatch.setattr(generator, "_generate_json", fake_generate_json)

    original_text = "This relationship turns sour fast when one partner is job hunting and the other turns chores into a scorecard."
    result = asyncio.run(
        generator.preflight_manual_content(
            action_kind="comment_post",
            profile_name="reddit_catherine_emmar",
            subreddit="women",
            keywords=["relationship", "partner", "chores"],
            style_samples=[],
            conversation_context=[],
            recent_texts=[],
            same_thread_texts=[],
            same_profile_texts=[],
            text=original_text,
            thread_title="confused if men actually like their partners at all",
            thread_excerpt="his partner finished a masters and is job hunting while he resents doing chores.",
            thread_author="deadtracts",
            policy_metadata={"subreddit": "women"},
        )
    )

    assert result["ok"] is True
    assert result["repair_applied"] is False
    assert result["effective_params"]["text"] == original_text


def test_preflight_manual_post_blocks_when_repair_stays_misaligned(monkeypatch):
    generator = RedditGrowthContentGenerator(api_key="")

    async def fake_generate_json(_prompt):
        return """{
          "ok": false,
          "violations": ["still sounds like testing"],
          "repair_applied": true,
          "effective_params": {
            "title": "api verification retry",
            "body": "proof matrix rerun"
          },
          "reasoning": "bad repair"
        }"""

    monkeypatch.setattr(generator, "_generate_json", fake_generate_json)

    result = asyncio.run(
        generator.preflight_manual_content(
            action_kind="create_post",
            profile_name="reddit_amy_schaefera",
            subreddit="Healthyhooha",
            keywords=["boric acid", "period"],
            style_samples=[],
            conversation_context=[
                {"title": "has anyone waited a few days after their period before using boric acid again?"},
            ],
            recent_texts=[],
            same_profile_texts=[],
            title="image post api verification retry",
            body="",
            policy_metadata={"subreddit": "Healthyhooha"},
        )
    )

    assert result["ok"] is False
    assert any("operator/meta" in item or "testing" in item for item in result["violations"])


def test_preflight_manual_post_rejects_clinical_repair_that_reads_like_an_article(monkeypatch):
    generator = RedditGrowthContentGenerator(api_key="")

    async def fake_generate_json(_prompt):
        return """{
          "ok": true,
          "violations": [],
          "repair_applied": true,
          "effective_params": {
            "title": "Clinical observations on dietary influence and vaginal pH levels",
            "body": "Evaluating the efficacy of dietary interventions like pineapple juice requires looking at actual metabolic breakdown. While anecdotal reports suggest a shift in acidity, clinical evidence remains sparse regarding immediate topical or systemic aromatic changes."
          },
          "reasoning": "bad formal repair"
        }"""

    monkeypatch.setattr(generator, "_generate_json", fake_generate_json)

    result = asyncio.run(
        generator.preflight_manual_content(
            action_kind="create_post",
            profile_name="reddit_catherine_emmar",
            subreddit="Healthyhooha",
            keywords=["pineapple", "vaginal health"],
            style_samples=[],
            conversation_context=[
                {
                    "title": "Does pineapple juice really work?",
                    "body_excerpt": "Does pineapple really change the taste and how long would it take to work?",
                }
            ],
            recent_texts=[],
            same_profile_texts=[],
            title="image post api verification retry",
            body="",
            policy_metadata={"subreddit": "Healthyhooha"},
        )
    )

    assert result["ok"] is False
    assert any("article" in item or "formal" in item or "clinical" in item for item in result["violations"])


def test_preflight_manual_post_rejects_clinical_reality_repair(monkeypatch):
    generator = RedditGrowthContentGenerator(api_key="")

    async def fake_generate_json(_prompt):
        return """{
          "ok": true,
          "violations": [],
          "repair_applied": true,
          "effective_params": {
            "title": "The clinical reality of using pineapple to modify vaginal chemistry.",
            "body": "I have identified a pattern where dietary enzymes are neutralized during the digestive process. According to metabolic research, this prevents the fruit from producing any significant chemical change in the local environment."
          },
          "reasoning": "bad formal repair"
        }"""

    monkeypatch.setattr(generator, "_generate_json", fake_generate_json)

    result = asyncio.run(
        generator.preflight_manual_content(
            action_kind="create_post",
            profile_name="reddit_catherine_emmar",
            subreddit="Healthyhooha",
            keywords=["pineapple", "hooha"],
            style_samples=[],
            conversation_context=[
                {
                    "title": "Does pineapple juice really work?",
                    "body_excerpt": "Does pineapple really change anything and how long would it take to work?",
                }
            ],
            recent_texts=[],
            same_profile_texts=[],
            title="image post api verification retry",
            body="",
            policy_metadata={"subreddit": "Healthyhooha"},
        )
    )

    assert result["ok"] is False
    assert any("article" in item or "clinical" in item or "formal" in item for item in result["violations"])


def test_validate_generated_text_requires_distinctive_thread_detail_not_just_generic_overlap():
    result = validate_generated_text(
        "Stop The Calls To Your Mother And Build An Inventory Of Legal Protections For This Relationship Status. Secure Your Solo Route Now.",
        context_anchor_texts=[
            "Is there anyone here who has remained voluntarily celibate all your life?",
            "I'm 20. I'm not interested in the opposite (or any) gender, or being in a relationship at all. My mom calls me childish for that, but I can't change how I feel; I don't feel attracted to anyone.",
            "advice",
            "relationship",
            "family",
            "work",
        ],
        require_context_overlap=True,
        persona_snapshot=get_reddit_persona_snapshot("reddit_connor_esla"),
        writing_rule_snapshot=get_writing_rule_snapshot(),
    )

    assert result["ok"] is False
    assert any("concrete thread detail" in item for item in result["violations"])
    assert "celibate" in result["distinctive_anchor_terms"]
    assert result["distinctive_overlap_terms"] == []
