"""
Reddit bulk session rollout orchestration and report persistence.
"""

import hashlib
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from credentials import CredentialManager
from reddit_bot import run_reddit_action
from reddit_login_bot import create_session_from_credentials as create_reddit_session_from_credentials
from reddit_login_bot import test_session as test_reddit_session
from reddit_session import RedditSession
from safe_io import atomic_write_json, safe_read_json

logger = logging.getLogger("RedditRollout")

BroadcastFn = Optional[Callable[[str, dict], Awaitable[None]]]


def _default_reddit_rollout_reports_dir() -> Path:
    env_value = os.getenv("REDDIT_ROLLOUT_REPORTS_DIR")
    if env_value:
        return Path(env_value)
    if Path("/data").exists():
        return Path("/data/reddit_rollouts")
    return Path("/tmp/commentbot_reddit_rollouts")


REDDIT_ROLLOUT_REPORTS_DIR = _default_reddit_rollout_reports_dir()
REDDIT_ROLLOUT_REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _utc_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _normalize_lines(lines: List[str]) -> List[str]:
    return [str(line or "").strip() for line in list(lines or []) if str(line or "").strip()]


def _parse_username(line: str) -> str:
    username = str(line or "").strip().split(":", 1)[0].strip()
    if not username:
        raise ValueError("Missing username in Reddit account line")
    return username


def _parse_profile_url(line: str) -> Optional[str]:
    parts = str(line or "").strip().split(":", 5)
    if len(parts) == 6:
        return parts[5].strip() or None
    return None


def _line_hash(lines: List[str]) -> str:
    joined = "\n".join(lines)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _report_path(run_id: str) -> Path:
    safe_run_id = "".join(ch for ch in str(run_id or "") if ch.isalnum() or ch in {"-", "_"})
    return REDDIT_ROLLOUT_REPORTS_DIR / f"{safe_run_id}.json"


def load_reddit_rollout_report(run_id: str) -> Optional[Dict[str, Any]]:
    return safe_read_json(str(_report_path(run_id)))


def _save_report(report: Dict[str, Any]) -> bool:
    report["updated_at"] = _utc_now()
    return atomic_write_json(str(_report_path(str(report["run_id"]))), report)


def _summarize_results(results: List[Dict[str, Any]]) -> Dict[str, int]:
    summary = {
        "total_accounts": len(results),
        "imported_accounts": 0,
        "create_success_count": 0,
        "test_success_count": 0,
        "action_success_count": 0,
        "active_sessions_count": 0,
        "blocked_accounts_count": 0,
    }
    for item in results:
        if item.get("imported"):
            summary["imported_accounts"] += 1
        if item.get("create_success"):
            summary["create_success_count"] += 1
        if item.get("test_success"):
            summary["test_success_count"] += 1
        if item.get("action_success"):
            summary["action_success_count"] += 1
        if item.get("active_session"):
            summary["active_sessions_count"] += 1
        if item.get("status") == "blocked":
            summary["blocked_accounts_count"] += 1
    return summary


def _make_initial_report(
    *,
    run_id: Optional[str],
    lines: List[str],
    fixture: bool,
    source_label: Optional[str],
    proxy_source: str,
    max_create_attempts: int,
) -> Dict[str, Any]:
    resolved_run_id = run_id or f"{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    usernames = [_parse_username(line) for line in lines]
    report = {
        "run_id": resolved_run_id,
        "status": "running",
        "created_at": _utc_now(),
        "source_label": source_label,
        "fixture": bool(fixture),
        "proxy_source": proxy_source,
        "max_create_attempts": int(max_create_attempts),
        "line_count": len(lines),
        "line_hash": _line_hash(lines),
        "target_usernames": usernames,
        "results": [],
        "summary": {
            "total_accounts": len(lines),
            "imported_accounts": 0,
            "create_success_count": 0,
            "test_success_count": 0,
            "action_success_count": 0,
            "active_sessions_count": 0,
            "blocked_accounts_count": 0,
        },
    }
    _save_report(report)
    return report


async def _broadcast(callback: BroadcastFn, update_type: str, payload: Dict[str, Any]) -> None:
    if callback:
        await callback(update_type, payload)


def _should_retry_create(result: Dict[str, Any], *, attempt_number: int, max_attempts: int) -> bool:
    if attempt_number >= max_attempts:
        return False

    failure_bucket = str(result.get("failure_bucket") or "").strip().lower()
    error = str(result.get("error") or "").lower()

    if "err_empty_response" in error or "timeout" in error:
        return True
    if failure_bucket in {"otp_never_shown", ""}:
        return True
    return False


def _extract_bootstrap_errors(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    extracted: List[Dict[str, Any]] = []
    for item in list(result.get("bootstrap_errors") or []):
        extracted.append(
            {
                "reference_session_id": item.get("reference_session_id"),
                "error": item.get("error"),
                "failure_bucket": item.get("failure_bucket"),
            }
        )
    return extracted


def _should_retry_existing_session(error: Optional[str]) -> bool:
    text = str(error or "").lower()
    return "err_empty_response" in text or "timeout" in text or "err_aborted" in text


async def _reuse_existing_reddit_session(
    *,
    profile_name: str,
    profile_url: Optional[str],
    proxy_url: str,
) -> Dict[str, Any]:
    session = RedditSession(str(profile_name))
    loaded = session.load()
    if not loaded:
        return {
            "success": False,
            "profile_name": profile_name,
            "error": "existing session not found",
            "reused_existing_session": False,
        }

    last_test_result = {"success": False, "error": None}
    last_action_result = {"success": False, "error": None, "current_url": None}

    for attempt in range(1, 4):
        try:
            test_result = await test_reddit_session(session, proxy_url)
        except Exception as exc:
            test_result = {"success": False, "error": str(exc)}
        last_test_result = test_result

        if not test_result.get("success"):
            if attempt < 3 and _should_retry_existing_session(test_result.get("error")):
                continue
            return {
                "success": False,
                "profile_name": profile_name,
                "error": test_result.get("error"),
                "reused_existing_session": True,
                "test_result": test_result,
                "action_result": last_action_result,
            }

        try:
            action_result = await run_reddit_action(
                session,
                action="open_target",
                proxy_url=proxy_url,
                url=profile_url or session.get_profile_url(),
            )
        except Exception as exc:
            action_result = {"success": False, "error": str(exc), "current_url": None}
        last_action_result = action_result

        success = bool(test_result.get("success")) and bool(action_result.get("success"))
        if success:
            return {
                "success": True,
                "profile_name": profile_name,
                "error": None,
                "reused_existing_session": True,
                "test_result": test_result,
                "action_result": action_result,
            }
        if attempt < 3 and _should_retry_existing_session(action_result.get("error")):
            continue
        return {
            "success": False,
            "profile_name": profile_name,
            "error": action_result.get("error") or test_result.get("error"),
            "reused_existing_session": True,
            "test_result": test_result,
            "action_result": action_result,
        }

    return {
        "success": False,
        "profile_name": profile_name,
        "error": last_action_result.get("error") or last_test_result.get("error"),
        "reused_existing_session": True,
        "test_result": last_test_result,
        "action_result": last_action_result,
    }


async def execute_reddit_bulk_session_rollout(
    *,
    run_id: Optional[str] = None,
    lines: List[str],
    proxy_url: str,
    proxy_source: str,
    fixture: bool = True,
    source_label: Optional[str] = None,
    max_create_attempts: int = 2,
    broadcast_callback: BroadcastFn = None,
    credential_manager: Optional[CredentialManager] = None,
) -> Dict[str, Any]:
    normalized_lines = _normalize_lines(lines)
    if not normalized_lines:
        raise ValueError("No Reddit account lines provided")
    if not proxy_url:
        raise ValueError("proxy_url is required")

    manager = credential_manager or CredentialManager()
    report = _make_initial_report(
        run_id=run_id,
        lines=normalized_lines,
        fixture=fixture,
        source_label=source_label,
        proxy_source=proxy_source,
        max_create_attempts=max_create_attempts,
    )
    run_id = str(report["run_id"])

    await _broadcast(
        broadcast_callback,
        "reddit_bulk_create_started",
        {
            "run_id": run_id,
            "line_count": len(normalized_lines),
            "source_label": source_label,
        },
    )

    try:
        for index, line in enumerate(normalized_lines, start=1):
            username = _parse_username(line)
            expected_profile_name = f"reddit_{username.lower()}"
            profile_url = _parse_profile_url(line)
            account_result: Dict[str, Any] = {
                "index": index,
                "username": username,
                "expected_profile_name": expected_profile_name,
                "profile_url": profile_url,
                "imported": False,
                "credential_id": None,
                "profile_name": expected_profile_name,
                "create_success": False,
                "test_success": False,
                "action_success": False,
                "active_session": False,
                "status": "running",
                "create_attempts": [],
                "attempt_id": None,
                "audit_json_url": None,
                "failure_bucket": None,
                "error": None,
                "bootstrap_errors": [],
            }

            await _broadcast(
                broadcast_callback,
                "reddit_bulk_create_account_start",
                {
                    "run_id": run_id,
                    "index": index,
                    "username": username,
                    "expected_profile_name": expected_profile_name,
                },
            )

            try:
                credential_id = manager.import_reddit_account_line(
                    line,
                    fixture=fixture,
                    tags=["reddit", "fixture"] if fixture else ["reddit"],
                    source_label=source_label,
                )
                credential = manager.get_credential(credential_id, platform="reddit")
                if not credential:
                    raise RuntimeError(f"Imported credential lookup failed for {credential_id}")

                account_result["imported"] = True
                account_result["credential_id"] = credential_id
                account_result["profile_name"] = credential.get("profile_name") or expected_profile_name
                account_result["profile_url"] = credential.get("profile_url") or profile_url
            except Exception as exc:
                account_result["status"] = "blocked"
                account_result["error"] = f"credential import failed: {exc}"
                report["results"].append(account_result)
                report["summary"] = _summarize_results(report["results"])
                _save_report(report)
                await _broadcast(
                    broadcast_callback,
                    "reddit_bulk_create_account_complete",
                    {
                        "run_id": run_id,
                        "username": username,
                        "status": account_result["status"],
                        "error": account_result["error"],
                    },
                )
                continue

            create_result: Dict[str, Any] = {}
            existing_profile_name = str(
                credential.get("linked_session_id")
                or account_result["profile_name"]
                or expected_profile_name
            )
            reused_existing_session = await _reuse_existing_reddit_session(
                profile_name=existing_profile_name,
                profile_url=account_result.get("profile_url"),
                proxy_url=proxy_url,
            )
            if reused_existing_session.get("success"):
                create_result = {
                    "success": True,
                    "profile_name": existing_profile_name,
                    "attempt_id": None,
                    "audit_json_url": None,
                    "failure_bucket": None,
                    "error": None,
                    "bootstrap_errors": [],
                    "reused_existing_session": True,
                    "test_result": reused_existing_session.get("test_result"),
                    "action_result": reused_existing_session.get("action_result"),
                }

            if not create_result.get("success"):
                for attempt_number in range(1, max(1, int(max_create_attempts)) + 1):
                    try:
                        create_result = await create_reddit_session_from_credentials(
                            credential_uid=str(account_result["credential_id"]),
                            proxy_url=proxy_url,
                            proxy_source=proxy_source,
                        )
                    except Exception as exc:
                        create_result = {
                            "success": False,
                            "profile_name": account_result["profile_name"],
                            "attempt_id": None,
                            "audit_json_url": None,
                            "failure_bucket": None,
                            "error": str(exc),
                            "bootstrap_errors": [],
                        }
                    attempt_record = {
                        "attempt_number": attempt_number,
                        "success": bool(create_result.get("success")),
                        "profile_name": create_result.get("profile_name"),
                        "attempt_id": create_result.get("attempt_id"),
                        "audit_json_url": create_result.get("audit_json_url"),
                        "failure_bucket": create_result.get("failure_bucket"),
                        "error": create_result.get("error"),
                        "bootstrap_errors": _extract_bootstrap_errors(create_result),
                    }
                    account_result["create_attempts"].append(attempt_record)
                    if create_result.get("success"):
                        break
                    if not _should_retry_create(create_result, attempt_number=attempt_number, max_attempts=max_create_attempts):
                        break

            account_result["create_success"] = bool(create_result.get("success"))
            account_result["attempt_id"] = create_result.get("attempt_id")
            account_result["audit_json_url"] = create_result.get("audit_json_url")
            account_result["failure_bucket"] = create_result.get("failure_bucket")
            account_result["error"] = create_result.get("error")
            account_result["bootstrap_errors"] = _extract_bootstrap_errors(create_result)
            account_result["profile_name"] = create_result.get("profile_name") or account_result["profile_name"]
            account_result["reused_existing_session"] = bool(create_result.get("reused_existing_session"))

            if account_result["create_success"]:
                if create_result.get("reused_existing_session"):
                    test_result = dict(create_result.get("test_result") or {})
                    action_result = dict(create_result.get("action_result") or {})
                else:
                    session = RedditSession(str(account_result["profile_name"]))
                    try:
                        test_result = await test_reddit_session(session, proxy_url)
                    except Exception as exc:
                        test_result = {"success": False, "error": str(exc)}

                    try:
                        action_result = await run_reddit_action(
                            session,
                            action="open_target",
                            proxy_url=proxy_url,
                            url=account_result.get("profile_url"),
                        )
                    except Exception as exc:
                        action_result = {"success": False, "error": str(exc), "current_url": None}

                account_result["test_success"] = bool(test_result.get("success"))
                if not account_result["test_success"] and not account_result["error"]:
                    account_result["error"] = test_result.get("error")

                account_result["action_success"] = bool(action_result.get("success"))
                if not account_result["action_success"] and not account_result["error"]:
                    account_result["error"] = action_result.get("error")

                account_result["test_result"] = {
                    "success": bool(test_result.get("success")),
                    "error": test_result.get("error"),
                }
                account_result["action_result"] = {
                    "success": bool(action_result.get("success")),
                    "error": action_result.get("error"),
                    "current_url": action_result.get("current_url"),
                }

            account_result["active_session"] = bool(
                account_result["create_success"] and account_result["test_success"] and account_result["action_success"]
            )
            account_result["status"] = "success" if account_result["active_session"] else "blocked"

            report["results"].append(account_result)
            report["summary"] = _summarize_results(report["results"])
            _save_report(report)

            await _broadcast(
                broadcast_callback,
                "reddit_bulk_create_account_complete",
                {
                    "run_id": run_id,
                    "username": username,
                    "profile_name": account_result.get("profile_name"),
                    "status": account_result["status"],
                    "active_session": account_result["active_session"],
                    "failure_bucket": account_result.get("failure_bucket"),
                    "error": account_result.get("error"),
                },
            )

        report["status"] = "completed"
        report["completed_at"] = _utc_now()
        report["summary"] = _summarize_results(report["results"])
        _save_report(report)
        await _broadcast(
            broadcast_callback,
            "reddit_bulk_create_completed",
            {
                "run_id": run_id,
                "summary": report["summary"],
            },
        )
        return report
    except Exception as exc:
        logger.error("Reddit bulk session rollout failed: %s", exc, exc_info=True)
        report["status"] = "failed"
        report["completed_at"] = _utc_now()
        report["error"] = str(exc)
        report["summary"] = _summarize_results(report["results"])
        _save_report(report)
        await _broadcast(
            broadcast_callback,
            "reddit_bulk_create_failed",
            {
                "run_id": run_id,
                "error": str(exc),
                "summary": report["summary"],
            },
        )
        return report
