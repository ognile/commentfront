"""
Job Queue Manager

Manages the queue of Facebook comment jobs.
Uses GeeLark Cloud Phones as the primary automation method.
Falls back to AdsPower/Playwright if GeeLark is unavailable.
"""

import asyncio
import logging
import os
from typing import List, Dict, Optional
from uuid import uuid4
from pydantic import BaseModel

# GeeLark (Primary)
from geelark_client import GeeLarkClient, GeeLarkTask

# Legacy fallback (AdsPower + Playwright)
from adspower import AdsPowerClient
from automation import run_automation_task, run_with_session
from credentials import CredentialManager
from fb_session import FacebookSession


# Data Models
class Job(BaseModel):
    id: str
    profileId: str  # Can be either GeeLark device ID or AdsPower profile ID
    profileName: str
    comment: str
    status: str = "pending"  # pending, running, success, failed
    proxyIP: Optional[str] = "N/A"
    error: Optional[str] = None
    geelark_task_id: Optional[str] = None  # Track GeeLark task ID


class JobManager:
    """
    Manages job queue processing.

    Primary: GeeLark Cloud Phones (recommended)
    Fallback: AdsPower + Playwright (legacy)
    """

    def __init__(self):
        self.jobs: List[Job] = []
        self.is_processing = False
        self.logger = logging.getLogger("JobManager")

        # Initialize GeeLark client (primary)
        self.geelark: Optional[GeeLarkClient] = None
        self.geelark_enabled = False
        self._init_geelark()

        # Initialize legacy clients (fallback)
        self.adspower = AdsPowerClient()
        self.credentials_manager = CredentialManager()

    def _init_geelark(self):
        """Initialize GeeLark client if credentials are available."""
        try:
            if os.getenv("GEELARK_BEARER_TOKEN"):
                self.geelark = GeeLarkClient()
                # Test connection
                if self.geelark.test_connection():
                    self.geelark_enabled = True
                    self.logger.info("✓ GeeLark client initialized and connected")
                else:
                    self.logger.warning("GeeLark credentials found but connection failed")
                    self.geelark_enabled = False
            else:
                self.logger.info("GeeLark not configured (no GEELARK_BEARER_TOKEN)")
        except Exception as e:
            self.logger.warning(f"Failed to initialize GeeLark: {e}")
            self.geelark_enabled = False

    def add_job(self, profile_id: str, profile_name: str, comment: str) -> Job:
        """Add a new job to the queue."""
        job = Job(
            id=str(uuid4()),
            profileId=profile_id,
            profileName=profile_name,
            comment=comment
        )
        self.jobs.append(job)
        return job

    def clear_jobs(self):
        """Clear all jobs from the queue."""
        self.jobs = []
        self.is_processing = False

    def get_jobs(self) -> List[Job]:
        """Get all jobs in the queue."""
        return self.jobs

    async def start_processing(self, target_url: str):
        """Start processing the job queue."""
        if self.is_processing:
            return

        self.is_processing = True
        asyncio.create_task(self._process_queue(target_url))

    async def _process_queue(self, target_url: str):
        """
        Process all jobs in the queue.

        Priority:
        1. GeeLark (if enabled and device available)
        2. Saved session (fast path)
        3. AdsPower/Playwright (slow path)
        """
        self.logger.info("Starting queue processing...")
        self.logger.info(f"GeeLark enabled: {self.geelark_enabled}")

        for job in self.jobs:
            if job.status != "pending":
                continue

            job.status = "running"
            self.logger.info(f"Processing Job {job.id} for Profile {job.profileId} ({job.profileName})")

            success = False

            try:
                # ============================================
                # PATH 0: RAILWAY / DIRECT CLOUD (PRIORITY)
                # ============================================
                # If we have a global proxy set in env (Railway mode), use it directly
                # ignoring GeeLark/AdsPower
                global_proxy = os.getenv("PROXY_URL")
                if global_proxy:
                    self.logger.info(f"[RAILWAY] Using global proxy: {global_proxy}")
                    # We need a session/cookies for this profile
                    # Ideally, we load them from the 'sessions' folder which we uploaded
                    session = FacebookSession(job.profileName)
                    
                    if not session.load():
                        # If no session, we can't login on a headless cloud container effortlessly
                        # unless we have user/pass and 2FA logic built-in.
                        # For now, let's assume session exists or fail.
                        raise Exception(f"No session file found for {job.profileName}. Cannot run on Railway without cookies.")
                        
                    # Inject the global proxy into the session if it doesn't have one
                    if not session.data.get("proxy"):
                        session.data["proxy"] = global_proxy
                        
                    await run_with_session(session, target_url, job.comment, job_id=job.id)
                    success = True
                    
                # ============================================
                # PATH 1: GeeLark Cloud Phone (RECOMMENDED)
                # ============================================
                elif self.geelark_enabled and self.geelark:
                    success = await self._process_with_geelark(job, target_url)

                # ============================================
                # PATH 2: Saved Session (FAST FALLBACK)
                # ============================================
                if not success:
                    success = await self._process_with_session(job, target_url)

                # ============================================
                # PATH 3: AdsPower/Playwright (SLOW FALLBACK)
                # ============================================
                if not success:
                    success = await self._process_with_adspower(job, target_url)

                # Update status
                if success:
                    job.status = "success"
                else:
                    job.status = "failed"
                    if not job.error:
                        job.error = "All automation methods failed"

            except Exception as e:
                self.logger.error(f"Job {job.id} failed: {e}")
                job.status = "failed"
                job.error = str(e)

            # Wait between jobs to be safe
            await asyncio.sleep(2)

        self.is_processing = False
        self.logger.info("Queue processing finished.")

    async def _process_with_geelark(self, job: Job, target_url: str) -> bool:
        """
        Process job using GeeLark Cloud Phone.

        Returns True if successful, False otherwise.
        """
        self.logger.info(f"[GEELARK] Attempting to process with GeeLark...")

        try:
            # Get available devices
            devices = self.geelark.list_devices()

            if not devices:
                self.logger.warning("[GEELARK] No cloud phones available")
                return False

            # Find matching device
            # Try to match by profile name or use first available device
            device = None
            for d in devices:
                if d.name == job.profileName or d.id == job.profileId:
                    device = d
                    break

            # Use first available if no match
            if not device:
                device = devices[0]
                self.logger.info(f"[GEELARK] No exact match, using device: {device.name}")

            self.logger.info(f"[GEELARK] Using device: {device.name} (ID: {device.id})")

            # Create comment task
            task_id = self.geelark.create_facebook_comment_task(
                device_id=device.id,
                post_url=target_url,
                comments=[job.comment],
                name=f"Job {job.id[:8]}",
            )

            job.geelark_task_id = task_id
            self.logger.info(f"[GEELARK] Created task: {task_id}")

            # Wait for completion (with timeout)
            # Real-world tasks can take 4+ minutes, so we set timeout to 5 minutes (300s)
            task = self.geelark.wait_for_task(task_id, timeout=300)

            if task.is_completed:
                self.logger.info(f"[GEELARK] ✅ Task completed successfully!")
                return True
            else:
                self.logger.warning(f"[GEELARK] Task failed with status: {task.status_name}")
                job.error = f"GeeLark task failed: {task.status_name}"
                return False

        except Exception as e:
            self.logger.warning(f"[GEELARK] Failed: {e}")
            job.error = f"GeeLark error: {str(e)}"
            return False

    async def _process_with_session(self, job: Job, target_url: str) -> bool:
        """
        Process job using saved session (Playwright without AdsPower).

        Returns True if successful, False otherwise.
        """
        self.logger.info(f"[SESSION] Attempting to process with saved session...")

        try:
            session = FacebookSession(job.profileName)
            if session.load() and session.has_valid_cookies():
                self.logger.info(f"[SESSION] Found saved session for {job.profileName}")
                await run_with_session(session, target_url, job.comment, job_id=job.id)
                self.logger.info(f"[SESSION] ✅ Job completed using saved session!")
                return True
            else:
                self.logger.info(f"[SESSION] No valid session found for {job.profileName}")
                return False

        except Exception as e:
            self.logger.warning(f"[SESSION] Failed: {e}")
            return False

    async def _process_with_adspower(self, job: Job, target_url: str) -> bool:
        """
        Process job using AdsPower + Playwright (legacy method).

        Returns True if successful, False otherwise.
        """
        self.logger.info(f"[ADSPOWER] Attempting to process with AdsPower...")

        try:
            # 1. Launch AdsPower Profile
            launch_data = self.adspower.start_profile(job.profileId)
            ws_endpoint = launch_data["ws_endpoint"]
            is_mock = launch_data["mock"]

            # 2. Get Credentials for Login (if needed)
            creds = self.credentials_manager.get_credential(job.profileName)
            if not creds:
                self.logger.warning(f"[ADSPOWER] No credentials found for {job.profileName}")

            # 3. Run Playwright Automation
            await run_automation_task(
                ws_endpoint=ws_endpoint,
                url=target_url,
                comment=job.comment,
                is_mock=is_mock,
                credentials=creds
            )

            self.logger.info(f"[ADSPOWER] ✅ Job completed!")
            return True

        except Exception as e:
            self.logger.warning(f"[ADSPOWER] Failed: {e}")
            job.error = f"AdsPower error: {str(e)}"
            return False

        finally:
            # Cleanup
            try:
                self.adspower.stop_profile(job.profileId)
            except Exception:
                pass


# ============================================
# GEELARK-ONLY JOB MANAGER (SIMPLIFIED)
# ============================================

class GeeLarkJobManager:
    """
    Simplified job manager that ONLY uses GeeLark.
    No fallback to AdsPower/Playwright.
    """

    def __init__(self):
        self.jobs: List[Job] = []
        self.is_processing = False
        self.logger = logging.getLogger("GeeLarkJobManager")
        self.geelark = GeeLarkClient()

    def add_job(self, device_id: str, device_name: str, comment: str) -> Job:
        """Add a new job to the queue."""
        job = Job(
            id=str(uuid4()),
            profileId=device_id,
            profileName=device_name,
            comment=comment
        )
        self.jobs.append(job)
        return job

    def clear_jobs(self):
        """Clear all jobs from the queue."""
        self.jobs = []
        self.is_processing = False

    def get_jobs(self) -> List[Job]:
        """Get all jobs in the queue."""
        return self.jobs

    async def start_processing(self, target_url: str):
        """Start processing the job queue."""
        if self.is_processing:
            return

        self.is_processing = True
        asyncio.create_task(self._process_queue(target_url))

    async def _process_queue(self, target_url: str):
        """Process all jobs using GeeLark."""
        self.logger.info("Starting GeeLark queue processing...")

        for job in self.jobs:
            if job.status != "pending":
                continue

            job.status = "running"
            self.logger.info(f"Processing Job {job.id} on device {job.profileId}")

            try:
                # Create comment task
                task_id = self.geelark.create_facebook_comment_task(
                    device_id=job.profileId,
                    post_url=target_url,
                    comments=[job.comment],
                    name=f"Job {job.id[:8]}",
                )

                job.geelark_task_id = task_id
                self.logger.info(f"Created GeeLark task: {task_id}")

                # Wait for completion
                task = self.geelark.wait_for_task(task_id, timeout=120)

                if task.is_completed:
                    job.status = "success"
                    self.logger.info(f"✅ Job {job.id} completed!")
                else:
                    job.status = "failed"
                    job.error = f"Task failed: {task.status_name}"
                    self.logger.error(f"Job {job.id} failed: {task.status_name}")

            except Exception as e:
                self.logger.error(f"Job {job.id} failed: {e}")
                job.status = "failed"
                job.error = str(e)

            # Wait between jobs
            await asyncio.sleep(2)

        self.is_processing = False
        self.logger.info("Queue processing finished.")
