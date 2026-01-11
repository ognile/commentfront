"""
Debug Logger for Comment Automation

Creates structured audit trails for each job:
- Per-job directories with timestamps
- Per-attempt subdirectories
- Screenshot + HTML at each step
- Browser console log capture
- JSON summary for easy debugging
"""

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime
from typing import Dict, List, Optional, Any
from pathlib import Path

logger = logging.getLogger("DebugLogger")

# Base debug directory
DEBUG_BASE = Path(__file__).parent / "debug"
MAX_JOBS_TO_KEEP = 20


class DebugLogger:
    """
    Captures comprehensive debug information for each job attempt.

    Usage:
        debug = DebugLogger(job_id="abc123", profile_name="FB_Android_1", url="...", comment="...")
        debug.new_attempt()

        await debug.log_step(page, "01_navigate", "goto", {"url": url})
        await debug.log_step(page, "02_comment_click", "mouse_click", {"x": 196, "y": 533})
        ...

        debug.end_attempt(status="success")  # or "failed" with error
        debug.save_summary()
    """

    def __init__(self, job_id: str, profile_name: str, url: str, comment: str):
        self.job_id = job_id
        self.profile_name = profile_name
        self.url = url
        self.comment = comment
        self.created_at = datetime.now()

        # Create job directory
        timestamp = self.created_at.strftime("%Y-%m-%d_%H%M%S")
        self.job_dir = DEBUG_BASE / f"job_{profile_name}_{timestamp}"
        self.job_dir.mkdir(parents=True, exist_ok=True)

        # Attempt tracking
        self.current_attempt = 0
        self.attempt_dir: Optional[Path] = None
        self.attempt_start: Optional[datetime] = None
        self.attempts: List[Dict] = []
        self.current_steps: List[Dict] = []

        # Console log buffer
        self.console_logs: List[Dict] = []
        self._console_handler_attached = False

        # Cleanup old job directories
        self._cleanup_old_jobs()

        logger.info(f"[DEBUG] Created job directory: {self.job_dir}")

    def _cleanup_old_jobs(self):
        """Keep only the last MAX_JOBS_TO_KEEP job directories."""
        try:
            if not DEBUG_BASE.exists():
                return

            # Get all job directories sorted by modification time
            job_dirs = [
                d for d in DEBUG_BASE.iterdir()
                if d.is_dir() and d.name.startswith("job_")
            ]
            job_dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)

            # Delete old ones
            for old_dir in job_dirs[MAX_JOBS_TO_KEEP:]:
                try:
                    shutil.rmtree(old_dir)
                    logger.info(f"[DEBUG] Cleaned up old job dir: {old_dir.name}")
                except Exception as e:
                    logger.warning(f"[DEBUG] Failed to cleanup {old_dir}: {e}")
        except Exception as e:
            logger.warning(f"[DEBUG] Cleanup failed: {e}")

    def new_attempt(self) -> int:
        """
        Start a new attempt. Returns the attempt number.
        """
        self.current_attempt += 1
        self.attempt_dir = self.job_dir / f"attempt_{self.current_attempt}"
        self.attempt_dir.mkdir(exist_ok=True)
        self.attempt_start = datetime.now()
        self.current_steps = []
        self.console_logs = []

        logger.info(f"[DEBUG] Starting attempt #{self.current_attempt}")
        return self.current_attempt

    def attach_console_listener(self, page):
        """
        Attach console log listener to capture browser console output.
        Call this once after creating a new page.
        """
        if self._console_handler_attached:
            return

        def handle_console(msg):
            self.console_logs.append({
                "type": msg.type,
                "text": msg.text,
                "timestamp": datetime.now().isoformat()
            })

        page.on("console", handle_console)
        self._console_handler_attached = True
        logger.info("[DEBUG] Attached console log listener")

    async def log_step(
        self,
        page,
        step_name: str,
        action: str,
        details: Optional[Dict] = None,
        capture_screenshot: bool = True,
        capture_html: bool = True
    ):
        """
        Log a single step with screenshot, HTML, and metadata.

        Args:
            page: Playwright page object
            step_name: Name for the step (e.g., "01_navigate", "02_comment_click")
            action: What action was taken (e.g., "goto", "mouse_click", "keyboard_type")
            details: Additional context (coordinates, selectors, text, etc.)
            capture_screenshot: Whether to capture a screenshot
            capture_html: Whether to capture HTML snapshot
        """
        if not self.attempt_dir:
            logger.warning("[DEBUG] log_step called before new_attempt()")
            return

        step_start = datetime.now()
        step_data = {
            "step": step_name,
            "action": action,
            "timestamp": step_start.isoformat(),
            "details": details or {}
        }

        # Capture viewport info
        try:
            viewport = page.viewport_size
            step_data["viewport"] = viewport
        except:
            pass

        # Capture current URL
        try:
            step_data["url"] = page.url
        except:
            pass

        # Screenshot
        if capture_screenshot:
            try:
                screenshot_path = self.attempt_dir / f"{step_name}.png"
                await page.screenshot(path=str(screenshot_path))
                
                # Copy to 'latest.png' for realtime frontend monitoring
                try:
                    latest_path = DEBUG_BASE / "latest.png"
                    shutil.copy(screenshot_path, latest_path)
                except Exception as copy_err:
                    logger.warning(f"[DEBUG] Failed to update latest.png: {copy_err}")

                step_data["screenshot"] = f"attempt_{self.current_attempt}/{step_name}.png"
                logger.info(f"[DEBUG] Screenshot: {step_name}.png")
            except Exception as e:
                step_data["screenshot_error"] = str(e)
                logger.warning(f"[DEBUG] Screenshot failed: {e}")

        # HTML snapshot
        if capture_html:
            try:
                html_path = self.attempt_dir / f"{step_name}.html"
                html_content = await page.content()
                html_path.write_text(html_content, encoding="utf-8")
                step_data["html"] = f"attempt_{self.current_attempt}/{step_name}.html"
            except Exception as e:
                step_data["html_error"] = str(e)

        # Calculate duration from previous step
        if self.current_steps:
            prev_time = datetime.fromisoformat(self.current_steps[-1]["timestamp"])
            step_data["duration_from_prev_ms"] = int((step_start - prev_time).total_seconds() * 1000)

        self.current_steps.append(step_data)
        logger.info(f"[DEBUG] Logged step: {step_name} ({action})")

    async def log_element_bounds(self, page, selector: str, label: str = "element"):
        """
        Log the bounding box of an element for debugging click positions.
        """
        try:
            element = page.locator(selector).first
            box = await element.bounding_box()
            if box:
                logger.info(f"[DEBUG] {label} bounds: x={box['x']:.0f}, y={box['y']:.0f}, w={box['width']:.0f}, h={box['height']:.0f}")
                return box
        except Exception as e:
            logger.debug(f"[DEBUG] Could not get bounds for {selector}: {e}")
        return None

    def end_attempt(self, status: str, error: Optional[str] = None):
        """
        End the current attempt and record results.

        Args:
            status: "success" or "failed"
            error: Error message if failed
        """
        if not self.attempt_start:
            return

        end_time = datetime.now()
        duration_ms = int((end_time - self.attempt_start).total_seconds() * 1000)

        attempt_data = {
            "attempt": self.current_attempt,
            "started_at": self.attempt_start.isoformat(),
            "ended_at": end_time.isoformat(),
            "duration_ms": duration_ms,
            "status": status,
            "steps": self.current_steps,
            "console_logs": self.console_logs
        }

        if error:
            attempt_data["error"] = error

        self.attempts.append(attempt_data)

        # Save attempt log to file
        try:
            log_path = self.attempt_dir / "log.json"
            log_path.write_text(json.dumps(attempt_data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"[DEBUG] Failed to save attempt log: {e}")

        logger.info(f"[DEBUG] Attempt #{self.current_attempt} ended: {status} ({duration_ms}ms)")

    def save_summary(self, final_status: str = None):
        """
        Save the complete job summary to summary.json
        """
        # Determine final status from attempts if not provided
        if final_status is None:
            if any(a["status"] == "success" for a in self.attempts):
                final_status = "success"
            elif self.attempts:
                final_status = "failed"
            else:
                final_status = "unknown"

        summary = {
            "job_id": self.job_id,
            "profile": self.profile_name,
            "url": self.url,
            "comment": self.comment,
            "created_at": self.created_at.isoformat(),
            "total_attempts": len(self.attempts),
            "final_status": final_status,
            "attempts": self.attempts
        }

        try:
            summary_path = self.job_dir / "summary.json"
            summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            logger.info(f"[DEBUG] Saved summary: {summary_path}")
        except Exception as e:
            logger.error(f"[DEBUG] Failed to save summary: {e}")

        return summary

    def get_job_dir(self) -> Path:
        """Return the job directory path."""
        return self.job_dir


def create_debug_logger(job_id: str, profile_name: str, url: str, comment: str) -> DebugLogger:
    """
    Factory function to create a DebugLogger instance.
    """
    return DebugLogger(job_id, profile_name, url, comment)
