import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import reddit_login_learning


def test_learning_store_records_attempts_and_recommends_strategy(tmp_path, monkeypatch):
    learning_path = tmp_path / "reddit_login_learning.json"
    monkeypatch.setattr(reddit_login_learning, "REDDIT_LOGIN_LEARNING_PATH", learning_path)

    store = reddit_login_learning.RedditLoginLearningStore(file_path=str(learning_path))
    store.record_attempt(
        username="Cloudia_Merra",
        strategy_id="baseline_humanized",
        result={
            "attempt_id": "attempt-1",
            "audit_json_url": "/audit/cloudia-1.json",
            "failure_bucket": "otp_never_shown",
            "error": "Page.goto: net::ERR_EMPTY_RESPONSE at https://www.reddit.com/login",
        },
        linked=False,
    )

    account = store.get_account("Cloudia_Merra")
    assert account["last_failure_bucket"] == "otp_never_shown"
    assert account["last_strategy_id"] == "baseline_humanized"

    strategies = store.recommended_strategies("Cloudia_Merra")
    assert [item["strategy_id"] for item in strategies][:2] == ["acquire_form_reload", "settle_home"]


def test_learning_store_syncs_existing_linked_sessions(tmp_path, monkeypatch):
    learning_path = tmp_path / "reddit_login_learning.json"
    monkeypatch.setattr(reddit_login_learning, "REDDIT_LOGIN_LEARNING_PATH", learning_path)
    monkeypatch.setattr(
        reddit_login_learning,
        "list_saved_reddit_sessions",
        lambda: [
            {
                "linked_credential_id": "reddit::Neera_Allvere",
                "username": "Neera_Allvere",
                "profile_name": "reddit_neera_allvere",
            }
        ],
    )

    class _FakeSession:
        def __init__(self, profile_name):
            self.profile_name = profile_name

        def load(self):
            return {"profile_name": self.profile_name}

        def get_cookies(self):
            return [{"name": "reddit_session"}, {"name": "token_v2"}]

    monkeypatch.setattr(reddit_login_learning, "RedditSession", _FakeSession)

    store = reddit_login_learning.RedditLoginLearningStore(file_path=str(learning_path))
    store.sync_linked_sessions()

    summary = store.summary()
    assert summary["linked_count"] == 1
    assert "Neera_Allvere" in summary["linked_accounts"]
    assert ["reddit_session", "token_v2"] in summary["winner_cookie_signatures"]
