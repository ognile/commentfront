import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
from main import (
    CampaignAIContextRequest,
    CampaignAIGenerateRequest,
    CampaignAIRegenerateOneRequest,
)


VALID_URL = "https://www.facebook.com/permalink.php?story_fbid=123456&id=987654321"


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

    yield

    main.queue_manager.file_path = old_queue_path
    main.draft_manager.file_path = old_draft_path
    main.queue_manager.campaigns = {}
    main.queue_manager.history = []
    main.draft_manager.drafts = {}


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
