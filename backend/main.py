"""
CommentBot API - Streamlined Facebook Comment Automation
"""

from env_loader import load_project_env

load_project_env()

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File, Depends, status, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer, APIKeyHeader
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from typing import Any, List, Optional, Dict, Set, Literal

# Authentication imports
from auth import create_access_token, create_refresh_token, decode_token, verify_password
from users import user_manager

# Maximum concurrent browser sessions for campaigns
MAX_CONCURRENT = 5
import logging
import os
import hashlib
import re

# API Key for programmatic access (Claude testing, CI/CD, etc.)
# Set via CLAUDE_API_KEY environment variable
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY")
import asyncio
import json
import random
from datetime import datetime, timedelta
from pathlib import Path
import uuid
from urllib.parse import urlparse, parse_qs
from zoneinfo import ZoneInfo
import nest_asyncio

# Patch asyncio to allow nested event loops (crucial for Playwright in FastAPI)
nest_asyncio.apply()

from comment_bot import (
    post_comment,
    post_comment_verified,
    reply_to_comment_verified,
    reconcile_comment_submission,
    parse_comment_id_from_url,
    test_session,
    MOBILE_VIEWPORT,
    DEFAULT_USER_AGENT,
)
from fb_session import FacebookSession, list_saved_sessions
from reddit_session import RedditSession, list_saved_reddit_sessions
from credentials import CredentialManager
from proxy_manager import ProxyManager
from draft_manager import DraftManager
from campaign_ai_product_store import get_campaign_ai_product_store
from queue_manager import (
    CampaignQueueManager,
    canonicalize_campaign_jobs,
    find_duplicate_text_conflicts,
    near_duplicate_ratio,
    get_campaign_success_count,
    get_campaign_total_jobs,
    LOOKBACK_DAYS_DEFAULT,
    NEAR_DUPLICATE_THRESHOLD,
)
from login_bot import create_session_from_credentials, refresh_session_profile_name, refresh_session_picture, fetch_profile_data_from_cookies
from browser_manager import get_browser_manager, UPLOAD_DIR
from reddit_login_bot import (
    compare_attempts as compare_reddit_login_attempts,
    create_session_from_credentials as create_reddit_session_from_credentials,
    run_reference_login_from_credentials,
    test_session as test_reddit_session,
)
from reddit_bot import run_reddit_action
from reddit_growth_generation import WRITING_RULE_SOURCE_PATHS
from reddit_program_notifications import build_program_email_body
from reddit_mission_store import RedditMissionScheduler, RedditMissionStore
from reddit_program_orchestrator import RedditProgramOrchestrator, RedditProgramScheduler
from reddit_program_store import RedditProgramStore
from reddit_login_learning import RedditLoginLearningStore
from reddit_convergence import (
    DEFAULT_UNLINKED_ORDER,
    execute_reddit_unlinked_convergence,
    load_reddit_convergence_report,
)
from reddit_rollout import (
    execute_reddit_bulk_session_rollout,
    load_reddit_rollout_report,
)
from name_dedupe_workflow import build_dedupe_plan, apply_dedupe_plan
from premium_models import (
    PremiumProfileConfig,
    RulesSyncRequest,
    PremiumRunCreateRequest,
)
from premium_rules import build_rules_snapshot, load_rule_texts_from_paths
from premium_store import get_premium_store
from premium_orchestrator import PremiumOrchestrator
from premium_scheduler import PremiumScheduler
from forensics import (
    build_forensic_group,
    download_forensic_artifact_bytes,
    get_forensic_artifact_by_id,
    get_forensic_attempt_detail,
    has_direct_active_restriction_proof,
    list_forensic_attempts,
)
from campaign_ai import (
    CampaignAIError,
    fetch_campaign_context,
    load_campaign_rules_snapshot,
    generate_campaign_comments,
    summarize_rules,
    ensure_comment_count,
)

# Setup Logging - JSON structured logs for production, readable logs for dev
class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging."""
    def format(self, record):
        log_data = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data)

# Use JSON logging in production (Railway), readable format locally
USE_JSON_LOGS = os.getenv("RAILWAY_ENVIRONMENT") is not None

if USE_JSON_LOGS:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logging.root.handlers = [handler]
    logging.root.setLevel(logging.INFO)
else:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
logger = logging.getLogger("API")

app = FastAPI()

# Mount debug directory for screenshots at /screenshots (not /debug to avoid shadowing API routes)
debug_path = os.getenv("DEBUG_DIR", os.path.join(os.path.dirname(__file__), "debug"))
os.makedirs(debug_path, exist_ok=True)
app.mount("/screenshots", StaticFiles(directory=debug_path), name="screenshots")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# WebSocket connections
active_connections: Set[WebSocket] = set()

async def broadcast_update(update_type: str, data: dict):
    """Broadcast to all WebSocket clients."""
    if not active_connections:
        return
    message = json.dumps({"type": update_type, "data": data, "timestamp": datetime.now().isoformat()})
    disconnected = set()
    for ws in active_connections:
        try:
            await ws.send_text(message)
        except WebSocketDisconnect:
            disconnected.add(ws)
        except RuntimeError as e:
            # "Cannot call send once close message has been sent"
            logger.debug(f"WebSocket already closed: {e}")
            disconnected.add(ws)
        except Exception as e:
            logger.warning(f"Broadcast error: {e}")
            disconnected.add(ws)
    for ws in disconnected:
        active_connections.discard(ws)

# Get proxy from environment
PROXY_URL = os.getenv("PROXY_URL", "")


from proxy_manager import get_system_proxy

# Initialize credential manager
credential_manager = CredentialManager()

# Initialize proxy manager
proxy_manager = ProxyManager()

# Initialize campaign queue manager
queue_manager = CampaignQueueManager()

# Initialize shared draft manager
draft_manager = DraftManager()

# Initialize campaign AI product preset store
campaign_ai_product_store = get_campaign_ai_product_store()

# Initialize premium automation components
premium_store = get_premium_store()
premium_orchestrator = PremiumOrchestrator(
    store=premium_store,
    broadcast_update=broadcast_update,
)
premium_scheduler = PremiumScheduler(
    store=premium_store,
    orchestrator=premium_orchestrator,
)

# Initialize Reddit mission store/scheduler (runner wired below once helpers exist)
reddit_mission_store = RedditMissionStore()
reddit_mission_scheduler: Optional[RedditMissionScheduler] = None
reddit_program_store = RedditProgramStore()
reddit_program_orchestrator = RedditProgramOrchestrator(
    store=reddit_program_store,
    proxy_resolver=lambda: _resolve_effective_proxy(),
    broadcast_update=broadcast_update,
)
reddit_program_scheduler = RedditProgramScheduler(
    store=reddit_program_store,
    orchestrator=reddit_program_orchestrator,
)
reddit_bulk_rollout_tasks: Dict[str, asyncio.Task] = {}
reddit_convergence_tasks: Dict[str, asyncio.Task] = {}

# =========================================================================
# Media Store (ephemeral file-backed storage for queue jobs)
# =========================================================================

MEDIA_DIR = Path(os.getenv("MEDIA_DIR", "/tmp/commentbot_media"))
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_TTL_HOURS = int(os.getenv("MEDIA_TTL_HOURS", "24"))
MEDIA_MAX_SIZE = 10 * 1024 * 1024  # 10MB
MEDIA_ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}

# In-memory metadata index for uploaded media
media_index: Dict[str, Dict] = {}

# Idempotency cache for /queue submissions
queue_idempotency_index: Dict[str, str] = {}


# =========================================================================
# Queue Processor - Background task for processing campaign queue
# =========================================================================

class QueueProcessor:
    """
    Singleton background processor that runs campaigns sequentially.
    Only one campaign processes at a time across all users.
    """

    def __init__(self, qm: CampaignQueueManager):
        self.queue_manager = qm
        self.is_running = False
        self._campaign_task: Optional[asyncio.Task] = None
        self._retry_task: Optional[asyncio.Task] = None
        self._stop_requested = False
        self._current_campaign_cancelled = False
        self.logger = logging.getLogger("QueueProcessor")

    async def start(self):
        """Start the background processor loop."""
        if self.is_running:
            self.logger.info("Queue processor already running")
            return

        self.is_running = True
        self._stop_requested = False
        self._campaign_task = asyncio.create_task(self._campaign_loop())
        self._retry_task = asyncio.create_task(self._retry_loop())
        self.logger.info("Queue processor started")

    async def stop(self):
        """Gracefully stop the processor."""
        self._stop_requested = True
        for task in (self._campaign_task, self._retry_task):
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self.is_running = False
        self.queue_manager.set_processor_running(False)
        self.logger.info("Queue processor stopped")

    def cancel_current_campaign(self):
        """Signal that the current campaign should be cancelled."""
        self._current_campaign_cancelled = True

    @staticmethod
    def _comment_hash(text: str) -> str:
        normalized = " ".join(str(text or "").strip().lower().split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def _failure_requires_reconciliation(result: Optional[dict]) -> bool:
        """Retry reconciliation is required for verification-shaped failures."""
        if not result:
            return False
        method = str(result.get("method") or "").lower()
        error = str(result.get("error") or "").lower()
        return method in ("verification_inconclusive", "uncertain_no_repost") or "comment not posted" in error

    @staticmethod
    def _determine_failure_type(
        *,
        success: bool,
        was_restriction: bool,
        error: Optional[str],
        method: Optional[str] = None,
    ) -> Optional[str]:
        """Keep post-submit uncertainty out of profile-quality penalties."""
        if success:
            return None

        error_lower = str(error or "").lower()
        if str(method or "").lower() == "verification_inconclusive":
            return "infrastructure"
        if was_restriction or "restricted" in error_lower or "ban" in error_lower:
            return "restriction"
        if any(x in error_lower for x in ["timeout", "proxy", "connection", "network", "tunnel", "net::err"]):
            return "infrastructure"
        return "facebook_error"

    @staticmethod
    def _apply_restriction_signal(
        profile_manager,
        *,
        profile_name: str,
        reason: str,
        attempt_id: Optional[str] = None,
    ) -> str:
        if has_direct_active_restriction_proof(reason):
            profile_manager.mark_profile_restricted(profile_name, reason=reason)
            return "restriction_verified"
        profile_manager.mark_profile_restriction_suspected(
            profile_name,
            reason=reason,
            attempt_id=attempt_id,
        )
        return "restriction_suspected"

    async def _recover_inflight_checkpoint(
        self,
        *,
        campaign: dict,
        jobs: List[dict],
        url: str,
        profile_manager,
    ) -> None:
        """
        Recover an unfinished inflight checkpoint after restart/redeploy.

        Policy: prefer no duplicates.
        - If post was likely submitted, reconcile read-only.
        - If found -> success.
        - If missing/inconclusive -> mark uncertain_no_repost failure.
        """
        campaign_id = campaign["id"]
        inflight = self.queue_manager.get_inflight_job(campaign_id)
        if not inflight:
            return

        try:
            job_index = int(inflight.get("job_index"))
        except Exception:
            self.logger.warning(f"Campaign {campaign_id[:8]} inflight checkpoint has invalid job_index, clearing")
            self.queue_manager.clear_inflight_job(campaign_id)
            return

        if job_index < 0 or job_index >= len(jobs):
            self.logger.warning(f"Campaign {campaign_id[:8]} inflight checkpoint out of range (job {job_index}), clearing")
            self.queue_manager.clear_inflight_job(campaign_id)
            return

        completed_indexes = self.queue_manager.get_completed_job_indexes(campaign_id)
        if job_index in completed_indexes:
            self.queue_manager.clear_inflight_job(campaign_id)
            return

        phase = str(inflight.get("phase") or "")
        if phase in ("starting", "finalized"):
            self.logger.info(f"Campaign {campaign_id[:8]} clearing inflight checkpoint phase={phase}")
            self.queue_manager.clear_inflight_job(campaign_id)
            return

        if phase not in ("submit_clicked", "verifying"):
            self.logger.info(f"Campaign {campaign_id[:8]} clearing unknown inflight phase={phase}")
            self.queue_manager.clear_inflight_job(campaign_id)
            return

        profile_name = str(inflight.get("profile_name") or "").strip()
        job = jobs[job_index]
        comment_text = str(job.get("text") or "")
        job_type = str(job.get("type", "post_comment")).strip().lower()

        self.logger.warning(
            f"Campaign {campaign_id[:8]} recovering inflight job {job_index} (phase={phase}, profile={profile_name or 'unknown'})"
        )

        reconciliation = {"found": None, "confidence": 0.0, "reason": "profile unavailable for reconciliation"}
        if profile_name:
            session = FacebookSession(profile_name)
            if session.load():
                reconciliation = await reconcile_comment_submission(
                    session=session,
                    url=url,
                    comment_text=comment_text,
                    proxy=get_system_proxy(),
                )
            else:
                reconciliation = {"found": None, "confidence": 0.0, "reason": "session missing during reconciliation"}

        if reconciliation.get("found") is True:
            recovered_result = {
                "profile_name": profile_name,
                "comment": comment_text,
                "text": comment_text,
                "job_type": job_type,
                "target_comment_url": job.get("target_comment_url"),
                "target_comment_id": parse_comment_id_from_url(str(job.get("target_comment_url") or "")),
                "image_id": job.get("image_id"),
                "success": True,
                "verified": True,
                "method": "reconciled_existing_comment",
                "error": None,
                "job_index": job_index,
                "recovered_from_inflight": True,
                "inflight_phase": phase,
                "reconciliation_confidence": reconciliation.get("confidence", 0.0),
            }
            failure_type = None
        else:
            recovered_result = {
                "profile_name": profile_name,
                "comment": comment_text,
                "text": comment_text,
                "job_type": job_type,
                "target_comment_url": job.get("target_comment_url"),
                "target_comment_id": parse_comment_id_from_url(str(job.get("target_comment_url") or "")),
                "image_id": job.get("image_id"),
                "success": False,
                "verified": False,
                "method": "uncertain_no_repost",
                "error": f"uncertain_no_repost: {reconciliation.get('reason', 'verification inconclusive')}",
                "job_index": job_index,
                "recovered_from_inflight": True,
                "inflight_phase": phase,
                "reconciliation_confidence": reconciliation.get("confidence", 0.0),
            }
            # Treat uncertain reconciliation as infrastructure class to avoid accidental restrictions.
            failure_type = "infrastructure"

        saved = self.queue_manager.save_job_result(campaign_id, job_index, recovered_result)
        self.queue_manager.clear_inflight_job(campaign_id)

        if saved and profile_name:
            profile_manager.mark_profile_used(
                profile_name=profile_name,
                campaign_id=campaign_id,
                comment=comment_text,
                success=bool(recovered_result["success"]),
                failure_type=failure_type,
            )

        if saved:
            await broadcast_update(
                "job_complete",
                {
                    "campaign_id": campaign_id,
                    "job_index": job_index,
                    "total_jobs": len(jobs),
                    "profile_name": profile_name,
                    "success": recovered_result["success"],
                    "verified": recovered_result["verified"],
                    "method": recovered_result["method"],
                    "error": recovered_result["error"],
                    "recovered_from_inflight": True,
                },
            )

    async def _campaign_loop(self):
        """Main processing loop for new campaigns."""
        while not self._stop_requested:
            try:
                worked = await self._run_campaign_iteration()
                if not worked:
                    await asyncio.sleep(2)
            except asyncio.CancelledError:
                self.logger.info("Campaign loop cancelled")
                break
            except Exception as e:
                self.logger.error(f"Campaign loop error: {e}")
                await asyncio.sleep(5)  # Wait before retrying

    async def _retry_loop(self):
        """Dedicated retry loop so overdue recoveries do not starve behind pending work."""
        while not self._stop_requested:
            try:
                worked = await self._run_retry_iteration()
                if not worked:
                    await asyncio.sleep(2)
            except asyncio.CancelledError:
                self.logger.info("Retry loop cancelled")
                break
            except Exception as e:
                self.logger.error(f"Retry loop error: {e}")
                await asyncio.sleep(5)

    async def _run_campaign_iteration(self) -> bool:
        """Run one new-campaign iteration. Returns True when work was processed."""
        if _retry_all_task and not _retry_all_task.done():
            return False

        campaign = self.queue_manager.get_next_pending()
        if not campaign:
            return False

        self._current_campaign_cancelled = False
        await self._process_campaign(campaign)
        return True

    async def _run_retry_iteration(self) -> bool:
        """Run one due auto-retry iteration. Returns True when work was processed."""
        if _retry_all_task and not _retry_all_task.done():
            return False

        retry_campaign = self.queue_manager.get_next_due_retry()
        if not retry_campaign:
            return False

        overdue_seconds = 0
        next_retry_at = retry_campaign.get("auto_retry", {}).get("next_retry_at")
        if next_retry_at:
            next_retry_dt = datetime.fromisoformat(str(next_retry_at).replace("Z", "+00:00")).replace(tzinfo=None)
            overdue_seconds = max(0, int((datetime.utcnow() - next_retry_dt).total_seconds()))

        remaining_jobs = len(
            [fj for fj in retry_campaign.get("auto_retry", {}).get("failed_jobs", []) if not fj.get("exhausted")]
        )
        self.logger.info(
            f"Auto-retry due for campaign {retry_campaign['id'][:8]}... "
            f"(overdue={overdue_seconds}s, remaining_jobs={remaining_jobs})"
        )

        health = await check_proxy_health()
        if not health["healthy"]:
            self.logger.warning(
                f"Skipping auto-retry for {retry_campaign['id'][:8]}... because proxy health is down: "
                f"{health.get('error', 'unknown')}"
            )
            return False

        await self._process_auto_retry(retry_campaign)
        return True

    async def _process_campaign(self, campaign: dict):
        """Process a single campaign."""
        campaign_id = campaign["id"]
        url = campaign["url"]
        jobs = campaign.get("jobs")
        if not jobs:
            # Backward compatibility for legacy campaigns stored as comments[] only.
            jobs = canonicalize_campaign_jobs(comments=campaign.get("comments") or [], jobs=None)
        duration_minutes = campaign["duration_minutes"]
        filter_tags = campaign.get("filter_tags", [])
        enable_warmup = campaign.get("enable_warmup", False)
        forced_profile_name = campaign.get("profile_name")

        try:
            # Pre-campaign proxy health check — fail fast instead of burning through profiles
            health = await check_proxy_health()
            if not health["healthy"]:
                error_msg = f"Proxy down: {health.get('error', 'unknown')}. Campaign paused, will auto-retry."
                self.logger.error(f"Campaign {campaign_id[:8]}: {error_msg}")
                await broadcast_update("queue_campaign_failed", {
                    "campaign_id": campaign_id,
                    "error": error_msg,
                    "proxy_error": True
                })
                # Keep campaign as pending — QueueProcessor will naturally retry after sleep
                return

            # Mark as processing
            self.queue_manager.set_processing(campaign_id)
            await broadcast_update("queue_campaign_start", {
                "campaign_id": campaign_id,
                "url": url,
                "total_comments": len(jobs),
                "total_jobs": len(jobs),
            })

            # Use UNIFIED profile selection (handles cookies, tags, restrictions, LRU)
            from profile_manager import get_profile_manager
            profile_manager = get_profile_manager()

            if forced_profile_name:
                eligible_for_filters = profile_manager.get_eligible_profiles(
                    filter_tags=filter_tags if filter_tags else None,
                    count=500,
                )
                if forced_profile_name not in eligible_for_filters:
                    error_msg = (
                        f"Forced profile not eligible: {forced_profile_name} "
                        f"(filters={filter_tags})"
                    )
                    self.queue_manager.set_failed(campaign_id, error_msg)
                    await broadcast_update("queue_campaign_failed", {
                        "campaign_id": campaign_id,
                        "error": error_msg
                    })
                    return
                assigned_profiles = [forced_profile_name for _ in range(len(jobs))]
            else:
                assigned_profiles = profile_manager.get_eligible_profiles(
                    filter_tags=filter_tags if filter_tags else None,
                    count=len(jobs)
                )

            if not assigned_profiles:
                error_msg = f"No eligible profiles match tags: {filter_tags}" if filter_tags else "No eligible profiles available"
                self.queue_manager.set_failed(campaign_id, error_msg)
                await broadcast_update("queue_campaign_failed", {
                    "campaign_id": campaign_id,
                    "error": error_msg
                })
                return

            self.logger.info(
                f"Profile selection (forced={forced_profile_name if forced_profile_name else 'none'}): {assigned_profiles}"
            )

            if len(assigned_profiles) < len(jobs):
                self.logger.warning(f"Only {len(assigned_profiles)} profiles available for {len(jobs)} jobs")

            # Calculate total jobs based on available profiles
            total_jobs = min(len(jobs), len(assigned_profiles))
            duration_seconds = duration_minutes * 60

            # Recover unfinished inflight checkpoint from previous deployment/restart.
            await self._recover_inflight_checkpoint(
                campaign=campaign,
                jobs=jobs[:total_jobs],
                url=url,
                profile_manager=profile_manager,
            )

            # DEPLOYMENT RESILIENCE: Get already-attempted job indexes
            attempted_indexes = self.queue_manager.get_completed_job_indexes(campaign_id)

            # Build list of PENDING jobs with their ORIGINAL indexes preserved
            pending_jobs = []
            for original_idx, job in enumerate(jobs[:total_jobs]):
                if original_idx not in attempted_indexes:
                    pending_jobs.append((original_idx, job))

            if attempted_indexes:
                self.logger.info(f"Campaign {campaign_id}: RESUMING - {len(attempted_indexes)} jobs already attempted, {len(pending_jobs)} remaining")

            if not pending_jobs:
                # All jobs already attempted - mark complete with existing results
                existing_results = self.queue_manager.get_campaign(campaign_id).get("results", [])
                success_count = sum(1 for r in existing_results if r.get("success"))
                self.queue_manager.set_completed(campaign_id, success_count, total_jobs, existing_results)
                await broadcast_update("queue_campaign_complete", {
                    "campaign_id": campaign_id,
                    "success": success_count,
                    "total": total_jobs
                })
                self.logger.info(f"Campaign {campaign_id}: All jobs already completed, marking done")
                return

            # Get profiles ONLY for pending jobs (not all jobs)
            if forced_profile_name:
                pending_profiles = [forced_profile_name for _ in range(len(pending_jobs))]
            else:
                pending_profiles = profile_manager.get_eligible_profiles(
                    filter_tags=filter_tags if filter_tags else None,
                    count=len(pending_jobs)
                )

            if not pending_profiles:
                error_msg = f"No eligible profiles for {len(pending_jobs)} remaining jobs"
                self.queue_manager.set_failed(campaign_id, error_msg)
                await broadcast_update("queue_campaign_failed", {"campaign_id": campaign_id, "error": error_msg})
                return

            self.logger.info(f"Profiles for {len(pending_jobs)} pending jobs: {pending_profiles}")

            # Recalculate timing for REMAINING jobs only
            base_delay = duration_seconds / len(pending_jobs) if len(pending_jobs) > 1 else 0

            # Broadcast campaign start
            await broadcast_update("campaign_start", {
                "url": url,
                "total_jobs": total_jobs,
                "duration_minutes": duration_minutes
            })

            # Process jobs - results list for this run only
            results = []

            for pending_idx, (original_job_idx, job_payload) in enumerate(pending_jobs):
                job = job_payload
                job_type = str(job.get("type", "post_comment")).strip().lower()
                text = str(job.get("text", ""))
                if job_type == "reply_comment":
                    text = text.lower()
                    job["text"] = text

                # Get profile for this pending job
                profile_name = pending_profiles[pending_idx] if pending_idx < len(pending_profiles) else None
                if not profile_name:
                    self.logger.error(f"No profile available for pending job {pending_idx}")
                    continue
                # Check for cancellation before each job
                if self._current_campaign_cancelled:
                    self.logger.info(f"Campaign {campaign_id} was cancelled, stopping")
                    self.queue_manager.set_cancelled(campaign_id)
                    await broadcast_update("queue_campaign_cancelled", {"campaign_id": campaign_id})
                    return

                current_campaign = self.queue_manager.get_campaign(campaign_id)
                if current_campaign and current_campaign.get("status") == "cancelled":
                    self.logger.info(f"Campaign {campaign_id} marked as cancelled, stopping")
                    await broadcast_update("queue_campaign_cancelled", {"campaign_id": campaign_id})
                    return

                # Staggered delay (except first pending job in this run)
                if pending_idx > 0:
                    jitter = random.uniform(0.8, 1.2)
                    delay_seconds = base_delay * jitter

                    await broadcast_update("job_waiting", {
                        "campaign_id": campaign_id,
                        "job_index": original_job_idx,
                        "delay_seconds": round(delay_seconds),
                        "profile_name": profile_name
                    })

                    self.logger.info(f"Campaign {campaign_id}: Waiting {delay_seconds:.0f}s before job {original_job_idx}")
                    await asyncio.sleep(delay_seconds)

                # Update progress with ORIGINAL job index for accurate display (e.g., "Job 8/13" not "Job 1/6")
                self.queue_manager.update_job_progress(campaign_id, original_job_idx + 1, total_jobs, profile_name)

                await broadcast_update("job_start", {
                    "campaign_id": campaign_id,
                    "job_index": original_job_idx,
                    "total_jobs": total_jobs,
                    "profile_name": profile_name,
                    "comment": text[:50],
                    "job_type": job_type,
                })

                # Reserve profile to prevent concurrent browser sessions
                reserved = await profile_manager.reserve_profile(profile_name)
                if not reserved:
                    self.logger.warning(f"Profile {profile_name} reserved by another task, skipping job {original_job_idx}")
                    job_result = {
                        "profile_name": profile_name,
                        "success": False,
                        "error": "Profile busy (reserved by another task)",
                        "job_index": original_job_idx
                    }
                    results.append(job_result)
                    self.queue_manager.save_job_result(campaign_id, original_job_idx, job_result)
                    continue

                attempt_id: Optional[str] = None
                try:
                    session = FacebookSession(profile_name)
                    attempt_id = str(uuid.uuid4())
                    comment_hash = self._comment_hash(text)
                    self.queue_manager.set_inflight_job(
                        campaign_id,
                        job_index=original_job_idx,
                        profile_name=profile_name,
                        comment_hash=comment_hash,
                        phase="starting",
                        attempt_id=attempt_id,
                        metadata={"job_type": job_type},
                    )

                    async def phase_callback(phase: str, metadata: Dict[str, str]):
                        self.queue_manager.update_inflight_phase(
                            campaign_id,
                            phase=phase,
                            attempt_id=attempt_id,
                            metadata=metadata,
                        )

                    if not session.load():
                        self.queue_manager.update_inflight_phase(
                            campaign_id,
                            phase="finalized",
                            attempt_id=attempt_id,
                            metadata={"error": "Session not found"},
                        )
                        await broadcast_update("job_error", {
                            "campaign_id": campaign_id,
                            "job_index": original_job_idx,
                            "error": "Session not found"
                        })
                        job_result = {
                            "profile_name": profile_name,
                            "success": False,
                            "error": "Session not found",
                            "job_index": original_job_idx
                        }
                        results.append(job_result)
                        # DEPLOYMENT RESILIENCE: Save immediately to disk
                        self.queue_manager.save_job_result(campaign_id, original_job_idx, job_result)
                        continue

                    # Set Gemini observation context for debugging
                    from gemini_vision import set_observation_context
                    set_observation_context(profile_name=profile_name, campaign_id=campaign_id)

                    # Broadcast warmup start if enabled
                    if enable_warmup:
                        await broadcast_update("warmup_start", {
                            "campaign_id": campaign_id,
                            "job_index": original_job_idx,
                            "profile_name": profile_name
                        })

                    result = await post_comment_verified(
                        session=session,
                        url=url,
                        comment=text,
                        proxy=get_system_proxy(),
                        enable_warmup=enable_warmup,
                        phase_callback=phase_callback,
                        forensic_context={
                            "platform": "facebook",
                            "engine": "campaign_comment",
                            "campaign_id": campaign_id,
                            "job_id": str(original_job_idx),
                            "run_id": campaign_id,
                        },
                    ) if job_type != "reply_comment" else None

                    target_comment_url = str(job.get("target_comment_url") or "").strip()
                    target_comment_id = parse_comment_id_from_url(target_comment_url) if target_comment_url else None
                    image_id = str(job.get("image_id") or "").strip()

                    if job_type == "reply_comment":
                        media_item = _get_media_or_none(image_id)
                        if not media_item:
                            result = {
                                "success": False,
                                "verified": False,
                                "method": "validation",
                                "error": f"image_id not found or expired: {image_id}",
                            }
                        elif not target_comment_id:
                            result = {
                                "success": False,
                                "verified": False,
                                "method": "validation",
                                "error": f"target_comment_url missing parseable comment_id: {target_comment_url}",
                            }
                        else:
                            result = await reply_to_comment_verified(
                                session=session,
                                url=url,
                                target_comment_url=target_comment_url,
                                target_comment_id=target_comment_id,
                                reply_text=text,
                                image_path=media_item["path"],
                                proxy=get_system_proxy(),
                                enable_warmup=enable_warmup,
                                phase_callback=phase_callback,
                            )
                    else:
                        target_comment_url = None
                        target_comment_id = None
                        image_id = None

                    self.queue_manager.update_inflight_phase(
                        campaign_id,
                        phase="finalized",
                        attempt_id=attempt_id,
                        metadata={"success": str(bool(result.get("success")))},
                    )
                    await broadcast_update("job_complete", {
                        "campaign_id": campaign_id,
                        "job_index": original_job_idx,
                        "profile_name": profile_name,
                        "success": result["success"],
                        "verified": result.get("verified", False),
                        "method": result.get("method", "unknown"),
                        "error": result.get("error"),
                        "warmup": result.get("warmup"),
                        "job_type": job_type,
                        "target_comment_id": target_comment_id,
                    })

                    job_result = {
                        "profile_name": profile_name,
                        "comment": text,
                        "text": text,
                        "job_type": job_type,
                        "target_comment_url": target_comment_url,
                        "target_comment_id": target_comment_id,
                        "image_id": image_id,
                        "success": result["success"],
                        "verified": result.get("verified", False),
                        "method": result.get("method", "unknown"),
                        "error": result.get("error"),
                        "job_index": original_job_idx,
                        "warmup": result.get("warmup")
                    }
                    results.append(job_result)
                    # DEPLOYMENT RESILIENCE: Save immediately to disk
                    self.queue_manager.save_job_result(campaign_id, original_job_idx, job_result)

                    # Determine failure type for analytics granularity
                    # Check infrastructure FIRST — proxy/timeout errors must not be classified as restriction
                    failure_type = self._determine_failure_type(
                        success=bool(result["success"]),
                        was_restriction=bool(result.get("throttled")),
                        error=result.get("error"),
                        method=result.get("method"),
                    )

                    # Track profile usage for rotation (LRU - only updates timestamp on success)
                    profile_manager.mark_profile_used(
                        profile_name=profile_name,
                        campaign_id=campaign_id,
                        comment=text,
                        success=result["success"],
                        failure_type=failure_type
                    )

                    # Check for throttling/restriction detection
                    # Layer 3: Don't restrict profiles on infrastructure errors even if throttled=True leaked through
                    if result.get("throttled") and failure_type != "infrastructure":
                        throttle_reason = result.get("throttle_reason", "Facebook restriction detected")
                        self.logger.warning(f"Profile {profile_name} throttled: {throttle_reason}")

                        self._apply_restriction_signal(
                            profile_manager,
                            profile_name=profile_name,
                            reason=throttle_reason,
                            attempt_id=result.get("attempt_id"),
                        )

                        # Broadcast throttle event to frontend
                        await broadcast_update("profile_throttled", {
                            "profile_name": profile_name,
                            "reason": throttle_reason,
                            "campaign_id": campaign_id,
                            "job_index": original_job_idx
                        })
                    elif result.get("throttled") and failure_type == "infrastructure":
                        self.logger.info(f"Skipping restriction for {profile_name} — infrastructure error, not real restriction")

                except Exception as e:
                    self.logger.error(f"Error processing job {original_job_idx} in campaign {campaign_id}: {e}")
                    self.queue_manager.update_inflight_phase(
                        campaign_id,
                        phase="finalized",
                        attempt_id=attempt_id,
                        metadata={"error": str(e)},
                    )
                    await broadcast_update("job_error", {
                        "campaign_id": campaign_id,
                        "job_index": original_job_idx,
                        "error": str(e)
                    })
                    job_result = {
                        "profile_name": profile_name,
                        "success": False,
                        "error": str(e),
                        "job_index": original_job_idx
                    }
                    results.append(job_result)
                    # DEPLOYMENT RESILIENCE: Save immediately to disk
                    self.queue_manager.save_job_result(campaign_id, original_job_idx, job_result)

                    # Track exception in analytics (classify as infrastructure error)
                    exc_failure_type = self._determine_failure_type(
                        success=False,
                        was_restriction=False,
                        error=str(e),
                    )

                    profile_manager.mark_profile_used(
                        profile_name=profile_name,
                        campaign_id=campaign_id,
                        comment=text,
                        success=False,
                        failure_type=exc_failure_type
                    )
                finally:
                    # Always release profile reservation after browser closes
                    self.queue_manager.clear_inflight_job(campaign_id, attempt_id=attempt_id)
                    await profile_manager.release_profile(profile_name)

            # Campaign completed - get ALL results (including from previous runs before deployment)
            current_campaign = self.queue_manager.get_campaign(campaign_id)
            all_results = current_campaign.get("results", []) if current_campaign else results

            # Total count should be original number of jobs
            total_count = len(jobs[:total_jobs])
            success_count = sum(1 for r in all_results if r.get("success"))

            self.queue_manager.set_completed(campaign_id, success_count, total_count, all_results)

            # Enable auto-retry if there are failures
            if success_count < total_count:
                failed_jobs = []
                # Build failed_jobs from results (jobs that never succeeded)
                job_has_success = {}
                for r in all_results:
                    idx = r.get("job_index", 0)
                    if r.get("success"):
                        job_has_success[idx] = True
                for r in all_results:
                    idx = r.get("job_index", 0)
                    if not job_has_success.get(idx):
                        inferred_job_type = r.get("job_type")
                        if not inferred_job_type and idx < len(jobs):
                            inferred_job_type = jobs[idx].get("type", "post_comment")
                        # Auto-retry currently supports text post comments only.
                        if inferred_job_type != "post_comment":
                            continue
                        # Only add once per job_index
                        if not any(fj["job_index"] == idx for fj in failed_jobs):
                            failed_jobs.append({
                                "job_index": idx,
                                "comment": r.get("text", r.get("comment", jobs[idx].get("text", "") if idx < len(jobs) else "")),
                                "last_profile": r.get("profile_name", "")
                            })
                if failed_jobs:
                    self.queue_manager.enable_auto_retry(campaign_id, failed_jobs)
                    first_retry_at = self.queue_manager.get_campaign_from_history(campaign_id).get("auto_retry", {}).get("next_retry_at")
                    await broadcast_update("auto_retry_enabled", {
                        "campaign_id": campaign_id,
                        "failed_count": len(failed_jobs),
                        "first_retry_at": first_retry_at
                    })

            # Include auto_retry in the broadcast so frontend gets it immediately
            completed_campaign = self.queue_manager.get_campaign_from_history(campaign_id)
            auto_retry_data = completed_campaign.get("auto_retry") if completed_campaign else None

            await broadcast_update("queue_campaign_complete", {
                "campaign_id": campaign_id,
                "success": success_count,
                "total": total_count,
                "auto_retry": auto_retry_data
            })

            await broadcast_update("campaign_complete", {"total": total_count, "success": success_count})

        except Exception as e:
            self.logger.error(f"Campaign {campaign_id} failed: {e}")
            self.queue_manager.set_failed(campaign_id, str(e))
            await broadcast_update("queue_campaign_failed", {
                "campaign_id": campaign_id,
                "error": str(e)
            })

    async def _process_auto_retry(self, campaign: dict):
        """Process one round of auto-retry for a campaign."""
        campaign_id = campaign["id"]
        ar = campaign.get("auto_retry", {})
        round_num = ar.get("current_round", 0)
        url = campaign.get("url", "")
        filter_tags = campaign.get("filter_tags", [])
        enable_warmup = campaign.get("enable_warmup", False)

        ar["status"] = "in_progress"
        self.queue_manager.save()

        self.logger.info(f"Auto-retry round {round_num} for campaign {campaign_id[:8]}...")

        await broadcast_update("auto_retry_round_start", {
            "campaign_id": campaign_id,
            "round": round_num,
            "jobs_remaining": sum(1 for fj in ar.get("failed_jobs", []) if not fj.get("exhausted"))
        })

        from profile_manager import get_profile_manager
        profile_manager = get_profile_manager()

        # Get profiles that already succeeded in this campaign
        succeeded_profiles = {
            r.get("profile_name")
            for r in campaign.get("results", [])
            if r.get("success") and r.get("profile_name")
        }

        round_succeeded = 0
        round_failed = 0

        for fj in ar.get("failed_jobs", []):
            if fj.get("exhausted"):
                continue

            job_index = fj["job_index"]
            comment = fj.get("comment", "")
            job_history = [
                result
                for result in campaign.get("results", [])
                if result.get("job_index") == job_index
            ]
            last_failure = next((result for result in reversed(job_history) if not result.get("success")), None)

            if self._failure_requires_reconciliation(last_failure):
                reconciliation_profile = fj.get("last_profile", "")
                if reconciliation_profile:
                    reserved_reconciliation = await profile_manager.reserve_profile(reconciliation_profile)
                    if not reserved_reconciliation:
                        self.logger.warning(
                            f"Auto-retry reconciliation skipped for campaign {campaign_id[:8]} job {job_index}: "
                            f"profile {reconciliation_profile} is currently reserved"
                        )
                        round_failed += 1
                        continue

                    try:
                        session = FacebookSession(reconciliation_profile)
                        if session.load():
                            reconciliation = await reconcile_comment_submission(
                                session=session,
                                url=url,
                                comment_text=comment,
                                proxy=get_system_proxy(),
                            )
                            if reconciliation.get("found") is True:
                                self.logger.info(
                                    f"Auto-retry reconciliation recovered campaign {campaign_id[:8]} job {job_index} "
                                    f"without repost (profile={reconciliation_profile}, confidence={reconciliation.get('confidence', 0.0):.2f})"
                                )
                                self.queue_manager.record_retry_attempt(
                                    campaign_id=campaign_id,
                                    job_index=job_index,
                                    profile=reconciliation_profile,
                                    round_num=round_num,
                                    success=True,
                                    error=None,
                                    was_restriction=False,
                                    method="reconciled_existing_comment",
                                    verified=True,
                                    metadata={
                                        "reconciled_without_repost": True,
                                        "reconciliation_confidence": reconciliation.get("confidence", 0.0),
                                        "reconciliation_reason": reconciliation.get("reason"),
                                    },
                                )
                                await broadcast_update("auto_retry_job_result", {
                                    "campaign_id": campaign_id,
                                    "job_index": job_index,
                                    "profile": reconciliation_profile,
                                    "success": True,
                                    "error": None,
                                    "method": "reconciled_existing_comment",
                                    "round": round_num,
                                })
                                round_succeeded += 1
                                succeeded_profiles.add(reconciliation_profile)
                                continue

                            if reconciliation.get("found") is None:
                                self.logger.warning(
                                    f"Auto-retry reconciliation inconclusive for campaign {campaign_id[:8]} job {job_index}: "
                                    f"{reconciliation.get('reason', 'unknown reason')}"
                                )
                                round_failed += 1
                                continue
                        else:
                            self.logger.warning(
                                f"Auto-retry reconciliation session missing for campaign {campaign_id[:8]} "
                                f"job {job_index} profile={reconciliation_profile}"
                            )
                    finally:
                        await profile_manager.release_profile(reconciliation_profile)

            excluded = set(fj.get("excluded_profiles", []))
            exclude_from_selection = list(excluded | succeeded_profiles)

            # Get eligible profiles
            eligible = profile_manager.get_eligible_profiles(
                filter_tags=filter_tags if filter_tags else None,
                count=5,
                exclude_profiles=exclude_from_selection
            )

            if not eligible:
                self.logger.warning(f"Auto-retry: job {job_index} exhausted all profiles")
                self.queue_manager.mark_retry_job_exhausted(campaign_id, job_index)
                round_failed += 1
                continue

            # Profile selection: prefer last_profile if still eligible (transient failure)
            last_profile = fj.get("last_profile", "")
            ordered_profiles = eligible[:]
            if last_profile and last_profile in ordered_profiles and last_profile not in excluded:
                ordered_profiles.remove(last_profile)
                ordered_profiles.insert(0, last_profile)

            profile_name: Optional[str] = None
            for candidate in ordered_profiles:
                if await profile_manager.reserve_profile(candidate):
                    profile_name = candidate
                    break

            if not profile_name:
                self.logger.warning(
                    f"Auto-retry: no free profile reservation available for campaign {campaign_id[:8]} job {job_index}"
                )
                round_failed += 1
                continue

            self.logger.info(f"Auto-retry: job {job_index} with profile {profile_name}")

            try:
                session = FacebookSession(profile_name)
                if not session.load():
                    self.logger.warning(f"Auto-retry: session {profile_name} not found, marking exhausted for job")
                    fj.setdefault("excluded_profiles", []).append(profile_name)
                    self.queue_manager.save()
                    round_failed += 1
                    continue

                from gemini_vision import set_observation_context
                set_observation_context(profile_name=profile_name, campaign_id=campaign_id)

                result = await post_comment_verified(
                    session=session,
                    url=url,
                    comment=comment,
                    proxy=get_system_proxy(),
                    enable_warmup=enable_warmup,
                    forensic_context={
                        "platform": "facebook",
                        "engine": "auto_retry_comment",
                        "campaign_id": campaign_id,
                        "job_id": str(job_index),
                        "run_id": campaign_id,
                        "parent_attempt_id": fj.get("attempt_id"),
                    },
                )

                success = result.get("success", False)
                was_restriction = bool(result.get("throttled"))
                error = result.get("error")
                method = result.get("method")
                failure_type = self._determine_failure_type(
                    success=bool(success),
                    was_restriction=was_restriction,
                    error=error,
                    method=method,
                )

                profile_manager.mark_profile_used(
                    profile_name=profile_name,
                    campaign_id=campaign_id,
                    comment=comment,
                    success=success,
                    failure_type=failure_type
                )

                if was_restriction:
                    self._apply_restriction_signal(
                        profile_manager,
                        profile_name=profile_name,
                        reason=result.get("throttle_reason", "Facebook restriction"),
                        attempt_id=result.get("attempt_id"),
                    )

                self.queue_manager.record_retry_attempt(
                    campaign_id=campaign_id,
                    job_index=job_index,
                    profile=profile_name,
                    round_num=round_num,
                    success=success,
                    error=error,
                    was_restriction=was_restriction,
                    method=str(method or "auto_retry"),
                    verified=result.get("verified"),
                )

                await broadcast_update("auto_retry_job_result", {
                    "campaign_id": campaign_id,
                    "job_index": job_index,
                    "profile": profile_name,
                    "success": success,
                    "error": error,
                    "method": method,
                    "round": round_num
                })

                if success:
                    round_succeeded += 1
                    succeeded_profiles.add(profile_name)
                else:
                    round_failed += 1

            except Exception as e:
                self.logger.error(f"Auto-retry: job {job_index} exception: {e}")
                self.queue_manager.record_retry_attempt(
                    campaign_id=campaign_id,
                    job_index=job_index,
                    profile=profile_name,
                    round_num=round_num,
                    success=False,
                    error=str(e),
                    was_restriction=False,
                )
                round_failed += 1
            finally:
                await profile_manager.release_profile(profile_name)

        # Check if all jobs are now succeeded or exhausted
        campaign = self.queue_manager.get_campaign_from_history(campaign_id)
        ar = campaign.get("auto_retry", {}) if campaign else {}
        remaining = [fj for fj in ar.get("failed_jobs", []) if not fj.get("exhausted")]

        # Check which remaining jobs still haven't succeeded
        job_successes = {}
        for r in campaign.get("results", []):
            if r.get("success"):
                job_successes[r.get("job_index")] = True

        still_failed = [fj for fj in remaining if not job_successes.get(fj["job_index"])]

        if not still_failed:
            final_status = "completed" if round_succeeded > 0 or not remaining else "exhausted"
            self.queue_manager.complete_auto_retry(campaign_id, final_status)
            await broadcast_update("auto_retry_complete", {
                "campaign_id": campaign_id,
                "final_status": final_status
            })
        else:
            # Advance to next round
            self.queue_manager.advance_retry_round(campaign_id)
            # Re-read to get updated next_retry_at
            campaign = self.queue_manager.get_campaign_from_history(campaign_id)
            next_at = campaign.get("auto_retry", {}).get("next_retry_at") if campaign else None

            await broadcast_update("auto_retry_round_complete", {
                "campaign_id": campaign_id,
                "round": round_num,
                "succeeded": round_succeeded,
                "failed": round_failed,
                "next_retry_at": next_at
            })

        self.logger.info(
            f"Auto-retry round {round_num} done for {campaign_id[:8]}...: "
            f"{round_succeeded} succeeded, {round_failed} failed, {len(still_failed)} still pending"
        )


# Initialize queue processor
queue_processor = QueueProcessor(queue_manager)

# Parallel retry-all infrastructure
_browser_semaphore = asyncio.Semaphore(MAX_CONCURRENT)  # Limit concurrent browser instances
_retry_all_task: Optional[asyncio.Task] = None
_retry_all_progress: Dict = {}


# Helper functions for campaign queue
def normalize_url(url: str) -> str:
    """Extract canonical post identifier from Facebook URL."""
    try:
        patterns = [
            r'/posts/(\d+)',
            r'story_fbid=(\d+)',
            r'/permalink/(\d+)',
            r'/photos/[^/]+/(\d+)',
            r'/(\d+)/?$'
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        # Fallback: use path without query params
        parsed = urlparse(url)
        return f"{parsed.netloc}{parsed.path}".lower()
    except Exception as e:
        logger.debug(f"Error normalizing URL: {e}")
        return url.lower().strip()


def assign_profiles_for_url(count: int, sessions: List[Dict]) -> List[str]:
    """Get profile names for commenting on a URL."""
    valid = [s["profile_name"] for s in sessions if s.get("has_valid_cookies", False)]
    random.shuffle(valid)  # Randomize who comments first
    return valid[:count]


def _is_debug_mode_enabled() -> bool:
    """Debug endpoints are disabled in production unless explicitly allowed."""
    if os.getenv("ENABLE_DEBUG_ENDPOINTS") == "1":
        return True
    return os.getenv("RAILWAY_ENVIRONMENT") is None


def _parse_job_target_comment_id(job: dict) -> Optional[str]:
    """Extract target comment id from a reply job target URL."""
    target_url = str(job.get("target_comment_url") or "").strip()
    if not target_url:
        return None
    return parse_comment_id_from_url(target_url)


def _ensure_full_url(value: str) -> bool:
    """Require absolute URL with scheme + host."""
    try:
        parsed = urlparse(value)
        return bool(parsed.scheme and parsed.netloc)
    except Exception:
        return False


def _cleanup_expired_media() -> None:
    """Evict expired media files from in-memory index and disk."""
    now = datetime.utcnow()
    expired_ids = []
    for image_id, item in media_index.items():
        expires_at = item.get("expires_at")
        if not expires_at:
            continue
        try:
            expiry = datetime.fromisoformat(str(expires_at))
            if now >= expiry:
                expired_ids.append(image_id)
        except Exception:
            expired_ids.append(image_id)

    for image_id in expired_ids:
        item = media_index.pop(image_id, None)
        if not item:
            continue
        try:
            path = Path(item["path"])
            path.unlink(missing_ok=True)
        except Exception as cleanup_err:
            logger.warning(f"Failed to cleanup media {image_id}: {cleanup_err}")


def _get_media_or_none(image_id: str) -> Optional[Dict]:
    """Resolve media metadata and ensure it is not expired."""
    _cleanup_expired_media()
    item = media_index.get(image_id)
    if not item:
        return None
    if not Path(item["path"]).exists():
        media_index.pop(image_id, None)
        return None
    return item


def _build_queue_jobs(
    comments: Optional[List[str]],
    jobs: Optional[List[dict]],
) -> List[dict]:
    """Build canonical jobs payload and enforce lowercase on reply jobs."""
    canonical_jobs = canonicalize_campaign_jobs(comments=comments, jobs=jobs)
    for job in canonical_jobs:
        if job.get("type") == "reply_comment":
            job["text"] = str(job.get("text", "")).lower()
    return canonical_jobs


def _model_to_dict(value) -> dict:
    """Pydantic v1/v2 compatibility helper."""
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return dict(value)


def _build_duplicate_guard_history() -> List[dict]:
    """
    Build duplicate-check corpus from:
    - Completed history (last N days filtered downstream)
    - Active pending/processing campaigns (prevents immediate resubmit duplicates)
    """
    combined: List[dict] = list(queue_manager.get_history(limit=100))
    full_state = queue_manager.get_full_state()
    active_campaigns = full_state.get("pending", [])

    for campaign in active_campaigns:
        jobs = campaign.get("jobs")
        if not jobs:
            try:
                jobs = canonicalize_campaign_jobs(comments=campaign.get("comments") or [], jobs=None)
            except Exception:
                jobs = []

        pseudo_results = []
        for idx, job in enumerate(jobs):
            text = str(job.get("text") or job.get("comment") or "").strip()
            if not text:
                continue
            pseudo_results.append({
                "job_index": idx,
                "text": text,
                "comment": text,
                "success": False,
            })

        combined.append({
            "id": campaign.get("id"),
            "created_at": campaign.get("created_at"),
            "completed_at": campaign.get("completed_at"),
            "results": pseudo_results,
        })

    return combined


def _validate_queue_jobs(
    url: str,
    jobs: List[dict],
    include_duplicate_guard: bool = True,
) -> Dict:
    """
    Validate canonical queue jobs.
    Returns a structured result consumed by both /queue and /debug/queue/validate.
    """
    errors: List[str] = []
    parsed_targets: List[Dict] = []

    if not _ensure_full_url(url):
        errors.append("url must be a full absolute URL")

    for idx, job in enumerate(jobs):
        job_type = str(job.get("type", "")).strip().lower()
        text = str(job.get("text", "")).strip()
        if not text:
            errors.append(f"jobs[{idx}].text is required")
            continue

        if job_type == "reply_comment":
            target_comment_url = str(job.get("target_comment_url") or "").strip()
            if not target_comment_url:
                errors.append(f"jobs[{idx}].target_comment_url is required for reply_comment")
                continue
            if not _ensure_full_url(target_comment_url):
                errors.append(f"jobs[{idx}].target_comment_url must be a full URL")
                continue

            parsed_comment_id = parse_comment_id_from_url(target_comment_url)
            if not parsed_comment_id:
                errors.append(f"jobs[{idx}].target_comment_url must contain parseable comment_id")
                continue

            parsed_targets.append({
                "job_index": idx,
                "target_comment_url": target_comment_url,
                "target_comment_id": parsed_comment_id,
            })

            image_id = str(job.get("image_id") or "").strip()
            if not image_id:
                errors.append(f"jobs[{idx}].image_id is required for reply_comment")
                continue
            if not _get_media_or_none(image_id):
                errors.append(f"jobs[{idx}].image_id not found or expired: {image_id}")

    duplicate_conflicts = []
    duplicate_warning: Optional[str] = None
    if include_duplicate_guard and not errors:
        history = _build_duplicate_guard_history()
        duplicate_conflicts = find_duplicate_text_conflicts(
            candidate_jobs=jobs,
            history=history,
            lookback_days=LOOKBACK_DAYS_DEFAULT,
            threshold=NEAR_DUPLICATE_THRESHOLD,
        )
        if duplicate_conflicts:
            duplicate_warning = (
                f"duplicate_text_guard triggered ({len(duplicate_conflicts)} conflict(s) in current campaign or last {LOOKBACK_DAYS_DEFAULT} days)"
            )

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "jobs": jobs,
        "target_comment_matches": parsed_targets,
        "target_comment_id": parsed_targets[0]["target_comment_id"] if parsed_targets else None,
        "duplicate_conflicts": duplicate_conflicts,
        "duplicate_warning": duplicate_warning,
        "dedupe_window_days": LOOKBACK_DAYS_DEFAULT,
        "near_duplicate_threshold": NEAR_DUPLICATE_THRESHOLD,
    }


# Models
class CommentRequest(BaseModel):
    url: str
    comment: str
    profile_name: str


class CampaignRequest(BaseModel):
    url: str
    comments: List[str]
    profile_names: List[str]
    duration_minutes: int = 30  # Total campaign duration (10-1440 minutes)


class QueuedCampaignItem(BaseModel):
    id: str
    url: str
    comments: List[str]
    duration_minutes: int = 30


class CampaignQueueRequest(BaseModel):
    campaigns: List[QueuedCampaignItem]
    filter_tags: Optional[List[str]] = None  # Tags to filter sessions (AND logic)


class SessionInfo(BaseModel):
    file: str
    profile_name: str
    display_name: Optional[str] = None  # Pretty name for UI display
    user_id: Optional[str]
    extracted_at: str
    valid: bool
    proxy: Optional[str] = None  # "session", "service", or None
    proxy_masked: Optional[str] = None  # Masked proxy URL for display
    proxy_source: Optional[str] = None  # "session" or "env" to show source
    profile_picture: Optional[str] = None  # Base64 encoded PNG
    tags: List[str] = []  # Session tags for filtering


class TagUpdateRequest(BaseModel):
    tags: List[str]


class CredentialAddRequest(BaseModel):
    uid: str
    password: str
    platform: Literal["facebook", "reddit"] = "facebook"
    secret: Optional[str] = None
    profile_name: Optional[str] = None
    username: Optional[str] = None
    email: Optional[str] = None
    email_password: Optional[str] = None
    profile_url: Optional[str] = None
    display_name: Optional[str] = None
    tags: Optional[List[str]] = None
    fixture: bool = False


class CredentialInfo(BaseModel):
    credential_id: Optional[str] = None
    uid: str
    platform: Literal["facebook", "reddit"] = "facebook"
    username: Optional[str] = None
    email: Optional[str] = None
    profile_name: Optional[str]
    display_name: Optional[str] = None
    profile_url: Optional[str] = None
    tags: List[str] = []
    fixture: bool = False
    linked_session_id: Optional[str] = None
    has_secret: bool
    created_at: Optional[str]
    updated_at: Optional[str] = None
    session_connected: bool = False
    session_valid: Optional[bool] = None
    session_profile_name: Optional[str] = None  # Profile name from the linked session


class OTPResponse(BaseModel):
    code: Optional[str]
    remaining_seconds: int
    valid: bool
    error: Optional[str] = None


class ProxyAddRequest(BaseModel):
    name: str
    url: str
    proxy_type: str = "mobile"
    country: str = "US"


class ProxyUpdateRequest(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    proxy_type: Optional[str] = None
    country: Optional[str] = None


class ProxyInfo(BaseModel):
    id: str
    name: str
    url_masked: str
    host: Optional[str]
    port: Optional[int]
    type: str
    country: str
    health_status: str
    last_tested: Optional[str]
    success_rate: Optional[float]
    avg_response_ms: Optional[int]
    test_count: int
    assigned_sessions: List[str]
    created_at: Optional[str]  # Optional for system proxy
    is_system: bool = False  # True for PROXY_URL system proxy
    is_default: bool = False  # True if this is the user-set default proxy


class ProxyTestResult(BaseModel):
    success: bool
    response_time_ms: Optional[int] = None
    ip: Optional[str] = None
    error: Optional[str] = None


class RedditSessionInfo(BaseModel):
    file: str
    platform: Literal["reddit"] = "reddit"
    profile_name: str
    display_name: Optional[str] = None
    username: Optional[str] = None
    email: Optional[str] = None
    profile_url: Optional[str] = None
    extracted_at: Optional[str] = None
    valid: bool
    proxy: Optional[str] = None
    proxy_masked: Optional[str] = None
    proxy_source: Optional[str] = None
    tags: List[str] = []
    fixture: bool = False
    linked_credential_id: Optional[str] = None
    warmup_state: Dict[str, Any] = {}


class RedditSessionCreateRequest(BaseModel):
    credential_id: str
    proxy_id: Optional[str] = None


class RedditSessionBulkCreateRequest(BaseModel):
    lines: List[str]
    fixture: bool = True
    proxy_id: Optional[str] = None
    source_label: Optional[str] = None
    max_create_attempts: int = 2
    wait_for_completion: bool = False


class RedditConvergeUnlinkedRequest(BaseModel):
    usernames: List[str] = []
    proxy_id: Optional[str] = None
    wait_for_completion: bool = False


class RedditReferenceLoginRequest(BaseModel):
    credential_id: str
    reference_session_id: Optional[str] = None


class RedditAuditCompareRequest(BaseModel):
    reference_attempt_id: str
    standalone_attempt_id: str


class RedditBulkSeedRequest(BaseModel):
    lines: List[str]
    fixture: bool = True


class RedditActionRequest(BaseModel):
    profile_name: str
    action: Literal[
        "browse_feed",
        "upvote",
        "upvote_post",
        "upvote_comment",
        "join_subreddit",
        "open_target",
        "create_post",
        "comment_post",
        "reply_comment",
        "upload_media",
    ]
    url: Optional[str] = None
    target_comment_url: Optional[str] = None
    text: Optional[str] = None
    title: Optional[str] = None
    body: Optional[str] = None
    subreddit: Optional[str] = None
    image_id: Optional[str] = None


class RedditMissionCadence(BaseModel):
    type: Literal["once", "daily", "interval_hours"] = "once"
    hour: Optional[int] = None
    minute: Optional[int] = None
    interval_hours: Optional[int] = None


class RedditMissionCreateRequest(BaseModel):
    profile_name: str
    action: Literal[
        "browse_feed",
        "upvote",
        "upvote_post",
        "upvote_comment",
        "join_subreddit",
        "open_target",
        "create_post",
        "comment_post",
        "reply_comment",
        "upload_media",
    ]
    target_url: Optional[str] = None
    target_comment_url: Optional[str] = None
    subreddit: Optional[str] = None
    brief: Optional[str] = None
    exact_text: Optional[str] = None
    title: Optional[str] = None
    body: Optional[str] = None
    image_id: Optional[str] = None
    cadence: RedditMissionCadence = RedditMissionCadence()
    verification_requirements: Optional[List[str]] = None


class RedditMissionUpdateRequest(BaseModel):
    status: Optional[Literal["active", "paused", "completed"]] = None
    brief: Optional[str] = None
    exact_text: Optional[str] = None
    title: Optional[str] = None
    body: Optional[str] = None
    image_id: Optional[str] = None
    target_url: Optional[str] = None
    target_comment_url: Optional[str] = None
    subreddit: Optional[str] = None
    cadence: Optional[RedditMissionCadence] = None


class RedditProgramRandomWindow(BaseModel):
    start_hour: int = 8
    end_hour: int = 22


class RedditProgramProfileSelection(BaseModel):
    profile_names: List[str]


class RedditProgramTopicConstraints(BaseModel):
    subreddits: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)
    explicit_post_targets: List[str] = Field(default_factory=list)
    explicit_comment_targets: List[str] = Field(default_factory=list)
    allow_own_content_targets: bool = False
    mandatory_join_urls: List[str] = Field(default_factory=list)


class RedditProgramAssignment(BaseModel):
    id: Optional[str] = None
    action: Literal[
        "upvote_post",
        "upvote_comment",
        "join_subreddit",
        "open_target",
        "create_post",
        "comment_post",
        "reply_comment",
    ]
    profile_name: str
    day_offset: int = 0
    target_url: Optional[str] = None
    target_comment_url: Optional[str] = None
    text: Optional[str] = None
    title: Optional[str] = None
    body: Optional[str] = None
    subreddit: Optional[str] = None
    verification_requirements: Optional[List[str]] = None


class RedditProgramContentAssignments(BaseModel):
    items: List[RedditProgramAssignment] = Field(default_factory=list)


class RedditProgramEngagementQuotas(BaseModel):
    upvotes_per_day: int = 0
    upvotes_min_per_day: int = 0
    upvotes_max_per_day: int = 0
    posts_min_per_day: int = 0
    posts_max_per_day: int = 0
    comment_upvote_min_per_day: int = 0
    comment_upvote_max_per_day: int = 0
    reply_min_per_day: int = 0
    reply_max_per_day: int = 0
    random_reply_templates: List[str] = Field(default_factory=list)
    random_upvote_action: Literal["upvote_post", "upvote_comment"] = "upvote_post"


class RedditProgramGenerationConfig(BaseModel):
    style_sample_count: int = 3
    writing_rule_paths: List[str] = Field(default_factory=lambda: list(WRITING_RULE_SOURCE_PATHS))
    uniqueness_scope: Literal["program"] = "program"


class RedditProgramRealismPolicy(BaseModel):
    forbid_own_content_interactions: bool = True
    require_conversation_context: bool = True
    require_subreddit_style_match: bool = True
    forbid_operator_language: bool = True
    forbid_meta_testing_language: bool = True


class RedditProgramNotificationConfig(BaseModel):
    email_enabled: bool = True
    email_account_mode: Literal["default_gog_account"] = "default_gog_account"
    daily_summary_hour: int = 20
    hard_failure_alerts_enabled: bool = False
    recipient_email: Optional[str] = None


class RedditProgramSchedule(BaseModel):
    start_at: Optional[str] = None
    duration_days: int = 1
    timezone: str = "Europe/Zurich"
    random_windows: List[RedditProgramRandomWindow] = Field(default_factory=lambda: [RedditProgramRandomWindow()])


class RedditProgramVerificationContract(BaseModel):
    require_success_confirmed: bool = True
    require_attempt_id: bool = True
    required_evidence_summary: bool = True
    required_target_reference: bool = True


class RedditProgramExecutionPolicy(BaseModel):
    strict_quotas: bool = True
    allow_target_reuse_within_day: bool = False
    cooldown_minutes: int = 15
    max_actions_per_tick: int = 3
    max_discovery_posts_per_subreddit: int = 6
    max_comment_candidates_per_post: int = 8
    retry_delay_minutes: int = 20
    max_attempts_per_item: int = 5


class RedditProgramCreateRequest(BaseModel):
    profile_selection: RedditProgramProfileSelection
    schedule: RedditProgramSchedule = Field(default_factory=RedditProgramSchedule)
    topic_constraints: RedditProgramTopicConstraints = Field(default_factory=RedditProgramTopicConstraints)
    content_assignments: RedditProgramContentAssignments = Field(default_factory=RedditProgramContentAssignments)
    engagement_quotas: RedditProgramEngagementQuotas = Field(default_factory=RedditProgramEngagementQuotas)
    generation_config: RedditProgramGenerationConfig = Field(default_factory=RedditProgramGenerationConfig)
    realism_policy: RedditProgramRealismPolicy = Field(default_factory=RedditProgramRealismPolicy)
    notification_config: RedditProgramNotificationConfig = Field(default_factory=RedditProgramNotificationConfig)
    verification_contract: RedditProgramVerificationContract = Field(default_factory=RedditProgramVerificationContract)
    execution_policy: RedditProgramExecutionPolicy = Field(default_factory=RedditProgramExecutionPolicy)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RedditProgramUpdateRequest(BaseModel):
    status: Optional[Literal["active", "paused", "cancelled", "completed"]] = None
    profile_selection: Optional[RedditProgramProfileSelection] = None
    schedule: Optional[RedditProgramSchedule] = None
    topic_constraints: Optional[RedditProgramTopicConstraints] = None
    content_assignments: Optional[RedditProgramContentAssignments] = None
    engagement_quotas: Optional[RedditProgramEngagementQuotas] = None
    generation_config: Optional[RedditProgramGenerationConfig] = None
    realism_policy: Optional[RedditProgramRealismPolicy] = None
    notification_config: Optional[RedditProgramNotificationConfig] = None
    verification_contract: Optional[RedditProgramVerificationContract] = None
    execution_policy: Optional[RedditProgramExecutionPolicy] = None
    metadata: Optional[Dict[str, Any]] = None


class SessionCreateRequest(BaseModel):
    credential_uid: str
    proxy_id: Optional[str] = None


class BatchSessionCreateRequest(BaseModel):
    credential_uids: List[str]
    proxy_id: Optional[str] = None


# ============================================================================
# Authentication Models and Dependencies
# ============================================================================

# OAuth2 scheme - tells FastAPI where to find the token
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)

# API Key header scheme
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    username: str
    role: str
    is_active: bool
    created_at: Optional[str]
    last_login: Optional[str]


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class AdminChangePasswordRequest(BaseModel):
    new_password: str


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    api_key: str = Depends(api_key_header)
) -> dict:
    """
    Dependency that validates JWT or API key and returns current user.
    Supports two authentication methods:
    1. JWT Bearer token (for frontend/users)
    2. X-API-Key header (for programmatic access like Claude testing)
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # Check API key first (for programmatic access)
    if api_key and CLAUDE_API_KEY and api_key == CLAUDE_API_KEY:
        # Return a virtual admin user for API key access
        return {
            "username": "claude_api",
            "role": "admin",
            "is_active": True,
            "created_at": None,
            "last_login": None
        }

    # Fall back to JWT token validation
    if not token:
        raise credentials_exception

    payload = decode_token(token)
    if payload is None:
        raise credentials_exception

    if payload.get("type") != "access":
        raise credentials_exception

    username = payload.get("sub")
    if username is None:
        raise credentials_exception

    user = user_manager.get_user(username)
    if user is None:
        raise credentials_exception

    if not user.get("is_active"):
        raise HTTPException(status_code=403, detail="User account is disabled")

    return user


async def get_admin_user(current_user: dict = Depends(get_current_user)) -> dict:
    """
    Dependency that requires admin role.
    Use this for admin-only endpoints.
    """
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return current_user


# ============================================================================
# Authentication Endpoints
# ============================================================================

@app.post("/auth/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    """Authenticate user and return JWT tokens."""
    user = user_manager.authenticate(request.username, request.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(data={"sub": user["username"]})
    refresh_token = create_refresh_token(data={"sub": user["username"]})

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token
    )


@app.post("/auth/refresh", response_model=TokenResponse)
async def refresh_tokens(request: RefreshRequest):
    """Get new access token using refresh token."""
    payload = decode_token(request.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token"
        )

    username = payload.get("sub")
    user = user_manager.get_user(username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found"
        )

    if not user.get("is_active"):
        raise HTTPException(status_code=403, detail="User account is disabled")

    new_access_token = create_access_token(data={"sub": username})
    new_refresh_token = create_refresh_token(data={"sub": username})

    return TokenResponse(
        access_token=new_access_token,
        refresh_token=new_refresh_token
    )


@app.get("/auth/me", response_model=UserResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    """Get current authenticated user info."""
    return current_user


@app.post("/auth/change-password")
async def change_own_password(
    request: ChangePasswordRequest,
    current_user: dict = Depends(get_current_user)
):
    """Change current user's password."""
    # Verify current password
    user_with_pwd = user_manager.get_user_with_password(current_user["username"])
    if not verify_password(request.current_password, user_with_pwd["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect"
        )

    user_manager.change_password(current_user["username"], request.new_password)
    return {"success": True, "message": "Password changed successfully"}


# ============================================================================
# User Management Endpoints (Admin Only)
# ============================================================================

@app.get("/users", response_model=List[UserResponse])
async def list_users(admin: dict = Depends(get_admin_user)):
    """List all users (admin only)."""
    return user_manager.list_users()


@app.post("/users", response_model=UserResponse)
async def create_user(request: CreateUserRequest, admin: dict = Depends(get_admin_user)):
    """Create a new user (admin only)."""
    if request.role not in ("admin", "user"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role must be 'admin' or 'user'"
        )

    user = user_manager.create_user(request.username, request.password, request.role)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists"
        )
    return user


@app.delete("/users/{username}")
async def delete_user(username: str, admin: dict = Depends(get_admin_user)):
    """Delete a user (admin only)."""
    # Cannot delete yourself
    if username == admin["username"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete yourself"
        )

    # Cannot delete last admin
    if user_manager.is_last_admin(username):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete the last admin user"
        )

    success = user_manager.delete_user(username)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    return {"success": True, "username": username}


@app.put("/users/{username}/password")
async def admin_change_password(
    username: str,
    request: AdminChangePasswordRequest,
    admin: dict = Depends(get_admin_user)
):
    """Change any user's password (admin only)."""
    success = user_manager.change_password(username, request.new_password)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    return {"success": True, "message": f"Password changed for {username}"}


@app.put("/users/{username}/role")
async def change_user_role(
    username: str,
    role: str,
    admin: dict = Depends(get_admin_user)
):
    """Change a user's role (admin only)."""
    if role not in ("admin", "user"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role must be 'admin' or 'user'"
        )

    # Cannot demote yourself if you're the last admin
    if username == admin["username"] and role != "admin":
        if user_manager.is_last_admin(username):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot demote the last admin"
            )

    success = user_manager.update_role(username, role)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    return {"success": True, "username": username, "role": role}


# Endpoints
@app.get("/")
async def root():
    return {"status": "ok", "message": "CommentBot API"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.get("/health/deep")
async def health_deep():
    """
    Deep health check — returns full system status in one call.
    Checks: Gemini circuit breaker, sessions, disk, queue, profiles, proxy health.
    """
    import shutil

    checks = {}
    overall = "healthy"

    # 1. Gemini circuit breaker status
    try:
        from gemini_vision import get_circuit_breaker
        cb = get_circuit_breaker()
        checks["gemini"] = cb.get_status()
        if cb.state == "open":
            overall = "degraded"
    except Exception as e:
        checks["gemini"] = {"error": str(e)}
        overall = "degraded"

    # 2. Session validity
    try:
        sessions = list_saved_sessions()
        valid_count = sum(1 for s in sessions if s.get("has_valid_cookies"))
        checks["sessions"] = {
            "total": len(sessions),
            "valid": valid_count,
            "invalid": len(sessions) - valid_count
        }
        if valid_count == 0:
            overall = "critical"
    except Exception as e:
        checks["sessions"] = {"error": str(e)}
        overall = "degraded"

    # 3. Disk usage
    try:
        data_dir = os.getenv("DATA_DIR", "/data")
        if os.path.exists(data_dir):
            usage = shutil.disk_usage(data_dir)
            free_gb = round(usage.free / (1024**3), 2)
            used_pct = round((usage.used / usage.total) * 100, 1)
            checks["disk"] = {"free_gb": free_gb, "used_pct": used_pct}
            if used_pct > 90:
                overall = "degraded"
        else:
            checks["disk"] = {"free_gb": None, "used_pct": None, "note": "DATA_DIR not found"}
    except Exception as e:
        checks["disk"] = {"error": str(e)}

    # 4. Queue status (read-only against live in-memory manager)
    try:
        pending_count = queue_manager.count_pending()
        processor_running = queue_manager.is_processor_running()
        current_campaign_id = queue_manager.processor_state.get("current_campaign_id")
        checks["queue"] = {
            "pending": pending_count,
            "processor_running": processor_running,
            "total_campaigns": len(queue_manager.campaigns),
            "current_campaign_id": current_campaign_id,
        }
    except Exception as e:
        checks["queue"] = {"error": str(e)}

    # 5. Profile stats
    try:
        from profile_manager import ProfileManager
        pm = ProfileManager()
        profiles = pm.state.get("profiles", {})
        active = sum(1 for p in profiles.values() if p.get("status") == "active")
        restricted = sum(1 for p in profiles.values() if p.get("status") == "restricted")
        checks["profiles"] = {
            "active": active,
            "restricted": restricted,
            "total": len(profiles)
        }
        if active == 0:
            overall = "critical"
    except Exception as e:
        checks["profiles"] = {"error": str(e)}

    # 6. Proxy health
    try:
        proxy_mgr = ProxyManager()
        proxies = proxy_mgr.list_proxies()
        recent_failures = sum(
            1 for p in proxies
            if p.get("health_status") in ("failed", "unhealthy")
        )
        runtime_proxy_url = get_system_proxy()
        runtime_source = "none"
        default_proxy = proxy_mgr.get_default_proxy()
        if default_proxy and default_proxy.get("url"):
            runtime_source = "default"
        elif os.getenv("PROXY_URL"):
            runtime_source = "env"

        runtime = {
            "configured": bool(runtime_proxy_url),
            "healthy": None,
            "ip": None,
            "response_ms": None,
            "error": None,
            "source": runtime_source,
        }
        if runtime_proxy_url:
            runtime_health = await check_proxy_health()
            runtime = {
                "configured": True,
                "healthy": runtime_health.get("healthy"),
                "ip": runtime_health.get("ip"),
                "response_ms": runtime_health.get("response_ms"),
                "error": runtime_health.get("error"),
                "source": runtime_source,
            }
            if runtime.get("healthy") is False:
                overall = "degraded"

        checks["proxy"] = {
            "total": len(proxies),
            "recent_failures": recent_failures,
            "runtime": runtime,
        }
    except Exception as e:
        checks["proxy"] = {"error": str(e)}

    return {
        "status": overall,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "checks": checks
    }


# =============================================================================
# DEBUG / ANALYTICS ENDPOINTS
# =============================================================================

@app.get("/debug/gemini-logs")
async def get_gemini_logs(
    limit: int = Query(default=20, ge=1, le=100),
    current_user: dict = Depends(get_current_user)
):
    """
    Get recent Gemini AI observations for debugging.
    Returns full AI responses, not just parsed results.
    """
    from gemini_vision import get_recent_observations
    observations = get_recent_observations(limit=limit)
    return {
        "count": len(observations),
        "observations": observations
    }


@app.post("/debug/gemini-logs/clear")
async def clear_gemini_logs(current_user: dict = Depends(get_current_user)):
    """Clear stored Gemini observations."""
    from gemini_vision import clear_observations
    count = clear_observations()
    return {"cleared": count}


# =============================================================================
# FORENSIC INVESTIGATION ENDPOINTS
# =============================================================================

@app.get("/forensics/attempts")
async def get_forensics_attempts(
    platform: Optional[str] = Query(default=None),
    engine: Optional[str] = Query(default=None),
    campaign_id: Optional[str] = Query(default=None),
    profile_name: Optional[str] = Query(default=None),
    run_id: Optional[str] = Query(default=None),
    final_verdict: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    current_user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    filters = {
        key: value
        for key, value in {
            "platform": platform,
            "engine": engine,
            "campaign_id": campaign_id,
            "profile_name": profile_name,
            "run_id": run_id,
            "final_verdict": final_verdict,
        }.items()
        if value
    }
    attempts = await list_forensic_attempts(filters=filters or None, limit=limit)
    return {"count": len(attempts), "attempts": attempts}


@app.get("/forensics/attempts/{attempt_id}")
async def get_forensics_attempt(attempt_id: str, current_user: dict = Depends(get_current_user)) -> Dict[str, Any]:
    detail = await get_forensic_attempt_detail(attempt_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Forensic attempt not found")
    return detail


@app.get("/forensics/attempts/{attempt_id}/timeline")
async def get_forensics_attempt_timeline(attempt_id: str, current_user: dict = Depends(get_current_user)) -> Dict[str, Any]:
    detail = await get_forensic_attempt_detail(attempt_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Forensic attempt not found")
    return {
        "attempt": detail.get("attempt"),
        "timeline": detail.get("events", []),
        "artifacts": detail.get("artifacts", []),
        "verdict": detail.get("verdict"),
        "links": detail.get("links"),
    }


@app.get("/forensics/campaigns/{campaign_id}/forensics")
async def get_campaign_forensics(campaign_id: str, current_user: dict = Depends(get_current_user)) -> Dict[str, Any]:
    return await build_forensic_group({"campaign_id": campaign_id}, limit=500)


@app.get("/forensics/profiles/{profile_name}/forensics")
async def get_profile_forensics(profile_name: str, current_user: dict = Depends(get_current_user)) -> Dict[str, Any]:
    return await build_forensic_group({"profile_name": profile_name}, limit=500)


@app.get("/forensics/runs/{run_id}")
async def get_run_forensics(run_id: str, current_user: dict = Depends(get_current_user)) -> Dict[str, Any]:
    return await build_forensic_group({"run_id": run_id}, limit=500)


@app.get("/forensics/artifacts/{artifact_id}")
async def get_forensics_artifact(artifact_id: str, current_user: dict = Depends(get_current_user)):
    artifact = await get_forensic_artifact_by_id(artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="Forensic artifact not found")
    response = await download_forensic_artifact_bytes(artifact_id)
    if response is None:
        raise HTTPException(status_code=404, detail="Forensic artifact payload not found")
    return Response(
        content=response.content,
        media_type=artifact.get("content_type") or response.headers.get("content-type") or "application/octet-stream",
        headers={"Cache-Control": "private, max-age=60"},
    )


# =============================================================================
# PROFILE ANALYTICS ENDPOINTS
# =============================================================================

@app.get("/analytics/summary")
async def get_analytics_summary(current_user: dict = Depends(get_current_user)):
    """Get summary analytics for all profiles."""
    from profile_manager import get_profile_manager
    pm = get_profile_manager()
    attempt_summary = pm.get_analytics_summary()

    history = queue_manager.get_history(limit=100)
    pending = queue_manager.get_full_state().get("pending", [])
    delivery_rows = history + pending

    today = datetime.utcnow().strftime("%Y-%m-%d")
    week_start = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")

    delivery_today_total = 0
    delivery_today_success = 0
    delivery_week_total = 0
    delivery_week_success = 0
    retry_backlog_campaigns = 0
    retry_backlog_jobs = 0
    overdue_retry_campaigns = 0
    overdue_retry_jobs = 0

    for campaign in delivery_rows:
        reference_date = (
            str(campaign.get("completed_at") or campaign.get("created_at") or campaign.get("started_at") or "")[:10]
        )
        total_jobs = get_campaign_total_jobs(campaign)
        success_jobs = get_campaign_success_count(campaign)
        remaining_jobs = max(0, total_jobs - success_jobs)

        if reference_date == today:
            delivery_today_total += total_jobs
            delivery_today_success += success_jobs
        if reference_date and reference_date >= week_start:
            delivery_week_total += total_jobs
            delivery_week_success += success_jobs

        if remaining_jobs > 0:
            retry_backlog_campaigns += 1
            retry_backlog_jobs += remaining_jobs
            if (campaign.get("retry_overdue_seconds") or 0) > 0:
                overdue_retry_campaigns += 1
                overdue_retry_jobs += remaining_jobs

    return {
        "today": {
            "comments": delivery_today_total,
            "success": delivery_today_success,
            "success_rate": (delivery_today_success / delivery_today_total * 100) if delivery_today_total > 0 else 0,
        },
        "week": {
            "comments": delivery_week_total,
            "success": delivery_week_success,
            "success_rate": (delivery_week_success / delivery_week_total * 100) if delivery_week_total > 0 else 0,
        },
        "attempt_today": attempt_summary.get("today", {}),
        "attempt_week": attempt_summary.get("week", {}),
        "profiles": attempt_summary.get("profiles", {}),
        "retry_backlog": {
            "campaigns": retry_backlog_campaigns,
            "jobs": retry_backlog_jobs,
        },
        "overdue_retries": {
            "campaigns": overdue_retry_campaigns,
            "jobs": overdue_retry_jobs,
        },
    }


@app.get("/analytics/profiles")
async def get_all_profile_analytics(current_user: dict = Depends(get_current_user)):
    """Get analytics for all session-backed profiles."""
    from profile_manager import get_profile_manager
    from fb_session import list_saved_sessions
    pm = get_profile_manager()
    pm.refresh_from_sessions()

    profiles = []
    for session in list_saved_sessions():
        raw_name = session.get("profile_name") or ""
        normalized = raw_name.replace(" ", "_").replace("/", "_").lower()
        analytics = pm.get_profile_analytics(normalized)
        if not analytics:
            continue
        analytics["display_name"] = session.get("display_name") or raw_name or normalized
        profiles.append(analytics)

    profiles.sort(key=lambda p: p.get("display_name") or p.get("profile_name") or "")
    return {"profiles": profiles}


@app.get("/analytics/profiles/{profile_name}")
async def get_profile_analytics(
    profile_name: str,
    current_user: dict = Depends(get_current_user)
):
    """Get detailed analytics for a single profile."""
    from profile_manager import get_profile_manager
    from fb_session import list_saved_sessions
    pm = get_profile_manager()
    pm.refresh_from_sessions()

    analytics = pm.get_profile_analytics(profile_name)
    if not analytics:
        raise HTTPException(status_code=404, detail="Profile not found")
    display_name = analytics["profile_name"]
    for session in list_saved_sessions():
        raw_name = session.get("profile_name") or ""
        normalized = raw_name.replace(" ", "_").replace("/", "_").lower()
        if normalized == analytics["profile_name"]:
            display_name = session.get("display_name") or raw_name or analytics["profile_name"]
            break
    analytics["display_name"] = display_name
    return analytics


@app.post("/analytics/profiles/{profile_name}/unblock")
async def unblock_profile(
    profile_name: str,
    reset_stats: bool = False,
    current_user: dict = Depends(get_current_user)
):
    """Manually unblock a restricted profile. Keeps stats by default so the profile stays visible in analytics."""
    from profile_manager import get_profile_manager
    pm = get_profile_manager()

    pm.unblock_profile(
        profile_name,
        reset_stats=reset_stats,
        recovery_event="manual_unblock",
        recovery_state="resolved",
        recovery_details={"reset_stats": reset_stats, "actor": current_user.get("username")},
    )
    return {"success": True, "profile_name": profile_name}


@app.post("/analytics/profiles/{profile_name}/restrict")
async def restrict_profile(
    profile_name: str,
    hours: int = Query(default=24, ge=1, le=168),
    reason: str = Query(default="manual"),
    current_user: dict = Depends(get_current_user)
):
    """Manually restrict a profile for a duration."""
    from profile_manager import get_profile_manager
    pm = get_profile_manager()

    pm.mark_profile_restricted(profile_name, hours=hours, reason=reason)
    return {"success": True, "profile_name": profile_name, "hours": hours}


@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket, token: str = Query(None)):
    """WebSocket endpoint for live updates. Requires token query parameter."""
    # Validate token before accepting connection
    if not token:
        await websocket.close(code=4001, reason="Token required")
        return

    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        await websocket.close(code=4001, reason="Invalid token")
        return

    username = payload.get("sub")
    user = user_manager.get_user(username)
    if not user or not user.get("is_active"):
        await websocket.close(code=4001, reason="User not found or inactive")
        return

    await websocket.accept()
    active_connections.add(websocket)
    logger.info(f"WS connected for user {username}. Total: {len(active_connections)}")

    # Send current queue state on connect for immediate sync
    try:
        queue_state = queue_manager.get_full_state()
        await websocket.send_text(json.dumps({
            "type": "queue_state_sync",
            "data": queue_state,
            "timestamp": datetime.now().isoformat()
        }))
        await websocket.send_text(json.dumps({
            "type": "drafts_state_sync",
            "data": {"drafts": draft_manager.list_drafts()},
            "timestamp": datetime.now().isoformat()
        }))
    except Exception as e:
        logger.warning(f"Failed to send queue state on connect: {e}")

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        active_connections.discard(websocket)
        logger.info(f"WS disconnected. Total: {len(active_connections)}")


@app.get("/sessions")
async def get_sessions(current_user: dict = Depends(get_current_user)) -> List[SessionInfo]:
    """Get all saved sessions with proxy info."""
    from urllib.parse import urlparse

    sessions = list_saved_sessions()
    results = []

    for s in sessions:
        # Load session to get actual proxy URL
        session = FacebookSession(s["profile_name"])
        stored_proxy = None
        if session.load():
            stored_proxy = session.get_proxy()

        # Determine proxy source and masked URL
        if stored_proxy:
            parsed = urlparse(stored_proxy)
            proxy_masked = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
            proxy_source = "session"
            proxy_label = "session"
        elif PROXY_URL:
            parsed = urlparse(PROXY_URL)
            proxy_masked = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
            proxy_source = "env"
            proxy_label = "service"
        else:
            proxy_masked = None
            proxy_source = None
            proxy_label = None

        results.append(SessionInfo(
            file=s["file"],
            profile_name=s["profile_name"],
            display_name=s.get("display_name"),
            user_id=s.get("user_id"),
            extracted_at=s["extracted_at"],
            valid=s["has_valid_cookies"],
            proxy=proxy_label,
            proxy_masked=proxy_masked,
            proxy_source=proxy_source,
            profile_picture=s.get("profile_picture"),
            tags=s.get("tags", []),
        ))

    return results


@app.get("/sessions/audit-proxies")
async def audit_session_proxies(current_user: dict = Depends(get_current_user)) -> List[Dict]:
    """
    Audit all sessions to show actual proxy values.
    Used to verify if stored proxies match PROXY_URL environment variable.
    """
    sessions = list_saved_sessions()
    results = []
    for s in sessions:
        session = FacebookSession(s["profile_name"])
        if session.load():
            stored_proxy = session.get_proxy() or ""
            matches = stored_proxy == PROXY_URL if stored_proxy else False
            results.append({
                "profile_name": s["profile_name"],
                "has_proxy": bool(stored_proxy),
                "matches_env_proxy": matches,
                "stored_proxy_masked": stored_proxy[:30] + "..." if stored_proxy else None,
                "env_proxy_masked": PROXY_URL[:30] + "..." if PROXY_URL else None,
            })
    return results


@app.post("/sessions/sync-all-to-env-proxy")
async def sync_all_sessions_to_env_proxy(current_user: dict = Depends(get_current_user)) -> Dict:
    """
    Force ALL sessions to use the PROXY_URL environment variable.
    This updates the proxy field in each session's JSON file.
    """
    if not PROXY_URL:
        raise HTTPException(400, "PROXY_URL environment variable not set")

    sessions = list_saved_sessions()
    updated = 0
    for s in sessions:
        session = FacebookSession(s["profile_name"])
        if session.load():
            session.data["proxy"] = PROXY_URL
            session.save()
            updated += 1

    return {
        "success": True,
        "updated": updated,
        "total": len(sessions),
        "proxy_masked": PROXY_URL[:30] + "..."
    }


@app.post("/sessions/{profile_name}/test")
async def test_session_endpoint(profile_name: str, current_user: dict = Depends(get_current_user)) -> Dict:
    """Test if a session is valid."""
    session = FacebookSession(profile_name)
    result = await test_session(session, get_system_proxy())
    return result


@app.delete("/sessions/{profile_name}")
async def delete_session(profile_name: str, current_user: dict = Depends(get_current_user)) -> Dict:
    """Delete a session by profile name."""
    session = FacebookSession(profile_name)
    if not session.session_file.exists():
        raise HTTPException(status_code=404, detail=f"Session not found: {profile_name}")

    try:
        session.session_file.unlink()
        logger.info(f"Deleted session: {profile_name}")
        return {"success": True, "profile_name": profile_name}
    except Exception as e:
        logger.error(f"Failed to delete session {profile_name}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete session: {e}")


@app.get("/tags")
async def get_all_tags_endpoint(current_user: dict = Depends(get_current_user)) -> List[str]:
    """Get all unique tags across all sessions."""
    from fb_session import get_all_tags
    return get_all_tags()


@app.put("/sessions/{profile_name}/tags")
async def update_session_tags_endpoint(
    profile_name: str,
    request: TagUpdateRequest,
    current_user: dict = Depends(get_current_user)
) -> Dict:
    """Update tags for a session."""
    from fb_session import update_session_tags
    success = update_session_tags(profile_name, request.tags)
    if not success:
        raise HTTPException(status_code=404, detail=f"Session not found: {profile_name}")
    return {"success": True, "tags": request.tags}


@app.put("/sessions/{profile_name}/display-name")
async def update_session_display_name(
    profile_name: str,
    request: Dict,
    current_user: dict = Depends(get_current_user)
) -> Dict:
    """Update display_name for a session."""
    session = FacebookSession(profile_name)
    if not session.load():
        raise HTTPException(status_code=404, detail=f"Session not found: {profile_name}")
    session.data["display_name"] = request.get("display_name", profile_name)
    session.save()
    return {"success": True, "display_name": session.data["display_name"]}


@app.post("/comment")
async def post_comment_endpoint(request: CommentRequest, current_user: dict = Depends(get_current_user)) -> Dict:
    """Post a comment using a saved session."""
    session = FacebookSession(request.profile_name)
    
    if not session.load():
        raise HTTPException(status_code=404, detail=f"Session not found: {request.profile_name}")
    
    if not session.has_valid_cookies():
        raise HTTPException(status_code=401, detail=f"Invalid session: {request.profile_name}")
    
    logger.info(f"Posting comment for {request.profile_name}: {request.url}")

    # Use the verified version with step-by-step verification
    result = await post_comment_verified(
        session=session,
        url=request.url,
        comment=request.comment,
        proxy=get_system_proxy(),
        forensic_context={"platform": "facebook", "engine": "direct_comment", "run_id": "direct_comment"},
    )
    
    if not result["success"]:
        # Return error but don't crash - let frontend see the error details
        return result
        # raise HTTPException(status_code=500, detail=result["error"])
    
    return result


@app.post("/campaign")
async def run_campaign(request: CampaignRequest, current_user: dict = Depends(get_current_user)) -> Dict:
    """Run a campaign with staggered timing - comments spread across specified duration."""
    total_jobs = min(len(request.profile_names), len(request.comments))
    duration_seconds = request.duration_minutes * 60

    # Calculate base delay between jobs (spread jobs across duration)
    # For N jobs, we have N-1 gaps between them
    base_delay = duration_seconds / total_jobs if total_jobs > 1 else 0

    estimated_completion = datetime.now() + timedelta(minutes=request.duration_minutes)

    await broadcast_update("campaign_start", {
        "url": request.url,
        "total_jobs": total_jobs,
        "duration_minutes": request.duration_minutes,
        "estimated_completion": estimated_completion.isoformat()
    })

    async def process_one(job_index: int, profile_name: str, comment: str) -> Dict:
        """Process a single comment job."""
        await broadcast_update("job_start", {
            "job_index": job_index,
            "profile_name": profile_name,
            "comment": comment[:50]
        })

        session = FacebookSession(profile_name)

        if not session.load():
            await broadcast_update("job_error", {"job_index": job_index, "error": "Session not found"})
            return {"profile_name": profile_name, "success": False, "error": "Session not found", "job_index": job_index}

        try:
            result = await post_comment_verified(
                session=session,
                url=request.url,
                comment=comment,
                proxy=get_system_proxy(),
                forensic_context={
                    "platform": "facebook",
                    "engine": "staggered_campaign_comment",
                    "run_id": request.url,
                    "job_id": str(job_index),
                },
            )

            await broadcast_update("job_complete", {
                "job_index": job_index,
                "profile_name": profile_name,
                "success": result["success"],
                "verified": result.get("verified", False),
                "method": result.get("method", "unknown"),
                "error": result.get("error")
            })

            return {
                "profile_name": profile_name,
                "comment": comment,
                "success": result["success"],
                "verified": result.get("verified", False),
                "method": result.get("method", "unknown"),
                "error": result.get("error"),
                "job_index": job_index
            }
        except Exception as e:
            logger.error(f"Error processing job {job_index}: {e}")
            await broadcast_update("job_error", {"job_index": job_index, "error": str(e)})
            return {"profile_name": profile_name, "success": False, "error": str(e), "job_index": job_index}

    # Process jobs sequentially with staggered delays
    results = []

    for i, (profile_name, comment) in enumerate(
        zip(request.profile_names[:total_jobs], request.comments[:total_jobs])
    ):
        # First job runs immediately, subsequent jobs wait with randomized delay
        if i > 0:
            # Add ±20% jitter to avoid predictable patterns
            jitter = random.uniform(0.8, 1.2)
            delay_seconds = base_delay * jitter

            await broadcast_update("job_waiting", {
                "job_index": i,
                "delay_seconds": round(delay_seconds),
                "profile_name": profile_name
            })

            logger.info(f"Waiting {delay_seconds:.0f}s before job {i} ({profile_name})")
            await asyncio.sleep(delay_seconds)

        # Process this job
        try:
            result = await process_one(i, profile_name, comment)
            results.append(result)
        except Exception as e:
            logger.error(f"Error processing job {i}: {e}")
            results.append({"profile_name": profile_name, "success": False, "error": str(e), "job_index": i})

    success_count = sum(1 for r in results if r.get("success"))
    await broadcast_update("campaign_complete", {"total": len(results), "success": success_count})

    return {"url": request.url, "total": len(results), "success": success_count, "results": results}


@app.post("/campaign/queue")
async def run_campaign_queue(request: CampaignQueueRequest, current_user: dict = Depends(get_current_user)) -> Dict:
    """Run multiple campaigns sequentially with per-URL profile assignment."""

    # Get all valid sessions
    sessions_list = list_saved_sessions()
    valid_profiles = [s for s in sessions_list if s.get("has_valid_cookies", False)]

    # Filter by tags if specified (AND logic - must match ALL tags)
    if request.filter_tags:
        valid_profiles = [
            s for s in valid_profiles
            if all(tag in s.get("tags", []) for tag in request.filter_tags)
        ]
        logger.info(f"Filtered to {len(valid_profiles)} profiles matching tags: {request.filter_tags}")

    valid_count = len(valid_profiles)

    if valid_count == 0:
        if request.filter_tags:
            raise HTTPException(status_code=400, detail=f"No valid sessions match tags: {request.filter_tags}")
        raise HTTPException(status_code=400, detail="No valid sessions available")

    # Validate per-URL limits
    url_comment_counts: Dict[str, int] = {}
    for campaign in request.campaigns:
        normalized = normalize_url(campaign.url)
        url_comment_counts[normalized] = url_comment_counts.get(normalized, 0) + len(campaign.comments)

    for url, count in url_comment_counts.items():
        if count > valid_count:
            raise HTTPException(
                status_code=400,
                detail=f"URL has {count} total comments but only {valid_count} profiles available"
            )

    # Broadcast queue start
    await broadcast_update("queue_start", {
        "total_campaigns": len(request.campaigns),
        "campaigns": [{"id": c.id, "url": c.url, "comment_count": len(c.comments)} for c in request.campaigns]
    })

    all_results = []

    # Track which profiles have been used for each URL
    url_used_profiles: Dict[str, Set[str]] = {}

    for idx, campaign in enumerate(request.campaigns):
        await broadcast_update("queue_campaign_start", {
            "campaign_id": campaign.id,
            "campaign_index": idx,
            "url": campaign.url
        })

        normalized_url = normalize_url(campaign.url)

        # Get available profiles for this URL (not yet used for this URL)
        used_for_url = url_used_profiles.get(normalized_url, set())
        available_for_url = [p["profile_name"] for p in valid_profiles if p["profile_name"] not in used_for_url]

        # Shuffle and assign profiles
        random.shuffle(available_for_url)
        assigned_profiles = available_for_url[:len(campaign.comments)]

        # Mark these profiles as used for this URL
        if normalized_url not in url_used_profiles:
            url_used_profiles[normalized_url] = set()
        url_used_profiles[normalized_url].update(assigned_profiles)

        # Calculate timing for this campaign
        total_jobs = len(campaign.comments)
        duration_seconds = campaign.duration_minutes * 60
        base_delay = duration_seconds / total_jobs if total_jobs > 1 else 0

        # Broadcast campaign start
        await broadcast_update("campaign_start", {
            "url": campaign.url,
            "total_jobs": total_jobs,
            "duration_minutes": campaign.duration_minutes
        })

        # Process jobs for this campaign
        campaign_results = []

        for job_idx, (profile_name, comment) in enumerate(zip(assigned_profiles, campaign.comments)):
            # Staggered delay (except first job)
            if job_idx > 0:
                jitter = random.uniform(0.8, 1.2)
                delay_seconds = base_delay * jitter

                await broadcast_update("job_waiting", {
                    "job_index": job_idx,
                    "delay_seconds": round(delay_seconds),
                    "profile_name": profile_name
                })

                logger.info(f"Queue campaign {idx+1}: Waiting {delay_seconds:.0f}s before job {job_idx} ({profile_name})")
                await asyncio.sleep(delay_seconds)

            await broadcast_update("job_start", {
                "job_index": job_idx,
                "profile_name": profile_name,
                "comment": comment[:50]
            })

            session = FacebookSession(profile_name)

            if not session.load():
                await broadcast_update("job_error", {"job_index": job_idx, "error": "Session not found"})
                campaign_results.append({
                    "profile_name": profile_name,
                    "success": False,
                    "error": "Session not found",
                    "job_index": job_idx
                })
                continue

            try:
                result = await post_comment_verified(
                    session=session,
                    url=campaign.url,
                    comment=comment,
                    proxy=get_system_proxy(),
                    forensic_context={
                        "platform": "facebook",
                        "engine": "campaign_comment",
                        "run_id": campaign.url,
                        "job_id": str(job_idx),
                    },
                )

                await broadcast_update("job_complete", {
                    "job_index": job_idx,
                    "profile_name": profile_name,
                    "success": result["success"],
                    "verified": result.get("verified", False),
                    "method": result.get("method", "unknown"),
                    "error": result.get("error")
                })

                campaign_results.append({
                    "profile_name": profile_name,
                    "comment": comment,
                    "success": result["success"],
                    "verified": result.get("verified", False),
                    "method": result.get("method", "unknown"),
                    "error": result.get("error"),
                    "job_index": job_idx
                })
            except Exception as e:
                logger.error(f"Error processing job {job_idx} in campaign {campaign.id}: {e}")
                await broadcast_update("job_error", {"job_index": job_idx, "error": str(e)})
                campaign_results.append({
                    "profile_name": profile_name,
                    "success": False,
                    "error": str(e),
                    "job_index": job_idx
                })

        success_count = sum(1 for r in campaign_results if r.get("success"))
        await broadcast_update("campaign_complete", {"total": len(campaign_results), "success": success_count})

        campaign_result = {
            "campaign_id": campaign.id,
            "url": campaign.url,
            "total": len(campaign_results),
            "success": success_count,
            "results": campaign_results
        }

        all_results.append(campaign_result)

        await broadcast_update("queue_campaign_complete", {
            "campaign_id": campaign.id,
            "success": success_count,
            "total": len(campaign_results)
        })

    await broadcast_update("queue_complete", {"results": all_results})

    return {"campaigns": all_results}


# =========================================================================
# Persistent Queue API Endpoints
# =========================================================================

class QueueJob(BaseModel):
    type: Literal["post_comment", "reply_comment"] = "post_comment"
    text: str
    target_comment_url: Optional[str] = None
    image_id: Optional[str] = None


class AddToQueueRequest(BaseModel):
    url: str
    comments: Optional[List[str]] = None  # Backward-compatible legacy field
    jobs: Optional[List[QueueJob]] = None  # Canonical queue field
    duration_minutes: int = 30
    filter_tags: Optional[List[str]] = None
    enable_warmup: bool = True  # Warmup enabled by default for new campaigns
    profile_name: Optional[str] = None  # Optional forced profile for all jobs


class DraftRequest(BaseModel):
    url: str
    comments: Optional[List[str]] = None
    jobs: Optional[List[QueueJob]] = None
    duration_minutes: int = 30
    filter_tags: Optional[List[str]] = None
    enable_warmup: bool = True


class CampaignAIContextRequest(BaseModel):
    url: str


class CampaignAIGenerateRequest(BaseModel):
    url: str
    product_id: str
    comment_count: int = 10
    filter_tags: Optional[List[str]] = None
    enable_warmup: bool = True
    draft_id: Optional[str] = None


class CampaignAIRegenerateOneRequest(BaseModel):
    index: int


class CampaignAIProductRequest(BaseModel):
    name: str
    prompt: str


class CampaignAIProductUpdateRequest(BaseModel):
    name: Optional[str] = None
    prompt: Optional[str] = None
    active: Optional[bool] = None


class DebugQueueValidateRequest(BaseModel):
    url: str
    comments: Optional[List[str]] = None
    jobs: Optional[List[QueueJob]] = None


class RetryJobRequest(BaseModel):
    """Request to retry a failed job in a completed campaign."""
    job_index: int
    profile_name: str
    comment: str
    original_profile: Optional[str] = None  # Track which profile originally failed


class MediaUploadResponse(BaseModel):
    success: bool
    image_id: Optional[str] = None
    filename: Optional[str] = None
    size: Optional[int] = None
    content_type: Optional[str] = None
    expires_at: Optional[str] = None
    error: Optional[str] = None


class DedupeWorkflowRequest(BaseModel):
    mode: Literal["dry_run", "apply"]
    plan_id: Optional[str] = None


# NOTE: BulkRetryRequest removed - bulk retry endpoint now takes no parameters
# and handles everything automatically with smart retry-until-success logic


class TestCampaignRequest(BaseModel):
    """Request for parallel test campaign - runs independently of main queue."""
    url: str
    comments: List[str]
    filter_tags: Optional[List[str]] = None
    enable_warmup: bool = True  # Default to True for testing
    profile_name: Optional[str] = None  # Optional: use specific profile instead of LRU selection


@app.post("/test-campaign")
async def run_test_campaign(request: TestCampaignRequest, current_user: dict = Depends(get_current_user)) -> Dict:
    """
    Run a test campaign INDEPENDENTLY of the main queue.

    This endpoint:
    - Runs in parallel with scheduled campaigns (doesn't wait in queue)
    - Uses UNIFIED profile selection (tags, restrictions, LRU by success)
    - Uses full pipeline: warmup, vision, posting, analytics tracking
    - Useful for testing while production campaigns are running
    - Does NOT add to queue or affect queue state
    """
    from profile_manager import get_profile_manager
    from gemini_vision import set_observation_context

    profile_manager = get_profile_manager()
    canonical_jobs = _build_queue_jobs(comments=request.comments, jobs=None)
    validation = _validate_queue_jobs(
        url=request.url,
        jobs=canonical_jobs,
        include_duplicate_guard=True,
    )
    if not validation["valid"]:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Test campaign validation failed",
                "errors": validation["errors"],
                "target_comment_matches": validation["target_comment_matches"],
                "duplicate_conflicts": validation["duplicate_conflicts"],
            },
        )
    if validation.get("duplicate_conflicts"):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "duplicate_text_guard blocked test campaign",
                "errors": [validation.get("duplicate_warning")],
                "duplicate_conflicts": validation["duplicate_conflicts"],
                "dedupe_window_days": validation["dedupe_window_days"],
                "near_duplicate_threshold": validation["near_duplicate_threshold"],
            },
        )

    comments_payload = [str(job.get("text", "")).strip() for job in canonical_jobs if str(job.get("text", "")).strip()]

    # If specific profile requested, validate it's eligible
    if request.profile_name:
        eligible = profile_manager.get_eligible_profiles(
            filter_tags=request.filter_tags,
            count=100  # Get all to check membership
        )
        if request.profile_name not in eligible:
            if request.filter_tags:
                raise HTTPException(
                    status_code=400,
                    detail=f"Profile '{request.profile_name}' is not eligible (must match tags {request.filter_tags} and not be restricted)"
                )
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Profile '{request.profile_name}' is not eligible (may be restricted or have invalid cookies)"
                )
        assigned_profiles = [request.profile_name] * len(comments_payload)
    else:
        # Use UNIFIED selection (handles cookies, tags, restrictions, LRU by success)
        assigned_profiles = profile_manager.get_eligible_profiles(
            filter_tags=request.filter_tags,
            count=len(comments_payload)
        )

    if not assigned_profiles:
        if request.filter_tags:
            raise HTTPException(status_code=400, detail=f"No eligible profiles match tags: {request.filter_tags}")
        else:
            raise HTTPException(status_code=400, detail="No eligible profiles available")

    if len(assigned_profiles) < len(comments_payload):
        logger.warning(f"[TEST] Only {len(assigned_profiles)} profiles for {len(comments_payload)} comments")

    total_jobs = min(len(comments_payload), len(assigned_profiles))
    test_id = f"test_{datetime.now().strftime('%H%M%S')}"

    logger.info(f"[TEST-CAMPAIGN] Starting {test_id}: {total_jobs} comments, warmup={request.enable_warmup}")

    await broadcast_update("test_campaign_start", {
        "test_id": test_id,
        "url": request.url,
        "total_jobs": total_jobs,
        "enable_warmup": request.enable_warmup
    })

    results = []

    for job_idx, (profile_name, comment) in enumerate(zip(assigned_profiles, comments_payload[:total_jobs])):
        logger.info(f"[TEST-CAMPAIGN] Job {job_idx + 1}/{total_jobs}: {profile_name}")

        await broadcast_update("test_job_start", {
            "test_id": test_id,
            "job_index": job_idx,
            "profile_name": profile_name,
            "comment": comment[:50]
        })

        session = FacebookSession(profile_name)

        if not session.load():
            results.append({
                "profile_name": profile_name,
                "success": False,
                "error": "Session not found",
                "job_index": job_idx
            })
            continue

        try:
            # Set Gemini context for debugging
            set_observation_context(profile_name=profile_name, campaign_id=test_id)

            # Broadcast warmup start if enabled
            if request.enable_warmup:
                await broadcast_update("test_warmup_start", {
                    "test_id": test_id,
                    "job_index": job_idx,
                    "profile_name": profile_name
                })

            # Run full pipeline with warmup
            result = await post_comment_verified(
                session=session,
                url=request.url,
                comment=comment,
                proxy=get_system_proxy(),
                enable_warmup=request.enable_warmup,
                forensic_context={
                    "platform": "facebook",
                    "engine": "campaign_test_comment",
                    "run_id": test_id,
                    "campaign_id": test_id,
                    "job_id": str(job_idx),
                },
            )

            await broadcast_update("test_job_complete", {
                "test_id": test_id,
                "job_index": job_idx,
                "profile_name": profile_name,
                "success": result["success"],
                "verified": result.get("verified", False),
                "warmup": result.get("warmup"),
                "error": result.get("error")
            })

            results.append({
                "profile_name": profile_name,
                "comment": comment,
                "success": result["success"],
                "verified": result.get("verified", False),
                "warmup": result.get("warmup"),
                "error": result.get("error"),
                "job_index": job_idx
            })

            # Determine failure type for analytics granularity
            failure_type = None
            if not result["success"]:
                error = result.get("error", "")
                if result.get("throttled") or "restricted" in str(error).lower() or "ban" in str(error).lower():
                    failure_type = "restriction"
                elif any(x in str(error).lower() for x in ["timeout", "proxy", "connection", "network"]):
                    failure_type = "infrastructure"
                else:
                    failure_type = "facebook_error"

            # Track in analytics (LRU only updates on success)
            profile_manager.mark_profile_used(
                profile_name=profile_name,
                campaign_id=test_id,
                comment=comment,
                success=result["success"],
                failure_type=failure_type
            )

            # Check for throttling
            if result.get("throttled"):
                logger.warning(f"[TEST] Profile {profile_name} throttled")
                queue_processor._apply_restriction_signal(
                    profile_manager,
                    profile_name=profile_name,
                    reason=result.get("throttle_reason", "Test detected throttle"),
                    attempt_id=result.get("attempt_id"),
                )

        except Exception as e:
            logger.error(f"[TEST-CAMPAIGN] Job {job_idx} error: {e}")

            # Track exception in analytics
            error_str = str(e).lower()
            if any(x in error_str for x in ["timeout", "proxy", "connection", "network"]):
                exc_failure_type = "infrastructure"
            else:
                exc_failure_type = "facebook_error"

            profile_manager.mark_profile_used(
                profile_name=profile_name,
                campaign_id=test_id,
                comment=comment,
                success=False,
                failure_type=exc_failure_type
            )

            results.append({
                "profile_name": profile_name,
                "success": False,
                "error": str(e),
                "job_index": job_idx
            })

        # Small delay between jobs in test (5-10 seconds)
        if job_idx < total_jobs - 1:
            delay = random.uniform(5, 10)
            logger.info(f"[TEST-CAMPAIGN] Waiting {delay:.1f}s before next job")
            await asyncio.sleep(delay)

    success_count = sum(1 for r in results if r.get("success"))

    await broadcast_update("test_campaign_complete", {
        "test_id": test_id,
        "success": success_count,
        "total": len(results)
    })

    logger.info(f"[TEST-CAMPAIGN] Complete: {success_count}/{len(results)} successful")

    return {
        "test_id": test_id,
        "url": request.url,
        "total": len(results),
        "success": success_count,
        "results": results
    }


@app.get("/queue")
async def get_queue(current_user: dict = Depends(get_current_user)) -> Dict:
    """Get full queue state including pending campaigns and history."""
    return queue_manager.get_full_state()


@app.post("/queue")
async def add_to_queue(
    request: AddToQueueRequest,
    current_user: dict = Depends(get_current_user),
    x_idempotency_key: Optional[str] = Header(None, alias="X-Idempotency-Key"),
) -> Dict:
    """Add a new campaign to the persistent queue with jobs[] + comments[] compatibility."""
    try:
        # Idempotent replay
        if x_idempotency_key:
            existing_campaign_id = queue_idempotency_index.get(x_idempotency_key)
            if existing_campaign_id:
                existing = queue_manager.get_campaign(existing_campaign_id)
                if existing:
                    replayed = dict(existing)
                    replayed["idempotent_replay"] = True
                    return replayed

        jobs_payload = [_model_to_dict(j) for j in request.jobs] if request.jobs else None
        canonical_jobs = _build_queue_jobs(comments=request.comments, jobs=jobs_payload)
        validation = _validate_queue_jobs(
            url=request.url,
            jobs=canonical_jobs,
            include_duplicate_guard=True,
        )
        if not validation["valid"]:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "Queue validation failed",
                    "errors": validation["errors"],
                    "target_comment_matches": validation["target_comment_matches"],
                    "duplicate_conflicts": validation["duplicate_conflicts"],
                },
            )

        legacy_comments = [str(job.get("text", "")) for job in canonical_jobs]
        campaign = queue_manager.add_campaign(
            url=request.url,
            comments=legacy_comments,
            jobs=canonical_jobs,
            duration_minutes=request.duration_minutes,
            username=current_user["username"],
            filter_tags=request.filter_tags,
            enable_warmup=request.enable_warmup,
            profile_name=request.profile_name,
            idempotency_key=x_idempotency_key,
        )
        if x_idempotency_key:
            queue_idempotency_index[x_idempotency_key] = campaign["id"]

        # Broadcast to all connected clients
        await broadcast_update("queue_campaign_added", campaign)
        response_payload = dict(campaign)
        if validation.get("duplicate_conflicts"):
            response_payload["warnings"] = [
                {
                    "code": "duplicate_text_guard",
                    "message": "duplicate-like comments detected",
                    "errors": [validation.get("duplicate_warning")],
                    "duplicate_conflicts": validation["duplicate_conflicts"],
                }
            ]
        return response_payload

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/drafts")
async def get_drafts(current_user: dict = Depends(get_current_user)) -> Dict:
    """List shared campaign drafts."""
    return {"drafts": draft_manager.list_drafts()}


@app.post("/drafts")
async def create_draft(request: DraftRequest, current_user: dict = Depends(get_current_user)) -> Dict:
    """Create a shared campaign draft."""
    jobs_payload = [_model_to_dict(j) for j in request.jobs] if request.jobs else []
    comments_payload = [str(c).strip() for c in (request.comments or []) if str(c).strip()]
    try:
        draft = draft_manager.create_draft(
            url=request.url,
            comments=comments_payload,
            jobs=jobs_payload,
            duration_minutes=request.duration_minutes,
            filter_tags=request.filter_tags,
            enable_warmup=request.enable_warmup,
            username=current_user["username"],
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Draft validation failed",
                "errors": [str(exc)],
            },
        ) from exc
    await broadcast_update("draft_created", draft)
    return draft


@app.put("/drafts/{draft_id}")
async def update_draft(draft_id: str, request: DraftRequest, current_user: dict = Depends(get_current_user)) -> Dict:
    """Update a shared campaign draft."""
    jobs_payload = [_model_to_dict(j) for j in request.jobs] if request.jobs else []
    comments_payload = [str(c).strip() for c in (request.comments or []) if str(c).strip()]
    try:
        updated = draft_manager.update_draft(
            draft_id,
            url=request.url,
            comments=comments_payload,
            jobs=jobs_payload,
            duration_minutes=request.duration_minutes,
            filter_tags=request.filter_tags,
            enable_warmup=request.enable_warmup,
            username=current_user["username"],
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Draft validation failed",
                "errors": [str(exc)],
            },
        ) from exc
    if not updated:
        raise HTTPException(status_code=404, detail="Draft not found")
    await broadcast_update("draft_updated", updated)
    return updated


@app.delete("/drafts/{draft_id}")
async def delete_draft(draft_id: str, current_user: dict = Depends(get_current_user)) -> Dict:
    """Delete a shared campaign draft."""
    deleted = draft_manager.delete_draft(draft_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Draft not found")
    await broadcast_update("draft_deleted", {"draft_id": draft_id})
    return {"success": True, "draft_id": draft_id}


@app.post("/drafts/{draft_id}/publish")
async def publish_draft(draft_id: str, current_user: dict = Depends(get_current_user)) -> Dict:
    """Publish draft into live queue and remove draft."""
    draft = draft_manager.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    try:
        canonical_jobs = _build_queue_jobs(
            comments=draft.get("comments") or [],
            jobs=draft.get("jobs"),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Queue validation failed",
                "errors": [str(exc)],
                "target_comment_matches": [],
                "duplicate_conflicts": [],
            },
        ) from exc
    validation = _validate_queue_jobs(
        url=draft.get("url", ""),
        jobs=canonical_jobs,
        include_duplicate_guard=True,
    )
    if not validation["valid"]:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Queue validation failed",
                "errors": validation["errors"],
                "target_comment_matches": validation["target_comment_matches"],
                "duplicate_conflicts": validation["duplicate_conflicts"],
            },
        )

    legacy_comments = [str(job.get("text", "")) for job in canonical_jobs]
    campaign = queue_manager.add_campaign(
        url=draft["url"],
        comments=legacy_comments,
        jobs=canonical_jobs,
        duration_minutes=int(draft.get("duration_minutes") or 30),
        username=current_user["username"],
        filter_tags=draft.get("filter_tags"),
        enable_warmup=bool(draft.get("enable_warmup", True)),
        profile_name=None,
        idempotency_key=None,
    )
    await broadcast_update("queue_campaign_added", campaign)

    draft_manager.delete_draft(draft_id)
    response_payload = {
        "campaign": campaign,
        "draft_id": draft_id,
        "success": True,
    }
    if validation.get("duplicate_conflicts"):
        response_payload["warnings"] = [
            {
                "code": "duplicate_text_guard",
                "message": "duplicate-like comments detected",
                "errors": [validation.get("duplicate_warning")],
                "duplicate_conflicts": validation["duplicate_conflicts"],
            }
        ]
    await broadcast_update(
        "draft_published",
        {
            "draft_id": draft_id,
            "campaign": campaign,
            "warnings": response_payload.get("warnings", []),
        },
    )
    return response_payload


AI_CAMPAIGN_DEFAULT_DURATION_MINUTES = 30


def _next_ai_metadata(
    *,
    product_id: str,
    product_name: str,
    product_prompt_snapshot: str,
    context_snapshot: Dict,
    rules_snapshot: Dict,
    previous: Optional[Dict] = None,
    increment_regeneration: bool = False,
) -> Dict:
    methodology_version = "campaign_ai_url_only_product_v1"
    previous_meta = dict(previous or {})
    regenerate_count = int(previous_meta.get("regenerate_count", 0) or 0)
    if increment_regeneration:
        regenerate_count += 1

    payload = {
        "product_id": str(product_id or "").strip(),
        "product_name": str(product_name or "").strip(),
        "product_prompt_snapshot": str(product_prompt_snapshot or "").strip(),
        "methodology_version": methodology_version,
        "model": os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
        "context_snapshot": context_snapshot,
        "generated_at": datetime.utcnow().isoformat(),
        "regenerate_count": regenerate_count,
        "rules_snapshot_version": str(rules_snapshot.get("version") or ""),
    }
    # Backward compatibility: preserve legacy intent when it already exists.
    legacy_intent = str(previous_meta.get("intent") or "").strip()
    if legacy_intent:
        payload["intent"] = legacy_intent
    return payload


def _resolve_ai_product_snapshot(ai_metadata: Dict) -> Dict[str, str]:
    """Resolve product snapshot for generation with legacy fallback support."""
    product_id = str(ai_metadata.get("product_id") or "").strip()
    product_name = str(ai_metadata.get("product_name") or "").strip()
    product_prompt_snapshot = str(ai_metadata.get("product_prompt_snapshot") or "").strip()

    if product_id and (not product_name or not product_prompt_snapshot):
        stored = campaign_ai_product_store.get_product(product_id, include_inactive=True)
        if stored:
            if not product_name:
                product_name = str(stored.get("name") or "").strip()
            if not product_prompt_snapshot:
                product_prompt_snapshot = str(stored.get("prompt") or "").strip()

    legacy_intent = str(ai_metadata.get("intent") or "").strip()
    if not product_prompt_snapshot and legacy_intent:
        product_prompt_snapshot = legacy_intent

    if not product_name:
        prompt_lower = product_prompt_snapshot.lower()
        if "nuora" in prompt_lower or "mynuora" in prompt_lower:
            product_name = "Nuora"
        else:
            product_name = "selected product"

    return {
        "product_id": product_id,
        "product_name": product_name,
        "product_prompt_snapshot": product_prompt_snapshot,
    }


@app.get("/campaign-ai/products")
async def campaign_ai_list_products(current_user: dict = Depends(get_current_user)) -> Dict:
    """List active product presets and current user's last selection."""
    products = campaign_ai_product_store.list_products(include_inactive=False)
    last_product_id = campaign_ai_product_store.get_last_product_id(current_user["username"])
    if last_product_id and not any(str(item.get("id")) == last_product_id for item in products):
        last_product_id = None
    return {
        "products": products,
        "last_product_id": last_product_id,
    }


@app.post("/campaign-ai/products")
async def campaign_ai_create_product(
    request: CampaignAIProductRequest,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    """Create campaign AI product preset."""
    try:
        product = campaign_ai_product_store.create_product(
            name=request.name,
            prompt=request.prompt,
            username=current_user["username"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"product": product}


@app.put("/campaign-ai/products/{product_id}")
async def campaign_ai_update_product(
    product_id: str,
    request: CampaignAIProductUpdateRequest,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    """Update campaign AI product preset."""
    if request.name is None and request.prompt is None and request.active is None:
        raise HTTPException(status_code=400, detail="Provide at least one field to update")
    try:
        product = campaign_ai_product_store.update_product(
            product_id,
            name=request.name,
            prompt=request.prompt,
            active=request.active,
            username=current_user["username"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"product": product}


@app.delete("/campaign-ai/products/{product_id}")
async def campaign_ai_delete_product(
    product_id: str,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    """Soft-delete campaign AI product preset by marking it inactive."""
    ok = campaign_ai_product_store.deactivate_product(product_id, username=current_user["username"])
    if not ok:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"success": True, "product_id": product_id}


@app.post("/campaign-ai/context")
async def campaign_ai_context(
    request: CampaignAIContextRequest,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    """Fetch OP post + first two comments for AI campaign planning."""
    try:
        return await fetch_campaign_context(request.url)
    except CampaignAIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except Exception as exc:
        logger.error(f"Campaign AI context fetch failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Campaign AI context fetch failed: {exc}") from exc


@app.post("/campaign-ai/generate")
async def campaign_ai_generate(
    request: CampaignAIGenerateRequest,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    """Generate campaign comments from URL context + selected product preset and persist as draft."""
    try:
        comment_count = ensure_comment_count(request.comment_count)
        product_id = str(request.product_id or "").strip()
        if not product_id:
            raise HTTPException(status_code=400, detail="product_id is required")

        product = campaign_ai_product_store.get_product(product_id, include_inactive=False)
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")

        context_snapshot = await fetch_campaign_context(request.url)
        rules_snapshot = load_campaign_rules_snapshot()
        campaign_ai_product_store.set_last_product_id(current_user["username"], product_id)
        generated_comments = await generate_campaign_comments(
            context_snapshot=context_snapshot,
            product_name=str(product.get("name") or ""),
            product_prompt=str(product.get("prompt") or ""),
            comment_count=comment_count,
            rules_snapshot=rules_snapshot,
            existing_comments=None,
        )

        draft_id = str(request.draft_id or "").strip() or None
        if draft_id:
            existing_draft = draft_manager.get_draft(draft_id)
            if not existing_draft:
                raise HTTPException(status_code=404, detail="Draft not found")
            ai_metadata = _next_ai_metadata(
                product_id=product_id,
                product_name=str(product.get("name") or ""),
                product_prompt_snapshot=str(product.get("prompt") or ""),
                context_snapshot=context_snapshot,
                rules_snapshot=rules_snapshot,
                previous=existing_draft.get("ai_metadata") or {},
                increment_regeneration=False,
            )
            draft = draft_manager.update_draft(
                draft_id,
                url=request.url,
                comments=generated_comments,
                jobs=None,
                duration_minutes=AI_CAMPAIGN_DEFAULT_DURATION_MINUTES,
                filter_tags=request.filter_tags,
                enable_warmup=bool(request.enable_warmup),
                username=current_user["username"],
                ai_metadata=ai_metadata,
            )
            if not draft:
                raise HTTPException(status_code=404, detail="Draft not found")
            await broadcast_update("draft_updated", draft)
        else:
            ai_metadata = _next_ai_metadata(
                product_id=product_id,
                product_name=str(product.get("name") or ""),
                product_prompt_snapshot=str(product.get("prompt") or ""),
                context_snapshot=context_snapshot,
                rules_snapshot=rules_snapshot,
                previous={},
                increment_regeneration=False,
            )
            draft = draft_manager.create_draft(
                url=request.url,
                comments=generated_comments,
                jobs=None,
                duration_minutes=AI_CAMPAIGN_DEFAULT_DURATION_MINUTES,
                filter_tags=request.filter_tags,
                enable_warmup=bool(request.enable_warmup),
                username=current_user["username"],
                ai_metadata=ai_metadata,
            )
            await broadcast_update("draft_created", draft)

        return {
            "draft_id": draft["id"],
            "comments": draft.get("comments", []),
            "context_snapshot": context_snapshot,
            "rules_summary": summarize_rules(rules_snapshot),
            "model": os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
            "product": {
                "id": product_id,
                "name": str(product.get("name") or ""),
            },
            "draft": draft,
        }
    except CampaignAIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@app.post("/campaign-ai/drafts/{draft_id}/regenerate-one")
async def campaign_ai_regenerate_one(
    draft_id: str,
    request: CampaignAIRegenerateOneRequest,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    """Regenerate one comment by index while preserving draft persistence."""
    draft = draft_manager.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    comments = [str(c).strip() for c in (draft.get("comments") or []) if str(c).strip()]
    if not comments:
        raise HTTPException(status_code=400, detail="Draft has no comments to regenerate")
    if request.index < 0 or request.index >= len(comments):
        raise HTTPException(status_code=400, detail=f"index out of range: {request.index}")

    ai_metadata = dict(draft.get("ai_metadata") or {})
    context_snapshot = ai_metadata.get("context_snapshot")
    if not isinstance(context_snapshot, dict):
        raise HTTPException(status_code=400, detail="Draft is missing ai_metadata.context_snapshot")
    product_snapshot = _resolve_ai_product_snapshot(ai_metadata)

    try:
        rules_snapshot = load_campaign_rules_snapshot()
        existing = comments[:request.index] + comments[request.index + 1 :]
        replacement = await generate_campaign_comments(
            context_snapshot=context_snapshot,
            product_name=product_snapshot.get("product_name") or "",
            product_prompt=product_snapshot.get("product_prompt_snapshot") or "",
            comment_count=1,
            rules_snapshot=rules_snapshot,
            existing_comments=existing,
        )
        comments[request.index] = replacement[0]
        next_meta = _next_ai_metadata(
            product_id=product_snapshot.get("product_id") or "",
            product_name=product_snapshot.get("product_name") or "",
            product_prompt_snapshot=product_snapshot.get("product_prompt_snapshot") or "",
            context_snapshot=context_snapshot,
            rules_snapshot=rules_snapshot,
            previous=ai_metadata,
            increment_regeneration=True,
        )
        updated = draft_manager.update_draft(
            draft_id,
            url=draft.get("url", ""),
            comments=comments,
            jobs=None,
            duration_minutes=int(draft.get("duration_minutes") or 30),
            filter_tags=draft.get("filter_tags"),
            enable_warmup=bool(draft.get("enable_warmup", True)),
            username=current_user["username"],
            ai_metadata=next_meta,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Draft not found")
        await broadcast_update("draft_updated", updated)
        return {
            "draft_id": updated["id"],
            "comments": updated.get("comments", []),
            "context_snapshot": context_snapshot,
            "rules_summary": summarize_rules(rules_snapshot),
            "model": next_meta.get("model"),
            "product": {
                "id": product_snapshot.get("product_id"),
                "name": product_snapshot.get("product_name"),
            },
            "draft": updated,
        }
    except CampaignAIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@app.post("/campaign-ai/drafts/{draft_id}/regenerate-all")
async def campaign_ai_regenerate_all(
    draft_id: str,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    """Regenerate all comments for an AI draft while preserving context + product snapshot."""
    draft = draft_manager.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    existing_comments = [str(c).strip() for c in (draft.get("comments") or []) if str(c).strip()]
    if not existing_comments:
        raise HTTPException(status_code=400, detail="Draft has no comments to regenerate")

    ai_metadata = dict(draft.get("ai_metadata") or {})
    context_snapshot = ai_metadata.get("context_snapshot")
    if not isinstance(context_snapshot, dict):
        raise HTTPException(status_code=400, detail="Draft is missing ai_metadata.context_snapshot")
    product_snapshot = _resolve_ai_product_snapshot(ai_metadata)

    try:
        rules_snapshot = load_campaign_rules_snapshot()
        regenerated = await generate_campaign_comments(
            context_snapshot=context_snapshot,
            product_name=product_snapshot.get("product_name") or "",
            product_prompt=product_snapshot.get("product_prompt_snapshot") or "",
            comment_count=len(existing_comments),
            rules_snapshot=rules_snapshot,
            existing_comments=None,
        )
        next_meta = _next_ai_metadata(
            product_id=product_snapshot.get("product_id") or "",
            product_name=product_snapshot.get("product_name") or "",
            product_prompt_snapshot=product_snapshot.get("product_prompt_snapshot") or "",
            context_snapshot=context_snapshot,
            rules_snapshot=rules_snapshot,
            previous=ai_metadata,
            increment_regeneration=True,
        )
        updated = draft_manager.update_draft(
            draft_id,
            url=draft.get("url", ""),
            comments=regenerated,
            jobs=None,
            duration_minutes=int(draft.get("duration_minutes") or 30),
            filter_tags=draft.get("filter_tags"),
            enable_warmup=bool(draft.get("enable_warmup", True)),
            username=current_user["username"],
            ai_metadata=next_meta,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Draft not found")
        await broadcast_update("draft_updated", updated)
        return {
            "draft_id": updated["id"],
            "comments": updated.get("comments", []),
            "context_snapshot": context_snapshot,
            "rules_summary": summarize_rules(rules_snapshot),
            "model": next_meta.get("model"),
            "product": {
                "id": product_snapshot.get("product_id"),
                "name": product_snapshot.get("product_name"),
            },
            "draft": updated,
        }
    except CampaignAIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@app.post("/debug/queue/validate")
async def debug_validate_queue_payload(
    request: DebugQueueValidateRequest,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    """
    Debug-only payload validator for queue jobs.
    Disabled in production unless ENABLE_DEBUG_ENDPOINTS=1.
    """
    if not _is_debug_mode_enabled():
        raise HTTPException(status_code=404, detail="Debug endpoint disabled")

    try:
        jobs_payload = [_model_to_dict(j) for j in request.jobs] if request.jobs else None
        jobs = _build_queue_jobs(comments=request.comments, jobs=jobs_payload)
    except ValueError as exc:
        return {
            "valid": False,
            "errors": [str(exc)],
            "jobs": [],
            "target_comment_matches": [],
            "target_comment_id": None,
            "duplicate_conflicts": [],
            "dedupe_window_days": LOOKBACK_DAYS_DEFAULT,
            "near_duplicate_threshold": NEAR_DUPLICATE_THRESHOLD,
        }

    return _validate_queue_jobs(url=request.url, jobs=jobs, include_duplicate_guard=True)


@app.post("/media/upload", response_model=MediaUploadResponse)
async def upload_media(
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
) -> MediaUploadResponse:
    """Upload media for queue jobs (reply_comment with image attachment)."""
    _cleanup_expired_media()

    suffix = Path(file.filename or "").suffix.lower()
    ext_to_mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }
    resolved_content_type = file.content_type

    # Accept either explicit MIME type or trusted image extension.
    if resolved_content_type not in MEDIA_ALLOWED_TYPES:
        if suffix in ext_to_mime:
            resolved_content_type = ext_to_mime[suffix]
        else:
            return MediaUploadResponse(
                success=False,
                error=f"Invalid file type. Allowed: {', '.join(sorted(MEDIA_ALLOWED_TYPES))}",
            )

    content = await file.read()
    if len(content) > MEDIA_MAX_SIZE:
        return MediaUploadResponse(
            success=False,
            error=f"File too large. Max size: {MEDIA_MAX_SIZE // (1024 * 1024)}MB",
        )

    if suffix not in (".jpg", ".jpeg", ".png", ".webp"):
        if resolved_content_type == "image/png":
            suffix = ".png"
        elif resolved_content_type == "image/webp":
            suffix = ".webp"
        else:
            suffix = ".jpg"

    image_id = uuid.uuid4().hex[:12]
    path = MEDIA_DIR / f"{image_id}{suffix}"
    path.write_bytes(content)

    expires_at = datetime.utcnow() + timedelta(hours=MEDIA_TTL_HOURS)
    media_index[image_id] = {
        "image_id": image_id,
        "path": str(path),
        "filename": file.filename or path.name,
        "size": len(content),
        "content_type": resolved_content_type,
        "uploaded_at": datetime.utcnow().isoformat(),
        "expires_at": expires_at.isoformat(),
        "uploaded_by": current_user.get("username"),
    }

    return MediaUploadResponse(
        success=True,
        image_id=image_id,
        filename=file.filename or path.name,
        size=len(content),
        content_type=resolved_content_type,
        expires_at=expires_at.isoformat(),
    )


@app.get("/media/{image_id}")
async def get_media(
    image_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Download uploaded media by image_id."""
    item = _get_media_or_none(image_id)
    if not item:
        raise HTTPException(status_code=404, detail="Media not found or expired")

    return FileResponse(
        path=item["path"],
        media_type=item.get("content_type") or "application/octet-stream",
        filename=item.get("filename") or Path(item["path"]).name,
    )


@app.delete("/media/{image_id}")
async def delete_media(
    image_id: str,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    """Delete uploaded media by image_id."""
    item = media_index.pop(image_id, None)
    if not item:
        return {"success": False, "error": "Media not found"}

    try:
        Path(item["path"]).unlink(missing_ok=True)
    except Exception as exc:
        logger.warning(f"Failed deleting media {image_id}: {exc}")

    return {"success": True, "image_id": image_id}


@app.delete("/queue/{campaign_id}")
async def remove_from_queue(campaign_id: str, current_user: dict = Depends(get_current_user)) -> Dict:
    """Remove a pending campaign from queue. Cannot remove if processing."""
    campaign = queue_manager.get_campaign(campaign_id)

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    if campaign.get("status") == "processing":
        raise HTTPException(status_code=400, detail="Cannot remove campaign while processing. Use cancel instead.")

    if campaign.get("status") in ("completed", "failed", "cancelled"):
        raise HTTPException(status_code=400, detail=f"Campaign already {campaign['status']}")

    if queue_manager.delete_campaign(campaign_id):
        await broadcast_update("queue_campaign_removed", {"campaign_id": campaign_id})
        return {"success": True, "campaign_id": campaign_id}

    raise HTTPException(status_code=500, detail="Failed to remove campaign")


@app.post("/queue/{campaign_id}/cancel")
async def cancel_campaign(campaign_id: str, current_user: dict = Depends(get_current_user)) -> Dict:
    """Cancel a pending or processing campaign."""
    campaign = queue_manager.get_campaign(campaign_id)

    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    if campaign.get("status") in ("completed", "failed", "cancelled"):
        raise HTTPException(status_code=400, detail=f"Campaign already {campaign['status']}")

    # If processing, signal the processor to stop
    if campaign.get("status") == "processing":
        queue_processor.cancel_current_campaign()

    queue_manager.set_cancelled(campaign_id)
    await broadcast_update("queue_campaign_cancelled", {"campaign_id": campaign_id})

    return {"success": True, "campaign_id": campaign_id}


@app.get("/queue/history")
async def get_queue_history(
    limit: int = 20,
    current_user: dict = Depends(get_current_user)
) -> List[Dict]:
    """Get completed campaign history."""
    return queue_manager.get_history(limit=min(limit, 100))


async def build_queue_reliability_audit_response(
    *,
    lookback_days: int = 2,
    min_total_count: int = 6,
) -> Dict:
    """Build an operator-focused reliability audit for recent completed queue campaigns."""
    from campaign_reliability_audit import build_campaign_reliability_audit
    from profile_manager import get_profile_manager

    pm = get_profile_manager()
    analytics_summary = pm.get_analytics_summary()
    appeal_profiles = []
    for name, state in pm.get_all_profiles().items():
        appeal_state = state.get("appeal_status", "none")
        if state.get("status") == "restricted" or appeal_state not in ("none", None):
            appeal_profiles.append({
                "profile_name": name,
                "status": state.get("status"),
                "appeal_status": appeal_state,
                "appeal_attempts": state.get("appeal_attempts", 0),
                "appeal_last_attempt_at": state.get("appeal_last_attempt_at"),
                "appeal_last_result": state.get("appeal_last_result"),
                "appeal_last_error": state.get("appeal_last_error"),
                "restriction_reason": state.get("restriction_reason"),
                "restriction_expires_at": state.get("restriction_expires_at"),
            })

    return build_campaign_reliability_audit(
        history=queue_manager.get_history(limit=100),
        analytics_summary=analytics_summary,
        appeal_status={"profiles": appeal_profiles, "total": len(appeal_profiles)},
        health_deep=await health_deep(),
        lookback_days=lookback_days,
        min_total_count=min_total_count,
    )


@app.get("/queue/reliability-audit")
async def get_queue_reliability_audit(
    lookback_days: int = Query(default=2, ge=1, le=14),
    min_total_count: int = Query(default=6, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
) -> Dict:
    return await build_queue_reliability_audit_response(
        lookback_days=lookback_days,
        min_total_count=min_total_count,
    )


@app.post("/queue/{campaign_id}/retry")
async def retry_campaign_job(
    campaign_id: str,
    request: RetryJobRequest,
    current_user: dict = Depends(get_current_user)
) -> Dict:
    """
    Retry a failed job in a completed campaign.

    RESPECTS original campaign settings:
    - filter_tags: Profile must match ALL original tags
    - warmup: Uses original campaign's warmup setting
    - restrictions: Profile must not be restricted
    - analytics: Tracks with failure_type granularity

    SKIPS queue (immediate execution).
    """
    from profile_manager import get_profile_manager
    profile_manager = get_profile_manager()

    # Verify campaign exists in history
    campaign = queue_manager.get_campaign_from_history(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found in history")

    # Get the campaign URL and settings
    url = campaign.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="Campaign has no URL")

    filter_tags = campaign.get("filter_tags", [])
    enable_warmup = campaign.get("enable_warmup", False)

    # Validate profile is eligible using UNIFIED selection
    eligible_profiles = profile_manager.get_eligible_profiles(
        filter_tags=filter_tags if filter_tags else None,
        count=100  # Get all eligible to check membership
    )

    if request.profile_name not in eligible_profiles:
        # Give helpful error message
        if filter_tags:
            raise HTTPException(
                status_code=400,
                detail=f"Profile '{request.profile_name}' is not eligible (must match tags {filter_tags} and not be restricted)"
            )
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Profile '{request.profile_name}' is not eligible (may be restricted or have invalid cookies)"
            )

    # Load the session
    session = FacebookSession(request.profile_name)
    if not session.load():
        raise HTTPException(status_code=400, detail=f"Session '{request.profile_name}' not found")

    # Broadcast that retry is starting
    await broadcast_update("queue_campaign_retry_start", {
        "campaign_id": campaign_id,
        "job_index": request.job_index,
        "profile_name": request.profile_name,
        "comment": request.comment[:50],
        "warmup_enabled": enable_warmup
    })

    try:
        # Run the comment posting WITH WARMUP from original campaign
        result = await post_comment_verified(
            session=session,
            url=url,
            comment=request.comment,
            proxy=get_system_proxy(),
            enable_warmup=enable_warmup,  # RESPECT original campaign's warmup setting
            forensic_context={
                "platform": "facebook",
                "engine": "manual_retry_comment",
                "run_id": campaign_id,
                "campaign_id": campaign_id,
                "job_id": str(request.job_index),
            },
        )

        # Determine failure type for analytics granularity
        failure_type = None
        if not result.get("success", False):
            error = result.get("error", "")
            if result.get("throttled") or "restricted" in str(error).lower() or "ban" in str(error).lower():
                failure_type = "restriction"
            elif any(x in str(error).lower() for x in ["timeout", "proxy", "connection", "network"]):
                failure_type = "infrastructure"
            else:
                failure_type = "facebook_error"

        # Track in profile analytics (LRU only updates on success)
        profile_manager.mark_profile_used(
            profile_name=request.profile_name,
            campaign_id=campaign_id,
            comment=request.comment,
            success=result.get("success", False),
            failure_type=failure_type
        )

        # Create the retry result record
        retry_result = {
            "profile_name": request.profile_name,
            "comment": request.comment,
            "success": result.get("success", False),
            "verified": result.get("verified", False),
            "method": result.get("method", "unknown"),
            "error": result.get("error"),
            "job_index": request.job_index,
            "is_retry": True,
            "original_profile": request.original_profile,
            "retried_at": datetime.utcnow().isoformat(),
            "warmup": result.get("warmup")
        }

        # Check for throttling/restriction and auto-block
        if result.get("throttled"):
            throttle_reason = result.get("throttle_reason", "Facebook restriction detected")
            queue_processor._apply_restriction_signal(
                profile_manager,
                profile_name=request.profile_name,
                reason=throttle_reason,
                attempt_id=result.get("attempt_id"),
            )

        # Update the campaign in history
        updated_campaign = queue_manager.add_retry_result(campaign_id, retry_result)

        if not updated_campaign:
            raise HTTPException(status_code=500, detail="Failed to update campaign")

        # Broadcast the retry completion
        await broadcast_update("queue_campaign_retry_complete", {
            "campaign_id": campaign_id,
            "result": retry_result,
            "new_success_count": updated_campaign.get("success_count"),
            "new_total_count": updated_campaign.get("total_count"),
            "campaign": updated_campaign
        })

        return {
            "success": True,
            "result": retry_result,
            "campaign": updated_campaign
        }

    except Exception as e:
        logger.error(f"Retry failed for campaign {campaign_id}: {e}")

        # Track exception in analytics
        error_str = str(e).lower()
        if any(x in error_str for x in ["timeout", "proxy", "connection", "network"]):
            exc_failure_type = "infrastructure"
        else:
            exc_failure_type = "facebook_error"

        profile_manager.mark_profile_used(
            profile_name=request.profile_name,
            campaign_id=campaign_id,
            comment=request.comment,
            success=False,
            failure_type=exc_failure_type
        )

        # Create failed retry result
        retry_result = {
            "profile_name": request.profile_name,
            "comment": request.comment,
            "success": False,
            "verified": False,
            "method": "unknown",
            "error": str(e),
            "job_index": request.job_index,
            "is_retry": True,
            "original_profile": request.original_profile,
            "retried_at": datetime.utcnow().isoformat()
        }

        # Still save the failed retry to history
        updated_campaign = queue_manager.add_retry_result(campaign_id, retry_result)

        await broadcast_update("queue_campaign_retry_complete", {
            "campaign_id": campaign_id,
            "result": retry_result,
            "new_success_count": updated_campaign.get("success_count") if updated_campaign else None,
            "new_total_count": updated_campaign.get("total_count") if updated_campaign else None,
            "campaign": updated_campaign
        })

        return {
            "success": False,
            "result": retry_result,
            "campaign": updated_campaign
        }


# NOTE: assign_profiles_to_jobs removed - bulk retry now handles profile selection
# internally with smart retry-until-success logic


@app.post("/queue/{campaign_id}/bulk-retry")
async def bulk_retry_failed_jobs(
    campaign_id: str,
    current_user: dict = Depends(get_current_user)
) -> Dict:
    """
    Retry all failed jobs in a completed campaign.

    SMART RETRY LOGIC:
    - Excludes profiles that already SUCCEEDED in this campaign (prevents duplicates)
    - For each failed job, tries profiles in LRU order until success
    - Saves each attempt IMMEDIATELY (deployment-safe)
    - Respects original campaign's filter_tags and warmup setting

    No parameters needed - just click "Retry Failed" and it works.
    """
    from profile_manager import get_profile_manager
    profile_manager = get_profile_manager()

    # Verify campaign exists in history
    campaign = queue_manager.get_campaign_from_history(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found in history")

    # Disable auto-retry when manual bulk-retry is triggered
    if campaign.get("auto_retry", {}).get("status") in ("scheduled", "in_progress"):
        queue_manager.complete_auto_retry(campaign_id, "completed")
        logger.info(f"Disabled auto-retry for campaign {campaign_id} (manual bulk-retry)")

    # Get the campaign URL and settings
    url = campaign.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="Campaign has no URL")

    filter_tags = campaign.get("filter_tags", [])
    enable_warmup = campaign.get("enable_warmup", False)

    # Get profiles that already SUCCEEDED in this campaign - these must be excluded
    # to prevent the same profile posting twice on the same post
    succeeded_profiles = {
        r.get("profile_name")
        for r in campaign.get("results", [])
        if r.get("success") and r.get("profile_name")
    }
    logger.info(f"Bulk retry: {len(succeeded_profiles)} profiles already succeeded, will be excluded")

    # Identify jobs that have NEVER succeeded (check ALL results, not just latest)
    # This is critical for deployment resilience - if a retry succeeded but then
    # deployment killed the process, we don't want to retry that job again
    job_has_success: Dict[int, bool] = {}
    job_comment: Dict[int, str] = {}
    job_original_profile: Dict[int, str] = {}

    for result in campaign.get("results", []):
        idx = result.get("job_index", 0)
        if idx not in job_has_success:
            job_has_success[idx] = False
            job_comment[idx] = result.get("comment", "")
            job_original_profile[idx] = result.get("profile_name", "")
        if result.get("success"):
            job_has_success[idx] = True

    # Only retry jobs that have NEVER succeeded
    failed_jobs = [
        {"job_index": idx, "comment": job_comment.get(idx, ""), "original_profile": job_original_profile.get(idx, "")}
        for idx, has_success in job_has_success.items()
        if not has_success
    ]

    logger.info(f"Bulk retry: {len(failed_jobs)} jobs still need success (out of {len(job_has_success)} total)")

    if not failed_jobs:
        return {"success": True, "message": "No failed jobs to retry", "retried": 0, "succeeded": 0, "failed": 0}

    # Broadcast that bulk retry is starting
    await broadcast_update("queue_campaign_bulk_retry_start", {
        "campaign_id": campaign_id,
        "total_jobs": len(failed_jobs),
        "warmup_enabled": enable_warmup
    })

    # Track profiles that SUCCEEDED - these must be excluded from ALL jobs to prevent duplicates
    # But failed profiles should only be excluded within the SAME job retry loop
    all_results = []
    jobs_succeeded = 0
    jobs_exhausted = 0  # Jobs where we ran out of profiles

    # Process each failed job
    for job_num, job in enumerate(failed_jobs):
        job_index = job["job_index"]
        comment = job["comment"]
        job_succeeded = False
        attempt = 0

        # For THIS job, track profiles we've tried (reset for each job)
        # But always exclude profiles that already succeeded in the campaign
        job_tried_profiles = set(succeeded_profiles)

        logger.info(f"Bulk retry: Starting job {job_index} (comment: {comment[:30]}...)")

        # Keep trying profiles until success or no more profiles available
        while not job_succeeded:
            # Get eligible profiles, excluding:
            # 1. Profiles that already succeeded in this campaign (prevents duplicates)
            # 2. Profiles we've already tried for THIS specific job
            eligible = profile_manager.get_eligible_profiles(
                filter_tags=filter_tags if filter_tags else None,
                count=5,  # Get a small batch
                exclude_profiles=list(job_tried_profiles)
            )

            if not eligible:
                # No more profiles to try for this job
                logger.warning(f"Bulk retry: Job {job_index} exhausted all profiles after {attempt} attempts")
                result = {
                    "profile_name": None,
                    "comment": comment,
                    "success": False,
                    "verified": False,
                    "method": "exhausted",
                    "error": f"No eligible profiles remaining (tried {attempt} profiles)",
                    "job_index": job_index,
                    "is_retry": True,
                    "original_profile": job.get("original_profile"),
                    "retried_at": datetime.utcnow().isoformat(),
                    "attempts": attempt
                }
                all_results.append(result)
                # Save immediately (deployment-safe)
                queue_manager.add_retry_result(campaign_id, result)
                jobs_exhausted += 1
                break

            # Try the first eligible profile (LRU order)
            profile_name = eligible[0]
            job_tried_profiles.add(profile_name)
            attempt += 1

            logger.info(f"Bulk retry: Job {job_index} attempt {attempt} with {profile_name}")

            try:
                session = FacebookSession(profile_name)
                if not session.load() or not session.has_valid_cookies():
                    # Session invalid, try next profile
                    logger.warning(f"Bulk retry: Session {profile_name} invalid, trying next")
                    continue

                # Execute with warmup from original campaign
                post_result = await post_comment_verified(
                    session=session,
                    url=url,
                    comment=comment,
                    proxy=get_system_proxy(),
                    enable_warmup=enable_warmup,
                    forensic_context={
                        "platform": "facebook",
                        "engine": "bulk_retry_comment",
                        "run_id": campaign_id,
                        "campaign_id": campaign_id,
                        "job_id": str(job_index),
                    },
                )

                # Determine failure type for analytics
                failure_type = None
                if not post_result.get("success", False):
                    error = post_result.get("error", "")
                    if post_result.get("throttled") or "restricted" in str(error).lower():
                        failure_type = "restriction"
                    elif any(x in str(error).lower() for x in ["timeout", "proxy", "connection"]):
                        failure_type = "infrastructure"
                    else:
                        failure_type = "facebook_error"

                # Track in profile analytics
                profile_manager.mark_profile_used(
                    profile_name=profile_name,
                    campaign_id=campaign_id,
                    comment=comment,
                    success=post_result.get("success", False),
                    failure_type=failure_type
                )

                # Check for throttling and auto-block
                if post_result.get("throttled"):
                    queue_processor._apply_restriction_signal(
                        profile_manager,
                        profile_name=profile_name,
                        reason=post_result.get("throttle_reason", "Facebook restriction"),
                        attempt_id=post_result.get("attempt_id"),
                    )

                result = {
                    "profile_name": profile_name,
                    "comment": comment,
                    "success": post_result.get("success", False),
                    "verified": post_result.get("verified", False),
                    "method": post_result.get("method", "unknown"),
                    "error": post_result.get("error"),
                    "job_index": job_index,
                    "is_retry": True,
                    "original_profile": job.get("original_profile"),
                    "retried_at": datetime.utcnow().isoformat(),
                    "warmup": post_result.get("warmup"),
                    "attempt": attempt
                }
                all_results.append(result)

                # DEPLOYMENT RESILIENCE: Save immediately to disk
                queue_manager.add_retry_result(campaign_id, result)

                if post_result.get("success"):
                    job_succeeded = True
                    jobs_succeeded += 1
                    # Add to succeeded_profiles so future jobs don't use this profile
                    succeeded_profiles.add(profile_name)
                    logger.info(f"Bulk retry: Job {job_index} succeeded with {profile_name} on attempt {attempt}")
                else:
                    logger.info(f"Bulk retry: Job {job_index} failed with {profile_name}, trying next profile")

            except Exception as e:
                logger.error(f"Bulk retry: Job {job_index} exception with {profile_name}: {e}")

                # Track exception
                profile_manager.mark_profile_used(
                    profile_name=profile_name,
                    campaign_id=campaign_id,
                    comment=comment,
                    success=False,
                    failure_type="facebook_error"
                )

                result = {
                    "profile_name": profile_name,
                    "comment": comment,
                    "success": False,
                    "verified": False,
                    "method": "error",
                    "error": str(e),
                    "job_index": job_index,
                    "is_retry": True,
                    "original_profile": job.get("original_profile"),
                    "retried_at": datetime.utcnow().isoformat(),
                    "attempt": attempt
                }
                all_results.append(result)

                # Save immediately
                queue_manager.add_retry_result(campaign_id, result)
                # Continue to next profile

        # Broadcast progress after each job completes (success or exhausted)
        await broadcast_update("queue_campaign_bulk_retry_progress", {
            "campaign_id": campaign_id,
            "completed": job_num + 1,
            "total": len(failed_jobs),
            "succeeded": jobs_succeeded,
            "exhausted": jobs_exhausted,
            "last_result": all_results[-1] if all_results else None
        })

    # Get updated campaign state
    updated_campaign = queue_manager.get_campaign_from_history(campaign_id)

    # Broadcast completion
    await broadcast_update("queue_campaign_bulk_retry_complete", {
        "campaign_id": campaign_id,
        "jobs_retried": len(failed_jobs),
        "jobs_succeeded": jobs_succeeded,
        "jobs_exhausted": jobs_exhausted,
        "total_attempts": len(all_results),
        "campaign": updated_campaign
    })

    return {
        "success": True,
        "jobs_retried": len(failed_jobs),
        "jobs_succeeded": jobs_succeeded,
        "jobs_exhausted": jobs_exhausted,
        "total_attempts": len(all_results),
        "results": all_results,
        "campaign": updated_campaign
    }


async def check_proxy_health() -> Dict:
    """Quick proxy health check via ipify.org. Returns {healthy, ip, response_ms, error}."""
    import aiohttp
    proxy_url = get_system_proxy()
    if not proxy_url:
        return {"healthy": False, "ip": None, "response_ms": None, "error": "No proxy configured"}

    start = datetime.utcnow()
    try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(
                "https://api.ipify.org?format=json",
                proxy=proxy_url,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                response_ms = int((datetime.utcnow() - start).total_seconds() * 1000)
                if response.status == 200:
                    data = await response.json()
                    return {"healthy": True, "ip": data.get("ip"), "response_ms": response_ms, "error": None}
                return {"healthy": False, "ip": None, "response_ms": response_ms, "error": f"HTTP {response.status}"}
    except asyncio.TimeoutError:
        return {"healthy": False, "ip": None, "response_ms": 10000, "error": "Timeout (10s)"}
    except Exception as e:
        return {"healthy": False, "ip": None, "response_ms": None, "error": str(e)}


@app.get("/proxy/health")
async def proxy_health_endpoint(current_user: dict = Depends(get_current_user)) -> Dict:
    """Check proxy connectivity."""
    return await check_proxy_health()


async def _retry_single_campaign(
    campaign: dict,
    campaign_index: int,
    total_campaigns: int,
    profile_manager,
    browser_semaphore: asyncio.Semaphore,
) -> Dict:
    """Retry all failed jobs in a single campaign. Used by _run_retry_all for parallel execution."""
    campaign_id = campaign.get("id")
    sc = campaign.get("success_count", 0)
    tc = campaign.get("total_count", 0)
    failed_count = tc - sc

    logger.info(f"Retry-all: campaign {campaign_index+1}/{total_campaigns} ({campaign_id[:8]}): {sc}/{tc} success, {failed_count} to retry")

    await broadcast_update("bulk_retry_all_campaign_start", {
        "campaign_index": campaign_index,
        "campaign_id": campaign_id,
        "total_campaigns": total_campaigns,
        "failed_jobs": failed_count
    })

    # Disable auto-retry if active
    if campaign.get("auto_retry", {}).get("status") in ("scheduled", "in_progress"):
        queue_manager.complete_auto_retry(campaign_id, "completed")

    url = campaign.get("url")
    if not url:
        logger.warning(f"Retry-all: campaign {campaign_id[:8]} has no URL, skipping")
        return {"campaign_id": campaign_id, "jobs_succeeded": 0, "jobs_exhausted": 0, "attempts": 0}

    filter_tags = campaign.get("filter_tags", [])
    enable_warmup = campaign.get("enable_warmup", False)

    # Get succeeded profiles (prevent duplicates on same post)
    succeeded_profiles = {
        r.get("profile_name")
        for r in campaign.get("results", [])
        if r.get("success") and r.get("profile_name")
    }

    # Find jobs that never succeeded
    job_has_success: Dict[int, bool] = {}
    job_comment: Dict[int, str] = {}
    job_original_profile: Dict[int, str] = {}

    for result in campaign.get("results", []):
        idx = result.get("job_index", 0)
        if idx not in job_has_success:
            job_has_success[idx] = False
            job_comment[idx] = result.get("comment", "")
            job_original_profile[idx] = result.get("profile_name", "")
        if result.get("success"):
            job_has_success[idx] = True

    failed_jobs = [
        {"job_index": idx, "comment": job_comment.get(idx, ""), "original_profile": job_original_profile.get(idx, "")}
        for idx, has_success in job_has_success.items()
        if not has_success
    ]

    if not failed_jobs:
        logger.info(f"Retry-all: campaign {campaign_id[:8]} has no failed jobs left")
        return {"campaign_id": campaign_id, "jobs_succeeded": 0, "jobs_exhausted": 0, "attempts": 0}

    campaign_jobs_succeeded = 0
    campaign_jobs_exhausted = 0
    campaign_attempts = 0

    url_is_dead = False  # Set True if post URL itself is broken — skip remaining jobs

    for job in failed_jobs:
        # If a previous job proved the URL is dead, skip remaining jobs
        if url_is_dead:
            result = {
                "profile_name": None, "comment": job["comment"], "success": False,
                "verified": False, "method": "exhausted",
                "error": "Post URL appears dead (all profiles failed on prior job)",
                "job_index": job["job_index"], "is_retry": True,
                "original_profile": job.get("original_profile"),
                "retried_at": datetime.utcnow().isoformat()
            }
            queue_manager.add_retry_result(campaign_id, result)
            campaign_jobs_exhausted += 1
            continue

        job_index = job["job_index"]
        comment = job["comment"]
        job_succeeded = False
        job_tried_profiles = set(succeeded_profiles)
        consecutive_post_not_visible = 0
        job_history = [
            result
            for result in campaign.get("results", [])
            if result.get("job_index") == job_index
        ]
        last_failure = next((result for result in reversed(job_history) if not result.get("success")), None)

        if queue_processor._failure_requires_reconciliation(last_failure):
            reconciliation_profile = (
                (last_failure or {}).get("profile_name")
                or job.get("original_profile")
                or ""
            )
            if reconciliation_profile:
                reserved_reconciliation = await profile_manager.reserve_profile(reconciliation_profile)
                if reserved_reconciliation:
                    try:
                        session = FacebookSession(reconciliation_profile)
                        if session.load():
                            reconciliation = await reconcile_comment_submission(
                                session=session,
                                url=url,
                                comment_text=comment,
                                proxy=get_system_proxy(),
                            )
                            if reconciliation.get("found") is True:
                                queue_manager.add_retry_result(
                                    campaign_id,
                                    {
                                        "profile_name": reconciliation_profile,
                                        "comment": comment,
                                        "success": True,
                                        "verified": True,
                                        "method": "reconciled_existing_comment",
                                        "error": None,
                                        "job_index": job_index,
                                        "is_retry": True,
                                        "original_profile": job.get("original_profile"),
                                        "retried_at": datetime.utcnow().isoformat(),
                                        "reconciled_without_repost": True,
                                        "reconciliation_confidence": reconciliation.get("confidence", 0.0),
                                        "reconciliation_reason": reconciliation.get("reason"),
                                    },
                                )
                                campaign_jobs_succeeded += 1
                                succeeded_profiles.add(reconciliation_profile)
                                logger.info(
                                    f"Retry-all reconciliation recovered campaign {campaign_id[:8]} "
                                    f"job {job_index} without repost"
                                )
                                continue
                            if reconciliation.get("found") is None:
                                queue_manager.add_retry_result(
                                    campaign_id,
                                    {
                                        "profile_name": reconciliation_profile,
                                        "comment": comment,
                                        "success": False,
                                        "verified": False,
                                        "method": "verification_inconclusive",
                                        "error": reconciliation.get(
                                            "reason",
                                            "Reconciliation inconclusive after prior submit evidence",
                                        ),
                                        "job_index": job_index,
                                        "is_retry": True,
                                        "original_profile": job.get("original_profile"),
                                        "retried_at": datetime.utcnow().isoformat(),
                                        "reconciliation_inconclusive": True,
                                    },
                                )
                                campaign_jobs_exhausted += 1
                                logger.warning(
                                    f"Retry-all reconciliation inconclusive for campaign {campaign_id[:8]} "
                                    f"job {job_index}; stopping without repost"
                                )
                                continue
                    finally:
                        await profile_manager.release_profile(reconciliation_profile)

        while not job_succeeded:
            eligible = profile_manager.get_eligible_profiles(
                filter_tags=filter_tags if filter_tags else None,
                count=5,
                exclude_profiles=list(job_tried_profiles)
            )

            if not eligible:
                result = {
                    "profile_name": None, "comment": comment, "success": False,
                    "verified": False, "method": "exhausted",
                    "error": "No eligible profiles remaining",
                    "job_index": job_index, "is_retry": True,
                    "original_profile": job.get("original_profile"),
                    "retried_at": datetime.utcnow().isoformat()
                }
                queue_manager.add_retry_result(campaign_id, result)
                campaign_jobs_exhausted += 1
                break

            profile_name = eligible[0]
            job_tried_profiles.add(profile_name)
            campaign_attempts += 1

            try:
                session = FacebookSession(profile_name)
                if not session.load() or not session.has_valid_cookies():
                    continue

                # Reserve profile to prevent parallel browser conflicts
                reserved = await profile_manager.reserve_profile(profile_name)
                if not reserved:
                    logger.debug(f"Retry-all: {profile_name} reserved by another campaign, skipping")
                    continue

                try:
                    async with browser_semaphore:
                        post_result = await post_comment_verified(
                            session=session, url=url, comment=comment,
                            proxy=get_system_proxy(), enable_warmup=enable_warmup,
                            forensic_context={
                                "platform": "facebook",
                                "engine": "retry_all_comment",
                                "run_id": campaign_id,
                                "campaign_id": campaign_id,
                                "job_id": str(job_index),
                            },
                        )
                finally:
                    await profile_manager.release_profile(profile_name)

                failure_type = None
                if not post_result.get("success", False):
                    error = post_result.get("error", "")
                    if any(x in str(error).lower() for x in ["timeout", "proxy", "connection", "network"]):
                        failure_type = "infrastructure"
                    elif post_result.get("throttled") or "restricted" in str(error).lower():
                        failure_type = "restriction"
                    else:
                        failure_type = "facebook_error"

                profile_manager.mark_profile_used(
                    profile_name=profile_name, campaign_id=campaign_id,
                    comment=comment, success=post_result.get("success", False),
                    failure_type=failure_type
                )

                # Layer 3: Don't restrict profiles on infrastructure errors
                if post_result.get("throttled") and failure_type != "infrastructure":
                    queue_processor._apply_restriction_signal(
                        profile_manager,
                        profile_name=profile_name,
                        reason=post_result.get("throttle_reason", "Facebook restriction"),
                        attempt_id=post_result.get("attempt_id"),
                    )
                elif post_result.get("throttled") and failure_type == "infrastructure":
                    logger.info(f"Retry-all: skipping restriction for {profile_name} — infrastructure error, not real restriction")

                result = {
                    "profile_name": profile_name, "comment": comment,
                    "success": post_result.get("success", False),
                    "verified": post_result.get("verified", False),
                    "method": post_result.get("method", "unknown"),
                    "error": post_result.get("error"),
                    "job_index": job_index, "is_retry": True,
                    "original_profile": job.get("original_profile"),
                    "retried_at": datetime.utcnow().isoformat(),
                    "warmup": post_result.get("warmup")
                }
                queue_manager.add_retry_result(campaign_id, result)

                if post_result.get("success"):
                    job_succeeded = True
                    campaign_jobs_succeeded += 1
                    succeeded_profiles.add(profile_name)
                    consecutive_post_not_visible = 0
                else:
                    error_text = str(post_result.get("error", "")).lower()
                    if "not visible" in error_text:
                        consecutive_post_not_visible += 1
                    else:
                        consecutive_post_not_visible = 0

                    # Only dead-post evidence can short-circuit a campaign. Infrastructure
                    # noise must keep rotating through profiles until the pool is actually exhausted.
                    if consecutive_post_not_visible >= 4:
                        logger.warning(
                            f"Retry-all: {campaign_id[:8]} job {job_index}: "
                            f"{consecutive_post_not_visible} consecutive post_not_visible failures, exhausting job early"
                        )
                        exhaust_result = {
                            "profile_name": None, "comment": comment, "success": False,
                            "verified": False, "method": "exhausted",
                            "error": (
                                f"Early termination: {consecutive_post_not_visible} "
                                "consecutive post_not_visible failures"
                            ),
                            "job_index": job_index, "is_retry": True,
                            "original_profile": job.get("original_profile"),
                            "retried_at": datetime.utcnow().isoformat()
                        }
                        queue_manager.add_retry_result(campaign_id, exhaust_result)
                        campaign_jobs_exhausted += 1
                        url_is_dead = True
                        logger.warning(f"Retry-all: {campaign_id[:8]} post URL appears dead, skipping remaining jobs")
                        break

            except Exception as e:
                logger.error(f"Retry-all: exception with {profile_name}: {e}")
                profile_manager.mark_profile_used(
                    profile_name=profile_name, campaign_id=campaign_id,
                    comment=comment, success=False, failure_type="facebook_error"
                )
                result = {
                    "profile_name": profile_name, "comment": comment,
                    "success": False, "verified": False, "method": "error",
                    "error": str(e), "job_index": job_index, "is_retry": True,
                    "original_profile": job.get("original_profile"),
                    "retried_at": datetime.utcnow().isoformat()
                }
                queue_manager.add_retry_result(campaign_id, result)

    # Check if campaign is now fully successful
    updated = queue_manager.get_campaign_from_history(campaign_id)
    campaign_result = {
        "campaign_id": campaign_id,
        "jobs_succeeded": campaign_jobs_succeeded,
        "jobs_exhausted": campaign_jobs_exhausted,
        "attempts": campaign_attempts
    }

    await broadcast_update("bulk_retry_all_campaign_complete", {
        "campaign_index": campaign_index,
        "campaign_id": campaign_id,
        "jobs_succeeded": campaign_jobs_succeeded,
        "jobs_exhausted": campaign_jobs_exhausted,
        "campaign": updated
    })

    logger.info(f"Retry-all: campaign {campaign_id[:8]} done: {campaign_jobs_succeeded} succeeded, {campaign_jobs_exhausted} exhausted")
    return campaign_result


MAX_PARALLEL_CAMPAIGNS = 3


async def _run_retry_all(failed_campaigns: list, profile_manager, proxy_ip: str):
    """Background coroutine: retry all failed campaigns with worker pool (not batched)."""
    global _retry_all_progress

    _retry_all_progress = {
        "active": True,
        "campaigns_total": len(failed_campaigns),
        "campaigns_completed": 0,
        "campaigns_succeeded": 0,
        "jobs_succeeded": 0,
        "jobs_exhausted": 0,
        "total_attempts": 0,
        "campaign_results": []
    }

    try:
        await broadcast_update("bulk_retry_all_start", {
            "total_campaigns": len(failed_campaigns),
            "proxy_ip": proxy_ip
        })

        # Worker pool: all campaigns launch immediately, semaphore limits concurrency
        campaign_semaphore = asyncio.Semaphore(MAX_PARALLEL_CAMPAIGNS)

        async def _run_with_limit(campaign, index):
            async with campaign_semaphore:
                return await _retry_single_campaign(
                    campaign=campaign,
                    campaign_index=index,
                    total_campaigns=len(failed_campaigns),
                    profile_manager=profile_manager,
                    browser_semaphore=_browser_semaphore,
                )

        async def _run_and_track(campaign, index):
            try:
                r = await _run_with_limit(campaign, index)
                _retry_all_progress["campaigns_completed"] += 1
                _retry_all_progress["jobs_succeeded"] += r.get("jobs_succeeded", 0)
                _retry_all_progress["jobs_exhausted"] += r.get("jobs_exhausted", 0)
                _retry_all_progress["total_attempts"] += r.get("attempts", 0)
                _retry_all_progress["campaign_results"].append(r)

                updated = queue_manager.get_campaign_from_history(r.get("campaign_id"))
                if updated and updated.get("success_count", 0) >= updated.get("total_count", 0):
                    _retry_all_progress["campaigns_succeeded"] += 1
            except Exception as e:
                logger.error(f"Retry-all: campaign {index} failed with exception: {e}")
                _retry_all_progress["campaigns_completed"] += 1

        logger.info(f"Retry-all: launching {len(failed_campaigns)} campaigns (max {MAX_PARALLEL_CAMPAIGNS} concurrent)")
        await asyncio.gather(
            *[_run_and_track(c, i) for i, c in enumerate(failed_campaigns)],
            return_exceptions=True
        )

        summary = {
            "success": True,
            "campaigns_found": _retry_all_progress["campaigns_total"],
            "campaigns_retried": _retry_all_progress["campaigns_completed"],
            "campaigns_succeeded": _retry_all_progress["campaigns_succeeded"],
            "total_jobs_succeeded": _retry_all_progress["jobs_succeeded"],
            "total_jobs_exhausted": _retry_all_progress["jobs_exhausted"],
            "total_attempts": _retry_all_progress["total_attempts"],
            "campaign_results": _retry_all_progress["campaign_results"]
        }

        await broadcast_update("bulk_retry_all_complete", summary)
        logger.info(
            f"Retry-all complete: {summary['campaigns_retried']} campaigns, "
            f"{summary['campaigns_succeeded']} fully succeeded, "
            f"{summary['total_jobs_succeeded']} jobs recovered, "
            f"{summary['total_jobs_exhausted']} exhausted"
        )
    except Exception as e:
        logger.error(f"Retry-all background task crashed: {e}")
        await broadcast_update("bulk_retry_all_complete", {"error": str(e)})
    finally:
        _retry_all_progress["active"] = False


@app.post("/queue/retry-all-failed")
async def retry_all_failed_campaigns(
    hours_back: int = Query(default=72, description="Only retry campaigns from the last N hours"),
    current_user: dict = Depends(get_current_user)
) -> Dict:
    """
    Launch parallel retry of ALL failed campaigns as a background task.
    Returns immediately. Monitor via GET /queue/retry-all-failed/status or websocket.
    """
    global _retry_all_task

    # Prevent double-trigger
    if _retry_all_task and not _retry_all_task.done():
        return {
            "success": False,
            "message": "Retry-all already running",
            "progress": _retry_all_progress
        }

    from profile_manager import get_profile_manager
    profile_manager = get_profile_manager()

    # Proxy health check first
    health = await check_proxy_health()
    if not health["healthy"]:
        raise HTTPException(
            status_code=503,
            detail=f"Proxy is down: {health['error']}. Fix proxy before retrying."
        )
    logger.info(f"Proxy health OK: ip={health['ip']}, {health['response_ms']}ms")

    # Unblock any auto-burned profiles with stats reset
    all_profiles = profile_manager.state.get("profiles", {})
    unblocked_count = 0
    for pname, pstate in all_profiles.items():
        if pstate.get("status") == "restricted" and "auto-burned" in (pstate.get("restriction_reason") or ""):
            profile_manager.unblock_profile(pname, reset_stats=True)
            unblocked_count += 1
    if unblocked_count:
        logger.info(f"Retry-all: unblocked {unblocked_count} auto-burned profiles with stats reset")

    # Find all campaigns with failures in the time window
    cutoff = datetime.utcnow() - timedelta(hours=hours_back)
    all_history = queue_manager.get_history(limit=100)

    failed_campaigns = []
    for campaign in all_history:
        sc = campaign.get("success_count")
        tc = campaign.get("total_count")
        if sc is None or tc is None:
            continue
        if sc >= tc:
            continue
        completed_at = campaign.get("completed_at")
        if completed_at:
            try:
                if datetime.fromisoformat(completed_at.replace("Z", "+00:00")).replace(tzinfo=None) < cutoff:
                    continue
            except (ValueError, TypeError):
                pass
        failed_campaigns.append(campaign)

    if not failed_campaigns:
        return {"success": True, "message": "No failed campaigns found", "campaigns_found": 0}

    logger.info(f"Retry-all: launching background task for {len(failed_campaigns)} failed campaigns ({MAX_PARALLEL_CAMPAIGNS} parallel)")

    # Launch as background task — returns immediately
    _retry_all_task = asyncio.create_task(
        _run_retry_all(failed_campaigns, profile_manager, health["ip"])
    )

    return {
        "success": True,
        "task_started": True,
        "campaigns_found": len(failed_campaigns),
        "parallel_limit": MAX_PARALLEL_CAMPAIGNS,
        "unblocked_profiles": unblocked_count,
        "message": f"Retrying {len(failed_campaigns)} campaigns in parallel (max {MAX_PARALLEL_CAMPAIGNS} at a time)"
    }


@app.get("/queue/retry-all-failed/status")
async def retry_all_status(current_user: dict = Depends(get_current_user)) -> Dict:
    """Get the current status of the retry-all background task."""
    if not _retry_all_task:
        return {"active": False, "message": "No retry-all task has been started"}
    return {
        "active": not _retry_all_task.done(),
        **_retry_all_progress
    }


# =========================================================================
# Premium Automation API
# =========================================================================

def _has_premium_tag(session_payload: Dict) -> bool:
    tags = [str(t).strip().lower() for t in (session_payload.get("tags") or []) if str(t).strip()]
    return "premium" in tags


def _find_session_by_profile_name(profile_name: str) -> Optional[Dict]:
    target = str(profile_name or "").strip().lower()
    for session in list_saved_sessions():
        candidate = str(session.get("profile_name") or "").strip().lower()
        if candidate == target:
            return session
    return None


def _premium_default_rule_paths() -> Dict[str, str]:
    return {
        "negative_patterns_path": os.getenv(
            "PREMIUM_NEGATIVE_PATTERNS_PATH",
            "/Users/nikitalienov/Documents/writing/.claude/rules/negative-patterns.md",
        ),
        "vocabulary_guidance_path": os.getenv(
            "PREMIUM_VOCAB_GUIDANCE_PATH",
            "/Users/nikitalienov/Documents/writing/.claude/rules/vocabulary-guidance.md",
        ),
    }


def _build_premium_safety_snapshot(run: Dict) -> Dict:
    evidence_items = list(run.get("evidence") or [])
    duplicate_checks = [item.get("duplicate_precheck") for item in evidence_items if isinstance(item.get("duplicate_precheck"), dict)]
    identity_checks = [item.get("identity_check") for item in evidence_items if isinstance(item.get("identity_check"), dict)]
    submit_guards = [item.get("submit_guard") for item in evidence_items if isinstance(item.get("submit_guard"), dict)]

    latest_duplicate = duplicate_checks[-1] if duplicate_checks else None
    latest_identity = identity_checks[-1] if identity_checks else None
    latest_submit_guard = submit_guards[-1] if submit_guards else None

    duplicate_passed = all(bool(item.get("passed", True)) for item in duplicate_checks) if duplicate_checks else None
    identity_passed = all(bool(item.get("passed", False)) for item in identity_checks) if identity_checks else None
    submit_guard_passed = all(bool(item.get("passed", True)) for item in submit_guards) if submit_guards else None

    return {
        "duplicate_precheck": {
            "latest": latest_duplicate,
            "all_passed": duplicate_passed,
            "checks_count": len(duplicate_checks),
        },
        "identity_check": {
            "latest": latest_identity,
            "all_passed": identity_passed,
            "checks_count": len(identity_checks),
        },
        "submit_guard": {
            "latest": latest_submit_guard,
            "all_passed": submit_guard_passed,
            "checks_count": len(submit_guards),
        },
    }


def _augment_run_payload(run: Dict) -> Dict:
    enriched = dict(run)
    enriched["safety"] = _build_premium_safety_snapshot(run)
    return enriched


@app.get("/premium/profiles")
async def list_premium_profiles(current_user: dict = Depends(get_current_user)) -> Dict:
    sessions = list_saved_sessions()
    configs_by_key = {
        str(c.get("profile_name", "")).strip().lower(): c
        for c in premium_store.list_profile_configs()
    }

    profiles = []
    for session in sessions:
        profile_name = session.get("profile_name")
        if not profile_name:
            continue
        has_tag = _has_premium_tag(session)
        key = str(profile_name).strip().lower()
        config = configs_by_key.get(key)
        if has_tag or config:
            profiles.append(
                {
                    "profile_name": profile_name,
                    "has_premium_tag": has_tag,
                    "has_config": config is not None,
                    "config_updated_at": config.get("updated_at") if config else None,
                    "valid_session": bool(session.get("has_valid_cookies")),
                    "tags": session.get("tags", []),
                }
            )

    profiles.sort(key=lambda p: str(p.get("profile_name", "")).lower())
    return {"profiles": profiles, "total": len(profiles)}


@app.put("/premium/profiles/{profile_name}/config")
async def upsert_premium_profile_config(
    profile_name: str,
    request: PremiumProfileConfig,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    session = _find_session_by_profile_name(profile_name)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session not found: {profile_name}")
    if not _has_premium_tag(session):
        raise HTTPException(
            status_code=400,
            detail=f"Profile '{profile_name}' must have 'premium' tag before configuring premium automation",
        )

    payload = _model_to_dict(request)
    snapshot = premium_store.get_rules_snapshot()
    if snapshot and not payload.get("content_policy", {}).get("rules_snapshot_version"):
        payload.setdefault("content_policy", {})
        payload["content_policy"]["rules_snapshot_version"] = snapshot.get("version")

    saved = premium_store.upsert_profile_config(profile_name, payload)
    await broadcast_update(
        "premium_profile_config_updated",
        {"profile_name": profile_name, "updated_by": current_user.get("username")},
    )
    return {"success": True, "config": saved}


@app.post("/premium/rules/sync")
async def sync_premium_rules(
    request: RulesSyncRequest,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    req = _model_to_dict(request)
    negative_text = (req.get("negative_patterns_text") or "").strip()
    vocab_text = (req.get("vocabulary_guidance_text") or "").strip()
    source_paths = req.get("source_paths") or {}

    # If texts are missing, try explicit source paths.
    if (not negative_text or not vocab_text) and source_paths:
        try:
            negative_text, vocab_text = load_rule_texts_from_paths(source_paths)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Failed reading source_paths: {exc}")

    # Fallback to configured default local paths for convenience.
    if not negative_text or not vocab_text:
        default_paths = _premium_default_rule_paths()
        try:
            negative_text, vocab_text = load_rule_texts_from_paths(default_paths)
            source_paths = default_paths
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Rules text missing and default local rule paths unavailable. "
                    f"Provide text or valid source_paths. error={exc}"
                ),
            )

    snapshot = build_rules_snapshot(
        negative_patterns_text=negative_text,
        vocabulary_guidance_text=vocab_text,
        source_paths=source_paths,
        source_sha=req.get("source_sha"),
    )
    premium_store.set_rules_snapshot(snapshot)

    await broadcast_update(
        "premium_rules_synced",
        {
            "version": snapshot.get("version"),
            "synced_by": current_user.get("username"),
            "negative_patterns_count": len(snapshot.get("negative_patterns", [])),
            "vocabulary_count": len(snapshot.get("vocabulary_guidance", [])),
        },
    )
    return {
        "success": True,
        "snapshot": {
            "version": snapshot.get("version"),
            "source_sha": snapshot.get("source_sha"),
            "source_paths": snapshot.get("source_paths", {}),
            "synced_at": snapshot.get("synced_at"),
            "negative_patterns_count": len(snapshot.get("negative_patterns", [])),
            "vocabulary_count": len(snapshot.get("vocabulary_guidance", [])),
        },
    }


@app.post("/premium/runs")
async def create_premium_run(
    request: PremiumRunCreateRequest,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    req = _model_to_dict(request)
    run_spec = req.get("run_spec") or {}
    profile_name = str(run_spec.get("profile_name") or "").strip()
    if not profile_name:
        raise HTTPException(status_code=400, detail="run_spec.profile_name is required")

    session = _find_session_by_profile_name(profile_name)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session not found: {profile_name}")
    if not _has_premium_tag(session):
        raise HTTPException(status_code=400, detail=f"Profile '{profile_name}' is not premium-tagged")

    config = premium_store.get_profile_config(profile_name)
    if not config:
        raise HTTPException(status_code=400, detail=f"Premium config missing for profile '{profile_name}'")

    run = premium_store.enqueue_or_create_run(run_spec=run_spec, created_by=current_user.get("username", "unknown"))
    premium_store.append_event(
        run["id"],
        "run_created",
        {
            "created_by": current_user.get("username"),
            "status": run.get("status"),
            "queue_position": run.get("queue_position"),
            "blocked_by_run_id": run.get("blocked_by_run_id"),
            "admission_policy": run.get("admission_policy"),
        },
    )

    if run.get("status") == "queued":
        await broadcast_update(
            "premium_run_queued",
            {
                "run_id": run.get("id"),
                "profile_name": profile_name,
                "queue_position": run.get("queue_position"),
                "blocked_by_run_id": run.get("blocked_by_run_id"),
                "admission_policy": run.get("admission_policy"),
            },
        )
    else:
        await broadcast_update(
            "premium_run_scheduled",
            {
                "run_id": run.get("id"),
                "profile_name": profile_name,
                "next_execute_at": run.get("next_execute_at"),
                "admission_policy": run.get("admission_policy"),
            },
        )
    return _augment_run_payload(run)


@app.post("/premium/scheduler/tick")
async def premium_scheduler_tick(current_user: dict = Depends(get_current_user)) -> Dict:
    return await premium_scheduler.tick(source="api")


@app.get("/premium/status")
async def premium_status(current_user: dict = Depends(get_current_user)) -> Dict:
    status_payload = premium_scheduler.get_status()
    status_payload["recent_runs"] = [_augment_run_payload(run) for run in status_payload.get("recent_runs", [])]
    snapshot = premium_store.get_rules_snapshot()
    if snapshot:
        status_payload["rules_snapshot"] = {
            "version": snapshot.get("version"),
            "source_sha": snapshot.get("source_sha"),
            "synced_at": snapshot.get("synced_at"),
            "negative_patterns_count": len(snapshot.get("negative_patterns", [])),
            "vocabulary_count": len(snapshot.get("vocabulary_guidance", [])),
        }
    else:
        status_payload["rules_snapshot"] = None
    return status_payload


@app.get("/premium/runs")
async def list_premium_runs(
    limit: int = Query(default=50, ge=1, le=200),
    status_filter: Optional[str] = Query(default=None),
    current_user: dict = Depends(get_current_user),
) -> Dict:
    runs = premium_store.list_runs(limit=limit, status=status_filter)
    return {"runs": [_augment_run_payload(run) for run in runs], "total": len(runs)}


@app.get("/premium/runs/{run_id}")
async def get_premium_run(run_id: str, current_user: dict = Depends(get_current_user)) -> Dict:
    run = premium_store.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Premium run not found")
    return _augment_run_payload(run)


@app.post("/premium/runs/{run_id}/cancel")
async def cancel_premium_run(run_id: str, current_user: dict = Depends(get_current_user)) -> Dict:
    run = premium_store.cancel_run(run_id, actor=current_user.get("username", "unknown"))
    if not run:
        raise HTTPException(status_code=404, detail="Premium run not found")

    await broadcast_update(
        "premium_run_cancelled",
        {"run_id": run_id, "cancelled_by": current_user.get("username")},
    )
    return {"success": True, "run": _augment_run_payload(run)}


@app.get("/premium/duplicates/report/{profile_name}")
async def premium_duplicates_report(
    profile_name: str,
    limit: int = Query(default=200, ge=1, le=1000),
    threshold: float = Query(default=0.90, ge=0.5, le=1.0),
    current_user: dict = Depends(get_current_user),
) -> Dict:
    target = str(profile_name or "").strip().lower()
    runs = premium_store.list_runs(limit=limit)

    feed_rows = []
    for run in runs:
        for item in list(run.get("evidence") or []):
            if str(item.get("action_type") or "") != "feed_post":
                continue
            if str(item.get("profile_name") or "").strip().lower() != target:
                continue
            caption = str(item.get("generated_caption") or "").strip()
            if not caption:
                continue
            permalink = (
                (item.get("confirmation") or {}).get("post_permalink")
                or item.get("target_url")
            )
            feed_rows.append(
                {
                    "timestamp": item.get("timestamp"),
                    "run_id": item.get("run_id"),
                    "step_id": item.get("step_id"),
                    "caption": caption,
                    "permalink": permalink,
                }
            )

    feed_rows.sort(key=lambda row: str(row.get("timestamp") or ""))

    duplicates = []
    for i, left in enumerate(feed_rows):
        for j in range(i + 1, len(feed_rows)):
            right = feed_rows[j]
            ratio = near_duplicate_ratio(left.get("caption", ""), right.get("caption", ""))
            if ratio < float(threshold):
                continue
            duplicates.append(
                {
                    "similarity": round(float(ratio), 4),
                    "left": left,
                    "right": right,
                }
            )

    duplicates.sort(key=lambda item: item.get("similarity", 0.0), reverse=True)
    return {
        "profile_name": profile_name,
        "threshold": float(threshold),
        "total_feed_posts_scanned": len(feed_rows),
        "total_duplicate_pairs": len(duplicates),
        "duplicates": duplicates,
    }


@app.get("/config")
async def get_config(current_user: dict = Depends(get_current_user)) -> Dict:
    """Get current configuration."""
    return {
        "proxy_configured": bool(PROXY_URL),
        "viewport": MOBILE_VIEWPORT,
        "user_agent": DEFAULT_USER_AGENT
    }


# Credential Endpoints
@app.get("/credentials", response_model=List[CredentialInfo])
async def get_credentials(
    platform: Optional[Literal["facebook", "reddit"]] = Query(default=None),
    current_user: dict = Depends(get_current_user),
):
    """Get all saved credentials (without passwords)."""
    credential_manager.load_credentials()
    credentials = credential_manager.get_all_credentials(platform=platform)
    fb_sessions = list_saved_sessions()
    reddit_sessions = list_saved_reddit_sessions()

    fb_sessions_by_profile = {
        (s.get("profile_name") or "").strip().lower(): s
        for s in fb_sessions
        if s.get("profile_name")
    }
    fb_sessions_by_user_id = {
        str(s.get("user_id")): s
        for s in fb_sessions
        if s.get("user_id") is not None
    }
    reddit_sessions_by_profile = {
        (s.get("profile_name") or "").strip().lower(): s
        for s in reddit_sessions
        if s.get("profile_name")
    }
    reddit_sessions_by_credential = {
        str(s.get("linked_credential_id")): s
        for s in reddit_sessions
        if s.get("linked_credential_id")
    }

    enriched: List[Dict] = []
    for cred in credentials:
        session = None
        cred_platform = str(cred.get("platform") or "facebook").strip().lower()
        profile_name = str(cred.get("profile_name") or "").strip()

        if cred_platform == "reddit":
            if profile_name:
                session = reddit_sessions_by_profile.get(profile_name.lower())
            if session is None and cred.get("credential_id"):
                session = reddit_sessions_by_credential.get(str(cred.get("credential_id")))
        else:
            if profile_name:
                session = fb_sessions_by_profile.get(profile_name.lower())
            if session is None:
                session = fb_sessions_by_user_id.get(str(cred.get("uid")))

        enriched.append(
            {
                **cred,
                "session_connected": session is not None,
                "session_valid": (
                    session.get("has_valid_session") if cred_platform == "reddit" and session else
                    session.get("has_valid_cookies") if session else
                    None
                ),
                "session_profile_name": (session.get("profile_name") if session else None),
            }
        )

    return enriched


@app.post("/credentials")
async def add_credential(request: CredentialAddRequest, current_user: dict = Depends(get_current_user)) -> Dict:
    """Add a new credential."""
    storage_key = credential_manager.add_credential(
        uid=request.uid,
        password=request.password,
        secret=request.secret,
        profile_name=request.profile_name,
        platform=request.platform,
        username=request.username,
        email=request.email,
        email_password=request.email_password,
        profile_url=request.profile_url,
        display_name=request.display_name,
        tags=request.tags,
        fixture=request.fixture,
    )
    return {"success": True, "uid": request.uid, "credential_id": storage_key, "platform": request.platform}


@app.post("/credentials/bulk-import")
async def bulk_import_credentials(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)) -> Dict:
    """
    Import credentials from text file.

    Supports two formats:
    1. Old format (3 fields): uid:password:2fa_secret
       Example: 61571384288937:BHvSDSchultz:EBKJL7AVC3X6PPCG56HPDQTKV4X5R37K
       -> Stores credential for later login

    2. New format (6 fields): uid:password:dob:2fa_secret:user_agent:base64_cookies
       Example: 61571384288937:Pass123:01.01.1990:SECRETKEY:Mozilla/5.0...:W3siZG9t...
       -> Creates session directly with cookies, fetches profile name & photo
    """
    content = await file.read()
    lines = content.decode("utf-8").strip().split("\n")

    imported = 0
    sessions_created = 0
    errors = []

    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue

        parts = line.split(":")

        # Detect format by field count
        if len(parts) == 3:
            # Old format: uid:password:secret
            uid, password, secret = parts
            profile_name = f"fb_{uid[-6:]}"

            try:
                credential_manager.add_credential(
                    uid=uid,
                    password=password,
                    secret=secret,
                    profile_name=profile_name
                )
                imported += 1
                logger.info(f"Imported credential for {uid} as {profile_name}")
            except Exception as e:
                errors.append(f"Line {i+1}: {str(e)}")

        elif len(parts) >= 6:
            # New format: uid:password:dob:secret:user_agent:cookies_base64
            # Note: user_agent may contain colons, so we join the rest
            uid = parts[0]
            password = parts[1]
            dob = parts[2]  # Date of birth (stored but not used currently)
            secret = parts[3]
            user_agent = parts[4]
            cookies_base64 = ":".join(parts[5:])  # In case base64 has colons (shouldn't but be safe)

            try:
                # 1. Decode and convert cookies
                import base64
                cookies_json = base64.b64decode(cookies_base64).decode("utf-8")
                raw_cookies = json.loads(cookies_json)

                # Filter to Facebook domain and convert to Playwright format
                playwright_cookies = []
                for cookie in raw_cookies:
                    if ".facebook.com" in cookie.get("domain", ""):
                        playwright_cookies.append(convert_cookie_to_playwright(cookie))

                if not playwright_cookies:
                    errors.append(f"Line {i+1}: No Facebook cookies found")
                    continue

                # 2. Fetch profile data (name + photo) using cookies
                logger.info(f"Line {i+1}: Fetching profile data for UID {uid}...")
                profile_data = await fetch_profile_data_from_cookies(
                    cookies=playwright_cookies,
                    user_agent=user_agent,
                    proxy=None,  # Use service proxy
                )

                if not profile_data["success"]:
                    errors.append(f"Line {i+1}: Failed to fetch profile - {profile_data['error']}")
                    continue

                # 3. Generate profile name from real Facebook name
                real_name = profile_data["profile_name"]
                sanitized_name = real_name.lower().replace(" ", "_").replace("-", "_")
                # Remove any non-alphanumeric characters
                sanitized_name = "".join(c for c in sanitized_name if c.isalnum() or c == "_")

                # 4. Create session
                session = FacebookSession(sanitized_name)
                session.import_from_cookies(
                    cookies=playwright_cookies,
                    user_agent=user_agent,
                    proxy="",  # Empty = use service proxy
                    profile_picture=profile_data["profile_picture"],
                    tags=["imported", "with_cookies"],
                    display_name=real_name  # Pretty name for UI (e.g., "Elizabeth Cruz")
                )
                session.save()

                # 5. Store credentials (for potential re-login later)
                credential_manager.add_credential(
                    uid=uid,
                    password=password,
                    secret=secret,
                    profile_name=sanitized_name
                )

                sessions_created += 1
                imported += 1
                logger.info(f"Line {i+1}: Created session '{sanitized_name}' ({real_name}) with profile photo")

            except Exception as e:
                logger.error(f"Line {i+1}: Error processing cookies - {e}")
                errors.append(f"Line {i+1}: {str(e)}")
        else:
            errors.append(f"Line {i+1}: Invalid format (expected 3 or 6+ fields, got {len(parts)})")

    return {
        "imported": imported,
        "sessions_created": sessions_created,
        "credentials_only": imported - sessions_created,
        "errors": errors,
        "total_lines": len(lines)
    }


def _mask_proxy_value(proxy_value: Optional[str]) -> Optional[str]:
    if not proxy_value:
        return None
    try:
        parsed = urlparse(proxy_value)
        return f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
    except Exception:
        return None


def _resolve_effective_proxy(proxy_id: Optional[str] = None) -> Optional[str]:
    proxy_url = get_system_proxy()
    if proxy_id:
        proxy = proxy_manager.get_proxy(proxy_id)
        if not proxy:
            raise HTTPException(status_code=404, detail=f"Proxy not found: {proxy_id}")
        proxy_url = proxy.get("url")
    return proxy_url


def _reddit_program_payload_from_request(request: RedditProgramCreateRequest) -> Dict[str, Any]:
    return {
        "profile_selection": request.profile_selection.model_dump(),
        "schedule": request.schedule.model_dump(),
        "topic_constraints": request.topic_constraints.model_dump(),
        "content_assignments": request.content_assignments.model_dump(),
        "engagement_quotas": request.engagement_quotas.model_dump(),
        "generation_config": request.generation_config.model_dump(),
        "realism_policy": request.realism_policy.model_dump(),
        "notification_config": request.notification_config.model_dump(),
        "verification_contract": request.verification_contract.model_dump(),
        "execution_policy": request.execution_policy.model_dump(),
        "metadata": dict(request.metadata or {}),
    }


def _validate_reddit_program_payload(payload: Dict[str, Any]) -> None:
    profile_selection = dict(payload.get("profile_selection") or {})
    schedule = dict(payload.get("schedule") or {})
    topic_constraints = dict(payload.get("topic_constraints") or {})
    content_assignments = dict(payload.get("content_assignments") or {})
    engagement_quotas = dict(payload.get("engagement_quotas") or {})
    generation_config = dict(payload.get("generation_config") or {})
    notification_config = dict(payload.get("notification_config") or {})

    profile_names = [
        str(name).strip()
        for name in list(profile_selection.get("profile_names") or [])
        if str(name).strip()
    ]
    if not profile_names:
        raise HTTPException(status_code=400, detail="profile_selection.profile_names must contain at least one reddit profile")

    sessions_by_name = {
        str(item.get("profile_name") or "").strip(): item
        for item in list_saved_reddit_sessions()
    }
    missing_profiles = [name for name in profile_names if name not in sessions_by_name]
    if missing_profiles:
        raise HTTPException(status_code=400, detail=f"reddit sessions not found: {missing_profiles}")

    invalid_profiles = [name for name in profile_names if not sessions_by_name[name].get("has_valid_session")]
    if invalid_profiles:
        raise HTTPException(status_code=400, detail=f"reddit sessions do not have valid persisted auth: {invalid_profiles}")

    duration_days = int(schedule.get("duration_days", 1))
    if duration_days < 1:
        raise HTTPException(status_code=400, detail="schedule.duration_days must be >= 1")

    assignments = list(content_assignments.get("items") or [])
    for idx, assignment in enumerate(assignments):
        action = str(assignment.get("action") or "").strip()
        profile_name = str(assignment.get("profile_name") or "").strip()
        if profile_name not in profile_names:
            raise HTTPException(status_code=400, detail=f"content_assignments.items[{idx}].profile_name must be included in profile_selection.profile_names")
        day_offset = int(assignment.get("day_offset", 0))
        if day_offset < 0 or day_offset >= duration_days:
            raise HTTPException(status_code=400, detail=f"content_assignments.items[{idx}].day_offset must be between 0 and duration_days - 1")
        if action == "comment_post":
            if not str(assignment.get("target_url") or "").strip():
                raise HTTPException(status_code=400, detail=f"content_assignments.items[{idx}].target_url is required for comment_post")
            if not str(assignment.get("text") or "").strip():
                raise HTTPException(status_code=400, detail=f"content_assignments.items[{idx}].text is required for comment_post")
        elif action == "reply_comment":
            if not str(assignment.get("target_comment_url") or "").strip():
                raise HTTPException(status_code=400, detail=f"content_assignments.items[{idx}].target_comment_url is required for reply_comment")
            if not str(assignment.get("text") or "").strip():
                raise HTTPException(status_code=400, detail=f"content_assignments.items[{idx}].text is required for reply_comment")
        elif action == "upvote_post":
            if not str(assignment.get("target_url") or "").strip():
                raise HTTPException(status_code=400, detail=f"content_assignments.items[{idx}].target_url is required for upvote_post")
        elif action == "upvote_comment":
            if not str(assignment.get("target_comment_url") or "").strip():
                raise HTTPException(status_code=400, detail=f"content_assignments.items[{idx}].target_comment_url is required for upvote_comment")
        elif action == "join_subreddit":
            if not str(assignment.get("target_url") or "").strip():
                raise HTTPException(status_code=400, detail=f"content_assignments.items[{idx}].target_url is required for join_subreddit")
        elif action == "create_post":
            if not str(assignment.get("title") or "").strip():
                raise HTTPException(status_code=400, detail=f"content_assignments.items[{idx}].title is required for create_post")

    upvotes_min_per_day = max(0, int(engagement_quotas.get("upvotes_min_per_day", engagement_quotas.get("upvotes_per_day", 0) or 0)))
    upvotes_max_per_day = max(upvotes_min_per_day, int(engagement_quotas.get("upvotes_max_per_day", max(upvotes_min_per_day, engagement_quotas.get("upvotes_per_day", 0) or 0))))
    posts_min_per_day = max(0, int(engagement_quotas.get("posts_min_per_day", 0)))
    posts_max_per_day = max(posts_min_per_day, int(engagement_quotas.get("posts_max_per_day", posts_min_per_day)))
    comment_upvote_min_per_day = max(0, int(engagement_quotas.get("comment_upvote_min_per_day", 0)))
    comment_upvote_max_per_day = max(comment_upvote_min_per_day, int(engagement_quotas.get("comment_upvote_max_per_day", comment_upvote_min_per_day)))
    reply_min_per_day = max(0, int(engagement_quotas.get("reply_min_per_day", 0)))
    reply_max_per_day = max(reply_min_per_day, int(engagement_quotas.get("reply_max_per_day", reply_min_per_day)))
    if comment_upvote_max_per_day > upvotes_max_per_day and upvotes_max_per_day > 0:
        raise HTTPException(status_code=400, detail="comment_upvote_max_per_day cannot exceed upvotes_max_per_day")

    subreddits = [str(item).strip() for item in list(topic_constraints.get("subreddits") or []) if str(item).strip()]
    explicit_post_targets = [str(item).strip() for item in list(topic_constraints.get("explicit_post_targets") or []) if str(item).strip()]
    explicit_comment_targets = [str(item).strip() for item in list(topic_constraints.get("explicit_comment_targets") or []) if str(item).strip()]
    mandatory_join_urls = [str(item).strip() for item in list(topic_constraints.get("mandatory_join_urls") or []) if str(item).strip()]

    if posts_max_per_day > 0 or upvotes_max_per_day > 0 or reply_max_per_day > 0 or mandatory_join_urls:
        if not subreddits and not explicit_post_targets and not explicit_comment_targets and not mandatory_join_urls:
            raise HTTPException(
                status_code=400,
                detail="topic_constraints must include subreddits or explicit targets when random engagement quotas are enabled",
            )

    if int(generation_config.get("style_sample_count", 3)) < 1:
        raise HTTPException(status_code=400, detail="generation_config.style_sample_count must be >= 1")

    daily_summary_hour = int(notification_config.get("daily_summary_hour", 20))
    if daily_summary_hour < 0 or daily_summary_hour > 23:
        raise HTTPException(status_code=400, detail="notification_config.daily_summary_hour must be between 0 and 23")


def _recent_generation_evidence(program: Dict[str, Any], *, limit: int = 12) -> List[Dict[str, Any]]:
    collected: List[Dict[str, Any]] = []
    for item in reversed(list(((program.get("compiled") or {}).get("work_items") or []))):
        evidence = item.get("generation_evidence")
        if not evidence:
            continue
        collected.append(
            {
                "work_item_id": item.get("id"),
                "action": item.get("action"),
                "profile_name": item.get("profile_name"),
                "local_date": item.get("local_date"),
                "status": item.get("status"),
                "subreddit": item.get("subreddit") or ((item.get("discovered_target") or {}).get("subreddit")),
                "target_url": item.get("target_url"),
                "target_comment_url": item.get("target_comment_url"),
                "generation_evidence": evidence,
            }
        )
        if len(collected) >= limit:
            break
    return collected


def _get_active_reddit_bulk_rollout() -> Optional[tuple[str, asyncio.Task]]:
    for run_id, task in list(reddit_bulk_rollout_tasks.items()):
        if task.done():
            reddit_bulk_rollout_tasks.pop(run_id, None)
            continue
        return run_id, task
    return None


def _get_active_reddit_convergence() -> Optional[tuple[str, asyncio.Task]]:
    for run_id, task in list(reddit_convergence_tasks.items()):
        if task.done():
            reddit_convergence_tasks.pop(run_id, None)
            continue
        return run_id, task
    return None


def _register_reddit_convergence_task(run_id: str, task: asyncio.Task) -> None:
    task.set_name(f"reddit_convergence_{run_id}")
    reddit_convergence_tasks[run_id] = task

    def _cleanup_reddit_convergence_task(completed_task: asyncio.Task, task_key: str = run_id) -> None:
        reddit_convergence_tasks.pop(task_key, None)

    task.add_done_callback(_cleanup_reddit_convergence_task)


def _resolve_reddit_action_media_path(image_id: Optional[str]) -> Optional[str]:
    if not image_id:
        return None
    item = _get_media_or_none(image_id)
    if not item:
        raise HTTPException(status_code=404, detail="Media not found or expired")
    return str(item.get("path"))


async def _execute_reddit_action_payload(payload: Dict[str, Any], *, proxy_override: Optional[str] = None) -> Dict[str, Any]:
    profile_name = str(payload.get("profile_name") or "").strip()
    if not profile_name:
        raise HTTPException(status_code=400, detail="profile_name is required")

    session = RedditSession(profile_name)
    if not session.load():
        raise HTTPException(status_code=404, detail=f"Reddit session not found: {profile_name}")

    image_path = _resolve_reddit_action_media_path(payload.get("image_id"))
    return await run_reddit_action(
        session,
        action=str(payload.get("action") or "").strip(),
        proxy_url=proxy_override,
        url=payload.get("url") or payload.get("target_url"),
        target_comment_url=payload.get("target_comment_url"),
        text=payload.get("text") or payload.get("exact_text") or payload.get("brief"),
        title=payload.get("title"),
        body=payload.get("body") or payload.get("brief"),
        subreddit=payload.get("subreddit"),
        image_path=image_path,
    )


@app.get("/reddit/credentials", response_model=List[CredentialInfo])
async def get_reddit_credentials(current_user: dict = Depends(get_current_user)):
    return await get_credentials(platform="reddit", current_user=current_user)


@app.post("/reddit/credentials/bulk-import")
async def bulk_import_reddit_credentials(
    file: UploadFile = File(...),
    fixture: bool = True,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    content = await file.read()
    lines = [line.strip() for line in content.decode("utf-8").splitlines() if line.strip()]

    imported = 0
    errors: List[str] = []
    created_ids: List[str] = []

    for idx, line in enumerate(lines):
        try:
            storage_key = credential_manager.import_reddit_account_line(
                line,
                fixture=fixture,
                tags=["reddit", "fixture"] if fixture else ["reddit"],
            )
            created_ids.append(storage_key)
            imported += 1
        except Exception as exc:
            errors.append(f"Line {idx + 1}: {exc}")

    return {
        "platform": "reddit",
        "imported": imported,
        "created_ids": created_ids,
        "errors": errors,
        "total_lines": len(lines),
    }


@app.post("/reddit/credentials/seed")
async def seed_reddit_credentials(
    request: RedditBulkSeedRequest,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    imported = 0
    errors: List[str] = []
    created_ids: List[str] = []
    for idx, line in enumerate(request.lines):
        try:
            storage_key = credential_manager.import_reddit_account_line(
                line,
                fixture=request.fixture,
                tags=["reddit", "fixture"] if request.fixture else ["reddit"],
            )
            imported += 1
            created_ids.append(storage_key)
        except Exception as exc:
            errors.append(f"Line {idx + 1}: {exc}")
    return {
        "platform": "reddit",
        "imported": imported,
        "created_ids": created_ids,
        "errors": errors,
    }


@app.get("/reddit/sessions", response_model=List[RedditSessionInfo])
async def get_reddit_sessions(current_user: dict = Depends(get_current_user)):
    sessions = list_saved_reddit_sessions()
    results = []
    for item in sessions:
        stored_proxy = item.get("proxy")
        proxy_masked = _mask_proxy_value(stored_proxy or get_system_proxy())
        proxy_source = "session" if stored_proxy else ("env" if get_system_proxy() else None)
        results.append(
            RedditSessionInfo(
                file=item["file"],
                profile_name=item["profile_name"],
                display_name=item.get("display_name"),
                username=item.get("username"),
                email=item.get("email"),
                profile_url=item.get("profile_url"),
                extracted_at=item.get("extracted_at"),
                valid=bool(item.get("has_valid_session")),
                proxy="session" if stored_proxy else ("service" if get_system_proxy() else None),
                proxy_masked=proxy_masked,
                proxy_source=proxy_source,
                tags=item.get("tags", []),
                fixture=bool(item.get("fixture", False)),
                linked_credential_id=item.get("linked_credential_id"),
                warmup_state=item.get("warmup_state", {}),
            )
        )
    return results


@app.post("/reddit/sessions/create")
async def create_reddit_session_endpoint(
    request: RedditSessionCreateRequest,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    proxy_url = _resolve_effective_proxy(request.proxy_id)
    if not proxy_url:
        raise HTTPException(
            status_code=400,
            detail="Cannot create Reddit session: no proxy configured. Configure a default proxy or PROXY_URL.",
        )

    async def broadcast_callback(update_type: str, data: dict):
        await broadcast_update(update_type, data)

    await broadcast_update(
        "reddit_session_create_start",
        {"credential_id": request.credential_id, "proxy_id": request.proxy_id},
    )
    result = await create_reddit_session_from_credentials(
        credential_uid=request.credential_id,
        proxy_url=proxy_url,
        proxy_source="proxy_id" if request.proxy_id else ("env" if get_system_proxy() else "runtime"),
        broadcast_callback=broadcast_callback,
    )
    await broadcast_update(
        "reddit_session_create_complete",
        {
            "credential_id": request.credential_id,
            "success": result.get("success", False),
            "profile_name": result.get("profile_name"),
            "error": result.get("error"),
            "needs_attention": result.get("needs_attention", False),
        },
    )
    return result


@app.post("/reddit/sessions/bulk-create")
async def bulk_create_reddit_sessions_endpoint(
    request: RedditSessionBulkCreateRequest,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    proxy_url = _resolve_effective_proxy(request.proxy_id)
    if not proxy_url:
        raise HTTPException(
            status_code=400,
            detail="Cannot create Reddit sessions: no proxy configured. Configure a default proxy or PROXY_URL.",
        )

    normalized_lines = [str(line or "").strip() for line in list(request.lines or []) if str(line or "").strip()]
    if not normalized_lines:
        raise HTTPException(status_code=400, detail="lines is required")

    active = _get_active_reddit_bulk_rollout()
    if active:
        active_run_id, _task = active
        raise HTTPException(
            status_code=409,
            detail=f"Reddit bulk session rollout already in progress: {active_run_id}",
        )

    async def broadcast_callback(update_type: str, data: dict):
        await broadcast_update(update_type, data)

    if request.wait_for_completion:
        run_id = f"{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
        return await execute_reddit_bulk_session_rollout(
            run_id=run_id,
            lines=normalized_lines,
            proxy_url=proxy_url,
            proxy_source="proxy_id" if request.proxy_id else ("env" if get_system_proxy() else "runtime"),
            fixture=request.fixture,
            source_label=request.source_label,
            max_create_attempts=request.max_create_attempts,
            broadcast_callback=broadcast_callback,
            credential_manager=credential_manager,
        )

    run_id = f"{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"
    task = asyncio.create_task(
        execute_reddit_bulk_session_rollout(
            run_id=run_id,
            lines=normalized_lines,
            proxy_url=proxy_url,
            proxy_source="proxy_id" if request.proxy_id else ("env" if get_system_proxy() else "runtime"),
            fixture=request.fixture,
            source_label=request.source_label,
            max_create_attempts=request.max_create_attempts,
            broadcast_callback=broadcast_callback,
            credential_manager=credential_manager,
        )
    )
    task.set_name(f"reddit_bulk_rollout_{run_id}")
    reddit_bulk_rollout_tasks[run_id] = task

    def _cleanup_bulk_rollout_task(completed_task: asyncio.Task, task_key: str = run_id) -> None:
        reddit_bulk_rollout_tasks.pop(task_key, None)

    task.add_done_callback(_cleanup_bulk_rollout_task)

    await broadcast_update(
        "reddit_bulk_create_dispatched",
        {
            "run_id": run_id,
            "line_count": len(normalized_lines),
            "source_label": request.source_label,
        },
    )
    return {
        "success": True,
        "status": "dispatched",
        "run_id": run_id,
        "line_count": len(normalized_lines),
        "source_label": request.source_label,
        "report_status_url": f"/reddit/sessions/bulk-create/{run_id}",
    }


@app.get("/reddit/sessions/bulk-create/{run_id}")
async def get_reddit_bulk_create_report_endpoint(
    run_id: str,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    report = load_reddit_rollout_report(run_id)
    if report:
        return report

    active = reddit_bulk_rollout_tasks.get(run_id)
    if active and not active.done():
        return {
            "run_id": run_id,
            "status": "running",
            "results": [],
            "summary": {
                "total_accounts": 0,
                "imported_accounts": 0,
                "create_success_count": 0,
                "test_success_count": 0,
                "action_success_count": 0,
                "active_sessions_count": 0,
                "blocked_accounts_count": 0,
            },
        }

    raise HTTPException(status_code=404, detail=f"Reddit bulk rollout report not found: {run_id}")


@app.post("/reddit/sessions/converge-unlinked")
async def converge_unlinked_reddit_sessions_endpoint(
    request: RedditConvergeUnlinkedRequest,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    active = _get_active_reddit_convergence()
    if active:
        active_run_id, _ = active
        raise HTTPException(status_code=409, detail=f"Reddit convergence already running: {active_run_id}")

    proxy_url = _resolve_effective_proxy(request.proxy_id)
    proxy_source = "named_proxy" if request.proxy_id else "env"
    usernames = [str(item or "").strip() for item in list(request.usernames or []) if str(item or "").strip()]
    target_usernames = usernames or list(DEFAULT_UNLINKED_ORDER)
    run_id = f"reddit_converge_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"

    if request.wait_for_completion:
        task = asyncio.create_task(
            execute_reddit_unlinked_convergence(
                run_id=run_id,
                usernames=target_usernames,
                proxy_url=proxy_url,
                proxy_source=proxy_source,
                credential_manager=credential_manager,
                learning_store=RedditLoginLearningStore(),
                broadcast_callback=broadcast_update,
            )
        )
        _register_reddit_convergence_task(run_id, task)
        report = await task
        return {"success": True, **report}

    task = asyncio.create_task(
        execute_reddit_unlinked_convergence(
            run_id=run_id,
            usernames=target_usernames,
            proxy_url=proxy_url,
            proxy_source=proxy_source,
            credential_manager=credential_manager,
            learning_store=RedditLoginLearningStore(),
            broadcast_callback=broadcast_update,
        )
    )
    _register_reddit_convergence_task(run_id, task)
    return {
        "success": True,
        "status": "dispatched",
        "run_id": run_id,
        "target_usernames": target_usernames,
        "report_status_url": f"/reddit/sessions/converge-unlinked/{run_id}",
    }


@app.get("/reddit/sessions/converge-unlinked/{run_id}")
async def get_reddit_convergence_report_endpoint(
    run_id: str,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    report = load_reddit_convergence_report(run_id)
    if report:
        return report

    active = reddit_convergence_tasks.get(run_id)
    if active and not active.done():
        return {
            "run_id": run_id,
            "status": "running",
            "results": [],
            "summary": {
                "target_count": 0,
                "linked_count": 0,
                "blocked_count": 0,
            },
        }

    raise HTTPException(status_code=404, detail=f"Reddit convergence report not found: {run_id}")


@app.get("/reddit/login-learning/summary")
async def get_reddit_login_learning_summary(
    current_user: dict = Depends(get_current_user),
) -> Dict:
    store = RedditLoginLearningStore()
    store.sync_linked_sessions()
    return store.summary()


@app.get("/reddit/login-learning/accounts/{username}")
async def get_reddit_login_learning_account(
    username: str,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    store = RedditLoginLearningStore()
    store.sync_linked_sessions()
    return {
        "username": username,
        "policy_version": store.summary().get("policy_version"),
        "account": store.get_account(username),
    }


@app.post("/reddit/debug/reference-login")
async def reference_reddit_login_endpoint(
    request: RedditReferenceLoginRequest,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    return await run_reference_login_from_credentials(
        request.credential_id,
        reference_session_id=request.reference_session_id,
    )


@app.post("/reddit/debug/compare-audits")
async def compare_reddit_audits_endpoint(
    request: RedditAuditCompareRequest,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    return compare_reddit_login_attempts(
        request.reference_attempt_id,
        request.standalone_attempt_id,
    )


@app.post("/reddit/sessions/{profile_name}/test")
async def test_reddit_session_endpoint(
    profile_name: str,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    session = RedditSession(profile_name)
    return await test_reddit_session(session, _resolve_effective_proxy())


@app.delete("/reddit/sessions/{profile_name}")
async def delete_reddit_session(profile_name: str, current_user: dict = Depends(get_current_user)) -> Dict:
    session = RedditSession(profile_name)
    if not session.load():
        raise HTTPException(status_code=404, detail=f"Reddit session not found: {profile_name}")
    session.delete()
    return {"success": True, "profile_name": profile_name, "platform": "reddit"}


@app.post("/reddit/actions/run")
async def run_reddit_action_endpoint(
    request: RedditActionRequest,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    proxy_url = _resolve_effective_proxy()
    await broadcast_update(
        "reddit_action_start",
        {"profile_name": request.profile_name, "action": request.action},
    )
    result = await _execute_reddit_action_payload(request.model_dump(), proxy_override=proxy_url)
    await broadcast_update(
        "reddit_action_complete",
        {
            "profile_name": request.profile_name,
            "action": request.action,
            "success": result.get("success", False),
            "error": result.get("error"),
        },
    )
    return result


def _mission_to_action_payload(mission: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "profile_name": mission.get("profile_name"),
        "action": mission.get("action"),
        "target_url": mission.get("target_url"),
        "target_comment_url": mission.get("target_comment_url"),
        "subreddit": mission.get("subreddit"),
        "brief": mission.get("brief"),
        "exact_text": mission.get("exact_text"),
        "title": mission.get("title"),
        "body": mission.get("body"),
        "image_id": mission.get("image_id"),
    }


async def _run_single_reddit_mission(mission: Dict[str, Any]) -> Dict[str, Any]:
    payload = _mission_to_action_payload(mission)
    await broadcast_update(
        "reddit_mission_start",
        {
            "mission_id": mission.get("id"),
            "profile_name": mission.get("profile_name"),
            "action": mission.get("action"),
        },
    )
    result = await _execute_reddit_action_payload(payload, proxy_override=_resolve_effective_proxy())
    await broadcast_update(
        "reddit_mission_complete",
        {
            "mission_id": mission.get("id"),
            "profile_name": mission.get("profile_name"),
            "action": mission.get("action"),
            "success": result.get("success", False),
            "error": result.get("error"),
        },
    )
    return result


if reddit_mission_scheduler is None:
    reddit_mission_scheduler = RedditMissionScheduler(
        store=reddit_mission_store,
        runner=_run_single_reddit_mission,
    )


@app.get("/reddit/missions")
async def list_reddit_missions(current_user: dict = Depends(get_current_user)) -> Dict:
    return {"missions": reddit_mission_store.list_missions()}


@app.post("/reddit/missions")
async def create_reddit_mission(
    request: RedditMissionCreateRequest,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    mission = reddit_mission_store.create_mission(
        {
            "profile_name": request.profile_name,
            "action": request.action,
            "target_url": request.target_url,
            "target_comment_url": request.target_comment_url,
            "subreddit": request.subreddit,
            "brief": request.brief,
            "exact_text": request.exact_text,
            "title": request.title,
            "body": request.body,
            "image_id": request.image_id,
            "verification_requirements": request.verification_requirements or [],
            "cadence": request.cadence.model_dump(),
            "created_by": current_user.get("username"),
        }
    )
    return {"success": True, "mission": mission}


@app.put("/reddit/missions/{mission_id}")
async def update_reddit_mission(
    mission_id: str,
    request: RedditMissionUpdateRequest,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    updates = {k: v for k, v in request.model_dump(exclude_none=True).items()}
    if "cadence" in updates and isinstance(updates["cadence"], RedditMissionCadence):
        updates["cadence"] = updates["cadence"].model_dump()
    mission = reddit_mission_store.update_mission(mission_id, updates)
    if not mission:
        raise HTTPException(status_code=404, detail="Reddit mission not found")
    return {"success": True, "mission": mission}


@app.delete("/reddit/missions/{mission_id}")
async def delete_reddit_mission(
    mission_id: str,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    if not reddit_mission_store.delete_mission(mission_id):
        raise HTTPException(status_code=404, detail="Reddit mission not found")
    return {"success": True, "mission_id": mission_id}


@app.post("/reddit/missions/{mission_id}/run-now")
async def run_reddit_mission_now(
    mission_id: str,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    mission = reddit_mission_store.get_mission(mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Reddit mission not found")
    result = await _run_single_reddit_mission(mission)
    reddit_mission_store.mark_run_result(mission_id, result)
    return {"success": True, "result": result}


@app.post("/reddit/missions/run-due")
async def run_due_reddit_missions(current_user: dict = Depends(get_current_user)) -> Dict:
    results = await reddit_mission_scheduler.run_due_now()
    return {"success": True, "count": len(results), "results": results}


@app.get("/reddit/missions/status")
async def reddit_mission_status(current_user: dict = Depends(get_current_user)) -> Dict:
    due = reddit_mission_store.due_missions()
    return {
        "scheduler_running": bool(reddit_mission_scheduler and reddit_mission_scheduler._task and not reddit_mission_scheduler._task.done()),
        "mission_count": len(reddit_mission_store.list_missions()),
        "due_count": len(due),
    }


@app.post("/reddit/programs/preview")
async def preview_reddit_program(
    request: RedditProgramCreateRequest,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    payload = _reddit_program_payload_from_request(request)
    _validate_reddit_program_payload(payload)
    preview = reddit_program_store.preview_program(payload)
    return {"success": True, "program": preview}


@app.post("/reddit/programs/run-due")
async def run_due_reddit_programs(current_user: dict = Depends(get_current_user)) -> Dict:
    result = await reddit_program_scheduler.tick(source="manual")
    return {"success": True, **result}


@app.get("/reddit/programs/scheduler/status")
async def get_reddit_program_scheduler_status(current_user: dict = Depends(get_current_user)) -> Dict:
    return reddit_program_scheduler.get_status()


@app.get("/reddit/programs")
async def list_reddit_programs(current_user: dict = Depends(get_current_user)) -> Dict:
    return {"programs": reddit_program_store.list_programs()}


@app.post("/reddit/programs")
async def create_reddit_program(
    request: RedditProgramCreateRequest,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    payload = _reddit_program_payload_from_request(request)
    payload["metadata"] = {
        **dict(payload.get("metadata") or {}),
        "created_by": current_user.get("username"),
    }
    _validate_reddit_program_payload(payload)
    program = reddit_program_store.create_program(payload)
    await reddit_program_orchestrator.notification_service.send_program_email(
        program,
        key="created",
        kind="created",
        subject=f"reddit program created: {program.get('id')}",
        body=build_program_email_body(program, headline="reddit growth program created"),
        metadata={"created_by": current_user.get("username")},
    )
    program = reddit_program_store.save_program(program)
    return {"success": True, "program": program}


@app.get("/reddit/programs/{program_id}")
async def get_reddit_program(
    program_id: str,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    program = reddit_program_store.get_program(program_id)
    if not program:
        raise HTTPException(status_code=404, detail="Reddit program not found")
    return {"program": program}


@app.put("/reddit/programs/{program_id}")
async def update_reddit_program(
    program_id: str,
    request: RedditProgramUpdateRequest,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    existing = reddit_program_store.get_program(program_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Reddit program not found")

    updates: Dict[str, Any] = {}
    if request.status is not None:
        updates["status"] = request.status

    mutable_keys = [
        "profile_selection",
        "schedule",
        "topic_constraints",
        "content_assignments",
        "engagement_quotas",
        "generation_config",
        "realism_policy",
        "notification_config",
        "verification_contract",
        "execution_policy",
        "metadata",
    ]
    for key in mutable_keys:
        value = getattr(request, key)
        if value is not None:
            updates[key] = value.model_dump() if hasattr(value, "model_dump") else value

    spec_updates = {key: value for key, value in updates.items() if key in mutable_keys}
    if spec_updates:
        merged_payload = dict(existing.get("spec") or {})
        merged_payload.update(spec_updates)
        _validate_reddit_program_payload(merged_payload)

    try:
        program = reddit_program_store.update_program(program_id, updates)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not program:
        raise HTTPException(status_code=404, detail="Reddit program not found")
    return {"success": True, "program": program}


@app.post("/reddit/programs/{program_id}/run-now")
async def run_reddit_program_now(
    program_id: str,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    program = reddit_program_store.get_program(program_id)
    if not program:
        raise HTTPException(status_code=404, detail="Reddit program not found")
    result = await reddit_program_orchestrator.process_program(program_id)
    updated = reddit_program_store.get_program(program_id)
    return {"success": True, "result": result, "program": updated}


@app.post("/reddit/programs/{program_id}/pause")
async def pause_reddit_program(
    program_id: str,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    program = reddit_program_store.update_program(program_id, {"status": "paused"})
    if not program:
        raise HTTPException(status_code=404, detail="Reddit program not found")
    return {"success": True, "program": program}


@app.post("/reddit/programs/{program_id}/resume")
async def resume_reddit_program(
    program_id: str,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    program = reddit_program_store.update_program(program_id, {"status": "active"})
    if not program:
        raise HTTPException(status_code=404, detail="Reddit program not found")
    return {"success": True, "program": program}


@app.post("/reddit/programs/{program_id}/cancel")
async def cancel_reddit_program(
    program_id: str,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    program = reddit_program_store.update_program(program_id, {"status": "cancelled"})
    if not program:
        raise HTTPException(status_code=404, detail="Reddit program not found")
    return {"success": True, "program": program}


@app.get("/reddit/programs/{program_id}/status")
async def get_reddit_program_status(
    program_id: str,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    program = reddit_program_store.get_program(program_id)
    if not program:
        raise HTTPException(status_code=404, detail="Reddit program not found")
    return {
        "program_id": program_id,
        "status": program.get("status"),
        "next_run_at": program.get("next_run_at"),
        "remaining_contract": program.get("remaining_contract"),
        "contract_totals": program.get("contract_totals"),
        "daily_progress": program.get("daily_progress"),
        "join_progress_matrix": program.get("join_progress_matrix"),
        "realism_policy": ((program.get("spec") or {}).get("realism_policy") or {}),
        "failure_summary": program.get("failure_summary") or {},
        "recent_generation_evidence": _recent_generation_evidence(program),
        "notification_log": program.get("notification_log"),
        "recent_attempt_ids": program.get("recent_attempt_ids"),
    }


def _reddit_program_available_days(program: Dict[str, Any]) -> List[str]:
    daily_progress = dict(program.get("daily_progress") or {})
    days = sorted(str(day) for day in daily_progress.keys() if str(day or "").strip())
    if days:
        return days
    seen: Set[str] = set()
    for item in ((program.get("compiled") or {}).get("work_items") or []):
        local_date = str(item.get("local_date") or "").strip()
        if local_date:
            seen.add(local_date)
    return sorted(seen)


def _reddit_program_selected_local_date(program: Dict[str, Any], requested_local_date: Optional[str]) -> Optional[str]:
    available_days = _reddit_program_available_days(program)
    if not available_days:
        return None
    if requested_local_date:
        requested = str(requested_local_date).strip()
        if requested not in available_days:
            raise HTTPException(status_code=400, detail="Unknown reddit program local_date")
        return requested

    timezone_name = str((((program.get("spec") or {}).get("schedule") or {}).get("timezone") or "UTC")).strip() or "UTC"
    try:
        current_local_date = datetime.now(ZoneInfo(timezone_name)).date().isoformat()
    except Exception:
        current_local_date = datetime.utcnow().date().isoformat()
    if current_local_date in available_days:
        return current_local_date

    past_or_current = [day for day in available_days if day <= current_local_date]
    if past_or_current:
        return past_or_current[-1]
    return available_days[0]


def _reddit_item_target_url(item: Dict[str, Any]) -> Optional[str]:
    value = (
        str(item.get("target_url") or "").strip()
        or str(((item.get("result") or {}).get("target_url") or "")).strip()
        or str((((item.get("discovered_target") or {}).get("target_url") or ""))).strip()
    )
    return value or None


def _reddit_item_target_comment_url(item: Dict[str, Any]) -> Optional[str]:
    value = (
        str(item.get("target_comment_url") or "").strip()
        or str(((item.get("result") or {}).get("target_comment_url") or "")).strip()
        or str((((item.get("discovered_target") or {}).get("target_comment_url") or ""))).strip()
    )
    return value or None


def _reddit_item_target_ref(item: Dict[str, Any]) -> Optional[str]:
    return _reddit_item_target_comment_url(item) or _reddit_item_target_url(item)


def _operator_screenshot_artifact_url(detail: Optional[Dict[str, Any]]) -> Optional[str]:
    if not detail:
        return None
    for artifact in list(detail.get("artifacts") or []):
        if str(artifact.get("artifact_type") or "") == "screenshot":
            return str(artifact.get("download_url") or "").strip() or None
    return None


async def _build_reddit_program_operator_view(
    program: Dict[str, Any],
    *,
    local_date: Optional[str] = None,
    profile_name: Optional[str] = None,
) -> Dict[str, Any]:
    selected_local_date = _reddit_program_selected_local_date(program, local_date)
    work_items = list(((program.get("compiled") or {}).get("work_items") or []))
    profile_filter = str(profile_name or "").strip() or None
    filtered_items = [
        item for item in work_items
        if (selected_local_date is None or str(item.get("local_date") or "") == selected_local_date)
        and (profile_filter is None or str(item.get("profile_name") or "") == profile_filter)
    ]

    execution_policy = dict(((program.get("spec") or {}).get("execution_policy") or {}))
    max_attempts_per_item = max(1, int(execution_policy.get("max_attempts_per_item", 1) or 1))
    relevant_work_item_ids = {
        str(item.get("id") or "").strip()
        for item in filtered_items
        if str(item.get("id") or "").strip()
    }
    grouped_attempts = await list_forensic_attempts(
        filters={"run_id": str(program.get("id") or "")},
        limit=max(200, min(2000, len(filtered_items) * max_attempts_per_item + 50)),
    ) if relevant_work_item_ids else []

    attempt_history_by_work_item: Dict[str, List[Dict[str, Any]]] = {}
    for attempt in grouped_attempts:
        metadata = dict(attempt.get("metadata") or {})
        work_item_id = str(metadata.get("work_item_id") or "").strip()
        if work_item_id not in relevant_work_item_ids:
            continue
        attempt_history_by_work_item.setdefault(work_item_id, []).append(
            {
                "attempt_id": attempt.get("attempt_id"),
                "status": attempt.get("status"),
                "final_verdict": attempt.get("final_verdict"),
                "failure_class": attempt.get("failure_class"),
                "started_at": attempt.get("started_at"),
                "ended_at": attempt.get("ended_at"),
            }
        )

    latest_attempt_ids: List[str] = []
    for item in filtered_items:
        attempt_id = str(((item.get("result") or {}).get("attempt_id") or "")).strip()
        if attempt_id and attempt_id not in latest_attempt_ids:
            latest_attempt_ids.append(attempt_id)
    latest_details = await asyncio.gather(*(get_forensic_attempt_detail(attempt_id) for attempt_id in latest_attempt_ids)) if latest_attempt_ids else []
    latest_detail_by_attempt_id = {
        str((detail.get("attempt") or {}).get("attempt_id") or ""): detail
        for detail in latest_details
        if detail and (detail.get("attempt") or {}).get("attempt_id")
    }

    profile_order = list((((program.get("spec") or {}).get("profile_selection") or {}).get("profile_names") or []))
    profile_position = {name: index for index, name in enumerate(profile_order)}
    target_ref_counts: Dict[str, int] = {}
    thread_reply_counts: Dict[str, int] = {}
    for item in filtered_items:
        target_ref = _reddit_item_target_ref(item)
        if target_ref:
            target_ref_counts[target_ref] = target_ref_counts.get(target_ref, 0) + 1
        thread_ref = (
            str((((item.get("generation_evidence") or {}).get("thread_url") or ""))).strip()
            or str((((item.get("discovered_target") or {}).get("thread_url") or ""))).strip()
            or str(item.get("target_url") or "").strip()
        )
        if str(item.get("action") or "") == "reply_comment" and thread_ref:
            thread_reply_counts[thread_ref] = thread_reply_counts.get(thread_ref, 0) + 1
    action_rows: List[Dict[str, Any]] = []
    rows_by_profile: Dict[str, List[Dict[str, Any]]] = {}
    for item in filtered_items:
        result = dict(item.get("result") or {})
        generation_evidence = dict(item.get("generation_evidence") or {})
        attempt_id = str(result.get("attempt_id") or "").strip() or None
        detail = latest_detail_by_attempt_id.get(attempt_id or "", {})
        attempt = dict((detail or {}).get("attempt") or {})
        verdict = dict((detail or {}).get("verdict") or {})
        target_url = _reddit_item_target_url(item)
        target_comment_url = _reddit_item_target_comment_url(item)
        target_ref = target_comment_url or target_url
        thread_url = (
            str(generation_evidence.get("thread_url") or "").strip()
            or str((((item.get("discovered_target") or {}).get("thread_url") or ""))).strip()
            or target_url
        )
        screenshot_artifact_url = _operator_screenshot_artifact_url(detail)
        final_verdict = (
            str(result.get("final_verdict") or "").strip()
            or str(verdict.get("final_verdict") or "").strip()
            or str(attempt.get("final_verdict") or "").strip()
            or None
        )
        similarity_checks = dict(
            ((generation_evidence.get("novelty_validation") or {}).get("similarity_checks"))
            or ((generation_evidence.get("validation") or {}).get("similarity_checks"))
            or {}
        )
        similarity_flags = [
            scope
            for scope, metrics in similarity_checks.items()
            if isinstance(metrics, dict) and (
                bool(metrics.get("exact_duplicate"))
                or bool(metrics.get("opening_overlap"))
                or float(metrics.get("sequence_ratio") or 0) >= 0.76
                or float(metrics.get("token_overlap") or 0) >= 0.62
                or float(metrics.get("ngram_overlap") or 0) >= 0.45
            )
        ]
        target_collision_flags = {
            "duplicate_target_ref": bool(target_ref and target_ref_counts.get(target_ref, 0) > 1),
            "duplicate_reply_thread": bool(str(item.get("action") or "") == "reply_comment" and thread_url and thread_reply_counts.get(thread_url, 0) > 1),
        }
        row = {
            "work_item_id": item.get("id"),
            "local_date": item.get("local_date"),
            "profile_name": item.get("profile_name"),
            "action": item.get("action"),
            "subreddit": (
                item.get("subreddit")
                or ((item.get("discovered_target") or {}).get("subreddit"))
                or result.get("subreddit")
                or ((attempt.get("metadata") or {}).get("subreddit"))
            ),
            "status": item.get("status"),
            "final_verdict": final_verdict,
            "attempts": int(item.get("attempts") or 0),
            "attempt_id": attempt_id,
            "target_url": target_url,
            "target_comment_url": target_comment_url,
            "target_ref": target_ref,
            "thread_url": thread_url,
            "screenshot_artifact_url": screenshot_artifact_url,
            "scheduled_at": item.get("scheduled_at"),
            "completed_at": item.get("completed_at"),
            "error": item.get("error") or result.get("error") or attempt.get("error"),
            "persona_id": generation_evidence.get("persona_id"),
            "persona_role": generation_evidence.get("persona_role"),
            "case_style_applied": generation_evidence.get("case_style_applied"),
            "generated_text": generation_evidence.get("combined_text") or generation_evidence.get("text"),
            "word_count": generation_evidence.get("word_count") or ((generation_evidence.get("validation") or {}).get("word_count")),
            "rule_source_hashes": dict(generation_evidence.get("rule_source_hashes") or {}),
            "semantic_similarity_flags": similarity_flags,
            "target_collision_flags": target_collision_flags,
            "proof_flags": {
                "has_url": bool(target_ref),
                "has_screenshot": bool(screenshot_artifact_url),
                "has_attempt": bool(attempt_id),
                "success_confirmed": final_verdict == "success_confirmed",
                "unsafe_rollout": bool(similarity_flags or any(target_collision_flags.values())),
            },
            "attempt_history": attempt_history_by_work_item.get(str(item.get("id") or ""), []),
        }
        action_rows.append(row)
        rows_by_profile.setdefault(str(item.get("profile_name") or ""), []).append(row)

    action_rows.sort(
        key=lambda row: (
            profile_position.get(str(row.get("profile_name") or ""), 10_000),
            str(row.get("scheduled_at") or ""),
            str(row.get("action") or ""),
            str(row.get("work_item_id") or ""),
        )
    )

    selected_day_progress = dict(((program.get("daily_progress") or {}).get(selected_local_date or "") or {}))
    summary_profile_names = [
        profile for profile in profile_order
        if profile in selected_day_progress and (profile_filter is None or profile == profile_filter)
    ]
    for profile in selected_day_progress.keys():
        if profile not in summary_profile_names and (profile_filter is None or profile == profile_filter):
            summary_profile_names.append(profile)

    profiles_by_day: List[Dict[str, Any]] = []
    for current_profile_name in summary_profile_names:
        progress = dict(selected_day_progress.get(current_profile_name) or {})
        rows = list(rows_by_profile.get(current_profile_name) or [])
        proof_coverage = {
            "required_actions": len(rows),
            "with_url": sum(1 for row in rows if row["proof_flags"]["has_url"]),
            "with_screenshot": sum(1 for row in rows if row["proof_flags"]["has_screenshot"]),
            "with_attempt": sum(1 for row in rows if row["proof_flags"]["has_attempt"]),
            "success_confirmed": sum(1 for row in rows if row["proof_flags"]["success_confirmed"]),
            "unsafe_rollout": sum(1 for row in rows if row["proof_flags"]["unsafe_rollout"]),
        }
        profiles_by_day.append(
            {
                "profile_name": current_profile_name,
                "planned": dict(progress.get("planned") or {}),
                "completed": dict(progress.get("completed") or {}),
                "pending": dict(progress.get("pending") or {}),
                "blocked": dict(progress.get("blocked") or {}),
                "planned_total": sum(int(value) for value in dict(progress.get("planned") or {}).values()),
                "completed_total": sum(int(value) for value in dict(progress.get("completed") or {}).values()),
                "pending_total": sum(int(value) for value in dict(progress.get("pending") or {}).values()),
                "blocked_total": sum(int(value) for value in dict(progress.get("blocked") or {}).values()),
                "proof_coverage": proof_coverage,
            }
        )

    return {
        "program": {
            "id": program.get("id"),
            "status": program.get("status"),
            "next_run_at": program.get("next_run_at"),
            "contract_totals": program.get("contract_totals"),
            "remaining_contract": program.get("remaining_contract"),
            "available_days": _reddit_program_available_days(program),
            "selected_local_date": selected_local_date,
            "available_actions": sorted(str(action) for action in dict(program.get("contract_totals") or {}).keys()),
            "notification_log": program.get("notification_log"),
            "failure_summary": program.get("failure_summary") or {},
            "unsafe_rollout_flags": {
                "rows": sum(1 for row in action_rows if row["proof_flags"]["unsafe_rollout"]),
                "duplicate_target_refs": sum(1 for row in action_rows if row["target_collision_flags"]["duplicate_target_ref"]),
                "duplicate_reply_threads": sum(1 for row in action_rows if row["target_collision_flags"]["duplicate_reply_thread"]),
                "semantic_similarity": sum(1 for row in action_rows if row["semantic_similarity_flags"]),
            },
        },
        "profiles_by_day": profiles_by_day,
        "action_rows": action_rows,
    }


@app.get("/reddit/programs/{program_id}/operator-view")
async def get_reddit_program_operator_view(
    program_id: str,
    local_date: Optional[str] = Query(default=None),
    profile_name: Optional[str] = Query(default=None),
    current_user: dict = Depends(get_current_user),
) -> Dict:
    program = reddit_program_store.get_program(program_id)
    if not program:
        raise HTTPException(status_code=404, detail="Reddit program not found")
    return await _build_reddit_program_operator_view(program, local_date=local_date, profile_name=profile_name)


@app.get("/reddit/programs/{program_id}/evidence")
async def get_reddit_program_evidence(
    program_id: str,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    program = reddit_program_store.get_program(program_id)
    if not program:
        raise HTTPException(status_code=404, detail="Reddit program not found")

    forensic_group = await build_forensic_group({"run_id": program_id}, limit=200)
    attempt_ids = list(program.get("recent_attempt_ids") or [])[:10]
    details = await asyncio.gather(*(get_forensic_attempt_detail(attempt_id) for attempt_id in attempt_ids))
    return {
        "program_id": program_id,
        "program_status": program.get("status"),
        "contract_totals": program.get("contract_totals"),
        "join_progress_matrix": program.get("join_progress_matrix"),
        "realism_policy": ((program.get("spec") or {}).get("realism_policy") or {}),
        "failure_summary": program.get("failure_summary") or {},
        "recent_generation_evidence": _recent_generation_evidence(program, limit=24),
        "notification_log": program.get("notification_log"),
        "forensics": forensic_group,
        "recent_attempts": details,
        "target_history": list(program.get("target_history") or [])[-50:],
    }


class CookieImportRequest(BaseModel):
    """Request to import a session with pre-made cookies."""
    uid: str  # Facebook UID
    password: str  # For credential storage
    secret: Optional[str] = None  # 2FA secret
    user_agent: str  # User agent from the order file
    cookies_base64: str  # Base64 encoded cookies JSON array
    proxy: Optional[str] = ""  # Optional proxy URL


def convert_cookie_to_playwright(cookie: Dict) -> Dict:
    """Convert browser-export cookie format to Playwright format."""
    result = {
        "name": cookie["name"],
        "value": cookie["value"],
        "domain": cookie["domain"],
        "path": cookie["path"],
        "secure": cookie.get("secure", True),
        "httpOnly": cookie.get("httpOnly", False),
    }

    # Convert expirationDate to expires
    # Playwright only accepts -1 (session cookie) or positive unix timestamp
    expires_val = cookie.get("expirationDate") or cookie.get("expires")
    if expires_val is not None:
        # Handle negative/invalid expiration dates (except -1)
        if expires_val <= 0 and expires_val != -1:
            result["expires"] = -1  # Convert to session cookie
        else:
            result["expires"] = int(expires_val)  # Convert to int for safety
    else:
        result["expires"] = -1  # Session cookie

    # Convert sameSite
    same_site = cookie.get("sameSite", "Unspecified")
    if same_site in ("Unspecified", "no_restriction"):
        result["sameSite"] = "None"
    elif same_site == "lax":
        result["sameSite"] = "Lax"
    elif same_site == "strict":
        result["sameSite"] = "Strict"
    else:
        result["sameSite"] = same_site

    return result


@app.post("/sessions/import-cookies")
async def import_session_with_cookies(
    request: CookieImportRequest,
    current_user: dict = Depends(get_current_user)
) -> Dict:
    """
    Import a session directly from pre-made cookies (bypass login flow).

    This endpoint:
    1. Converts cookies to Playwright format
    2. Validates session by connecting to Facebook
    3. Fetches real profile name from Facebook
    4. Extracts profile picture
    5. Creates session file
    6. Stores credentials for future use

    All steps are mandatory - if any fails, the import fails.

    Authentication: JWT Bearer token OR X-API-Key header
    """
    import base64

    try:
        # Decode and convert cookies
        cookies_raw = json.loads(base64.b64decode(request.cookies_base64))
        cookies = [
            convert_cookie_to_playwright(c)
            for c in cookies_raw
            if c.get("domain", "").endswith("facebook.com")
        ]

        # Validate essential cookies exist
        cookie_names = [c["name"] for c in cookies]
        if "c_user" not in cookie_names or "xs" not in cookie_names:
            raise HTTPException(
                status_code=400,
                detail="Missing essential cookies: c_user and xs are required"
            )

        logger.info(f"Importing session for UID: {request.uid} with {len(cookies)} cookies")

        # Fetch profile data (name + photo) from Facebook
        profile_data = await fetch_profile_data_from_cookies(
            cookies=cookies,
            user_agent=request.user_agent,
            proxy=request.proxy if request.proxy else None
        )

        if not profile_data["success"]:
            raise HTTPException(
                status_code=400,
                detail=f"Failed to fetch profile data: {profile_data['error']}"
            )

        # Generate profile_name from real Facebook name
        real_name = profile_data["profile_name"]
        profile_name = real_name.lower().replace(" ", "_").replace(".", "")
        logger.info(f"Using profile name: {real_name} -> {profile_name}")

        # Create session using FacebookSession
        session = FacebookSession(profile_name)
        session.import_from_cookies(
            cookies=cookies,
            user_agent=request.user_agent,
            proxy=request.proxy or "",
            profile_picture=profile_data["profile_picture"],
            tags=["imported"]
        )
        session.save()

        # Store credentials for future re-login if needed
        credential_manager.add_credential(
            uid=request.uid,
            password=request.password,
            secret=request.secret,
            profile_name=profile_name
        )

        return {
            "success": True,
            "profile_name": profile_name,
            "display_name": real_name,
            "user_id": profile_data["user_id"],
            "has_profile_picture": True,
            "cookies_count": len(cookies)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to import session: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/credentials/{uid}")
async def delete_credential(
    uid: str,
    platform: Optional[Literal["facebook", "reddit"]] = Query(default=None),
    current_user: dict = Depends(get_current_user),
) -> Dict:
    """Delete a credential."""
    success = credential_manager.delete_credential(uid, platform=platform)
    if success:
        return {"success": True, "uid": uid, "platform": platform}
    raise HTTPException(status_code=404, detail=f"Credential not found: {uid}")


@app.get("/otp/{uid}", response_model=OTPResponse)
async def get_otp(
    uid: str,
    platform: Optional[Literal["facebook", "reddit"]] = Query(default=None),
    current_user: dict = Depends(get_current_user),
) -> OTPResponse:
    """Generate current OTP code for a UID."""
    credential_manager.load_credentials()
    result = credential_manager.generate_otp(uid, platform=platform)
    return OTPResponse(
        code=result.get("code"),
        remaining_seconds=result.get("remaining_seconds", 0),
        valid=result.get("valid", False),
        error=result.get("error")
    )


# Proxy Endpoints
@app.get("/proxies", response_model=List[ProxyInfo])
async def get_proxies(current_user: dict = Depends(get_current_user)):
    """Get all saved proxies including system proxy from PROXY_URL."""
    from urllib.parse import urlparse

    proxy_manager.load_proxies()
    proxies = proxy_manager.list_proxies()

    result = []

    # Add system proxy (from PROXY_URL env var) if configured
    if PROXY_URL:
        parsed = urlparse(PROXY_URL)
        # Get sessions that have this proxy stored
        sessions = list_saved_sessions()
        assigned = []
        for s in sessions:
            session = FacebookSession(s["profile_name"])
            if session.load() and session.get_proxy() == PROXY_URL:
                assigned.append(s["profile_name"])

        result.append(ProxyInfo(
            id="system",
            name="Mobile Proxy (System)",
            url_masked=f"{parsed.scheme}://{parsed.hostname}:{parsed.port}",
            host=parsed.hostname,
            port=parsed.port,
            type="mobile",
            country="US",
            health_status="active",
            last_tested=None,
            success_rate=None,
            avg_response_ms=None,
            test_count=0,
            assigned_sessions=assigned,
            created_at=None,
            is_system=True,
            is_default=False  # System proxy cannot be set as default
        ))

    # Add user-configured proxies
    for p in proxies:
        result.append(ProxyInfo(
            id=p["id"],
            name=p["name"],
            url_masked=p["url_masked"],
            host=p.get("host"),
            port=p.get("port"),
            type=p.get("type", "mobile"),
            country=p.get("country", "US"),
            health_status=p.get("health_status", "untested"),
            last_tested=p.get("last_tested"),
            success_rate=p.get("success_rate"),
            avg_response_ms=p.get("avg_response_ms"),
            test_count=p.get("test_count", 0),
            assigned_sessions=p.get("assigned_sessions", []),
            created_at=p.get("created_at", ""),
            is_system=False,
            is_default=p.get("is_default", False)
        ))

    return result


@app.post("/proxies")
async def add_proxy(request: ProxyAddRequest, current_user: dict = Depends(get_current_user)) -> Dict:
    """Add a new proxy."""
    proxy = proxy_manager.add_proxy(
        name=request.name,
        url=request.url,
        proxy_type=request.proxy_type,
        country=request.country
    )
    return {"success": True, "proxy_id": proxy["id"], "proxy": proxy}


@app.get("/proxies/{proxy_id}")
async def get_proxy(proxy_id: str, current_user: dict = Depends(get_current_user)) -> Dict:
    """Get a proxy by ID."""
    proxy = proxy_manager.get_proxy(proxy_id)
    if not proxy:
        raise HTTPException(status_code=404, detail=f"Proxy not found: {proxy_id}")
    return proxy


@app.put("/proxies/{proxy_id}")
async def update_proxy(proxy_id: str, request: ProxyUpdateRequest, current_user: dict = Depends(get_current_user)) -> Dict:
    """Update a proxy."""
    updates = {}
    if request.name is not None:
        updates["name"] = request.name
    if request.url is not None:
        updates["url"] = request.url
    if request.proxy_type is not None:
        updates["type"] = request.proxy_type
    if request.country is not None:
        updates["country"] = request.country

    proxy = proxy_manager.update_proxy(proxy_id, updates)
    if not proxy:
        raise HTTPException(status_code=404, detail=f"Proxy not found: {proxy_id}")
    return {"success": True, "proxy": proxy}


@app.delete("/proxies/{proxy_id}")
async def delete_proxy(proxy_id: str, current_user: dict = Depends(get_current_user)) -> Dict:
    """Delete a proxy."""
    success = proxy_manager.delete_proxy(proxy_id)
    if success:
        return {"success": True, "proxy_id": proxy_id}
    raise HTTPException(status_code=404, detail=f"Proxy not found: {proxy_id}")


@app.post("/proxies/{proxy_id}/test", response_model=ProxyTestResult)
async def test_proxy(proxy_id: str, current_user: dict = Depends(get_current_user)) -> ProxyTestResult:
    """Test a proxy's connectivity."""
    result = await proxy_manager.test_proxy(proxy_id)
    return ProxyTestResult(
        success=result.get("success", False),
        response_time_ms=result.get("response_time_ms"),
        ip=result.get("ip"),
        error=result.get("error")
    )


@app.post("/proxies/{proxy_id}/set-default")
async def set_default_proxy(proxy_id: str, current_user: dict = Depends(get_current_user)) -> Dict:
    """
    Set a proxy as the system default.

    The default proxy will be used for all operations that don't have
    a per-session proxy configured. This takes precedence over the
    PROXY_URL environment variable.
    """
    success = proxy_manager.set_default(proxy_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Proxy not found: {proxy_id}")

    proxy = proxy_manager.get_proxy(proxy_id)
    return {
        "success": True,
        "default_proxy_id": proxy_id,
        "default_proxy_name": proxy.get("name") if proxy else None,
        "message": f"Proxy '{proxy.get('name')}' is now the default"
    }


@app.post("/proxies/clear-default")
async def clear_default_proxy(current_user: dict = Depends(get_current_user)) -> Dict:
    """
    Clear the default proxy setting.

    After clearing, the system will fall back to using the PROXY_URL
    environment variable.
    """
    proxy_manager.clear_default()
    return {
        "success": True,
        "message": "Default proxy cleared. System will use PROXY_URL environment variable."
    }


@app.post("/sessions/{profile_name}/assign-proxy")
async def assign_proxy_to_session(profile_name: str, proxy_id: str, current_user: dict = Depends(get_current_user)) -> Dict:
    """Assign a proxy to a session."""
    # Verify session exists
    session = FacebookSession(profile_name)
    if not session.load():
        raise HTTPException(status_code=404, detail=f"Session not found: {profile_name}")

    # Verify proxy exists
    proxy = proxy_manager.get_proxy(proxy_id)
    if not proxy:
        raise HTTPException(status_code=404, detail=f"Proxy not found: {proxy_id}")

    # Assign proxy
    success = proxy_manager.assign_to_session(proxy_id, profile_name)
    if success:
        # Also update the session's proxy field
        session.data["proxy"] = proxy.get("url")
        session.save()
        return {"success": True, "profile_name": profile_name, "proxy_id": proxy_id}

    raise HTTPException(status_code=500, detail="Failed to assign proxy")


# Session Creation Endpoint
@app.post("/sessions/create")
async def create_session(request: SessionCreateRequest, current_user: dict = Depends(get_current_user)) -> Dict:
    """
    Create a new session by logging in with stored credentials.

    This triggers automated login using the login_bot module.
    Progress is broadcast via WebSocket.
    """
    credential_uid = request.credential_uid

    # Get proxy URL - use effective proxy (user default > env var), allow override from proxy_id
    proxy_url = get_system_proxy()  # Start with effective proxy (respects user default)
    if request.proxy_id:
        proxy = proxy_manager.get_proxy(request.proxy_id)
        if proxy:
            proxy_url = proxy.get("url")
        else:
            raise HTTPException(status_code=404, detail=f"Proxy not found: {request.proxy_id}")

    # FAIL if no proxy available - sessions must always have a proxy
    if not proxy_url:
        raise HTTPException(
            status_code=400,
            detail="Cannot create session: No proxy configured. Set a default proxy or PROXY_URL environment variable."
        )

    # Broadcast that session creation is starting
    await broadcast_update("session_create_start", {
        "credential_uid": credential_uid,
        "proxy_id": request.proxy_id
    })

    # Create a broadcast callback that uses our WebSocket broadcast
    async def broadcast_callback(update_type: str, data: dict):
        await broadcast_update(update_type, data)

    # Run login automation
    result = await create_session_from_credentials(
        credential_uid=credential_uid,
        proxy_url=proxy_url,
        broadcast_callback=broadcast_callback
    )

    # Broadcast completion
    await broadcast_update("session_create_complete", {
        "credential_uid": credential_uid,
        "success": result.get("success", False),
        "profile_name": result.get("profile_name"),
        "error": result.get("error"),
        "needs_attention": result.get("needs_attention", False)
    })

    if not result.get("success"):
        # Return error details but don't throw exception
        # so frontend can handle the "needs_attention" state
        return result

    return result


@app.post("/sessions/create-batch")
async def create_sessions_batch(request: BatchSessionCreateRequest, current_user: dict = Depends(get_current_user)) -> Dict:
    """
    Create multiple sessions concurrently with rate limiting.

    Uses asyncio.Semaphore to limit concurrent logins to MAX_CONCURRENT (5).
    Broadcasts progress for each credential via WebSocket.
    """
    credential_uids = request.credential_uids

    if not credential_uids:
        raise HTTPException(status_code=400, detail="No credentials provided")

    # Get proxy URL - use effective proxy (user default > env var), allow override from proxy_id
    proxy_url = get_system_proxy()
    if request.proxy_id:
        proxy = proxy_manager.get_proxy(request.proxy_id)
        if proxy:
            proxy_url = proxy.get("url")
        else:
            raise HTTPException(status_code=404, detail=f"Proxy not found: {request.proxy_id}")

    if not proxy_url:
        raise HTTPException(
            status_code=400,
            detail="Cannot create sessions: No proxy configured. Set a default proxy or PROXY_URL environment variable."
        )

    logger.info(f"Starting batch session creation for {len(credential_uids)} credentials")

    # Broadcast batch start
    await broadcast_update("batch_session_start", {
        "total": len(credential_uids),
        "credential_uids": credential_uids
    })

    # Semaphore for concurrency control
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def create_one(credential_uid: str) -> Dict:
        async with semaphore:
            logger.info(f"[Batch] Starting session creation for {credential_uid}")

            # Broadcast individual start
            await broadcast_update("session_create_start", {
                "credential_uid": credential_uid,
                "proxy_id": request.proxy_id
            })

            # Create broadcast callback
            async def broadcast_callback(update_type: str, data: dict):
                await broadcast_update(update_type, data)

            try:
                result = await create_session_from_credentials(
                    credential_uid=credential_uid,
                    proxy_url=proxy_url,
                    broadcast_callback=broadcast_callback
                )

                # Broadcast individual completion
                await broadcast_update("session_create_complete", {
                    "credential_uid": credential_uid,
                    "success": result.get("success", False),
                    "profile_name": result.get("profile_name"),
                    "error": result.get("error"),
                    "needs_attention": result.get("needs_attention", False)
                })

                logger.info(f"[Batch] Completed session creation for {credential_uid}: success={result.get('success')}")

                return {
                    "credential_uid": credential_uid,
                    **result
                }
            except Exception as e:
                logger.error(f"[Batch] Error creating session for {credential_uid}: {e}")
                # Broadcast failure
                await broadcast_update("session_create_complete", {
                    "credential_uid": credential_uid,
                    "success": False,
                    "error": str(e)
                })
                return {
                    "credential_uid": credential_uid,
                    "success": False,
                    "error": str(e)
                }

    # Run all sessions concurrently (limited by semaphore)
    results = await asyncio.gather(
        *[create_one(uid) for uid in credential_uids],
        return_exceptions=True
    )

    # Process results
    successes = []
    failures = []
    for i, result in enumerate(results):
        uid = credential_uids[i]
        if isinstance(result, Exception):
            failures.append({"credential_uid": uid, "error": str(result)})
        elif result.get("success"):
            successes.append(result)
        else:
            failures.append(result)

    logger.info(f"Batch session creation complete: {len(successes)} successes, {len(failures)} failures")

    # Broadcast batch completion
    await broadcast_update("batch_session_complete", {
        "total": len(credential_uids),
        "success_count": len(successes),
        "failure_count": len(failures)
    })

    return {
        "total": len(credential_uids),
        "success_count": len(successes),
        "failure_count": len(failures),
        "successes": successes,
        "failures": failures
    }


@app.post("/sessions/{profile_name}/refresh-name")
async def refresh_profile_name(profile_name: str, current_user: dict = Depends(get_current_user)) -> Dict:
    """
    Refresh the profile name for an existing session by fetching it from Facebook.

    This navigates to /me/ using the session's cookies and extracts the real profile name.
    The session file is renamed and the credential is updated.
    """
    result = await refresh_session_profile_name(profile_name)

    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to refresh profile name"))

    return result


@app.post("/sessions/{profile_name}/refresh-picture")
async def refresh_profile_picture(profile_name: str, current_user: dict = Depends(get_current_user)) -> Dict:
    """Refresh the profile picture for a session by visiting Facebook and extracting the current photo."""
    result = await refresh_session_picture(profile_name)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to refresh profile picture"))
    return result


class BatchRefreshPicturesRequest(BaseModel):
    profile_names: List[str]


@app.post("/sessions/batch-refresh-pictures")
async def batch_refresh_pictures(request: BatchRefreshPicturesRequest, current_user: dict = Depends(get_current_user)) -> Dict:
    """Batch refresh profile pictures for multiple sessions. Runs 3 at a time."""
    semaphore = asyncio.Semaphore(3)
    results = {"total": len(request.profile_names), "success_count": 0, "failure_count": 0, "results": []}

    async def refresh_one(name: str):
        async with semaphore:
            return await refresh_session_picture(name)

    tasks = [refresh_one(name) for name in request.profile_names]
    completed = await asyncio.gather(*tasks, return_exceptions=True)

    for r in completed:
        if isinstance(r, Exception):
            results["failure_count"] += 1
            results["results"].append({"success": False, "error": str(r)})
        elif r.get("success"):
            results["success_count"] += 1
            results["results"].append(r)
        else:
            results["failure_count"] += 1
            results["results"].append(r)

    return results


@app.post("/sessions/fix-display-names")
async def fix_display_names(current_user: dict = Depends(get_current_user)) -> Dict:
    """
    Fix display_name field for existing sessions.

    Converts profile_name (e.g., "elizabeth_cruz") to display_name (e.g., "Elizabeth Cruz").
    """
    sessions = list_saved_sessions()
    fixed = 0

    for s in sessions:
        profile_name = s.get("profile_name")
        if not profile_name:
            continue

        session = FacebookSession(profile_name)
        if session.load():
            # Check if display_name is missing or same as profile_name
            current_display = session.data.get("display_name")
            if not current_display or current_display == profile_name:
                # Convert profile_name to title case (elizabeth_cruz -> Elizabeth Cruz)
                pretty_name = profile_name.replace("_", " ").title()
                session.data["display_name"] = pretty_name
                session.save()
                fixed += 1
                logger.info(f"Fixed display_name for {profile_name} -> {pretty_name}")

    return {"fixed": fixed, "total": len(sessions)}


@app.post("/sessions/refresh-all-names")
async def refresh_all_profile_names(current_user: dict = Depends(get_current_user)) -> Dict:
    """
    Refresh profile names for all existing sessions.

    Returns a summary of which sessions were successfully updated.
    """
    sessions = list_saved_sessions()
    results = {
        "total": len(sessions),
        "success": 0,
        "failed": 0,
        "updates": []
    }

    for session in sessions:
        profile_name = session.get("profile_name")
        if not profile_name:
            continue

        try:
            result = await refresh_session_profile_name(profile_name)
            if result.get("success"):
                results["success"] += 1
                results["updates"].append({
                    "old_name": profile_name,
                    "new_name": result.get("new_profile_name"),
                    "success": True
                })
            else:
                results["failed"] += 1
                results["updates"].append({
                    "old_name": profile_name,
                    "error": result.get("error"),
                    "success": False
                })
        except Exception as e:
            results["failed"] += 1
            results["updates"].append({
                "old_name": profile_name,
                "error": str(e),
                "success": False
            })

    return results


@app.post("/workflow/dedupe-profile-names")
async def workflow_dedupe_profile_names(
    request: DedupeWorkflowRequest,
    current_user: dict = Depends(get_current_user),
) -> Dict:
    """
    Build/apply duplicate display-name remediation workflow.

    - dry_run: returns deterministic plan with keep/rename split
    - apply: executes sequentially, retries failed profile jobs up to 2 times
    """
    sessions = list_saved_sessions()
    plan = build_dedupe_plan(sessions)

    if request.mode == "dry_run":
        return {
            "success": True,
            "mode": "dry_run",
            **plan,
        }

    if request.plan_id and request.plan_id != plan.get("plan_id"):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "plan_id mismatch",
                "provided_plan_id": request.plan_id,
                "current_plan_id": plan.get("plan_id"),
            },
        )

    apply_result = await apply_dedupe_plan(plan)
    return {
        "success": True,
        "mode": "apply",
        **apply_result,
    }


# ============================================================================
# Interactive Remote Control Endpoints
# ============================================================================

# Models for remote control
class RemoteActionRequest(BaseModel):
    """Generic action request for remote control."""
    action_type: str  # "click", "key", "scroll", "navigate", "type"
    x: Optional[int] = None
    y: Optional[int] = None
    key: Optional[str] = None
    modifiers: Optional[List[str]] = None
    text: Optional[str] = None
    delta_y: Optional[int] = None
    url: Optional[str] = None


class ImageUploadResponse(BaseModel):
    success: bool
    image_id: Optional[str] = None
    filename: Optional[str] = None
    size: Optional[int] = None
    expires_at: Optional[str] = None
    error: Optional[str] = None


# In-memory storage for pending uploads (per-session)
pending_uploads: Dict[str, Dict] = {}


async def _websocket_session_control_impl(
    websocket: WebSocket,
    session_id: str,
    *,
    platform: Literal["facebook", "reddit"],
    token: Optional[str] = None,
):
    """
    WebSocket endpoint for interactive browser control. Requires token query parameter.

    Handles:
    - Frame streaming (server -> client, JSON with base64 image)
    - Input events (client -> server, JSON)
    - State updates (bidirectional, JSON)
    """
    # Validate token before accepting connection
    if not token:
        await websocket.close(code=4001, reason="Token required")
        return

    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        await websocket.close(code=4001, reason="Invalid token")
        return

    username = payload.get("sub")
    user = user_manager.get_user(username)
    if not user or not user.get("is_active"):
        await websocket.close(code=4001, reason="User not found or inactive")
        return

    await websocket.accept()
    manager = get_browser_manager()

    try:
        # Subscribe FIRST so we receive progress updates during start_session
        manager.subscribe(websocket)

        # Health-aware readiness check even for same session id.
        result = await manager.ensure_session_ready(session_id, platform=platform)
        if not result["success"]:
            manager.unsubscribe(websocket)
            await websocket.send_json({"type": "error", "data": {"message": result.get("error", "Failed to start session")}})
            await websocket.close()
            return

        # Send initial state
        state = await manager.get_current_state()
        try:
            await websocket.send_json({"type": "state", "data": state})
            await websocket.send_json({"type": "browser_ready", "data": {"session_id": session_id}})
            bootstrap_sent = await manager.send_bootstrap_frame(websocket)
            if not bootstrap_sent:
                await asyncio.sleep(3.0)
                if not manager.subscriber_has_recent_frame(websocket, within_seconds=3.0):
                    heal_result = await manager.auto_heal_session(
                        session_id=session_id,
                        platform=platform,
                        reason="bootstrap_frame_timeout",
                    )
                    if not heal_result.get("success"):
                        await websocket.send_json({
                            "type": "error",
                            "data": {"message": heal_result.get("error", "Auto-heal failed")},
                        })
                    else:
                        await manager.send_bootstrap_frame(websocket)
        except Exception as e:
            logger.warning(f"Failed to send initial state: {e}")
            return

        # Handle incoming messages
        while True:
            try:
                message = await websocket.receive_text()
                data = json.loads(message)

                action_type = data.get("type")
                action_data = data.get("data", {})
                action_id = data.get("action_id", "")

                result = {"success": False, "error": "Unknown action"}

                if action_type == "click":
                    result = await manager.handle_click(
                        x=action_data.get("x", 0),
                        y=action_data.get("y", 0)
                    )
                elif action_type == "key":
                    result = await manager.handle_keyboard(
                        key=action_data.get("key", ""),
                        modifiers=action_data.get("modifiers", [])
                    )
                elif action_type == "type":
                    result = await manager.handle_type(
                        text=action_data.get("text", "")
                    )
                elif action_type == "scroll":
                    result = await manager.handle_scroll(
                        x=action_data.get("x", 0),
                        y=action_data.get("y", 0),
                        delta_y=action_data.get("deltaY", 0)
                    )
                elif action_type == "navigate":
                    result = await manager.navigate(
                        url=action_data.get("url", "")
                    )
                elif action_type == "ping":
                    result = {"success": True, "action": "pong"}

                # Send action result
                await websocket.send_json({
                    "type": "action_result",
                    "data": {
                        "action_id": action_id,
                        **result
                    }
                })

            except WebSocketDisconnect:
                break
            except json.JSONDecodeError as e:
                try:
                    await websocket.send_json({"type": "error", "data": {"message": f"Invalid JSON: {e}"}})
                except Exception:
                    pass  # Connection already dead
            except Exception as e:
                logger.error(f"Error handling WS message: {e}")
                try:
                    await websocket.send_json({"type": "error", "data": {"message": str(e)}})
                except Exception:
                    pass  # Connection already dead

    except WebSocketDisconnect:
        logger.info(f"Remote control WS disconnected for session {session_id} ({platform})")
    except Exception as e:
        logger.error(f"Remote control WS error: {e}")
    finally:
        manager.unsubscribe(websocket)
        # Note: Browser stays open for reconnection


@app.websocket("/ws/session/{session_id}/control")
async def websocket_session_control(websocket: WebSocket, session_id: str, token: str = Query(None)):
    await _websocket_session_control_impl(websocket, session_id, platform="facebook", token=token)


@app.websocket("/ws/reddit/session/{session_id}/control")
async def websocket_reddit_session_control(websocket: WebSocket, session_id: str, token: str = Query(None)):
    await _websocket_session_control_impl(websocket, session_id, platform="reddit", token=token)


@app.post("/sessions/{session_id}/remote/start")
async def start_remote_session(session_id: str, current_user: dict = Depends(get_current_user)) -> Dict:
    """Start a remote control session for the given session."""
    manager = get_browser_manager()
    return await manager.start_session(session_id, platform="facebook")


@app.post("/sessions/{session_id}/remote/restart")
async def restart_remote_session(session_id: str, current_user: dict = Depends(get_current_user)) -> Dict:
    """Force restart remote control browser for the same session id."""
    manager = get_browser_manager()
    return await manager.restart_session(
        session_id,
        platform="facebook",
        reason=f"manual_restart:{current_user.get('username', 'unknown')}",
    )


@app.post("/sessions/{session_id}/remote/stop")
async def stop_remote_session(session_id: str, current_user: dict = Depends(get_current_user)) -> Dict:
    """Stop the current remote control session."""
    manager = get_browser_manager()
    if manager.session_id != session_id or manager.platform != "facebook":
        return {"success": False, "error": "Session not active"}
    return await manager.close_session()


@app.get("/sessions/remote/status")
async def get_remote_status(current_user: dict = Depends(get_current_user)) -> Dict:
    """Get current remote session status."""
    manager = get_browser_manager()
    return await manager.get_current_state()


@app.get("/sessions/{session_id}/remote/logs")
async def get_session_action_logs(session_id: str, limit: int = 100, current_user: dict = Depends(get_current_user)) -> List[Dict]:
    """Get action logs for the current session."""
    manager = get_browser_manager()
    if manager.session_id == session_id and manager.platform == "facebook":
        return manager.get_action_log(limit)
    return []


@app.post("/reddit/sessions/{session_id}/remote/start")
async def start_reddit_remote_session(session_id: str, current_user: dict = Depends(get_current_user)) -> Dict:
    """Start a Reddit remote control session for the given saved session."""
    manager = get_browser_manager()
    return await manager.start_session(session_id, platform="reddit")


@app.post("/reddit/sessions/{session_id}/remote/restart")
async def restart_reddit_remote_session(session_id: str, current_user: dict = Depends(get_current_user)) -> Dict:
    """Force restart the current Reddit remote control browser for the same session id."""
    manager = get_browser_manager()
    return await manager.restart_session(
        session_id,
        platform="reddit",
        reason=f"manual_restart:{current_user.get('username', 'unknown')}",
    )


@app.post("/reddit/sessions/{session_id}/remote/stop")
async def stop_reddit_remote_session(session_id: str, current_user: dict = Depends(get_current_user)) -> Dict:
    """Stop the current Reddit remote control session."""
    manager = get_browser_manager()
    if manager.session_id != session_id or manager.platform != "reddit":
        return {"success": False, "error": "Session not active"}
    return await manager.close_session()


@app.get("/reddit/sessions/{session_id}/remote/logs")
async def get_reddit_session_action_logs(session_id: str, limit: int = 100, current_user: dict = Depends(get_current_user)) -> List[Dict]:
    """Get action logs for the current Reddit remote session."""
    manager = get_browser_manager()
    if manager.session_id == session_id and manager.platform == "reddit":
        return manager.get_action_log(limit)
    return []


# Image upload for file chooser interception
@app.post("/sessions/{session_id}/upload-image", response_model=ImageUploadResponse)
async def upload_image_for_session(session_id: str, file: UploadFile = File(...), current_user: dict = Depends(get_current_user)) -> ImageUploadResponse:
    """
    Upload an image for use in an interactive session.
    Stores temporarily and associates with the session for file chooser interception.
    """
    from datetime import timedelta
    from pathlib import Path
    import uuid

    # Validation
    allowed_types = ['image/jpeg', 'image/png', 'image/webp']
    max_size = 10 * 1024 * 1024  # 10MB

    if file.content_type not in allowed_types:
        return ImageUploadResponse(
            success=False,
            error=f"Invalid file type. Allowed: {', '.join(allowed_types)}"
        )

    content = await file.read()
    if len(content) > max_size:
        return ImageUploadResponse(
            success=False,
            error=f"File too large. Max size: {max_size // (1024*1024)}MB"
        )

    # Generate unique ID and save to temp location
    image_id = str(uuid.uuid4())[:8]
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    # Preserve original extension
    ext = Path(file.filename).suffix or '.jpg'
    temp_path = UPLOAD_DIR / f"{image_id}{ext}"
    temp_path.write_bytes(content)

    # Store in pending uploads with expiration
    expires_at = datetime.now() + timedelta(minutes=30)
    pending_uploads[session_id] = {
        "image_id": image_id,
        "path": str(temp_path),
        "filename": file.filename,
        "size": len(content),
        "uploaded_at": datetime.now().isoformat(),
        "expires_at": expires_at.isoformat()
    }

    logger.info(f"Image uploaded for session {session_id}: {image_id}")

    return ImageUploadResponse(
        success=True,
        image_id=image_id,
        filename=file.filename,
        size=len(content),
        expires_at=expires_at.isoformat()
    )


@app.delete("/sessions/{session_id}/upload-image")
async def clear_pending_upload(session_id: str, current_user: dict = Depends(get_current_user)) -> Dict:
    """Clear pending upload for a session."""
    from pathlib import Path

    if session_id in pending_uploads:
        upload = pending_uploads.pop(session_id)
        # Delete temp file
        try:
            Path(upload["path"]).unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"Failed to delete temp upload file: {e}")
        return {"success": True}
    return {"success": False, "error": "No pending upload"}


@app.get("/sessions/{session_id}/pending-upload")
async def get_pending_upload(session_id: str, current_user: dict = Depends(get_current_user)) -> Dict:
    """Check if session has a pending upload."""
    if session_id in pending_uploads:
        return {"has_pending": True, **pending_uploads[session_id]}
    return {"has_pending": False}


@app.post("/sessions/{session_id}/prepare-file-upload")
async def prepare_file_upload(session_id: str, current_user: dict = Depends(get_current_user)) -> Dict:
    """
    Prepare the interactive session to use the pending upload.
    Call this before the user clicks a file input on the page.
    """
    manager = get_browser_manager()

    if manager.session_id != session_id or manager.platform != "facebook":
        raise HTTPException(404, "Interactive session not found or not active")

    if session_id not in pending_uploads:
        raise HTTPException(400, "No pending upload for this session")

    upload = pending_uploads[session_id]

    # Set the file on the browser manager for interception
    manager.set_pending_file(upload["path"])

    return {
        "success": True,
        "message": "File ready. Click the upload button on the page to use it.",
        "filename": upload["filename"]
    }


# Cleanup task for expired uploads
async def cleanup_expired_uploads():
    """Background task to clean up expired uploads."""
    from pathlib import Path

    while True:
        await asyncio.sleep(300)  # Run every 5 minutes

        now = datetime.now()
        expired = []

        for session_id, upload in pending_uploads.items():
            expires_at = datetime.fromisoformat(upload["expires_at"])
            if now > expires_at:
                expired.append(session_id)

        for session_id in expired:
            upload = pending_uploads.pop(session_id, None)
            if upload:
                try:
                    Path(upload["path"]).unlink(missing_ok=True)
                    logger.info(f"Cleaned up expired upload: {upload['image_id']}")
                except Exception as e:
                    logger.warning(f"Failed to clean up expired upload: {e}")


# ===========================================================================
# TEMPORARY TEST ENDPOINT - Dialog Navigation Testing
# Remove after testing is complete
# ===========================================================================

class DialogTestRequest(BaseModel):
    """Request model for dialog navigation test."""
    profile_name: str = "anna_pelfrey"
    max_steps: int = 10
    navigate_to_feed: bool = True


@app.post("/test-dialog-navigation")
async def test_dialog_navigation(
    request: DialogTestRequest,
    current_user: dict = Depends(get_current_user)
) -> Dict:
    """
    TEMPORARY TEST ENDPOINT - Remove after testing.

    Tests adaptive dialog navigation using Gemini Vision.
    Uses the SAME setup as post_comment_verified() for consistency.
    """
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth
    from fb_session import FacebookSession, apply_session_to_context
    from gemini_vision import get_vision_client, set_observation_context
    from comment_bot import save_debug_screenshot, _build_playwright_proxy
    from google.genai import types
    import re

    results = {
        "profile_name": request.profile_name,
        "steps": [],
        "screenshots": [],
        "errors": [],
        "final_status": "unknown"
    }

    # Load session
    session = FacebookSession(request.profile_name)
    if not session.load():
        return {"error": f"Failed to load session for {request.profile_name}"}

    # Get vision client
    vision = get_vision_client()
    if not vision:
        return {"error": "Vision client not available"}

    # Set context for Gemini logging
    set_observation_context(profile_name=request.profile_name, campaign_id="dialog_test")

    logger.info(f"[DIALOG-TEST] Starting test for {request.profile_name}")

    async with async_playwright() as p:
        # Build context options - SAME as post_comment_verified()
        fingerprint = session.get_device_fingerprint()
        context_options = {
            "user_agent": session.get_user_agent(),
            "viewport": session.get_viewport() or {"width": 393, "height": 873},
            "ignore_https_errors": True,
            "device_scale_factor": 1,
            "timezone_id": fingerprint["timezone"],
            "locale": fingerprint["locale"],
        }

        # Add system proxy (mandatory)
        proxy = get_system_proxy()
        if not proxy:
            raise Exception("No proxy available — cannot launch browser without proxy")
        context_options["proxy"] = _build_playwright_proxy(proxy)
        logger.info(f"[DIALOG-TEST] Using proxy: {proxy[:30]}...")

        # Launch browser
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-notifications", "--disable-geolocation"]
        )
        context = await browser.new_context(**context_options)

        # Apply stealth
        await Stealth().apply_stealth_async(context)

        # Create page and apply session cookies
        page = await context.new_page()
        await apply_session_to_context(context, session)

        try:
            # Step 1: Navigate to Facebook
            if request.navigate_to_feed:
                logger.info("[DIALOG-TEST] Navigating to Facebook feed...")
                await page.goto("https://m.facebook.com", wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(3)

                screenshot_path = await save_debug_screenshot(page, "dialog_test_initial")
                results["screenshots"].append(screenshot_path)
                results["steps"].append({
                    "step": 0,
                    "action": "navigate_to_feed",
                    "url": page.url,
                    "screenshot": screenshot_path
                })
                logger.info(f"[DIALOG-TEST] Initial URL: {page.url}")

            # Step 2: Adaptive dialog navigation loop
            for step_num in range(1, request.max_steps + 1):
                logger.info(f"[DIALOG-TEST] Step {step_num}/{request.max_steps}")

                # Take screenshot
                screenshot_path = await save_debug_screenshot(page, f"dialog_test_step_{step_num}")
                results["screenshots"].append(screenshot_path)

                # Read screenshot
                with open(screenshot_path, "rb") as f:
                    image_data = f.read()

                # Ask Gemini to analyze the screenshot
                prompt = """Analyze this Facebook mobile screenshot (393x873 pixels).

Is there a dialog/popup/modal/notice visible? Look for:
- "We removed your comment" notice
- Survey or questionnaire dialogs
- "See why", "OK", "Done", "Continue", "Close", "Got it" buttons
- Radio buttons asking "Why did you comment this?"
- Any overlay that blocks the main content
- Cookie consent or privacy notices

If you see a DIALOG/POPUP/MODAL:
1. Identify what kind of dialog it is
2. Find the best button to dismiss it (usually "OK", "Done", "Continue", "Close", "Got it", etc.)
3. Provide the CENTER coordinates (x, y) of that button

Response format if dialog found:
DIALOG_FOUND type="<dialog type>" button="<button text>" x=<center_x> y=<center_y>

Response format if NO dialog (just normal Facebook feed/page):
NO_DIALOG page_type="<feed/profile/post/login/other>"

Response format if you see a dialog but can't determine what to click:
UNCLEAR reason=<explanation>

IMPORTANT: Coordinates must be within 0-393 for x and 0-873 for y."""

                image_part = types.Part.from_bytes(data=image_data, mime_type="image/png")

                try:
                    response = await asyncio.to_thread(
                        vision.client.models.generate_content,
                        model=vision.model,
                        contents=[prompt, image_part]
                    )
                    result_text = response.text.strip()
                    logger.info(f"[DIALOG-TEST] Gemini response: {result_text}")
                except Exception as e:
                    logger.error(f"[DIALOG-TEST] Gemini API error: {e}")
                    results["errors"].append(f"Step {step_num}: Gemini API error - {e}")
                    continue

                step_result = {
                    "step": step_num,
                    "gemini_response": result_text,
                    "screenshot": screenshot_path,
                    "action_taken": None
                }

                # Parse Gemini response
                if "NO_DIALOG" in result_text.upper():
                    step_result["action_taken"] = "no_dialog_detected"
                    results["steps"].append(step_result)
                    results["final_status"] = "success_no_dialogs"
                    logger.info("[DIALOG-TEST] No dialog detected - test complete")
                    break

                if "DIALOG_FOUND" in result_text.upper():
                    # Extract coordinates using regex
                    x_match = re.search(r'x=(\d+)', result_text)
                    y_match = re.search(r'y=(\d+)', result_text)
                    button_match = re.search(r'button="([^"]+)"', result_text)
                    type_match = re.search(r'type="([^"]+)"', result_text)

                    if x_match and y_match:
                        x = int(x_match.group(1))
                        y = int(y_match.group(1))
                        button_text = button_match.group(1) if button_match else "unknown"
                        dialog_type = type_match.group(1) if type_match else "unknown"

                        # Validate coordinates
                        if 0 <= x <= 393 and 0 <= y <= 873:
                            logger.info(f"[DIALOG-TEST] Clicking '{button_text}' at ({x}, {y})")
                            await page.mouse.click(x, y)
                            await asyncio.sleep(2)

                            step_result["action_taken"] = f"clicked_{button_text}_at_{x}_{y}"
                            step_result["dialog_type"] = dialog_type
                            results["steps"].append(step_result)
                            continue
                        else:
                            logger.warning(f"[DIALOG-TEST] Invalid coordinates: ({x}, {y})")
                            step_result["action_taken"] = f"invalid_coordinates_{x}_{y}"
                    else:
                        step_result["action_taken"] = "could_not_parse_coordinates"

                    results["steps"].append(step_result)

                    # Try CSS selector fallback for common buttons
                    fallback_selectors = [
                        '[aria-label="OK"]',
                        '[aria-label="Done"]',
                        '[aria-label="Close"]',
                        '[aria-label="Got it"]',
                        '[aria-label="Continue"]',
                        'button:has-text("OK")',
                        'button:has-text("Done")',
                        'button:has-text("Close")',
                        'div[role="button"]:has-text("OK")',
                        'div[role="button"]:has-text("Done")',
                    ]

                    for selector in fallback_selectors:
                        try:
                            if await page.locator(selector).count() > 0:
                                logger.info(f"[DIALOG-TEST] CSS fallback: clicking {selector}")
                                await page.locator(selector).first.click()
                                await asyncio.sleep(2)
                                results["steps"][-1]["action_taken"] = f"css_fallback_{selector}"
                                break
                        except Exception:
                            pass
                    continue

                if "UNCLEAR" in result_text.upper():
                    step_result["action_taken"] = "unclear_gemini_response"
                    results["steps"].append(step_result)
                    results["errors"].append(f"Step {step_num}: Gemini unclear - {result_text}")
                    continue

                # Unknown response format
                step_result["action_taken"] = "unknown_response_format"
                results["steps"].append(step_result)
            else:
                # Loop completed without finding end
                results["final_status"] = "max_steps_reached"

            # Take final screenshot
            final_screenshot = await save_debug_screenshot(page, "dialog_test_final")
            results["screenshots"].append(final_screenshot)
            results["final_url"] = page.url

        except Exception as e:
            logger.error(f"[DIALOG-TEST] Error: {e}")
            results["errors"].append(str(e))
            results["final_status"] = "error"
        finally:
            await browser.close()

    logger.info(f"[DIALOG-TEST] Test complete: {results['final_status']}")
    return results


# ===========================================================================
# Adaptive Agent Endpoint
# Uses the AdaptiveAgent module for DOM-based Facebook automation
# ===========================================================================

from adaptive_agent import run_adaptive_task
from workflows import (
    update_profile_photo,
    batch_update_profile_photos,
    regenerate_profile_photo_with_pose,
    batch_regenerate_imported_photos
)
from gemini_image_gen import POSE_VARIATIONS


class AdaptiveAgentRequest(BaseModel):
    """Request model for adaptive agent."""
    profile_name: str
    task: str
    max_steps: int = 15
    start_url: str = "https://m.facebook.com"


class ProfilePhotoRequest(BaseModel):
    """Request model for profile photo update workflow."""
    profile_name: str
    persona_description: str


class RegeneratePhotoRequest(BaseModel):
    """Request model for profile photo regeneration with pose."""
    profile_name: str
    pose_name: Optional[str] = None  # If None, picks random pose


class BatchPhotoRequest(BaseModel):
    """Request model for batch profile photo generation."""
    profiles: List[Dict[str, str]]  # [{"profile_name": "...", "persona_description": "..."}]


@app.post("/adaptive-agent")
async def adaptive_agent_endpoint(
    request: AdaptiveAgentRequest,
    api_key: str = Header(None, alias="X-API-Key")
) -> Dict:
    """
    DOM-Based Adaptive Agent - Gemini decides WHAT, Playwright finds WHERE.

    Uses the AdaptiveAgent module for Facebook automation tasks like:
    - Submitting restriction appeals
    - Navigating and interacting with pages
    - Performing multi-step tasks
    """
    # Verify API key
    if not api_key or not CLAUDE_API_KEY or api_key != CLAUDE_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    logger.info(f"[ADAPTIVE] Starting task for {request.profile_name}: {request.task}")

    result = await run_adaptive_task(
        profile_name=request.profile_name,
        task=request.task,
        max_steps=request.max_steps,
        start_url=request.start_url,
        forensic_context={
            "platform": "facebook",
            "engine": "adaptive_agent_endpoint",
            "run_id": f"adaptive_endpoint:{request.profile_name}",
        },
    )

    return result


# ===========================================================================
# Appeal Endpoints
# Batch and single restriction appeal management
# ===========================================================================

class BatchAppealRequest(BaseModel):
    max_attempts: int = 3
    retry_failed: bool = True

class AppealSingleRequest(BaseModel):
    profile_name: str


@app.post("/appeals/batch")
async def batch_appeal_endpoint(
    request: BatchAppealRequest = BatchAppealRequest(),
    api_key: str = Header(None, alias="X-API-Key")
) -> Dict:
    """Appeal ALL restricted profiles. Runs concurrently with retries."""
    if not api_key or not CLAUDE_API_KEY or api_key != CLAUDE_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    from appeal_manager import batch_appeal_all
    return await batch_appeal_all(
        max_attempts=request.max_attempts,
        retry_failed=request.retry_failed
    )


@app.post("/appeals/single")
async def appeal_single_endpoint(
    request: AppealSingleRequest,
    current_user: dict = Depends(get_current_user)
) -> Dict:
    """Appeal a single restricted profile."""
    from appeal_manager import appeal_single_profile, get_profile_busy_reason
    busy_reason = get_profile_busy_reason(request.profile_name)
    if busy_reason:
        raise HTTPException(
            status_code=409,
            detail={
                "status": "busy",
                "profile_name": request.profile_name,
                "message": f"Profile is currently {busy_reason.replace('_', ' ')}",
                "reason": busy_reason,
            },
        )
    return await appeal_single_profile(request.profile_name)


@app.get("/appeals/status")
async def appeal_status_endpoint(
    current_user: dict = Depends(get_current_user)
) -> Dict:
    """Get appeal status for all restricted/appealed profiles (for frontend)."""
    from profile_manager import get_profile_manager
    pm = get_profile_manager()
    profiles = []
    for name, state in pm.get_all_profiles().items():
        appeal_status = state.get("appeal_status", "none")
        if state.get("status") == "restricted" or appeal_status not in ("none", None):
            profiles.append({
                "profile_name": name,
                "status": state.get("status"),
                "appeal_status": appeal_status,
                "appeal_attempts": state.get("appeal_attempts", 0),
                "appeal_last_attempt_at": state.get("appeal_last_attempt_at"),
                "appeal_last_result": state.get("appeal_last_result"),
                "appeal_last_error": state.get("appeal_last_error"),
                "restriction_reason": state.get("restriction_reason"),
                "restriction_expires_at": state.get("restriction_expires_at"),
            })
    return {"profiles": profiles, "total": len(profiles)}


class VerifyProfileRequest(BaseModel):
    profile_name: str


@app.post("/appeals/verify")
async def verify_single_endpoint(
    request: VerifyProfileRequest,
    current_user: dict = Depends(get_current_user)
) -> Dict:
    """Verify if a profile's restriction is still active on Facebook. Auto-unblocks if resolved."""
    from appeal_manager import verify_single_profile, get_profile_busy_reason
    busy_reason = get_profile_busy_reason(request.profile_name)
    if busy_reason:
        raise HTTPException(
            status_code=409,
            detail={
                "status": "busy",
                "profile_name": request.profile_name,
                "message": f"Profile is currently {busy_reason.replace('_', ' ')}",
                "reason": busy_reason,
            },
        )
    return await verify_single_profile(request.profile_name)


@app.post("/appeals/verify-all")
async def verify_all_endpoint(
    api_key: str = Header(None, alias="X-API-Key")
) -> Dict:
    """Verify ALL restricted profiles. Auto-unblocks resolved ones."""
    if not api_key or not CLAUDE_API_KEY or api_key != CLAUDE_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    from appeal_manager import verify_all_restricted
    return await verify_all_restricted()


# ===========================================================================
# Workflow Endpoints
# High-level workflows combining multiple capabilities
# ===========================================================================

@app.post("/workflow/update-profile-photo")
async def workflow_update_profile_photo(
    request: ProfilePhotoRequest,
    api_key: str = Header(None, alias="X-API-Key")
) -> Dict:
    """
    Generate AI profile photo and upload to Facebook.

    This workflow:
    1. Generates a hyper-realistic AI selfie using Gemini 2.5 Flash Image
    2. Uses the Adaptive Agent to navigate Facebook and upload the photo
    3. Returns results from both steps

    Example:
        POST /workflow/update-profile-photo
        {
            "profile_name": "Priscilla Hicks",
            "persona_description": "friendly middle-aged white woman with light brown hair"
        }
    """
    # Verify API key
    if not api_key or not CLAUDE_API_KEY or api_key != CLAUDE_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    logger.info(f"[WORKFLOW] Starting profile photo update for {request.profile_name}")
    logger.info(f"[WORKFLOW] Persona: {request.persona_description}")

    result = await update_profile_photo(
        profile_name=request.profile_name,
        persona_description=request.persona_description
    )

    return result


@app.post("/workflow/regenerate-profile-photo")
async def workflow_regenerate_profile_photo(
    request: RegeneratePhotoRequest,
    api_key: str = Header(None, alias="X-API-Key")
) -> Dict:
    """
    Regenerate profile photo using existing face as reference.
    Creates a new photo of the same person in a different pose/setting.

    This workflow:
    1. Loads current profile picture from session (as identity reference)
    2. Generates new image with same person in new pose using Gemini
    3. Uploads the new photo to Facebook via Adaptive Agent
    4. Updates the session file with new profile picture

    Args:
        profile_name: The profile to update (must have existing profile_picture)
        pose_name: Specific pose (e.g., "beach", "gym_mirror"). Random if not specified.

    Available poses:
        beach, gym_mirror, coffee_shop, car, kitchen, living_room,
        outdoor_walk, with_pet, restaurant, bathroom_mirror, hiking, pool_backyard

    Example:
        POST /workflow/regenerate-profile-photo
        {
            "profile_name": "adele_compton",
            "pose_name": "beach"
        }
    """
    # Verify API key
    if not api_key or not CLAUDE_API_KEY or api_key != CLAUDE_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    logger.info(f"[WORKFLOW] Starting photo regeneration for {request.profile_name}")
    if request.pose_name:
        logger.info(f"[WORKFLOW] Requested pose: {request.pose_name}")

    result = await regenerate_profile_photo_with_pose(
        profile_name=request.profile_name,
        pose_name=request.pose_name
    )

    return result


@app.get("/workflow/available-poses")
async def get_available_poses(
    api_key: str = Header(None, alias="X-API-Key")
) -> Dict:
    """
    Get list of available poses for profile photo regeneration.
    """
    # Verify API key
    if not api_key or not CLAUDE_API_KEY or api_key != CLAUDE_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return {
        "poses": [
            {"name": p["name"], "description": p["prompt"][:80] + "..."}
            for p in POSE_VARIATIONS
        ],
        "total": len(POSE_VARIATIONS)
    }


@app.post("/workflow/regenerate-all-imported-photos")
async def workflow_regenerate_all_imported_photos(
    api_key: str = Header(None, alias="X-API-Key")
) -> Dict:
    """
    Regenerate profile photos for all profiles with 'imported' tag.
    Each profile gets a random unique pose from the variations pool.

    This is a long-running operation - may take several minutes for many profiles.

    Returns:
        Dict with:
            - total: Number of profiles processed
            - successful: Number of successful regenerations
            - failed: Number of failed regenerations
            - results: Detailed results for each profile
    """
    # Verify API key
    if not api_key or not CLAUDE_API_KEY or api_key != CLAUDE_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    logger.info(f"[WORKFLOW] Starting batch photo regeneration for all imported profiles")

    result = await batch_regenerate_imported_photos()

    return result


@app.post("/workflow/batch-generate-photos")
async def workflow_batch_generate_photos(
    request: BatchPhotoRequest,
    api_key: str = Header(None, alias="X-API-Key")
) -> Dict:
    """
    Batch generate AI profile photos and upload to Facebook.
    Processes sequentially. Each entry needs profile_name + persona_description.
    """
    if not api_key or not CLAUDE_API_KEY or api_key != CLAUDE_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    logger.info(f"[WORKFLOW] Starting batch photo generation for {len(request.profiles)} profiles")
    result = await batch_update_profile_photos(request.profiles)
    return result


@app.on_event("startup")
async def startup_event():
    """Start background tasks on app startup."""
    asyncio.create_task(cleanup_expired_uploads())
    # Start queue processor for background campaign processing
    await queue_processor.start()
    logger.info("Queue processor started on startup")
    # Start appeal scheduler
    from appeal_scheduler import get_appeal_scheduler
    scheduler = get_appeal_scheduler()
    await scheduler.start()
    logger.info("Appeal scheduler started on startup")
    # Start premium automation scheduler
    await premium_scheduler.start()
    logger.info("Premium scheduler started on startup")
    if reddit_mission_scheduler:
        await reddit_mission_scheduler.start()
        logger.info("Reddit mission scheduler started on startup")
    await reddit_program_scheduler.start()
    logger.info("Reddit program scheduler started on startup")


@app.on_event("shutdown")
async def shutdown_event():
    """Gracefully stop background tasks on shutdown."""
    await queue_processor.stop()
    logger.info("Queue processor stopped on shutdown")
    from appeal_scheduler import get_appeal_scheduler
    scheduler = get_appeal_scheduler()
    await scheduler.stop()
    logger.info("Appeal scheduler stopped on shutdown")
    await premium_scheduler.stop()
    logger.info("Premium scheduler stopped on shutdown")
    if reddit_mission_scheduler:
        await reddit_mission_scheduler.stop()
        logger.info("Reddit mission scheduler stopped on shutdown")
    await reddit_program_scheduler.stop()
    logger.info("Reddit program scheduler stopped on shutdown")


# =========================================================================
# Appeal Scheduler Endpoints
# =========================================================================

@app.get("/appeals/scheduler/status")
async def get_appeal_scheduler_status(current_user: dict = Depends(get_current_user)):
    """Get current appeal scheduler state."""
    from appeal_scheduler import get_appeal_scheduler
    return get_appeal_scheduler().get_status()


@app.post("/appeals/scheduler/run-now")
async def run_appeal_scheduler_now(current_user: dict = Depends(get_current_user)):
    """Manually trigger appeal scheduler run."""
    from appeal_scheduler import get_appeal_scheduler
    scheduler = get_appeal_scheduler()
    result = await scheduler.run_now()
    return result


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
