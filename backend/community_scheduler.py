"""Community task scheduler — 60s polling loop identical to premium_scheduler.py."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from community_orchestrator import CommunityOrchestrator
from community_store import CommunityStore, get_community_store

logger = logging.getLogger("CommunityScheduler")

# Track last daily plan generation to avoid duplicates
_last_plan_date: Optional[str] = None


class CommunityScheduler:
    def __init__(
        self,
        store: Optional[CommunityStore] = None,
        orchestrator: Optional[CommunityOrchestrator] = None,
    ):
        self.store = store or get_community_store()
        self.orchestrator = orchestrator or CommunityOrchestrator(self.store)
        self._task: Optional[asyncio.Task] = None
        self._stop = False
        self._tick_lock = asyncio.Lock()
        self._last_tick_at: Optional[str] = None
        self._last_error: Optional[str] = None
        self._last_processed: int = 0

    async def start(self):
        if not self.store.enabled:
            logger.warning("community scheduler disabled — supabase not configured")
            return
        if self._task and not self._task.done():
            logger.info("community scheduler already running")
            return
        self._stop = False
        self._task = asyncio.create_task(self._loop())
        logger.info("community scheduler started")

    async def stop(self):
        self._stop = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("community scheduler stopped")

    async def _loop(self):
        while not self._stop:
            try:
                await self.tick(source="loop")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"community scheduler loop error: {exc}")
                self._last_error = str(exc)
            await asyncio.sleep(60)

    async def tick(self, source: str = "manual") -> Dict:
        async with self._tick_lock:
            tick_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            summary = {"processed": 0, "failed": 0, "skipped": 0}
            try:
                # Check if daily plan needs generating
                await self._maybe_generate_daily_plan()
                summary = await self.orchestrator.process_due_tasks(max_tasks=3)
                self._last_tick_at = tick_at
                self._last_error = None
                self._last_processed = summary.get("processed", 0)
            except Exception as exc:
                self._last_tick_at = tick_at
                self._last_error = str(exc)
                logger.error(f"community scheduler tick failed ({source}): {exc}")
                raise

            return {
                "source": source,
                "tick_at": tick_at,
                **summary,
            }

    async def _maybe_generate_daily_plan(self) -> None:
        """Check if a daily plan needs generating (runs at midnight ET)."""
        global _last_plan_date
        try:
            config = await self.store.get_planner_config()
            if config.get("planner_schedule") != "daily_midnight":
                return

            from zoneinfo import ZoneInfo
            tz = ZoneInfo(config.get("timezone", "America/New_York"))
            now_local = datetime.now(tz)

            # Only run between midnight and 1am local time
            if now_local.hour != 0:
                return

            tomorrow = (now_local + timedelta(days=1)).strftime("%Y-%m-%d")
            if _last_plan_date == tomorrow:
                return  # already generated for this date

            from community_planner import generate_daily_plan
            result = await generate_daily_plan(target_date=tomorrow, store=self.store)
            if result.get("tasks_created", 0) > 0:
                _last_plan_date = tomorrow
                logger.info(f"daily plan auto-generated for {tomorrow}: {result.get('tasks_created')} tasks")
            else:
                logger.warning(f"daily plan generation returned 0 tasks for {tomorrow}: {result}")
        except Exception as exc:
            logger.error(f"daily plan generation failed: {exc}")

    def get_status(self) -> Dict:
        return {
            "running": self._task is not None and not self._task.done() if self._task else False,
            "last_tick_at": self._last_tick_at,
            "last_error": self._last_error,
            "last_processed": self._last_processed,
        }
