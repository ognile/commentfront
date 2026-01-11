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
from datetime import datetime
from typing import Optional, Dict, Any, List, Set
from pathlib import Path

from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Playwright
from playwright_stealth import Stealth
from urllib.parse import urlparse, unquote

from fb_session import FacebookSession, apply_session_to_context

logger = logging.getLogger("BrowserManager")

# Mobile viewport (same as comment_bot.py)
MOBILE_VIEWPORT = {"width": 393, "height": 873}
DEFAULT_USER_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"

# Temp directory for uploaded images
UPLOAD_DIR = Path("/tmp/commentbot_uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _build_playwright_proxy(proxy_url: str) -> Dict[str, str]:
    """Convert proxy URL to Playwright format (same as comment_bot.py)."""
    parsed = urlparse(proxy_url)
    if parsed.scheme and parsed.hostname and parsed.port:
        proxy: Dict[str, str] = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
        if parsed.username:
            proxy["username"] = unquote(parsed.username)
        if parsed.password:
            proxy["password"] = unquote(parsed.password)
        return proxy
    return {"server": proxy_url}


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
        self._action_log: List[Dict] = []
        self._lock = asyncio.Lock()

        # For file chooser interception
        self._pending_file: Optional[str] = None

        # Frame streaming state
        self._last_frame_hash: Optional[str] = None
        self._last_action_time: float = 0
        self._frame_count: int = 0

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

    async def start_session(self, session_id: str) -> Dict[str, Any]:
        """
        Launch browser with session's fingerprint.

        Args:
            session_id: Profile name to load session for

        Returns:
            Dict with success status and details
        """
        async with self._lock:
            # Close existing session if any
            if self.is_active:
                await self._cleanup()

            try:
                # Load session data
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
                proxy_url = session.get_proxy()
                device_fingerprint = session.get_device_fingerprint()

                # Build context options (same as comment_bot.py)
                context_options = {
                    "user_agent": user_agent,
                    "viewport": viewport,
                    "ignore_https_errors": True,
                    "device_scale_factor": 1,  # Critical for coordinate accuracy
                    "timezone_id": device_fingerprint["timezone"],
                    "locale": device_fingerprint["locale"],
                }

                if proxy_url:
                    context_options["proxy"] = _build_playwright_proxy(proxy_url)

                # Launch browser
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(
                    headless=True,
                    args=["--disable-notifications", "--disable-geolocation"]
                )

                # Create context
                self._context = await self._browser.new_context(**context_options)

                # Apply stealth mode (MANDATORY for anti-detection)
                await Stealth().apply_stealth_async(self._context)

                # Create page
                self._page = await self._context.new_page()

                # Apply session cookies
                await apply_session_to_context(self._context, session)

                # Set up file chooser interception
                self._page.on("filechooser", self._handle_file_chooser)

                # Navigate to Facebook
                await self._page.goto("https://m.facebook.com/", wait_until="domcontentloaded", timeout=30000)

                # Start frame streaming
                self._streaming_task = asyncio.create_task(self._streaming_loop())

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
        # Stop streaming
        if self._streaming_task:
            self._streaming_task.cancel()
            try:
                await self._streaming_task
            except asyncio.CancelledError:
                pass
            self._streaming_task = None

        # Close browser
        if self._browser:
            try:
                await self._browser.close()
            except:
                pass
            self._browser = None

        # Stop playwright
        if self._playwright:
            try:
                await self._playwright.stop()
            except:
                pass
            self._playwright = None

        self._page = None
        self._context = None
        self._session_id = None
        self._session = None
        self._pending_file = None
        self._last_frame_hash = None

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
            except:
                pass

    def set_pending_file(self, file_path: str):
        """Set the file to use for next file chooser."""
        self._pending_file = file_path
        logger.info(f"Pending file set: {file_path}")

    async def _streaming_loop(self):
        """
        Background task that captures and broadcasts JPEG frames.

        Strategy:
        - Base rate: 10 FPS (100ms interval)
        - Burst rate: 30 FPS (33ms) for 500ms after any user action
        - Skip frame if identical to previous (delta detection via hash)
        """
        import time

        while self._page and not self._page.is_closed():
            try:
                # Adjust rate based on recent activity
                now = time.time()
                if now - self._last_action_time < 0.5:
                    interval = 0.033  # 30 FPS burst mode
                else:
                    interval = 0.100  # 10 FPS idle mode

                # Capture screenshot as JPEG bytes
                frame = await self._page.screenshot(
                    type="jpeg",
                    quality=70,
                    scale="css"  # 1:1 pixel mapping for coordinate accuracy
                )

                # Delta detection - skip if unchanged
                frame_hash = hashlib.md5(frame).hexdigest()[:8]
                if frame_hash != self._last_frame_hash:
                    self._last_frame_hash = frame_hash
                    self._frame_count += 1
                    await self._broadcast_frame(frame)

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Streaming error: {e}")
                await asyncio.sleep(0.5)

    async def _broadcast_frame(self, frame: bytes):
        """Send frame to all subscribed WebSocket connections."""
        import base64

        if not self._subscribers:
            return

        # Send as base64 JSON (simpler than binary for now)
        message = json.dumps({
            "type": "frame",
            "data": {
                "image": base64.b64encode(frame).decode("utf-8"),
                "width": MOBILE_VIEWPORT["width"],
                "height": MOBILE_VIEWPORT["height"],
                "timestamp": datetime.now().isoformat()
            }
        })

        disconnected = set()
        for ws in self._subscribers:
            try:
                await ws.send_text(message)
            except:
                disconnected.add(ws)

        for ws in disconnected:
            self._subscribers.discard(ws)

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
        for ws in self._subscribers:
            try:
                await ws.send_text(message)
            except:
                disconnected.add(ws)

        for ws in disconnected:
            self._subscribers.discard(ws)

    def subscribe(self, websocket) -> None:
        """Add WebSocket to frame subscribers."""
        self._subscribers.add(websocket)
        logger.info(f"Subscriber added, total: {len(self._subscribers)}")

    def unsubscribe(self, websocket) -> None:
        """Remove WebSocket from subscribers."""
        self._subscribers.discard(websocket)
        logger.info(f"Subscriber removed, total: {len(self._subscribers)}")

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
            return await self._page.screenshot(type="jpeg", quality=80, scale="css")
        except:
            return None

    async def get_current_state(self) -> Dict[str, Any]:
        """Return current session state."""
        if not self._page or self._page.is_closed():
            return {
                "active": False,
                "session_id": None,
                "url": None,
                "title": None
            }

        try:
            return {
                "active": True,
                "session_id": self._session_id,
                "url": self._page.url,
                "title": await self._page.title(),
                "viewport": self._page.viewport_size,
                "subscriber_count": len(self._subscribers),
                "frame_count": self._frame_count
            }
        except:
            return {"active": False, "session_id": self._session_id}

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
