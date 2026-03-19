import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from credentials import CredentialManager
import reddit_rollout


def _line(username: str) -> str:
    return (
        f"{username}:pass123:{username.lower()}@example.com:mailpass:"
        f"ABCD EFGH IJKL MNOP:https://www.reddit.com/user/{username}/"
    )


def _line_4(username: str) -> str:
    return f"{username}:pass123:{username.lower()}@example.com:mailpass"


def test_execute_reddit_bulk_session_rollout_success_persists_report_and_preserves_order(tmp_path, monkeypatch):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(reddit_rollout, "REDDIT_ROLLOUT_REPORTS_DIR", reports_dir)

    manager = CredentialManager(file_path=str(tmp_path / "credentials.json"))
    calls = []

    async def fake_create(*, credential_uid, proxy_url, proxy_source, broadcast_callback=None):
        calls.append(("create", credential_uid))
        username = credential_uid.split("::", 1)[1]
        return {
            "success": True,
            "profile_name": f"reddit_{username.lower()}",
            "attempt_id": f"attempt-{username}",
            "audit_json_url": f"/audit/{username}.json",
        }

    async def fake_test(session, proxy_url=None):
        calls.append(("test", session.profile_name))
        return {"success": True, "error": None}

    async def fake_action(session, **kwargs):
        calls.append(("action", session.profile_name, kwargs.get("url")))
        return {"success": True, "error": None, "current_url": kwargs.get("url")}

    monkeypatch.setattr(reddit_rollout, "create_reddit_session_from_credentials", fake_create)
    monkeypatch.setattr(reddit_rollout, "test_reddit_session", fake_test)
    monkeypatch.setattr(reddit_rollout, "run_reddit_action", fake_action)

    report = asyncio.run(
        reddit_rollout.execute_reddit_bulk_session_rollout(
            run_id="run_success",
            lines=[_line("Amy_Schaefera"), _line("Victor_Saunders")],
            proxy_url="http://proxy.example:8080",
            proxy_source="env",
            credential_manager=manager,
        )
    )

    assert report["status"] == "completed"
    assert report["summary"]["active_sessions_count"] == 2
    assert [item["username"] for item in report["results"]] == ["Amy_Schaefera", "Victor_Saunders"]
    assert calls == [
        ("create", "reddit::Amy_Schaefera"),
        ("test", "reddit_amy_schaefera"),
        ("action", "reddit_amy_schaefera", "https://www.reddit.com/user/Amy_Schaefera/"),
        ("create", "reddit::Victor_Saunders"),
        ("test", "reddit_victor_saunders"),
        ("action", "reddit_victor_saunders", "https://www.reddit.com/user/Victor_Saunders/"),
    ]

    persisted = reddit_rollout.load_reddit_rollout_report("run_success")
    assert persisted is not None
    assert persisted["summary"]["active_sessions_count"] == 2


def test_execute_reddit_bulk_session_rollout_supports_4_field_lines(tmp_path, monkeypatch):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(reddit_rollout, "REDDIT_ROLLOUT_REPORTS_DIR", reports_dir)

    manager = CredentialManager(file_path=str(tmp_path / "credentials.json"))

    async def fake_create(*, credential_uid, proxy_url, proxy_source, broadcast_callback=None):
        username = credential_uid.split("::", 1)[1]
        return {
            "success": True,
            "profile_name": f"reddit_{username.lower()}",
            "attempt_id": f"attempt-{username}",
            "audit_json_url": f"/audit/{username}.json",
        }

    async def fake_test(session, proxy_url=None):
        return {"success": True, "error": None}

    async def fake_action(session, **kwargs):
        return {"success": True, "error": None, "current_url": kwargs.get("url")}

    monkeypatch.setattr(reddit_rollout, "create_reddit_session_from_credentials", fake_create)
    monkeypatch.setattr(reddit_rollout, "test_reddit_session", fake_test)
    monkeypatch.setattr(reddit_rollout, "run_reddit_action", fake_action)

    report = asyncio.run(
        reddit_rollout.execute_reddit_bulk_session_rollout(
            run_id="run_4_field",
            lines=[_line_4("Amy_Schaefera")],
            proxy_url="http://proxy.example:8080",
            proxy_source="env",
            source_label="/tmp/business-order-transaction-details.txt",
            fixture=False,
            credential_manager=manager,
        )
    )

    assert report["summary"]["active_sessions_count"] == 1
    credential = manager.get_credential("reddit::Amy_Schaefera", platform="reddit")
    assert credential is not None
    assert credential["profile_url"] == "https://www.reddit.com/user/Amy_Schaefera/"
    assert credential["tags"] == ["reddit", "source_business_order_transaction_details"]


def test_execute_reddit_bulk_session_rollout_records_blocker_evidence(tmp_path, monkeypatch):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(reddit_rollout, "REDDIT_ROLLOUT_REPORTS_DIR", reports_dir)

    manager = CredentialManager(file_path=str(tmp_path / "credentials.json"))

    async def fake_create(*, credential_uid, proxy_url, proxy_source, broadcast_callback=None):
        return {
            "success": False,
            "profile_name": "reddit_mary_miaby",
            "attempt_id": "attempt-mary",
            "audit_json_url": "/audit/mary.json",
            "failure_bucket": "protected_routes_fail",
            "error": "Reddit session failed authenticated destination verification on www.reddit.com",
            "bootstrap_errors": [
                {
                    "reference_session_id": "adele_compton",
                    "error": "protected route bounced to login",
                    "failure_bucket": "protected_routes_fail",
                }
            ],
        }

    async def unexpected(*args, **kwargs):
        raise AssertionError("test/action should not run when create fails")

    monkeypatch.setattr(reddit_rollout, "create_reddit_session_from_credentials", fake_create)
    monkeypatch.setattr(reddit_rollout, "test_reddit_session", unexpected)
    monkeypatch.setattr(reddit_rollout, "run_reddit_action", unexpected)

    report = asyncio.run(
        reddit_rollout.execute_reddit_bulk_session_rollout(
            run_id="run_blocked",
            lines=[_line("Mary_Miaby")],
            proxy_url="http://proxy.example:8080",
            proxy_source="env",
            credential_manager=manager,
        )
    )

    assert report["status"] == "completed"
    assert report["summary"]["blocked_accounts_count"] == 1
    assert report["results"][0]["status"] == "blocked"
    assert report["results"][0]["attempt_id"] == "attempt-mary"
    assert report["results"][0]["audit_json_url"] == "/audit/mary.json"
    assert report["results"][0]["bootstrap_errors"][0]["reference_session_id"] == "adele_compton"


def test_execute_reddit_bulk_session_rollout_continues_when_create_raises(tmp_path, monkeypatch):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(reddit_rollout, "REDDIT_ROLLOUT_REPORTS_DIR", reports_dir)

    manager = CredentialManager(file_path=str(tmp_path / "credentials.json"))

    async def raising_create(*, credential_uid, proxy_url, proxy_source, broadcast_callback=None):
        raise RuntimeError("No valid Facebook sessions available for Reddit reference audit")

    async def fake_test(session, proxy_url=None):
        return {"success": True, "error": None}

    async def fake_action(session, **kwargs):
        return {"success": True, "error": None, "current_url": kwargs.get("url")}

    monkeypatch.setattr(reddit_rollout, "create_reddit_session_from_credentials", raising_create)
    monkeypatch.setattr(reddit_rollout, "test_reddit_session", fake_test)
    monkeypatch.setattr(reddit_rollout, "run_reddit_action", fake_action)

    report = asyncio.run(
        reddit_rollout.execute_reddit_bulk_session_rollout(
            run_id="run_exception",
            lines=[_line("Amy_Schaefera"), _line("Victor_Saunders")],
            proxy_url="http://proxy.example:8080",
            proxy_source="env",
            credential_manager=manager,
        )
    )

    assert report["status"] == "completed"
    assert report["summary"]["blocked_accounts_count"] == 2
    assert all(item["status"] == "blocked" for item in report["results"])
    assert all("No valid Facebook sessions available" in str(item["error"]) for item in report["results"])


def test_execute_reddit_bulk_session_rollout_is_idempotent_for_existing_credentials(tmp_path, monkeypatch):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(reddit_rollout, "REDDIT_ROLLOUT_REPORTS_DIR", reports_dir)

    manager = CredentialManager(file_path=str(tmp_path / "credentials.json"))

    async def fake_create(*, credential_uid, proxy_url, proxy_source, broadcast_callback=None):
        username = credential_uid.split("::", 1)[1]
        return {"success": True, "profile_name": f"reddit_{username.lower()}"}

    async def fake_test(session, proxy_url=None):
        return {"success": True, "error": None}

    async def fake_action(session, **kwargs):
        return {"success": True, "error": None, "current_url": kwargs.get("url")}

    monkeypatch.setattr(reddit_rollout, "create_reddit_session_from_credentials", fake_create)
    monkeypatch.setattr(reddit_rollout, "test_reddit_session", fake_test)
    monkeypatch.setattr(reddit_rollout, "run_reddit_action", fake_action)

    line = _line("Neera_Allvere")

    first = asyncio.run(
        reddit_rollout.execute_reddit_bulk_session_rollout(
            run_id="run_first",
            lines=[line],
            proxy_url="http://proxy.example:8080",
            proxy_source="env",
            credential_manager=manager,
        )
    )
    second = asyncio.run(
        reddit_rollout.execute_reddit_bulk_session_rollout(
            run_id="run_second",
            lines=[line],
            proxy_url="http://proxy.example:8080",
            proxy_source="env",
            credential_manager=manager,
        )
    )

    creds = manager.get_all_credentials(platform="reddit")
    assert len(creds) == 1
    assert first["results"][0]["credential_id"] == "reddit::Neera_Allvere"
    assert second["results"][0]["credential_id"] == "reddit::Neera_Allvere"
    assert second["summary"]["active_sessions_count"] == 1


def test_execute_reddit_bulk_session_rollout_reuses_existing_valid_session(tmp_path, monkeypatch):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(reddit_rollout, "REDDIT_ROLLOUT_REPORTS_DIR", reports_dir)

    manager = CredentialManager(file_path=str(tmp_path / "credentials.json"))
    manager.import_reddit_account_line(
        _line("Neera_Allvere"),
        fixture=False,
        tags=["reddit"],
        source_label="existing-session",
    )

    class _FakeSession:
        def __init__(self, profile_name):
            self.profile_name = profile_name

        def load(self):
            return {"profile_name": self.profile_name}

        def get_profile_url(self):
            return f"https://www.reddit.com/user/{self.profile_name.removeprefix('reddit_').title().replace('_', '_')}/"

    async def fake_test(session, proxy_url=None):
        return {"success": True, "error": None}

    async def fake_action(session, **kwargs):
        return {"success": True, "error": None, "current_url": kwargs.get("url")}

    async def unexpected_create(**kwargs):
        raise AssertionError("create should not run when existing session is valid")

    monkeypatch.setattr(reddit_rollout, "RedditSession", _FakeSession)
    monkeypatch.setattr(reddit_rollout, "test_reddit_session", fake_test)
    monkeypatch.setattr(reddit_rollout, "run_reddit_action", fake_action)
    monkeypatch.setattr(reddit_rollout, "create_reddit_session_from_credentials", unexpected_create)

    report = asyncio.run(
        reddit_rollout.execute_reddit_bulk_session_rollout(
            run_id="reuse_run",
            lines=[_line("Neera_Allvere")],
            proxy_url="http://proxy.example:8080",
            proxy_source="env",
            fixture=False,
            credential_manager=manager,
        )
    )

    result = report["results"][0]
    assert report["status"] == "completed"
    assert report["summary"]["active_sessions_count"] == 1
    assert result["status"] == "success"
    assert result["reused_existing_session"] is True
    assert result["create_attempts"] == []


def test_execute_reddit_bulk_session_rollout_retries_existing_session_on_empty_response(tmp_path, monkeypatch):
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(reddit_rollout, "REDDIT_ROLLOUT_REPORTS_DIR", reports_dir)

    manager = CredentialManager(file_path=str(tmp_path / "credentials.json"))
    manager.import_reddit_account_line(
        _line("Denyse_Cowans"),
        fixture=False,
        tags=["reddit"],
        source_label="existing-session-retry",
    )

    class _FakeSession:
        def __init__(self, profile_name):
            self.profile_name = profile_name

        def load(self):
            return {"profile_name": self.profile_name}

        def get_profile_url(self):
            return "https://www.reddit.com/user/Denyse_Cowans/"

    test_calls = {"count": 0}

    async def flaky_test(session, proxy_url=None):
        test_calls["count"] += 1
        if test_calls["count"] == 1:
            return {"success": False, "error": "Page.goto: net::ERR_EMPTY_RESPONSE at https://www.reddit.com/submit"}
        return {"success": True, "error": None}

    async def fake_action(session, **kwargs):
        return {"success": True, "error": None, "current_url": kwargs.get("url")}

    async def unexpected_create(**kwargs):
        raise AssertionError("create should not run when existing session succeeds after retry")

    monkeypatch.setattr(reddit_rollout, "RedditSession", _FakeSession)
    monkeypatch.setattr(reddit_rollout, "test_reddit_session", flaky_test)
    monkeypatch.setattr(reddit_rollout, "run_reddit_action", fake_action)
    monkeypatch.setattr(reddit_rollout, "create_reddit_session_from_credentials", unexpected_create)

    report = asyncio.run(
        reddit_rollout.execute_reddit_bulk_session_rollout(
            run_id="reuse_retry_run",
            lines=[_line("Denyse_Cowans")],
            proxy_url="http://proxy.example:8080",
            proxy_source="env",
            fixture=False,
            credential_manager=manager,
        )
    )

    result = report["results"][0]
    assert report["summary"]["active_sessions_count"] == 1
    assert result["status"] == "success"
    assert result["reused_existing_session"] is True
    assert test_calls["count"] == 2
