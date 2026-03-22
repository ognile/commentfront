"""Thin supabase CRUD client for community simulation tables."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from forensics import SupabaseForensicsStore, get_forensics_store

logger = logging.getLogger("CommunityStore")


class CommunityStore:
    """Wraps SupabaseForensicsStore for community-specific operations."""

    def __init__(self, store: Optional[SupabaseForensicsStore] = None):
        self._store = store or get_forensics_store()

    @property
    def enabled(self) -> bool:
        return self._store.enabled

    # ── personas ──

    async def upsert_personas(self, rows: List[Dict[str, Any]]) -> None:
        if not rows:
            return
        headers = self._store._headers(prefer_return=True)
        headers["Prefer"] = "return=representation,resolution=merge-duplicates"
        await self._store._request(
            "POST",
            "/rest/v1/community_personas",
            json_payload=rows,
            headers=headers,
        )
        logger.info(f"upserted {len(rows)} community personas")

    async def list_personas(self) -> List[Dict[str, Any]]:
        return await self._store.select_rows("community_personas", order="profile_name.asc")

    async def get_persona(self, profile_name: str) -> Optional[Dict[str, Any]]:
        rows = await self._store.select_rows(
            "community_personas",
            filters={"profile_name": profile_name},
            limit=1,
        )
        return rows[0] if rows else None

    # ── plans ──

    async def create_plan(self, name: str, phase: str, config: dict) -> Dict[str, Any]:
        row = await self._store.insert_row("community_plans", {
            "name": name,
            "phase": phase,
            "config": config,
        })
        logger.info(f"created community plan: {row.get('id') if row else 'unknown'} ({phase})")
        return row or {}

    async def list_plans(self) -> List[Dict[str, Any]]:
        return await self._store.select_rows("community_plans", order="created_at.desc")

    async def get_plan(self, plan_id: str) -> Optional[Dict[str, Any]]:
        rows = await self._store.select_rows(
            "community_plans",
            filters={"id": plan_id},
            limit=1,
        )
        return rows[0] if rows else None

    async def update_plan_status(self, plan_id: str, status: str) -> None:
        await self._store.update_rows(
            "community_plans",
            filters={"id": plan_id},
            payload={"status": status, "updated_at": _now_iso()},
        )
        logger.info(f"plan {plan_id} status → {status}")

    # ── tasks ──

    async def insert_tasks(self, rows: List[Dict[str, Any]]) -> None:
        if not rows:
            return
        await self._store.bulk_insert("community_tasks", rows)
        logger.info(f"inserted {len(rows)} community tasks")

    async def get_due_tasks(self, limit: int = 3) -> List[Dict[str, Any]]:
        """Fetch pending tasks whose scheduled_at <= now."""
        now = _now_iso()
        return await self._store.select_rows(
            "community_tasks",
            filters={
                "status": "pending",
                "scheduled_at": ("lte", now),
            },
            order="scheduled_at.asc",
            limit=limit,
        )

    async def claim_task(self, task_id: str) -> None:
        """Mark task as running, increment attempts."""
        # Fetch current attempts first
        rows = await self._store.select_rows(
            "community_tasks",
            filters={"id": task_id},
            select="attempts",
            limit=1,
        )
        current_attempts = rows[0]["attempts"] if rows else 0
        await self._store.update_rows(
            "community_tasks",
            filters={"id": task_id},
            payload={
                "status": "running",
                "attempts": current_attempts + 1,
                "started_at": _now_iso(),
            },
        )

    async def complete_task(self, task_id: str, result: Optional[dict] = None) -> None:
        await self._store.update_rows(
            "community_tasks",
            filters={"id": task_id},
            payload={
                "status": "completed",
                "completed_at": _now_iso(),
                "result": result or {},
            },
        )

    async def fail_task(self, task_id: str, error: str) -> None:
        """Fail task. If attempts < max_attempts, reschedule as pending."""
        rows = await self._store.select_rows(
            "community_tasks",
            filters={"id": task_id},
            select="attempts,max_attempts",
            limit=1,
        )
        if not rows:
            return
        task = rows[0]
        if task["attempts"] < task["max_attempts"]:
            # Reschedule: set back to pending (will be picked up next tick)
            await self._store.update_rows(
                "community_tasks",
                filters={"id": task_id},
                payload={"status": "pending", "error": error},
            )
            logger.info(f"task {task_id} rescheduled (attempt {task['attempts']}/{task['max_attempts']})")
        else:
            await self._store.update_rows(
                "community_tasks",
                filters={"id": task_id},
                payload={
                    "status": "failed",
                    "completed_at": _now_iso(),
                    "error": error,
                },
            )
            logger.warning(f"task {task_id} permanently failed after {task['attempts']} attempts: {error[:200]}")

    async def list_tasks(
        self,
        *,
        plan_id: Optional[str] = None,
        status: Optional[str] = None,
        profile_name: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        filters: Dict[str, Any] = {}
        if plan_id:
            filters["plan_id"] = plan_id
        if status:
            filters["status"] = status
        if profile_name:
            filters["profile_name"] = profile_name
        return await self._store.select_rows(
            "community_tasks",
            filters=filters,
            order="scheduled_at.asc",
            limit=limit,
        )

    async def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        rows = await self._store.select_rows(
            "community_tasks",
            filters={"id": task_id},
            limit=1,
        )
        return rows[0] if rows else None

    async def get_task_counts(self, plan_id: Optional[str] = None) -> Dict[str, int]:
        """Get task counts by status."""
        filters: Dict[str, Any] = {}
        if plan_id:
            filters["plan_id"] = plan_id
        tasks = await self._store.select_rows(
            "community_tasks",
            filters=filters,
            select="status",
        )
        counts: Dict[str, int] = {"pending": 0, "running": 0, "completed": 0, "failed": 0, "skipped": 0}
        for t in tasks:
            s = t.get("status", "")
            if s in counts:
                counts[s] += 1
        counts["total"] = len(tasks)
        return counts

    # ── execution log ──

    async def log_execution(
        self,
        task_id: str,
        attempt: int,
        started_at: str,
        *,
        completed_at: Optional[str] = None,
        success: Optional[bool] = None,
        error: Optional[str] = None,
        result: Optional[dict] = None,
        screenshot_url: Optional[str] = None,
    ) -> None:
        await self._store.insert_row("community_execution_log", {
            "task_id": task_id,
            "attempt": attempt,
            "started_at": started_at,
            "completed_at": completed_at,
            "success": success,
            "error": error,
            "result": result,
            "screenshot_url": screenshot_url,
        })

    # ── screenshot upload ──

    async def upload_screenshot(self, task_id: str, attempt: int, image_data: bytes) -> str:
        """Upload screenshot to supabase storage and return public URL."""
        path = f"community/{task_id}/attempt_{attempt}.png"
        await self._store.upload_artifact(path, image_data, "image/png")
        url = f"{self._store.base_url}/storage/v1/object/public/forensics/{path}"
        return url


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


_community_store: Optional[CommunityStore] = None


def get_community_store() -> CommunityStore:
    global _community_store
    if _community_store is None:
        _community_store = CommunityStore()
    return _community_store
