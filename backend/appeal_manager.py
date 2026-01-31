"""
Batch Restriction Appeal Manager.
Orchestrates appeals for restricted profiles using the Adaptive Agent.
Handles verification, scenario detection, retries, and state tracking.
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, List

logger = logging.getLogger("AppealManager")

# Global lock: prevents concurrent appeal/verify batches (scheduler + manual)
_appeal_lock = asyncio.Lock()

VERIFY_TASK_PROMPT = """Check if this Facebook account has an ACTIVE restriction:

1. Click the notifications bell icon at top of page.
2. Look for ANY restriction notice ("We removed your comment", "We added restrictions", etc).
3. If found, click "See why" to view restriction details.
4. READ THE PAGE CAREFULLY and report:
   - If "Ended on [any date]" visible -> DONE reason="RESOLVED: restriction ended"
   - If "How we made this decision" with no active restriction language -> DONE reason="RESOLVED: past restriction, no longer active"
   - If "In review" or "We're reviewing" -> DONE reason="IN_REVIEW: appeal already submitted"
   - If "Request review" button visible -> DONE reason="ACTIVE: restriction active, appeal available"
   - If active restriction text but no review option -> DONE reason="ACTIVE: restriction active, no appeal option"
5. If NO restriction notices found after scrolling notifications:
   -> DONE reason="RESOLVED: no restriction notices found"

RULES:
- This is CHECK ONLY. Do NOT click "Request review".
- READ dates on the page. "Ended on Jan 21" means restriction is OVER.
- Login page -> FAILED reason="Session expired"
- Checkpoint/verification page -> FAILED reason="Checkpoint detected"
- NEVER repeat same action more than 2 times
"""

COMMENT_CHECK_PROMPT = """Check if this account can comment on Facebook:
1. Navigate to https://m.facebook.com
2. Find any post with a comment button.
3. Tap the comment button/icon.
4. If comment input box appears -> DONE reason="CAN_COMMENT: comment input available"
5. If restriction/blocked message appears -> DONE reason="BLOCKED: commenting is blocked"
6. If no posts or can't find comment button -> DONE reason="UNCLEAR: could not determine"
RULES: Do NOT actually post a comment. Just check if the UI allows it.
"""

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
   - If "Ended on [any date]" visible -> DONE reason="Restriction already resolved"
   - If "How we made this decision" with no active restriction -> DONE reason="Restriction already resolved"
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
- No restriction notices after scrolling notifications -> DONE reason="No active restriction found"
- READ dates on the page. "Ended on Jan 21" means restriction is OVER.
- Click fails 2x -> scroll slightly then click again. Still fails -> try different approach or FAILED
- Login page -> FAILED reason="Session expired - login required"
- Checkpoint/verification page -> FAILED reason="Account checkpoint detected"
- NEVER repeat the same failed click more than 2 times - try something different or FAILED
"""

MAX_APPEAL_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 30

RESOLVED_SIGNALS = [
    "resolved", "no longer active", "restriction ended",
    "already resolved", "no active restriction",
    "restriction may be lifted", "no restriction notices",
]

ACTIVE_SIGNALS = ["active: restriction active"]
IN_REVIEW_SIGNALS = ["in_review:", "appeal already submitted", "already in review"]


def _scan_steps_for_signals(result: Dict, signals: List[str]) -> bool:
    """Check if any agent step contains any of the given signal strings."""
    for step in result.get("steps", []):
        combined = (
            str(step.get("action_taken", "")).lower() + " " +
            str(step.get("reasoning", "")).lower()
        )
        if any(s in combined for s in signals):
            return True
    return False


def _parse_verify_status(result: Dict) -> str:
    """Extract RESOLVED/ACTIVE/IN_REVIEW/UNKNOWN from verify agent result."""
    # Check the last DONE/FAILED action for structured reason
    for step in reversed(result.get("steps", [])):
        action = str(step.get("action_taken", ""))
        if "reason=" in action:
            parts = action.split("reason=", 1)
            if len(parts) > 1:
                reason = parts[1].strip().strip('"')
                # Return the structured prefix (RESOLVED:, ACTIVE:, IN_REVIEW:, etc.)
                return reason

    # Fallback: scan all steps for signal keywords
    if _scan_steps_for_signals(result, RESOLVED_SIGNALS):
        return "RESOLVED: detected from agent reasoning"
    if _scan_steps_for_signals(result, IN_REVIEW_SIGNALS):
        return "IN_REVIEW: detected from agent reasoning"
    if _scan_steps_for_signals(result, ACTIVE_SIGNALS):
        return "ACTIVE: detected from agent reasoning"

    return "UNKNOWN: could not determine restriction status"


def _extract_failure_reason(result: Dict) -> str:
    """Extract failure reason from adaptive agent result."""
    for step in reversed(result.get("steps", [])):
        action = str(step.get("action_taken", ""))
        if "FAILED" in action:
            parts = action.split("reason=")
            if len(parts) > 1:
                return parts[1].strip().strip('"')
            return action
    errors = result.get("errors", [])
    return errors[-1] if errors else "Unknown failure"


async def verify_single_profile(profile_name: str) -> Dict[str, Any]:
    """Verify if a profile's restriction is still active on Facebook."""
    from adaptive_agent import run_adaptive_task
    from profile_manager import get_profile_manager
    from fb_session import remove_session_tag, append_session_tag

    pm = get_profile_manager()
    prefix = f"[VERIFY:{profile_name}]"

    state = pm.get_profile_state(profile_name)
    if not state:
        return {"profile_name": profile_name, "verified_status": "ERROR: profile not found", "action_taken": "none"}

    logger.info(f"{prefix} Verifying restriction status")

    try:
        result = await run_adaptive_task(profile_name=profile_name, task=VERIFY_TASK_PROMPT, max_steps=10)
        verified_status = _parse_verify_status(result)
        steps_used = len(result.get("steps", []))
        action_taken = "none"

        if verified_status.startswith("RESOLVED"):
            pm.unblock_profile(profile_name)
            remove_session_tag(profile_name, "appeal_pending")
            action_taken = "auto_unblocked"
            logger.info(f"{prefix} {verified_status} -> auto-unblocked")
        elif verified_status.startswith("IN_REVIEW"):
            pm.update_appeal_state(profile_name, "task_completed")
            append_session_tag(profile_name, "appeal_pending")
            action_taken = "marked_in_review"
            logger.info(f"{prefix} {verified_status} -> marked in_review")
        elif verified_status.startswith("ACTIVE"):
            action_taken = "confirmed_restricted"
            logger.info(f"{prefix} {verified_status} -> still restricted")
        elif verified_status.startswith("UNKNOWN"):
            # Fallback: try comment check
            logger.info(f"{prefix} Status unclear, running comment check fallback")
            action_taken, verified_status = await _comment_check_fallback(profile_name, pm)
        else:
            logger.warning(f"{prefix} Unexpected status: {verified_status}")

        return {
            "profile_name": profile_name,
            "verified_status": verified_status,
            "action_taken": action_taken,
            "steps_used": steps_used,
        }

    except Exception as e:
        logger.error(f"{prefix} Exception: {e}")
        return {"profile_name": profile_name, "verified_status": f"ERROR: {e}", "action_taken": "none"}


async def _comment_check_fallback(profile_name: str, pm) -> tuple:
    """Fallback: check if profile can comment to determine restriction status."""
    from adaptive_agent import run_adaptive_task
    from fb_session import remove_session_tag

    prefix = f"[VERIFY:{profile_name}]"
    try:
        result = await run_adaptive_task(profile_name=profile_name, task=COMMENT_CHECK_PROMPT, max_steps=8)
        status = _parse_verify_status(result)

        if "CAN_COMMENT" in status.upper():
            pm.unblock_profile(profile_name)
            remove_session_tag(profile_name, "appeal_pending")
            logger.info(f"{prefix} Comment check: can comment -> auto-unblocked")
            return "auto_unblocked", "RESOLVED: comment check passed - can comment"
        elif "BLOCKED" in status.upper():
            logger.info(f"{prefix} Comment check: blocked -> confirmed restricted")
            return "confirmed_restricted", "ACTIVE: comment check confirmed restriction"
        else:
            logger.info(f"{prefix} Comment check: unclear -> keeping as restricted")
            return "unclear", "UNKNOWN: comment check inconclusive"
    except Exception as e:
        logger.error(f"{prefix} Comment check exception: {e}")
        return "error", f"ERROR: comment check failed: {e}"


async def appeal_single_profile(profile_name: str) -> Dict[str, Any]:
    """Run appeal for a single profile with scenario detection."""
    from adaptive_agent import run_adaptive_task
    from profile_manager import get_profile_manager
    from fb_session import append_session_tag

    pm = get_profile_manager()
    prefix = f"[APPEAL:{profile_name}]"

    state = pm.get_profile_state(profile_name)
    if not state:
        logger.warning(f"{prefix} Profile not found in state")
        return {"profile_name": profile_name, "success": False, "scenario": "not_found", "error": "Profile not in state"}

    scenario = pm.classify_restriction(profile_name)
    logger.info(f"{prefix} Scenario: {scenario}")

    if scenario == "checkpoint":
        error = "Account checkpoint - manual CAPTCHA solve required"
        pm.update_appeal_state(profile_name, "task_failed", error=error)
        append_session_tag(profile_name, "needs_captcha")
        logger.warning(f"{prefix} {error}")
        return {"profile_name": profile_name, "success": False, "scenario": scenario, "error": error}

    if scenario == "expired":
        pm.unblock_profile(profile_name)
        logger.info(f"{prefix} Restriction expired - auto-unblocked")
        return {"profile_name": profile_name, "success": True, "scenario": scenario, "final_status": "auto_unblocked"}

    logger.info(f"{prefix} Running appeal via adaptive agent")
    try:
        result = await run_adaptive_task(profile_name=profile_name, task=APPEAL_TASK_PROMPT, max_steps=15)
        final_status = result.get("final_status", "unknown")
        steps_used = len(result.get("steps", []))
        error = None

        # Check for restriction-resolved signals (priority)
        if _scan_steps_for_signals(result, RESOLVED_SIGNALS):
            from fb_session import remove_session_tag
            pm.unblock_profile(profile_name)
            remove_session_tag(profile_name, "appeal_pending")
            logger.info(f"{prefix} Restriction resolved during appeal - auto-unblocked")
            return {
                "profile_name": profile_name, "success": True,
                "scenario": "restriction_resolved", "final_status": "auto_unblocked",
                "steps_used": steps_used,
            }

        # Check if already in review
        already_in_review = _scan_steps_for_signals(result, IN_REVIEW_SIGNALS)

        if final_status == "task_completed" or already_in_review:
            pm.update_appeal_state(profile_name, "task_completed", steps_used=steps_used)
            append_session_tag(profile_name, "appeal_pending")
            logger.info(f"{prefix} Appeal submitted (already_in_review={already_in_review})")
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
            "scenario": scenario, "final_status": final_status,
            "already_in_review": already_in_review, "steps_used": steps_used, "error": error,
        }

    except Exception as e:
        error_msg = str(e)
        pm.update_appeal_state(profile_name, "error", error=error_msg)
        logger.error(f"{prefix} Exception: {error_msg}")
        return {"profile_name": profile_name, "success": False, "scenario": scenario, "error": error_msg}


async def verify_all_restricted(skip_profiles: List[str] = None) -> Dict[str, Any]:
    """Verify ALL restricted profiles and auto-unblock resolved ones.
    skip_profiles: profiles currently in use (e.g. by queue processor) — skip them.
    Acquires global appeal lock; returns busy status if lock held."""
    if _appeal_lock.locked():
        return {"status": "busy", "message": "Appeal batch already running"}
    async with _appeal_lock:
        return await _verify_all_restricted_inner(skip_profiles)


async def _verify_all_restricted_inner(skip_profiles: List[str] = None) -> Dict[str, Any]:
    from profile_manager import get_profile_manager

    pm = get_profile_manager()
    batch_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    logger.info(f"[VERIFY_ALL:{batch_id}] Starting verification")

    skip_set = set(skip_profiles or [])
    restricted = [
        name for name, state in pm.get_all_profiles().items()
        if state.get("status") == "restricted" and name not in skip_set
    ]
    if skip_set:
        logger.info(f"[VERIFY_ALL:{batch_id}] Skipping {len(skip_set)} profiles in use")
    logger.info(f"[VERIFY_ALL:{batch_id}] Found {len(restricted)} restricted profiles")

    if not restricted:
        return {"batch_id": batch_id, "total": 0, "results": [], "message": "No restricted profiles"}

    tasks = [verify_single_profile(p) for p in restricted]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    parsed = []
    for r in results:
        if isinstance(r, Exception):
            parsed.append({"profile_name": "unknown", "verified_status": f"ERROR: {r}", "action_taken": "none"})
        else:
            parsed.append(r)

    unblocked = sum(1 for r in parsed if r.get("action_taken") == "auto_unblocked")
    in_review = sum(1 for r in parsed if r.get("action_taken") == "marked_in_review")
    still_restricted = sum(1 for r in parsed if r.get("action_taken") == "confirmed_restricted")

    summary = {
        "batch_id": batch_id, "total": len(restricted),
        "unblocked": unblocked, "in_review": in_review,
        "still_restricted": still_restricted, "results": parsed,
    }
    logger.info(
        f"[VERIFY_ALL:{batch_id}] Done: {unblocked} unblocked, {in_review} in_review, "
        f"{still_restricted} still restricted"
    )
    return summary


async def batch_appeal_all(
    max_attempts: int = MAX_APPEAL_ATTEMPTS,
    retry_failed: bool = True,
    skip_profiles: List[str] = None
) -> Dict[str, Any]:
    """Appeal ALL restricted profiles: verify first, then appeal confirmed active.
    Acquires global appeal lock; returns busy status if lock held."""
    if _appeal_lock.locked():
        return {"status": "busy", "message": "Appeal batch already running"}
    async with _appeal_lock:
        return await _batch_appeal_all_inner(max_attempts, retry_failed, skip_profiles)


async def _batch_appeal_all_inner(
    max_attempts: int = MAX_APPEAL_ATTEMPTS,
    retry_failed: bool = True,
    skip_profiles: List[str] = None
) -> Dict[str, Any]:
    from profile_manager import get_profile_manager

    pm = get_profile_manager()
    batch_id = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    logger.info(f"[APPEAL_BATCH:{batch_id}] Starting batch appeal")

    all_results = []

    # Reset exhausted profiles so they can be re-verified
    for name, state in pm.get_all_profiles().items():
        if state.get("status") == "restricted" and state.get("appeal_status") == "exhausted":
            pm.update_appeal_state(name, "none")
            logger.info(f"[APPEAL_BATCH:{batch_id}] Reset exhausted profile: {name}")

    # Get all restricted profiles (skip profiles currently in use)
    skip_set = set(skip_profiles or [])
    restricted = {
        name: state for name, state in pm.get_all_profiles().items()
        if state.get("status") == "restricted" and name not in skip_set
    }
    logger.info(f"[APPEAL_BATCH:{batch_id}] Found {len(restricted)} restricted profiles")

    if not restricted:
        return {"batch_id": batch_id, "total_profiles": 0, "results": [], "message": "No restricted profiles found"}

    # Categorize by scenario (expired/checkpoint handled immediately)
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

    # Handle expired (instant unblock)
    for name in expired:
        result = await appeal_single_profile(name)
        all_results.append(result)

    # Handle checkpoint (mark as failed)
    for name in checkpoint:
        result = await appeal_single_profile(name)
        all_results.append(result)

    # VERIFY comment_restriction profiles first
    if comment_restriction:
        logger.info(f"[APPEAL_BATCH:{batch_id}] Verifying {len(comment_restriction)} profiles before appeal")
        verify_tasks = [verify_single_profile(p) for p in comment_restriction]
        verify_results = await asyncio.gather(*verify_tasks, return_exceptions=True)

        profiles_to_appeal = []
        for vr in verify_results:
            if isinstance(vr, Exception):
                profiles_to_appeal.append(comment_restriction[verify_results.index(vr)])
                continue
            status = vr.get("verified_status", "")
            action = vr.get("action_taken", "")
            all_results.append({
                "profile_name": vr.get("profile_name"), "success": action == "auto_unblocked" or action == "marked_in_review",
                "scenario": "verify_" + action, "final_status": action, "verified_status": status,
                "steps_used": vr.get("steps_used", 0),
            })
            if action == "confirmed_restricted":
                profiles_to_appeal.append(vr["profile_name"])

        logger.info(f"[APPEAL_BATCH:{batch_id}] After verify: {len(profiles_to_appeal)} confirmed active to appeal")
    else:
        profiles_to_appeal = []

    # Appeal confirmed-active restrictions with retries
    round_num = 0
    while profiles_to_appeal and round_num < max_attempts:
        round_num += 1
        logger.info(f"[APPEAL_BATCH:{batch_id}] Appeal round {round_num}: {len(profiles_to_appeal)} profiles")

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
            "expired": len(expired), "checkpoint": len(checkpoint),
            "comment_restriction": len(comment_restriction),
        },
        "successful": successful, "failed": failed, "results": all_results,
    }

    logger.info(
        f"[APPEAL_BATCH:{batch_id}] Complete: {successful} success, {failed} failed "
        f"across {round_num} appeal rounds"
    )
    return summary
