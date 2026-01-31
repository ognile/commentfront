"""
Appeal Scheduler - Automatically verifies and appeals restricted profiles on a schedule.
Runs every 24h by default. State persisted to survive deployments.
"""

import asyncio
import json
import os
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

logger = logging.getLogger("AppealScheduler")

DEFAULT_INTERVAL_HOURS = 24
MAX_HISTORY = 10


class AppealScheduler:
    def __init__(self, state_file: str = None):
        self.state_file = state_file or os.getenv(
            "APPEAL_SCHEDULER_STATE_PATH",
            os.path.join(os.path.dirname(__file__), "appeal_scheduler_state.json")
        )
        self.state = self._load_state()
        self._task: Optional[asyncio.Task] = None
        self._stop = False

    def _load_state(self) -> dict:
        from safe_io import safe_read_json
        data = safe_read_json(self.state_file)
        if data:
            return data
        # Default state
        return {
            "enabled": True,
            "interval_hours": DEFAULT_INTERVAL_HOURS,
            "last_run_at": None,
            "next_run_at": None,
            "last_results": None,
            "run_history": []
        }

    def _save_state(self):
        from safe_io import atomic_write_json
        if not atomic_write_json(self.state_file, self.state):
            logger.error("Failed to save appeal scheduler state")

    async def start(self):
        """Start the scheduler background loop."""
        if self._task and not self._task.done():
            logger.info("Appeal scheduler already running")
            return
        self._stop = False
        # Calculate next_run_at if missing
        if not self.state.get("next_run_at"):
            self._schedule_next_run()
        self._task = asyncio.create_task(self._scheduler_loop())
        logger.info(f"Appeal scheduler started. Next run at: {self.state.get('next_run_at')}")

    async def stop(self):
        """Stop the scheduler."""
        self._stop = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Appeal scheduler stopped")

    def _schedule_next_run(self):
        interval = self.state.get("interval_hours", DEFAULT_INTERVAL_HOURS)
        self.state["next_run_at"] = (datetime.utcnow() + timedelta(hours=interval)).isoformat()
        self._save_state()

    def _is_due(self) -> bool:
        if not self.state.get("enabled", True):
            return False
        next_at = self.state.get("next_run_at")
        if not next_at:
            return True
        return datetime.utcnow().isoformat() >= next_at

    async def _scheduler_loop(self):
        """Check every 60s if a run is due."""
        while not self._stop:
            try:
                if self._is_due():
                    await self._run_batch()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Appeal scheduler loop error: {e}")
            await asyncio.sleep(60)

    def _get_active_campaign_profiles(self) -> list:
        """Get profiles currently in use by the queue processor."""
        try:
            from queue_manager import CampaignQueueManager
            # Access the singleton queue manager via main module
            import main
            qm = main.queue_manager
            active = []
            for campaign in qm.campaigns.values():
                if campaign.get("status") == "processing":
                    profile = campaign.get("current_profile")
                    if profile:
                        active.append(profile)
            return active
        except Exception as e:
            logger.warning(f"Could not get active campaign profiles: {e}")
            return []

    async def _run_batch(self):
        """Run verify-all then appeal active restrictions."""
        from appeal_manager import verify_all_restricted, batch_appeal_all

        logger.info("[SCHEDULER] Starting scheduled appeal batch")
        self.state["last_run_at"] = datetime.utcnow().isoformat()

        skip_profiles = self._get_active_campaign_profiles()
        if skip_profiles:
            logger.info(f"[SCHEDULER] Skipping {len(skip_profiles)} profiles in use: {skip_profiles}")

        # Broadcast start event
        try:
            from main import broadcast_update
            await broadcast_update("appeal_scheduler_start", {
                "started_at": self.state["last_run_at"],
                "skip_profiles": skip_profiles
            })
        except Exception:
            pass

        results = {
            "started_at": self.state["last_run_at"],
            "verify_phase": None,
            "appeal_phase": None,
            "per_profile": []
        }

        # Phase 1: Verify
        try:
            verify_result = await verify_all_restricted(skip_profiles=skip_profiles)
            if verify_result.get("status") == "busy":
                logger.warning("[SCHEDULER] Appeal lock busy, skipping this run")
                self._schedule_next_run()
                return {"status": "busy", "message": "Appeal batch already running"}

            results["verify_phase"] = {
                "total": verify_result.get("total", 0),
                "unblocked": verify_result.get("unblocked", 0),
                "in_review": verify_result.get("in_review", 0),
                "still_restricted": verify_result.get("still_restricted", 0)
            }
            for r in verify_result.get("results", []):
                results["per_profile"].append({
                    "name": r.get("profile_name"),
                    "phase": "verify",
                    "status": r.get("verified_status", "unknown"),
                    "action": r.get("action_taken", "none")
                })
        except Exception as e:
            logger.error(f"[SCHEDULER] Verify phase error: {e}")
            results["verify_phase"] = {"error": str(e)}

        # Phase 2: Appeal (only if still-restricted profiles exist)
        still_restricted = results.get("verify_phase", {}).get("still_restricted", 0)
        if still_restricted > 0:
            try:
                appeal_result = await batch_appeal_all(skip_profiles=skip_profiles)
                if appeal_result.get("status") == "busy":
                    logger.warning("[SCHEDULER] Appeal lock busy for appeal phase")
                    results["appeal_phase"] = {"status": "busy"}
                else:
                    results["appeal_phase"] = {
                        "total": appeal_result.get("total_attempts", 0),
                        "succeeded": appeal_result.get("successful", 0),
                        "failed": appeal_result.get("failed", 0)
                    }
                    for r in appeal_result.get("results", []):
                        results["per_profile"].append({
                            "name": r.get("profile_name"),
                            "phase": "appeal",
                            "status": "succeeded" if r.get("success") else "failed",
                            "action": r.get("final_status", "unknown"),
                            "error": r.get("error")
                        })
            except Exception as e:
                logger.error(f"[SCHEDULER] Appeal phase error: {e}")
                results["appeal_phase"] = {"error": str(e)}
        else:
            results["appeal_phase"] = {"total": 0, "succeeded": 0, "failed": 0}
            logger.info("[SCHEDULER] No still-restricted profiles, skipping appeal phase")

        # Save results
        self.state["last_results"] = results
        results["completed_at"] = datetime.utcnow().isoformat()

        # Add to run history (keep last MAX_HISTORY)
        history_entry = {
            "run_at": self.state["last_run_at"],
            "completed_at": results["completed_at"],
            "verify": results["verify_phase"],
            "appeal": results["appeal_phase"],
            "profile_count": len(results["per_profile"])
        }
        self.state.setdefault("run_history", []).insert(0, history_entry)
        self.state["run_history"] = self.state["run_history"][:MAX_HISTORY]

        # Schedule next run
        self._schedule_next_run()
        logger.info(f"[SCHEDULER] Batch complete. Next run at: {self.state['next_run_at']}")

        # Broadcast completion
        try:
            from main import broadcast_update
            await broadcast_update("appeal_scheduler_complete", {
                "completed_at": results["completed_at"],
                "verify_phase": results["verify_phase"],
                "appeal_phase": results["appeal_phase"],
                "next_run_at": self.state["next_run_at"]
            })
        except Exception:
            pass

        return results

    async def run_now(self) -> dict:
        """Manual trigger. Returns results directly."""
        from appeal_manager import _appeal_lock
        if _appeal_lock.locked():
            return {"status": "busy", "message": "Appeal batch already running"}
        return await self._run_batch()

    def get_status(self) -> dict:
        """Return current scheduler state for API."""
        return {
            "enabled": self.state.get("enabled", True),
            "interval_hours": self.state.get("interval_hours", DEFAULT_INTERVAL_HOURS),
            "last_run_at": self.state.get("last_run_at"),
            "next_run_at": self.state.get("next_run_at"),
            "last_results": self.state.get("last_results"),
            "run_history": self.state.get("run_history", [])
        }


# Singleton
_scheduler_instance: Optional[AppealScheduler] = None


def get_appeal_scheduler() -> AppealScheduler:
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = AppealScheduler()
    return _scheduler_instance
