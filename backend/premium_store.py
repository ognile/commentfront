"""
Persistent store for premium automation configs, rules, and run state.
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from safe_io import atomic_write_json, safe_read_json
from premium_verify import initialize_verification_state, register_progress

logger = logging.getLogger("PremiumStore")

DEFAULT_TIMEZONE = "America/New_York"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso() -> str:
    return _utc_now().isoformat().replace("+00:00", "Z")


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _normalize_profile_name(profile_name: str) -> str:
    return str(profile_name or "").strip().lower()


class PremiumStore:
    def __init__(self, file_path: Optional[str] = None):
        self.file_path = file_path or self._default_path()
        self._lock = threading.RLock()
        self.state = self._load()

    def _default_path(self) -> str:
        configured = os.getenv("PREMIUM_AUTOMATION_STATE_PATH")
        if configured:
            return configured

        data_dir = os.getenv("DATA_DIR", "/data")
        preferred = os.path.join(data_dir, "premium_automation_state.json")
        try:
            os.makedirs(os.path.dirname(preferred), exist_ok=True)
            return preferred
        except Exception:
            return os.path.join(os.path.dirname(__file__), "premium_automation_state.json")

    def _empty_state(self) -> Dict:
        return {
            "updated_at": _utc_iso(),
            "profile_configs": {},
            "rules_snapshot": None,
            "runs": {},
            "run_order": [],
            "scheduler": {
                "enabled": True,
                "is_running": False,
                "last_tick_at": None,
                "last_error": None,
                "last_processed_count": 0,
            },
        }

    def _load(self) -> Dict:
        data = safe_read_json(self.file_path)
        if not isinstance(data, dict):
            return self._empty_state()

        baseline = self._empty_state()
        baseline.update(data)
        baseline.setdefault("profile_configs", {})
        baseline.setdefault("runs", {})
        baseline.setdefault("run_order", [])
        baseline.setdefault("scheduler", self._empty_state()["scheduler"])
        return baseline

    def save(self) -> bool:
        with self._lock:
            self.state["updated_at"] = _utc_iso()
            return atomic_write_json(self.file_path, self.state)

    # ------------------------------------------------------------------
    # profile config
    # ------------------------------------------------------------------
    def upsert_profile_config(self, profile_name: str, payload: Dict) -> Dict:
        key = _normalize_profile_name(profile_name)
        with self._lock:
            self.state["profile_configs"][key] = {
                **payload,
                "profile_name": profile_name,
                "updated_at": _utc_iso(),
            }
            self.save()
            return self.state["profile_configs"][key]

    def get_profile_config(self, profile_name: str) -> Optional[Dict]:
        key = _normalize_profile_name(profile_name)
        with self._lock:
            return self.state.get("profile_configs", {}).get(key)

    def list_profile_configs(self) -> List[Dict]:
        with self._lock:
            configs = list(self.state.get("profile_configs", {}).values())
        configs.sort(key=lambda c: str(c.get("profile_name", "")).lower())
        return configs

    def remember_recent_caption(self, profile_name: str, caption: str, *, limit: int = 20) -> Optional[Dict]:
        key = _normalize_profile_name(profile_name)
        text = str(caption or "").strip()
        if not text:
            return None
        with self._lock:
            config = self.state.get("profile_configs", {}).get(key)
            if not config:
                return None
            character_profile = config.setdefault("character_profile", {})
            existing = [str(item).strip() for item in (character_profile.get("recent_captions") or []) if str(item).strip()]
            deduped = [text]
            text_key = text.lower()
            for item in existing:
                if item.lower() == text_key:
                    continue
                deduped.append(item)
            character_profile["recent_captions"] = deduped[: max(1, int(limit))]
            config["updated_at"] = _utc_iso()
            self.save()
            return config

    # ------------------------------------------------------------------
    # rules snapshot
    # ------------------------------------------------------------------
    def set_rules_snapshot(self, snapshot: Dict) -> Dict:
        with self._lock:
            self.state["rules_snapshot"] = snapshot
            self.save()
            return snapshot

    def get_rules_snapshot(self) -> Optional[Dict]:
        with self._lock:
            return self.state.get("rules_snapshot")

    # ------------------------------------------------------------------
    # runs
    # ------------------------------------------------------------------
    def _build_cycle_plan(self, run_spec: Dict, seed: str) -> List[Dict]:
        schedule = run_spec.get("schedule", {})
        feed_plan = run_spec.get("feed_plan", {})

        total_posts = int(feed_plan.get("total_posts", 0))
        character_posts = int(feed_plan.get("character_posts", 0))
        ambient_posts = int(feed_plan.get("ambient_posts", 0))

        post_kinds = (["character"] * character_posts) + (["ambient"] * ambient_posts)
        if len(post_kinds) != total_posts:
            raise ValueError("invalid feed plan: character_posts + ambient_posts must equal total_posts")

        rnd = random.Random(seed)
        rnd.shuffle(post_kinds)

        timezone_name = str(schedule.get("timezone") or DEFAULT_TIMEZONE)
        try:
            local_tz = ZoneInfo(timezone_name)
        except Exception:
            local_tz = ZoneInfo(DEFAULT_TIMEZONE)
            timezone_name = DEFAULT_TIMEZONE

        start_at = _parse_iso(schedule.get("start_at")) or _utc_now()
        if start_at.tzinfo is None:
            start_at = start_at.replace(tzinfo=timezone.utc)
        start_local = start_at.astimezone(local_tz)

        duration_days = max(1, int(schedule.get("duration_days", 1)))
        random_windows = schedule.get("random_windows") or [{"start_hour": 8, "end_hour": 22}]
        if not isinstance(random_windows, list) or not random_windows:
            random_windows = [{"start_hour": 8, "end_hour": 22}]

        cycles: List[Dict] = []

        for idx in range(total_posts):
            day_offset = min(duration_days - 1, int((idx * duration_days) / max(1, total_posts)))
            local_date = (start_local + timedelta(days=day_offset)).date()

            window = random_windows[idx % len(random_windows)]
            wh_start = max(0, min(23, int(window.get("start_hour", 8))))
            wh_end = max(wh_start + 1, min(24, int(window.get("end_hour", 22))))

            hour = rnd.randint(wh_start, wh_end - 1)
            minute = rnd.randint(0, 59)
            second = rnd.randint(0, 59)

            scheduled_local = datetime(
                year=local_date.year,
                month=local_date.month,
                day=local_date.day,
                hour=hour,
                minute=minute,
                second=second,
                tzinfo=local_tz,
            )

            # Ensure first cycle is not in the past versus start.
            if idx == 0 and scheduled_local < start_local:
                scheduled_local = start_local + timedelta(seconds=15)

            scheduled_utc = scheduled_local.astimezone(timezone.utc)
            cycles.append(
                {
                    "index": idx,
                    "post_kind": post_kinds[idx],
                    "scheduled_at": scheduled_utc.isoformat().replace("+00:00", "Z"),
                    "status": "pending",
                    "attempts": 0,
                    "started_at": None,
                    "completed_at": None,
                    "error": None,
                    "results": [],
                }
            )

        cycles.sort(key=lambda c: c["scheduled_at"])
        return cycles

    def _next_pending_cycle(self, run: Dict) -> Optional[Dict]:
        pending = [c for c in run.get("cycles", []) if c.get("status") == "pending"]
        if not pending:
            return None
        pending.sort(key=lambda c: c.get("scheduled_at") or "")
        return pending[0]

    def _has_running_cycle(self, run: Dict) -> bool:
        return any(str(c.get("status")) == "running" for c in run.get("cycles", []))

    def _profile_key_for_run(self, run: Dict) -> str:
        run_spec = run.get("run_spec") or {}
        return _normalize_profile_name(str(run_spec.get("profile_name") or ""))

    def _list_profile_runs_locked(self, profile_key: str) -> List[Dict]:
        runs = []
        for run_id in self.state.get("run_order", []):
            run = self.state.get("runs", {}).get(run_id)
            if not run:
                continue
            if self._profile_key_for_run(run) != profile_key:
                continue
            runs.append(run)
        return runs

    def _find_profile_active_run_locked(self, profile_key: str) -> Optional[Dict]:
        runs = self._list_profile_runs_locked(profile_key)
        for run in runs:
            if run.get("status") in ("scheduled", "in_progress"):
                return run
        return None

    def _list_profile_queued_runs_locked(self, profile_key: str) -> List[Dict]:
        runs = self._list_profile_runs_locked(profile_key)
        return [run for run in runs if run.get("status") == "queued"]

    def _recompute_profile_queue_locked(self, profile_key: str) -> None:
        queued_runs = sorted(
            self._list_profile_queued_runs_locked(profile_key),
            key=lambda run: str(run.get("created_at") or ""),
        )
        active = self._find_profile_active_run_locked(profile_key)
        blocker_id = str(active.get("id")) if active else None
        for position, queued_run in enumerate(queued_runs, start=1):
            queued_run["queue_position"] = position
            queued_run["blocked_by_run_id"] = blocker_id
            queued_run["admission_policy"] = "queue_behind"
            blocker_id = queued_run.get("id")

    def _promote_next_queued_run_locked(self, profile_key: str) -> Optional[Dict]:
        queued_runs = sorted(
            self._list_profile_queued_runs_locked(profile_key),
            key=lambda run: str(run.get("created_at") or ""),
        )
        if not queued_runs:
            return None
        next_run = queued_runs[0]
        next_cycle = self._next_pending_cycle(next_run)
        next_run["status"] = "scheduled"
        next_run["blocked_by_run_id"] = None
        next_run["queue_position"] = 0
        next_run["next_execute_at"] = next_cycle.get("scheduled_at") if next_cycle else None
        next_run["updated_at"] = _utc_iso()
        self._recompute_profile_queue_locked(profile_key)
        return next_run

    def _create_run_locked(
        self,
        *,
        run_spec: Dict,
        created_by: str,
        status: str,
        blocked_by_run_id: Optional[str],
        queue_position: int,
        admission_policy: str,
    ) -> Dict:
        run_id = str(uuid.uuid4())
        cycles = self._build_cycle_plan(run_spec, seed=run_id)
        next_cycle = cycles[0] if cycles else None

        run = {
            "id": run_id,
            "created_at": _utc_iso(),
            "created_by": created_by,
            "updated_at": _utc_iso(),
            "status": status,
            "error": None,
            "run_spec": run_spec,
            "cycles": cycles,
            "next_execute_at": next_cycle.get("scheduled_at") if (next_cycle and status != "queued") else None,
            "verification_state": initialize_verification_state(run_spec),
            "events": [],
            "evidence": [],
            "pass_matrix": {},
            "blocked_by_run_id": blocked_by_run_id,
            "queue_position": int(queue_position),
            "admission_policy": admission_policy,
        }

        self.state["runs"][run_id] = run
        self.state["run_order"].insert(0, run_id)
        return run

    def create_run(self, *, run_spec: Dict, created_by: str) -> Dict:
        with self._lock:
            run = self._create_run_locked(
                run_spec=run_spec,
                created_by=created_by,
                status="scheduled",
                blocked_by_run_id=None,
                queue_position=0,
                admission_policy="direct",
            )
            self.save()
            return run

    def enqueue_or_create_run(self, *, run_spec: Dict, created_by: str) -> Dict:
        with self._lock:
            profile_key = _normalize_profile_name(str((run_spec or {}).get("profile_name") or ""))
            active_run = self._find_profile_active_run_locked(profile_key)
            queued_runs = self._list_profile_queued_runs_locked(profile_key)

            if active_run:
                blocked_by_run_id = str(queued_runs[-1].get("id")) if queued_runs else str(active_run.get("id"))
                run = self._create_run_locked(
                    run_spec=run_spec,
                    created_by=created_by,
                    status="queued",
                    blocked_by_run_id=blocked_by_run_id,
                    queue_position=len(queued_runs) + 1,
                    admission_policy="queue_behind",
                )
                self._recompute_profile_queue_locked(profile_key)
            else:
                run = self._create_run_locked(
                    run_spec=run_spec,
                    created_by=created_by,
                    status="scheduled",
                    blocked_by_run_id=None,
                    queue_position=0,
                    admission_policy="direct",
                )
            self.save()
            return run

    def list_runs(self, *, limit: int = 100, status: Optional[str] = None) -> List[Dict]:
        with self._lock:
            run_ids = list(self.state.get("run_order", []))
            runs: List[Dict] = []
            for run_id in run_ids:
                run = self.state.get("runs", {}).get(run_id)
                if not run:
                    continue
                if status and run.get("status") != status:
                    continue
                runs.append(run)
                if len(runs) >= limit:
                    break
            return runs

    def get_run(self, run_id: str) -> Optional[Dict]:
        with self._lock:
            run = self.state.get("runs", {}).get(run_id)
            if not run:
                return None
            return run

    def append_event(self, run_id: str, event_type: str, data: Dict) -> None:
        with self._lock:
            run = self.state.get("runs", {}).get(run_id)
            if not run:
                return
            run.setdefault("events", []).append(
                {
                    "timestamp": _utc_iso(),
                    "type": event_type,
                    "data": data,
                }
            )
            run["updated_at"] = _utc_iso()
            self.save()

    def append_evidence(self, run_id: str, evidence: Dict) -> None:
        with self._lock:
            run = self.state.get("runs", {}).get(run_id)
            if not run:
                return
            run.setdefault("evidence", []).append(evidence)
            run["updated_at"] = _utc_iso()
            self.save()

    def register_verification(
        self,
        *,
        run_id: str,
        key: str,
        count: int,
        post_kind: Optional[str] = None,
        evidence: Optional[Dict] = None,
    ) -> Optional[Dict]:
        with self._lock:
            run = self.state.get("runs", {}).get(run_id)
            if not run:
                return None
            state = run.setdefault("verification_state", initialize_verification_state(run.get("run_spec", {})))
            run["verification_state"] = register_progress(
                state,
                key=key,
                count=count,
                post_kind=post_kind,
                evidence=evidence,
            )
            run["updated_at"] = _utc_iso()
            self.save()
            return run["verification_state"]

    def set_cycle_status(
        self,
        *,
        run_id: str,
        cycle_index: int,
        status: str,
        error: Optional[str] = None,
        result: Optional[Dict] = None,
    ) -> Optional[Dict]:
        with self._lock:
            run = self.state.get("runs", {}).get(run_id)
            if not run:
                return None

            cycle = None
            for c in run.get("cycles", []):
                if int(c.get("index", -1)) == int(cycle_index):
                    cycle = c
                    break
            if not cycle:
                return None

            cycle["status"] = status
            if status == "running":
                cycle["attempts"] = int(cycle.get("attempts", 0)) + 1
                cycle["started_at"] = _utc_iso()
                cycle["error"] = None
            if status in ("success", "failed"):
                cycle["completed_at"] = _utc_iso()
                cycle["error"] = error
            if result is not None:
                cycle.setdefault("results", []).append(result)

            next_cycle = self._next_pending_cycle(run)
            run["next_execute_at"] = next_cycle.get("scheduled_at") if next_cycle else None
            run["updated_at"] = _utc_iso()
            self.save()
            return cycle

    def set_run_status(self, run_id: str, status: str, error: Optional[str] = None, pass_matrix: Optional[Dict] = None) -> Optional[Dict]:
        with self._lock:
            run = self.state.get("runs", {}).get(run_id)
            if not run:
                return None
            previous_status = str(run.get("status") or "")
            run["status"] = status
            run["error"] = error
            run["updated_at"] = _utc_iso()
            if status in ("completed", "failed", "cancelled"):
                run["completed_at"] = _utc_iso()
            if pass_matrix is not None:
                run["pass_matrix"] = pass_matrix
            profile_key = self._profile_key_for_run(run)
            if status in ("completed", "failed", "cancelled"):
                if previous_status != "queued":
                    self._promote_next_queued_run_locked(profile_key)
            self._recompute_profile_queue_locked(profile_key)
            self.save()
            return run

    def cancel_run(self, run_id: str, actor: str) -> Optional[Dict]:
        with self._lock:
            run = self.state.get("runs", {}).get(run_id)
            if not run:
                return None
            if run.get("status") in ("completed", "failed", "cancelled"):
                return run
            cancelled_at = _utc_iso()
            for cycle in run.get("cycles", []):
                if str(cycle.get("status")) == "running":
                    cycle["status"] = "cancelled"
                    cycle["completed_at"] = cancelled_at
                    cycle["error"] = f"cancelled by {actor}"
            run["status"] = "cancelled"
            run["error"] = f"cancelled by {actor}"
            run["updated_at"] = cancelled_at
            run["completed_at"] = cancelled_at
            run["next_execute_at"] = None
            profile_key = self._profile_key_for_run(run)
            self._promote_next_queued_run_locked(profile_key)
            self._recompute_profile_queue_locked(profile_key)
            self.save()
            return run

    def get_due_cycles(self, now: Optional[datetime] = None) -> List[Tuple[str, int, str]]:
        current = now or _utc_now()
        due: List[Tuple[str, int, str]] = []

        with self._lock:
            for run_id in self.state.get("run_order", []):
                run = self.state.get("runs", {}).get(run_id)
                if not run:
                    continue
                if run.get("status") not in ("scheduled", "in_progress"):
                    continue
                if self._has_running_cycle(run):
                    continue
                next_cycle = self._next_pending_cycle(run)
                if not next_cycle:
                    continue
                when = _parse_iso(next_cycle.get("scheduled_at"))
                if not when:
                    continue
                if when <= current:
                    due.append((run_id, int(next_cycle["index"]), next_cycle.get("scheduled_at")))

        due.sort(key=lambda x: x[2])
        return due

    # ------------------------------------------------------------------
    # scheduler metadata
    # ------------------------------------------------------------------
    def update_scheduler_state(self, **patch: Dict) -> Dict:
        with self._lock:
            self.state.setdefault("scheduler", {})
            self.state["scheduler"].update(patch)
            self.save()
            return self.state["scheduler"]

    def get_scheduler_state(self) -> Dict:
        with self._lock:
            return dict(self.state.get("scheduler", {}))


_store_singleton: Optional[PremiumStore] = None


def get_premium_store() -> PremiumStore:
    global _store_singleton
    if _store_singleton is None:
        _store_singleton = PremiumStore()
    return _store_singleton
