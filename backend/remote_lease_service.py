"""
Remote lease service for interactive browser control.

Replaces the singleton browser session model with per-profile leases that:
- reserve profiles through profile_manager
- keep browser lifetime separate from viewer lifetime
- support one controller plus observers
- persist lease logs for post-mortem debugging
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Tuple

from fastapi import WebSocket
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from browser_factory import apply_page_identity_overrides, create_browser_context
from config import DEFAULT_USER_AGENT, MOBILE_VIEWPORT, REDDIT_MOBILE_USER_AGENT
from fb_session import FacebookSession, apply_session_to_context
from profile_manager import get_profile_manager
from proxy_manager import get_system_proxy
from reddit_session import RedditSession
from safe_io import atomic_write_json, safe_read_json

logger = logging.getLogger("RemoteLeaseService")

RemotePlatform = Literal["facebook", "reddit"]
RemoteRole = Literal["controller", "observer"]

VIEWERLESS_READY_CLOSE_SECONDS = int(os.getenv("REMOTE_VIEWERLESS_READY_CLOSE_SECONDS", "15"))
MAX_ACTIVE_LEASES = int(os.getenv("REMOTE_MAX_ACTIVE_LEASES", "2"))
REMOTE_FRAME_IDLE_INTERVAL_SECONDS = float(os.getenv("REMOTE_FRAME_IDLE_INTERVAL_SECONDS", "0.10"))
REMOTE_FRAME_ACTIVE_INTERVAL_SECONDS = float(os.getenv("REMOTE_FRAME_ACTIVE_INTERVAL_SECONDS", "0.033"))
REMOTE_FRAME_ACTIVE_BURST_SECONDS = float(os.getenv("REMOTE_FRAME_ACTIVE_BURST_SECONDS", "0.5"))
REMOTE_FRAME_SEND_STALE_SECONDS = float(os.getenv("REMOTE_FRAME_SEND_STALE_SECONDS", "2.5"))
REMOTE_FRAME_CAPTURE_TIMEOUT_SECONDS = float(os.getenv("REMOTE_FRAME_CAPTURE_TIMEOUT_SECONDS", "10"))
REMOTE_STARTUP_NAVIGATION_TIMEOUT_SECONDS = float(os.getenv("REMOTE_STARTUP_NAVIGATION_TIMEOUT_SECONDS", "8"))
REMOTE_STARTUP_RENDER_TIMEOUT_SECONDS = float(os.getenv("REMOTE_STARTUP_RENDER_TIMEOUT_SECONDS", "6"))
REMOTE_RENDERABLE_MIN_HTML_LENGTH = int(os.getenv("REMOTE_RENDERABLE_MIN_HTML_LENGTH", "2048"))


class RemoteLeaseError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        code: str = "remote_lease_error",
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.details = details or {}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat().replace("+00:00", "Z")


def _normalize_profile_name(value: str) -> str:
    return str(value or "").replace(" ", "_").replace("/", "_").lower()


def _default_remote_leases_dir() -> Path:
    data_dir = os.getenv("DATA_DIR")
    if data_dir:
        return Path(data_dir) / "remote_leases"
    if Path("/data").exists():
        return Path("/data/remote_leases")
    return Path(__file__).parent / "remote_leases"


REMOTE_LEASES_DIR = _default_remote_leases_dir()
REMOTE_LEASES_DIR.mkdir(parents=True, exist_ok=True)

UPLOAD_DIR = Path("/tmp/commentbot_uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class RemoteSessionSpec:
    platform: RemotePlatform
    session_id: str
    user_agent: str
    viewport: Dict[str, int]
    timezone_id: str
    locale: str
    proxy_url: str
    proxy_source: Literal["session", "env"]
    start_url: str
    fallback_proxy_url: Optional[str] = None
    fallback_proxy_source: Optional[Literal["session", "env"]] = None
    wait_until: str = "domcontentloaded"
    fallback_start_urls: List[str] = field(default_factory=list)
    storage_state: Optional[Dict[str, Any]] = None
    apply_context: Optional[Callable[[BrowserContext], Awaitable[bool]]] = None
    is_mobile: Optional[bool] = None
    has_touch: Optional[bool] = None


@dataclass
class RemoteViewer:
    websocket: WebSocket
    username: str
    role: RemoteRole
    attached_at: str = field(default_factory=_iso_now)
    last_frame_at: Optional[str] = None
    last_frame_at_ts: float = 0.0
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send_json(self, payload: Dict[str, Any]) -> bool:
        async with self.send_lock:
            try:
                await self.websocket.send_json(payload)
                return True
            except Exception:
                return False

    async def send_text(self, payload: str) -> bool:
        async with self.send_lock:
            try:
                await self.websocket.send_text(payload)
                return True
            except Exception:
                return False


def _reddit_session_has_persisted_auth(session: RedditSession) -> bool:
    storage_state = dict(session.get_storage_state() or {})
    stored_cookies = list(storage_state.get("cookies") or [])
    direct_cookies = list(session.get_cookies() or [])
    return bool(stored_cookies or direct_cookies)


def _pick_remote_proxy(stored_proxy: Optional[str]) -> Optional[str]:
    return stored_proxy or get_system_proxy()


def _resolve_remote_proxy_plan(
    stored_proxy: Optional[str],
) -> Tuple[str, Literal["session", "env"], Optional[str], Optional[Literal["session", "env"]]]:
    session_proxy = str(stored_proxy or "").strip()
    env_proxy = str(get_system_proxy() or "").strip()

    if session_proxy:
        fallback_proxy = env_proxy if env_proxy and env_proxy != session_proxy else None
        return session_proxy, "session", fallback_proxy, ("env" if fallback_proxy else None)

    if env_proxy:
        return env_proxy, "env", None, None

    return "", "env", None, None


def _facebook_remote_start_urls(session: FacebookSession) -> Tuple[str, List[str]]:
    candidates: List[str] = []
    seen: set[str] = set()
    user_id = session.get_user_id()

    def _add(url: str) -> None:
        value = str(url or "").strip()
        if not value or value in seen:
            return
        seen.add(value)
        candidates.append(value)

    _add("https://m.facebook.com/me/?v=timeline")
    _add("https://m.facebook.com/me/")
    _add("https://www.facebook.com/")
    _add("https://m.facebook.com/")
    if user_id:
        _add(f"https://m.facebook.com/profile.php?id={user_id}&v=timeline")
        _add(f"https://m.facebook.com/profile.php?id={user_id}")
        _add(f"https://www.facebook.com/profile.php?id={user_id}")

    if not candidates:
        candidates.append("https://m.facebook.com/")

    return candidates[0], candidates[1:]


def _resolve_remote_session_spec(session_id: str, platform: RemotePlatform) -> RemoteSessionSpec:
    if platform == "facebook":
        session = FacebookSession(session_id)
        if not session.load():
            raise RuntimeError(f"session '{session_id}' not found")
        if not session.has_valid_cookies():
            raise RuntimeError("session has invalid cookies")

        stored_proxy = session.get_proxy()
        proxy_url, proxy_source, fallback_proxy_url, fallback_proxy_source = _resolve_remote_proxy_plan(stored_proxy)
        if not proxy_url:
            raise RuntimeError("no proxy available. configure PROXY_URL or persist a session proxy.")

        fingerprint = session.get_device_fingerprint()
        start_url, fallback_start_urls = _facebook_remote_start_urls(session)

        async def _apply_facebook(context: BrowserContext) -> bool:
            return await apply_session_to_context(context, session)

        return RemoteSessionSpec(
            platform="facebook",
            session_id=session_id,
            user_agent=session.get_user_agent() or DEFAULT_USER_AGENT,
            viewport=session.get_viewport() or MOBILE_VIEWPORT,
            timezone_id=fingerprint["timezone"],
            locale=fingerprint["locale"],
            proxy_url=proxy_url,
            proxy_source=proxy_source,
            fallback_proxy_url=fallback_proxy_url,
            fallback_proxy_source=fallback_proxy_source,
            start_url=start_url,
            wait_until="commit",
            fallback_start_urls=fallback_start_urls,
            apply_context=_apply_facebook,
        )

    session = RedditSession(session_id)
    if not session.load():
        raise RuntimeError(f"reddit session '{session_id}' not found")
    if not _reddit_session_has_persisted_auth(session):
        raise RuntimeError("reddit session has no persisted auth state")

    stored_proxy = session.get_proxy()
    proxy_url, proxy_source, fallback_proxy_url, fallback_proxy_source = _resolve_remote_proxy_plan(stored_proxy)
    if not proxy_url:
        raise RuntimeError("no proxy available. configure PROXY_URL or persist a session proxy.")

    fingerprint = session.get_device_fingerprint()
    storage_state = session.get_storage_state() or None
    direct_cookies = list(session.get_cookies() or [])

    async def _apply_reddit(context: BrowserContext) -> bool:
        if direct_cookies and not storage_state:
            try:
                await context.add_cookies(direct_cookies)
            except Exception as exc:
                logger.warning(f"failed to apply reddit cookies for remote session {session_id}: {exc}")
                return False
        return True

    return RemoteSessionSpec(
        platform="reddit",
        session_id=session_id,
        user_agent=session.get_user_agent() or REDDIT_MOBILE_USER_AGENT,
        viewport=session.get_viewport() or MOBILE_VIEWPORT,
        timezone_id=fingerprint["timezone"],
        locale=fingerprint["locale"],
        proxy_url=proxy_url,
        proxy_source=proxy_source,
        fallback_proxy_url=fallback_proxy_url,
        fallback_proxy_source=fallback_proxy_source,
        start_url=session.get_profile_url() or "https://www.reddit.com/",
        wait_until="domcontentloaded",
        storage_state=storage_state,
        apply_context=_apply_reddit,
        is_mobile=True,
        has_touch=True,
    )


class RemoteLease:
    def __init__(
        self,
        *,
        service: "RemoteLeaseService",
        lease_id: str,
        session_id: str,
        platform: RemotePlatform,
        controller_user: str,
    ):
        self.service = service
        self.lease_id = lease_id
        self.session_id = session_id
        self.session_key = _normalize_profile_name(session_id)
        self.platform = platform
        self.controller_user = controller_user
        self.created_at = _iso_now()
        self.updated_at = self.created_at
        self.closed_at: Optional[str] = None
        self.status = "created"
        self.last_error: Optional[str] = None
        self.current_url: Optional[str] = None
        self.current_title: Optional[str] = None
        self.last_frame_at: Optional[str] = None
        self.browser_started_at: Optional[str] = None
        self.browser_restart_count = 0
        self.pending_upload: Optional[Dict[str, Any]] = None
        self.latest_frame: Optional[bytes] = None
        self.latest_frame_format = "jpeg"
        self.latest_frame_bootstrap = False
        self.latest_state_revision = 0
        self.pointer_x = MOBILE_VIEWPORT["width"] // 2
        self.pointer_y = MOBILE_VIEWPORT["height"] // 2
        self.viewer_count = 0
        self.active_proxy_source: Optional[str] = None

        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._cdp: Any = None
        self._expected_page_close = False
        self._frame_stream_task: Optional[asyncio.Task] = None
        self._frame_stream_state = "stopped"
        self._last_frame_hash: Optional[str] = None
        self._startup_task: Optional[asyncio.Task] = None
        self._startup_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._viewers: Dict[WebSocket, RemoteViewer] = {}
        self._action_queue: asyncio.Queue = asyncio.Queue()
        self._action_worker_task = asyncio.create_task(self._action_worker())
        self._idle_close_task: Optional[asyncio.Task] = None
        self._last_action_at: Optional[str] = None
        self._last_action_at_ts = 0.0
        self._last_page_health: Dict[str, Any] = {}
        self._meta_file = REMOTE_LEASES_DIR / self.lease_id / "meta.json"
        self._events_file = REMOTE_LEASES_DIR / self.lease_id / "events.jsonl"
        self._events: List[Dict[str, Any]] = []
        self._event_cap = 500
        self._meta_file.parent.mkdir(parents=True, exist_ok=True)
        self._persist_meta()

    @property
    def active(self) -> bool:
        return self._page is not None and not self._page.is_closed() and self.status not in {"closed", "failed"}

    @property
    def has_viewers(self) -> bool:
        return bool(self._viewers)

    @property
    def viewport(self) -> Dict[str, int]:
        if self._page and self._page.viewport_size:
            return dict(self._page.viewport_size)
        return dict(MOBILE_VIEWPORT)

    def summary(self) -> Dict[str, Any]:
        return {
            "lease_id": self.lease_id,
            "session_id": self.session_id,
            "platform": self.platform,
            "controller_user": self.controller_user,
            "viewer_count": len(self._viewers),
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "closed_at": self.closed_at,
            "url": self.current_url,
            "title": self.current_title,
            "last_frame_at": self.last_frame_at,
            "last_action_at": self._last_action_at,
            "browser_started_at": self.browser_started_at,
            "browser_restart_count": self.browser_restart_count,
            "last_error": self.last_error,
            "viewport": self.viewport,
            "frame_stream_state": self._frame_stream_state,
            "reservation_source": "remote_lease",
            "reservation_owner": self.lease_id,
            "active": self.active,
        }

    def get_role_for(self, username: str) -> RemoteRole:
        return "controller" if username == self.controller_user else "observer"

    def _persist_meta(self) -> None:
        payload = self.summary()
        payload["latest_state_revision"] = self.latest_state_revision
        atomic_write_json(str(self._meta_file), payload)

    def _log_event(self, action: str, details: Optional[Dict[str, Any]] = None) -> None:
        entry = {
            "timestamp": _iso_now(),
            "action": action,
            "details": details or {},
            "lease_id": self.lease_id,
            "platform": self.platform,
            "session_id": self.session_id,
            "controller_user": self.controller_user,
            "url": self.current_url,
        }
        self._events.append(entry)
        if len(self._events) > self._event_cap:
            self._events = self._events[-self._event_cap :]
        with self._events_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=True) + "\n")
        self.updated_at = entry["timestamp"]
        self.latest_state_revision += 1
        self._persist_meta()
        logger.info(f"remote lease {self.lease_id}: {action} {json.dumps(details or {})}")

    async def _send_json_to_viewer(
        self,
        viewer: RemoteViewer,
        payload: Dict[str, Any],
        *,
        detach_on_failure: bool,
    ) -> bool:
        ok = await viewer.send_json(payload)
        if not ok and detach_on_failure and viewer.websocket in self._viewers:
            await self.detach_viewer(viewer.websocket)
        return ok

    async def _send_text_to_viewer(
        self,
        viewer: RemoteViewer,
        payload: str,
        *,
        detach_on_failure: bool,
    ) -> bool:
        ok = await viewer.send_text(payload)
        if not ok and detach_on_failure and viewer.websocket in self._viewers:
            await self.detach_viewer(viewer.websocket)
        return ok

    async def _safe_broadcast_json(
        self,
        payload_factory: Callable[[RemoteViewer], Dict[str, Any]],
    ) -> None:
        disconnected: List[RemoteViewer] = []
        for viewer in list(self._viewers.values()):
            ok = await self._send_json_to_viewer(
                viewer,
                payload_factory(viewer),
                detach_on_failure=False,
            )
            if not ok:
                disconnected.append(viewer)
        for viewer in disconnected:
            await self.detach_viewer(viewer.websocket)

    async def _broadcast_event(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> None:
        await self._safe_broadcast_json(
            lambda _viewer: {
                "type": event_type,
                "data": {
                    **(data or {}),
                    "timestamp": _iso_now(),
                },
            }
        )

    async def _broadcast_state(self) -> None:
        base = self.summary()

        async def _send(viewer: RemoteViewer) -> Dict[str, Any]:
            return {
                "type": "state",
                "data": {
                    **base,
                    "role": viewer.role,
                    "can_control": viewer.role == "controller",
                    "timestamp": _iso_now(),
                },
            }

        disconnected: List[RemoteViewer] = []
        for viewer in list(self._viewers.values()):
            ok = await self._send_json_to_viewer(
                viewer,
                await _send(viewer),
                detach_on_failure=False,
            )
            if not ok:
                disconnected.append(viewer)
        for viewer in disconnected:
            await self.detach_viewer(viewer.websocket)

    async def _send_browser_ready(self, viewer: RemoteViewer) -> bool:
        return await self._send_json_to_viewer(
            viewer,
            {
                "type": "browser_ready",
                "data": {
                    "lease_id": self.lease_id,
                    "session_id": self.session_id,
                    "platform": self.platform,
                    "role": viewer.role,
                    "timestamp": _iso_now(),
                },
            },
            detach_on_failure=True,
        )

    async def _send_role_event(self, viewer: RemoteViewer) -> bool:
        return await self._send_json_to_viewer(
            viewer,
            {
                "type": "lease_role",
                "data": {
                    "lease_id": self.lease_id,
                    "role": viewer.role,
                    "controller_user": self.controller_user,
                    "can_control": viewer.role == "controller",
                    "timestamp": _iso_now(),
                },
            },
            detach_on_failure=True,
        )

    async def _notify_and_close_viewers(self, *, reason: str) -> None:
        payload = {
            "type": "session_closed",
            "data": {
                "lease_id": self.lease_id,
                "session_id": self.session_id,
                "platform": self.platform,
                "reason": reason,
                "timestamp": _iso_now(),
            },
        }
        viewers = list(self._viewers.values())
        self._viewers = {}
        self.viewer_count = 0
        for viewer in viewers:
            try:
                await viewer.send_json(payload)
            except Exception:
                pass
            try:
                await viewer.websocket.close(code=1000, reason=reason)
            except Exception:
                pass

    async def _page_health_snapshot(self) -> Dict[str, Any]:
        if not self._page or self._page.is_closed():
            return {}
        try:
            health = await self._page.evaluate(
                """() => ({
                    readyState: document.readyState,
                    visibilityState: document.visibilityState,
                    bodyTextLength: (document.body?.innerText || '').trim().length,
                    htmlLength: document.documentElement?.outerHTML?.length || 0,
                })"""
            )
        except Exception as exc:
            health = {"error": str(exc)}
        if not isinstance(health, dict):
            health = {}
        health["title"] = self.current_title or ""
        health["url"] = self.current_url or ""
        self._last_page_health = health
        return health

    def _page_has_renderable_document(self, health: Dict[str, Any]) -> bool:
        html_length = int(health.get("htmlLength") or 0)
        body_text_length = int(health.get("bodyTextLength") or 0)
        title = str(health.get("title") or "").strip()
        ready_state = str(health.get("readyState") or "")
        return (
            body_text_length > 0
            or bool(title)
            or (ready_state in {"interactive", "complete"} and html_length >= REMOTE_RENDERABLE_MIN_HTML_LENGTH)
        )

    async def _wait_for_renderable_document(
        self,
        *,
        timeout_seconds: float = REMOTE_STARTUP_RENDER_TIMEOUT_SECONDS,
    ) -> Dict[str, Any]:
        deadline = time.monotonic() + max(0.5, timeout_seconds)
        last_health: Dict[str, Any] = {}
        while time.monotonic() < deadline:
            if self.closed_at:
                return last_health
            self._refresh_page_state()
            await self.refresh_title()
            last_health = await self._page_health_snapshot()
            if self._page_has_renderable_document(last_health):
                return last_health
            await asyncio.sleep(0.5)
        return last_health

    async def _navigate_initial_page(self, session_spec: RemoteSessionSpec, *, reason: str) -> Dict[str, Any]:
        assert self._page is not None
        candidates: List[str] = []
        seen: set[str] = set()
        for url in [session_spec.start_url, *session_spec.fallback_start_urls]:
            normalized = str(url or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            candidates.append(normalized)

        last_error = ""
        last_health: Dict[str, Any] = {}
        nav_timeout_ms = max(3000, int(REMOTE_STARTUP_NAVIGATION_TIMEOUT_SECONDS * 1000))
        for index, candidate in enumerate(candidates, start=1):
            if self.closed_at:
                raise asyncio.CancelledError()
            try:
                await self._page.goto(candidate, wait_until=session_spec.wait_until, timeout=nav_timeout_ms)
            except Exception as exc:
                last_error = str(exc)
                self._log_event(
                    "browser_start_url_failed",
                    {"reason": reason, "url": candidate, "attempt": index, "error": str(exc)},
                )
                continue

            health = await self._wait_for_renderable_document()
            if self._page_has_renderable_document(health):
                return {"url": candidate, "attempt": index, "page_health": health}

            last_health = health
            self._log_event(
                "browser_start_url_empty",
                {"reason": reason, "url": candidate, "attempt": index, "page_health": health},
            )

        raise RuntimeError(
            f"failed to load a renderable {self.platform} start page"
            + (f": {last_error}" if last_error else "")
            + (f" health={json.dumps(last_health, ensure_ascii=True)}" if last_health else "")
        )

    async def _capture_frame_bytes(self) -> Optional[bytes]:
        if not self._page or self._page.is_closed():
            return None
        try:
            frame = await asyncio.wait_for(
                self._page.screenshot(
                    type=self.latest_frame_format,
                    quality=70,
                    scale="css",
                ),
                timeout=REMOTE_FRAME_CAPTURE_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.warning(f"remote lease {self.lease_id}: frame capture failed: {exc}")
            return None
        self.latest_frame = frame
        self.latest_frame_bootstrap = False
        self.last_frame_at = _iso_now()
        return frame

    def _build_frame_message(self, frame: bytes, *, bootstrap: bool) -> str:
        return json.dumps(
            {
                "type": "frame",
                "data": {
                    "image": base64.b64encode(frame).decode("utf-8"),
                    "width": self.viewport["width"],
                    "height": self.viewport["height"],
                    "format": self.latest_frame_format,
                    "bootstrap": bootstrap,
                    "timestamp": _iso_now(),
                },
            }
        )

    def _viewer_missing_recent_frame(self, viewer: RemoteViewer, *, within_seconds: float) -> bool:
        if not viewer.last_frame_at_ts:
            return True
        return (time.time() - float(viewer.last_frame_at_ts)) > float(within_seconds)

    def _any_viewer_missing_recent_frame(self, *, within_seconds: float) -> bool:
        return any(self._viewer_missing_recent_frame(viewer, within_seconds=within_seconds) for viewer in self._viewers.values())

    async def _broadcast_frame(self, frame: bytes, *, bootstrap: bool) -> None:
        disconnected: List[RemoteViewer] = []
        payload = self._build_frame_message(frame, bootstrap=bootstrap)
        for viewer in list(self._viewers.values()):
            ok = await self._send_text_to_viewer(
                viewer,
                payload,
                detach_on_failure=False,
            )
            if ok:
                viewer.last_frame_at = self.last_frame_at
                viewer.last_frame_at_ts = time.time()
            else:
                disconnected.append(viewer)
        for viewer in disconnected:
            await self.detach_viewer(viewer.websocket)

    async def _capture_bootstrap_frame(self) -> Optional[bytes]:
        if not self._page or self._page.is_closed():
            return None
        frame = await self._capture_frame_bytes()
        if frame is None:
            return None
        self.latest_frame = frame
        self.latest_frame_bootstrap = True
        return frame

    async def _start_frame_stream(self) -> None:
        if not self.has_viewers:
            return
        task = self._frame_stream_task
        if task and not task.done():
            return
        self._frame_stream_state = "starting"
        self._frame_stream_task = asyncio.create_task(self._frame_stream_loop())
        self._log_event("frame_stream_start", {"viewer_count": len(self._viewers)})

    async def _stop_frame_stream(self) -> None:
        task = self._frame_stream_task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._frame_stream_task = None
        if self._frame_stream_state != "stopped":
            self._log_event("frame_stream_stop", {"viewer_count": len(self._viewers)})
        self._frame_stream_state = "stopped"

    async def _frame_stream_loop(self) -> None:
        self._frame_stream_state = "running"
        consecutive_errors = 0
        try:
            while self.has_viewers and self.active:
                try:
                    interval = REMOTE_FRAME_IDLE_INTERVAL_SECONDS
                    if time.time() - self._last_action_at_ts < REMOTE_FRAME_ACTIVE_BURST_SECONDS:
                        interval = REMOTE_FRAME_ACTIVE_INTERVAL_SECONDS

                    frame = await self._capture_frame_bytes()
                    if frame is None:
                        raise RuntimeError("frame capture returned empty result")

                    consecutive_errors = 0
                    frame_hash = hashlib.md5(frame).hexdigest()[:8]
                    should_send = frame_hash != self._last_frame_hash
                    if should_send or self._any_viewer_missing_recent_frame(within_seconds=REMOTE_FRAME_SEND_STALE_SECONDS):
                        self._last_frame_hash = frame_hash
                        await self._broadcast_frame(frame, bootstrap=False)
                    await asyncio.sleep(interval)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    consecutive_errors += 1
                    self.last_error = str(exc)
                    self._log_event(
                        "frame_stream_capture_failed",
                        {"error": str(exc), "consecutive_errors": consecutive_errors},
                    )
                    if consecutive_errors >= 5:
                        self._frame_stream_state = "failed"
                        break
                    await asyncio.sleep(min(1.5, 0.25 * consecutive_errors))
        except asyncio.CancelledError:
            self._frame_stream_state = "stopped"
            raise
        finally:
            if self._frame_stream_state != "failed":
                self._frame_stream_state = "stopped"
            self._frame_stream_task = None

    async def add_viewer(self, websocket: WebSocket, username: str) -> RemoteViewer:
        role = self.get_role_for(username)
        viewer = RemoteViewer(websocket=websocket, username=username, role=role)
        self._viewers[websocket] = viewer
        self.viewer_count = len(self._viewers)
        self._cancel_idle_close()
        if not await self._send_role_event(viewer):
            raise RemoteLeaseError(
                "remote viewer disconnected during attach",
                status_code=410,
                code="remote_viewer_disconnected",
                details={"lease_id": self.lease_id, "session_id": self.session_id},
            )
        await self._broadcast_state()
        await self._start_frame_stream()
        if self.latest_frame:
            sent = await self._send_text_to_viewer(
                viewer,
                self._build_frame_message(self.latest_frame, bootstrap=True),
                detach_on_failure=True,
            )
            if not sent:
                raise RemoteLeaseError(
                    "remote viewer disconnected during attach",
                    status_code=410,
                    code="remote_viewer_disconnected",
                    details={"lease_id": self.lease_id, "session_id": self.session_id},
                )
        return viewer

    async def detach_viewer(self, websocket: WebSocket) -> None:
        viewer = self._viewers.pop(websocket, None)
        if viewer is None:
            return
        self.viewer_count = len(self._viewers)
        if not self._viewers:
            await self._stop_frame_stream()
            self._schedule_idle_close()
        await self._broadcast_state()

    def _cancel_idle_close(self) -> None:
        task = self._idle_close_task
        if task and not task.done():
            task.cancel()
        self._idle_close_task = None

    def _schedule_idle_close(self) -> None:
        if self._idle_close_task or self.closed_at:
            return
        self._idle_close_task = asyncio.create_task(self._idle_close_worker())

    async def _idle_close_worker(self) -> None:
        idle_seconds = max(1, VIEWERLESS_READY_CLOSE_SECONDS)
        try:
            await asyncio.sleep(idle_seconds)
        except asyncio.CancelledError:
            return
        if self._viewers:
            return
        self._log_event("session_idle_timeout_close", {"idle_seconds": idle_seconds})
        await self._broadcast_event(
            "session_idle_timeout_close",
            {
                "lease_id": self.lease_id,
                "session_id": self.session_id,
                "platform": self.platform,
                "idle_seconds": idle_seconds,
            },
        )
        await self.service.close_lease(self, reason="idle_timeout")

    async def ensure_browser_ready(self, *, reason: str) -> None:
        async with self._startup_lock:
            if self.closed_at:
                raise RemoteLeaseError(
                    "interactive session closed",
                    status_code=410,
                    code="remote_session_closed",
                    details={"lease_id": self.lease_id, "session_id": self.session_id, "platform": self.platform},
                )
            if self.active:
                self._refresh_page_state()
                await self.refresh_title()
                if self.has_viewers and (self._frame_stream_task is None or self._frame_stream_task.done()):
                    await self._start_frame_stream()
                return
            current_task = asyncio.current_task()
            self._startup_task = current_task
            try:
                await self._start_browser(reason=reason)
            except asyncio.CancelledError:
                self._log_event("browser_start_cancelled", {"reason": reason})
                raise
            finally:
                if self._startup_task is current_task:
                    self._startup_task = None

    async def restart_browser(self, *, reason: str) -> None:
        async with self._startup_lock:
            await self._teardown_browser(persist=True)
            await self._start_browser(reason=reason)
            await self.refresh_title()
            if self.latest_frame:
                await self._broadcast_frame(self.latest_frame, bootstrap=True)
            else:
                frame = await self._capture_bootstrap_frame()
                if frame:
                    await self._broadcast_frame(frame, bootstrap=True)
            await self._broadcast_state()

    async def _start_browser(self, *, reason: str) -> None:
        self.status = "starting"
        self.last_error = None
        await self._teardown_browser(persist=False)
        self._log_event("browser_start", {"reason": reason})

        session_spec = _resolve_remote_session_spec(self.session_id, self.platform)
        proxy_attempts: List[Tuple[str, Literal["session", "env"]]] = [(session_spec.proxy_url, session_spec.proxy_source)]
        if session_spec.fallback_proxy_url and session_spec.fallback_proxy_source:
            proxy_attempts.append((session_spec.fallback_proxy_url, session_spec.fallback_proxy_source))

        last_error = ""
        for proxy_attempt_index, (proxy_url, proxy_source) in enumerate(proxy_attempts, start=1):
            self.active_proxy_source = proxy_source
            self._log_event(
                "browser_proxy_attempt",
                {"reason": reason, "proxy_source": proxy_source, "proxy_attempt": proxy_attempt_index},
            )
            try:
                self._playwright = await async_playwright().start()
                self._browser, self._context = await create_browser_context(
                    self._playwright,
                    user_agent=session_spec.user_agent,
                    viewport=session_spec.viewport,
                    proxy_url=proxy_url,
                    timezone_id=session_spec.timezone_id,
                    locale=session_spec.locale,
                    headless=True,
                    storage_state=session_spec.storage_state,
                    is_mobile=session_spec.is_mobile,
                    has_touch=session_spec.has_touch,
                )
                if session_spec.apply_context:
                    applied = await session_spec.apply_context(self._context)
                    if not applied:
                        raise RuntimeError(f"failed to apply {self.platform} session state")

                self._page = await self._context.new_page()
                await apply_page_identity_overrides(
                    self._context,
                    self._page,
                    user_agent=session_spec.user_agent,
                    locale=session_spec.locale,
                )
                self._page.on("filechooser", lambda chooser: asyncio.create_task(self._handle_file_chooser(chooser)))
                self._page.on("close", lambda: asyncio.create_task(self._handle_page_closed("page_closed")))
                self._page.on("crash", lambda: asyncio.create_task(self._handle_page_closed("page_crashed")))
                navigation_result = await self._navigate_initial_page(session_spec, reason=reason)

                self._cdp = await self._context.new_cdp_session(self._page)
                await self._cdp.send("Page.enable")
                self.browser_started_at = _iso_now()
                self.browser_restart_count += 1
                self.status = "ready"
                self._refresh_page_state()
                await self.refresh_title()
                page_health = navigation_result.get("page_health") or await self._page_health_snapshot()
                self._persist_meta()
                if self.has_viewers:
                    await self._start_frame_stream()
                self._log_event(
                    "browser_ready",
                    {
                        "reason": reason,
                        "start_url": navigation_result.get("url") or session_spec.start_url,
                        "start_attempt": navigation_result.get("attempt"),
                        "proxy_source": proxy_source,
                        "proxy_attempt": proxy_attempt_index,
                        "page_health": page_health,
                    },
                )
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = str(exc)
                self.last_error = str(exc)
                self._log_event(
                    "browser_proxy_failed",
                    {
                        "reason": reason,
                        "proxy_source": proxy_source,
                        "proxy_attempt": proxy_attempt_index,
                        "error": str(exc),
                    },
                )
                await self._teardown_browser(persist=False)

        raise RuntimeError(last_error or f"failed to start {self.platform} remote browser")

    async def _handle_page_closed(self, reason: str) -> None:
        if self._expected_page_close or self.closed_at:
            return
        self.status = "failed"
        self.last_error = reason
        self._persist_meta()
        self._log_event(reason, {})
        await self._broadcast_event("error", {"message": f"browser {reason.replace('_', ' ')}"})
        try:
            await self.ensure_browser_ready(reason=reason)
            await self._broadcast_event(
                "stream_restarted",
                {
                    "lease_id": self.lease_id,
                    "session_id": self.session_id,
                    "platform": self.platform,
                    "reason": reason,
                    "url": self.current_url,
                },
            )
            await self._broadcast_state()
        except Exception as exc:
            self.status = "failed"
            self.last_error = str(exc)
            self._persist_meta()
            self._log_event("browser_restart_failed", {"reason": reason, "error": str(exc)})

    async def _handle_file_chooser(self, chooser: Any) -> None:
        upload = dict(self.pending_upload or {})
        upload_path = str(upload.get("path") or "")
        if upload_path and Path(upload_path).exists():
            await chooser.set_files(upload_path)
            self._log_event("file_upload", {"path": upload_path, "filename": upload.get("filename")})
            return
        await chooser.set_files([])
        self._log_event("file_upload_missing", {"path": upload_path})

    async def store_upload(self, *, filename: str, content_type: str, content: bytes) -> Dict[str, Any]:
        allowed_types = {"image/jpeg", "image/png", "image/webp"}
        if content_type not in allowed_types:
            raise ValueError(f"invalid file type. allowed: {', '.join(sorted(allowed_types))}")
        if len(content) > 10 * 1024 * 1024:
            raise ValueError("file too large. max size: 10mb")
        image_id = uuid.uuid4().hex[:8]
        ext = Path(filename).suffix or ".jpg"
        temp_path = UPLOAD_DIR / f"{image_id}{ext}"
        temp_path.write_bytes(content)
        self.pending_upload = {
            "image_id": image_id,
            "path": str(temp_path),
            "filename": filename,
            "size": len(content),
            "uploaded_at": _iso_now(),
            "expires_at": (_utc_now() + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
        }
        self._log_event("upload_stored", {"filename": filename, "image_id": image_id, "size": len(content)})
        return dict(self.pending_upload)

    def clear_upload(self) -> None:
        upload = dict(self.pending_upload or {})
        upload_path = str(upload.get("path") or "")
        if upload_path:
            try:
                Path(upload_path).unlink(missing_ok=True)
            except Exception as exc:
                logger.warning(f"failed to delete upload file {upload_path}: {exc}")
        self.pending_upload = None

    async def set_controller(self, username: str, *, takeover: bool) -> None:
        previous = self.controller_user
        self.controller_user = username
        for viewer in list(self._viewers.values()):
            viewer.role = self.get_role_for(viewer.username)
            await self._send_role_event(viewer)
        self._log_event(
            "lease_takeover" if takeover else "lease_controller_refresh",
            {"previous_controller_user": previous, "controller_user": username},
        )
        await self._broadcast_state()

    async def enqueue_action(
        self,
        *,
        viewer: RemoteViewer,
        action_type: str,
        action_data: Dict[str, Any],
        action_id: str,
    ) -> None:
        await self._action_queue.put((viewer, action_type, dict(action_data or {}), action_id))

    async def _send_action_result(
        self,
        viewer: RemoteViewer,
        *,
        action_id: str,
        result: Dict[str, Any],
    ) -> bool:
        return await self._send_json_to_viewer(
            viewer,
            {"type": "action_result", "data": {"action_id": action_id, **result}},
            detach_on_failure=True,
        )

    async def _action_worker(self) -> None:
        while True:
            viewer: Optional[RemoteViewer] = None
            action_id = ""
            try:
                viewer, action_type, action_data, action_id = await self._action_queue.get()
                result = await self._execute_action(viewer=viewer, action_type=action_type, action_data=action_data)
                await self._send_action_result(viewer, action_id=action_id, result=result)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                if viewer is not None:
                    await self._send_action_result(
                        viewer,
                        action_id=action_id,
                        result={"success": False, "error": str(exc)},
                    )

    async def _execute_action(
        self,
        *,
        viewer: RemoteViewer,
        action_type: str,
        action_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        if viewer.role != "controller":
            return {"success": False, "error": "observer cannot control active lease"}
        await self.ensure_browser_ready(reason=f"action:{action_type}")
        self._last_action_at = _iso_now()
        self._last_action_at_ts = time.time()

        if action_type == "tap":
            x = int(action_data.get("x", self.pointer_x))
            y = int(action_data.get("y", self.pointer_y))
            await self._dispatch_touch_tap(x, y)
            self.pointer_x, self.pointer_y = x, y
            self._log_event("tap", {"x": x, "y": y, "user": viewer.username})
            await self._broadcast_state()
            return {"success": True, "action": "tap", "x": x, "y": y}

        if action_type == "pointer_move":
            x = int(action_data.get("x", self.pointer_x))
            y = int(action_data.get("y", self.pointer_y))
            await self._dispatch_mouse_event("mouseMoved", x=x, y=y, buttons=1)
            self.pointer_x, self.pointer_y = x, y
            return {"success": True, "action": "pointer_move", "x": x, "y": y}

        if action_type == "pointer_down":
            x = int(action_data.get("x", self.pointer_x))
            y = int(action_data.get("y", self.pointer_y))
            await self._dispatch_mouse_event("mousePressed", x=x, y=y, button="left", buttons=1, click_count=1)
            self.pointer_x, self.pointer_y = x, y
            return {"success": True, "action": "pointer_down", "x": x, "y": y}

        if action_type == "pointer_up":
            x = int(action_data.get("x", self.pointer_x))
            y = int(action_data.get("y", self.pointer_y))
            await self._dispatch_mouse_event("mouseReleased", x=x, y=y, button="left", buttons=0, click_count=1)
            self.pointer_x, self.pointer_y = x, y
            return {"success": True, "action": "pointer_up", "x": x, "y": y}

        if action_type == "drag":
            start_x = int(action_data.get("startX", self.pointer_x))
            start_y = int(action_data.get("startY", self.pointer_y))
            end_x = int(action_data.get("endX", start_x))
            end_y = int(action_data.get("endY", start_y))
            await self._perform_drag(start_x=start_x, start_y=start_y, end_x=end_x, end_y=end_y)
            self.pointer_x, self.pointer_y = end_x, end_y
            self._log_event("drag", {"start_x": start_x, "start_y": start_y, "end_x": end_x, "end_y": end_y, "user": viewer.username})
            return {"success": True, "action": "drag"}

        if action_type == "scroll_gesture":
            x = int(action_data.get("x", self.pointer_x))
            y = int(action_data.get("y", self.pointer_y))
            delta_y = int(action_data.get("deltaY", 0))
            await self._perform_scroll(x=x, y=y, delta_y=delta_y)
            self.pointer_x, self.pointer_y = x, y
            self._log_event("scroll_gesture", {"x": x, "y": y, "delta_y": delta_y, "user": viewer.username})
            return {"success": True, "action": "scroll_gesture", "delta_y": delta_y}

        if action_type == "text_input":
            text = str(action_data.get("text") or "")
            await self._insert_text(text)
            self._log_event("text_input", {"length": len(text), "user": viewer.username})
            return {"success": True, "action": "text_input", "length": len(text)}

        if action_type == "paste_text":
            text = str(action_data.get("text") or "")
            await self._insert_text(text)
            self._log_event("paste_text", {"length": len(text), "user": viewer.username})
            return {"success": True, "action": "paste_text", "length": len(text)}

        if action_type == "key_down":
            key = str(action_data.get("key") or "")
            await self._page.keyboard.down(key)
            self._log_event("key_down", {"key": key, "user": viewer.username})
            return {"success": True, "action": "key_down", "key": key}

        if action_type == "key_up":
            key = str(action_data.get("key") or "")
            await self._page.keyboard.up(key)
            self._log_event("key_up", {"key": key, "user": viewer.username})
            return {"success": True, "action": "key_up", "key": key}

        if action_type == "navigate":
            url = str(action_data.get("url") or "").strip()
            if not url.startswith("http://") and not url.startswith("https://"):
                url = "https://" + url
            await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            self._refresh_page_state()
            await self.refresh_title()
            self._log_event("navigate", {"url": url, "user": viewer.username, "page_health": await self._wait_for_renderable_document()})
            await self._broadcast_state()
            return {"success": True, "action": "navigate", "url": self.current_url}

        raise RuntimeError(f"unsupported action: {action_type}")

    async def _dispatch_touch_tap(self, x: int, y: int) -> None:
        if not self._cdp:
            raise RuntimeError("cdp session unavailable")
        await self._cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y, "radiusX": 1, "radiusY": 1}]})
        await self._cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})

    async def _dispatch_mouse_event(
        self,
        event_type: str,
        *,
        x: int,
        y: int,
        button: str = "none",
        buttons: int = 0,
        click_count: int = 0,
        delta_x: int = 0,
        delta_y: int = 0,
    ) -> None:
        if not self._cdp:
            raise RuntimeError("cdp session unavailable")
        payload: Dict[str, Any] = {
            "type": event_type,
            "x": x,
            "y": y,
            "button": button,
            "buttons": buttons,
            "clickCount": click_count,
        }
        if event_type == "mouseWheel":
            payload["deltaX"] = delta_x
            payload["deltaY"] = delta_y
        await self._cdp.send("Input.dispatchMouseEvent", payload)

    async def _perform_drag(self, *, start_x: int, start_y: int, end_x: int, end_y: int) -> None:
        await self._dispatch_mouse_event("mouseMoved", x=start_x, y=start_y, buttons=0)
        await self._dispatch_mouse_event("mousePressed", x=start_x, y=start_y, button="left", buttons=1, click_count=1)
        steps = 8
        for index in range(1, steps + 1):
            x = round(start_x + (end_x - start_x) * index / steps)
            y = round(start_y + (end_y - start_y) * index / steps)
            await self._dispatch_mouse_event("mouseMoved", x=x, y=y, buttons=1)
        await self._dispatch_mouse_event("mouseReleased", x=end_x, y=end_y, button="left", buttons=0, click_count=1)

    async def _perform_scroll(self, *, x: int, y: int, delta_y: int) -> None:
        assert self._page is not None
        handled = await self._page.evaluate(
            """({ x, y, deltaY }) => {
                const pointTarget = document.elementFromPoint(x, y);
                const canScroll = (node) => {
                  if (!node || !(node instanceof Element)) return false;
                  const style = window.getComputedStyle(node);
                  const overflowY = style.overflowY || '';
                  return /(auto|scroll|overlay)/.test(overflowY) && node.scrollHeight > node.clientHeight;
                };
                let current = pointTarget;
                while (current) {
                  if (canScroll(current)) {
                    current.scrollBy({ top: deltaY, behavior: 'auto' });
                    return 'element';
                  }
                  current = current.parentElement;
                }
                const target = document.scrollingElement || document.documentElement || document.body;
                if (!target) return 'none';
                target.scrollBy({ top: deltaY, behavior: 'auto' });
                return 'document';
            }""",
            {"x": x, "y": y, "deltaY": delta_y},
        )
        if handled == "none":
            await self._dispatch_mouse_event("mouseWheel", x=x, y=y, delta_y=delta_y)

    async def _insert_text(self, text: str) -> None:
        if not text:
            return
        if self._cdp:
            await self._cdp.send("Input.insertText", {"text": text})
            return
        assert self._page is not None
        await self._page.keyboard.insert_text(text)

    def _refresh_page_state(self) -> None:
        if not self._page or self._page.is_closed():
            self.current_url = None
            self.current_title = None
            return
        self.current_url = self._page.url

    async def refresh_title(self) -> None:
        if not self._page or self._page.is_closed():
            self.current_title = None
            return
        try:
            self.current_title = await self._page.title()
        except Exception:
            self.current_title = None

    async def _teardown_browser(self, *, persist: bool) -> None:
        self._expected_page_close = True
        try:
            await self._stop_frame_stream()
        except Exception:
            pass
        if persist:
            try:
                await self.persist_session_state()
            except Exception as exc:
                self._log_event("persist_session_state_failed", {"error": str(exc)})
        if self._browser:
            try:
                await self._browser.close()
            except Exception as exc:
                logger.warning(f"remote lease {self.lease_id}: browser close failed: {exc}")
        self._browser = None
        self._context = None
        self._page = None
        self._cdp = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as exc:
                logger.warning(f"remote lease {self.lease_id}: playwright stop failed: {exc}")
        self._playwright = None
        self._expected_page_close = False
        self._frame_stream_state = "stopped"
        self._last_frame_hash = None
        self.latest_frame = None
        self.last_frame_at = None
        self._last_page_health = {}
        self._refresh_page_state()

    async def persist_session_state(self) -> None:
        if not self._context or not self.active:
            return
        if self.platform == "facebook":
            session = FacebookSession(self.session_id)
            if not session.load():
                return
            data = dict(session.data or {})
            data["cookies"] = await self._context.cookies()
            data["user_agent"] = data.get("user_agent") or DEFAULT_USER_AGENT
            data["viewport"] = data.get("viewport") or self.viewport
            data["updated_at"] = _iso_now()
            session.data = data
            session.save()
            return

        session = RedditSession(self.session_id)
        if not session.load():
            return
        base = dict(session.data or {})
        assert self._page is not None
        await session.extract_from_context(
            self._context,
            self._page,
            username=base.get("username") or self.session_id,
            email=base.get("email"),
            profile_url=base.get("profile_url"),
            proxy=base.get("proxy"),
            tags=list(base.get("tags") or []),
            linked_credential_id=base.get("linked_credential_id"),
            display_name=base.get("display_name"),
            fixture=bool(base.get("fixture", False)),
            warmup_state=dict(base.get("warmup_state") or {}),
            device=dict(base.get("device") or {}),
            bootstrap_source_session_id=base.get("bootstrap_source_session_id"),
        )
        session.save()

    async def close(self, *, reason: str) -> None:
        async with self._close_lock:
            if self.closed_at:
                return
            self.closed_at = _iso_now()
            self.status = "closed"
            self._cancel_idle_close()
            self._log_event("lease_close", {"reason": reason})
            startup_task = self._startup_task
            if startup_task and startup_task is not asyncio.current_task() and not startup_task.done():
                startup_task.cancel()
            await self._notify_and_close_viewers(reason=reason)
            if startup_task and startup_task is not asyncio.current_task() and not startup_task.done():
                try:
                    await startup_task
                except asyncio.CancelledError:
                    pass
                except Exception as exc:
                    self._log_event("browser_start_cancel_wait_failed", {"reason": reason, "error": str(exc)})
            await self._teardown_browser(persist=True)
            self.clear_upload()
            if self._action_worker_task:
                self._action_worker_task.cancel()
                try:
                    await self._action_worker_task
                except asyncio.CancelledError:
                    pass
            self._persist_meta()


class RemoteLeaseService:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._leases_by_id: Dict[str, RemoteLease] = {}
        self._lease_by_profile: Dict[Tuple[RemotePlatform, str], str] = {}

    def _profile_key(self, session_id: str, platform: RemotePlatform) -> Tuple[RemotePlatform, str]:
        return platform, _normalize_profile_name(session_id)

    async def attach(
        self,
        *,
        websocket: WebSocket,
        session_id: str,
        platform: RemotePlatform,
        username: str,
    ) -> Tuple[RemoteLease, RemoteViewer]:
        lease = await self._get_or_create_lease(
            session_id=session_id,
            platform=platform,
            username=username,
        )
        try:
            await lease.ensure_browser_ready(reason="attach")
            await lease.refresh_title()
            viewer = await lease.add_viewer(websocket, username)
            await lease._broadcast_state()
            if not await lease._send_browser_ready(viewer):
                raise RemoteLeaseError(
                    "remote viewer disconnected during attach",
                    status_code=410,
                    code="remote_viewer_disconnected",
                    details={"lease_id": lease.lease_id, "session_id": session_id, "platform": platform},
                )
            if not lease.latest_frame:
                frame = await lease._capture_bootstrap_frame()
                if frame:
                    await lease._broadcast_frame(frame, bootstrap=True)
            return lease, viewer
        except asyncio.CancelledError as exc:
            raise RemoteLeaseError(
                "interactive session closed",
                status_code=410,
                code="remote_session_closed",
                details={"lease_id": lease.lease_id, "session_id": session_id, "platform": platform},
            ) from exc
        except Exception:
            if not lease.has_viewers:
                await self.close_lease(lease, reason="attach_failed")
            raise

    async def _get_or_create_lease(
        self,
        *,
        session_id: str,
        platform: RemotePlatform,
        username: str,
    ) -> RemoteLease:
        lease: Optional[RemoteLease] = None
        async with self._lock:
            key = self._profile_key(session_id, platform)
            existing_id = self._lease_by_profile.get(key)
            if existing_id:
                lease = self._leases_by_id.get(existing_id)
                if lease:
                    return lease
            if len(self._leases_by_id) >= MAX_ACTIVE_LEASES:
                summaries = [item.summary() for item in self._leases_by_id.values()]
                raise RemoteLeaseError(
                    f"remote capacity full: {len(self._leases_by_id)}/{MAX_ACTIVE_LEASES} active leases",
                    status_code=409,
                    code="remote_capacity_full",
                    details={"capacity_limit": MAX_ACTIVE_LEASES, "active_leases": summaries},
                )
            lease_id = uuid.uuid4().hex[:12]
            profile_manager = get_profile_manager()
            reserved = await profile_manager.reserve_profile(
                session_id,
                source="remote_lease",
                owner=lease_id,
                metadata={
                    "platform": platform,
                    "controller_user": username,
                    "lease_id": lease_id,
                },
            )
            if not reserved:
                reservation = profile_manager.get_reservation(session_id) or {}
                raise RemoteLeaseError(
                    f"profile busy: {session_id}",
                    status_code=409,
                    code="profile_busy",
                    details={"session_id": session_id, "platform": platform, "reservation": reservation},
                )
            lease = RemoteLease(
                service=self,
                lease_id=lease_id,
                session_id=session_id,
                platform=platform,
                controller_user=username,
            )
            self._leases_by_id[lease.lease_id] = lease
            self._lease_by_profile[key] = lease.lease_id
            return lease

    async def start_detached(
        self,
        *,
        session_id: str,
        platform: RemotePlatform,
        username: str,
    ) -> Dict[str, Any]:
        lease = await self._get_or_create_lease(session_id=session_id, platform=platform, username=username)
        try:
            await lease.ensure_browser_ready(reason="start")
            await lease.refresh_title()
            if not lease.has_viewers:
                lease._schedule_idle_close()
            return {"success": True, **lease.summary()}
        except Exception:
            if not lease.has_viewers:
                await self.close_lease(lease, reason="start_failed")
            raise

    async def detach(self, websocket: WebSocket) -> None:
        lease = self.get_lease_by_websocket(websocket)
        if lease:
            await lease.detach_viewer(websocket)

    def get_lease_by_websocket(self, websocket: WebSocket) -> Optional[RemoteLease]:
        for lease in self._leases_by_id.values():
            if websocket in lease._viewers:
                return lease
        return None

    def get_viewer(self, websocket: WebSocket) -> Optional[RemoteViewer]:
        lease = self.get_lease_by_websocket(websocket)
        if not lease:
            return None
        return lease._viewers.get(websocket)

    async def close_lease(self, lease: RemoteLease, *, reason: str) -> None:
        async with self._lock:
            existing = self._leases_by_id.get(lease.lease_id)
            if existing is None:
                return
            self._leases_by_id.pop(lease.lease_id, None)
            self._lease_by_profile.pop(self._profile_key(lease.session_id, lease.platform), None)
            await get_profile_manager().release_profile(
                lease.session_id,
                source="remote_lease",
                owner=lease.lease_id,
            )
        await lease.close(reason=reason)

    def active_leases(self) -> List[RemoteLease]:
        return list(self._leases_by_id.values())

    def find_active_lease(self, *, session_id: str, platform: RemotePlatform) -> Optional[RemoteLease]:
        existing_id = self._lease_by_profile.get(self._profile_key(session_id, platform))
        if not existing_id:
            return None
        return self._leases_by_id.get(existing_id)

    async def handle_takeover(self, *, lease: RemoteLease, username: str) -> Dict[str, Any]:
        await lease.set_controller(username, takeover=True)
        return {
            "success": True,
            "action": "takeover",
            "lease_id": lease.lease_id,
            "controller_user": lease.controller_user,
        }

    async def restart(self, *, session_id: str, platform: RemotePlatform, requested_by: str) -> Dict[str, Any]:
        lease = self.find_active_lease(session_id=session_id, platform=platform)
        if not lease:
            raise RemoteLeaseError(
                "interactive session not active",
                status_code=404,
                code="remote_session_not_found",
                details={"session_id": session_id, "platform": platform},
            )
        await lease.restart_browser(reason=f"manual_restart:{requested_by}")
        return {"success": True, **lease.summary()}

    async def stop(self, *, session_id: str, platform: RemotePlatform) -> Dict[str, Any]:
        lease = self.find_active_lease(session_id=session_id, platform=platform)
        if not lease:
            return {"success": False, "error": "session not active"}
        await self.close_lease(lease, reason="manual_stop")
        return {"success": True, "session_id": session_id, "platform": platform}

    async def store_upload(self, *, session_id: str, filename: str, content_type: str, content: bytes) -> Dict[str, Any]:
        lease = self.find_active_lease(session_id=session_id, platform="facebook")
        if not lease:
            raise RemoteLeaseError(
                "interactive session not found or not active",
                status_code=404,
                code="remote_session_not_found",
                details={"session_id": session_id, "platform": "facebook"},
            )
        return await lease.store_upload(filename=filename, content_type=content_type, content=content)

    def clear_upload(self, *, session_id: str) -> bool:
        lease = self.find_active_lease(session_id=session_id, platform="facebook")
        if not lease:
            return False
        lease.clear_upload()
        return True

    def get_pending_upload(self, *, session_id: str) -> Dict[str, Any]:
        lease = self.find_active_lease(session_id=session_id, platform="facebook")
        if not lease or not lease.pending_upload:
            return {"has_pending": False}
        return {"has_pending": True, **dict(lease.pending_upload)}

    def prepare_upload(self, *, session_id: str) -> Dict[str, Any]:
        lease = self.find_active_lease(session_id=session_id, platform="facebook")
        if not lease:
            raise RemoteLeaseError(
                "interactive session not found or not active",
                status_code=404,
                code="remote_session_not_found",
                details={"session_id": session_id, "platform": "facebook"},
            )
        if not lease.pending_upload:
            raise RemoteLeaseError(
                "no pending upload for this session",
                status_code=400,
                code="pending_upload_missing",
                details={"session_id": session_id},
            )
        lease._log_event("upload_prepared", {"filename": lease.pending_upload.get("filename")})
        upload = dict(lease.pending_upload)
        return {
            "success": True,
            "message": "file ready. click the upload button on the page to use it.",
            "image_id": upload.get("image_id"),
            "filename": upload.get("filename"),
            "size": upload.get("size"),
            "expires_at": upload.get("expires_at"),
        }

    def status_snapshot(self) -> Dict[str, Any]:
        leases = [lease.summary() for lease in self.active_leases()]
        primary = leases[0] if leases else {}
        return {
            "active": bool(leases),
            "active_leases": leases,
            "count": len(leases),
            "session_id": primary.get("session_id"),
            "platform": primary.get("platform"),
            "url": primary.get("url"),
            "title": primary.get("title"),
            "capacity_limit": MAX_ACTIVE_LEASES,
            "streaming_active": any(bool(lease.has_viewers and lease._frame_stream_state == "running") for lease in self.active_leases()),
        }

    def _read_events_from_dir(self, lease_dir: Path, limit: int) -> List[Dict[str, Any]]:
        events_file = lease_dir / "events.jsonl"
        if not events_file.exists():
            return []
        lines = events_file.read_text().splitlines()
        items: List[Dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                items.append(json.loads(line))
            except Exception:
                continue
        return items

    def get_logs(self, *, session_id: str, platform: RemotePlatform, limit: int) -> List[Dict[str, Any]]:
        lease = self.find_active_lease(session_id=session_id, platform=platform)
        if lease:
            return lease._events[-limit:]

        session_key = _normalize_profile_name(session_id)
        candidates: List[Tuple[datetime, Path]] = []
        for lease_dir in REMOTE_LEASES_DIR.iterdir():
            if not lease_dir.is_dir():
                continue
            meta = safe_read_json(str(lease_dir / "meta.json"), default={})
            if not meta:
                continue
            if meta.get("platform") != platform or _normalize_profile_name(meta.get("session_id") or "") != session_key:
                continue
            timestamp = str(meta.get("updated_at") or meta.get("created_at") or "")
            try:
                parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except Exception:
                parsed = datetime.fromtimestamp(0, tz=timezone.utc)
            candidates.append((parsed, lease_dir))
        if not candidates:
            return []
        candidates.sort(key=lambda item: item[0], reverse=True)
        return self._read_events_from_dir(candidates[0][1], limit)


_remote_lease_service: Optional[RemoteLeaseService] = None


def get_remote_lease_service() -> RemoteLeaseService:
    global _remote_lease_service
    if _remote_lease_service is None:
        _remote_lease_service = RemoteLeaseService()
    return _remote_lease_service
