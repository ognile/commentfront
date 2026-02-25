"""
Persistent Browser Manager for Interactive Session Control

Manages a single long-lived browser session for real-time remote control.
Streams JPEG frames via WebSocket and handles input events (click, keyboard, scroll).
"""

import asyncio
import hashlib
import logging
import os
import json
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Set
from pathlib import Path

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright
from playwright_stealth import Stealth
from urllib.parse import urlparse, unquote

from fb_session import FacebookSession, apply_session_to_context
from config import MOBILE_VIEWPORT, DEFAULT_USER_AGENT

logger = logging.getLogger("BrowserManager")

# Temp directory for uploaded images
UPLOAD_DIR = Path("/tmp/commentbot_uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

IDLE_CLOSE_SECONDS = int(os.getenv("REMOTE_IDLE_CLOSE_SECONDS", "300"))
STREAM_STALE_SECONDS = float(os.getenv("REMOTE_STREAM_STALE_SECONDS", "10"))
SINGLE_SCREENSHOT_TIMEOUT_SECONDS = float(os.getenv("REMOTE_SCREENSHOT_TIMEOUT_SECONDS", "3"))


def _build_playwright_proxy(proxy_url: str) -> Dict[str, str]:
    """Wrapper for backward compatibility — delegates to browser_factory."""
    from browser_factory import build_playwright_proxy
    return build_playwright_proxy(proxy_url) or {"server": proxy_url}


class PersistentBrowserManager:
    """
    Singleton manager for persistent browser sessions.
    Only one session can be active at a time.
    """

    _instance: Optional["PersistentBrowserManager"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._session_id: Optional[str] = None
        self._session: Optional[FacebookSession] = None

        self._streaming_task: Optional[asyncio.Task] = None
        self._subscribers: Set = set()  # WebSocket connections
        self._subscriber_last_frame_at: Dict[Any, float] = {}
        self._action_log: List[Dict] = []
        self._lock = asyncio.Lock()
        self._idle_close_task: Optional[asyncio.Task] = None

        # For file chooser interception
        self._pending_file: Optional[str] = None

        # Frame streaming state
        self._last_frame_hash: Optional[str] = None
        self._last_action_time: float = 0
        self._frame_count: int = 0
        self._latest_frame: Optional[bytes] = None
        self._stream_task_state: str = "stopped"
        self._last_frame_at_ts: Optional[float] = None
        self._last_subscriber_at_ts: Optional[float] = None
        self._idle_deadline_at_ts: Optional[float] = None

    @property
    def is_active(self) -> bool:
        """Check if a session is currently active."""
        return self._page is not None and not self._page.is_closed()

    @property
    def session_id(self) -> Optional[str]:
        """Get current session ID."""
        return self._session_id

    @property
    def current_url(self) -> Optional[str]:
        """Get current page URL."""
        if self._page and not self._page.is_closed():
            return self._page.url
        return None

    @staticmethod
    def _now_ts() -> float:
        import time

        return time.time()

    @staticmethod
    def _iso_from_ts(ts: Optional[float]) -> Optional[str]:
        if ts is None:
            return None
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")
        except Exception:
            return None

    def _streaming_active(self) -> bool:
        return bool(self._streaming_task and not self._streaming_task.done())

    def _stream_health_reason(self) -> Optional[str]:
        if not self.is_active:
            return "page_inactive"
        if not self._streaming_active():
            return "stream_task_inactive"
        if self._stream_task_state in ("failed", "stopped"):
            return f"stream_state_{self._stream_task_state}"
        if self._subscribers:
            if self._last_frame_at_ts is None:
                return "stream_no_frames"
            age = self._now_ts() - float(self._last_frame_at_ts)
            if age > STREAM_STALE_SECONDS:
                return f"stream_stale_{age:.1f}s"
        return None

    def _cancel_idle_close_timer(self) -> None:
        current = asyncio.current_task()
        if self._idle_close_task and not self._idle_close_task.done() and self._idle_close_task is not current:
            self._idle_close_task.cancel()
        self._idle_close_task = None
        self._idle_deadline_at_ts = None

    def _schedule_idle_close_timer(self) -> None:
        if self._subscribers:
            return
        if not self._session_id or not self.is_active:
            return
        self._cancel_idle_close_timer()
        deadline_ts = self._now_ts() + max(1, IDLE_CLOSE_SECONDS)
        self._idle_deadline_at_ts = deadline_ts
        self._idle_close_task = asyncio.create_task(self._idle_close_worker(deadline_ts))

    async def _idle_close_worker(self, deadline_ts: float) -> None:
        sleep_for = max(0.0, float(deadline_ts) - self._now_ts())
        try:
            await asyncio.sleep(sleep_for)
        except asyncio.CancelledError:
            return

        if self._subscribers:
            self._idle_deadline_at_ts = None
            return

        if not self._session_id or not self.is_active:
            self._idle_deadline_at_ts = None
            return

        payload = {
            "session_id": self._session_id,
            "idle_seconds": IDLE_CLOSE_SECONDS,
            "closed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        self._log_action("session_idle_timeout_close", payload)
        await self.broadcast_event("session_idle_timeout_close", payload)
        await self.close_session()

    async def start_session(self, session_id: str) -> Dict[str, Any]:
        """
        Launch browser with session's fingerprint.

        Args:
            session_id: Profile name to load session for

        Returns:
            Dict with success status and details
        """
        import time
        async with self._lock:
            # Close existing resources (active or stale) before opening target session.
            if self._page or self._context or self._browser or self._playwright:
                await self._cleanup()
            self._cancel_idle_close_timer()

            try:
                t_total = time.time()

                # Load session data
                t0 = time.time()
                session = FacebookSession(session_id)
                if not session.load():
                    return {"success": False, "error": f"Session '{session_id}' not found"}

                if not session.has_valid_cookies():
                    return {"success": False, "error": "Session has invalid cookies"}

                self._session = session
                self._session_id = session_id

                # Get fingerprint data
                user_agent = session.get_user_agent() or DEFAULT_USER_AGENT
                viewport = session.get_viewport() or MOBILE_VIEWPORT
                from proxy_manager import get_system_proxy
                proxy_url = get_system_proxy()
                if not proxy_url:
                    return {"success": False, "error": "No proxy available. Configure PROXY_URL or add a default proxy."}
                device_fingerprint = session.get_device_fingerprint()
                logger.info(f"[TIMING] Session loaded in {time.time()-t0:.2f}s")

                # Build context options (same as comment_bot.py)
                context_options = {
                    "user_agent": user_agent,
                    "viewport": viewport,
                    "ignore_https_errors": True,
                    "device_scale_factor": 1,  # Critical for coordinate accuracy
                    "timezone_id": device_fingerprint["timezone"],
                    "locale": device_fingerprint["locale"],
                }

                context_options["proxy"] = _build_playwright_proxy(proxy_url)
                logger.info(f"Using proxy: {proxy_url[:30]}...")

                # Launch playwright
                await self.broadcast_progress("launching_browser")
                t1 = time.time()
                self._playwright = await async_playwright().start()
                logger.info(f"[TIMING] Playwright started in {time.time()-t1:.2f}s")

                # Launch browser
                t2 = time.time()
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=["--disable-notifications", "--disable-geolocation"]
                )
                logger.info(f"[TIMING] Browser launched in {time.time()-t2:.2f}s")

                # Create context
                t3 = time.time()
                self._context = await self._browser.new_context(**context_options)
                logger.info(f"[TIMING] Context created in {time.time()-t3:.2f}s")

                # Apply stealth mode (MANDATORY for anti-detection)
                await self.broadcast_progress("applying_stealth")
                t4 = time.time()
                await Stealth().apply_stealth_async(self._context)
                logger.info(f"[TIMING] Stealth applied in {time.time()-t4:.2f}s")

                # Create page
                t5 = time.time()
                self._page = await self._context.new_page()

                # Apply session cookies
                await apply_session_to_context(self._context, session)
                logger.info(f"[TIMING] Page created + cookies in {time.time()-t5:.2f}s")

                # Set up file chooser interception
                self._page.on("filechooser", self._handle_file_chooser)

                # Set up page lifecycle listeners for crash/close detection
                self._page.on("close", lambda: asyncio.create_task(self._on_page_close()))
                self._page.on("crash", lambda: asyncio.create_task(self._on_page_crash()))

                # Navigate to Facebook with retry on timeout
                await self.broadcast_progress("navigating")
                t6 = time.time()
                try:
                    await self._page.goto("https://m.facebook.com/", wait_until="commit", timeout=30000)
                except Exception as nav_error:
                    logger.warning(f"[DEBUG] Navigation failed: {nav_error}, retrying...")
                    await self.broadcast_progress("retrying")
                    await asyncio.sleep(2)
                    await self._page.reload(wait_until="commit", timeout=30000)
                logger.info(f"[TIMING] Navigation completed in {time.time()-t6:.2f}s")

                # Start frame streaming
                self._stream_task_state = "starting"
                self._latest_frame = None
                self._last_frame_at_ts = None
                self._streaming_task = asyncio.create_task(self._streaming_loop())

                logger.info(f"[TIMING] Total session start: {time.time()-t_total:.2f}s")
                self._log_action("session_start", {"session_id": session_id})
                logger.info(f"Interactive session started for {session_id}")

                return {
                    "success": True,
                    "session_id": session_id,
                    "url": self._page.url,
                    "viewport": viewport
                }

            except Exception as e:
                logger.error(f"Failed to start session: {e}")
                await self._cleanup()
                return {"success": False, "error": str(e)}

    async def close_session(self) -> Dict[str, Any]:
        """Close the current session and cleanup."""
        async with self._lock:
            session_id = self._session_id
            await self._cleanup()
            logger.info(f"Interactive session closed for {session_id}")
            return {"success": True, "session_id": session_id}

    async def _cleanup(self):
        """Clean up all browser resources."""
        self._cancel_idle_close_timer()

        # Stop streaming
        if self._streaming_task:
            self._streaming_task.cancel()
            try:
                await self._streaming_task
            except asyncio.CancelledError:
                pass
            self._streaming_task = None
        self._stream_task_state = "stopped"

        # Close browser
        if self._browser:
            try:
                await self._browser.close()
            except Exception as e:
                logger.warning(f"Error closing browser: {e}")
            self._browser = None

        # Stop playwright
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as e:
                logger.warning(f"Error stopping playwright: {e}")
            self._playwright = None

        self._page = None
        self._context = None
        self._session_id = None
        self._session = None
        self._pending_file = None
        self._last_frame_hash = None
        self._latest_frame = None
        self._last_frame_at_ts = None
        self._subscriber_last_frame_at = {}
        self._frame_count = 0

    async def _handle_file_chooser(self, file_chooser):
        """
        Intercept file dialog and use pending upload.
        Called when any file input is triggered on the page.
        """
        if self._pending_file and Path(self._pending_file).exists():
            logger.info(f"File chooser intercepted, using: {self._pending_file}")
            try:
                await file_chooser.set_files(self._pending_file)
                self._log_action("file_upload", {"file": self._pending_file})
            except Exception as e:
                logger.error(f"Failed to set file: {e}")
            self._pending_file = None
        else:
            logger.warning("File chooser opened but no pending file, canceling")
            try:
                await file_chooser.set_files([])
            except Exception as e:
                logger.warning(f"Failed to cancel file chooser: {e}")

    def set_pending_file(self, file_path: str):
        """Set the file to use for next file chooser."""
        self._pending_file = file_path
        logger.info(f"Pending file set: {file_path}")

    async def _on_page_close(self):
        """Handle unexpected page closure."""
        logger.warning("Page closed unexpectedly")
        self._stream_task_state = "failed"
        # Notify subscribers of disconnect
        for ws in self._subscribers:
            try:
                await ws.send_json({"type": "error", "data": {"message": "Browser page closed"}})
            except Exception as e:
                logger.debug(f"Failed to notify subscriber of page close: {e}")

    async def _on_page_crash(self):
        """Handle page crash."""
        logger.error("Page crashed!")
        self._stream_task_state = "failed"
        # Notify subscribers of crash
        for ws in self._subscribers:
            try:
                await ws.send_json({"type": "error", "data": {"message": "Browser page crashed"}})
            except Exception as e:
                logger.debug(f"Failed to notify subscriber of page crash: {e}")

    async def _streaming_loop(self):
        """
        Background task that captures and broadcasts JPEG frames.

        Strategy:
        - Base rate: 10 FPS (100ms interval)
        - Burst rate: 30 FPS (33ms) for 500ms after any user action
        - Skip frame if identical to previous (delta detection via hash)
        - Stop after 5 consecutive errors with exponential backoff
        """
        import time

        consecutive_errors = 0
        self._stream_task_state = "running"

        try:
            while self._page and not self._page.is_closed():
                try:
                    # Adjust rate based on recent activity
                    now = time.time()
                    if now - self._last_action_time < 0.5:
                        interval = 0.033  # 30 FPS burst mode
                    else:
                        interval = 0.100  # 10 FPS idle mode

                    # Capture screenshot as JPEG bytes with timeout
                    frame = await asyncio.wait_for(
                        self._page.screenshot(
                            type="jpeg",
                            quality=70,
                            scale="css"  # 1:1 pixel mapping for coordinate accuracy
                        ),
                        timeout=10.0  # 10 second max to prevent hangs
                    )

                    # Reset consecutive errors on success
                    consecutive_errors = 0
                    self._latest_frame = frame
                    self._last_frame_at_ts = self._now_ts()

                    # Delta detection - skip if unchanged
                    frame_hash = hashlib.md5(frame).hexdigest()[:8]
                    if frame_hash != self._last_frame_hash:
                        self._last_frame_hash = frame_hash
                        self._frame_count += 1
                        await self._broadcast_frame(frame)
                    elif self._any_subscriber_missing_recent_frame():
                        # Static pages can keep the same hash indefinitely.
                        # Re-send the current frame so reconnecting subscribers
                        # do not remain stuck waiting for the first render.
                        await self._broadcast_frame(frame)

                    await asyncio.sleep(interval)

                except asyncio.CancelledError:
                    raise
                except asyncio.TimeoutError:
                    consecutive_errors += 1
                    logger.warning(f"Screenshot timeout ({consecutive_errors}/5)")
                    if consecutive_errors >= 5:
                        self._stream_task_state = "failed"
                        logger.error("Too many consecutive screenshot timeouts, stopping stream")
                        break
                    await asyncio.sleep(0.5 * consecutive_errors)  # Exponential backoff
                except Exception as e:
                    consecutive_errors += 1
                    logger.error(f"Streaming error ({consecutive_errors}/5): {e}")
                    if consecutive_errors >= 5:
                        self._stream_task_state = "failed"
                        logger.error("Too many consecutive streaming errors, stopping stream")
                        break
                    await asyncio.sleep(0.5 * consecutive_errors)  # Exponential backoff
        except asyncio.CancelledError:
            self._stream_task_state = "stopped"
            raise
        finally:
            if self._stream_task_state != "failed":
                self._stream_task_state = "stopped"

    def _build_frame_message(self, frame: bytes, *, bootstrap: bool) -> str:
        import base64

        return json.dumps({
            "type": "frame",
            "data": {
                "image": base64.b64encode(frame).decode("utf-8"),
                "width": MOBILE_VIEWPORT["width"],
                "height": MOBILE_VIEWPORT["height"],
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "bootstrap": bootstrap,
            }
        })

    async def _send_frame_to_subscriber(self, websocket, frame: bytes, *, bootstrap: bool) -> bool:
        try:
            await websocket.send_text(self._build_frame_message(frame, bootstrap=bootstrap))
            self._subscriber_last_frame_at[websocket] = self._now_ts()
            self._last_frame_at_ts = self._subscriber_last_frame_at[websocket]
            return True
        except Exception:
            return False

    async def _broadcast_frame(self, frame: bytes):
        """Send frame to all subscribed WebSocket connections."""
        if not self._subscribers:
            return

        disconnected = set()
        for ws in list(self._subscribers):
            ok = await self._send_frame_to_subscriber(ws, frame, bootstrap=False)
            if not ok:
                disconnected.add(ws)

        for ws in disconnected:
            self.unsubscribe(ws)

    async def send_bootstrap_frame(self, websocket) -> bool:
        """Send one immediate frame to a subscriber right after browser_ready."""
        if websocket not in self._subscribers:
            return False

        frame = self._latest_frame
        if frame is None:
            frame = await self.get_screenshot()
        if frame is None:
            return False

        sent = await self._send_frame_to_subscriber(websocket, frame, bootstrap=True)
        if sent:
            self._latest_frame = frame
        return sent

    def subscriber_has_recent_frame(self, websocket, within_seconds: float = 3.0) -> bool:
        ts = self._subscriber_last_frame_at.get(websocket)
        if ts is None:
            return False
        return (self._now_ts() - float(ts)) <= float(within_seconds)

    def _any_subscriber_missing_recent_frame(self, within_seconds: float = 2.5) -> bool:
        if not self._subscribers:
            return False
        for ws in list(self._subscribers):
            if not self.subscriber_has_recent_frame(ws, within_seconds=within_seconds):
                return True
        return False

    async def broadcast_event(self, event_type: str, data: Dict[str, Any]):
        """Broadcast a non-frame event to all subscribers."""
        if not self._subscribers:
            return

        message = json.dumps({
            "type": event_type,
            "data": {
                **(data or {}),
                "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            },
        })

        disconnected = set()
        for ws in list(self._subscribers):
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.add(ws)

        for ws in disconnected:
            self.unsubscribe(ws)

    async def broadcast_progress(self, stage: str):
        """Broadcast progress update to all subscribers during session startup."""
        if not self._subscribers:
            return

        message = json.dumps({
            "type": "progress",
            "data": {
                "stage": stage,
                "timestamp": datetime.now().isoformat()
            }
        })

        disconnected = set()
        for ws in list(self._subscribers):
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.add(ws)

        for ws in disconnected:
            self.unsubscribe(ws)

    async def broadcast_state(self):
        """Broadcast current state to all subscribers."""
        if not self._subscribers or not self._page:
            return

        message = json.dumps({
            "type": "state",
            "data": {
                "session_id": self._session_id,
                "url": self._page.url,
                "title": await self._page.title(),
                "timestamp": datetime.now().isoformat()
            }
        })

        disconnected = set()
        for ws in list(self._subscribers):
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.add(ws)

        for ws in disconnected:
            self.unsubscribe(ws)

    def subscribe(self, websocket) -> None:
        """Add WebSocket to frame subscribers."""
        self._subscribers.add(websocket)
        self._subscriber_last_frame_at.setdefault(websocket, 0.0)
        self._last_subscriber_at_ts = self._now_ts()
        self._cancel_idle_close_timer()
        logger.info(f"Subscriber added, total: {len(self._subscribers)}")

    def unsubscribe(self, websocket) -> None:
        """Remove WebSocket from subscribers."""
        self._subscribers.discard(websocket)
        self._subscriber_last_frame_at.pop(websocket, None)
        self._last_subscriber_at_ts = self._now_ts()
        if not self._subscribers:
            self._schedule_idle_close_timer()
        logger.info(f"Subscriber removed, total: {len(self._subscribers)}")

    async def auto_heal_session(self, *, session_id: Optional[str], reason: str) -> Dict[str, Any]:
        target = session_id or self._session_id
        if not target:
            return {"success": False, "error": "No session available for auto-heal"}

        payload = {
            "session_id": target,
            "reason": reason,
        }
        self._log_action("session_auto_heal_start", payload)
        await self.broadcast_event("session_auto_heal_start", payload)

        result = await self.start_session(target)
        if result.get("success"):
            await self.broadcast_event("stream_restarted", {
                "session_id": target,
                "reason": reason,
                "url": result.get("url"),
            })

        completion_payload = {
            "session_id": target,
            "reason": reason,
            "success": bool(result.get("success")),
            "error": result.get("error"),
        }
        self._log_action("session_auto_heal_done", completion_payload)
        await self.broadcast_event("session_auto_heal_done", completion_payload)
        return result

    async def restart_session(self, session_id: str, *, reason: str = "manual_restart") -> Dict[str, Any]:
        return await self.auto_heal_session(session_id=session_id, reason=reason)

    async def ensure_session_ready(self, session_id: str) -> Dict[str, Any]:
        """
        Health-aware session readiness check.
        Reuses healthy session, otherwise restarts automatically.
        """
        if self.session_id != session_id:
            return await self.start_session(session_id)

        if not self.is_active:
            return await self.start_session(session_id)

        reason = self._stream_health_reason()
        if reason:
            return await self.auto_heal_session(session_id=session_id, reason=reason)

        return {
            "success": True,
            "session_id": session_id,
            "url": self.current_url,
            "reused": True,
            "stream_task_state": self._stream_task_state,
        }

    async def handle_click(self, x: int, y: int) -> Dict[str, Any]:
        """
        Handle click at viewport coordinates.

        Args:
            x: X coordinate (0-393)
            y: Y coordinate (0-873)

        Returns:
            Dict with success status
        """
        if not self._page or self._page.is_closed():
            return {"success": False, "error": "No active session"}

        # Validate bounds
        viewport = self._page.viewport_size or MOBILE_VIEWPORT
        if not (0 <= x <= viewport["width"] and 0 <= y <= viewport["height"]):
            return {"success": False, "error": f"Coordinates out of bounds: ({x}, {y})"}

        try:
            import time
            self._last_action_time = time.time()

            await self._page.mouse.click(x, y)
            self._log_action("click", {"x": x, "y": y})

            # Wait briefly for UI response
            await asyncio.sleep(0.1)

            # Broadcast state update
            await self.broadcast_state()

            return {"success": True, "action": "click", "x": x, "y": y}
        except Exception as e:
            logger.error(f"Click error: {e}")
            return {"success": False, "error": str(e)}

    async def handle_keyboard(self, key: str, modifiers: List[str] = None) -> Dict[str, Any]:
        """
        Forward keyboard input to Playwright.

        Args:
            key: Key name or character
            modifiers: List of modifier keys ["Control", "Shift", "Alt", "Meta"]

        Returns:
            Dict with success status
        """
        if not self._page or self._page.is_closed():
            return {"success": False, "error": "No active session"}

        modifiers = modifiers or []

        try:
            import time
            self._last_action_time = time.time()

            # Build key combination for Playwright
            if modifiers:
                key_combo = "+".join(modifiers + [key])
            else:
                key_combo = key

            # Special keys need press(), regular chars can use type()
            special_keys = {
                "Backspace", "Tab", "Enter", "Escape", "Delete",
                "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
                "Home", "End", "PageUp", "PageDown", "F1", "F2", "F3",
                "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12"
            }

            if key in special_keys or len(key) > 1 or modifiers:
                await self._page.keyboard.press(key_combo)
            else:
                await self._page.keyboard.type(key)

            self._log_action("key", {"key": key_combo})

            return {"success": True, "action": "key", "key": key_combo}
        except Exception as e:
            logger.error(f"Keyboard error: {e}")
            return {"success": False, "error": str(e)}

    async def handle_type(self, text: str) -> Dict[str, Any]:
        """
        Type a string of text.

        Args:
            text: Text to type

        Returns:
            Dict with success status
        """
        if not self._page or self._page.is_closed():
            return {"success": False, "error": "No active session"}

        try:
            import time
            self._last_action_time = time.time()

            await self._page.keyboard.type(text, delay=30)
            self._log_action("type", {"text": text[:50]})  # Truncate for log

            return {"success": True, "action": "type", "length": len(text)}
        except Exception as e:
            logger.error(f"Type error: {e}")
            return {"success": False, "error": str(e)}

    async def handle_scroll(self, x: int, y: int, delta_y: int) -> Dict[str, Any]:
        """
        Scroll at position.

        Args:
            x: X coordinate
            y: Y coordinate
            delta_y: Scroll amount (positive = down, negative = up)

        Returns:
            Dict with success status
        """
        if not self._page or self._page.is_closed():
            return {"success": False, "error": "No active session"}

        try:
            import time
            self._last_action_time = time.time()

            # Move to position first
            await self._page.mouse.move(x, y)
            # Then scroll
            await self._page.mouse.wheel(0, delta_y)

            self._log_action("scroll", {"x": x, "y": y, "delta_y": delta_y})

            return {"success": True, "action": "scroll", "delta_y": delta_y}
        except Exception as e:
            logger.error(f"Scroll error: {e}")
            return {"success": False, "error": str(e)}

    async def navigate(self, url: str) -> Dict[str, Any]:
        """
        Navigate to URL.

        Args:
            url: URL to navigate to

        Returns:
            Dict with success status and final URL
        """
        if not self._page or self._page.is_closed():
            return {"success": False, "error": "No active session"}

        # Ensure URL has protocol
        if not url.startswith("http://") and not url.startswith("https://"):
            url = "https://" + url

        try:
            import time
            self._last_action_time = time.time()

            await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
            self._log_action("navigate", {"url": url})

            # Broadcast state update
            await self.broadcast_state()

            return {"success": True, "action": "navigate", "url": self._page.url}
        except Exception as e:
            logger.error(f"Navigate error: {e}")
            return {"success": False, "error": str(e)}

    async def get_screenshot(self) -> Optional[bytes]:
        """Take a screenshot and return as bytes."""
        if not self._page or self._page.is_closed():
            return None

        try:
            frame = await asyncio.wait_for(
                self._page.screenshot(type="jpeg", quality=80, scale="css"),
                timeout=max(0.5, float(SINGLE_SCREENSHOT_TIMEOUT_SECONDS)),
            )
            self._latest_frame = frame
            self._last_frame_at_ts = self._now_ts()
            return frame
        except asyncio.TimeoutError:
            logger.warning("Single screenshot timed out")
            return None
        except Exception as e:
            logger.warning(f"Screenshot failed: {e}")
            return None

    async def get_current_state(self) -> Dict[str, Any]:
        """Return current session state."""
        stream_status = {
            "streaming_active": self._streaming_active(),
            "stream_task_state": self._stream_task_state,
            "last_frame_at": self._iso_from_ts(self._last_frame_at_ts),
            "last_subscriber_at": self._iso_from_ts(self._last_subscriber_at_ts),
            "idle_close_at": self._iso_from_ts(self._idle_deadline_at_ts),
        }

        if not self._page or self._page.is_closed():
            return {
                "active": False,
                "session_id": self._session_id,
                "url": None,
                "title": None,
                **stream_status,
            }

        try:
            return {
                "active": True,
                "session_id": self._session_id,
                "url": self._page.url,
                "title": await self._page.title(),
                "viewport": self._page.viewport_size,
                "subscriber_count": len(self._subscribers),
                "frame_count": self._frame_count,
                **stream_status,
            }
        except Exception as e:
            logger.warning(f"Error getting current state: {e}")
            return {"active": False, "session_id": self._session_id, **stream_status}

    def _log_action(self, action: str, details: Dict):
        """Log every action for debugging and audit trail."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "details": details,
            "url": self._page.url if self._page else None
        }
        self._action_log.append(entry)
        logger.info(f"Action: {action} - {json.dumps(details)}")

        # Keep log bounded to prevent memory issues
        if len(self._action_log) > 1000:
            self._action_log = self._action_log[-500:]

    def get_action_log(self, limit: int = 100) -> List[Dict]:
        """Get recent action log entries."""
        return self._action_log[-limit:]


# Global instance getter
_browser_manager: Optional[PersistentBrowserManager] = None


def get_browser_manager() -> PersistentBrowserManager:
    """Get the singleton browser manager instance."""
    global _browser_manager
    if _browser_manager is None:
        _browser_manager = PersistentBrowserManager()
    return _browser_manager
