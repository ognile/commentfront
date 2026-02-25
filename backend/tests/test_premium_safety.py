import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import premium_safety
from premium_safety import (
    _extract_post_segments_from_blob,
    _profile_candidate_urls,
    _snapshot_score,
    _url_profile_hint,
)


class _SessionStub:
    def __init__(self, user_id: str):
        self.data = {"user_id": user_id}


def test_profile_candidate_urls_prefers_me_timeline_first():
    session = _SessionStub("12345")
    urls = _profile_candidate_urls(session, "Wanda Lobb")
    assert urls[0] == "https://m.facebook.com/me/?v=timeline"
    assert urls[1] == "https://m.facebook.com/me/"
    assert urls[2] == "https://mbasic.facebook.com/me/?v=timeline"
    assert urls[3] == "https://mbasic.facebook.com/me/"
    assert "https://m.facebook.com/profile.php?id=12345&v=timeline" in urls
    assert "https://m.facebook.com/me/?v=timeline" in urls
    assert "https://mbasic.facebook.com/me/?v=timeline" in urls


def test_profile_candidate_urls_respects_max_candidate_limit(monkeypatch):
    monkeypatch.setattr(premium_safety, "PRECHECK_MAX_CANDIDATE_URLS", 3)
    session = _SessionStub("12345")
    urls = _profile_candidate_urls(session, "Wanda Lobb")
    assert len(urls) == 3
    assert urls == [
        "https://m.facebook.com/me/?v=timeline",
        "https://m.facebook.com/me/",
        "https://mbasic.facebook.com/me/?v=timeline",
    ]


def test_url_profile_hint_detects_profile_routes():
    assert _url_profile_hint("https://m.facebook.com/profile.php?id=42", "42") is True
    assert _url_profile_hint("https://m.facebook.com/me/", "42") is True
    assert _url_profile_hint("https://mbasic.facebook.com/me/", "42") is True
    assert _url_profile_hint("https://m.facebook.com/?v=timeline", "42") is True
    assert _url_profile_hint("https://m.facebook.com/home.php", "42") is False


def test_snapshot_score_prefers_strong_profile_context():
    expected = "Wanda Lobb"
    user_id = "42"
    strong = {
        "profile_name_seen": "Wanda Lobb",
        "body_text": "Wanda Lobb updated her status.",
        "posts": [{"text": "post 1"}, {"text": "post 2"}, {"text": "post 3"}],
        "profile_surface_detected": True,
        "go_to_profile_visible": False,
        "current_url": "https://m.facebook.com/profile.php?id=42&v=timeline",
    }
    weak = {
        "profile_name_seen": "Friends",
        "body_text": "general home feed content",
        "posts": [],
        "profile_surface_detected": False,
        "go_to_profile_visible": True,
        "current_url": "https://m.facebook.com/",
    }
    strong_score = _snapshot_score(strong, expected, user_id)
    weak_score = _snapshot_score(weak, expected, user_id)
    assert int(strong_score["score"]) > int(weak_score["score"])
    assert strong_score["strict_name_match"] is True
    assert weak_score["strict_name_match"] is False


def test_extract_post_segments_from_blob_splits_compound_timeline_text():
    blob = (
        "Wanda Lobb 26m small wins today and trying to stay consistent with my routine. "
        "Like Comment Share "
        "Wanda Lobb 32m kept things simple today and that honestly helped a lot. "
        "Like Comment Share "
        "Wanda Lobb 1h slow morning, warm coffee, and a better mood after a long week."
    )

    posts = _extract_post_segments_from_blob(blob, "Wanda Lobb", max_posts=5)
    assert len(posts) >= 3
    assert any("small wins today" in p["text"].lower() for p in posts)
    assert any("kept things simple" in p["text"].lower() for p in posts)


def test_extract_post_segments_from_blob_ignores_profile_header_noise():
    blob = (
        "Wanda Lobb 25 posts Add to story Edit profile All Photos Reels Personal details "
        "Post a status update Loading more..."
    )

    posts = _extract_post_segments_from_blob(blob, "Wanda Lobb", max_posts=5)
    assert posts == []
