"""
Shared campaign draft manager.

Drafts are persisted to JSON (Railway volume in production) and are shared
across authenticated users.
"""

import os
import uuid
import logging
from datetime import datetime
from typing import Dict, List, Optional

from safe_io import atomic_write_json, safe_read_json
from queue_manager import canonicalize_campaign_jobs


logger = logging.getLogger("DraftManager")


class DraftManager:
    """Manage shared campaign drafts with atomic persistence."""

    def __init__(self, file_path: Optional[str] = None):
        self.file_path = file_path or self._default_path()
        self.drafts: Dict[str, dict] = {}
        self.load()

    def _default_path(self) -> str:
        if os.getenv("DRAFTS_PATH"):
            return os.getenv("DRAFTS_PATH", "")

        data_dir = os.getenv("DATA_DIR", "/data")
        preferred = os.path.join(data_dir, "campaign_drafts.json")
        try:
            os.makedirs(os.path.dirname(preferred), exist_ok=True)
            return preferred
        except Exception:
            fallback_dir = os.path.dirname(__file__)
            return os.path.join(fallback_dir, "campaign_drafts.json")

    def load(self):
        data = safe_read_json(self.file_path)
        if data is None:
            self.drafts = {}
            logger.info(f"Draft file not found at {self.file_path}, starting fresh")
            return
        try:
            self.drafts = data.get("drafts", {})
            logger.info(f"Loaded {len(self.drafts)} shared drafts")
        except Exception as exc:
            logger.error(f"Failed to load drafts: {exc}")
            self.drafts = {}

    def save(self) -> bool:
        payload = {
            "updated_at": datetime.utcnow().isoformat(),
            "drafts": self.drafts,
        }
        ok = atomic_write_json(self.file_path, payload)
        if not ok:
            logger.error("Failed to save drafts")
        return ok

    def list_drafts(self) -> List[dict]:
        drafts = list(self.drafts.values())
        drafts.sort(key=lambda d: d.get("updated_at", ""), reverse=True)
        return drafts

    def get_draft(self, draft_id: str) -> Optional[dict]:
        return self.drafts.get(draft_id)

    def create_draft(
        self,
        *,
        url: str,
        comments: Optional[List[str]],
        jobs: Optional[List[dict]],
        duration_minutes: int,
        filter_tags: Optional[List[str]],
        enable_warmup: bool,
        username: str,
    ) -> dict:
        now = datetime.utcnow().isoformat()
        canonical_jobs = self._normalize_jobs(comments=comments, jobs=jobs)
        legacy_comments = self._normalize_comments(comments, canonical_jobs)

        draft_id = str(uuid.uuid4())
        draft = {
            "id": draft_id,
            "url": url,
            "comments": legacy_comments,
            "jobs": canonical_jobs,
            "duration_minutes": duration_minutes,
            "filter_tags": filter_tags or [],
            "enable_warmup": bool(enable_warmup),
            "created_at": now,
            "updated_at": now,
            "created_by": username,
            "updated_by": username,
        }
        self.drafts[draft_id] = draft
        self.save()
        return draft

    def update_draft(
        self,
        draft_id: str,
        *,
        url: str,
        comments: Optional[List[str]],
        jobs: Optional[List[dict]],
        duration_minutes: int,
        filter_tags: Optional[List[str]],
        enable_warmup: bool,
        username: str,
    ) -> Optional[dict]:
        existing = self.drafts.get(draft_id)
        if not existing:
            return None

        canonical_jobs = self._normalize_jobs(comments=comments, jobs=jobs)
        legacy_comments = self._normalize_comments(comments, canonical_jobs)

        existing.update(
            {
                "url": url,
                "comments": legacy_comments,
                "jobs": canonical_jobs,
                "duration_minutes": duration_minutes,
                "filter_tags": filter_tags or [],
                "enable_warmup": bool(enable_warmup),
                "updated_at": datetime.utcnow().isoformat(),
                "updated_by": username,
            }
        )
        self.save()
        return existing

    def delete_draft(self, draft_id: str) -> Optional[dict]:
        draft = self.drafts.pop(draft_id, None)
        if draft:
            self.save()
        return draft

    @staticmethod
    def _normalize_jobs(comments: Optional[List[str]], jobs: Optional[List[dict]]) -> List[dict]:
        """
        Drafts may be incomplete while users are typing.
        Keep jobs empty until publish-time validation if no usable comments/jobs exist yet.
        """
        jobs = jobs or []
        cleaned_comments = [str(c).strip() for c in (comments or []) if str(c).strip()]
        if not jobs and not cleaned_comments:
            return []
        return canonicalize_campaign_jobs(comments=cleaned_comments, jobs=jobs or None)

    @staticmethod
    def _normalize_comments(comments: Optional[List[str]], canonical_jobs: List[dict]) -> List[str]:
        if canonical_jobs:
            return [str(job.get("text", "")) for job in canonical_jobs]
        return [str(c).strip() for c in (comments or []) if str(c).strip()]
