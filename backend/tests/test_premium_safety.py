import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from premium_safety import _profile_candidate_urls, _snapshot_score, _url_profile_hint


class _SessionStub:
    def __init__(self, user_id: str):
        self.data = {"user_id": user_id}


def test_profile_candidate_urls_prefers_direct_profile_first():
    session = _SessionStub("12345")
    urls = _profile_candidate_urls(session, "Wanda Lobb")
    assert urls[0] == "https://m.facebook.com/profile.php?id=12345&v=timeline"
    assert urls[1] == "https://m.facebook.com/profile.php?id=12345"
    assert "https://m.facebook.com/me/?v=timeline" in urls


def test_url_profile_hint_detects_profile_routes():
    assert _url_profile_hint("https://m.facebook.com/profile.php?id=42", "42") is True
    assert _url_profile_hint("https://m.facebook.com/me/", "42") is True
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
