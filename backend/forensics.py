"""
Supabase-backed forensic evidence spine.

This module is intentionally backend-first and optional. If Supabase env vars are
missing, the recorder degrades to a no-op and runtime behavior stays intact.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
import os
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import httpx
from playwright.async_api import BrowserContext, Page

from env_loader import load_project_env


load_project_env()

logger = logging.getLogger("Forensics")


SUPABASE_URL = (os.getenv("SUPABASE_URL") or "").rstrip("/")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_STORAGE_BUCKET_FORENSICS = os.getenv("SUPABASE_STORAGE_BUCKET_FORENSICS", "forensics")
FORENSICS_RETENTION_RUNS = int(os.getenv("FORENSICS_RETENTION_RUNS", "500"))

ACTIVE_RESTRICTION_KEYWORDS = [
    "you can't comment right now",
    "you cannot comment right now",
    "try again later",
    "action blocked",
    "temporarily blocked",
    "you've been restricted",
    "you have been restricted",
    "request review",
    "can't comment for",
    "couldn't comment for",
]

INFRA_FAILURE_KEYWORDS = [
    "timeout",
    "proxy",
    "connection",
    "network",
    "net::err",
    "tunnel",
    "econnrefused",
    "econnreset",
    "browsertype.launch",
    "executable doesn't exist",
    "playwright",
]

SENSITIVE_HEADER_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "apikey",
    "x-client-info",
    "x-supabase-auth",
    "proxy-authorization",
}
SENSITIVE_QUERY_KEYS = {"token", "key", "apikey", "auth", "authorization", "session", "cookie", "sig"}
SENSITIVE_BODY_KEYS = {"password", "token", "cookie", "authorization", "proxy", "proxy_url", "apikey"}

_current_recorder: ContextVar[Optional["ForensicAttemptRecorder"]] = ContextVar("current_forensic_recorder", default=None)


def _utcnow() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _brief_text(value: Any, limit: int = 400) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "..."


def _json_bytes(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=True, indent=2).encode("utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _strip_sensitive_query(url: str) -> str:
    try:
        parts = urlsplit(url)
        if not parts.query:
            return url
        safe_items = []
        for item in parts.query.split("&"):
            if "=" not in item:
                safe_items.append(item)
                continue
            key, value = item.split("=", 1)
            if key.lower() in SENSITIVE_QUERY_KEYS:
                safe_items.append(f"{key}=redacted")
            else:
                safe_items.append(f"{key}={value}")
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(safe_items), parts.fragment))
    except Exception:
        return url


def _redact_headers(headers: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    safe: Dict[str, Any] = {}
    for key, value in (headers or {}).items():
        lowered = str(key).lower()
        safe[key] = "redacted" if lowered in SENSITIVE_HEADER_KEYS else value
    return safe


def _redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        safe = {}
        for key, item in value.items():
            if str(key).lower() in SENSITIVE_BODY_KEYS:
                safe[key] = "redacted"
            else:
                safe[key] = _redact_payload(item)
        return safe
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    if isinstance(value, str):
        return _brief_text(value, limit=2000)
    return value


def has_direct_active_restriction_proof(reason: Optional[str]) -> bool:
    lowered = str(reason or "").lower()
    if "ended on" in lowered:
        return False
    return any(keyword in lowered for keyword in ACTIVE_RESTRICTION_KEYWORDS)


def is_infra_error_text(text: Optional[str]) -> bool:
    lowered = str(text or "").lower()
    return any(keyword in lowered for keyword in INFRA_FAILURE_KEYWORDS)


def get_current_forensic_recorder() -> Optional["ForensicAttemptRecorder"]:
    return _current_recorder.get()


def set_current_forensic_recorder(recorder: Optional["ForensicAttemptRecorder"]):
    return _current_recorder.set(recorder)


def reset_current_forensic_recorder(token) -> None:
    _current_recorder.reset(token)


class _NoopAsyncToken:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


@dataclass
class ForensicVerdict:
    final_verdict: str
    status: str
    failure_class: Optional[str]
    confidence: float
    winning_evidence: List[str]
    rejected_hypotheses: List[Dict[str, Any]]
    summary: str
    summary_payload: Dict[str, Any]


def build_comment_verdict(result: Dict[str, Any]) -> ForensicVerdict:
    method = str(result.get("method") or "")
    error = str(result.get("error") or "")
    winning_evidence: List[str] = []
    rejected: List[Dict[str, Any]] = []
    summary_payload = {
        "method": method,
        "steps_completed": list(result.get("steps_completed") or []),
        "submission_evidence": result.get("submission_evidence"),
    }

    if result.get("success"):
        if method == "hybrid_verified":
            winning_evidence = ["local_dom_evidence", "action_trace_evidence"]
        else:
            winning_evidence = ["screenshot_evidence", "action_trace_evidence"]
        return ForensicVerdict(
            final_verdict="success_confirmed",
            status="completed",
            failure_class=None,
            confidence=float(result.get("verification_confidence") or 1.0),
            winning_evidence=winning_evidence,
            rejected_hypotheses=[{"hypothesis": "failed_confirmed", "reason": "comment posting succeeded"}],
            summary="comment posting completed with confirming evidence.",
            summary_payload=summary_payload,
        )

    if method in ("verification_inconclusive", "uncertain_no_repost"):
        winning_evidence = ["local_dom_evidence", "action_trace_evidence"]
        rejected = [{"hypothesis": "failed_confirmed", "reason": "strong submit evidence exists"}]
        return ForensicVerdict(
            final_verdict="success_inconclusive",
            status="completed",
            failure_class=None,
            confidence=0.65,
            winning_evidence=winning_evidence,
            rejected_hypotheses=rejected,
            summary="comment submission evidence is strong, but visual confirmation stayed inconclusive.",
            summary_payload=summary_payload,
        )

    if result.get("throttled"):
        throttle_reason = str(result.get("throttle_reason") or "")
        final_verdict = "restriction_verified" if has_direct_active_restriction_proof(throttle_reason) else "restriction_suspected"
        winning_evidence = ["screenshot_evidence", "model_evidence"]
        rejected = [{"hypothesis": "infra_failure", "reason": "restriction-like ui signal won"}]
        return ForensicVerdict(
            final_verdict=final_verdict,
            status="failed",
            failure_class="restriction",
            confidence=0.8 if final_verdict == "restriction_verified" else 0.55,
            winning_evidence=winning_evidence,
            rejected_hypotheses=rejected,
            summary=f"posting failed with restriction signal: {_brief_text(throttle_reason, 160)}",
            summary_payload={**summary_payload, "throttle_reason": throttle_reason},
        )

    if is_infra_error_text(error):
        return ForensicVerdict(
            final_verdict="infra_failure",
            status="failed",
            failure_class="infrastructure",
            confidence=0.95,
            winning_evidence=["network_evidence"],
            rejected_hypotheses=[{"hypothesis": "restriction_verified", "reason": "infra keywords dominate the failure"}],
            summary=f"posting failed due to infrastructure/network evidence: {_brief_text(error, 180)}",
            summary_payload=summary_payload,
        )

    return ForensicVerdict(
        final_verdict="failed_confirmed",
        status="failed",
        failure_class="facebook_error",
        confidence=0.9,
        winning_evidence=["action_trace_evidence"],
        rejected_hypotheses=[{"hypothesis": "success_confirmed", "reason": "no confirming evidence remained"}],
        summary=f"posting failed with confirmed workflow error: {_brief_text(error, 180)}",
        summary_payload=summary_payload,
    )


def build_adaptive_verdict(result: Dict[str, Any]) -> ForensicVerdict:
    final_status = str(result.get("final_status") or "unknown")
    errors = list(result.get("errors") or [])
    if final_status == "task_completed":
        return ForensicVerdict(
            final_verdict="success_confirmed",
            status="completed",
            failure_class=None,
            confidence=0.9,
            winning_evidence=["action_trace_evidence", "model_evidence"],
            rejected_hypotheses=[{"hypothesis": "needs_review", "reason": "task reached completed state"}],
            summary="adaptive task completed successfully.",
            summary_payload={"final_status": final_status, "steps": len(result.get("steps") or [])},
        )

    error_blob = " | ".join(str(item) for item in errors)
    if is_infra_error_text(error_blob):
        final_verdict = "infra_failure"
        failure_class = "infrastructure"
    else:
        final_verdict = "needs_review"
        failure_class = "workflow"
    return ForensicVerdict(
        final_verdict=final_verdict,
        status="failed",
        failure_class=failure_class,
        confidence=0.7,
        winning_evidence=["action_trace_evidence", "model_evidence"],
        rejected_hypotheses=[{"hypothesis": "success_confirmed", "reason": "task did not reach completed state"}],
        summary=f"adaptive task ended with status '{final_status}'.",
        summary_payload={"final_status": final_status, "errors": errors},
    )


def build_generic_verdict(result: Dict[str, Any], *, success_summary: str) -> ForensicVerdict:
    success = bool(result.get("success"))
    error = str(result.get("error") or "")
    if success:
        return ForensicVerdict(
            final_verdict="success_confirmed",
            status="completed",
            failure_class=None,
            confidence=0.9,
            winning_evidence=["action_trace_evidence"],
            rejected_hypotheses=[{"hypothesis": "failed_confirmed", "reason": "action returned success"}],
            summary=success_summary,
            summary_payload={"result": _redact_payload(result)},
        )
    if is_infra_error_text(error):
        return ForensicVerdict(
            final_verdict="infra_failure",
            status="failed",
            failure_class="infrastructure",
            confidence=0.9,
            winning_evidence=["network_evidence"],
            rejected_hypotheses=[],
            summary=f"action failed due to infrastructure evidence: {_brief_text(error, 180)}",
            summary_payload={"result": _redact_payload(result)},
        )
    return ForensicVerdict(
        final_verdict="failed_confirmed",
        status="failed",
        failure_class="workflow",
        confidence=0.8,
        winning_evidence=["action_trace_evidence"],
        rejected_hypotheses=[],
        summary=f"action failed: {_brief_text(error, 180)}",
        summary_payload={"result": _redact_payload(result)},
    )


class SupabaseForensicsStore:
    def __init__(self):
        self.base_url = SUPABASE_URL
        self.anon_key = SUPABASE_ANON_KEY
        self.service_key = SUPABASE_SERVICE_ROLE_KEY
        self.bucket = SUPABASE_STORAGE_BUCKET_FORENSICS
        self.enabled = bool(self.base_url and self.service_key)

    def _headers(self, *, prefer_return: bool = False, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = {
            "apikey": self.service_key,
            "Authorization": f"Bearer {self.service_key}",
        }
        if prefer_return:
            headers["Prefer"] = "return=representation"
        if extra:
            headers.update(extra)
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_payload: Any = None,
        content: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
        accept_json: bool = True,
    ) -> Any:
        if not self.enabled:
            return None
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.request(
                method,
                url,
                params=params,
                json=json_payload,
                content=content,
                headers=headers or self._headers(prefer_return=method in {"POST", "PATCH"}),
            )
        if response.status_code >= 400:
            raise RuntimeError(f"supabase request failed {response.status_code}: {_brief_text(response.text, 400)}")
        if not accept_json:
            return response
        if not response.text:
            return None
        return response.json()

    async def insert_row(self, table: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        rows = await self._request("POST", f"/rest/v1/{table}", json_payload=payload)
        if isinstance(rows, list):
            return rows[0] if rows else None
        return rows

    async def bulk_insert(self, table: str, rows: List[Dict[str, Any]]) -> None:
        if not rows or not self.enabled:
            return
        await self._request("POST", f"/rest/v1/{table}", json_payload=rows)

    async def update_rows(self, table: str, filters: Dict[str, Any], payload: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
        params = {key: f"eq.{value}" for key, value in filters.items()}
        rows = await self._request("PATCH", f"/rest/v1/{table}", params=params, json_payload=payload)
        return rows if isinstance(rows, list) else None

    async def select_rows(
        self,
        table: str,
        *,
        filters: Optional[Dict[str, Any]] = None,
        select: str = "*",
        order: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        params: Dict[str, Any] = {"select": select}
        for key, value in (filters or {}).items():
            if isinstance(value, tuple) and len(value) == 2:
                params[key] = f"{value[0]}.{value[1]}"
            else:
                params[key] = f"eq.{value}"
        if order:
            params["order"] = order
        if limit is not None:
            params["limit"] = str(limit)
        rows = await self._request("GET", f"/rest/v1/{table}", params=params)
        return rows if isinstance(rows, list) else []

    async def upload_artifact(self, storage_path: str, data: bytes, content_type: str) -> None:
        if not self.enabled:
            return
        headers = self._headers(extra={"x-upsert": "true", "content-type": content_type})
        await self._request(
            "POST",
            f"/storage/v1/object/{self.bucket}/{quote(storage_path, safe='/')}",
            content=data,
            headers=headers,
            accept_json=False,
        )

    async def download_artifact(self, storage_path: str) -> httpx.Response:
        response = await self._request(
            "GET",
            f"/storage/v1/object/authenticated/{self.bucket}/{quote(storage_path, safe='/')}",
            headers=self._headers(),
            accept_json=False,
        )
        return response


_store: Optional[SupabaseForensicsStore] = None


def get_forensics_store() -> SupabaseForensicsStore:
    global _store
    if _store is None:
        _store = SupabaseForensicsStore()
    return _store


@dataclass
class NetworkCapture:
    attempt_id: str
    events: List[Dict[str, Any]] = field(default_factory=list)
    _request_index: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def bind(self, page: Page) -> None:
        page.on("request", self._on_request)
        page.on("response", self._on_response)
        page.on("requestfailed", self._on_request_failed)

    def _on_request(self, request) -> None:
        entry = {
            "request_id": str(uuid.uuid4()),
            "ts": _utcnow(),
            "kind": "request",
            "method": request.method,
            "url": _strip_sensitive_query(request.url),
            "resource_type": request.resource_type,
            "headers": _redact_headers(request.headers),
        }
        post_data = None
        try:
            post_data = request.post_data
        except Exception:
            post_data = None
        if post_data:
            entry["post_data_excerpt"] = _brief_text(post_data, 2000)
        key = repr(request)
        self._request_index[key] = entry
        self.events.append(entry)

    def _on_response(self, response) -> None:
        request = response.request
        entry = {
            "request_ref": repr(request),
            "ts": _utcnow(),
            "kind": "response",
            "status": response.status,
            "status_text": response.status_text,
            "url": _strip_sensitive_query(response.url),
            "headers": _redact_headers(response.headers),
        }

        async def _capture_body():
            try:
                body_text = await response.text()
            except Exception:
                return
            if not body_text:
                return
            entry["body_excerpt"] = _brief_text(body_text, 4000)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_capture_body())
        except RuntimeError:
            pass

        self.events.append(entry)

    def _on_request_failed(self, request) -> None:
        entry = self._request_index.get(repr(request), {})
        failure = request.failure
        self.events.append(
            {
                "request_ref": repr(request),
                "ts": _utcnow(),
                "kind": "request_failed",
                "url": _strip_sensitive_query(request.url),
                "failure": _brief_text(failure or "unknown", 400),
                "request": entry,
            }
        )


class ForensicAttemptRecorder:
    def __init__(
        self,
        *,
        platform: str,
        engine: str,
        profile_name: Optional[str] = None,
        campaign_id: Optional[str] = None,
        job_id: Optional[str] = None,
        session_id: Optional[str] = None,
        parent_attempt_id: Optional[str] = None,
        run_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.store = get_forensics_store()
        self.enabled = self.store.enabled
        self.attempt_id = str(uuid.uuid4())
        self.trace_id = trace_id or self.attempt_id
        self.run_id = run_id or campaign_id or self.trace_id
        self.platform = platform
        self.engine = engine
        self.profile_name = profile_name
        self.campaign_id = campaign_id
        self.job_id = job_id
        self.session_id = session_id
        self.parent_attempt_id = parent_attempt_id
        self.metadata = metadata or {}
        self.started_at = _utcnow()
        self._ordinal = 0
        self._queued_events: List[Dict[str, Any]] = []
        self.network_capture: Optional[NetworkCapture] = None

    async def initialize(self) -> "ForensicAttemptRecorder":
        if not self.enabled:
            return self
        try:
            await self.store.insert_row(
                "forensic_attempts",
                {
                    "attempt_id": self.attempt_id,
                    "trace_id": self.trace_id,
                    "run_id": self.run_id,
                    "platform": self.platform,
                    "engine": self.engine,
                    "profile_name": self.profile_name,
                    "campaign_id": self.campaign_id,
                    "job_id": self.job_id,
                    "session_id": self.session_id,
                    "parent_attempt_id": self.parent_attempt_id,
                    "started_at": self.started_at,
                    "phase": "started",
                    "status": "running",
                    "metadata": _redact_payload(self.metadata),
                },
            )
        except Exception as exc:
            logger.error(f"failed to initialize forensic attempt {self.attempt_id}: {exc}")
            self.enabled = False
        return self

    def _next_ordinal(self) -> int:
        self._ordinal += 1
        return self._ordinal

    def queue_event(
        self,
        event_type: str,
        payload: Dict[str, Any],
        *,
        phase: Optional[str] = None,
        source: Optional[str] = None,
        event_time: Optional[str] = None,
    ) -> None:
        if not self.enabled:
            return
        self._queued_events.append(
            {
                "attempt_id": self.attempt_id,
                "event_type": event_type,
                "phase": phase,
                "source": source,
                "event_time": event_time or _utcnow(),
                "ordinal": self._next_ordinal(),
                "payload": _redact_payload(payload),
            }
        )

    async def record_event(
        self,
        event_type: str,
        payload: Dict[str, Any],
        *,
        phase: Optional[str] = None,
        source: Optional[str] = None,
        event_time: Optional[str] = None,
    ) -> None:
        self.queue_event(event_type, payload, phase=phase, source=source, event_time=event_time)
        await self.flush_events()

    async def flush_events(self) -> None:
        if not self.enabled or not self._queued_events:
            return
        pending = list(self._queued_events)
        self._queued_events.clear()
        try:
            await self.store.bulk_insert("forensic_events", pending)
        except Exception as exc:
            logger.error(f"failed to flush forensic events for {self.attempt_id}: {exc}")

    async def attach_page(self, page: Page, context: Optional[BrowserContext] = None) -> None:
        self.network_capture = NetworkCapture(self.attempt_id)
        self.network_capture.bind(page)
        await self.record_event(
            "network_capture",
            {
                "status": "attached",
                "page_url": page.url,
                "has_context": bool(context),
            },
            phase="setup",
            source=self.engine,
        )

    def _artifact_storage_path(self, artifact_type: str, filename: str) -> str:
        now = datetime.utcnow()
        return (
            f"{self.platform}/{self.engine}/{now:%Y}/{now:%m}/{now:%d}/"
            f"{self.attempt_id}/{artifact_type}/{filename}"
        )

    async def attach_bytes_artifact(
        self,
        *,
        artifact_type: str,
        filename: str,
        data: bytes,
        content_type: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.enabled or not data:
            return None
        storage_path = self._artifact_storage_path(artifact_type, filename)
        artifact_id = str(uuid.uuid4())
        payload = {
            "artifact_id": artifact_id,
            "attempt_id": self.attempt_id,
            "artifact_type": artifact_type,
            "storage_bucket": self.store.bucket,
            "storage_path": storage_path,
            "content_type": content_type,
            "size_bytes": len(data),
            "sha256": _sha256_bytes(data),
            "redaction_level": "light",
            "captured_at": _utcnow(),
            "metadata": _redact_payload(metadata or {}),
        }
        try:
            await self.store.upload_artifact(storage_path, data, content_type)
            await self.store.insert_row("forensic_artifacts", payload)
            self.queue_event(
                "artifact_attached",
                {
                    "artifact_id": artifact_id,
                    "artifact_type": artifact_type,
                    "storage_path": storage_path,
                },
                phase="artifact",
                source=self.engine,
            )
            return payload
        except Exception as exc:
            logger.error(f"failed to upload forensic artifact {artifact_type} for {self.attempt_id}: {exc}")
            return None

    async def attach_json_artifact(
        self,
        *,
        artifact_type: str,
        filename: str,
        data: Any,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        return await self.attach_bytes_artifact(
            artifact_type=artifact_type,
            filename=filename,
            data=_json_bytes(_redact_payload(data)),
            content_type="application/json",
            metadata=metadata,
        )

    async def attach_file_artifact(
        self,
        *,
        artifact_type: str,
        file_path: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        path = Path(file_path)
        if not path.exists():
            return None
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        try:
            data = path.read_bytes()
        except Exception as exc:
            logger.error(f"failed to read artifact file {file_path}: {exc}")
            return None
        return await self.attach_bytes_artifact(
            artifact_type=artifact_type,
            filename=path.name,
            data=data,
            content_type=content_type,
            metadata=metadata,
        )

    async def flush_network_capture(self) -> None:
        if not self.enabled or not self.network_capture or not self.network_capture.events:
            return
        await self.attach_json_artifact(
            artifact_type="network_bundle",
            filename="network.json",
            data={
                "attempt_id": self.attempt_id,
                "events": self.network_capture.events,
            },
            metadata={"event_count": len(self.network_capture.events)},
        )

    async def finalize(self, verdict: ForensicVerdict, *, metadata: Optional[Dict[str, Any]] = None) -> None:
        if not self.enabled:
            return
        await self.flush_network_capture()
        await self.flush_events()
        ended_at = _utcnow()
        try:
            await self.store.insert_row(
                "forensic_verdicts",
                {
                    "attempt_id": self.attempt_id,
                    "final_verdict": verdict.final_verdict,
                    "confidence": verdict.confidence,
                    "winning_evidence": verdict.winning_evidence,
                    "rejected_hypotheses": verdict.rejected_hypotheses,
                    "summary": verdict.summary,
                    "summary_payload": verdict.summary_payload,
                },
            )
            await self.store.update_rows(
                "forensic_attempts",
                {"attempt_id": self.attempt_id},
                {
                    "ended_at": ended_at,
                    "phase": "completed",
                    "status": verdict.status,
                    "final_verdict": verdict.final_verdict,
                    "failure_class": verdict.failure_class,
                    "confidence": verdict.confidence,
                    "metadata": _redact_payload({**self.metadata, **(metadata or {})}),
                    "evidence_summary": {
                        "summary": verdict.summary,
                        "winning_evidence": verdict.winning_evidence,
                        "rejected_hypotheses": verdict.rejected_hypotheses,
                    },
                },
            )
        except Exception as exc:
            logger.error(f"failed to finalize forensic attempt {self.attempt_id}: {exc}")


async def start_forensic_attempt(**kwargs) -> ForensicAttemptRecorder:
    recorder = ForensicAttemptRecorder(**kwargs)
    return await recorder.initialize()


async def link_attempts(parent_attempt_id: Optional[str], child_attempt_id: Optional[str], *, link_type: str, metadata: Optional[Dict[str, Any]] = None) -> None:
    if not parent_attempt_id or not child_attempt_id:
        return
    store = get_forensics_store()
    if not store.enabled:
        return
    try:
        await store.insert_row(
            "forensic_links",
            {
                "parent_attempt_id": parent_attempt_id,
                "child_attempt_id": child_attempt_id,
                "link_type": link_type,
                "metadata": _redact_payload(metadata or {}),
            },
        )
    except Exception as exc:
        logger.error(f"failed to link forensic attempts {parent_attempt_id} -> {child_attempt_id}: {exc}")


async def attach_current_json_artifact(artifact_type: str, filename: str, data: Any, metadata: Optional[Dict[str, Any]] = None) -> None:
    recorder = get_current_forensic_recorder()
    if recorder:
        await recorder.attach_json_artifact(artifact_type=artifact_type, filename=filename, data=data, metadata=metadata)


async def attach_current_file_artifact(artifact_type: str, file_path: str, metadata: Optional[Dict[str, Any]] = None) -> None:
    recorder = get_current_forensic_recorder()
    if recorder:
        await recorder.attach_file_artifact(artifact_type=artifact_type, file_path=file_path, metadata=metadata)


def queue_current_event(event_type: str, payload: Dict[str, Any], *, phase: Optional[str] = None, source: Optional[str] = None) -> None:
    recorder = get_current_forensic_recorder()
    if recorder:
        recorder.queue_event(event_type, payload, phase=phase, source=source)


async def record_current_event(event_type: str, payload: Dict[str, Any], *, phase: Optional[str] = None, source: Optional[str] = None) -> None:
    recorder = get_current_forensic_recorder()
    if recorder:
        await recorder.record_event(event_type, payload, phase=phase, source=source)


async def list_forensic_attempts(*, filters: Optional[Dict[str, Any]] = None, limit: int = 50) -> List[Dict[str, Any]]:
    store = get_forensics_store()
    attempts = await store.select_rows("forensic_attempts", filters=filters, order="started_at.desc", limit=limit)
    return attempts


async def get_forensic_attempt_detail(attempt_id: str) -> Dict[str, Any]:
    store = get_forensics_store()
    attempts = await store.select_rows("forensic_attempts", filters={"attempt_id": attempt_id}, limit=1)
    attempt = attempts[0] if attempts else None
    if not attempt:
        return {}
    events = await store.select_rows("forensic_events", filters={"attempt_id": attempt_id}, order="ordinal.asc", limit=1000)
    artifacts = await store.select_rows("forensic_artifacts", filters={"attempt_id": attempt_id}, order="captured_at.asc", limit=1000)
    verdict_rows = await store.select_rows("forensic_verdicts", filters={"attempt_id": attempt_id}, limit=1)
    links = await store.select_rows("forensic_links", filters={"parent_attempt_id": attempt_id}, order="created_at.asc", limit=1000)
    reverse_links = await store.select_rows("forensic_links", filters={"child_attempt_id": attempt_id}, order="created_at.asc", limit=1000)
    for artifact in artifacts:
        artifact["download_url"] = f"/forensics/artifacts/{artifact['artifact_id']}"
    return {
        "attempt": attempt,
        "events": events,
        "artifacts": artifacts,
        "verdict": verdict_rows[0] if verdict_rows else None,
        "links": {"children": links, "parents": reverse_links},
    }


async def get_forensic_artifact_by_id(artifact_id: str) -> Optional[Dict[str, Any]]:
    store = get_forensics_store()
    rows = await store.select_rows("forensic_artifacts", filters={"artifact_id": artifact_id}, limit=1)
    return rows[0] if rows else None


async def download_forensic_artifact_bytes(artifact_id: str) -> Optional[httpx.Response]:
    artifact = await get_forensic_artifact_by_id(artifact_id)
    if not artifact:
        return None
    store = get_forensics_store()
    return await store.download_artifact(artifact["storage_path"])


async def build_forensic_group(filters: Dict[str, Any], *, limit: int = 200) -> Dict[str, Any]:
    attempts = await list_forensic_attempts(filters=filters, limit=limit)
    verdict_counts: Dict[str, int] = {}
    for attempt in attempts:
        verdict = attempt.get("final_verdict") or "unknown"
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
    return {
        "attempt_count": len(attempts),
        "verdict_counts": verdict_counts,
        "attempts": attempts,
    }
