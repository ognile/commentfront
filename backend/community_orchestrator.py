"""Community task orchestrator — routes due tasks to existing premium_actions."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from community_content import generate_warmup_post
from community_store import CommunityStore, get_community_store

logger = logging.getLogger("CommunityOrchestrator")

# Limit concurrent FB actions to avoid competing with premium/campaign schedulers
_semaphore = asyncio.Semaphore(2)


class CommunityOrchestrator:
    def __init__(self, store: Optional[CommunityStore] = None):
        self.store = store or get_community_store()

    async def process_due_tasks(self, max_tasks: int = 3) -> Dict[str, int]:
        """Fetch and execute due community tasks."""
        if not self.store.enabled:
            return {"processed": 0, "failed": 0, "skipped": 0}

        tasks = await self.store.get_due_tasks(limit=max_tasks)
        if not tasks:
            return {"processed": 0, "failed": 0, "skipped": 0}

        logger.info(f"processing {len(tasks)} due community tasks")
        results = {"processed": 0, "failed": 0, "skipped": 0}

        for task in tasks:
            async with _semaphore:
                try:
                    await self._execute_task(task)
                    results["processed"] += 1
                except Exception as exc:
                    logger.error(f"community task {task['id']} failed: {exc}")
                    await self.store.fail_task(task["id"], str(exc))
                    results["failed"] += 1

        return results

    async def _execute_task(self, task: Dict[str, Any]) -> None:
        """Route a single task to the correct action handler."""
        task_id = task["id"]
        action = task["action"]
        profile_name = task["profile_name"]
        started_at = _now_iso()

        await self.store.claim_task(task_id)
        logger.info(f"executing community task {task_id}: {action} for {profile_name}")

        try:
            if action == "join_group":
                result = await self._action_join_group(task)
            elif action == "warmup_post":
                result = await self._action_warmup_post(task)
            elif action == "post_in_group":
                result = await self._action_post_in_group(task)
            elif action == "like_post":
                result = await self._action_like_post(task)
            elif action == "reply_to_post":
                result = await self._action_reply_to_post(task)
            else:
                raise ValueError(f"unknown community action: {action}")

            success = result.get("success", False)
            screenshot_url = await self._capture_proof_screenshot(task_id, task["attempts"], result)

            if success:
                await self.store.complete_task(task_id, result)
                logger.info(f"community task {task_id} completed successfully")
            else:
                error = result.get("error") or "action reported failure"
                await self.store.fail_task(task_id, error)

            await self.store.log_execution(
                task_id=task_id,
                attempt=task.get("attempts", 0) + 1,
                started_at=started_at,
                completed_at=_now_iso(),
                success=success,
                error=result.get("error"),
                result=_slim_result(result),
                screenshot_url=screenshot_url,
            )

        except Exception as exc:
            await self.store.log_execution(
                task_id=task_id,
                attempt=task.get("attempts", 0) + 1,
                started_at=started_at,
                completed_at=_now_iso(),
                success=False,
                error=str(exc),
            )
            raise

    async def _action_join_group(self, task: Dict[str, Any]) -> Dict:
        """Navigate to group URL and click Join Group."""
        import premium_actions

        group_url = task.get("target_url", "")
        join_task = f"""
Navigate to this Facebook group and request to join it.

Rules:
- If you see a banner saying "The link you followed may be broken", close it using the X button.
- Look for a "Join Group" or "Join" button and click it.
- If you see "Pending" or "Your request is pending", that means join was requested — finish with DONE.
- If you are already a member, finish with DONE.
- Do NOT click "ok" unless a visible button with text exactly "OK" exists.
- Finish with DONE after requesting to join or confirming membership.
""".strip()

        result = await premium_actions._execute_task(
            run_id=task["id"],
            cycle_index=0,
            step_id="join_group",
            profile_name=task["profile_name"],
            action_type="join_group",
            task=join_task,
            start_url=group_url,
            expected_count=1,
            confirmation_keyword="join",
            max_steps=15,
        )
        return result

    async def _action_warmup_post(self, task: Dict[str, Any]) -> Dict:
        """Generate content and post to own timeline."""
        import premium_actions

        persona = await self.store.get_persona(task["profile_name"])
        if not persona:
            return {"success": False, "error": f"no persona found for {task['profile_name']}"}

        # Generate warmup content
        content = await generate_warmup_post(persona, day_index=0)
        if not content.get("text"):
            return {"success": False, "error": f"content generation failed: {content.get('error', 'empty')}"}

        result = await premium_actions.publish_feed_post(
            run_id=task["id"],
            cycle_index=0,
            profile_name=task["profile_name"],
            caption=content["text"],
            image_path=content.get("image_path"),
        )
        result["generated_content"] = content
        return result

    async def _action_post_in_group(self, task: Dict[str, Any]) -> Dict:
        """Post in a Facebook group with optional image."""
        import premium_actions

        image_path = await self._resolve_image(task)

        result = await premium_actions.discover_group_and_publish(
            run_id=task["id"],
            cycle_index=0,
            profile_name=task["profile_name"],
            topic_seed=task.get("target_url", ""),
            allow_join_new=False,
            join_pending_policy="fail_run",
            group_post_text=task.get("text", ""),
            image_path=image_path,
        )
        return result

    async def _action_like_post(self, task: Dict[str, Any]) -> Dict:
        """Like a specific post."""
        import premium_actions

        result = await premium_actions.perform_likes(
            run_id=task["id"],
            cycle_index=0,
            profile_name=task["profile_name"],
            likes_count=1,
            start_url=task.get("target_url"),
        )
        return result

    async def _action_reply_to_post(self, task: Dict[str, Any]) -> Dict:
        """Reply to a specific post."""
        import premium_actions

        result = await premium_actions.perform_comment_replies(
            run_id=task["id"],
            cycle_index=0,
            profile_name=task["profile_name"],
            replies_count=1,
            reply_text=task.get("text", ""),
            start_url=task.get("target_url"),
        )
        return result

    async def _resolve_image(self, task: Dict[str, Any]) -> Optional[str]:
        """Download image_url or generate from image_prompt. Returns local file path."""
        if task.get("image_url"):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(task["image_url"])
                    resp.raise_for_status()
                path = Path(os.getenv("DEBUG_DIR", "/data/debug")) / f"community_img_{task['id'][:8]}.png"
                path.write_bytes(resp.content)
                return str(path)
            except Exception as exc:
                logger.warning(f"image download failed for task {task['id']}: {exc}")
                return None

        if task.get("image_prompt"):
            from community_content import _generate_warmup_image
            persona = await self.store.get_persona(task["profile_name"]) or {}
            result = await _generate_warmup_image(persona, task["image_prompt"])
            return result.get("image_path") if result.get("success") else None

        return None

    async def _capture_proof_screenshot(
        self, task_id: str, attempt: int, result: Dict[str, Any]
    ) -> Optional[str]:
        """Extract final screenshot from adaptive agent result and upload to supabase."""
        adaptive_result = result.get("result") or {}
        screenshot_path = adaptive_result.get("final_screenshot")

        if not screenshot_path:
            screenshots = adaptive_result.get("screenshots", [])
            screenshot_path = screenshots[-1] if screenshots else None

        if not screenshot_path:
            return None

        try:
            path = Path(screenshot_path)
            if not path.exists():
                return None
            image_data = path.read_bytes()
            url = await self.store.upload_screenshot(task_id, attempt, image_data)
            logger.info(f"uploaded proof screenshot for task {task_id}: {url}")
            return url
        except Exception as exc:
            logger.warning(f"screenshot upload failed for task {task_id}: {exc}")
            return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _slim_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Trim large fields from result for storage."""
    slim = {}
    for key in ("success", "completed_count", "expected_count", "error"):
        if key in result:
            slim[key] = result[key]
    adaptive = result.get("result") or {}
    slim["final_status"] = adaptive.get("final_status")
    slim["final_url"] = adaptive.get("final_url")
    slim["steps_count"] = len(adaptive.get("steps", []))
    slim["errors"] = adaptive.get("errors", [])[:3]
    if result.get("generated_content"):
        slim["generated_text"] = result["generated_content"].get("text", "")[:200]
        slim["generated_topic"] = result["generated_content"].get("topic", "")
    return slim
