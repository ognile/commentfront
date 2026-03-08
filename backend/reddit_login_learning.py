"""
Persistent learning store for Reddit production login convergence.
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from reddit_session import RedditSession, list_saved_reddit_sessions
from safe_io import atomic_write_json, safe_read_json


def _default_learning_path() -> Path:
    env_value = os.getenv("REDDIT_LOGIN_LEARNING_PATH")
    if env_value:
        return Path(env_value)
    if Path("/data").exists():
        return Path("/data/reddit_login_learning.json")
    return Path("/tmp/commentbot_reddit_login_learning.json")


REDDIT_LOGIN_LEARNING_PATH = _default_learning_path()


STRATEGY_CONFIGS: Dict[str, Dict[str, Any]] = {
    "baseline_humanized": {
        "strategy_id": "baseline_humanized",
        "login_identifier_preference": "username",
        "humanize_input": True,
        "form_wait_timeout_ms": 12000,
        "form_reload_attempts": 0,
        "pre_interaction_wait_ms": 0,
        "between_field_wait_ms": 400,
        "post_submit_wait_ms": 3500,
        "credential_retry_attempts": 0,
        "credential_retry_wait_ms": 0,
        "otp_pre_submit_wait_ms": 800,
        "otp_retry_attempts": 0,
        "otp_min_remaining_seconds": 8,
        "otp_resolution_timeout_ms": 20000,
        "auth_surface_timeout_ms": 20000,
        "force_home_settle": False,
        "fresh_page_home_settle": False,
    },
    "settle_home": {
        "strategy_id": "settle_home",
        "login_identifier_preference": "username",
        "humanize_input": True,
        "form_wait_timeout_ms": 14000,
        "form_reload_attempts": 0,
        "pre_interaction_wait_ms": 1200,
        "between_field_wait_ms": 600,
        "post_submit_wait_ms": 4500,
        "credential_retry_attempts": 1,
        "credential_retry_wait_ms": 6000,
        "otp_pre_submit_wait_ms": 1200,
        "otp_retry_attempts": 0,
        "otp_min_remaining_seconds": 8,
        "otp_resolution_timeout_ms": 22000,
        "auth_surface_timeout_ms": 22000,
        "force_home_settle": True,
        "fresh_page_home_settle": True,
    },
    "acquire_form_reload": {
        "strategy_id": "acquire_form_reload",
        "login_identifier_preference": "username",
        "humanize_input": True,
        "form_wait_timeout_ms": 18000,
        "form_reload_attempts": 2,
        "pre_interaction_wait_ms": 1500,
        "between_field_wait_ms": 700,
        "post_submit_wait_ms": 4500,
        "credential_retry_attempts": 1,
        "credential_retry_wait_ms": 7000,
        "otp_pre_submit_wait_ms": 1200,
        "otp_retry_attempts": 0,
        "otp_min_remaining_seconds": 8,
        "otp_resolution_timeout_ms": 22000,
        "auth_surface_timeout_ms": 22000,
        "force_home_settle": True,
        "fresh_page_home_settle": True,
    },
    "email_identifier_dwell": {
        "strategy_id": "email_identifier_dwell",
        "login_identifier_preference": "email",
        "humanize_input": True,
        "form_wait_timeout_ms": 15000,
        "form_reload_attempts": 1,
        "pre_interaction_wait_ms": 4500,
        "between_field_wait_ms": 1200,
        "post_submit_wait_ms": 6500,
        "credential_retry_attempts": 2,
        "credential_retry_wait_ms": 9000,
        "otp_pre_submit_wait_ms": 1200,
        "otp_retry_attempts": 0,
        "otp_min_remaining_seconds": 10,
        "otp_resolution_timeout_ms": 22000,
        "auth_surface_timeout_ms": 22000,
        "force_home_settle": True,
        "fresh_page_home_settle": True,
    },
    "email_identifier_fast_otp": {
        "strategy_id": "email_identifier_fast_otp",
        "login_identifier_preference": "email",
        "humanize_input": True,
        "form_wait_timeout_ms": 15000,
        "form_reload_attempts": 1,
        "pre_interaction_wait_ms": 4500,
        "between_field_wait_ms": 1200,
        "post_submit_wait_ms": 6500,
        "credential_retry_attempts": 2,
        "credential_retry_wait_ms": 9000,
        "otp_pre_submit_wait_ms": 250,
        "otp_retry_attempts": 1,
        "otp_min_remaining_seconds": 0,
        "otp_resolution_timeout_ms": 24000,
        "auth_surface_timeout_ms": 24000,
        "force_home_settle": True,
        "fresh_page_home_settle": True,
    },
    "otp_retry_fresh_cycle": {
        "strategy_id": "otp_retry_fresh_cycle",
        "login_identifier_preference": "username",
        "humanize_input": True,
        "form_wait_timeout_ms": 15000,
        "form_reload_attempts": 0,
        "pre_interaction_wait_ms": 2000,
        "between_field_wait_ms": 900,
        "post_submit_wait_ms": 4500,
        "credential_retry_attempts": 1,
        "credential_retry_wait_ms": 7000,
        "otp_pre_submit_wait_ms": 1800,
        "otp_retry_attempts": 1,
        "otp_min_remaining_seconds": 24,
        "otp_resolution_timeout_ms": 24000,
        "auth_surface_timeout_ms": 24000,
        "force_home_settle": True,
        "fresh_page_home_settle": True,
    },
}


def default_strategy_config(strategy_id: Optional[str] = None) -> Dict[str, Any]:
    chosen = str(strategy_id or "baseline_humanized").strip() or "baseline_humanized"
    return dict(STRATEGY_CONFIGS.get(chosen, STRATEGY_CONFIGS["baseline_humanized"]))


class RedditLoginLearningStore:
    def __init__(self, file_path: Optional[str] = None):
        self.file_path = str(file_path or REDDIT_LOGIN_LEARNING_PATH)
        self.data = self._load()

    def _load(self) -> Dict[str, Any]:
        loaded = safe_read_json(self.file_path)
        if isinstance(loaded, dict):
            loaded.setdefault("policy_version", 1)
            loaded.setdefault("updated_at", None)
            loaded.setdefault("global", {})
            loaded.setdefault("accounts", {})
            return loaded
        return {
            "policy_version": 1,
            "updated_at": None,
            "global": {
                "linked_usernames": [],
                "winner_cookie_signatures": [],
                "last_convergence_run_id": None,
            },
            "accounts": {},
        }

    def save(self) -> bool:
        self.data["updated_at"] = datetime.utcnow().isoformat() + "Z"
        self.data["policy_version"] = int(self.data.get("policy_version") or 0) + 1
        return atomic_write_json(self.file_path, self.data)

    def summary(self) -> Dict[str, Any]:
        accounts = dict(self.data.get("accounts") or {})
        blocked = {}
        linked = {}
        for username, record in accounts.items():
            if record.get("linked"):
                linked[username] = {
                    "last_strategy_id": record.get("last_strategy_id"),
                    "linked_profile_name": record.get("linked_profile_name"),
                    "last_attempt_id": record.get("last_attempt_id"),
                }
            else:
                blocked[username] = {
                    "last_failure_bucket": record.get("last_failure_bucket"),
                    "last_strategy_id": record.get("last_strategy_id"),
                    "last_attempt_id": record.get("last_attempt_id"),
                    "last_audit_json_url": record.get("last_audit_json_url"),
                }
        return {
            "policy_version": self.data.get("policy_version"),
            "updated_at": self.data.get("updated_at"),
            "linked_accounts": linked,
            "blocked_accounts": blocked,
            "linked_count": len(linked),
            "blocked_count": len(blocked),
            "winner_cookie_signatures": list(self.data.get("global", {}).get("winner_cookie_signatures") or []),
            "last_convergence_run_id": self.data.get("global", {}).get("last_convergence_run_id"),
        }

    def get_account(self, username: str) -> Dict[str, Any]:
        return dict((self.data.get("accounts") or {}).get(str(username or ""), {}))

    def set_last_convergence_run(self, run_id: str) -> None:
        self.data.setdefault("global", {})["last_convergence_run_id"] = run_id
        self.save()

    def sync_linked_sessions(self) -> None:
        changed = False
        linked_usernames: List[str] = []
        winner_cookie_signatures: List[List[str]] = []
        for session_info in list_saved_reddit_sessions():
            if not session_info.get("linked_credential_id"):
                continue
            username = str(session_info.get("username") or "").strip()
            profile_name = str(session_info.get("profile_name") or "").strip()
            if not username or not profile_name:
                continue
            session = RedditSession(profile_name)
            if not session.load():
                continue
            cookie_names = sorted({str(cookie.get("name") or "") for cookie in session.get_cookies()})
            if not cookie_names:
                continue
            linked_usernames.append(username)
            if cookie_names not in winner_cookie_signatures:
                winner_cookie_signatures.append(cookie_names)
            account = self.data.setdefault("accounts", {}).setdefault(username, {})
            if account.get("linked") is not True:
                changed = True
            if account.get("linked_profile_name") != profile_name:
                changed = True
            if account.get("winner_cookie_signature") != cookie_names:
                changed = True
            account["linked"] = True
            account["linked_profile_name"] = profile_name
            account["winner_cookie_signature"] = cookie_names
            if not account.get("last_strategy_id"):
                account["last_strategy_id"] = "existing_linked_session"

        global_data = self.data.setdefault("global", {})
        normalized_usernames = sorted(set(linked_usernames))
        if global_data.get("linked_usernames") != normalized_usernames:
            changed = True
        if global_data.get("winner_cookie_signatures") != winner_cookie_signatures:
            changed = True
        global_data["linked_usernames"] = normalized_usernames
        global_data["winner_cookie_signatures"] = winner_cookie_signatures
        if changed:
            self.save()

    def record_attempt(
        self,
        *,
        username: str,
        strategy_id: str,
        result: Dict[str, Any],
        linked: bool,
        test_success: bool = False,
        action_success: bool = False,
        session: Optional[RedditSession] = None,
    ) -> None:
        username = str(username or "").strip()
        if not username:
            return

        account = self.data.setdefault("accounts", {}).setdefault(
            username,
            {
                "history": [],
                "linked": False,
            },
        )
        history = list(account.get("history") or [])
        history.append(
            {
                "ts": datetime.utcnow().isoformat() + "Z",
                "strategy_id": strategy_id,
                "attempt_id": result.get("attempt_id"),
                "audit_json_url": result.get("audit_json_url"),
                "success": bool(result.get("success")),
                "linked": bool(linked),
                "test_success": bool(test_success),
                "action_success": bool(action_success),
                "failure_bucket": result.get("failure_bucket"),
                "error": result.get("error"),
            }
        )
        account["history"] = history[-20:]
        account["last_attempt_id"] = result.get("attempt_id")
        account["last_audit_json_url"] = result.get("audit_json_url")
        account["last_strategy_id"] = strategy_id
        account["last_failure_bucket"] = result.get("failure_bucket")
        account["last_error"] = result.get("error")
        account["linked"] = bool(linked)

        if linked:
            account["linked_profile_name"] = result.get("profile_name")
            cookie_names = []
            if session and session.load():
                cookie_names = sorted({str(cookie.get("name") or "") for cookie in session.get_cookies()})
            elif session:
                cookie_names = sorted({str(cookie.get("name") or "") for cookie in session.get_cookies()})
            if cookie_names:
                account["winner_cookie_signature"] = cookie_names
                global_signatures = self.data.setdefault("global", {}).setdefault("winner_cookie_signatures", [])
                if cookie_names not in global_signatures:
                    global_signatures.append(cookie_names)
            linked_usernames = set(self.data.setdefault("global", {}).get("linked_usernames") or [])
            linked_usernames.add(username)
            self.data["global"]["linked_usernames"] = sorted(linked_usernames)

        self.save()

    def recommended_strategies(self, username: str) -> List[Dict[str, Any]]:
        record = self.get_account(username)
        history = list(record.get("history") or [])
        recent_failure_buckets = [str(item.get("failure_bucket") or "").strip().lower() for item in history[-3:]]
        recent_errors = " ".join(str(item.get("error") or "") for item in history[-3:]).lower()
        historical_errors = " ".join(str(item.get("error") or "") for item in history[-10:]).lower()
        historical_strategy_ids = [str(item.get("strategy_id") or "").strip() for item in history[-10:]]

        ordered: List[str] = []
        if "otp_never_shown" in recent_failure_buckets or "err_empty_response" in recent_errors or "inputs not found" in recent_errors:
            ordered.extend(["acquire_form_reload", "email_identifier_dwell", "settle_home", "baseline_humanized"])
        elif "otp submit rejected" in recent_errors or "otp submit rejected" in historical_errors:
            ordered.extend(["email_identifier_fast_otp", "otp_retry_fresh_cycle", "settle_home", "email_identifier_dwell", "baseline_humanized", "acquire_form_reload"])
        elif "user_interaction_failed" in recent_failure_buckets or "protected_routes_fail" in recent_failure_buckets:
            ordered.extend(["email_identifier_dwell", "settle_home", "email_identifier_fast_otp", "otp_retry_fresh_cycle", "baseline_humanized", "acquire_form_reload"])
        else:
            ordered.extend(["baseline_humanized", "settle_home", "email_identifier_dwell", "email_identifier_fast_otp", "otp_retry_fresh_cycle", "acquire_form_reload"])

        if "email_identifier_dwell" in historical_strategy_ids:
            ordered.insert(0, "email_identifier_dwell")
        if "otp submit rejected" in historical_errors:
            ordered.insert(0, "email_identifier_fast_otp")

        deduped: List[Dict[str, Any]] = []
        seen = set()
        for strategy_id in ordered:
            if strategy_id in seen:
                continue
            seen.add(strategy_id)
            deduped.append(default_strategy_config(strategy_id))
        return deduped
