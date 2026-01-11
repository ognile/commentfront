"""
CommentBot API - Streamlined Facebook Comment Automation
"""

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional, Dict, Set

# Maximum concurrent browser sessions for campaigns
MAX_CONCURRENT = 5
import logging
import os
import asyncio
import json
from datetime import datetime
import nest_asyncio

# Patch asyncio to allow nested event loops (crucial for Playwright in FastAPI)
nest_asyncio.apply()

from comment_bot import post_comment, post_comment_verified, test_session, MOBILE_VIEWPORT, DEFAULT_USER_AGENT
from fb_session import FacebookSession, list_saved_sessions
from credentials import CredentialManager
from proxy_manager import ProxyManager
from login_bot import create_session_from_credentials

# Setup Logging
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


# Models
class CommentRequest(BaseModel):
    url: str
    comment: str
    profile_name: str


class CampaignRequest(BaseModel):
    url: str
    comments: List[str]
    profile_names: List[str]


class SessionInfo(BaseModel):
    file: str
    profile_name: str
    user_id: Optional[str]
    extracted_at: str
    valid: bool
    proxy: Optional[str] = None


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
    created_at: str


class ProxyTestResult(BaseModel):
    success: bool
    response_time_ms: Optional[int] = None
    ip: Optional[str] = None
    error: Optional[str] = None


class SessionCreateRequest(BaseModel):
    credential_uid: str
    proxy_id: Optional[str] = None


# Endpoints
@app.get("/")
async def root():
    return {"status": "ok", "message": "CommentBot API"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


@app.websocket("/ws/live")
async def websocket_live(websocket: WebSocket):
    """WebSocket endpoint for live updates."""
    await websocket.accept()
    active_connections.add(websocket)
    logger.info(f"WS connected. Total: {len(active_connections)}")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        active_connections.discard(websocket)
        logger.info(f"WS disconnected. Total: {len(active_connections)}")


@app.get("/sessions")
async def get_sessions() -> List[SessionInfo]:
    """Get all saved sessions."""
    sessions = list_saved_sessions()
    return [
        SessionInfo(
            file=s["file"],
            profile_name=s["profile_name"],
            user_id=s.get("user_id"),
            extracted_at=s["extracted_at"],
            valid=s["has_valid_cookies"],
            proxy=("session" if s.get("proxy") else ("service" if PROXY_URL else None)),
        )
        for s in sessions
    ]


@app.post("/sessions/{profile_name}/test")
async def test_session_endpoint(profile_name: str) -> Dict:
    """Test if a session is valid."""
    session = FacebookSession(profile_name)
    result = await test_session(session, PROXY_URL if PROXY_URL else None)
    return result


@app.post("/comment")
async def post_comment_endpoint(request: CommentRequest) -> Dict:
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
async def run_campaign(request: CampaignRequest) -> Dict:
    """Run a campaign with concurrent execution (max 5 parallel sessions)."""
    total_jobs = min(len(request.profile_names), len(request.comments))

    await broadcast_update("campaign_start", {"url": request.url, "total_jobs": total_jobs})

    # Semaphore to limit concurrent browser sessions
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    results_dict: Dict[int, Dict] = {}

    async def process_one(job_index: int, profile_name: str, comment: str) -> Dict:
        """Process a single comment job with concurrency limit."""
        async with semaphore:
            await broadcast_update("job_start", {"job_index": job_index, "profile_name": profile_name, "comment": comment[:50]})

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

    # Create tasks for all profile/comment pairs
    tasks = [
        process_one(i, profile_name, comment)
        for i, (profile_name, comment) in enumerate(zip(request.profile_names[:total_jobs], request.comments[:total_jobs]))
    ]

    # Run concurrently (max 5 at a time via semaphore)
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Process results (handle any exceptions from gather)
    results = []
    for r in raw_results:
        if isinstance(r, Exception):
            results.append({"success": False, "error": str(r)})
        else:
            results.append(r)

    # Sort results by job_index to maintain order
    results.sort(key=lambda x: x.get("job_index", 0))

    success_count = sum(1 for r in results if r.get("success"))
    await broadcast_update("campaign_complete", {"total": len(results), "success": success_count})

    return {"url": request.url, "total": len(results), "success": success_count, "results": results}


@app.get("/config")
async def get_config() -> Dict:
    """Get current configuration."""
    return {
        "proxy_configured": bool(PROXY_URL),
        "viewport": MOBILE_VIEWPORT,
        "user_agent": DEFAULT_USER_AGENT
    }


# Credential Endpoints
@app.get("/credentials", response_model=List[CredentialInfo])
async def get_credentials():
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
            }
        )

    return enriched


@app.post("/credentials")
async def add_credential(request: CredentialAddRequest) -> Dict:
    """Add a new credential."""
    credential_manager.add_credential(
        uid=request.uid,
        password=request.password,
        secret=request.secret,
        profile_name=request.profile_name
    )
    return {"success": True, "uid": request.uid}


@app.post("/credentials/bulk-import")
async def bulk_import_credentials(file: UploadFile = File(...)) -> Dict:
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
async def delete_credential(uid: str) -> Dict:
    """Delete a credential."""
    success = credential_manager.delete_credential(uid)
    if success:
        return {"success": True, "uid": uid}
    raise HTTPException(status_code=404, detail=f"Credential not found: {uid}")


@app.get("/otp/{uid}", response_model=OTPResponse)
async def get_otp(uid: str) -> OTPResponse:
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
async def get_proxies():
    """Get all saved proxies."""
    proxy_manager.load_proxies()
    proxies = proxy_manager.list_proxies()
    return [
        ProxyInfo(
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
            created_at=p.get("created_at", "")
        )
        for p in proxies
    ]


@app.post("/proxies")
async def add_proxy(request: ProxyAddRequest) -> Dict:
    """Add a new proxy."""
    proxy = proxy_manager.add_proxy(
        name=request.name,
        url=request.url,
        proxy_type=request.proxy_type,
        country=request.country
    )
    return {"success": True, "proxy_id": proxy["id"], "proxy": proxy}


@app.get("/proxies/{proxy_id}")
async def get_proxy(proxy_id: str) -> Dict:
    """Get a proxy by ID."""
    proxy = proxy_manager.get_proxy(proxy_id)
    if not proxy:
        raise HTTPException(status_code=404, detail=f"Proxy not found: {proxy_id}")
    return proxy


@app.put("/proxies/{proxy_id}")
async def update_proxy(proxy_id: str, request: ProxyUpdateRequest) -> Dict:
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
async def delete_proxy(proxy_id: str) -> Dict:
    """Delete a proxy."""
    success = proxy_manager.delete_proxy(proxy_id)
    if success:
        return {"success": True, "proxy_id": proxy_id}
    raise HTTPException(status_code=404, detail=f"Proxy not found: {proxy_id}")


@app.post("/proxies/{proxy_id}/test", response_model=ProxyTestResult)
async def test_proxy(proxy_id: str) -> ProxyTestResult:
    """Test a proxy's connectivity."""
    result = await proxy_manager.test_proxy(proxy_id)
    return ProxyTestResult(
        success=result.get("success", False),
        response_time_ms=result.get("response_time_ms"),
        ip=result.get("ip"),
        error=result.get("error")
    )


@app.post("/sessions/{profile_name}/assign-proxy")
async def assign_proxy_to_session(profile_name: str, proxy_id: str) -> Dict:
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
async def create_session(request: SessionCreateRequest) -> Dict:
    """
    Create a new session by logging in with stored credentials.

    This triggers automated login using the login_bot module.
    Progress is broadcast via WebSocket.
    """
    credential_uid = request.credential_uid

    # Get proxy URL if specified
    proxy_url = None
    if request.proxy_id:
        proxy = proxy_manager.get_proxy(request.proxy_id)
        if proxy:
            proxy_url = proxy.get("url")
        else:
            raise HTTPException(status_code=404, detail=f"Proxy not found: {request.proxy_id}")
    elif PROXY_URL:
        # Use global proxy if no specific proxy specified
        proxy_url = PROXY_URL

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


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
