from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict
import logging
import asyncio
import os
import pyotp

from queue_manager import JobManager, Job
from adspower import AdsPowerClient
from credentials import CredentialManager
from mappings import MappingManager
from fb_session import FacebookSession, list_saved_sessions, apply_session_to_context, verify_session_logged_in
from playwright.async_api import async_playwright
from fastapi.staticfiles import StaticFiles

# GeeLark (optional - if credentials available)
try:
    from geelark_client import GeeLarkClient
    GEELARK_AVAILABLE = bool(os.getenv("GEELARK_BEARER_TOKEN"))
except ImportError:
    GEELARK_AVAILABLE = False
    
from url_utils import clean_facebook_url, is_url_safe_for_geelark, resolve_facebook_redirect

# Setup Logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("backend.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("API")

app = FastAPI()
job_manager = JobManager()
adspower = AdsPowerClient()
credentials_manager = CredentialManager()
mapping_manager = MappingManager()

# Initialize GeeLark client if available
geelark_client = None
if GEELARK_AVAILABLE:
    try:
        geelark_client = GeeLarkClient()
        logger.info("GeeLark client initialized")
    except Exception as e:
        logger.warning(f"Failed to initialize GeeLark: {e}")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount Debug Directory for Visual Verification
debug_path = os.path.join(os.path.dirname(__file__), "debug")
os.makedirs(debug_path, exist_ok=True)
app.mount("/debug_view", StaticFiles(directory=debug_path), name="debug_view")

# Models
class JobRequest(BaseModel):
    profileId: str
    profileName: str
    comment: str

class StartRequest(BaseModel):
    url: str
    jobs: List[JobRequest]

class ProfileAction(BaseModel):
    profileId: str

class MappingRequest(BaseModel):
    profileId: str
    uid: str

class CredentialPayload(BaseModel):
    uid: str
    password: str
    secret: str

# --- ROUTES ---

@app.get("/unified_profiles")
async def get_unified_profiles():
    """
    Returns the Master List: AdsPower Profiles + Linked FB Account Data
    """
    # 1. Load Data
    credentials_manager.load_credentials()
    mapping_manager.load()
    
    # Fetch AdsPower profiles (or mocks if AP not running)
    raw_profiles = adspower.get_profile_list(page_size=100)
    
    unified_list = []
    
    # --- 1. Process AdsPower Profiles ---
    for p in raw_profiles:
        pid = p.get("user_id")
        name = p.get("name", "Unknown")
        
        # 2. Check Mapping
        linked_uid = mapping_manager.get_uid(pid)
        
        # 3. Auto-Map if missing (Name matching logic as fallback)
        if not linked_uid:
            for uid in credentials_manager.credentials.keys():
                if uid in name:
                    linked_uid = uid
                    mapping_manager.set_mapping(pid, uid) # Save it
                    break
        
        # 4. Get Account Details
        account_info = None
        if linked_uid:
            creds = credentials_manager.credentials.get(linked_uid)
            if creds:
                account_info = {
                    "uid": creds['uid'],
                    "password_masked": creds['password'][:2] + "****",
                    "has_secret": bool(creds['secret'])
                }

        unified_list.append({
            "profile_id": pid,
            "profile_name": name,
            "proxy": p.get("remark") or "N/A",
            "linked_uid": linked_uid,
            "account": account_info,
            "source": "adspower"
        })

    # --- 3. Process File-Based Sessions (For Cloud/Railway Mode) ---
    # In cloud mode, we don't need active devices if we have session cookies.
    try:
        saved_sessions = list_saved_sessions()
        for s in saved_sessions:
            p_name = s['profile_name']
            # Avoid duplicates if already found via API
            if any(u['profile_name'] == p_name for u in unified_list):
                continue
                
            # Create a "Virtual" profile for this session
            unified_list.append({
                "profile_id": f"session_{s['user_id'] or 'unknown'}",
                "profile_name": p_name,
                "proxy": "Global Proxy (Cloud)",
                "linked_uid": s['user_id'],
                "account": None, # Could link if needed
                "source": "session", # New source type
                "status": "ready" # Always ready if session exists
            })
    except Exception as e:
        logger.warning(f"Failed to load file-based sessions: {e}")
        
    return unified_list

@app.post("/map_account")
async def map_account(req: MappingRequest):
    mapping_manager.set_mapping(req.profileId, req.uid)
    return {"status": "success", "profile_id": req.profileId, "uid": req.uid}

@app.post("/unmap_account")
async def unmap_account(req: ProfileAction):
    mapping_manager.delete_mapping(req.profileId)
    return {"status": "success"}

@app.get("/otp/{uid}")
async def generate_otp(uid: str):
    creds = credentials_manager.credentials.get(uid)
    if not creds or not creds.get('secret'):
        raise HTTPException(status_code=404, detail="No secret for this UID")
    try:
        totp = pyotp.TOTP(creds['secret'].replace(" ", ""))
        return {"code": totp.now(), "uid": uid}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/credentials")
async def list_credentials():
    credentials_manager.load_credentials()
    return list(credentials_manager.credentials.values())

@app.post("/credentials")
async def add_credential(creds: CredentialPayload):
    line = f"{creds.uid}:{creds.password}:{creds.secret}\n"
    with open("../accounts info.txt", "a") as f:
        f.write(line)
    credentials_manager.load_credentials()
    return {"status": "success"}

@app.delete("/credentials/{uid}")
async def delete_credential(uid: str):
    with open("../accounts info.txt", "r") as f:
        lines = f.readlines()
    new_lines = [l for l in lines if not l.startswith(f"{uid}:")]
    with open("../accounts info.txt", "w") as f:
        f.writelines(new_lines)
    credentials_manager.load_credentials()
    return {"status": "success"}

@app.post("/launch")
async def launch_profile(req: ProfileAction):
    resp = adspower.start_profile(req.profileId)
    return {"status": "launched", "ws": resp.get("ws_endpoint", "")}

@app.get("/check_status_smart/{profile_id}")
async def check_status_smart(profile_id: str):
    """
    Checks login status AND extracts the logged-in UID.
    """
    try:
        launch_data = adspower.start_profile(profile_id)
        ws_endpoint = launch_data["ws_endpoint"]
        
        result = {"status": "unknown", "detected_uid": None}
        
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(ws_endpoint)
            context = browser.contexts[0]
            
            # Check Cookies for 'c_user'
            cookies = await context.cookies()
            c_user = next((c for c in cookies if c['name'] == 'c_user'), None)
            
            if c_user:
                result["status"] = "logged_in"
                result["detected_uid"] = c_user['value']
            else:
                # Fallback: Open page and check visible text
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto("https://m.facebook.com/", wait_until="domcontentloaded")
                await asyncio.sleep(2)
                
                # Double check cookies after load
                cookies = await context.cookies()
                c_user = next((c for c in cookies if c['name'] == 'c_user'), None)
                
                if c_user:
                    result["status"] = "logged_in"
                    result["detected_uid"] = c_user['value']
                elif await page.locator('div[data-sigil="m-area"]').count() > 0:
                    result["status"] = "logged_in"
                    result["detected_uid"] = "unknown_uid"
                else:
                    result["status"] = "logged_out"
            
        return result
        
    except Exception as e:
        logger.error(f"Status check failed: {e}")
        return {"status": "error", "error": str(e)}

@app.post("/start_campaign")
async def start_automation(req: StartRequest):
    job_manager.clear_jobs()
    for j in req.jobs:
        job_manager.add_job(j.profileId, j.profileName, j.comment)
    await job_manager.start_processing(req.url)
    return {"message": "started", "count": len(req.jobs)}

@app.get("/status")
async def get_status():
    return job_manager.get_jobs()


# ============================================
# SESSION MANAGEMENT ENDPOINTS
# ============================================

@app.get("/sessions")
async def list_sessions():
    """List all saved sessions with their status."""
    sessions = list_saved_sessions()
    return sessions


@app.post("/sessions/extract/{profile_id}")
async def extract_session(profile_id: str):
    """
    Extract session from a running AdsPower profile.
    Profile must be logged into Facebook.
    """
    result = None

    try:
        # Get profile info
        profiles = adspower.get_profile_list(page_size=100)
        target_profile = next((p for p in profiles if p.get("user_id") == profile_id), None)

        if not target_profile:
            raise HTTPException(status_code=404, detail="Profile not found")

        profile_name = target_profile.get("name", profile_id)
        proxy_info = target_profile.get("remark", "")

        # Start the profile
        launch_data = adspower.start_profile(profile_id)
        ws_endpoint = launch_data["ws_endpoint"]

        async with async_playwright() as p:
            await asyncio.sleep(3)  # Wait for browser to fully initialize
            browser = await p.chromium.connect_over_cdp(ws_endpoint)
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else await context.new_page()

            # Navigate to Facebook to get cookies
            logger.info(f"Navigating to Facebook for session extraction...")
            await page.goto("https://m.facebook.com/", wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)

            # Extract session
            session = FacebookSession(profile_name)
            await session.extract_from_page(page, adspower_id=profile_id, proxy=proxy_info)

            # Save session
            if not session.save():
                raise HTTPException(status_code=500, detail="Failed to save session")

            result = {
                "status": "success",
                "profile_name": profile_name,
                "user_id": session.get_user_id(),
                "cookies_count": len(session.get_cookies()),
                "has_valid_cookies": session.has_valid_cookies()
            }

            # CRITICAL: Disconnect browser BEFORE exiting async context
            # This prevents the race condition with adspower.stop_profile()
            logger.info("Disconnecting from browser...")
            await browser.close()

        # Return result after async context has cleaned up
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Session extraction failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Stop the profile AFTER Playwright has disconnected
        try:
            logger.info(f"Stopping AdsPower profile {profile_id}...")
            adspower.stop_profile(profile_id)
        except Exception as e:
            logger.warning(f"Failed to stop profile {profile_id}: {e}")


@app.delete("/sessions/{profile_name}")
async def delete_session(profile_name: str):
    """Delete a saved session."""
    session = FacebookSession(profile_name)
    if session.session_file.exists():
        session.session_file.unlink()
        return {"status": "success", "message": f"Session for {profile_name} deleted"}
    else:
        raise HTTPException(status_code=404, detail="Session not found")


@app.get("/sessions/{profile_name}/validate")
async def validate_session(profile_name: str):
    """
    Test if a saved session is still valid.
    Loads cookies and checks login status without AdsPower.
    """
    session = FacebookSession(profile_name)
    if not session.load():
        raise HTTPException(status_code=404, detail="Session not found")

    if not session.has_valid_cookies():
        return {"valid": False, "reason": "Missing required cookies (c_user or xs)"}

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=session.get_user_agent(),
                viewport=session.get_viewport()
            )

            # Apply cookies
            await apply_session_to_context(context, session)

            page = await context.new_page()
            is_valid = await verify_session_logged_in(page, debug=False)

            await browser.close()

            return {
                "valid": is_valid,
                "profile_name": profile_name,
                "user_id": session.get_user_id(),
                "reason": "Session is valid and logged in" if is_valid else "Session expired or invalid"
            }

    except Exception as e:
        logger.error(f"Session validation failed: {e}")
        return {"valid": False, "reason": str(e)}


# ============================================
# GEELARK ENDPOINTS
# ============================================

@app.get("/geelark/status")
async def geelark_status():
    """Check GeeLark connection status and configuration."""
    return {
        "available": GEELARK_AVAILABLE,
        "connected": geelark_client is not None,
        "enabled_in_job_manager": job_manager.geelark_enabled if hasattr(job_manager, 'geelark_enabled') else False,
    }


@app.get("/geelark/devices")
async def list_geelark_devices():
    """
    List all GeeLark cloud phone devices.
    Returns device ID, name, status, and group.
    """
    if not geelark_client:
        raise HTTPException(status_code=503, detail="GeeLark not configured")

    try:
        devices = geelark_client.list_devices()
        return [
            {
                "id": d.id,
                "name": d.name,
                "status": d.status,
                "is_online": d.is_online,
                "group_name": d.group_name,
                "tags": d.tags,
            }
            for d in devices
        ]
    except Exception as e:
        logger.error(f"Failed to list GeeLark devices: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/geelark/devices/{device_id}/start")
async def start_geelark_device(device_id: str):
    """Start a GeeLark cloud phone device."""
    if not geelark_client:
        raise HTTPException(status_code=503, detail="GeeLark not configured")

    try:
        result = geelark_client.start_device(device_id)
        return {"status": "success", "data": result}
    except Exception as e:
        logger.error(f"Failed to start device {device_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/geelark/devices/{device_id}/stop")
async def stop_geelark_device(device_id: str):
    """Stop a GeeLark cloud phone device."""
    if not geelark_client:
        raise HTTPException(status_code=503, detail="GeeLark not configured")

    try:
        result = geelark_client.stop_device(device_id)
        return {"status": "success", "data": result}
    except Exception as e:
        logger.error(f"Failed to stop device {device_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/geelark/tasks/{task_id}")
async def get_geelark_task(task_id: str):
    """Get the status of a GeeLark task."""
    if not geelark_client:
        raise HTTPException(status_code=503, detail="GeeLark not configured")

    try:
        task = geelark_client.query_task(task_id)
        return {
            "id": task.id,
            "device_id": task.device_id,
            "status": task.status,
            "status_name": task.status_name,
            "is_completed": task.is_completed,
            "is_failed": task.is_failed,
            "failure_code": task.failure_code,
        }
    except Exception as e:
        logger.error(f"Failed to get task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class GeeLarkCommentRequest(BaseModel):
    device_id: str
    post_url: str
    comment: str
    wait_for_completion: bool = False


# GeeLark API has a max URL length of ~100 characters for PostAddress
GEELARK_MAX_URL_LENGTH = 100


@app.post("/geelark/validate-url")
async def validate_geelark_url(url: str):
    """
    Validate if a Facebook URL is compatible with GeeLark API.
    Returns validation result with suggestions if URL is too long.
    """
    cleaned = clean_facebook_url(url)
    
    # Try resolving if still too long
    if not is_url_safe_for_geelark(cleaned, GEELARK_MAX_URL_LENGTH):
        cleaned = resolve_facebook_redirect(cleaned)
        # Clean again after resolve
        cleaned = clean_facebook_url(cleaned)

    is_valid = is_url_safe_for_geelark(cleaned, GEELARK_MAX_URL_LENGTH)
    
    msg = "URL is valid"
    if not is_valid:
        msg = f"URL too long ({len(cleaned)} chars > {GEELARK_MAX_URL_LENGTH} max). Even after resolving redirects, it's too long. Try a different link."
    elif len(url) > len(cleaned):
        msg = f"URL was optimized (cleaned/resolved). Original: {len(url)}, Final: {len(cleaned)}."

    return {
        "url": url,
        "cleaned_url": cleaned,
        "length": len(cleaned),
        "max_length": GEELARK_MAX_URL_LENGTH,
        "is_valid": is_valid,
        "message": msg
    }


@app.post("/geelark/comment")
async def post_geelark_comment(req: GeeLarkCommentRequest):
    """
    Post a Facebook comment using GeeLark.
    This is a direct API call to GeeLark, bypassing the job queue.

    Note: Facebook URLs with pfbid format are often too long (130+ chars).
    The GeeLark API has a max URL length of ~100 characters.
    Use shorter URL formats like: https://facebook.com/{user_id}/posts/{post_id}
    """
    if not geelark_client:
        raise HTTPException(status_code=503, detail="GeeLark not configured")

    # Auto-clean URL to meet GeeLark's 100-char limit
    cleaned_url = clean_facebook_url(req.post_url)
    if not is_url_safe_for_geelark(cleaned_url, GEELARK_MAX_URL_LENGTH):
        resolved = resolve_facebook_redirect(cleaned_url)
        cleaned_url = clean_facebook_url(resolved)

    if not is_url_safe_for_geelark(cleaned_url, GEELARK_MAX_URL_LENGTH):
        raise HTTPException(
            status_code=400,
            detail=f"URL still too long after cleaning ({len(cleaned_url)} chars). Max is {GEELARK_MAX_URL_LENGTH}."
        )

    logger.info(f"URL cleaned: {len(req.post_url)} → {len(cleaned_url)} chars")

    try:
        task = geelark_client.post_facebook_comment(
            device_id=req.device_id,
            post_url=cleaned_url,
            comment=req.comment,
            wait_for_completion=req.wait_for_completion,
            timeout=120
        )
        return {
            "status": "success",
            "task_id": task.id,
            "task_status": task.status_name,
            "is_completed": task.is_completed,
        }
    except Exception as e:
        logger.error(f"Failed to post comment: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/profiles")
async def get_all_profiles():
    """
    Get all available profiles from both AdsPower and GeeLark.
    This is the unified endpoint for the frontend.
    """
    profiles = []

    # Get AdsPower profiles
    try:
        adspower_profiles = adspower.get_profile_list(page_size=100)
        for p in adspower_profiles:
            profiles.append({
                "id": p.get("user_id"),
                "name": p.get("name", "Unknown"),
                "source": "adspower",
                "status": "unknown",
                "proxy": p.get("remark") or "N/A",
            })
    except Exception as e:
        logger.warning(f"Failed to get AdsPower profiles: {e}")

    # Get GeeLark devices
    if geelark_client:
        try:
            devices = geelark_client.list_devices()
            for d in devices:
                profiles.append({
                    "id": d.id,
                    "name": d.name,
                    "source": "geelark",
                    "status": d.status,
                    "is_online": d.is_online,
                    "group_name": d.group_name,
                })
        except Exception as e:
            logger.warning(f"Failed to get GeeLark devices: {e}")

    return profiles


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)