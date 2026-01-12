"""
CommentBot API - Streamlined Facebook Comment Automation
"""

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File, Depends, status, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from typing import List, Optional, Dict, Set

# Authentication imports
from auth import create_access_token, create_refresh_token, decode_token, verify_password
from users import user_manager

# Maximum concurrent browser sessions for campaigns
MAX_CONCURRENT = 5
import logging
import os
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

# Mount debug directory for screenshots
debug_path = os.path.join(os.path.dirname(__file__), "debug")
os.makedirs(debug_path, exist_ok=True)
app.mount("/debug", StaticFiles(directory=debug_path), name="debug")

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
        except:
            disconnected.add(ws)
    for ws in disconnected:
        active_connections.discard(ws)

# Get proxy from environment
PROXY_URL = os.getenv("PROXY_URL", "")

# Initialize credential manager
credential_manager = CredentialManager()

# Initialize proxy manager
proxy_manager = ProxyManager()


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
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


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


async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    """
    Dependency that validates JWT and returns current user.
    Use this to protect endpoints that require authentication.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

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
    result = await test_session(session, PROXY_URL if PROXY_URL else None)
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
        proxy=PROXY_URL if PROXY_URL else None
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
                proxy=PROXY_URL if PROXY_URL else None
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
    valid_count = len(valid_profiles)

    if valid_count == 0:
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
                    proxy=PROXY_URL if PROXY_URL else None
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
            is_system=True
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
            is_system=False
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

    # Get proxy URL - ALWAYS use PROXY_URL as default, allow override from proxy_id
    proxy_url = PROXY_URL  # Start with environment variable
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
            detail="Cannot create session: No proxy configured. Set PROXY_URL environment variable or specify a proxy_id."
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

    # Get proxy URL - ALWAYS use PROXY_URL as default, allow override from proxy_id
    proxy_url = PROXY_URL
    if request.proxy_id:
        proxy = proxy_manager.get_proxy(request.proxy_id)
        if proxy:
            proxy_url = proxy.get("url")
        else:
            raise HTTPException(status_code=404, detail=f"Proxy not found: {request.proxy_id}")

    if not proxy_url:
        raise HTTPException(
            status_code=400,
            detail="Cannot create sessions: No proxy configured. Set PROXY_URL environment variable or specify a proxy_id."
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
        # Start session if not already active for this session_id
        if manager.session_id != session_id:
            result = await manager.start_session(session_id)
            if not result["success"]:
                await websocket.send_json({"type": "error", "data": {"message": result.get("error", "Failed to start session")}})
                await websocket.close()
                return

        # Subscribe to frame updates
        manager.subscribe(websocket)

        # Send initial state
        state = await manager.get_current_state()
        await websocket.send_json({"type": "state", "data": state})
        await websocket.send_json({"type": "browser_ready", "data": {"session_id": session_id}})

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
                await websocket.send_json({"type": "error", "data": {"message": f"Invalid JSON: {e}"}})
            except Exception as e:
                logger.error(f"Error handling WS message: {e}")
                await websocket.send_json({"type": "error", "data": {"message": str(e)}})

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


@app.on_event("startup")
async def startup_event():
    """Start background tasks on app startup."""
    asyncio.create_task(cleanup_expired_uploads())


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
