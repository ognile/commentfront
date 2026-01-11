"""
CommentBot API - Streamlined Facebook Comment Automation
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict
import logging
import os
import asyncio

from comment_bot import post_comment, test_session, MOBILE_VIEWPORT, DEFAULT_USER_AGENT
from fb_session import FacebookSession, list_saved_sessions

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("API")

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Get proxy from environment
PROXY_URL = os.getenv("PROXY_URL", "")


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


# Endpoints
@app.get("/")
async def root():
    return {"status": "ok", "message": "CommentBot API"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


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
            valid=s["has_valid_cookies"]
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
    
    result = await post_comment(
        session=session,
        url=request.url,
        comment=request.comment,
        proxy=PROXY_URL if PROXY_URL else None
    )
    
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result["error"])
    
    return result


@app.post("/campaign")
async def run_campaign(request: CampaignRequest) -> Dict:
    """Run a campaign with multiple comments."""
    results = []
    
    for i, profile_name in enumerate(request.profile_names):
        if i >= len(request.comments):
            break
        
        comment = request.comments[i]
        session = FacebookSession(profile_name)
        
        if not session.load():
            results.append({
                "profile_name": profile_name,
                "success": False,
                "error": "Session not found"
            })
            continue
        
        result = await post_comment(
            session=session,
            url=request.url,
            comment=comment,
            proxy=PROXY_URL if PROXY_URL else None
        )
        
        results.append({
            "profile_name": profile_name,
            "comment": comment,
            "success": result["success"],
            "error": result.get("error")
        })
        
        # Wait between comments
        await asyncio.sleep(2)
    
    return {
        "url": request.url,
        "total": len(results),
        "success": sum(1 for r in results if r["success"]),
        "results": results
    }


@app.get("/config")
async def get_config() -> Dict:
    """Get current configuration."""
    return {
        "proxy_url": PROXY_URL if PROXY_URL else None,
        "viewport": MOBILE_VIEWPORT,
        "user_agent": DEFAULT_USER_AGENT
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
