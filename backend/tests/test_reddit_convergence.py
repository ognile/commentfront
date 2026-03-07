import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from credentials import CredentialManager
import reddit_convergence


def _line(username: str) -> str:
    return (
        f"{username}:pass123:{username.lower()}@example.com:mailpass:"
        f"ABCD EFGH IJKL MNOP:https://www.reddit.com/user/{username}/"
    )


def test_execute_reddit_unlinked_convergence_uses_existing_linked_session(tmp_path, monkeypatch):
    reports_dir = tmp_path / "convergence"
    reports_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(reddit_convergence, "REDDIT_CONVERGENCE_REPORTS_DIR", reports_dir)

    manager = CredentialManager(file_path=str(tmp_path / "credentials.json"))
    manager.import_reddit_account_line(_line("Neera_Allvere"), fixture=True)
    manager.set_linked_session_id("reddit::Neera_Allvere", "reddit_neera_allvere", platform="reddit")

    class _FakeLearning:
        def __init__(self):
            self._summary = {"policy_version": 7}

        def sync_linked_sessions(self):
            return None

        def summary(self):
            return self._summary

        def set_last_convergence_run(self, run_id):
            self._summary["last_run"] = run_id

        def record_attempt(self, **kwargs):
            return None

        def recommended_strategies(self, username):
            return []

    async def fake_verify(profile_name, profile_url, proxy_url):
        return {
            "linked": True,
            "test_result": {"success": True},
            "action_result": {"success": True},
            "session": object(),
        }

    monkeypatch.setattr(reddit_convergence, "_verify_linked_session", fake_verify)

    report = asyncio.run(
        reddit_convergence.execute_reddit_unlinked_convergence(
            run_id="run_existing",
            usernames=["Neera_Allvere"],
            proxy_url="http://proxy.example:8080",
            proxy_source="env",
            credential_manager=manager,
            learning_store=_FakeLearning(),
        )
    )

    assert report["status"] == "completed"
    assert report["summary"]["linked_count"] == 1
    assert report["results"][0]["strategy_id"] == "existing_linked_session"


def test_execute_reddit_unlinked_convergence_retries_strategies_until_success(tmp_path, monkeypatch):
    reports_dir = tmp_path / "convergence"
    reports_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(reddit_convergence, "REDDIT_CONVERGENCE_REPORTS_DIR", reports_dir)

    manager = CredentialManager(file_path=str(tmp_path / "credentials.json"))
    manager.import_reddit_account_line(_line("Connor_Esla"), fixture=True)
    calls = []

    class _FakeLearning:
        def __init__(self):
            self._summary = {"policy_version": 3}
            self.recorded = []

        def sync_linked_sessions(self):
            return None

        def summary(self):
            return self._summary

        def set_last_convergence_run(self, run_id):
            self._summary["last_run"] = run_id

        def record_attempt(self, **kwargs):
            self.recorded.append(kwargs)

        def recommended_strategies(self, username):
            return [
                {"strategy_id": "settle_home"},
                {"strategy_id": "acquire_form_reload"},
            ]

    verify_calls = {"count": 0}

    async def fake_verify(profile_name, profile_url, proxy_url):
        verify_calls["count"] += 1
        calls.append(("verify", profile_name))
        if verify_calls["count"] == 1:
            return {
                "linked": False,
                "test_result": {"success": False},
                "action_result": {"success": False},
            }
        return {
            "linked": True,
            "test_result": {"success": True},
            "action_result": {"success": True},
            "session": _FakeSession(profile_name),
        }

    class _FakeSession:
        def __init__(self, profile_name):
            self.profile_name = profile_name

        def load(self):
            return {"profile_name": self.profile_name}

        def get_cookies(self):
            return [{"name": "reddit_session"}, {"name": "token_v2"}]

    async def fake_create(*, credential_uid, proxy_url, proxy_source, broadcast_callback=None, strategy_id=None, allow_reference_bootstrap=False):
        calls.append(("create", credential_uid, strategy_id, allow_reference_bootstrap))
        if strategy_id == "settle_home":
            return {
                "success": False,
                "profile_name": "reddit_connor_esla",
                "attempt_id": "attempt-1",
                "audit_json_url": "/audit/connor-1.json",
                "failure_bucket": "user_interaction_failed",
                "error": "Reddit OTP submit rejected: 401 user-interaction-failed",
            }
        return {
            "success": True,
            "profile_name": "reddit_connor_esla",
            "attempt_id": "attempt-2",
            "audit_json_url": "/audit/connor-2.json",
        }

    monkeypatch.setattr(reddit_convergence, "_verify_linked_session", fake_verify)
    monkeypatch.setattr(reddit_convergence, "create_reddit_session_from_credentials", fake_create)

    learning = _FakeLearning()
    report = asyncio.run(
        reddit_convergence.execute_reddit_unlinked_convergence(
            run_id="run_retry",
            usernames=["Connor_Esla"],
            proxy_url="http://proxy.example:8080",
            proxy_source="env",
            credential_manager=manager,
            learning_store=learning,
        )
    )

    assert report["status"] == "completed"
    assert report["summary"]["linked_count"] == 1
    assert report["results"][0]["strategy_id"] == "acquire_form_reload"
    assert calls[:3] == [
        ("verify", "reddit_connor_esla"),
        ("create", "reddit::Connor_Esla", "settle_home", False),
        ("create", "reddit::Connor_Esla", "acquire_form_reload", False),
    ]
    assert learning.recorded[0]["linked"] is False
    assert learning.recorded[-1]["linked"] is True
