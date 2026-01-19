"""
CommentBot API - Streamlined Facebook Comment Automation
"""

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File, Depends, status, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer, APIKeyHeader
from pydantic import BaseModel
from typing import List, Optional, Dict, Set

# Authentication imports
from auth import create_access_token, create_refresh_token, decode_token, verify_password
from users import user_manager

# Maximum concurrent browser sessions for campaigns
MAX_CONCURRENT = 5
import logging
import os

# API Key for programmatic access (Claude testing, CI/CD, etc.)
# Set via CLAUDE_API_KEY environment variable
CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY")
import asyncio
import json
import random
from datetime import datetime, timedelta
import nest_asyncio

# Patch asyncio to allow nested event loops (crucial for Playwright in FastAPI)
nest_asyncio.apply()

from comment_bot import post_comment, post_comment_verified, test_session, MOBILE_VIEWPORT, DEFAULT_USER_AGENT
from fb_session import FacebookSession, list_saved_sessions
from credentials import CredentialManager
from proxy_manager import ProxyManager
from queue_manager import CampaignQueueManager
from login_bot import create_session_from_credentials, refresh_session_profile_name
from browser_manager import get_browser_manager, UPLOAD_DIR

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


def get_effective_proxy() -> Optional[str]:
    """
    Get the effective system proxy.

    Resolution order:
    1. User-set default proxy (from proxies.json with is_default=True)
    2. PROXY_URL environment variable (fallback)

    Returns:
        Proxy URL string or None if no proxy configured
    """
    # Check for user-set default proxy first (proxy_manager initialized below)
    try:
        default_proxy = proxy_manager.get_default_proxy()
        if default_proxy:
            return default_proxy.get("url")
    except NameError:
        # proxy_manager not yet initialized during startup
        pass

    # Fall back to environment variable
    return PROXY_URL if PROXY_URL else None

# Initialize credential manager
credential_manager = CredentialManager()

# Initialize proxy manager
proxy_manager = ProxyManager()

# Initialize campaign queue manager
queue_manager = CampaignQueueManager()


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
        self._task: Optional[asyncio.Task] = None
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
        self._task = asyncio.create_task(self._process_loop())
        self.logger.info("Queue processor started")

    async def stop(self):
        """Gracefully stop the processor."""
        self._stop_requested = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.is_running = False
        self.queue_manager.set_processor_running(False)
        self.logger.info("Queue processor stopped")

    def cancel_current_campaign(self):
        """Signal that the current campaign should be cancelled."""
        self._current_campaign_cancelled = True

    async def _process_loop(self):
        """Main processing loop - runs continuously."""
        while not self._stop_requested:
            try:
                campaign = self.queue_manager.get_next_pending()

                if campaign:
                    self._current_campaign_cancelled = False
                    await self._process_campaign(campaign)
                else:
                    # No campaigns, wait before checking again
                    await asyncio.sleep(2)

            except asyncio.CancelledError:
                self.logger.info("Processor loop cancelled")
                break
            except Exception as e:
                self.logger.error(f"Processor loop error: {e}")
                await asyncio.sleep(5)  # Wait before retrying

    async def _process_campaign(self, campaign: dict):
        """Process a single campaign."""
        campaign_id = campaign["id"]
        url = campaign["url"]
        comments = campaign["comments"]
        duration_minutes = campaign["duration_minutes"]
        filter_tags = campaign.get("filter_tags", [])
        enable_warmup = campaign.get("enable_warmup", False)

        try:
            # Mark as processing
            self.queue_manager.set_processing(campaign_id)
            await broadcast_update("queue_campaign_start", {
                "campaign_id": campaign_id,
                "url": url,
                "total_comments": len(comments)
            })

            # Use UNIFIED profile selection (handles cookies, tags, restrictions, LRU)
            from profile_manager import get_profile_manager
            profile_manager = get_profile_manager()

            assigned_profiles = profile_manager.get_eligible_profiles(
                filter_tags=filter_tags if filter_tags else None,
                count=len(comments)
            )

            if not assigned_profiles:
                error_msg = f"No eligible profiles match tags: {filter_tags}" if filter_tags else "No eligible profiles available"
                self.queue_manager.set_failed(campaign_id, error_msg)
                await broadcast_update("queue_campaign_failed", {
                    "campaign_id": campaign_id,
                    "error": error_msg
                })
                return

            self.logger.info(f"Profile selection (LRU by success): {assigned_profiles}")

            if len(assigned_profiles) < len(comments):
                self.logger.warning(f"Only {len(assigned_profiles)} profiles available for {len(comments)} comments")

            # Calculate timing
            total_jobs = min(len(comments), len(assigned_profiles))
            duration_seconds = duration_minutes * 60
            base_delay = duration_seconds / total_jobs if total_jobs > 1 else 0

            # Broadcast campaign start
            await broadcast_update("campaign_start", {
                "url": url,
                "total_jobs": total_jobs,
                "duration_minutes": duration_minutes
            })

            # Process jobs
            results = []

            for job_idx, (profile_name, comment) in enumerate(zip(assigned_profiles, comments[:total_jobs])):
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

                # Staggered delay (except first job)
                if job_idx > 0:
                    jitter = random.uniform(0.8, 1.2)
                    delay_seconds = base_delay * jitter

                    await broadcast_update("job_waiting", {
                        "campaign_id": campaign_id,
                        "job_index": job_idx,
                        "delay_seconds": round(delay_seconds),
                        "profile_name": profile_name
                    })

                    self.logger.info(f"Campaign {campaign_id}: Waiting {delay_seconds:.0f}s before job {job_idx}")
                    await asyncio.sleep(delay_seconds)

                # Update progress
                self.queue_manager.update_job_progress(campaign_id, job_idx + 1, total_jobs, profile_name)

                await broadcast_update("job_start", {
                    "campaign_id": campaign_id,
                    "job_index": job_idx,
                    "total_jobs": total_jobs,
                    "profile_name": profile_name,
                    "comment": comment[:50]
                })

                session = FacebookSession(profile_name)

                if not session.load():
                    await broadcast_update("job_error", {
                        "campaign_id": campaign_id,
                        "job_index": job_idx,
                        "error": "Session not found"
                    })
                    results.append({
                        "profile_name": profile_name,
                        "success": False,
                        "error": "Session not found",
                        "job_index": job_idx
                    })
                    continue

                try:
                    # Set Gemini observation context for debugging
                    from gemini_vision import set_observation_context
                    set_observation_context(profile_name=profile_name, campaign_id=campaign_id)

                    # Broadcast warmup start if enabled
                    if enable_warmup:
                        await broadcast_update("warmup_start", {
                            "campaign_id": campaign_id,
                            "job_index": job_idx,
                            "profile_name": profile_name
                        })

                    result = await post_comment_verified(
                        session=session,
                        url=url,
                        comment=comment,
                        proxy=get_effective_proxy(),
                        enable_warmup=enable_warmup
                    )

                    await broadcast_update("job_complete", {
                        "campaign_id": campaign_id,
                        "job_index": job_idx,
                        "profile_name": profile_name,
                        "success": result["success"],
                        "verified": result.get("verified", False),
                        "method": result.get("method", "unknown"),
                        "error": result.get("error"),
                        "warmup": result.get("warmup")
                    })

                    results.append({
                        "profile_name": profile_name,
                        "comment": comment,
                        "success": result["success"],
                        "verified": result.get("verified", False),
                        "method": result.get("method", "unknown"),
                        "error": result.get("error"),
                        "job_index": job_idx,
                        "warmup": result.get("warmup")
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

                    # Track profile usage for rotation (LRU - only updates timestamp on success)
                    profile_manager.mark_profile_used(
                        profile_name=profile_name,
                        campaign_id=campaign_id,
                        comment=comment,
                        success=result["success"],
                        failure_type=failure_type
                    )

                    # Check for throttling/restriction detection
                    if result.get("throttled"):
                        throttle_reason = result.get("throttle_reason", "Facebook restriction detected")
                        self.logger.warning(f"Profile {profile_name} throttled: {throttle_reason}")

                        # Mark profile as restricted for 24 hours
                        profile_manager.mark_profile_restricted(
                            profile_name=profile_name,
                            hours=24,
                            reason=throttle_reason
                        )

                        # Broadcast throttle event to frontend
                        await broadcast_update("profile_throttled", {
                            "profile_name": profile_name,
                            "reason": throttle_reason,
                            "campaign_id": campaign_id,
                            "job_index": job_idx
                        })

                except Exception as e:
                    self.logger.error(f"Error processing job {job_idx} in campaign {campaign_id}: {e}")
                    await broadcast_update("job_error", {
                        "campaign_id": campaign_id,
                        "job_index": job_idx,
                        "error": str(e)
                    })
                    results.append({
                        "profile_name": profile_name,
                        "success": False,
                        "error": str(e),
                        "job_index": job_idx
                    })

                    # Track exception in analytics (classify as infrastructure error)
                    error_str = str(e).lower()
                    if any(x in error_str for x in ["timeout", "proxy", "connection", "network"]):
                        exc_failure_type = "infrastructure"
                    else:
                        exc_failure_type = "facebook_error"

                    profile_manager.mark_profile_used(
                        profile_name=profile_name,
                        campaign_id=campaign_id,
                        comment=comment,
                        success=False,
                        failure_type=exc_failure_type
                    )

            # Campaign completed
            success_count = sum(1 for r in results if r.get("success"))
            self.queue_manager.set_completed(campaign_id, success_count, len(results), results)

            await broadcast_update("queue_campaign_complete", {
                "campaign_id": campaign_id,
                "success": success_count,
                "total": len(results)
            })

            await broadcast_update("campaign_complete", {"total": len(results), "success": success_count})

        except Exception as e:
            self.logger.error(f"Campaign {campaign_id} failed: {e}")
            self.queue_manager.set_failed(campaign_id, str(e))
            await broadcast_update("queue_campaign_failed", {
                "campaign_id": campaign_id,
                "error": str(e)
            })


# Initialize queue processor
queue_processor = QueueProcessor(queue_manager)


# Helper functions for campaign queue
import re
from urllib.parse import urlparse

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
    except:
        return url.lower().strip()


def assign_profiles_for_url(count: int, sessions: List[Dict]) -> List[str]:
    """Get profile names for commenting on a URL."""
    valid = [s["profile_name"] for s in sessions if s.get("has_valid_cookies", False)]
    random.shuffle(valid)  # Randomize who comments first
    return valid[:count]


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
    secret: Optional[str] = None
    profile_name: Optional[str] = None


class CredentialInfo(BaseModel):
    uid: str
    profile_name: Optional[str]
    has_secret: bool
    created_at: Optional[str]
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
# PROFILE ANALYTICS ENDPOINTS
# =============================================================================

@app.get("/analytics/summary")
async def get_analytics_summary(current_user: dict = Depends(get_current_user)):
    """Get summary analytics for all profiles."""
    from profile_manager import get_profile_manager
    pm = get_profile_manager()
    return pm.get_analytics_summary()


@app.get("/analytics/profiles")
async def get_all_profile_analytics(current_user: dict = Depends(get_current_user)):
    """Get analytics for profiles that have been used (usage_count > 0)."""
    from profile_manager import get_profile_manager
    from fb_session import list_saved_sessions
    pm = get_profile_manager()

    # Build display name lookup from sessions (normalized_name -> display_name)
    sessions = list_saved_sessions()
    display_names = {}
    for s in sessions:
        display_name = s.get("profile_name", "")
        normalized = display_name.replace(" ", "_").replace("/", "_").lower()
        display_names[normalized] = display_name

    profiles = []
    for profile_name in pm.get_all_profiles():
        analytics = pm.get_profile_analytics(profile_name)
        if analytics and analytics.get("usage_count", 0) > 0:
            # Add pretty display name from session data
            analytics["display_name"] = display_names.get(profile_name, profile_name)
            profiles.append(analytics)

    # Sort by last_used_at (most recent first)
    profiles.sort(key=lambda p: p.get("last_used_at") or "", reverse=True)
    return {"profiles": profiles}


@app.get("/analytics/profiles/{profile_name}")
async def get_profile_analytics(
    profile_name: str,
    current_user: dict = Depends(get_current_user)
):
    """Get detailed analytics for a single profile."""
    from profile_manager import get_profile_manager
    pm = get_profile_manager()

    analytics = pm.get_profile_analytics(profile_name)
    if not analytics:
        raise HTTPException(status_code=404, detail="Profile not found")
    return analytics


@app.post("/analytics/profiles/{profile_name}/unblock")
async def unblock_profile(
    profile_name: str,
    current_user: dict = Depends(get_current_user)
):
    """Manually unblock a restricted profile."""
    from profile_manager import get_profile_manager
    pm = get_profile_manager()

    pm.unblock_profile(profile_name)
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
    result = await test_session(session, get_effective_proxy())
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
        proxy=get_effective_proxy()
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
                proxy=get_effective_proxy()
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
                    proxy=get_effective_proxy()
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

class AddToQueueRequest(BaseModel):
    url: str
    comments: List[str]
    duration_minutes: int = 30
    filter_tags: Optional[List[str]] = None
    enable_warmup: bool = True  # Warmup enabled by default for new campaigns


class RetryJobRequest(BaseModel):
    """Request to retry a failed job in a completed campaign."""
    job_index: int
    profile_name: str
    comment: str
    original_profile: Optional[str] = None  # Track which profile originally failed


class BulkRetryRequest(BaseModel):
    """Request to retry all failed jobs in a completed campaign."""
    strategy: str = "auto"  # "auto" | "single" | "manual"
    # auto: Rotate through available healthy profiles
    # single: Use one profile for all retries
    # manual: Explicit mapping (advanced)
    profile_name: Optional[str] = None  # For "single" strategy
    profile_mapping: Optional[Dict[int, str]] = None  # For "manual" strategy {job_index: profile_name}


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
        assigned_profiles = [request.profile_name] * len(request.comments)
    else:
        # Use UNIFIED selection (handles cookies, tags, restrictions, LRU by success)
        assigned_profiles = profile_manager.get_eligible_profiles(
            filter_tags=request.filter_tags,
            count=len(request.comments)
        )

    if not assigned_profiles:
        if request.filter_tags:
            raise HTTPException(status_code=400, detail=f"No eligible profiles match tags: {request.filter_tags}")
        else:
            raise HTTPException(status_code=400, detail="No eligible profiles available")

    if len(assigned_profiles) < len(request.comments):
        logger.warning(f"[TEST] Only {len(assigned_profiles)} profiles for {len(request.comments)} comments")

    total_jobs = min(len(request.comments), len(assigned_profiles))
    test_id = f"test_{datetime.now().strftime('%H%M%S')}"

    logger.info(f"[TEST-CAMPAIGN] Starting {test_id}: {total_jobs} comments, warmup={request.enable_warmup}")

    await broadcast_update("test_campaign_start", {
        "test_id": test_id,
        "url": request.url,
        "total_jobs": total_jobs,
        "enable_warmup": request.enable_warmup
    })

    results = []

    for job_idx, (profile_name, comment) in enumerate(zip(assigned_profiles, request.comments[:total_jobs])):
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
                proxy=get_effective_proxy(),
                enable_warmup=request.enable_warmup
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
                profile_manager.mark_profile_restricted(
                    profile_name=profile_name,
                    hours=24,
                    reason=result.get("throttle_reason", "Test detected throttle")
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
async def add_to_queue(request: AddToQueueRequest, current_user: dict = Depends(get_current_user)) -> Dict:
    """Add a new campaign to the persistent queue."""
    try:
        campaign = queue_manager.add_campaign(
            url=request.url,
            comments=request.comments,
            duration_minutes=request.duration_minutes,
            username=current_user["username"],
            filter_tags=request.filter_tags,
            enable_warmup=request.enable_warmup
        )

        # Broadcast to all connected clients
        await broadcast_update("queue_campaign_added", campaign)

        return campaign

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


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
            proxy=get_effective_proxy(),
            enable_warmup=enable_warmup  # RESPECT original campaign's warmup setting
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
            profile_manager.mark_profile_restricted(
                profile_name=request.profile_name,
                hours=24,
                reason=throttle_reason
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


def assign_profiles_to_jobs(
    failed_jobs: List[dict],
    profiles: List[str],
    request: BulkRetryRequest
) -> List[dict]:
    """Assign profiles to jobs based on strategy."""
    if request.strategy == "single":
        # Use the same profile for all
        profile = request.profile_name or profiles[0]
        return [{**job, "profile_name": profile} for job in failed_jobs]

    elif request.strategy == "manual" and request.profile_mapping:
        # Use explicit mapping
        return [{
            **job,
            "profile_name": request.profile_mapping.get(job["job_index"], profiles[0])
        } for job in failed_jobs]

    else:  # "auto" - rotate through profiles
        assigned = []
        for i, job in enumerate(failed_jobs):
            profile = profiles[i % len(profiles)]
            assigned.append({**job, "profile_name": profile})
        return assigned


@app.post("/queue/{campaign_id}/bulk-retry")
async def bulk_retry_failed_jobs(
    campaign_id: str,
    request: BulkRetryRequest,
    current_user: dict = Depends(get_current_user)
) -> Dict:
    """
    Retry all failed jobs in a completed campaign.

    RESPECTS original campaign settings:
    - filter_tags: Profiles must match ALL original tags
    - warmup: Uses original campaign's warmup setting
    - restrictions: Profiles must not be restricted
    - analytics: Tracks with failure_type granularity

    SKIPS queue (immediate execution).

    Strategies:
    - auto: Rotate through ELIGIBLE profiles (respects tags, restrictions)
    - single: Use one profile for all retries (must be eligible)
    - manual: Use explicit job_index -> profile_name mapping (all must be eligible)
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

    # Identify failed jobs (consolidated - latest result for each job_index)
    consolidated: Dict[int, dict] = {}
    for result in campaign.get("results", []):
        consolidated[result.get("job_index", 0)] = result

    failed_jobs = [
        {"job_index": idx, "comment": r.get("comment", ""), "original_profile": r.get("profile_name", "")}
        for idx, r in consolidated.items()
        if not r.get("success")
    ]

    if not failed_jobs:
        return {"success": True, "message": "No failed jobs to retry", "retried": 0, "succeeded": 0, "failed": 0}

    # Get ELIGIBLE profiles using UNIFIED selection (respects tags, restrictions, LRU)
    eligible_profiles = profile_manager.get_eligible_profiles(
        filter_tags=filter_tags if filter_tags else None,
        count=len(failed_jobs)  # Get enough for all jobs
    )

    if not eligible_profiles:
        if filter_tags:
            raise HTTPException(
                status_code=400,
                detail=f"No eligible profiles match original campaign tags: {filter_tags}"
            )
        else:
            raise HTTPException(status_code=400, detail="No eligible profiles for retry")

    # Validate manual/single profile selections against eligible pool
    if request.strategy == "single" and request.profile_name:
        if request.profile_name not in eligible_profiles:
            raise HTTPException(
                status_code=400,
                detail=f"Profile '{request.profile_name}' is not eligible (must match tags {filter_tags} and not be restricted)"
            )

    if request.strategy == "manual" and request.profile_mapping:
        for job_idx, profile_name in request.profile_mapping.items():
            if profile_name not in eligible_profiles:
                raise HTTPException(
                    status_code=400,
                    detail=f"Profile '{profile_name}' for job {job_idx} is not eligible (must match tags {filter_tags} and not be restricted)"
                )

    # Assign profiles based on strategy (using only eligible profiles)
    assignments = assign_profiles_to_jobs(failed_jobs, eligible_profiles, request)

    # Broadcast that bulk retry is starting
    await broadcast_update("queue_campaign_bulk_retry_start", {
        "campaign_id": campaign_id,
        "total_jobs": len(assignments),
        "strategy": request.strategy,
        "warmup_enabled": enable_warmup
    })

    # Execute retries sequentially
    results = []
    for i, job in enumerate(assignments):
        try:
            session = FacebookSession(job["profile_name"])
            if not session.load() or not session.has_valid_cookies():
                # Skip invalid sessions (shouldn't happen with unified selection, but safety check)
                results.append({
                    "profile_name": job["profile_name"],
                    "comment": job["comment"],
                    "success": False,
                    "verified": False,
                    "method": "skipped",
                    "error": f"Session '{job['profile_name']}' is invalid or expired",
                    "job_index": job["job_index"],
                    "is_retry": True,
                    "original_profile": job.get("original_profile"),
                    "retried_at": datetime.utcnow().isoformat()
                })
                continue

            # Execute with WARMUP from original campaign
            result = await post_comment_verified(
                session=session,
                url=url,
                comment=job["comment"],
                proxy=get_effective_proxy(),
                enable_warmup=enable_warmup  # RESPECT original campaign's warmup setting
            )

            # Determine failure type for analytics
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
                profile_name=job["profile_name"],
                campaign_id=campaign_id,
                comment=job["comment"],
                success=result.get("success", False),
                failure_type=failure_type
            )

            # Check for throttling/restriction and auto-block
            if result.get("throttled"):
                throttle_reason = result.get("throttle_reason", "Facebook restriction detected")
                profile_manager.mark_profile_restricted(
                    profile_name=job["profile_name"],
                    hours=24,
                    reason=throttle_reason
                )

            results.append({
                "profile_name": job["profile_name"],
                "comment": job["comment"],
                "success": result.get("success", False),
                "verified": result.get("verified", False),
                "method": result.get("method", "unknown"),
                "error": result.get("error"),
                "job_index": job["job_index"],
                "is_retry": True,
                "original_profile": job.get("original_profile"),
                "retried_at": datetime.utcnow().isoformat(),
                "warmup": result.get("warmup")
            })

            # Broadcast progress
            await broadcast_update("queue_campaign_bulk_retry_progress", {
                "campaign_id": campaign_id,
                "completed": i + 1,
                "total": len(assignments),
                "last_result": results[-1]
            })

        except Exception as e:
            logger.error(f"Bulk retry job {job['job_index']} failed: {e}")

            # Track exception in analytics
            error_str = str(e).lower()
            if any(x in error_str for x in ["timeout", "proxy", "connection", "network"]):
                exc_failure_type = "infrastructure"
            else:
                exc_failure_type = "facebook_error"

            profile_manager.mark_profile_used(
                profile_name=job["profile_name"],
                campaign_id=campaign_id,
                comment=job["comment"],
                success=False,
                failure_type=exc_failure_type
            )

            results.append({
                "profile_name": job["profile_name"],
                "comment": job["comment"],
                "success": False,
                "verified": False,
                "method": "error",
                "error": str(e),
                "job_index": job["job_index"],
                "is_retry": True,
                "original_profile": job.get("original_profile"),
                "retried_at": datetime.utcnow().isoformat()
            })

    # Batch update campaign with all retry results
    updated_campaign = queue_manager.add_bulk_retry_results(campaign_id, results)

    succeeded = sum(1 for r in results if r.get("success"))
    failed_count = len(results) - succeeded

    # Broadcast completion
    await broadcast_update("queue_campaign_bulk_retry_complete", {
        "campaign_id": campaign_id,
        "retried": len(results),
        "succeeded": succeeded,
        "failed": failed_count,
        "campaign": updated_campaign
    })

    return {
        "success": True,
        "retried": len(results),
        "succeeded": succeeded,
        "failed": failed_count,
        "results": results,
        "campaign": updated_campaign
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
async def get_credentials(current_user: dict = Depends(get_current_user)):
    """Get all saved credentials (without passwords)."""
    credential_manager.load_credentials()
    credentials = credential_manager.get_all_credentials()
    sessions = list_saved_sessions()

    sessions_by_profile = {
        (s.get("profile_name") or "").strip().lower(): s
        for s in sessions
        if s.get("profile_name")
    }
    sessions_by_user_id = {
        str(s.get("user_id")): s
        for s in sessions
        if s.get("user_id") is not None
    }

    enriched: List[Dict] = []
    for cred in credentials:
        session = None

        profile_name = cred.get("profile_name")
        if profile_name:
            session = sessions_by_profile.get(profile_name.strip().lower())

        if session is None:
            session = sessions_by_user_id.get(str(cred.get("uid")))

        enriched.append(
            {
                **cred,
                "session_connected": session is not None,
                "session_valid": (session.get("has_valid_cookies") if session else None),
                "session_profile_name": (session.get("profile_name") if session else None),
            }
        )

    return enriched


@app.post("/credentials")
async def add_credential(request: CredentialAddRequest, current_user: dict = Depends(get_current_user)) -> Dict:
    """Add a new credential."""
    credential_manager.add_credential(
        uid=request.uid,
        password=request.password,
        secret=request.secret,
        profile_name=request.profile_name
    )
    return {"success": True, "uid": request.uid}


@app.post("/credentials/bulk-import")
async def bulk_import_credentials(file: UploadFile = File(...), current_user: dict = Depends(get_current_user)) -> Dict:
    """
    Import credentials from text file.
    Format per line: uid:password:2fa_secret
    Example: 61571384288937:BHvSDSchultz:EBKJL7AVC3X6PPCG56HPDQTKV4X5R37K
    """
    content = await file.read()
    lines = content.decode("utf-8").strip().split("\n")

    imported = 0
    errors = []

    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue

        parts = line.split(":")
        if len(parts) != 3:
            errors.append(f"Line {i+1}: Invalid format (expected uid:password:secret)")
            continue

        uid, password, secret = parts

        # Auto-generate profile name from last 6 digits of UID
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

    return {"imported": imported, "errors": errors, "total_lines": len(lines)}


@app.delete("/credentials/{uid}")
async def delete_credential(uid: str, current_user: dict = Depends(get_current_user)) -> Dict:
    """Delete a credential."""
    success = credential_manager.delete_credential(uid)
    if success:
        return {"success": True, "uid": uid}
    raise HTTPException(status_code=404, detail=f"Credential not found: {uid}")


@app.get("/otp/{uid}", response_model=OTPResponse)
async def get_otp(uid: str, current_user: dict = Depends(get_current_user)) -> OTPResponse:
    """Generate current OTP code for a UID."""
    credential_manager.load_credentials()
    result = credential_manager.generate_otp(uid)
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
    proxy_url = get_effective_proxy()  # Start with effective proxy (respects user default)
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
    proxy_url = get_effective_proxy()
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


@app.websocket("/ws/session/{session_id}/control")
async def websocket_session_control(websocket: WebSocket, session_id: str, token: str = Query(None)):
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

        # Start session if not already active for this session_id
        if manager.session_id != session_id:
            result = await manager.start_session(session_id)
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
                except:
                    pass  # Connection already dead
            except Exception as e:
                logger.error(f"Error handling WS message: {e}")
                try:
                    await websocket.send_json({"type": "error", "data": {"message": str(e)}})
                except:
                    pass  # Connection already dead

    except WebSocketDisconnect:
        logger.info(f"Remote control WS disconnected for session {session_id}")
    except Exception as e:
        logger.error(f"Remote control WS error: {e}")
    finally:
        manager.unsubscribe(websocket)
        # Note: Browser stays open for reconnection


@app.post("/sessions/{session_id}/remote/start")
async def start_remote_session(session_id: str, current_user: dict = Depends(get_current_user)) -> Dict:
    """Start a remote control session for the given session."""
    manager = get_browser_manager()
    return await manager.start_session(session_id)


@app.post("/sessions/{session_id}/remote/stop")
async def stop_remote_session(session_id: str, current_user: dict = Depends(get_current_user)) -> Dict:
    """Stop the current remote control session."""
    manager = get_browser_manager()
    if manager.session_id != session_id:
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
    if manager.session_id == session_id:
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
        except:
            pass
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

    if manager.session_id != session_id:
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
                except:
                    pass


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

        # Add proxy if session has one
        proxy = session.get_proxy()
        if proxy:
            context_options["proxy"] = _build_playwright_proxy(proxy)
            logger.info(f"[DIALOG-TEST] Using proxy: {proxy[:30]}...")

        # Launch browser
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-notifications", "--disable-gpu"]
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
# TEMPORARY TEST ENDPOINT - DOM-Based Adaptive Agent
# Gemini decides WHAT to do, Playwright finds WHERE (no coordinate hallucination)
# ===========================================================================

class AdaptiveAgentRequest(BaseModel):
    """Request model for adaptive agent test."""
    profile_name: str = "anna_pelfrey"
    task: str = "scroll the feed, find an interesting post, open it, and write a contextual comment"
    max_steps: int = 20


@app.post("/test-adaptive-agent-v2")
async def test_adaptive_agent_v2(
    request: AdaptiveAgentRequest,
    current_user: dict = Depends(get_current_user)
) -> Dict:
    """
    DOM-Based Adaptive Agent - Gemini decides WHAT, Playwright finds WHERE.

    No coordinate hallucination - uses DOM element matching instead.
    """
    from playwright.async_api import async_playwright
    from playwright_stealth import Stealth
    from fb_session import FacebookSession, apply_session_to_context
    from gemini_vision import get_vision_client, set_observation_context
    from comment_bot import save_debug_screenshot, _build_playwright_proxy, dump_interactive_elements
    from google.genai import types
    import re
    import json as json_module

    results = {
        "profile_name": request.profile_name,
        "task": request.task,
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
    set_observation_context(profile_name=request.profile_name, campaign_id="adaptive_agent_v2")

    logger.info(f"[ADAPTIVE-V2] Starting task: {request.task}")
    logger.info(f"[ADAPTIVE-V2] Profile: {request.profile_name}, Max steps: {request.max_steps}")

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
            "has_touch": True,  # Enable touch support for mobile tap gestures
            "is_mobile": True,  # Mobile mode for proper touch handling
        }

        # Add proxy if session has one
        proxy = session.get_proxy()
        if proxy:
            context_options["proxy"] = _build_playwright_proxy(proxy)
            logger.info(f"[ADAPTIVE-V2] Using proxy: {proxy[:30]}...")

        # Launch browser
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-notifications", "--disable-gpu"]
        )
        context = await browser.new_context(**context_options)

        # Apply stealth
        await Stealth().apply_stealth_async(context)

        # Create page and apply session cookies
        page = await context.new_page()
        await apply_session_to_context(context, session)

        # Helper function to check if element is visible on screen
        def is_element_visible(el: dict, viewport_height: int = 873) -> bool:
            """Check if element is within visible viewport."""
            bounds = el.get('bounds', {})
            if not bounds:
                return False
            y = bounds.get('y', -1)
            h = bounds.get('h', 0)
            # Element must be at least partially visible (y > 0 and top edge below viewport)
            # Allow elements that are partially visible at top (y > -h/2)
            return y > -(h // 2) and y < viewport_height

        # Helper function to find element by Gemini's description
        async def find_element_by_description(description: str, elements: list) -> Optional[dict]:
            """Match Gemini's description to a DOM element. Only matches VISIBLE elements."""
            desc_lower = description.lower()

            # Filter to only visible elements first
            visible_elements = [el for el in elements if is_element_visible(el)]
            logger.info(f"[ADAPTIVE-V2] Matching '{description}' against {len(visible_elements)} visible elements (filtered from {len(elements)} total)")

            # Priority 1: Exact aria-label match
            for el in visible_elements:
                if el.get('ariaLabel', '').lower() == desc_lower:
                    logger.info(f"[ADAPTIVE-V2] Matched by exact aria-label: {el.get('ariaLabel')}")
                    return el

            # Priority 2: Partial aria-label match
            for el in visible_elements:
                if desc_lower in el.get('ariaLabel', '').lower():
                    logger.info(f"[ADAPTIVE-V2] Matched by partial aria-label (desc in aria): {el.get('ariaLabel')}")
                    return el
                if el.get('ariaLabel', '').lower() in desc_lower:
                    logger.info(f"[ADAPTIVE-V2] Matched by partial aria-label (aria in desc): {el.get('ariaLabel')}")
                    return el

            # Priority 3: Text content match
            for el in visible_elements:
                if desc_lower in el.get('text', '').lower():
                    logger.info(f"[ADAPTIVE-V2] Matched by text content (desc in text): {el.get('text', '')[:30]}")
                    return el
                if el.get('text', '').lower() in desc_lower:
                    logger.info(f"[ADAPTIVE-V2] Matched by text content (text in desc): {el.get('text', '')[:30]}")
                    return el

            # Priority 4: Role-based matching for common elements
            if 'back' in desc_lower or 'close' in desc_lower or 'dismiss' in desc_lower:
                for el in visible_elements:
                    aria = el.get('ariaLabel', '').lower()
                    if 'back' in aria or 'close' in aria or aria == 'x':
                        logger.info(f"[ADAPTIVE-V2] Matched back/close button: {el.get('ariaLabel')}")
                        return el

            if 'comment' in desc_lower:
                for el in visible_elements:
                    aria = el.get('ariaLabel', '').lower()
                    if 'comment' in aria:
                        logger.info(f"[ADAPTIVE-V2] Matched comment element: {el.get('ariaLabel')}")
                        return el

            if 'like' in desc_lower:
                for el in visible_elements:
                    aria = el.get('ariaLabel', '').lower()
                    if 'like' in aria and 'unlike' not in aria:
                        logger.info(f"[ADAPTIVE-V2] Matched like element: {el.get('ariaLabel')}")
                        return el

            if 'see why' in desc_lower:
                for el in visible_elements:
                    if 'see why' in el.get('text', '').lower():
                        logger.info(f"[ADAPTIVE-V2] Matched 'see why' element")
                        return el

            logger.warning(f"[ADAPTIVE-V2] No visible element matched for: {description}")
            return None

        async def click_element(el: dict) -> bool:
            """Click an element using its bounding box center."""
            bounds = el.get('bounds', {})
            if not bounds:
                return False
            x = bounds['x'] + bounds['w'] // 2
            y = bounds['y'] + bounds['h'] // 2
            await page.mouse.click(x, y)
            return True

        try:
            # Step 0: Navigate to Facebook
            logger.info("[ADAPTIVE-V2] Navigating to Facebook...")
            await page.goto("https://m.facebook.com", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            screenshot_path = await save_debug_screenshot(page, "adaptive_v2_step_0")
            results["screenshots"].append(screenshot_path)
            results["steps"].append({
                "step": 0,
                "action": "navigate",
                "target": "https://m.facebook.com",
                "url": page.url,
                "screenshot": screenshot_path
            })

            # Track action history for loop detection
            action_history = []

            # Adaptive loop
            for step_num in range(1, request.max_steps + 1):
                logger.info(f"[ADAPTIVE-V2] Step {step_num}/{request.max_steps}")

                # Take screenshot
                screenshot_path = await save_debug_screenshot(page, f"adaptive_v2_step_{step_num}")

                # Dump DOM elements
                elements = await dump_interactive_elements(page, f"step_{step_num}")

                # Filter to visible elements only
                visible_elements = [el for el in elements if is_element_visible(el)]
                logger.info(f"[ADAPTIVE-V2] Found {len(elements)} total elements, {len(visible_elements)} visible")

                # Format elements for Gemini (simplified view) - ONLY VISIBLE
                elements_summary = []
                for i, el in enumerate(visible_elements[:30]):  # Limit to 30 visible elements
                    text = el.get('text', '')[:40] or el.get('ariaLabel', '')[:40] or el.get('placeholder', '')[:40]
                    bounds = el.get('bounds', {})
                    if text:
                        elements_summary.append(f"[{i}] {el['tag']} \"{text}\" (y={bounds.get('y', '?')})")

                # Read screenshot
                with open(screenshot_path, "rb") as f:
                    image_data = f.read()

                # Build action history summary for loop detection
                recent_actions = action_history[-5:] if action_history else []
                history_text = chr(10).join([f"  Step {a['step']}: {a['action']}" for a in recent_actions]) if recent_actions else "  (none yet)"

                # Detect repeated actions
                if len(action_history) >= 3:
                    last_3 = [a['action'] for a in action_history[-3:]]
                    if len(set(last_3)) == 1:  # All 3 are the same
                        loop_warning = f"\n⚠️ WARNING: You have repeated '{last_3[0]}' 3 times with no progress. TRY SOMETHING DIFFERENT!"
                    else:
                        loop_warning = ""
                else:
                    loop_warning = ""

                # Build the adaptive prompt - NO COORDINATES
                prompt = f"""You are an AI agent controlling a Facebook mobile browser.

TASK: {request.task}

CURRENT STEP: {step_num} of {request.max_steps}

RECENT ACTION HISTORY:
{history_text}{loop_warning}

INTERACTIVE ELEMENTS ON PAGE (from DOM):
{chr(10).join(elements_summary) if elements_summary else "No elements found"}

Analyze the screenshot and DOM elements. Decide the NEXT ACTION.

AVAILABLE ACTIONS:
1. SCROLL direction=<up|down> - Scroll to see more content
2. CLICK element="<description>" - Click an element (describe it by text/label, NOT coordinates)
3. TYPE text="<text to type>" - Type text into active input field
4. WAIT reason="<why>" - Wait for content to load
5. DONE reason="<why task is complete>" - Task completed
6. FAILED reason="<why task cannot be completed>" - Task failed

IMPORTANT RULES:
- For CLICK: describe the element by its TEXT or LABEL, not coordinates
- Example: CLICK element="Comment" or CLICK element="Back" or CLICK element="See why"
- If you see a "We removed your comment" notification, click "Back" or the back arrow to dismiss
- To comment on a post, first CLICK the post or its Comment button, then TYPE your comment
- Make comments contextual to the post content (not generic)
- CRITICAL: If an action didn't change the page, DO NOT repeat it! Try a DIFFERENT action.
- If a button doesn't work after clicking, try scrolling or clicking other elements
- If stuck in a loop, use FAILED to report that the task cannot be completed

RESPONSE FORMAT:
ACTION: <action_type> <parameters>
REASONING: <brief explanation>

Examples:
ACTION: SCROLL direction=down
REASONING: Looking for an interesting post to comment on

ACTION: CLICK element="Comment"
REASONING: Opening comments on the NFL post to write a comment

ACTION: CLICK element="Back"
REASONING: Dismissing the notification to return to feed

ACTION: TYPE text="What a great play! The defense really stepped up."
REASONING: Writing a contextual comment about the football post

ACTION: DONE reason="Successfully commented on a post"
REASONING: Comment was submitted"""

                image_part = types.Part.from_bytes(data=image_data, mime_type="image/png")

                # Safety settings to allow processing of restriction/policy pages
                safety_settings = [
                    types.SafetySetting(
                        category="HARM_CATEGORY_HARASSMENT",
                        threshold="BLOCK_NONE"
                    ),
                    types.SafetySetting(
                        category="HARM_CATEGORY_HATE_SPEECH",
                        threshold="BLOCK_NONE"
                    ),
                    types.SafetySetting(
                        category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
                        threshold="BLOCK_NONE"
                    ),
                    types.SafetySetting(
                        category="HARM_CATEGORY_DANGEROUS_CONTENT",
                        threshold="BLOCK_NONE"
                    ),
                ]

                try:
                    response = await asyncio.to_thread(
                        vision.client.models.generate_content,
                        model=vision.model,
                        contents=[prompt, image_part],
                        config=types.GenerateContentConfig(safety_settings=safety_settings)
                    )

                    # Debug: log full response structure if text is empty
                    result_text = response.text
                    if not result_text:
                        # Check for safety blocks or other issues
                        logger.warning(f"[ADAPTIVE-V2] Gemini empty response. Candidates: {response.candidates if hasattr(response, 'candidates') else 'N/A'}")
                        if hasattr(response, 'candidates') and response.candidates:
                            candidate = response.candidates[0]
                            if hasattr(candidate, 'finish_reason'):
                                logger.warning(f"[ADAPTIVE-V2] Finish reason: {candidate.finish_reason}")
                            if hasattr(candidate, 'content') and candidate.content:
                                logger.warning(f"[ADAPTIVE-V2] Content parts: {candidate.content.parts if hasattr(candidate.content, 'parts') else 'N/A'}")

                        # Retry with longer delay (might be rate limited)
                        logger.warning(f"[ADAPTIVE-V2] Retrying Gemini call after 3s delay...")
                        await asyncio.sleep(3)
                        response = await asyncio.to_thread(
                            vision.client.models.generate_content,
                            model=vision.model,
                            contents=[prompt, image_part]
                        )
                        result_text = response.text

                    if not result_text:
                        # Fallback: try to find and click known buttons when Gemini fails
                        logger.info(f"[ADAPTIVE-V2] Gemini empty - trying DOM fallback for known buttons")
                        fallback_clicked = False

                        # Check for "Request review" button (restriction flow)
                        for el in visible_elements:
                            aria = el.get('ariaLabel', '')
                            text = el.get('text', '').lower()
                            if 'request review' in aria.lower() or 'request review' in text:
                                bounds = el.get('bounds', {})
                                if bounds.get('y', 0) > 0:
                                    # Use JS click via locator (more reliable than mouse.click)
                                    try:
                                        locator = page.locator(f'[aria-label="{aria}"]').first
                                        if await locator.count() > 0:
                                            await locator.scroll_into_view_if_needed()
                                            await asyncio.sleep(0.5)

                                            # Log element info
                                            el_tag = await locator.evaluate("el => el.tagName")
                                            el_role = await locator.evaluate("el => el.getAttribute('role')")
                                            logger.info(f"[ADAPTIVE-V2] Request review button: tag={el_tag}, role={el_role}")

                                            # Try Playwright's built-in click with force (bypasses actionability)
                                            url_before = page.url
                                            await locator.click(force=True, timeout=5000)
                                            await asyncio.sleep(3)
                                            url_after = page.url

                                            logger.info(f"[ADAPTIVE-V2] After click(force): URL changed={url_before != url_after}")

                                            # If URL didn't change, try tap (mobile gesture)
                                            if url_before == url_after:
                                                logger.info(f"[ADAPTIVE-V2] click(force) didn't work, trying tap...")
                                                try:
                                                    await locator.tap(timeout=5000)
                                                    await asyncio.sleep(3)
                                                except Exception as tap_err:
                                                    logger.warning(f"[ADAPTIVE-V2] tap() failed: {tap_err}")

                                            logger.info(f"[ADAPTIVE-V2] Fallback clicked 'Request review'")
                                            results["steps"].append({
                                                "step": step_num,
                                                "action_taken": f"FALLBACK_CLICK 'Request review'",
                                                "screenshot": screenshot_path
                                            })
                                            fallback_clicked = True
                                            break
                                    except Exception as e:
                                        logger.warning(f"[ADAPTIVE-V2] Fallback click failed: {e}, trying coordinates")

                                    # Fallback to mouse click if JS fails
                                    if not fallback_clicked:
                                        x = bounds['x'] + bounds['w'] // 2
                                        y = bounds['y'] + bounds['h'] // 2
                                        await page.mouse.click(x, y)
                                        await asyncio.sleep(2)
                                        logger.info(f"[ADAPTIVE-V2] Fallback mouse clicked 'Request review' at ({x},{y})")
                                        results["steps"].append({
                                            "step": step_num,
                                            "action_taken": f"FALLBACK_MOUSE_CLICK 'Request review' at ({x},{y})",
                                            "screenshot": screenshot_path
                                        })
                                        fallback_clicked = True
                                        break

                        # If button not found, try scrolling down to find it
                        if not fallback_clicked:
                            logger.info(f"[ADAPTIVE-V2] Request review not found, trying scroll down...")
                            await page.mouse.wheel(0, 500)
                            await asyncio.sleep(2)

                            # Re-scan for the button after scroll using dump_interactive_elements
                            elements_after_scroll = await dump_interactive_elements(page, f"scroll_fallback_{step_num}")
                            visible_after = [e for e in elements_after_scroll if is_element_visible(e)]

                            for el in visible_after:
                                aria = el.get('ariaLabel', '')
                                if 'request review' in aria.lower():
                                    try:
                                        locator = page.locator(f'[aria-label="{aria}"]').first
                                        if await locator.count() > 0:
                                            await locator.click(force=True, timeout=5000)
                                            await asyncio.sleep(3)
                                            logger.info(f"[ADAPTIVE-V2] Fallback clicked 'Request review' after scroll")
                                            results["steps"].append({
                                                "step": step_num,
                                                "action_taken": f"FALLBACK_CLICK_AFTER_SCROLL 'Request review'",
                                                "screenshot": screenshot_path
                                            })
                                            fallback_clicked = True
                                            break
                                    except Exception as e:
                                        logger.warning(f"[ADAPTIVE-V2] Click after scroll failed: {e}")

                        if not fallback_clicked:
                            results["errors"].append(f"Step {step_num}: Gemini returned empty response")
                        continue
                    result_text = result_text.strip()
                    logger.info(f"[ADAPTIVE-V2] Gemini response:\n{result_text}")
                except Exception as e:
                    logger.error(f"[ADAPTIVE-V2] Gemini API error: {e}")
                    results["errors"].append(f"Step {step_num}: Gemini API error - {e}")
                    continue

                step_result = {
                    "step": step_num,
                    "gemini_response": result_text,
                    "screenshot": screenshot_path,
                    "elements_count": len(elements),
                    "action_taken": None,
                    "url": page.url
                }

                # Parse the action from response
                action_match = re.search(r'ACTION:\s*(\w+)\s*(.*)', result_text, re.IGNORECASE)
                reasoning_match = re.search(r'REASONING:\s*(.*)', result_text, re.IGNORECASE | re.DOTALL)

                if not action_match:
                    step_result["action_taken"] = "could_not_parse_action"
                    results["steps"].append(step_result)
                    results["errors"].append(f"Step {step_num}: Could not parse action")
                    continue

                action_type = action_match.group(1).upper()
                action_params = action_match.group(2).strip()
                reasoning = reasoning_match.group(1).strip() if reasoning_match else ""

                step_result["reasoning"] = reasoning

                # Execute the action
                if action_type == "DONE":
                    step_result["action_taken"] = f"DONE: {action_params}"
                    results["steps"].append(step_result)
                    results["final_status"] = "task_completed"
                    logger.info(f"[ADAPTIVE-V2] Task completed: {action_params}")
                    break

                elif action_type == "FAILED":
                    step_result["action_taken"] = f"FAILED: {action_params}"
                    results["steps"].append(step_result)
                    results["final_status"] = "task_failed"
                    logger.info(f"[ADAPTIVE-V2] Task failed: {action_params}")
                    break

                elif action_type == "SCROLL":
                    direction_match = re.search(r'direction=(\w+)', action_params)
                    direction = direction_match.group(1) if direction_match else "down"
                    delta_y = 400 if direction == "down" else -400

                    await page.mouse.wheel(0, delta_y)
                    await asyncio.sleep(1.5)

                    step_result["action_taken"] = f"SCROLL {direction}"
                    logger.info(f"[ADAPTIVE-V2] Scrolled {direction}")

                elif action_type == "CLICK":
                    # Extract element description
                    element_match = re.search(r'element="([^"]+)"', action_params)
                    if element_match:
                        element_desc = element_match.group(1)

                        # Find element in DOM
                        target_el = await find_element_by_description(element_desc, elements)

                        if target_el:
                            aria_label = target_el.get('ariaLabel', '')
                            clicked_via = None

                            # Try native Playwright click FIRST (more reliable for React buttons)
                            if aria_label:
                                try:
                                    locator = page.locator(f'[aria-label="{aria_label}"]').first
                                    if await locator.count() > 0:
                                        # Scroll into view first
                                        await locator.scroll_into_view_if_needed()
                                        await asyncio.sleep(0.3)

                                        # Try JavaScript click first (bypasses all actionability checks)
                                        try:
                                            await locator.evaluate("el => el.click()")
                                            clicked_via = f"JS click [aria-label=\"{aria_label}\"]"
                                            logger.info(f"[ADAPTIVE-V2] JS clicked: {aria_label}")
                                        except Exception as js_err:
                                            logger.warning(f"[ADAPTIVE-V2] JS click failed: {js_err}")
                                            # Fallback to tap (mobile)
                                            try:
                                                await locator.tap(timeout=3000)
                                                clicked_via = f"TAP [aria-label=\"{aria_label}\"]"
                                                logger.info(f"[ADAPTIVE-V2] Tapped: {aria_label}")
                                            except Exception:
                                                # Last resort: click with force
                                                await locator.click(timeout=5000, force=True)
                                                clicked_via = f"CLICK force [aria-label=\"{aria_label}\"]"
                                                logger.info(f"[ADAPTIVE-V2] Click force: {aria_label}")
                                except Exception as e:
                                    logger.warning(f"[ADAPTIVE-V2] Native click failed: {e}, falling back to coordinates")

                            # Fallback to coordinate click if native failed
                            if not clicked_via:
                                bounds = target_el.get('bounds', {})
                                x = bounds['x'] + bounds['w'] // 2
                                y = bounds['y'] + bounds['h'] // 2
                                await page.mouse.click(x, y)
                                clicked_via = f"coordinates ({x},{y})"
                                logger.info(f"[ADAPTIVE-V2] Clicked via coordinates: ({x},{y})")

                            # Wait for potential page change
                            url_before = page.url
                            await asyncio.sleep(2)

                            # Check if page changed
                            url_after = page.url
                            page_changed = url_before != url_after
                            if page_changed:
                                logger.info(f"[ADAPTIVE-V2] Page changed after click: {url_before} -> {url_after}")

                            step_result["action_taken"] = f"CLICK \"{element_desc}\" via {clicked_via}"
                            step_result["page_changed"] = page_changed
                            step_result["matched_element"] = {
                                "tag": target_el.get('tag'),
                                "ariaLabel": aria_label,
                                "text": target_el.get('text', '')[:30]
                            }
                        else:
                            # Fallback: try common selectors
                            clicked = False
                            fallback_selectors = [
                                f'[aria-label*="{element_desc}" i]',
                                f'text="{element_desc}"',
                                f'button:has-text("{element_desc}")',
                                f'div[role="button"]:has-text("{element_desc}")',
                            ]

                            for selector in fallback_selectors:
                                try:
                                    locator = page.locator(selector).first
                                    if await locator.count() > 0:
                                        await locator.click()
                                        await asyncio.sleep(2)
                                        step_result["action_taken"] = f"CLICK \"{element_desc}\" via selector {selector}"
                                        clicked = True
                                        logger.info(f"[ADAPTIVE-V2] Clicked via selector: {selector}")
                                        break
                                except Exception:
                                    continue

                            if not clicked:
                                step_result["action_taken"] = f"CLICK_FAILED: Could not find \"{element_desc}\""
                                results["errors"].append(f"Step {step_num}: Element not found: {element_desc}")
                    else:
                        step_result["action_taken"] = "CLICK_PARSE_ERROR"
                        results["errors"].append(f"Step {step_num}: Could not parse element description")

                elif action_type == "TYPE":
                    text_match = re.search(r'text="([^"]+)"', action_params)
                    if text_match:
                        text = text_match.group(1)

                        # First try to find and focus a VISIBLE input
                        input_focused = False
                        for el in visible_elements:  # Use visible_elements, not all elements
                            if el.get('contentEditable') == 'true' or el['tag'] in ['INPUT', 'TEXTAREA']:
                                bounds = el.get('bounds', {})
                                if bounds and bounds.get('y', -1) > 0:  # Extra check for visibility
                                    x = bounds['x'] + bounds['w'] // 2
                                    y = bounds['y'] + bounds['h'] // 2
                                    logger.info(f"[ADAPTIVE-V2] Focusing input at ({x},{y})")
                                    await page.mouse.click(x, y)
                                    await asyncio.sleep(0.5)
                                    input_focused = True
                                    break

                        await page.keyboard.type(text, delay=50)
                        await asyncio.sleep(1)
                        step_result["action_taken"] = f"TYPE: {text[:50]}..."
                        step_result["input_focused"] = input_focused
                        logger.info(f"[ADAPTIVE-V2] Typed: {text[:50]}... (input_focused={input_focused})")
                    else:
                        step_result["action_taken"] = "TYPE_PARSE_ERROR"

                elif action_type == "WAIT":
                    await asyncio.sleep(2)
                    step_result["action_taken"] = "WAIT 2s"
                    logger.info("[ADAPTIVE-V2] Waited 2s")

                else:
                    step_result["action_taken"] = f"UNKNOWN: {action_type}"
                    results["errors"].append(f"Step {step_num}: Unknown action: {action_type}")

                results["steps"].append(step_result)
                results["screenshots"].append(screenshot_path)

                # Track action for loop detection
                if step_result.get("action_taken"):
                    action_history.append({
                        "step": step_num,
                        "action": step_result["action_taken"]
                    })

            else:
                results["final_status"] = "max_steps_reached"

            # Take final screenshot
            final_screenshot = await save_debug_screenshot(page, "adaptive_v2_final")
            results["screenshots"].append(final_screenshot)
            results["final_url"] = page.url

        except Exception as e:
            logger.error(f"[ADAPTIVE-V2] Error: {e}")
            results["errors"].append(str(e))
            results["final_status"] = "error"
        finally:
            await browser.close()

    logger.info(f"[ADAPTIVE-V2] Test complete: {results['final_status']}")
    return results


@app.on_event("startup")
async def startup_event():
    """Start background tasks on app startup."""
    asyncio.create_task(cleanup_expired_uploads())
    # Start queue processor for background campaign processing
    await queue_processor.start()
    logger.info("Queue processor started on startup")


@app.on_event("shutdown")
async def shutdown_event():
    """Gracefully stop background tasks on shutdown."""
    await queue_processor.stop()
    logger.info("Queue processor stopped on shutdown")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
