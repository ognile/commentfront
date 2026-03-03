import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
import campaign_ai
from main import (
    CampaignAIContextRequest,
    CampaignAIGenerateRequest,
    CampaignAIRegenerateOneRequest,
)


VALID_URL = "https://www.facebook.com/permalink.php?story_fbid=123456&id=987654321"
PFBID_URL = (
    "https://www.facebook.com/permalink.php?"
    "story_fbid=pfbid02iUeBP7zaMfL7hyzKtLXHo2c2Bi7SyYe9gQkq6ptPJioqYEdX6nWPGgoUDy86EKxJl"
    "&id=61574636237654"
)


@pytest.fixture(autouse=True)
def isolate_queue_and_drafts(tmp_path, monkeypatch):
    old_queue_path = main.queue_manager.file_path
    old_draft_path = main.draft_manager.file_path

    main.queue_manager.file_path = str(tmp_path / "campaign_queue.json")
    main.queue_manager.campaigns = {}
    main.queue_manager.history = []
    main.queue_manager.processor_state = {
        "is_running": False,
        "current_campaign_id": None,
        "last_processed_at": None,
    }
    main.queue_manager.save()

    main.draft_manager.file_path = str(tmp_path / "campaign_drafts.json")
    main.draft_manager.drafts = {}
    main.draft_manager.save()

    async def _noop_broadcast(*_args, **_kwargs):
        return None

    monkeypatch.setattr(main, "broadcast_update", _noop_broadcast)
    campaign_ai._STYLE_PROFILE_CACHE.update(
        {
            "profile": None,
            "loaded_at_ts": 0.0,
            "source_path": "",
            "source_mtime": 0.0,
        }
    )

    yield

    main.queue_manager.file_path = old_queue_path
    main.draft_manager.file_path = old_draft_path
    main.queue_manager.campaigns = {}
    main.queue_manager.history = []
    main.draft_manager.drafts = {}
    campaign_ai._STYLE_PROFILE_CACHE.update(
        {
            "profile": None,
            "loaded_at_ts": 0.0,
            "source_path": "",
            "source_mtime": 0.0,
        }
    )


async def _fake_context(_url: str):
    return {
        "context_id": "ctx_1",
        "url": VALID_URL,
        "op_post": {"id": "post_1", "text": "need help with strategy"},
        "supporting_comments": [
            {"id": "c1", "text": "totally agree"},
            {"id": "c2", "text": "i had this issue too"},
        ],
        "source_meta": {"token_source": "FACEBOOK_PAGE_ACCESS_TOKEN"},
    }


def test_campaign_ai_context_returns_snapshot(monkeypatch):
    monkeypatch.setattr(main, "fetch_campaign_context", _fake_context)

    result = asyncio.run(
        main.campaign_ai_context(
            CampaignAIContextRequest(url=VALID_URL),
            current_user={"username": "tester"},
        )
    )

    assert result["url"] == VALID_URL
    assert result["op_post"]["id"] == "post_1"
    assert len(result["supporting_comments"]) == 2
    assert result["source_meta"]["token_source"] == "FACEBOOK_PAGE_ACCESS_TOKEN"


def test_campaign_ai_context_propagates_campaign_ai_error(monkeypatch):
    async def _raise_context_error(_url: str):
        raise main.CampaignAIError(403, "context token missing")

    monkeypatch.setattr(main, "fetch_campaign_context", _raise_context_error)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            main.campaign_ai_context(
                CampaignAIContextRequest(url=VALID_URL),
                current_user={"username": "tester"},
            )
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "context token missing"


def test_resolve_post_id_uses_direct_story_fbid_reference():
    resolved = asyncio.run(campaign_ai._resolve_post_id(PFBID_URL, "token"))
    assert resolved == (
        "61574636237654_"
        "pfbid02iUeBP7zaMfL7hyzKtLXHo2c2Bi7SyYe9gQkq6ptPJioqYEdX6nWPGgoUDy86EKxJl"
    )


def test_fetch_context_allows_url_owner_mismatch(monkeypatch):
    post_id = "61574636237654_122164258202821207"

    async def _fake_resolve_post_id(_url: str, _token: str):
        return post_id

    async def _fake_graph_get(path: str, params, token: str):
        assert token == "token"
        if path == post_id:
            return {
                "id": "663571616828831_122164258202821207",
                "message": "post text",
                "from": {"id": "663571616828831", "name": "Alicia Darling"},
                "permalink_url": "https://www.facebook.com/permalink.php?story_fbid=abc&id=61574636237654",
                "created_time": "2026-03-03T00:00:00+0000",
            }
        if path == f"{post_id}/comments":
            return {
                "data": [
                    {
                        "id": "c1",
                        "message": "first",
                        "from": {"id": "1", "name": "Commenter 1"},
                        "permalink_url": "https://www.facebook.com/comment/1",
                        "created_time": "2026-03-03T00:01:00+0000",
                    },
                    {
                        "id": "c2",
                        "message": "second",
                        "from": {"id": "2", "name": "Commenter 2"},
                        "permalink_url": "https://www.facebook.com/comment/2",
                        "created_time": "2026-03-03T00:02:00+0000",
                    },
                ]
            }
        raise AssertionError(f"Unexpected path: {path}")

    monkeypatch.setattr(campaign_ai, "_resolve_post_id", _fake_resolve_post_id)
    monkeypatch.setattr(campaign_ai, "_graph_get", _fake_graph_get)

    context = asyncio.run(campaign_ai._fetch_context_with_token(PFBID_URL, "token", "FACEBOOK_PAGE_ACCESS_TOKEN"))

    assert context["op_post"]["author_id"] == "663571616828831"
    assert len(context["supporting_comments"]) == 2
    assert context["source_meta"]["url_page_id"] == "61574636237654"
    assert context["source_meta"]["post_owner_id"] == "663571616828831"
    assert context["source_meta"]["url_page_id_match"] is False
    assert context["source_meta"]["controlled_page_validated"] is True


def test_style_mix_targets_handles_single_comment():
    targets = campaign_ai._style_mix_targets(
        1,
        {
            "length_distribution": {
                "short": 0.34,
                "medium": 0.33,
                "long": 0.33,
            }
        },
    )

    assert sum(targets.values()) == 1
    assert all(value >= 0 for value in targets.values())


def test_fetch_campaign_style_profile_prefers_snapshot_file(tmp_path, monkeypatch):
    style_file = tmp_path / "style.json"
    style_file.write_text(
        """
        {
          "source": "snapshot",
          "sample_size": 500,
          "length_distribution": {"short": 0.2, "medium": 0.5, "long": 0.3},
          "endings": {"none": 0.5, "period": 0.2, "question": 0.2, "exclaim": 0.1},
          "first_char_lower_ratio": 0.08,
          "mention_ratio": 0.05,
          "testimonial_ratio": 0.3,
          "archetype_distribution": {
            "reaction": 0.2,
            "supportive": 0.3,
            "question": 0.2,
            "testimonial": 0.2,
            "alternative": 0.1
          },
          "examples": ["same", "facts"]
        }
        """.strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("CAMPAIGN_AI_STYLE_PROFILE_PATH", str(style_file))

    profile = asyncio.run(
        campaign_ai.fetch_campaign_style_profile(
            {
                "supporting_comments": [
                    {"text": "fallback one"},
                    {"text": "fallback two"},
                ]
            }
        )
    )

    assert profile["source"] == "snapshot"
    assert profile["sample_size"] == 500
    assert profile["examples"] == ["same", "facts"]


def test_fetch_campaign_style_profile_falls_back_to_context(monkeypatch):
    monkeypatch.setenv("CAMPAIGN_AI_STYLE_PROFILE_PATH", "/tmp/definitely-missing-style-profile.json")

    profile = asyncio.run(
        campaign_ai.fetch_campaign_style_profile(
            {
                "supporting_comments": [
                    {"text": "same here"},
                    {"text": "i tried this and it helped"},
                ]
            }
        )
    )

    assert profile["source"] == "context_fallback"
    assert int(profile["sample_size"]) >= 2


def test_lane_targets_enable_contrarian_when_intent_requests_debate():
    targets = campaign_ai._lane_targets(
        10,
        "support narrative but add rage bait and contrarian hot take comments",
    )

    assert targets["contrarian"] >= 2
    assert sum(targets.values()) == 10


def test_generation_prompt_is_top_level_isolated():
    prompt = campaign_ai._build_generation_prompt(
        context_snapshot={
            "op_post": {"text": "I keep dealing with this issue every week"},
            "supporting_comments": [
                {"text": "hidden supporting detail should not leak"},
                {"text": "another supporting detail"},
            ],
        },
        intent="support the OP and suggest one alternative",
        comment_count=5,
        existing_comments=[],
        rules_snapshot={"negative_patterns": [], "vocabulary_guidance": []},
        remaining_attempt=1,
        style_profile=campaign_ai._default_style_profile(),
        mix_targets={"short": 1, "medium": 3, "long": 1},
        mix_missing={"short": 1, "medium": 3, "long": 1},
        surface_missing={
            "endings": {"none": 2, "period": 1, "question": 1, "exclaim": 1},
            "reaction": 1,
            "testimonial": 1,
            "question": 1,
            "alternative": 1,
            "mention": 0,
            "lowercase_start": 0,
            "uppercase_start_min": 1,
        },
        lane_targets={"supportive": 2, "testimonial": 2, "alternative": 1, "contrarian": 0},
        lane_missing={"supportive": 2, "testimonial": 2, "alternative": 1, "contrarian": 0},
        brand_plan={"brand": "Nuora", "recommendation_target": 2, "justification_target": 1},
        brand_missing={"recommendation": 2, "justification": 1},
    )

    assert "Every output is an isolated TOP-LEVEL comment on the OP post" in prompt
    assert "Never write as a reply to another comment" in prompt
    assert "hidden supporting detail should not leak" not in prompt
    assert "Do not make everything lowercase" in prompt
    assert "brand in play: Nuora" in prompt


def test_detect_primary_brand_from_supporting_comments():
    brand = campaign_ai._detect_primary_brand(
        {
            "op_post": {"text": "need help"},
            "supporting_comments": [
                {"text": "brand we recommend is myNuora"},
            ],
        },
        "support narrative",
    )

    assert brand == "Nuora"


def test_brand_targets_for_10_comments():
    plan = campaign_ai._brand_targets(10, brand="Nuora")
    assert plan["brand"] == "Nuora"
    assert plan["recommendation_target"] >= 3
    assert plan["justification_target"] >= 1


def test_apply_surface_variability_enforces_normal_case_min():
    comments = [
        "omg same",
        "this is wild",
        "i had this too",
        "totally get this",
        "been there",
        "same here",
        "makes sense",
        "i get it",
        "yes exactly",
        "i feel this",
    ]
    adjusted = campaign_ai._apply_surface_variability(
        comments,
        target_surface={
            "endings": {"none": 10, "period": 0, "question": 0, "exclaim": 0},
            "lowercase_start": 2,
            "uppercase_start_min": 2,
        },
    )

    lower = 0
    for text in adjusted:
        first = campaign_ai._first_alpha_char(text)
        if first and first.islower():
            lower += 1
    uppercase = len(adjusted) - lower
    assert uppercase >= 2


def _fake_rules():
    return {
        "version": "rules_v1",
        "negative_patterns": [],
        "vocabulary_guidance": [],
    }


async def _fake_generate(*, context_snapshot, intent, comment_count, rules_snapshot, existing_comments=None):
    assert isinstance(context_snapshot, dict)
    assert isinstance(intent, str)
    assert isinstance(rules_snapshot, dict)
    assert existing_comments is None or isinstance(existing_comments, list)
    return [f"generated comment {i + 1}" for i in range(comment_count)]


def test_campaign_ai_generate_creates_draft_with_metadata(monkeypatch):
    monkeypatch.setattr(main, "fetch_campaign_context", _fake_context)
    monkeypatch.setattr(main, "load_campaign_rules_snapshot", _fake_rules)
    monkeypatch.setattr(main, "generate_campaign_comments", _fake_generate)

    request = CampaignAIGenerateRequest(
        url=VALID_URL,
        intent="support this narrative with social proof",
        comment_count=10,
        duration_minutes=30,
        filter_tags=["team-a"],
        enable_warmup=True,
    )

    result = asyncio.run(main.campaign_ai_generate(request, current_user={"username": "tester"}))

    assert len(result["comments"]) == 10
    draft = main.draft_manager.get_draft(result["draft_id"])
    assert draft is not None
    assert draft["comments"][0] == "generated comment 1"
    assert draft["ai_metadata"]["intent"] == request.intent
    assert draft["ai_metadata"]["rules_snapshot_version"] == "rules_v1"
    assert draft["ai_metadata"]["regenerate_count"] == 0


def test_campaign_ai_generate_updates_existing_draft(monkeypatch):
    monkeypatch.setattr(main, "fetch_campaign_context", _fake_context)
    monkeypatch.setattr(main, "load_campaign_rules_snapshot", _fake_rules)
    monkeypatch.setattr(main, "generate_campaign_comments", _fake_generate)

    existing = main.draft_manager.create_draft(
        url=VALID_URL,
        comments=["old"],
        jobs=None,
        duration_minutes=20,
        filter_tags=None,
        enable_warmup=True,
        username="tester",
        ai_metadata={
            "intent": "old intent",
            "model": "claude-sonnet-4-6",
            "context_snapshot": {"context_id": "old"},
            "generated_at": "2026-01-01T00:00:00",
            "regenerate_count": 7,
            "rules_snapshot_version": "old",
        },
    )

    request = CampaignAIGenerateRequest(
        url=VALID_URL,
        intent="new intent",
        comment_count=10,
        duration_minutes=45,
        filter_tags=["team-b"],
        enable_warmup=True,
        draft_id=existing["id"],
    )

    result = asyncio.run(main.campaign_ai_generate(request, current_user={"username": "tester"}))

    assert result["draft_id"] == existing["id"]
    updated = main.draft_manager.get_draft(existing["id"])
    assert updated is not None
    assert updated["duration_minutes"] == 45
    assert updated["filter_tags"] == ["team-b"]
    assert updated["ai_metadata"]["intent"] == "new intent"
    assert updated["ai_metadata"]["regenerate_count"] == 7


def test_campaign_ai_generate_rejects_invalid_comment_count():
    request = CampaignAIGenerateRequest(
        url=VALID_URL,
        intent="test",
        comment_count=9,
        duration_minutes=30,
        filter_tags=None,
        enable_warmup=True,
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.campaign_ai_generate(request, current_user={"username": "tester"}))

    assert exc.value.status_code == 400
    assert "comment_count must be between" in str(exc.value.detail)


def test_campaign_ai_regenerate_one_updates_only_target_index(monkeypatch):
    captured = {}

    async def fake_generate_one(*, context_snapshot, intent, comment_count, rules_snapshot, existing_comments=None):
        captured["existing_comments"] = list(existing_comments or [])
        assert comment_count == 1
        return ["replacement comment"]

    monkeypatch.setattr(main, "load_campaign_rules_snapshot", _fake_rules)
    monkeypatch.setattr(main, "generate_campaign_comments", fake_generate_one)

    draft = main.draft_manager.create_draft(
        url=VALID_URL,
        comments=["first", "second", "third"],
        jobs=None,
        duration_minutes=30,
        filter_tags=None,
        enable_warmup=True,
        username="tester",
        ai_metadata={
            "intent": "align to mission",
            "model": "claude-sonnet-4-6",
            "context_snapshot": {"context_id": "ctx_1", "op_post": {"text": "hello"}},
            "generated_at": "2026-01-01T00:00:00",
            "regenerate_count": 0,
            "rules_snapshot_version": "rules_v1",
        },
    )

    result = asyncio.run(
        main.campaign_ai_regenerate_one(
            draft["id"],
            CampaignAIRegenerateOneRequest(index=1),
            current_user={"username": "tester"},
        )
    )

    assert result["comments"] == ["first", "replacement comment", "third"]
    assert captured["existing_comments"] == ["first", "third"]
    refreshed = main.draft_manager.get_draft(draft["id"])
    assert refreshed is not None
    assert refreshed["ai_metadata"]["regenerate_count"] == 1


def test_campaign_ai_regenerate_all_replaces_full_list(monkeypatch):
    async def fake_generate_all(*, context_snapshot, intent, comment_count, rules_snapshot, existing_comments=None):
        assert comment_count == 3
        return ["new 1", "new 2", "new 3"]

    monkeypatch.setattr(main, "load_campaign_rules_snapshot", _fake_rules)
    monkeypatch.setattr(main, "generate_campaign_comments", fake_generate_all)

    draft = main.draft_manager.create_draft(
        url=VALID_URL,
        comments=["old 1", "old 2", "old 3"],
        jobs=None,
        duration_minutes=30,
        filter_tags=None,
        enable_warmup=True,
        username="tester",
        ai_metadata={
            "intent": "align to mission",
            "model": "claude-sonnet-4-6",
            "context_snapshot": {"context_id": "ctx_1", "op_post": {"text": "hello"}},
            "generated_at": "2026-01-01T00:00:00",
            "regenerate_count": 2,
            "rules_snapshot_version": "rules_v1",
        },
    )

    result = asyncio.run(main.campaign_ai_regenerate_all(draft["id"], current_user={"username": "tester"}))

    assert result["comments"] == ["new 1", "new 2", "new 3"]
    refreshed = main.draft_manager.get_draft(draft["id"])
    assert refreshed is not None
    assert refreshed["ai_metadata"]["regenerate_count"] == 3
