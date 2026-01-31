"""
Batch Restriction Appeal Manager.
Orchestrates appeals for restricted profiles using the Adaptive Agent.
Handles scenario detection, retries, and state tracking.
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, List

logger = logging.getLogger("AppealManager")

APPEAL_TASK_PROMPT = """Appeal a Facebook restriction on this account:

1. Click the notifications bell icon at top of page.
2. Look for ANY restriction notice in notifications:
   - "We removed your comment"
   - "We added restrictions to your account"
   - "Your comment didn't follow our Community Standards"
   - Any message about restrictions, violations, or removed content
3. Click "See why" on the restriction notice.
4. Check the page:
   - If "In review" or "We're reviewing your request" visible -> DONE reason="Appeal already in review"
   - If "Request review" button visible -> click it
   - If no review option, only expiry date -> FAILED reason="No request review option available"
5. After clicking "Request review":
   - Select ANY reason from the list (first option is fine)
   - Click "Continue"
   - If a second reason selection appears, pick any option and click "Continue" again
   - Click "Submit" if visible
6. Verify the page shows "In review" or "Thanks for requesting a review"
7. DONE reason="Appeal submitted successfully"

RULES:
- No restriction notices after scrolling notifications -> FAILED reason="No restriction notices found in notifications"
- Click fails 2x -> scroll slightly then click again. Still fails -> try different approach or FAILED
- Login page -> FAILED reason="Session expired - login required"
- Checkpoint/verification page -> FAILED reason="Account checkpoint detected"
- NEVER repeat the same failed click more than 2 times - try something different or FAILED
"""

MAX_APPEAL_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 30


async def appeal_single_profile(profile_name: str) -> Dict[str, Any]:
    """Run appeal for a single profile with scenario detection."""
    from adaptive_agent import run_adaptive_task
    from profile_manager import get_profile_manager
    from fb_session import append_session_tag

    pm = get_profile_manager()
    prefix = f"[APPEAL:{profile_name}]"

    # Get profile state
    state = pm.get_profile_state(profile_name)
    if not state:
        logger.warning(f"{prefix} Profile not found in state")
        return {"profile_name": profile_name, "success": False, "scenario": "not_found", "error": "Profile not in state"}

    # Scenario detection
    scenario = pm.classify_restriction(profile_name)
    logger.info(f"{prefix} Scenario: {scenario}")

    # CHECKPOINT: can't auto-appeal
    if scenario == "checkpoint":
        error = "Account checkpoint - manual CAPTCHA solve required"
        pm.update_appeal_state(profile_name, "task_failed", error=error)
        append_session_tag(profile_name, "needs_captcha")
        logger.warning(f"{prefix} {error}")
        return {"profile_name": profile_name, "success": False, "scenario": scenario, "error": error}

    # EXPIRED: just unblock
    if scenario == "expired":
        pm.unblock_profile(profile_name)
        logger.info(f"{prefix} Restriction expired - auto-unblocked")
        return {"profile_name": profile_name, "success": True, "scenario": scenario, "final_status": "auto_unblocked"}

    # COMMENT_RESTRICTION: run adaptive agent
    logger.info(f"{prefix} Running appeal via adaptive agent")
    try:
        result = await run_adaptive_task(
            profile_name=profile_name,
            task=APPEAL_TASK_PROMPT,
            max_steps=15
        )

        final_status = result.get("final_status", "unknown")
        steps_used = len(result.get("steps", []))
        error = None

        # Check if already in review (agent found existing appeal)
        already_in_review = False
        for step in result.get("steps", []):
            action = str(step.get("action_taken", "")).lower()
            reasoning = str(step.get("reasoning", "")).lower()
            if "already in review" in action or "already in review" in reasoning:
                already_in_review = True
                break

        if final_status == "task_completed" or already_in_review:
            pm.update_appeal_state(profile_name, "task_completed", steps_used=steps_used)
            append_session_tag(profile_name, "appeal_pending")
            logger.info(f"{prefix} Appeal submitted successfully (already_in_review={already_in_review})")
        elif final_status == "task_failed":
            error = _extract_failure_reason(result)
            pm.update_appeal_state(profile_name, "task_failed", error=error, steps_used=steps_used)
            logger.warning(f"{prefix} Appeal failed: {error}")
        elif final_status == "max_steps_reached":
            error = "Max steps reached without completing appeal"
            pm.update_appeal_state(profile_name, "max_steps_reached", error=error, steps_used=steps_used)
            logger.warning(f"{prefix} {error}")
        else:
            error = f"Unexpected status: {final_status}"
            pm.update_appeal_state(profile_name, "error", error=error, steps_used=steps_used)
            logger.error(f"{prefix} {error}")

        return {
            "profile_name": profile_name,
            "success": final_status == "task_completed" or already_in_review,
            "scenario": scenario,
            "final_status": final_status,
            "already_in_review": already_in_review,
            "steps_used": steps_used,
            "error": error
        }

    except Exception as e:
        error_msg = str(e)
        pm.update_appeal_state(profile_name, "error", error=error_msg)
        logger.error(f"{prefix} Exception: {error_msg}")
        return {"profile_name": profile_name, "success": False, "scenario": scenario, "error": error_msg}


def _extract_failure_reason(result: Dict) -> str:
    """Extract failure reason from adaptive agent result."""
    for step in reversed(result.get("steps", [])):
        action = str(step.get("action_taken", ""))
        if "FAILED" in action:
            # Extract reason after FAILED
            parts = action.split("reason=")
            if len(parts) > 1:
                return parts[1].strip().strip('"')
            return action
    errors = result.get("errors", [])
    return errors[-1] if errors else "Unknown failure"


async def batch_appeal_all(
    max_attempts: int = MAX_APPEAL_ATTEMPTS,
    retry_failed: bool = True
) -> Dict[str, Any]:
    """Appeal ALL restricted profiles with scenario detection and retries."""
    from profile_manager import get_profile_manager

    pm = get_profile_manager()
    batch_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    logger.info(f"[APPEAL_BATCH:{batch_id}] Starting batch appeal")

    all_results = []

    # Get all restricted profiles
    restricted = {
        name: state for name, state in pm.get_all_profiles().items()
        if state.get("status") == "restricted"
    }
    logger.info(f"[APPEAL_BATCH:{batch_id}] Found {len(restricted)} restricted profiles")

    if not restricted:
        return {
            "batch_id": batch_id,
            "total_profiles": 0,
            "results": [],
            "message": "No restricted profiles found"
        }

    # Categorize by scenario
    expired = []
    checkpoint = []
    comment_restriction = []

    for name in restricted:
        scenario = pm.classify_restriction(name)
        if scenario == "expired":
            expired.append(name)
        elif scenario == "checkpoint":
            checkpoint.append(name)
        else:
            comment_restriction.append(name)

    logger.info(
        f"[APPEAL_BATCH:{batch_id}] Scenarios: "
        f"{len(expired)} expired, {len(checkpoint)} checkpoint, {len(comment_restriction)} comment_restriction"
    )

    # Handle expired profiles (instant unblock)
    for name in expired:
        result = await appeal_single_profile(name)
        all_results.append(result)

    # Handle checkpoint profiles (mark as failed)
    for name in checkpoint:
        result = await appeal_single_profile(name)
        all_results.append(result)

    # Handle comment restrictions with retries
    profiles_to_appeal = comment_restriction[:]
    round_num = 0

    while profiles_to_appeal and round_num < max_attempts:
        round_num += 1
        logger.info(f"[APPEAL_BATCH:{batch_id}] Round {round_num}: {len(profiles_to_appeal)} profiles")

        # Run concurrently (semaphore in run_adaptive_task limits to 5)
        tasks = [appeal_single_profile(p) for p in profiles_to_appeal]
        round_results = await asyncio.gather(*tasks, return_exceptions=True)

        failed_profiles = []
        for r in round_results:
            if isinstance(r, Exception):
                r = {"profile_name": "unknown", "success": False, "error": str(r)}
            all_results.append(r)

            if not r.get("success") and retry_failed:
                pname = r.get("profile_name")
                if pname and pname != "unknown":
                    state = pm.get_profile_state(pname)
                    if state and state.get("appeal_attempts", 0) < max_attempts:
                        failed_profiles.append(pname)

        if not failed_profiles or not retry_failed:
            break

        profiles_to_appeal = failed_profiles
        logger.info(f"[APPEAL_BATCH:{batch_id}] Waiting {RETRY_DELAY_SECONDS}s before retry")
        await asyncio.sleep(RETRY_DELAY_SECONDS)

    # Summary
    successful = sum(1 for r in all_results if r.get("success"))
    failed = sum(1 for r in all_results if not r.get("success"))
    unique_profiles = set(r.get("profile_name") for r in all_results)

    summary = {
        "batch_id": batch_id,
        "total_profiles": len(unique_profiles),
        "total_attempts": len(all_results),
        "rounds": round_num,
        "scenarios": {
            "expired": len(expired),
            "checkpoint": len(checkpoint),
            "comment_restriction": len(comment_restriction)
        },
        "successful": successful,
        "failed": failed,
        "results": all_results
    }

    logger.info(
        f"[APPEAL_BATCH:{batch_id}] Complete: {successful} success, {failed} failed "
        f"across {round_num} rounds"
    )
    return summary
