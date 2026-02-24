import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import premium_actions


def test_group_discovery_prompt_enforces_try_next_group_policy(monkeypatch):
    captured = {"tasks": []}

    async def fake_run_adaptive_task(**kwargs):
        captured["tasks"].append(kwargs.get("task", ""))
        return {
            "final_status": "task_completed",
            "final_url": "https://m.facebook.com/groups/123/posts/456?story_fbid=456",
            "screenshots": ["/tmp/before.png", "/tmp/after.png"],
            "steps": [
                {
                    "action_taken": "group post submitted",
                    "gemini_response": "group completed",
                    "reasoning": "done",
                    "matched_element": {"tag": "DIV", "ariaLabel": "Post", "text": "Post"},
                }
            ],
            "errors": [],
        }

    monkeypatch.setattr(premium_actions, "run_adaptive_task", fake_run_adaptive_task)

    result = asyncio.run(
        premium_actions.discover_group_and_publish(
            run_id="run_1",
            cycle_index=0,
            profile_name="Vanessa Hines",
            topic_seed="menopause groups",
            allow_join_new=True,
            join_pending_policy="try_next_group",
            group_post_text="supportive post",
            image_path="/tmp/image.png",
        )
    )

    assert result["success"] is True
    assert any("skip to the next actionable group immediately" in task for task in captured["tasks"])
