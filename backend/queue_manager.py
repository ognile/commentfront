"""
Campaign Queue Manager - Persistent queue with background processing support
Follows the same pattern as proxy_manager.py
"""

import os
import json
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import uuid


class CampaignQueueManager:
    """
    Manages persistent campaign queue with CRUD operations.
    Supports background processing with state recovery on restart.
    """

    MAX_PENDING = 50  # Maximum number of pending campaigns
    MAX_HISTORY = 100  # Maximum number of completed campaigns to keep

    def __init__(self, file_path: str = None):
        self.file_path = file_path or os.getenv(
            "CAMPAIGN_QUEUE_PATH",
            os.path.join(os.path.dirname(__file__), "campaign_queue.json")
        )
        self.campaigns: Dict[str, dict] = {}  # Active queue (pending/processing)
        self.history: List[dict] = []  # Completed/failed campaigns (FIFO)
        self.processor_state = {
            "is_running": False,
            "current_campaign_id": None,
            "last_processed_at": None
        }
        self._lock = asyncio.Lock()
        self.logger = logging.getLogger("CampaignQueueManager")
        self.load()

    def load(self):
        """Load queue from JSON file with recovery for interrupted campaigns."""
        from safe_io import safe_read_json
        data = safe_read_json(self.file_path)
        if data is None:
            self.logger.info(f"Queue file not found at {self.file_path}, starting fresh")
            self.campaigns = {}
            self.history = []
            return

        try:
            self.campaigns = data.get("campaigns", {})
            self.history = data.get("history", [])
            self.processor_state = data.get("processor_state", {
                "is_running": False,
                "current_campaign_id": None,
                "last_processed_at": None
            })

            # Recovery: reset any "processing" campaigns back to "pending"
            # This handles server crashes mid-campaign
            recovered = 0
            for campaign_id, campaign in self.campaigns.items():
                if campaign.get("status") == "processing":
                    self.logger.warning(f"Recovering campaign {campaign_id} from processing to pending")
                    campaign["status"] = "pending"
                    campaign["started_at"] = None
                    recovered += 1

            if recovered > 0:
                self.processor_state["is_running"] = False
                self.processor_state["current_campaign_id"] = None
                self.save()

            self.logger.info(f"Loaded {len(self.campaigns)} active campaigns, {len(self.history)} in history")

        except Exception as e:
            self.logger.error(f"Failed to parse queue file: {e}")
            self.campaigns = {}
            self.history = []

    def save(self):
        """Save queue to JSON file atomically."""
        from safe_io import atomic_write_json
        data = {
            "updated_at": datetime.utcnow().isoformat(),
            "processor_state": self.processor_state,
            "campaigns": self.campaigns,
            "history": self.history
        }
        if not atomic_write_json(self.file_path, data):
            self.logger.error(f"Failed to save queue atomically")

    # =========================================================================
    # CRUD Operations
    # =========================================================================

    def add_campaign(
        self,
        url: str,
        comments: List[str],
        duration_minutes: int,
        username: str,
        filter_tags: Optional[List[str]] = None,
        enable_warmup: bool = True
    ) -> dict:
        """
        Add a new campaign to the queue.

        Args:
            url: Facebook post URL
            comments: List of comment texts
            duration_minutes: Duration to spread comments over
            username: User who created the campaign
            filter_tags: Optional tags to filter sessions (AND logic)
            enable_warmup: If True, profiles will browse feed before commenting

        Returns:
            The created campaign object

        Raises:
            ValueError: If queue is full (50 pending campaigns)
        """
        # Check queue limit
        pending_count = self.count_pending()
        if pending_count >= self.MAX_PENDING:
            raise ValueError(f"Queue is full ({pending_count}/{self.MAX_PENDING}). Wait for campaigns to complete.")

        campaign_id = str(uuid.uuid4())

        campaign = {
            "id": campaign_id,
            "url": url,
            "comments": comments,
            "duration_minutes": duration_minutes,
            "filter_tags": filter_tags or [],
            "enable_warmup": enable_warmup,
            "status": "pending",
            "created_at": datetime.utcnow().isoformat(),
            "created_by": username,
            "started_at": None,
            "completed_at": None,
            "success_count": None,
            "total_count": None,
            "error": None,
            "results": []
        }

        self.campaigns[campaign_id] = campaign
        self.save()
        self.logger.info(f"Added campaign {campaign_id} with {len(comments)} comments")

        return campaign

    def get_campaign(self, campaign_id: str) -> Optional[dict]:
        """Get a campaign by ID (from active queue or history)."""
        # Check active campaigns first
        if campaign_id in self.campaigns:
            return self.campaigns[campaign_id]

        # Check history
        for campaign in self.history:
            if campaign.get("id") == campaign_id:
                return campaign

        return None

    def delete_campaign(self, campaign_id: str) -> bool:
        """
        Delete a pending campaign from the queue.
        Cannot delete campaigns that are processing.

        Returns:
            True if deleted, False if not found or cannot delete
        """
        campaign = self.campaigns.get(campaign_id)
        if not campaign:
            return False

        if campaign.get("status") == "processing":
            self.logger.warning(f"Cannot delete processing campaign {campaign_id}")
            return False

        del self.campaigns[campaign_id]
        self.save()
        self.logger.info(f"Deleted campaign {campaign_id}")
        return True

    # =========================================================================
    # Queue Operations
    # =========================================================================

    def count_pending(self) -> int:
        """Count pending campaigns in the queue."""
        return sum(1 for c in self.campaigns.values() if c.get("status") == "pending")

    def get_next_pending(self) -> Optional[dict]:
        """
        Get the next pending campaign to process (FIFO by created_at).

        Returns:
            Next pending campaign or None if queue is empty
        """
        pending = [
            c for c in self.campaigns.values()
            if c.get("status") == "pending"
        ]

        if not pending:
            return None

        # Sort by created_at to process in order
        pending.sort(key=lambda c: c.get("created_at", ""))
        return pending[0]

    def set_processing(self, campaign_id: str) -> bool:
        """Mark a campaign as processing."""
        if campaign_id not in self.campaigns:
            return False

        self.campaigns[campaign_id]["status"] = "processing"
        self.campaigns[campaign_id]["started_at"] = datetime.utcnow().isoformat()
        self.processor_state["current_campaign_id"] = campaign_id
        self.processor_state["is_running"] = True
        self.save()
        self.logger.info(f"Campaign {campaign_id} started processing")
        return True

    def set_completed(self, campaign_id: str, success_count: int, total_count: int, results: List[dict]) -> bool:
        """Mark a campaign as completed and move to history."""
        if campaign_id not in self.campaigns:
            return False

        campaign = self.campaigns[campaign_id]
        campaign["status"] = "completed"
        campaign["completed_at"] = datetime.utcnow().isoformat()
        campaign["success_count"] = success_count
        campaign["total_count"] = total_count
        campaign["results"] = results

        self._move_to_history(campaign_id)
        self._clear_processor_state()

        self.logger.info(f"Campaign {campaign_id} completed: {success_count}/{total_count}")
        return True

    def set_failed(self, campaign_id: str, error: str) -> bool:
        """Mark a campaign as failed and move to history."""
        if campaign_id not in self.campaigns:
            return False

        campaign = self.campaigns[campaign_id]
        campaign["status"] = "failed"
        campaign["completed_at"] = datetime.utcnow().isoformat()
        campaign["error"] = error

        self._move_to_history(campaign_id)
        self._clear_processor_state()

        self.logger.error(f"Campaign {campaign_id} failed: {error}")
        return True

    def set_cancelled(self, campaign_id: str) -> bool:
        """Mark a campaign as cancelled."""
        if campaign_id not in self.campaigns:
            return False

        campaign = self.campaigns[campaign_id]
        was_processing = campaign.get("status") == "processing"

        campaign["status"] = "cancelled"
        campaign["completed_at"] = datetime.utcnow().isoformat()

        self._move_to_history(campaign_id)

        if was_processing:
            self._clear_processor_state()

        self.logger.info(f"Campaign {campaign_id} cancelled")
        return True

    def _move_to_history(self, campaign_id: str):
        """Move a campaign from active queue to history (FIFO, max 100).
        Campaigns with scheduled auto-retries are protected from trimming."""
        if campaign_id not in self.campaigns:
            return

        campaign = self.campaigns.pop(campaign_id)
        self.history.insert(0, campaign)

        # Keep only last MAX_HISTORY items, but protect campaigns with scheduled auto-retries
        if len(self.history) > self.MAX_HISTORY:
            protected = [c for c in self.history if c.get("auto_retry", {}).get("status") == "scheduled"]
            unprotected = [c for c in self.history if c.get("auto_retry", {}).get("status") != "scheduled"]
            # Trim unprotected first, keep all protected
            unprotected = unprotected[:max(0, self.MAX_HISTORY - len(protected))]
            self.history = sorted(
                protected + unprotected,
                key=lambda c: c.get("completed_at", c.get("created_at", "")),
                reverse=True
            )

        self.save()

    def _clear_processor_state(self):
        """Clear processor state after campaign completes."""
        self.processor_state["is_running"] = False
        self.processor_state["current_campaign_id"] = None
        self.processor_state["last_processed_at"] = datetime.utcnow().isoformat()
        self.save()

    def get_history(self, limit: int = 100) -> List[dict]:
        """Get completed campaign history."""
        return self.history[:min(limit, self.MAX_HISTORY)]

    def add_retry_result(self, campaign_id: str, result: dict) -> Optional[dict]:
        """
        Add a retry result to a campaign in history.

        Args:
            campaign_id: ID of the campaign to update
            result: The new job result to add

        Returns:
            Updated campaign or None if not found
        """
        # Find campaign in history
        for i, campaign in enumerate(self.history):
            if campaign.get("id") == campaign_id:
                # Initialize results array if missing
                if "results" not in campaign:
                    campaign["results"] = []

                # Add the retry result
                campaign["results"].append(result)

                # Recalculate success_count as unique job_indexes with at least one success
                # This ensures retries don't double-count and properly update status
                job_successes = {}
                original_job_count = 0
                for r in campaign["results"]:
                    job_idx = r.get("job_index", 0)
                    # Track the highest job_index to determine original job count
                    if not r.get("is_retry"):
                        original_job_count = max(original_job_count, job_idx + 1)
                    if r.get("success"):
                        job_successes[job_idx] = True

                campaign["success_count"] = len(job_successes)
                # total_count should stay as original number of comments (don't increment for retries)
                if original_job_count > 0:
                    campaign["total_count"] = original_job_count

                # Update status if all original jobs now have a success
                if campaign["success_count"] >= campaign.get("total_count", 0):
                    campaign["status"] = "completed"

                # Mark as having retries
                campaign["has_retries"] = True
                campaign["last_retry_at"] = datetime.utcnow().isoformat()

                self.save()
                self.logger.info(f"Added retry result to campaign {campaign_id}: success={result.get('success')}")

                return campaign

        self.logger.warning(f"Campaign {campaign_id} not found in history for retry")
        return None

    def get_campaign_from_history(self, campaign_id: str) -> Optional[dict]:
        """Get a campaign from history by ID."""
        for campaign in self.history:
            if campaign.get("id") == campaign_id:
                return campaign
        return None

    def add_bulk_retry_results(self, campaign_id: str, results: List[dict]) -> Optional[dict]:
        """
        Add multiple retry results to a campaign in history.

        Args:
            campaign_id: ID of the campaign to update
            results: List of job results to add

        Returns:
            Updated campaign or None if not found
        """
        # Find campaign in history
        for i, campaign in enumerate(self.history):
            if campaign.get("id") == campaign_id:
                # Initialize results array if missing
                if "results" not in campaign:
                    campaign["results"] = []

                # Add all retry results
                campaign["results"].extend(results)

                # Recalculate success_count as unique job_indexes with at least one success
                job_successes = {}
                original_job_count = 0
                for r in campaign["results"]:
                    job_idx = r.get("job_index", 0)
                    # Track the highest job_index to determine original job count
                    if not r.get("is_retry"):
                        original_job_count = max(original_job_count, job_idx + 1)
                    if r.get("success"):
                        job_successes[job_idx] = True

                campaign["success_count"] = len(job_successes)
                # total_count should stay as original number of comments
                if original_job_count > 0:
                    campaign["total_count"] = original_job_count

                # Update status if all original jobs now have a success
                if campaign["success_count"] >= campaign.get("total_count", 0):
                    campaign["status"] = "completed"

                # Mark as having retries
                campaign["has_retries"] = True
                campaign["last_retry_at"] = datetime.utcnow().isoformat()
                campaign["bulk_retry_count"] = campaign.get("bulk_retry_count", 0) + 1

                self.save()
                succeeded = sum(1 for r in results if r.get("success"))
                self.logger.info(
                    f"Added {len(results)} bulk retry results to campaign {campaign_id}: "
                    f"{succeeded}/{len(results)} succeeded"
                )

                return campaign

        self.logger.warning(f"Campaign {campaign_id} not found in history for bulk retry")
        return None

    # =========================================================================
    # State Management
    # =========================================================================

    def get_full_state(self) -> dict:
        """
        Get full queue state for API response.

        Returns:
            Dict with processor_running, current_campaign_id, pending, and history
        """
        # Get pending campaigns sorted by created_at
        pending = [
            c for c in self.campaigns.values()
            if c.get("status") in ("pending", "processing")
        ]
        pending.sort(key=lambda c: c.get("created_at", ""))

        return {
            "processor_running": self.processor_state.get("is_running", False),
            "current_campaign_id": self.processor_state.get("current_campaign_id"),
            "pending_count": len(pending),
            "max_pending": self.MAX_PENDING,
            "pending": pending,
            "history": self.history[:20]  # Send first 20 for initial load
        }

    def is_processor_running(self) -> bool:
        """Check if processor is currently running a campaign."""
        return self.processor_state.get("is_running", False)

    def set_processor_running(self, running: bool, campaign_id: str = None):
        """Update processor running state."""
        self.processor_state["is_running"] = running
        if campaign_id:
            self.processor_state["current_campaign_id"] = campaign_id
        elif not running:
            self.processor_state["current_campaign_id"] = None
        self.save()

    # =========================================================================
    # Job Progress Tracking (for WebSocket updates)
    # =========================================================================

    def update_job_progress(self, campaign_id: str, current_job: int, total_jobs: int, current_profile: str = None):
        """Update progress for a processing campaign (called from processor)."""
        if campaign_id not in self.campaigns:
            return

        campaign = self.campaigns[campaign_id]
        campaign["current_job"] = current_job
        campaign["total_jobs"] = total_jobs
        if current_profile:
            campaign["current_profile"] = current_profile
        # Don't save on every update - too frequent. Processor handles save on completion.

    def save_job_result(self, campaign_id: str, job_index: int, result: dict):
        """
        Save a single job result immediately to disk.
        This ensures results survive deployments/crashes.

        CRITICAL: This is the key to deployment resilience - each job result
        is persisted immediately so we never re-attempt completed jobs.
        """
        if campaign_id not in self.campaigns:
            return False

        campaign = self.campaigns[campaign_id]

        # Initialize results array if missing
        if "results" not in campaign:
            campaign["results"] = []

        # Add job_index to result if not present
        result_with_index = {**result, "job_index": job_index}

        # Check if we already have a result for this job_index (avoid duplicates)
        existing_indexes = {r.get("job_index") for r in campaign["results"]}
        if job_index in existing_indexes:
            self.logger.warning(f"Job {job_index} already has result, skipping duplicate save")
            return False

        campaign["results"].append(result_with_index)

        # Save immediately to disk
        self.save()
        self.logger.info(f"Saved result for job {job_index} in campaign {campaign_id[:8]}... (success={result.get('success')})")
        return True

    def get_completed_job_indexes(self, campaign_id: str) -> set:
        """
        Get set of job indexes that have already been attempted.
        Used on recovery to skip already-processed jobs.
        """
        if campaign_id not in self.campaigns:
            return set()

        campaign = self.campaigns[campaign_id]
        results = campaign.get("results", [])
        return {r.get("job_index") for r in results if r.get("job_index") is not None}

    # =========================================================================
    # Auto-Retry Methods
    # =========================================================================

    RETRY_SCHEDULE = [300, 1800, 7200, 21600]  # 5min, 30min, 2h, 6h
    MAX_RETRY_ROUNDS = 4

    def enable_auto_retry(self, campaign_id: str, failed_jobs: List[dict]):
        """Initialize auto-retry state on a completed campaign with failures.
        failed_jobs: [{"job_index": int, "comment": str, "last_profile": str}]
        """
        campaign = self.get_campaign_from_history(campaign_id)
        if not campaign:
            self.logger.warning(f"Cannot enable auto-retry: campaign {campaign_id} not in history")
            return

        now = datetime.utcnow()
        campaign["auto_retry"] = {
            "status": "scheduled",
            "current_round": 0,
            "max_rounds": self.MAX_RETRY_ROUNDS,
            "next_retry_at": (now + timedelta(seconds=self.RETRY_SCHEDULE[0])).isoformat(),
            "schedule_seconds": self.RETRY_SCHEDULE,
            "failed_jobs": [
                {
                    "job_index": j["job_index"],
                    "comment": j["comment"],
                    "excluded_profiles": [],
                    "last_profile": j.get("last_profile", ""),
                    "exhausted": False
                }
                for j in failed_jobs
            ]
        }
        self.save()
        self.logger.info(f"Auto-retry enabled for campaign {campaign_id[:8]}...: {len(failed_jobs)} failed jobs, first retry at +{self.RETRY_SCHEDULE[0]}s")

    def get_next_due_retry(self) -> Optional[dict]:
        """Get campaign with earliest past-due auto-retry. Returns None if none due."""
        now = datetime.utcnow().isoformat()
        candidates = []
        for campaign in self.history:
            ar = campaign.get("auto_retry")
            if not ar or ar.get("status") != "scheduled":
                continue
            next_at = ar.get("next_retry_at", "")
            if next_at and next_at <= now:
                candidates.append(campaign)

        if not candidates:
            return None

        # Earliest first
        candidates.sort(key=lambda c: c["auto_retry"]["next_retry_at"])
        return candidates[0]

    def record_retry_attempt(self, campaign_id: str, job_index: int, profile: str,
                             round_num: int, success: bool, error: Optional[str],
                             was_restriction: bool):
        """Record a single auto-retry attempt result. Saves to disk immediately."""
        campaign = self.get_campaign_from_history(campaign_id)
        if not campaign:
            return

        # Add result to campaign results
        result = {
            "profile_name": profile,
            "comment": "",
            "success": success,
            "verified": success,
            "method": "auto_retry",
            "error": error,
            "job_index": job_index,
            "is_retry": True,
            "auto_retry_round": round_num,
            "retried_at": datetime.utcnow().isoformat()
        }

        # Find comment text from auto_retry.failed_jobs
        ar = campaign.get("auto_retry", {})
        for fj in ar.get("failed_jobs", []):
            if fj["job_index"] == job_index:
                result["comment"] = fj["comment"]
                # If restriction → exclude profile from future retries for this job
                if was_restriction:
                    if profile not in fj.get("excluded_profiles", []):
                        fj.setdefault("excluded_profiles", []).append(profile)
                # Update last_profile
                fj["last_profile"] = profile
                break

        if "results" not in campaign:
            campaign["results"] = []
        campaign["results"].append(result)

        # Recalculate success_count
        job_successes = {}
        original_job_count = 0
        for r in campaign["results"]:
            idx = r.get("job_index", 0)
            if not r.get("is_retry"):
                original_job_count = max(original_job_count, idx + 1)
            if r.get("success"):
                job_successes[idx] = True
        campaign["success_count"] = len(job_successes)
        if original_job_count > 0:
            campaign["total_count"] = original_job_count
        campaign["has_retries"] = True
        campaign["last_retry_at"] = datetime.utcnow().isoformat()

        self.save()

    def mark_retry_job_exhausted(self, campaign_id: str, job_index: int):
        """Mark a specific retry job as exhausted (all eligible profiles tried)."""
        campaign = self.get_campaign_from_history(campaign_id)
        if not campaign:
            return
        ar = campaign.get("auto_retry", {})
        for fj in ar.get("failed_jobs", []):
            if fj["job_index"] == job_index:
                fj["exhausted"] = True
                break
        self.save()

    def advance_retry_round(self, campaign_id: str):
        """Increment retry round and schedule next retry time."""
        campaign = self.get_campaign_from_history(campaign_id)
        if not campaign:
            return
        ar = campaign.get("auto_retry")
        if not ar:
            return

        ar["current_round"] = ar.get("current_round", 0) + 1
        round_idx = ar["current_round"]

        if round_idx >= ar.get("max_rounds", self.MAX_RETRY_ROUNDS):
            self.complete_auto_retry(campaign_id, "exhausted")
            return

        schedule = ar.get("schedule_seconds", self.RETRY_SCHEDULE)
        delay = schedule[min(round_idx, len(schedule) - 1)]
        ar["next_retry_at"] = (datetime.utcnow() + timedelta(seconds=delay)).isoformat()
        ar["status"] = "scheduled"
        self.save()
        self.logger.info(f"Auto-retry round {round_idx} scheduled for campaign {campaign_id[:8]}... in {delay}s")

    def complete_auto_retry(self, campaign_id: str, final_status: str = "completed"):
        """Mark auto-retry as completed or exhausted."""
        campaign = self.get_campaign_from_history(campaign_id)
        if not campaign:
            return
        ar = campaign.get("auto_retry")
        if not ar:
            return
        ar["status"] = final_status
        ar["completed_at"] = datetime.utcnow().isoformat()

        # Update campaign status if all jobs now succeeded
        if campaign.get("success_count", 0) >= campaign.get("total_count", 0):
            campaign["status"] = "completed"

        self.save()
        self.logger.info(f"Auto-retry {final_status} for campaign {campaign_id[:8]}...")
