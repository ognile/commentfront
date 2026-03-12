from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from safe_io import atomic_write_json, safe_read_json


def _utc_iso(value: Optional[datetime] = None) -> str:
    dt = value or datetime.now(timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _clone(value: Any) -> Any:
    return json.loads(json.dumps(value))


class RedditExecutionStore:
    def __init__(self, file_path: Optional[str] = None):
        self.file_path = file_path or self._default_path()
        self._lock = threading.RLock()
        self.state = self._load()

    def _default_path(self) -> str:
        configured = os.getenv("REDDIT_EXECUTIONS_PATH")
        if configured:
            return configured
        data_dir = os.getenv("DATA_DIR", "/data")
        preferred = os.path.join(data_dir, "reddit_executions_state.json")
        try:
            os.makedirs(os.path.dirname(preferred), exist_ok=True)
            return preferred
        except Exception:
            return os.path.join(os.path.dirname(__file__), "reddit_executions_state.json")

    def _empty_state(self) -> Dict[str, Any]:
        return {
            "updated_at": _utc_iso(),
            "runs": {},
        }

    def _load(self) -> Dict[str, Any]:
        data = safe_read_json(self.file_path)
        if not isinstance(data, dict):
            return self._empty_state()
        baseline = self._empty_state()
        baseline.update(data)
        baseline.setdefault("runs", {})
        return baseline

    def save(self) -> bool:
        with self._lock:
            self.state["updated_at"] = _utc_iso()
            return atomic_write_json(self.file_path, self.state)

    def upsert_run(self, run: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            payload = _clone(run)
            payload["updated_at"] = _utc_iso()
            self.state.setdefault("runs", {})[payload["run_id"]] = payload
            self.save()
            return _clone(payload)

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            item = self.state.get("runs", {}).get(run_id)
            return _clone(item) if item else None

