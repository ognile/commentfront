"""
Workflow Functions Module

High-level workflow functions that combine multiple capabilities
for complex multi-step automation tasks.
"""

import logging
from pathlib import Path
from typing import Dict, Any, Optional

from gemini_image_gen import (
    generate_profile_photo_for_persona,
    generate_profile_photo_with_reference,
    get_random_pose,
    get_pose_by_name,
    POSE_VARIATIONS
)
from adaptive_agent import run_adaptive_task
from fb_session import FacebookSession, list_saved_sessions

logger = logging.getLogger(__name__)


async def update_profile_photo(
    profile_name: str,
    persona_description: str
) -> Dict[str, Any]:
    """
    Complete workflow: Generate AI photo + Upload to Facebook profile.

    This workflow:
    1. Generates a hyper-realistic AI selfie using Gemini 2.5 Flash Image
    2. Uses the Adaptive Agent to navigate Facebook and upload the photo
    3. Cleans up the temporary image file

    Args:
        profile_name: Facebook profile to update (must exist in sessions)
        persona_description: Natural language description of the person
            e.g., "friendly middle-aged white woman with brown hair"

    Returns:
        Dict with:
            - profile_name: The profile that was updated
            - persona_description: The description used
            - image_generation: Result from image generation step
            - profile_upload: Result from upload step
            - success: Overall success boolean
            - error: Error message if failed
    """
    result = {
        "profile_name": profile_name,
        "persona_description": persona_description,
        "image_generation": None,
        "profile_upload": None,
        "success": False
    }

    logger.info(f"[WORKFLOW] Starting profile photo update for {profile_name}")
    logger.info(f"[WORKFLOW] Persona: {persona_description}")

    # =========================================================================
    # Step 1: Generate AI photo
    # =========================================================================
    logger.info(f"[WORKFLOW] Step 1: Generating AI profile photo...")

    try:
        image_result = await generate_profile_photo_for_persona(
            persona_description=persona_description,
            profile_name=profile_name.replace(" ", "_").lower()
        )
        result["image_generation"] = image_result

        if not image_result.get("success"):
            error_msg = f"Image generation failed: {image_result.get('error')}"
            logger.error(f"[WORKFLOW] {error_msg}")
            result["error"] = error_msg
            return result

        image_path = image_result["image_path"]
        logger.info(f"[WORKFLOW] Image generated: {image_path}")

    except Exception as e:
        error_msg = f"Image generation exception: {str(e)}"
        logger.error(f"[WORKFLOW] {error_msg}")
        result["error"] = error_msg
        result["image_generation"] = {"success": False, "error": str(e)}
        return result

    # =========================================================================
    # Step 2: Upload via Adaptive Agent
    # =========================================================================
    logger.info(f"[WORKFLOW] Step 2: Uploading photo via Adaptive Agent...")

    task = """Update your Facebook profile picture:

1. You are on your profile page. Look for your current profile picture at the top.
2. Tap on your profile picture (the circular photo at the top of the page).
3. You should see options like "Select Profile Picture", "Upload Photo", or "Edit".
4. Select "Upload Photo" or similar option to upload a new photo.
5. When prompted to choose a file, use UPLOAD element="Upload" or UPLOAD element="Choose" to select the photo.
6. After the photo uploads, you may need to crop or adjust it.
7. Tap "Save", "Done", or "Confirm" to save the new profile picture.
8. Once saved, use DONE to complete the task.

IMPORTANT:
- Use UPLOAD action when you see a file/photo selection interface
- Look for buttons labeled "Upload Photo", "Choose Photo", "Select Photo", "Camera Roll"
- After uploading, look for "Save", "Done", "Confirm", or checkmark buttons
"""

    try:
        upload_result = await run_adaptive_task(
            profile_name=profile_name,
            task=task,
            max_steps=20,
            start_url="https://m.facebook.com/me",
            upload_file_path=image_path
        )
        result["profile_upload"] = upload_result

        # Check success based on final status
        if upload_result.get("final_status") == "task_completed":
            result["success"] = True
            logger.info(f"[WORKFLOW] Profile photo upload completed successfully!")
        elif upload_result.get("final_status") == "max_steps_reached":
            # Check if we BOTH uploaded the file AND clicked save/update
            # Just uploading the file is NOT enough - we need to confirm the save
            has_upload = False
            has_save_click = False
            for step in upload_result.get("steps", []):
                action = str(step.get("action_taken", "")).upper()
                if "UPLOAD" in action and "FAILED" not in action:
                    has_upload = True
                # Check if Update/Save/Confirm/Done was clicked AFTER upload
                if has_upload and "CLICK" in action:
                    element = str(step.get("action_taken", "")).lower()
                    if any(word in element for word in ["update", "save", "confirm", "done"]):
                        has_save_click = True
                        break

            if has_upload and has_save_click:
                result["success"] = True
                logger.info(f"[WORKFLOW] Photo uploaded and saved (max_steps_reached but workflow completed)")
            else:
                logger.warning(f"[WORKFLOW] Max steps reached - upload={has_upload}, save_clicked={has_save_click}")
        else:
            logger.warning(f"[WORKFLOW] Upload ended with status: {upload_result.get('final_status')}")

    except Exception as e:
        error_msg = f"Upload exception: {str(e)}"
        logger.error(f"[WORKFLOW] {error_msg}")
        result["error"] = error_msg
        result["profile_upload"] = {"error": str(e), "final_status": "error"}

    # =========================================================================
    # Step 3: Update session thumbnail with the generated image
    # =========================================================================
    if result["success"]:
        try:
            import base64
            with open(image_path, "rb") as f:
                new_base64 = base64.b64encode(f.read()).decode("utf-8")
            session = FacebookSession(profile_name)
            if session.load():
                session.data["profile_picture"] = new_base64
                session.save()
                result["session_updated"] = True
                logger.info(f"[WORKFLOW] Session thumbnail updated for {profile_name}")
        except Exception as e:
            logger.warning(f"[WORKFLOW] Failed to update session thumbnail: {e}")

    # =========================================================================
    # Cleanup: Remove temporary image file
    # =========================================================================
    try:
        Path(image_path).unlink(missing_ok=True)
        logger.info(f"[WORKFLOW] Cleaned up temp file: {image_path}")
    except Exception as e:
        logger.warning(f"[WORKFLOW] Failed to cleanup temp file: {e}")

    # Final logging
    if result["success"]:
        logger.info(f"[WORKFLOW] SUCCESS: Profile photo updated for {profile_name}")
    else:
        logger.warning(f"[WORKFLOW] FAILED: Profile photo update for {profile_name}")

    return result


# Additional workflow functions can be added here

async def batch_update_profile_photos(
    profiles: list[Dict[str, str]]
) -> Dict[str, Any]:
    """
    Update profile photos for multiple profiles.

    Args:
        profiles: List of dicts with 'profile_name' and 'persona_description'

    Returns:
        Dict with results for each profile
    """
    results = {
        "total": len(profiles),
        "successful": 0,
        "failed": 0,
        "results": []
    }

    for profile in profiles:
        profile_name = profile.get("profile_name")
        persona_description = profile.get("persona_description")

        if not profile_name or not persona_description:
            results["results"].append({
                "profile_name": profile_name,
                "success": False,
                "error": "Missing profile_name or persona_description"
            })
            results["failed"] += 1
            continue

        result = await update_profile_photo(
            profile_name=profile_name,
            persona_description=persona_description
        )

        results["results"].append(result)
        if result.get("success"):
            results["successful"] += 1
        else:
            results["failed"] += 1

    return results


async def regenerate_profile_photo_with_pose(
    profile_name: str,
    pose_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Regenerate profile photo using existing face as reference.
    Creates a new photo of the same person in a different pose/setting.

    This workflow:
    1. Loads current profile picture from session (as reference)
    2. Generates new image with same person in new pose using Gemini
    3. Uploads the new photo to Facebook via Adaptive Agent
    4. Updates the session file with new profile picture

    Args:
        profile_name: Facebook profile to update (must exist in sessions)
        pose_name: Specific pose to use (e.g., "beach", "gym_mirror").
                   If None, picks a random pose.

    Returns:
        Dict with:
            - profile_name: The profile that was updated
            - pose_used: Name and prompt of the pose
            - image_generation: Result from image generation step
            - profile_upload: Result from upload step
            - session_updated: Whether session file was updated
            - success: Overall success boolean
            - error: Error message if failed
    """
    result = {
        "profile_name": profile_name,
        "pose_used": None,
        "image_generation": None,
        "profile_upload": None,
        "session_updated": False,
        "success": False
    }

    logger.info(f"[WORKFLOW] Starting profile photo regeneration for {profile_name}")

    # =========================================================================
    # Step 0: Load session and get current profile picture
    # =========================================================================
    session = FacebookSession(profile_name)
    if not session.load():
        error_msg = f"Session not found for profile: {profile_name}"
        logger.error(f"[WORKFLOW] {error_msg}")
        result["error"] = error_msg
        return result

    current_picture = session.data.get("profile_picture")
    if not current_picture:
        error_msg = f"No profile picture found in session for: {profile_name}"
        logger.error(f"[WORKFLOW] {error_msg}")
        result["error"] = error_msg
        return result

    logger.info(f"[WORKFLOW] Loaded existing profile picture ({len(current_picture)} chars)")

    # =========================================================================
    # Step 1: Select pose
    # =========================================================================
    if pose_name:
        pose = get_pose_by_name(pose_name)
        if not pose:
            error_msg = f"Unknown pose: {pose_name}. Available: {[p['name'] for p in POSE_VARIATIONS]}"
            logger.error(f"[WORKFLOW] {error_msg}")
            result["error"] = error_msg
            return result
    else:
        pose = get_random_pose()

    result["pose_used"] = {"name": pose["name"], "prompt": pose["prompt"][:50] + "..."}
    logger.info(f"[WORKFLOW] Using pose: {pose['name']}")

    # =========================================================================
    # Step 2: Generate new photo with reference
    # =========================================================================
    logger.info(f"[WORKFLOW] Step 1: Generating new photo with identity preservation...")

    try:
        image_result = await generate_profile_photo_with_reference(
            reference_image_base64=current_picture,
            pose_prompt=pose["prompt"],
            profile_name=profile_name.replace(" ", "_").lower()
        )
        result["image_generation"] = image_result

        if not image_result.get("success"):
            error_msg = f"Image generation failed: {image_result.get('error')}"
            logger.error(f"[WORKFLOW] {error_msg}")
            result["error"] = error_msg
            return result

        image_path = image_result["image_path"]
        new_base64 = image_result.get("base64_image")
        logger.info(f"[WORKFLOW] New image generated: {image_path}")

    except Exception as e:
        error_msg = f"Image generation exception: {str(e)}"
        logger.error(f"[WORKFLOW] {error_msg}")
        result["error"] = error_msg
        result["image_generation"] = {"success": False, "error": str(e)}
        return result

    # =========================================================================
    # Step 3: Upload via Adaptive Agent
    # =========================================================================
    logger.info(f"[WORKFLOW] Step 2: Uploading photo via Adaptive Agent...")

    task = """Update your Facebook profile picture:

1. You are on your profile page. Look for your current profile picture at the top.
2. Tap on your profile picture (the circular photo at the top of the page).
3. You should see options like "Select Profile Picture", "Upload Photo", or "Edit".
4. Select "Upload Photo" or similar option to upload a new photo.
5. When prompted to choose a file, use UPLOAD element="Upload" or UPLOAD element="Choose" to select the photo.
6. After the photo uploads, you may need to crop or adjust it.
7. Tap "Save", "Done", or "Confirm" to save the new profile picture.
8. Once saved, use DONE to complete the task.

IMPORTANT:
- Use UPLOAD action when you see a file/photo selection interface
- Look for buttons labeled "Upload Photo", "Choose Photo", "Select Photo", "Camera Roll"
- After uploading, look for "Save", "Done", "Confirm", or checkmark buttons
"""

    try:
        upload_result = await run_adaptive_task(
            profile_name=profile_name,
            task=task,
            max_steps=20,
            start_url="https://m.facebook.com/me",
            upload_file_path=image_path
        )
        result["profile_upload"] = upload_result

        # Check success based on final status
        upload_success = False
        if upload_result.get("final_status") == "task_completed":
            upload_success = True
            logger.info(f"[WORKFLOW] Profile photo upload completed successfully!")
        elif upload_result.get("final_status") == "max_steps_reached":
            # Check if we BOTH uploaded the file AND clicked save/update
            # Just uploading the file is NOT enough - we need to confirm the save
            has_upload = False
            has_save_click = False
            for step in upload_result.get("steps", []):
                action = str(step.get("action_taken", "")).upper()
                if "UPLOAD" in action and "FAILED" not in action:
                    has_upload = True
                # Check if Update/Save/Confirm/Done was clicked AFTER upload
                if has_upload and "CLICK" in action:
                    element = str(step.get("action_taken", "")).lower()
                    if any(word in element for word in ["update", "save", "confirm", "done"]):
                        has_save_click = True
                        break

            if has_upload and has_save_click:
                upload_success = True
                logger.info(f"[WORKFLOW] Photo uploaded and saved (max_steps_reached but workflow completed)")
            else:
                logger.warning(f"[WORKFLOW] Max steps reached - upload={has_upload}, save_clicked={has_save_click}")
        else:
            logger.warning(f"[WORKFLOW] Upload ended with status: {upload_result.get('final_status')}")

    except Exception as e:
        error_msg = f"Upload exception: {str(e)}"
        logger.error(f"[WORKFLOW] {error_msg}")
        result["error"] = error_msg
        result["profile_upload"] = {"error": str(e), "final_status": "error"}
        upload_success = False

    # =========================================================================
    # Step 4: Update session with new profile picture
    # =========================================================================
    if upload_success and new_base64:
        try:
            session.data["profile_picture"] = new_base64
            session.data["profile_picture_pose"] = pose["name"]
            session.save()
            result["session_updated"] = True
            logger.info(f"[WORKFLOW] Session updated with new profile picture")
        except Exception as e:
            logger.warning(f"[WORKFLOW] Failed to update session: {e}")

    # =========================================================================
    # Cleanup: Remove temporary image file
    # =========================================================================
    try:
        Path(image_path).unlink(missing_ok=True)
        logger.info(f"[WORKFLOW] Cleaned up temp file: {image_path}")
    except Exception as e:
        logger.warning(f"[WORKFLOW] Failed to cleanup temp file: {e}")

    # Final result
    result["success"] = upload_success
    if result["success"]:
        logger.info(f"[WORKFLOW] SUCCESS: Profile photo regenerated for {profile_name}")
    else:
        logger.warning(f"[WORKFLOW] FAILED: Profile photo regeneration for {profile_name}")

    return result


async def batch_regenerate_imported_photos() -> Dict[str, Any]:
    """
    Regenerate profile photos for all profiles with 'imported' tag.
    Each profile gets a random pose from the variations pool.

    Returns:
        Dict with:
            - total: Number of profiles processed
            - successful: Number of successful regenerations
            - failed: Number of failed regenerations
            - results: List of individual results
    """
    # Get all sessions with 'imported' tag
    all_sessions = list_saved_sessions()
    imported_sessions = [
        s for s in all_sessions
        if "imported" in s.get("tags", [])
    ]

    logger.info(f"[WORKFLOW] Found {len(imported_sessions)} imported profiles to regenerate")

    results = {
        "total": len(imported_sessions),
        "successful": 0,
        "failed": 0,
        "results": []
    }

    # Track used poses to ensure variety
    used_poses = []
    available_pose_names = [p["name"] for p in POSE_VARIATIONS]

    for session_info in imported_sessions:
        profile_name = session_info.get("profile_name")
        if not profile_name:
            continue

        # Pick a pose we haven't used yet (if possible)
        unused_poses = [p for p in available_pose_names if p not in used_poses]
        if not unused_poses:
            # Reset if we've used all poses
            used_poses = []
            unused_poses = available_pose_names

        import random
        pose_name = random.choice(unused_poses)
        used_poses.append(pose_name)

        logger.info(f"[WORKFLOW] Processing {profile_name} with pose: {pose_name}")

        try:
            result = await regenerate_profile_photo_with_pose(
                profile_name=profile_name,
                pose_name=pose_name
            )
            results["results"].append(result)

            if result.get("success"):
                results["successful"] += 1
            else:
                results["failed"] += 1

        except Exception as e:
            logger.error(f"[WORKFLOW] Exception for {profile_name}: {e}")
            results["results"].append({
                "profile_name": profile_name,
                "success": False,
                "error": str(e)
            })
            results["failed"] += 1

    logger.info(f"[WORKFLOW] Batch complete: {results['successful']}/{results['total']} successful")
    return results
