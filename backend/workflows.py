"""
Workflow Functions Module

High-level workflow functions that combine multiple capabilities
for complex multi-step automation tasks.
"""

import logging
from pathlib import Path
from typing import Dict, Any, Optional

from gemini_image_gen import generate_profile_photo_for_persona
from adaptive_agent import run_adaptive_task

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
            # Check if we got far enough (uploaded the photo)
            for step in upload_result.get("steps", []):
                action = str(step.get("action_taken", ""))
                if "UPLOAD" in action and "FAILED" not in action:
                    result["success"] = True
                    logger.info(f"[WORKFLOW] Photo was uploaded (max_steps_reached but upload successful)")
                    break

            if not result["success"]:
                logger.warning(f"[WORKFLOW] Max steps reached without confirmed upload")
        else:
            logger.warning(f"[WORKFLOW] Upload ended with status: {upload_result.get('final_status')}")

    except Exception as e:
        error_msg = f"Upload exception: {str(e)}"
        logger.error(f"[WORKFLOW] {error_msg}")
        result["error"] = error_msg
        result["profile_upload"] = {"error": str(e), "final_status": "error"}

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
