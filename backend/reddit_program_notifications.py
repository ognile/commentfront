"""
reddit program email notifications.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx


logger = logging.getLogger("RedditProgramNotifications")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _notification_config(program: Dict[str, Any]) -> Dict[str, Any]:
    return dict(((program.get("spec") or {}).get("notification_config") or {}))


def _default_recipient() -> Optional[str]:
    return (
        os.getenv("REDDIT_PROGRAM_NOTIFY_EMAIL")
        or os.getenv("REDDIT_PROGRAM_NOTIFY_ACCOUNT")
        or os.getenv("GOG_ACCOUNT")
        or None
    )


class GmailNotificationClient:
    def __init__(self):
        self.client_id = str(os.getenv("REDDIT_PROGRAM_GMAIL_CLIENT_ID") or "").strip()
        self.client_secret = str(os.getenv("REDDIT_PROGRAM_GMAIL_CLIENT_SECRET") or "").strip()
        self.refresh_token = str(os.getenv("REDDIT_PROGRAM_GMAIL_REFRESH_TOKEN") or "").strip()
        self.sender = str(os.getenv("REDDIT_PROGRAM_NOTIFY_ACCOUNT") or _default_recipient() or "").strip()
        self.enabled = bool(self.client_id and self.client_secret and self.refresh_token and self.sender)

    async def _access_token(self) -> str:
        if not self.enabled:
            raise RuntimeError("gmail notifications are not configured")
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                "https://oauth2.googleapis.com/token",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                content=urlencode(
                    {
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "refresh_token": self.refresh_token,
                        "grant_type": "refresh_token",
                    }
                ),
            )
            response.raise_for_status()
            payload = response.json()
            return str(payload.get("access_token") or "")

    async def send_email(self, *, to_email: str, subject: str, body: str) -> Dict[str, Any]:
        access_token = await self._access_token()
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(body)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={"raw": raw},
            )
            response.raise_for_status()
            return dict(response.json() or {})


def append_notification_log(
    program: Dict[str, Any],
    *,
    key: str,
    kind: str,
    subject: str,
    recipient: Optional[str],
    state: str,
    error: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    log = list(program.get("notification_log") or [])
    log.append(
        {
            "key": key,
            "kind": kind,
            "subject": subject,
            "recipient": recipient,
            "state": state,
            "error": error,
            "metadata": dict(metadata or {}),
            "timestamp": _utcnow(),
        }
    )
    program["notification_log"] = log[-200:]


def notification_already_sent(program: Dict[str, Any], key: str) -> bool:
    return any(str(entry.get("key") or "") == key and str(entry.get("state") or "") == "sent" for entry in list(program.get("notification_log") or []))


def build_program_counts(program: Dict[str, Any]) -> Dict[str, Any]:
    work_items = list(((program.get("compiled") or {}).get("work_items") or []))
    counts: Dict[str, Dict[str, int]] = {}
    for item in work_items:
        action = str(item.get("action") or "unknown")
        bucket = counts.setdefault(action, {"planned": 0, "completed": 0, "pending": 0, "blocked": 0})
        bucket["planned"] += 1
        status = str(item.get("status") or "pending")
        if status == "completed":
            bucket["completed"] += 1
        elif status in {"blocked", "exhausted", "cancelled"}:
            bucket["blocked"] += 1
        else:
            bucket["pending"] += 1
    return counts


def build_program_email_body(program: Dict[str, Any], *, headline: str) -> str:
    counts = build_program_counts(program)
    failure_summary = dict(program.get("failure_summary") or {})
    lines: List[str] = [
        headline,
        "",
        f"program id: {program.get('id')}",
        f"status: {program.get('status')}",
        f"next run at: {program.get('next_run_at')}",
        "",
        "counts:",
    ]
    for action, bucket in sorted(counts.items()):
        lines.append(
            f"- {action}: planned={bucket['planned']} completed={bucket['completed']} pending={bucket['pending']} blocked={bucket['blocked']}"
        )
    lines.extend(
        [
            "",
            "remaining contract:",
            json.dumps(program.get("remaining_contract") or {}, ensure_ascii=True, indent=2),
            "",
            "grouped failures:",
            json.dumps(failure_summary or {}, ensure_ascii=True, indent=2),
            "",
            "recent attempt ids:",
            json.dumps(list(program.get("recent_attempt_ids") or [])[:12], ensure_ascii=True, indent=2),
        ]
    )
    return "\n".join(lines)


class RedditProgramNotificationService:
    def __init__(self, *, gmail_client: Optional[GmailNotificationClient] = None):
        self.gmail_client = gmail_client or GmailNotificationClient()

    async def send_program_email(
        self,
        program: Dict[str, Any],
        *,
        key: str,
        kind: str,
        subject: str,
        body: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if notification_already_sent(program, key):
            return False

        config = _notification_config(program)
        summary_only = bool((metadata or {}).get("summary_only"))
        if summary_only:
            append_notification_log(
                program,
                key=key,
                kind=kind,
                subject=subject,
                recipient=None,
                state="summary_only",
                metadata=metadata,
            )
            return True
        if not bool(config.get("email_enabled", True)):
            append_notification_log(
                program,
                key=key,
                kind=kind,
                subject=subject,
                recipient=None,
                state="disabled",
                metadata=metadata,
            )
            return True

        recipient = str(config.get("recipient_email") or _default_recipient() or "").strip()
        if not recipient:
            append_notification_log(
                program,
                key=key,
                kind=kind,
                subject=subject,
                recipient=None,
                state="failed",
                error="notification recipient is not configured",
                metadata=metadata,
            )
            return True

        try:
            result = await self.gmail_client.send_email(to_email=recipient, subject=subject, body=body)
            append_notification_log(
                program,
                key=key,
                kind=kind,
                subject=subject,
                recipient=recipient,
                state="sent",
                metadata={**dict(metadata or {}), "message_id": result.get("id"), "thread_id": result.get("threadId")},
            )
        except Exception as exc:
            logger.error(f"reddit program email failed ({kind}): {exc}")
            append_notification_log(
                program,
                key=key,
                kind=kind,
                subject=subject,
                recipient=recipient,
                state="failed",
                error=str(exc),
                metadata=metadata,
            )
        return True
