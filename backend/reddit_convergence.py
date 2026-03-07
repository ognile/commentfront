"""
Targeted convergence runner for unlinked Reddit accounts.
"""

import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from credentials import CredentialManager
from reddit_bot import run_reddit_action
from reddit_login_bot import create_session_from_credentials as create_reddit_session_from_credentials
from reddit_login_learning import RedditLoginLearningStore
from reddit_session import RedditSession
from safe_io import atomic_write_json, safe_read_json

logger = logging.getLogger("RedditConvergence")

BroadcastFn = Optional[Callable[[str, dict], Awaitable[None]]]

DEFAULT_UNLINKED_ORDER = [
    "Cloudia_Merra",
    "Kaylee_Andreas",
    "Jenee_Waters",
    "Connor_Esla",
]


def _default_reddit_convergence_reports_dir() -> Path:
    env_value = os.getenv("REDDIT_CONVERGENCE_REPORTS_DIR")
    if env_value:
        return Path(env_value)
    if Path("/data").exists():
        return Path("/data/reddit_convergence")
    return Path("/tmp/commentbot_reddit_convergence")


REDDIT_CONVERGENCE_REPORTS_DIR = _default_reddit_convergence_reports_dir()
REDDIT_CONVERGENCE_REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _utc_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _report_path(run_id: str) -> Path:
    safe_run_id = "".join(ch for ch in str(run_id or "") if ch.isalnum() or ch in {"-", "_"})
    return REDDIT_CONVERGENCE_REPORTS_DIR / f"{safe_run_id}.json"


def load_reddit_convergence_report(run_id: str) -> Optional[Dict[str, Any]]:
    return safe_read_json(str(_report_path(run_id)))


def _save_report(report: Dict[str, Any]) -> bool:
    report["updated_at"] = _utc_now()
    return atomic_write_json(str(_report_path(str(report["run_id"]))), report)


async def _broadcast(callback: BroadcastFn, update_type: str, payload: Dict[str, Any]) -> None:
    if callback:
        await callback(update_type, payload)


def _make_initial_report(*, run_id: Optional[str], usernames: List[str]) -> Dict[str, Any]:
    resolved_run_id = run_id or f"{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    report = {
        "run_id": resolved_run_id,
        "status": "running",
        "created_at": _utc_now(),
        "target_usernames": usernames,
        "results": [],
        "summary": {
            "target_count": len(usernames),
            "linked_count": 0,
            "blocked_count": 0,
        },
    }
    _save_report(report)
    return report


def _summarize_results(results: List[Dict[str, Any]], *, target_count: int) -> Dict[str, int]:
    linked_count = sum(1 for item in results if item.get("linked"))
    blocked_count = sum(1 for item in results if item.get("status") == "blocked")
    return {
        "target_count": target_count,
        "linked_count": linked_count,
        "blocked_count": blocked_count,
    }


def _normalize_usernames(usernames: Optional[List[str]]) -> List[str]:
    items = [str(item or "").strip() for item in list(usernames or []) if str(item or "").strip()]
    return items or list(DEFAULT_UNLINKED_ORDER)


async def _verify_linked_session(profile_name: str, profile_url: str, proxy_url: Optional[str]) -> Dict[str, Any]:
    session = RedditSession(profile_name)
    if not session.load():
        return {
            "linked": False,
            "test_result": {"success": False, "error": "session not found"},
            "action_result": {"success": False, "error": "session not found"},
        }

    from reddit_login_bot import test_session as test_reddit_session

    test_result = await test_reddit_session(session, proxy_url)
    action_result = (
        await run_reddit_action(
            session,
            action="open_target",
            url=profile_url,
            proxy_url=proxy_url,
        )
        if test_result.get("success")
        else {"success": False, "error": "session test failed"}
    )
    linked = bool(test_result.get("success")) and bool(action_result.get("success"))
    return {
        "linked": linked,
        "test_result": test_result,
        "action_result": action_result,
        "session": session,
    }


async def execute_reddit_unlinked_convergence(
    *,
    run_id: Optional[str] = None,
    usernames: Optional[List[str]] = None,
    proxy_url: Optional[str],
    proxy_source: str,
    credential_manager: Optional[CredentialManager] = None,
    learning_store: Optional[RedditLoginLearningStore] = None,
    broadcast_callback: BroadcastFn = None,
) -> Dict[str, Any]:
    ordered_usernames = _normalize_usernames(usernames)
    manager = credential_manager or CredentialManager()
    learning = learning_store or RedditLoginLearningStore()
    learning.sync_linked_sessions()
    report = _make_initial_report(run_id=run_id, usernames=ordered_usernames)
    learning.set_last_convergence_run(str(report["run_id"]))

    await _broadcast(
        broadcast_callback,
        "reddit_convergence_started",
        {"run_id": report["run_id"], "target_usernames": ordered_usernames},
    )

    try:
        for index, username in enumerate(ordered_usernames, start=1):
            credential = manager.get_credential(username, platform="reddit")
            profile_name = f"reddit_{username.lower()}"
            profile_url = f"https://www.reddit.com/user/{username}/"
            account_result: Dict[str, Any] = {
                "index": index,
                "username": username,
                "profile_name": profile_name,
                "profile_url": profile_url,
                "linked": False,
                "status": "running",
                "strategy_attempts": [],
                "attempt_id": None,
                "audit_json_url": None,
                "failure_bucket": None,
                "test_success": False,
                "action_success": False,
                "error": None,
                "policy_version": learning.summary().get("policy_version"),
                "strategy_id": None,
            }

            await _broadcast(
                broadcast_callback,
                "reddit_convergence_account_start",
                {"run_id": report["run_id"], "index": index, "username": username},
            )

            if not credential:
                account_result["status"] = "blocked"
                account_result["error"] = f"Reddit credential not found: {username}"
                report["results"].append(account_result)
                report["summary"] = _summarize_results(report["results"], target_count=len(ordered_usernames))
                _save_report(report)
                continue

            linked_session_id = credential.get("linked_session_id") or profile_name
            existing = await _verify_linked_session(linked_session_id, profile_url, proxy_url)
            if existing["linked"]:
                account_result.update(
                    {
                        "linked": True,
                        "status": "success",
                        "profile_name": linked_session_id,
                        "test_success": True,
                        "action_success": True,
                        "strategy_id": "existing_linked_session",
                    }
                )
                learning.record_attempt(
                    username=username,
                    strategy_id="existing_linked_session",
                    result={"success": True, "profile_name": linked_session_id},
                    linked=True,
                    test_success=True,
                    action_success=True,
                    session=existing["session"],
                )
                report["results"].append(account_result)
                report["summary"] = _summarize_results(report["results"], target_count=len(ordered_usernames))
                _save_report(report)
                continue

            for strategy in learning.recommended_strategies(username):
                strategy_id = str(strategy.get("strategy_id"))
                create_result = await create_reddit_session_from_credentials(
                    credential_uid=str(credential.get("credential_id")),
                    proxy_url=proxy_url,
                    proxy_source=proxy_source,
                    broadcast_callback=broadcast_callback,
                    strategy_id=strategy_id,
                    allow_reference_bootstrap=False,
                )
                create_result["strategy_id"] = strategy_id
                account_result["strategy_attempts"].append(
                    {
                        "strategy_id": strategy_id,
                        "attempt_id": create_result.get("attempt_id"),
                        "audit_json_url": create_result.get("audit_json_url"),
                        "failure_bucket": create_result.get("failure_bucket"),
                        "error": create_result.get("error"),
                        "success": bool(create_result.get("success")),
                    }
                )

                if not create_result.get("success"):
                    learning.record_attempt(
                        username=username,
                        strategy_id=strategy_id,
                        result=create_result,
                        linked=False,
                    )
                    account_result.update(
                        {
                            "strategy_id": strategy_id,
                            "attempt_id": create_result.get("attempt_id"),
                            "audit_json_url": create_result.get("audit_json_url"),
                            "failure_bucket": create_result.get("failure_bucket"),
                            "error": create_result.get("error"),
                        }
                    )
                    continue

                linked_profile_name = create_result.get("profile_name") or profile_name
                verification = await _verify_linked_session(linked_profile_name, profile_url, proxy_url)
                session = verification.get("session")
                if verification["linked"]:
                    account_result.update(
                        {
                            "linked": True,
                            "status": "success",
                            "profile_name": linked_profile_name,
                            "strategy_id": strategy_id,
                            "attempt_id": create_result.get("attempt_id"),
                            "audit_json_url": create_result.get("audit_json_url"),
                            "failure_bucket": None,
                            "error": None,
                            "test_success": True,
                            "action_success": True,
                        }
                    )
                    learning.record_attempt(
                        username=username,
                        strategy_id=strategy_id,
                        result=create_result,
                        linked=True,
                        test_success=True,
                        action_success=True,
                        session=session,
                    )
                    break

                session = RedditSession(linked_profile_name)
                if session.load():
                    session.delete()
                manager.set_linked_session_id(credential.get("credential_id"), None, platform="reddit")
                create_result["success"] = False
                create_result["failure_bucket"] = "post_create_verification_failed"
                create_result["error"] = verification["action_result"].get("error") or verification["test_result"].get("error")
                learning.record_attempt(
                    username=username,
                    strategy_id=strategy_id,
                    result=create_result,
                    linked=False,
                    test_success=bool(verification["test_result"].get("success")),
                    action_success=bool(verification["action_result"].get("success")),
                )
                account_result.update(
                    {
                        "strategy_id": strategy_id,
                        "attempt_id": create_result.get("attempt_id"),
                        "audit_json_url": create_result.get("audit_json_url"),
                        "failure_bucket": create_result.get("failure_bucket"),
                        "error": create_result.get("error"),
                    }
                )

            if not account_result.get("linked"):
                account_result["status"] = "blocked"

            report["results"].append(account_result)
            report["summary"] = _summarize_results(report["results"], target_count=len(ordered_usernames))
            report["policy_version"] = learning.summary().get("policy_version")
            _save_report(report)
            await _broadcast(
                broadcast_callback,
                "reddit_convergence_account_complete",
                {
                    "run_id": report["run_id"],
                    "username": username,
                    "linked": account_result.get("linked"),
                    "strategy_id": account_result.get("strategy_id"),
                    "failure_bucket": account_result.get("failure_bucket"),
                },
            )

        report["status"] = "completed"
        report["completed_at"] = _utc_now()
        report["summary"] = _summarize_results(report["results"], target_count=len(ordered_usernames))
        report["policy_version"] = learning.summary().get("policy_version")
        _save_report(report)
        await _broadcast(
            broadcast_callback,
            "reddit_convergence_completed",
            {
                "run_id": report["run_id"],
                "summary": report["summary"],
                "policy_version": report.get("policy_version"),
            },
        )
        return report
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = str(exc)
        report["summary"] = _summarize_results(report["results"], target_count=len(ordered_usernames))
        _save_report(report)
        raise
