"""
Campaign Queue Manager - Persistent queue with background processing support
Follows the same pattern as proxy_manager.py
"""

import os
import json
import logging
import asyncio
from datetime import datetime
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
        if not os.path.exists(self.file_path):
            self.logger.info(f"Queue file not found at {self.file_path}, starting fresh")
            self.campaigns = {}
            self.history = []
            return

        try:
            with open(self.file_path, "r") as f:
                data = json.load(f)
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
        """Save queue to JSON file."""
        try:
            data = {
                "updated_at": datetime.utcnow().isoformat(),
                "processor_state": self.processor_state,
                "campaigns": self.campaigns,
                "history": self.history
            }
            with open(self.file_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            self.logger.error(f"Failed to save queue: {e}")

    # =========================================================================
    # CRUD Operations
    # =========================================================================

    def add_campaign(
        self,
        url: str,
        comments: List[str],
        duration_minutes: int,
        username: str,
        filter_tags: Optional[List[str]] = None
    ) -> dict:
        """
        Add a new campaign to the queue.

        Args:
            url: Facebook post URL
            comments: List of comment texts
            duration_minutes: Duration to spread comments over
            username: User who created the campaign
            filter_tags: Optional tags to filter sessions (AND logic)

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
        """Move a campaign from active queue to history (FIFO, max 100)."""
        if campaign_id not in self.campaigns:
            return

        campaign = self.campaigns.pop(campaign_id)
        self.history.insert(0, campaign)

        # Keep only last MAX_HISTORY items
        if len(self.history) > self.MAX_HISTORY:
            self.history = self.history[:self.MAX_HISTORY]

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
