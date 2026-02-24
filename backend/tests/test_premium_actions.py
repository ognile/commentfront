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


def test_group_publish_retries_from_groups_home_on_tunnel_error(monkeypatch):
    calls = []
    direct_url = "https://m.facebook.com/groups/1037210351792668/"

    async def fake_run_adaptive_task(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "final_status": "error",
                "final_url": None,
                "screenshots": [],
                "steps": [],
                "errors": [
                    "Page.goto: net::ERR_TUNNEL_CONNECTION_FAILED at https://m.facebook.com/groups/1037210351792668/"
                ],
            }
        return {
            "final_status": "task_completed",
            "final_url": "https://m.facebook.com/groups/1037210351792668/posts/12345?story_fbid=12345",
            "screenshots": ["/tmp/before.png", "/tmp/after.png"],
            "steps": [
                {
                    "action_taken": "group post submitted",
                    "gemini_response": "DONE: posted in group",
                    "reasoning": "completed",
                    "matched_element": {"tag": "DIV", "ariaLabel": "Post", "text": "Post"},
                }
            ],
            "errors": [],
        }

    monkeypatch.setattr(premium_actions, "run_adaptive_task", fake_run_adaptive_task)

    result = asyncio.run(
        premium_actions.discover_group_and_publish(
            run_id="run_2",
            cycle_index=1,
            profile_name="Vanessa Hines",
            topic_seed=direct_url,
            allow_join_new=True,
            join_pending_policy="try_next_group",
            group_post_text="supportive post",
            image_path=None,
        )
    )

    assert result["success"] is True
    assert len(calls) == 2
    assert calls[0]["start_url"] == direct_url
    assert calls[1]["start_url"] == "https://m.facebook.com/groups"
    assert result["evidence"]["action_method"]["retry_used"] is True
    assert result["evidence"]["action_method"]["retry_from_start_url"] == direct_url
