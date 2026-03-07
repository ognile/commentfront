"""
Structured audit helpers for Reddit login investigation.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import BROWSER_ARGS, DEBUG_DIR
from safe_io import atomic_write_json, safe_read_json

logger = logging.getLogger("RedditLoginAudit")

AUDIT_ROOT = Path(os.getenv("REDDIT_LOGIN_AUDIT_DIR", os.path.join(DEBUG_DIR, "reddit_login_audit")))
AUDIT_ROOT.mkdir(parents=True, exist_ok=True)


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower())
    return normalized.strip("_") or "unknown"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _attempt_id(mode: str, credential_label: str, session_id: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    seed = f"{mode}:{credential_label}:{session_id}:{timestamp}"
    suffix = hashlib.md5(seed.encode()).hexdigest()[:8]
    return f"{timestamp}_{_slug(mode)}_{_slug(credential_label)}_{_slug(session_id)}_{suffix}"


def _screenshot_url(relative_path: str) -> str:
    return f"/screenshots/{relative_path.replace(os.sep, '/')}"


def classify_reddit_failure(audit: Dict[str, Any], error: Optional[str]) -> Optional[str]:
    checkpoints = list(audit.get("checkpoints") or [])
    error_text = " ".join(
        " ".join(checkpoint.get("visible_errors") or []) for checkpoint in checkpoints
    ).lower()
    error_blob = f"{error or ''} {error_text}".lower()

    otp_seen = any(bool(checkpoint.get("otp_input_present")) for checkpoint in checkpoints)
    profile_seen = any("/user/" in str(checkpoint.get("url") or "").lower() for checkpoint in checkpoints)
    protected_fail = any(
        str(checkpoint.get("name") or "").startswith("protected_destination_verify")
        and "/login" in str(checkpoint.get("url") or "").lower()
        for checkpoint in checkpoints
    )

    if "something went wrong logging in" in error_blob:
        return "login_banner_error"
    if "incorrect username or password" in error_blob:
        return "login_banner_error"
    if otp_seen and ("invalid code" in error_blob or "incorrect code" in error_blob):
        return "otp_shown_but_rejected"
    if not otp_seen and ("/login" in error_blob or "login" in error_blob):
        return "otp_never_shown"
    if profile_seen:
        return "protected_routes_fail" if protected_fail else "public_profile_only"
    if "authenticated destination verification" in error_blob:
        return "partial_auth_only"
    if "reopen" in error_blob or "storage" in error_blob:
        return "saved_session_reopen_fails"
    return None


def compare_reddit_audits(reference: Dict[str, Any], standalone: Dict[str, Any]) -> Dict[str, Any]:
    ref_context = dict(reference.get("context") or {})
    std_context = dict(standalone.get("context") or {})

    context_keys = [
        "user_agent",
        "viewport",
        "is_mobile",
        "has_touch",
        "locale",
        "timezone_id",
        "proxy_source",
        "proxy_url",
        "launch_args",
    ]
    context_differences = {}
    for key in context_keys:
        ref_value = ref_context.get(key)
        std_value = std_context.get(key)
        if ref_value != std_value:
            context_differences[key] = {"reference": ref_value, "standalone": std_value}

    ref_checkpoint_map = {item.get("name"): item for item in list(reference.get("checkpoints") or [])}
    std_checkpoint_map = {item.get("name"): item for item in list(standalone.get("checkpoints") or [])}
    checkpoint_differences: Dict[str, Any] = {}

    for checkpoint_name in sorted(set(ref_checkpoint_map) | set(std_checkpoint_map)):
        ref_item = ref_checkpoint_map.get(checkpoint_name) or {}
        std_item = std_checkpoint_map.get(checkpoint_name) or {}
        delta = {}
        for key in (
            "url",
            "login_inputs_present",
            "login_inputs_visible",
            "otp_input_present",
            "otp_input_visible",
            "otp_visible_selectors",
            "visible_errors",
            "cookie_names",
        ):
            if ref_item.get(key) != std_item.get(key):
                delta[key] = {"reference": ref_item.get(key), "standalone": std_item.get(key)}
        if delta:
            checkpoint_differences[checkpoint_name] = delta

    return {
        "reference_attempt_id": reference.get("attempt_id"),
        "standalone_attempt_id": standalone.get("attempt_id"),
        "context_differences": context_differences,
        "checkpoint_differences": checkpoint_differences,
        "reference_result": dict(reference.get("result") or {}),
        "standalone_result": dict(standalone.get("result") or {}),
    }


def load_reddit_audit(attempt_id: str) -> Optional[Dict[str, Any]]:
    path = AUDIT_ROOT / str(attempt_id) / "audit.json"
    if path.exists():
        return safe_read_json(str(path))

    needle = _slug(attempt_id)
    for audit_dir in AUDIT_ROOT.iterdir():
        if not audit_dir.is_dir():
            continue
        if _slug(audit_dir.name) == needle:
            candidate = audit_dir / "audit.json"
            if candidate.exists():
                return safe_read_json(str(candidate))
    return None


class RedditLoginAudit:
    def __init__(
        self,
        *,
        mode: str,
        credential_label: str,
        session_id: str,
        proxy_url: Optional[str],
        proxy_source: str,
        context_data: Dict[str, Any],
    ):
        self.attempt_id = _attempt_id(mode, credential_label, session_id)
        self.attempt_dir = AUDIT_ROOT / self.attempt_id
        self.attempt_dir.mkdir(parents=True, exist_ok=True)
        self.relative_dir = os.path.join(AUDIT_ROOT.name, self.attempt_id)
        self.audit_path = self.attempt_dir / "audit.json"
        self._request_count = 0
        self._response_count = 0

        self.data: Dict[str, Any] = {
            "attempt_id": self.attempt_id,
            "created_at": _now_iso(),
            "mode": mode,
            "credential_label": credential_label,
            "session_id": session_id,
            "context": {
                "browser_engine": "chromium",
                "launch_args": list(BROWSER_ARGS),
                "proxy_source": proxy_source,
                "proxy_url": proxy_url,
                **dict(context_data or {}),
            },
            "requests": [],
            "responses": [],
            "navigations": [],
            "events": [],
            "checkpoints": [],
            "result": {},
        }
        self.flush()

    @property
    def audit_json_url(self) -> str:
        return _screenshot_url(os.path.join(self.relative_dir, "audit.json"))

    def record_event(self, event: str, **payload: Any) -> None:
        self.data["events"].append({"ts": _now_iso(), "event": event, **payload})
        self.flush()

    def attach_page(self, page) -> None:
        def _capture_request(request) -> None:
            try:
                url = str(request.url or "")
                lowered = url.lower()
                if not self._request_relevant(request, lowered):
                    return
                self._request_count += 1
                self.data["requests"].append(
                    {
                        "idx": self._request_count,
                        "ts": _now_iso(),
                        "method": request.method,
                        "url": url,
                        "resource_type": request.resource_type,
                        "is_navigation_request": request.is_navigation_request(),
                        "headers": self._trim_headers(request.headers),
                        "post_data": (request.post_data or "")[:1000],
                    }
                )
            except Exception as exc:
                logger.debug(f"failed to capture reddit audit request: {exc}")

        def _capture_response(response) -> None:
            try:
                request = response.request
                lowered = str(response.url or "").lower()
                if not self._request_relevant(request, lowered):
                    return
                self._response_count += 1
                record = {
                    "idx": self._response_count,
                    "ts": _now_iso(),
                    "url": response.url,
                    "status": response.status,
                    "status_text": response.status_text,
                    "from_service_worker": response.from_service_worker,
                    "resource_type": request.resource_type,
                    "is_navigation_request": request.is_navigation_request(),
                }
                self.data["responses"].append(record)
                if self._response_body_relevant(lowered):
                    asyncio.create_task(self._enrich_response_record(response, record))
                self.flush()
            except Exception as exc:
                logger.debug(f"failed to capture reddit audit response: {exc}")

        def _capture_navigation(frame) -> None:
            try:
                if frame != page.main_frame:
                    return
                self.data["navigations"].append({"ts": _now_iso(), "url": frame.url})
            except Exception as exc:
                logger.debug(f"failed to capture reddit audit navigation: {exc}")

        page.on("request", _capture_request)
        page.on("response", _capture_response)
        page.on("framenavigated", _capture_navigation)

    async def capture_checkpoint(self, page, context, name: str) -> Dict[str, Any]:
        screenshot_filename = f"{len(self.data['checkpoints']):02d}_{_slug(name)}.png"
        screenshot_path = self.attempt_dir / screenshot_filename
        visible_errors = await self._extract_visible_errors(page)
        body_preview = await self._body_preview(page)
        fingerprint = await self._fingerprint_snapshot(page)
        storage_keys = await self._storage_keys(page)
        cookie_names = await self._cookie_names(context)
        login_inputs_present = await self._selector_count(page, 'input[name="username"], input[name="password"]') > 0
        login_inputs_visible = await self._any_visible(page, ('input[name="username"]', 'input[name="password"]'))
        otp_input_present = False
        otp_visible_selectors: List[str] = []
        for selector in (
            'input[name="otp"]',
            'input[name="code"]',
            'input[autocomplete="one-time-code"]',
            'input[inputmode="numeric"]',
        ):
            if await self._selector_count(page, selector) > 0:
                otp_input_present = True
            if await self._selector_visible(page, selector):
                otp_visible_selectors.append(selector)
        otp_input_visible = bool(otp_visible_selectors)

        try:
            await page.screenshot(path=str(screenshot_path), scale="css", timeout=10000)
        except Exception as exc:
            logger.warning(f"failed to capture reddit audit screenshot {screenshot_filename}: {exc}")

        checkpoint = {
            "ts": _now_iso(),
            "name": name,
            "url": page.url,
            "visible_errors": visible_errors,
            "body_preview": body_preview,
            "login_inputs_present": login_inputs_present,
            "login_inputs_visible": login_inputs_visible,
            "otp_input_present": otp_input_present,
            "otp_input_visible": otp_input_visible,
            "otp_visible_selectors": otp_visible_selectors,
            "cookie_names": cookie_names,
            "storage_keys": storage_keys,
            "fingerprint": fingerprint,
            "screenshot_path": str(screenshot_path),
            "screenshot_url": _screenshot_url(os.path.join(self.relative_dir, screenshot_filename)),
        }
        self.data["checkpoints"].append(checkpoint)
        self.flush()
        return checkpoint

    def finalize(self, *, success: bool, error: Optional[str], extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        failure_bucket = None if success else classify_reddit_failure(self.data, error)
        self.data["result"] = {
            "success": success,
            "error": error,
            "failure_bucket": failure_bucket,
            **dict(extra or {}),
        }
        self.data["updated_at"] = _now_iso()
        self.flush()
        return {
            "attempt_id": self.attempt_id,
            "audit_json_url": self.audit_json_url,
            "audit_dir_url": _screenshot_url(self.relative_dir),
            "failure_bucket": failure_bucket,
        }

    def flush(self) -> None:
        atomic_write_json(str(self.audit_path), self.data)

    @staticmethod
    def _trim_headers(headers: Dict[str, Any]) -> Dict[str, Any]:
        keep = (
            "accept",
            "accept-language",
            "content-type",
            "origin",
            "referer",
            "sec-ch-ua",
            "sec-ch-ua-mobile",
            "sec-ch-ua-platform",
            "user-agent",
        )
        lowered = {str(k).lower(): v for k, v in dict(headers or {}).items()}
        return {key: lowered.get(key) for key in keep if key in lowered}

    @staticmethod
    def _trim_response_headers(headers: Dict[str, Any]) -> Dict[str, Any]:
        keep = (
            "cache-control",
            "content-length",
            "content-type",
            "retry-after",
            "set-cookie",
            "x-reddit-loid",
            "x-ua-compatible",
        )
        lowered = {str(k).lower(): v for k, v in dict(headers or {}).items()}
        return {key: lowered.get(key) for key in keep if key in lowered}

    @staticmethod
    def _request_relevant(request, lowered_url: str) -> bool:
        if request.is_navigation_request():
            return True
        markers = (
            "reddit.com/login",
            "svc/shreddit",
            "reddit.com/user/",
            "reddit.com/submit",
            "reddit.com/settings",
            "captcha",
            "recaptcha",
            "otp",
            "token",
            "auth",
        )
        return any(marker in lowered_url for marker in markers)

    @staticmethod
    def _response_body_relevant(lowered_url: str) -> bool:
        markers = (
            "/svc/shreddit/account/login",
            "/svc/shreddit/account/login/otp",
            "/svc/shreddit/graphql",
            "/svc/shreddit/partial/",
        )
        return any(marker in lowered_url for marker in markers)

    async def _enrich_response_record(self, response, record: Dict[str, Any]) -> None:
        try:
            text = await response.text()
        except Exception as exc:
            record["body_read_error"] = str(exc)
            self.flush()
            return

        record["headers"] = self._trim_response_headers(response.headers)
        record["body_preview"] = self._normalize_body_preview(text)
        self.flush()

    @staticmethod
    def _normalize_body_preview(text: Any, limit: int = 1200) -> str:
        if text is None:
            return ""
        return re.sub(r"\s+", " ", str(text))[:limit]

    @staticmethod
    async def _selector_count(page, selector: str) -> int:
        try:
            return await page.locator(selector).count()
        except Exception:
            return 0

    @staticmethod
    async def _selector_visible(page, selector: str) -> bool:
        try:
            locator = page.locator(selector).first
            if await locator.count() <= 0:
                return False
            return await locator.is_visible()
        except Exception:
            return False

    async def _any_visible(self, page, selectors: tuple[str, ...]) -> bool:
        for selector in selectors:
            if await self._selector_visible(page, selector):
                return True
        return False

    @staticmethod
    async def _cookie_names(context) -> List[str]:
        try:
            cookies = await context.cookies()
            return sorted({str(cookie.get("name") or "") for cookie in cookies})
        except Exception:
            return []

    @staticmethod
    async def _storage_keys(page) -> Dict[str, List[str]]:
        try:
            return await page.evaluate(
                """() => ({
                    local_storage: Object.keys(window.localStorage || {}),
                    session_storage: Object.keys(window.sessionStorage || {}),
                })"""
            )
        except Exception:
            return {"local_storage": [], "session_storage": []}

    @staticmethod
    async def _body_preview(page) -> str:
        try:
            text = await page.locator("body").inner_text()
        except Exception:
            return ""
        return re.sub(r"\s+", " ", text)[:400]

    @staticmethod
    async def _extract_visible_errors(page) -> List[str]:
        scripts = """
            () => {
                const selectors = [
                    '[role="alert"]',
                    '[data-testid*="error"]',
                    '[class*="error"]',
                    '[class*="alert"]',
                    'faceplate-form-helper-text',
                ];
                const values = [];
                for (const selector of selectors) {
                    for (const node of document.querySelectorAll(selector)) {
                        const text = (node.innerText || node.textContent || '').trim();
                        if (text) values.push(text);
                    }
                }
                return Array.from(new Set(values)).slice(0, 10);
            }
        """
        try:
            values = await page.evaluate(scripts)
        except Exception:
            values = []
        return [str(value)[:300] for value in list(values or []) if str(value).strip()]

    @staticmethod
    async def _fingerprint_snapshot(page) -> Dict[str, Any]:
        script = """
            async () => {
                const data = {
                    user_agent: navigator.userAgent,
                    platform: navigator.platform,
                    webdriver: navigator.webdriver,
                    languages: navigator.languages,
                    language: navigator.language,
                    screen: {
                        width: window.screen.width,
                        height: window.screen.height,
                        availWidth: window.screen.availWidth,
                        availHeight: window.screen.availHeight,
                    },
                    viewport: {
                        innerWidth: window.innerWidth,
                        innerHeight: window.innerHeight,
                    },
                    device_pixel_ratio: window.devicePixelRatio,
                    max_touch_points: navigator.maxTouchPoints,
                    user_agent_data: null,
                    permissions: {},
                };

                try {
                    if (navigator.userAgentData) {
                        data.user_agent_data = {
                            mobile: navigator.userAgentData.mobile,
                            platform: navigator.userAgentData.platform,
                            brands: navigator.userAgentData.brands,
                        };
                    }
                } catch (error) {}

                for (const name of ['geolocation', 'notifications', 'clipboard-read']) {
                    try {
                        const status = await navigator.permissions.query({ name });
                        data.permissions[name] = status.state;
                    } catch (error) {
                        data.permissions[name] = 'unsupported';
                    }
                }
                return data;
            }
        """
        try:
            return await page.evaluate(script)
        except Exception:
            return {}
