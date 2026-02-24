"""
Premium run scheduler with hybrid in-app loop + external tick support.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Optional

from premium_orchestrator import PremiumOrchestrator
from premium_store import PremiumStore

logger = logging.getLogger("PremiumScheduler")


class PremiumScheduler:
    def __init__(self, store: PremiumStore, orchestrator: PremiumOrchestrator):
        self.store = store
        self.orchestrator = orchestrator
        self._task: Optional[asyncio.Task] = None
        self._stop = False
        self._tick_lock = asyncio.Lock()

    async def start(self):
        if self._task and not self._task.done():
            logger.info("Premium scheduler already running")
            return
        self._stop = False
        self.store.update_scheduler_state(is_running=True)
        self._task = asyncio.create_task(self._loop())
        logger.info("Premium scheduler started")

    async def stop(self):
        self._stop = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.store.update_scheduler_state(is_running=False)
        logger.info("Premium scheduler stopped")

    async def _loop(self):
        while not self._stop:
            try:
                await self.tick(source="loop")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"premium scheduler loop error: {exc}")
                self.store.update_scheduler_state(last_error=str(exc))
            await asyncio.sleep(60)

    async def tick(self, source: str = "manual") -> Dict:
        async with self._tick_lock:
            tick_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            summary = {"processed": 0, "failed": 0}
            try:
                summary = await self.orchestrator.process_due_runs(max_runs=5)
                self.store.update_scheduler_state(
                    last_tick_at=tick_at,
                    last_error=None,
                    last_processed_count=int(summary.get("processed", 0)),
                )
            except Exception as exc:
                self.store.update_scheduler_state(last_tick_at=tick_at, last_error=str(exc))
                logger.error(f"premium scheduler tick failed ({source}): {exc}")
                raise

            return {
                "source": source,
                "tick_at": tick_at,
                **summary,
            }

    def get_status(self) -> Dict:
        scheduler_state = self.store.get_scheduler_state()
        runs = self.store.list_runs(limit=25)

        counts = {
            "scheduled": 0,
            "in_progress": 0,
            "completed": 0,
            "failed": 0,
            "cancelled": 0,
        }
        for run in runs:
            status = str(run.get("status", ""))
            if status in counts:
                counts[status] += 1

        return {
            "scheduler": scheduler_state,
            "counts": counts,
            "recent_runs": runs,
        }
