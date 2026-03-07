"""
Persistent mission store and scheduler for Reddit brief execution.
"""

import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional

from safe_io import atomic_write_json, safe_read_json

logger = logging.getLogger("RedditMissionStore")


def _utcnow() -> datetime:
    return datetime.utcnow()


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


class RedditMissionStore:
    def __init__(self, file_path: Optional[str] = None):
        self.file_path = file_path or os.getenv(
            "REDDIT_MISSIONS_PATH",
            os.path.join(os.path.dirname(__file__), "reddit_missions.json"),
        )
        self.state: Dict[str, Any] = {"missions": {}}
        self.load()

    def load(self):
        data = safe_read_json(self.file_path, default={"missions": {}})
        self.state = data or {"missions": {}}

    def save(self):
        payload = {
            "updated_at": _iso(_utcnow()),
            **self.state,
        }
        atomic_write_json(self.file_path, payload)

    def _compute_next_run_at(self, mission: Dict[str, Any], from_time: Optional[datetime] = None) -> Optional[str]:
        cadence = dict(mission.get("cadence") or {})
        cadence_type = str(cadence.get("type") or "once").strip().lower()
        base = from_time or _utcnow()

        if cadence_type == "once":
            return None
        if cadence_type == "daily":
            scheduled_hour = int(cadence.get("hour", 9))
            scheduled_minute = int(cadence.get("minute", 0))
            next_run = base.replace(hour=scheduled_hour, minute=scheduled_minute, second=0, microsecond=0)
            if next_run <= base:
                next_run += timedelta(days=1)
            return _iso(next_run)
        interval_hours = max(1, int(cadence.get("interval_hours", 24)))
        return _iso(base + timedelta(hours=interval_hours))

    def list_missions(self) -> List[Dict[str, Any]]:
        return sorted(self.state.get("missions", {}).values(), key=lambda item: item.get("created_at") or "")

    def get_mission(self, mission_id: str) -> Optional[Dict[str, Any]]:
        mission = self.state.get("missions", {}).get(mission_id)
        return dict(mission) if mission else None

    def create_mission(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        mission_id = f"reddit_mission_{uuid.uuid4().hex[:10]}"
        created_at = _iso(_utcnow())
        mission = {
            "id": mission_id,
            "platform": "reddit",
            "status": "active",
            "created_at": created_at,
            "updated_at": created_at,
            "last_run_at": None,
            "last_result": None,
            **payload,
        }
        mission["next_run_at"] = mission.get("next_run_at") or self._compute_next_run_at(mission, _utcnow())
        self.state.setdefault("missions", {})[mission_id] = mission
        self.save()
        return dict(mission)

    def update_mission(self, mission_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        mission = self.state.get("missions", {}).get(mission_id)
        if not mission:
            return None
        mission.update(updates)
        mission["updated_at"] = _iso(_utcnow())
        if "cadence" in updates and "next_run_at" not in updates:
            mission["next_run_at"] = self._compute_next_run_at(mission, _utcnow())
        self.save()
        return dict(mission)

    def delete_mission(self, mission_id: str) -> bool:
        missions = self.state.get("missions", {})
        if mission_id not in missions:
            return False
        del missions[mission_id]
        self.save()
        return True

    def due_missions(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        current = now or _utcnow()
        due: List[Dict[str, Any]] = []
        for mission in self.state.get("missions", {}).values():
            if mission.get("status") != "active":
                continue
            next_run = _parse_iso(mission.get("next_run_at"))
            if next_run and next_run <= current:
                due.append(dict(mission))
        return sorted(due, key=lambda item: item.get("next_run_at") or "")

    def mark_run_result(self, mission_id: str, result: Dict[str, Any], ran_at: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
        mission = self.state.get("missions", {}).get(mission_id)
        if not mission:
            return None
        completed_at = ran_at or _utcnow()
        mission["last_run_at"] = _iso(completed_at)
        mission["last_result"] = result
        mission["updated_at"] = _iso(completed_at)
        mission["next_run_at"] = self._compute_next_run_at(mission, completed_at)
        if mission.get("cadence", {}).get("type", "once") == "once":
            mission["status"] = "completed"
        self.save()
        return dict(mission)


class RedditMissionScheduler:
    def __init__(
        self,
        store: RedditMissionStore,
        runner: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]],
        *,
        tick_seconds: int = 60,
    ):
        self.store = store
        self.runner = runner
        self.tick_seconds = max(10, int(tick_seconds))
        self._task: Optional[asyncio.Task] = None
        self._stopping = False
        self._run_lock = asyncio.Lock()

    async def start(self):
        if self._task and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(self._worker())

    async def stop(self):
        self._stopping = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def run_due_now(self) -> List[Dict[str, Any]]:
        async with self._run_lock:
            results = []
            for mission in self.store.due_missions():
                result = await self.runner(mission)
                self.store.mark_run_result(mission["id"], result)
                results.append(result)
            return results

    async def _worker(self):
        while not self._stopping:
            try:
                await self.run_due_now()
            except Exception as exc:
                logger.error(f"Reddit mission scheduler tick failed: {exc}")
            await asyncio.sleep(self.tick_seconds)
