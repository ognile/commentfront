import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import premium_actions


def test_group_discovery_prompt_enforces_try_next_group_policy(monkeypatch):
    captured = {"tasks": [], "calls": []}

    async def fake_run_adaptive_task(**kwargs):
        captured["tasks"].append(kwargs.get("task", ""))
        captured["calls"].append(kwargs)
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
    assert captured["calls"][0]["max_steps"] == 28


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


def test_group_publish_retries_to_home_after_groups_tunnel_error(monkeypatch):
    calls = []
    direct_url = "https://m.facebook.com/groups/1037210351792668/"

    async def fake_run_adaptive_task(**kwargs):
        calls.append(kwargs)
        if len(calls) < 3:
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
            "final_url": "https://m.facebook.com/groups/1037210351792668/posts/67890?story_fbid=67890",
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
            run_id="run_3",
            cycle_index=2,
            profile_name="Vanessa Hines",
            topic_seed=direct_url,
            allow_join_new=True,
            join_pending_policy="try_next_group",
            group_post_text="supportive post",
            image_path=None,
        )
    )

    assert result["success"] is True
    assert len(calls) == 3
    assert calls[0]["start_url"] == direct_url
    assert calls[1]["start_url"] == "https://m.facebook.com/groups"
    assert calls[2]["start_url"] == "https://m.facebook.com/"
    assert result["evidence"]["action_method"]["retry_attempts"] == 2


def test_group_publish_retries_when_submission_not_verifiable(monkeypatch):
    calls = []

    async def fake_run_adaptive_task(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "final_status": "task_completed",
                "final_url": "https://m.facebook.com/groups/111111111111111/",
                "screenshots": ["/tmp/before1.png", "/tmp/after1.png"],
                "steps": [
                    {
                        "action_taken": "group navigation completed",
                        "gemini_response": "DONE: inside group feed",
                        "reasoning": "in group",
                        "matched_element": {"tag": "DIV", "ariaLabel": "Visit", "text": "Visit"},
                    }
                ],
                "errors": [],
            }
        return {
            "final_status": "task_completed",
            "final_url": "https://m.facebook.com/groups/222222222222222/posts/67890?story_fbid=67890",
            "screenshots": ["/tmp/before2.png", "/tmp/after2.png"],
            "steps": [
                {
                    "action_taken": "group post submitted",
                    "gemini_response": "DONE: post is pending admin approval",
                    "reasoning": "facebook confirmed pending admin approval",
                    "matched_element": {"tag": "DIV", "ariaLabel": "Post", "text": "Post"},
                }
            ],
            "errors": [],
        }

    monkeypatch.setattr(premium_actions, "run_adaptive_task", fake_run_adaptive_task)

    result = asyncio.run(
        premium_actions.discover_group_and_publish(
            run_id="run_group_retry_unverified",
            cycle_index=0,
            profile_name="Vanessa Hines",
            topic_seed="menopause groups",
            allow_join_new=True,
            join_pending_policy="try_next_group",
            group_post_text="supportive post",
            image_path=None,
        )
    )

    assert len(calls) == 2
    assert result["success"] is True
    assert result["evidence"]["action_method"]["group_attempt"] == 2
    assert result["evidence"]["confirmation"]["post_visible_or_permalink_resolved"] is True


def test_comment_replies_fallback_submit_marks_reply_visible(monkeypatch):
    async def fake_run_adaptive_task(**kwargs):
        return {
            "final_status": "task_completed",
            "final_url": "https://m.facebook.com/story.php?story_fbid=123&id=456",
            "screenshots": ["/tmp/before.png", "/tmp/after.png"],
            "steps": [
                {
                    "action_taken": "CLICK \"Posts\"",
                    "gemini_response": "ACTION: CLICK element=\"Posts\"",
                    "reasoning": "open posts tab",
                    "matched_element": {"tag": "DIV", "ariaLabel": "Posts", "text": "Posts"},
                },
                {
                    "action_taken": "FALLBACK_REPLY_SUBMIT",
                    "gemini_response": "",
                    "reasoning": "",
                },
            ],
            "errors": [],
        }

    monkeypatch.setattr(premium_actions, "run_adaptive_task", fake_run_adaptive_task)

    result = asyncio.run(
        premium_actions.perform_comment_replies(
            run_id="run_reply_1",
            cycle_index=0,
            profile_name="Vanessa Hines",
            replies_count=1,
            reply_text="sending support here",
        )
    )

    assert result["success"] is True
    assert result["completed_count"] == 1
    assert result["evidence"]["confirmation"]["reply_visible_under_thread"] is True
    assert result["evidence"]["result_state"]["success"] is True


def test_comment_replies_accepts_reply_submit_trace_with_done(monkeypatch):
    async def fake_run_adaptive_task(**kwargs):
        return {
            "final_status": "task_completed",
            "final_url": "https://m.facebook.com/story.php?story_fbid=123&id=456",
            "screenshots": ["/tmp/before.png", "/tmp/after.png"],
            "steps": [
                {
                    "action_taken": 'CLICK "Reply"',
                    "gemini_response": "ACTION: CLICK element=\"Reply\"",
                    "reasoning": "reply to thread",
                    "matched_element": {"tag": "DIV", "ariaLabel": "", "text": "Reply"},
                },
                {
                    "action_taken": "TYPE: sending you support. you are not alone in this and...",
                    "gemini_response": "ACTION: TYPE text=...",
                    "reasoning": "type supportive message",
                },
                {
                    "action_taken": 'CLICK "Post a comment"',
                    "gemini_response": "ACTION: CLICK element=\"Post a comment\"",
                    "reasoning": "submit reply",
                    "matched_element": {"tag": "DIV", "ariaLabel": "Post a comment", "text": "Post"},
                },
            ],
            "errors": [],
        }

    monkeypatch.setattr(premium_actions, "run_adaptive_task", fake_run_adaptive_task)

    result = asyncio.run(
        premium_actions.perform_comment_replies(
            run_id="run_reply_trace",
            cycle_index=0,
            profile_name="Vanessa Hines",
            replies_count=1,
            reply_text="sending you support. you are not alone in this and i hope today gets gentler for you.",
        )
    )

    assert result["success"] is True
    assert result["evidence"]["confirmation"]["reply_visible_under_thread"] is True
    assert result["evidence"]["result_state"]["success"] is True


def test_comment_replies_accepts_type_set_exact_reply_trace(monkeypatch):
    async def fake_run_adaptive_task(**kwargs):
        return {
            "final_status": "task_completed",
            "final_url": "https://m.facebook.com/story.php?story_fbid=123&id=456",
            "screenshots": ["/tmp/before.png", "/tmp/after.png"],
            "steps": [
                {
                    "action_taken": 'CLICK "Reply"',
                    "gemini_response": "ACTION: CLICK element=\"Reply\"",
                    "reasoning": "reply to thread",
                    "matched_element": {"tag": "DIV", "ariaLabel": "", "text": "Reply"},
                },
                {
                    "action_taken": "TYPE_SET_EXACT: sending you support. you are not alone in this and...",
                    "gemini_response": "ACTION: TYPE text=...",
                    "reasoning": "type supportive message",
                },
                {
                    "action_taken": 'CLICK "Post a comment"',
                    "gemini_response": "ACTION: CLICK element=\"Post a comment\"",
                    "reasoning": "submit reply",
                    "matched_element": {"tag": "DIV", "ariaLabel": "Post a comment", "text": "Post"},
                },
            ],
            "errors": [],
        }

    monkeypatch.setattr(premium_actions, "run_adaptive_task", fake_run_adaptive_task)

    result = asyncio.run(
        premium_actions.perform_comment_replies(
            run_id="run_reply_trace_exact",
            cycle_index=0,
            profile_name="Vanessa Hines",
            replies_count=1,
            reply_text="sending you support. you are not alone in this and i hope today gets gentler for you.",
        )
    )

    assert result["success"] is True
    assert result["evidence"]["confirmation"]["reply_visible_under_thread"] is True
    assert result["evidence"]["confirmation"]["reply_cta_clicked"] is True
    assert result["evidence"]["confirmation"]["reply_submit_clicked"] is True
    assert result["evidence"]["confirmation"]["reply_text_typed"] is True
    assert result["evidence"]["result_state"]["success"] is True


def test_comment_replies_rejects_non_thread_comment_flow(monkeypatch):
    async def fake_run_adaptive_task(**kwargs):
        return {
            "final_status": "task_completed",
            "final_url": "https://m.facebook.com/story.php?story_fbid=123&id=456",
            "screenshots": ["/tmp/before.png", "/tmp/after.png"],
            "steps": [
                {
                    "action_taken": 'CLICK "󰍹"',
                    "gemini_response": "ACTION: CLICK element=\"󰍹\"",
                    "reasoning": "open comments",
                    "matched_element": {"tag": "DIV", "ariaLabel": "󰍹comment", "text": "󰍹"},
                },
                {
                    "action_taken": "TYPE_SET_EXACT: sending you support. you are not alone in this and...",
                    "gemini_response": "ACTION: TYPE text=...",
                    "reasoning": "type supportive message",
                },
                {
                    "action_taken": 'CLICK "Post a comment"',
                    "gemini_response": "ACTION: CLICK element=\"Post a comment\"",
                    "reasoning": "submit top-level comment",
                    "matched_element": {"tag": "DIV", "ariaLabel": "Post a comment", "text": "Post"},
                },
            ],
            "errors": [],
        }

    monkeypatch.setattr(premium_actions, "run_adaptive_task", fake_run_adaptive_task)

    result = asyncio.run(
        premium_actions.perform_comment_replies(
            run_id="run_reply_non_thread",
            cycle_index=0,
            profile_name="Vanessa Hines",
            replies_count=1,
            reply_text="sending you support. you are not alone in this and i hope today gets gentler for you.",
        )
    )

    assert result["success"] is False
    assert result["evidence"]["confirmation"]["reply_visible_under_thread"] is False
    assert result["evidence"]["confirmation"]["reply_cta_clicked"] is False
    assert result["evidence"]["confirmation"]["reply_submit_clicked"] is True
    assert result["evidence"]["confirmation"]["reply_text_typed"] is True
    assert result["evidence"]["result_state"]["success"] is False


def test_comment_replies_retries_from_group_search_when_no_reply_cta(monkeypatch):
    calls = []

    async def fake_run_adaptive_task(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "final_status": "max_steps_reached",
                "final_url": "https://m.facebook.com/groups/no-comments/",
                "screenshots": ["/tmp/before1.png", "/tmp/after1.png"],
                "steps": [
                    {"action_taken": 'CLICK "󰍹"', "gemini_response": "ACTION: CLICK element=\"󰍹\""},
                    {"action_taken": 'CLICK "󱙸"', "gemini_response": "ACTION: CLICK element=\"󱙸\""},
                ],
                "errors": [],
            }
        return {
            "final_status": "task_completed",
            "final_url": "https://m.facebook.com/story.php?story_fbid=321&id=654",
            "screenshots": ["/tmp/before2.png", "/tmp/after2.png"],
            "steps": [
                {"action_taken": 'CLICK "Reply"', "gemini_response": "ACTION: CLICK element=\"Reply\""},
                {
                    "action_taken": "TYPE_SET_EXACT: sending you support. you are not alone in this and...",
                    "gemini_response": "ACTION: TYPE text=...",
                },
                {"action_taken": 'CLICK "Post a comment"', "gemini_response": "ACTION: CLICK element=\"Post a comment\""},
            ],
            "errors": [],
        }

    monkeypatch.setattr(premium_actions, "run_adaptive_task", fake_run_adaptive_task)

    result = asyncio.run(
        premium_actions.perform_comment_replies(
            run_id="run_reply_retry",
            cycle_index=0,
            profile_name="Vanessa Hines",
            replies_count=1,
            reply_text="sending you support. you are not alone in this and i hope today gets gentler for you.",
            start_url="https://m.facebook.com/groups/example-group/",
        )
    )

    assert len(calls) == 2
    assert calls[0]["start_url"] == "https://m.facebook.com/groups/example-group/"
    assert "search/groups/?q=menopause+groups" in calls[1]["start_url"]
    assert result["success"] is True
    assert result["evidence"]["confirmation"]["reply_visible_under_thread"] is True
    assert result["evidence"]["action_method"]["retry_used"] is True
    assert result["evidence"]["action_method"]["retry_attempts"] == 1


def test_comment_replies_retries_when_submit_not_confirmed(monkeypatch):
    calls = []

    async def fake_run_adaptive_task(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "final_status": "task_completed",
                "final_url": "https://m.facebook.com/",
                "screenshots": ["/tmp/before1.png", "/tmp/after1.png"],
                "steps": [
                    {"action_taken": 'CLICK "Reply"', "gemini_response": "ACTION: CLICK element=\"Reply\""},
                    {
                        "action_taken": "TYPE_SET_EXACT: sending you support. you are not alone in this and...",
                        "gemini_response": "ACTION: TYPE text=...",
                    },
                    {"action_taken": 'CLICK "Post"', "gemini_response": "ACTION: CLICK element=\"Post a photo\""},
                ],
                "errors": [],
            }
        return {
            "final_status": "task_completed",
            "final_url": "https://m.facebook.com/story.php?story_fbid=321&id=654",
            "screenshots": ["/tmp/before2.png", "/tmp/after2.png"],
            "steps": [
                {"action_taken": 'CLICK "Reply"', "gemini_response": "ACTION: CLICK element=\"Reply\""},
                {
                    "action_taken": "TYPE_SET_EXACT: sending you support. you are not alone in this and...",
                    "gemini_response": "ACTION: TYPE text=...",
                },
                {"action_taken": 'CLICK "Post a comment"', "gemini_response": "ACTION: CLICK element=\"Post a comment\""},
            ],
            "errors": [],
        }

    monkeypatch.setattr(premium_actions, "run_adaptive_task", fake_run_adaptive_task)

    result = asyncio.run(
        premium_actions.perform_comment_replies(
            run_id="run_reply_retry_submit_missing",
            cycle_index=0,
            profile_name="Vanessa Hines",
            replies_count=1,
            reply_text="sending you support. you are not alone in this and i hope today gets gentler for you.",
            start_url="https://m.facebook.com/groups/example-group/",
        )
    )

    assert len(calls) == 2
    assert calls[0]["start_url"] == "https://m.facebook.com/groups/example-group/"
    assert "search/groups/?q=menopause+groups" in calls[1]["start_url"]
    assert result["success"] is True
    assert result["evidence"]["action_method"]["retry_used"] is True
    assert result["evidence"]["confirmation"]["reply_submit_clicked"] is True
    assert result["evidence"]["confirmation"]["reply_visible_under_thread"] is True


def test_comment_replies_uses_second_fallback_with_comment_count_rule(monkeypatch):
    calls = []

    async def fake_run_adaptive_task(**kwargs):
        calls.append(kwargs)
        if len(calls) < 3:
            return {
                "final_status": "max_steps_reached",
                "final_url": "https://m.facebook.com/groups/example-group/",
                "screenshots": ["/tmp/before.png", "/tmp/after.png"],
                "steps": [
                    {"action_taken": 'SCROLL down', "gemini_response": "ACTION: SCROLL direction=down"},
                    {"action_taken": 'CLICK \"󰍹\"', "gemini_response": "ACTION: CLICK element=\"󰍹\""},
                ],
                "errors": [],
            }
        return {
            "final_status": "task_completed",
            "final_url": "https://m.facebook.com/story.php?story_fbid=654&id=321",
            "screenshots": ["/tmp/before3.png", "/tmp/after3.png"],
            "steps": [
                {"action_taken": 'CLICK "Reply"', "gemini_response": "ACTION: CLICK element=\"Reply\""},
                {
                    "action_taken": "TYPE_SET_EXACT: sending you support. you are not alone in this and...",
                    "gemini_response": "ACTION: TYPE text=...",
                },
                {"action_taken": 'CLICK "Post a comment"', "gemini_response": "ACTION: CLICK element=\"Post a comment\""},
            ],
            "errors": [],
        }

    monkeypatch.setattr(premium_actions, "run_adaptive_task", fake_run_adaptive_task)

    result = asyncio.run(
        premium_actions.perform_comment_replies(
            run_id="run_reply_second_retry",
            cycle_index=0,
            profile_name="Vanessa Hines",
            replies_count=1,
            reply_text="sending you support. you are not alone in this and i hope today gets gentler for you.",
            start_url="https://m.facebook.com/groups/example-group/",
        )
    )

    assert len(calls) == 3
    assert "search/groups/?q=menopause+groups" in calls[1]["start_url"]
    assert "search/groups/?q=menopause+groups" in calls[2]["start_url"]
    assert "Only open posts that show explicit comment-count text" in calls[2]["task"]
    assert result["success"] is True
    assert result["evidence"]["action_method"]["retry_used"] is True
    assert result["evidence"]["action_method"]["retry_attempts"] == 2
    assert result["evidence"]["confirmation"]["reply_visible_under_thread"] is True


def test_group_publish_accepts_pending_admin_approval_signal(monkeypatch):
    async def fake_run_adaptive_task(**kwargs):
        return {
            "final_status": "task_completed",
            "final_url": "https://m.facebook.com/groups/123456789/",
            "screenshots": ["/tmp/before.png", "/tmp/after.png"],
            "steps": [
                {
                    "action_taken": "group post submitted",
                    "gemini_response": "DONE: post is pending admin approval",
                    "reasoning": "facebook confirmed pending admin approval",
                    "matched_element": {"tag": "DIV", "ariaLabel": "Post", "text": "Post"},
                }
            ],
            "errors": [],
        }

    monkeypatch.setattr(premium_actions, "run_adaptive_task", fake_run_adaptive_task)

    result = asyncio.run(
        premium_actions.discover_group_and_publish(
            run_id="run_group_pending",
            cycle_index=0,
            profile_name="Vanessa Hines",
            topic_seed="menopause groups",
            allow_join_new=True,
            join_pending_policy="try_next_group",
            group_post_text="supportive post",
            image_path=None,
        )
    )

    assert result["success"] is True
    assert result["evidence"]["confirmation"]["post_visible_or_permalink_resolved"] is True


def test_feed_submit_guard_blocks_repeated_submit_loop(monkeypatch):
    async def fake_run_adaptive_task(**kwargs):
        return {
            "final_status": "task_completed",
            "final_url": "https://m.facebook.com/story.php?story_fbid=123&id=456",
            "screenshots": ["/tmp/before.png", "/tmp/after.png"],
            "steps": [
                {"action_taken": 'CLICK "Post"'},
                {"action_taken": 'CLICK "Post"'},
            ],
            "errors": [],
        }

    monkeypatch.setattr(premium_actions, "run_adaptive_task", fake_run_adaptive_task)

    result = asyncio.run(
        premium_actions.publish_feed_post(
            run_id="run_feed_guard",
            cycle_index=0,
            profile_name="Vanessa Hines",
            caption="test caption",
            image_path=None,
            single_submit_guard=True,
        )
    )

    assert result["success"] is False
    assert result["error"] == "submit_idempotency_blocked"
    assert result["evidence"]["submit_guard"]["passed"] is False


def test_feed_type_guard_blocks_repeated_caption_typing(monkeypatch):
    async def fake_run_adaptive_task(**kwargs):
        return {
            "final_status": "task_completed",
            "final_url": "https://m.facebook.com/story.php?story_fbid=123&id=456",
            "screenshots": ["/tmp/before.png", "/tmp/after.png"],
            "steps": [
                {"action_taken": "TYPE: small wins today and trying to stay..."},
                {"action_taken": "TYPE: small wins today and trying to stay..."},
                {"action_taken": 'CLICK "POST"'},
            ],
            "errors": [],
        }

    monkeypatch.setattr(premium_actions, "run_adaptive_task", fake_run_adaptive_task)

    result = asyncio.run(
        premium_actions.publish_feed_post(
            run_id="run_feed_type_guard",
            cycle_index=0,
            profile_name="Vanessa Hines",
            caption="small wins today and trying to stay consistent with my routine.",
            image_path=None,
            single_submit_guard=True,
        )
    )

    assert result["success"] is False
    assert result["error"] == "composer_type_idempotency_blocked"
    assert result["evidence"]["type_guard"]["passed"] is False


def test_feed_publish_sets_max_type_actions_guard(monkeypatch):
    calls = []

    async def fake_run_adaptive_task(**kwargs):
        calls.append(kwargs)
        return {
            "final_status": "task_completed",
            "final_url": "https://m.facebook.com/story.php?story_fbid=123&id=456",
            "screenshots": ["/tmp/before.png", "/tmp/after.png"],
            "steps": [
                {"action_taken": 'CLICK "What\'s on your mind?"'},
                {"action_taken": "TYPE_SET_EXACT: checking single-type guard..."},
                {"action_taken": 'CLICK "POST"'},
            ],
            "errors": [],
        }

    monkeypatch.setattr(premium_actions, "run_adaptive_task", fake_run_adaptive_task)

    result = asyncio.run(
        premium_actions.publish_feed_post(
            run_id="run_feed_single_type_limit",
            cycle_index=0,
            profile_name="Vanessa Hines",
            caption="checking single-type guard",
            image_path=None,
            single_submit_guard=True,
        )
    )

    assert result["success"] is True
    assert len(calls) == 1
    assert calls[0]["max_type_actions"] == 1


def test_feed_type_guard_blocks_repeated_exact_set_typing(monkeypatch):
    async def fake_run_adaptive_task(**kwargs):
        return {
            "final_status": "task_completed",
            "final_url": "https://m.facebook.com/story.php?story_fbid=123&id=456",
            "screenshots": ["/tmp/before.png", "/tmp/after.png"],
            "steps": [
                {"action_taken": "TYPE_SET_EXACT: small wins today and trying to stay..."},
                {"action_taken": "TYPE_SET_EXACT: small wins today and trying to stay..."},
                {"action_taken": 'CLICK "POST"'},
            ],
            "errors": [],
        }

    monkeypatch.setattr(premium_actions, "run_adaptive_task", fake_run_adaptive_task)

    result = asyncio.run(
        premium_actions.publish_feed_post(
            run_id="run_feed_type_guard_exact",
            cycle_index=0,
            profile_name="Vanessa Hines",
            caption="small wins today and trying to stay consistent with my routine.",
            image_path=None,
            single_submit_guard=True,
        )
    )

    assert result["success"] is False
    assert result["error"] == "composer_type_idempotency_blocked"
    assert result["evidence"]["type_guard"]["passed"] is False
    assert result["evidence"]["type_guard"]["caption_type_count"] == 2


def test_feed_type_guard_ignores_type_skipped_duplicate(monkeypatch):
    async def fake_run_adaptive_task(**kwargs):
        return {
            "final_status": "task_completed",
            "final_url": "https://m.facebook.com/",
            "screenshots": ["/tmp/before.png", "/tmp/after.png"],
            "steps": [
                {"action_taken": "TYPE: small wins today and trying to stay..."},
                {"action_taken": "TYPE_SKIPPED_DUPLICATE: small wins today and trying to stay..."},
                {"action_taken": 'CLICK "POST"'},
                {"action_taken": "DONE: post visible"},
            ],
            "errors": [],
        }

    monkeypatch.setattr(premium_actions, "run_adaptive_task", fake_run_adaptive_task)

    result = asyncio.run(
        premium_actions.publish_feed_post(
            run_id="run_feed_type_guard_ok",
            cycle_index=0,
            profile_name="Vanessa Hines",
            caption="small wins today and trying to stay consistent with my routine.",
            image_path=None,
            single_submit_guard=True,
        )
    )

    assert result["success"] is True
    assert result["evidence"]["type_guard"]["passed"] is True
    assert result["evidence"]["type_guard"]["caption_type_count"] == 1


def test_feed_visibility_accepts_done_signal_on_own_feed_url(monkeypatch):
    async def fake_run_adaptive_task(**kwargs):
        return {
            "final_status": "task_completed",
            "final_url": "https://m.facebook.com/",
            "screenshots": ["/tmp/before.png", "/tmp/after.png"],
            "steps": [
                {
                    "action_taken": 'CLICK "POST"',
                    "gemini_response": "DONE: post has been successfully submitted",
                    "reasoning": "The post is visible on the user's feed.",
                    "matched_element": {"tag": "DIV", "ariaLabel": "Post", "text": "POST"},
                }
            ],
            "errors": [],
        }

    monkeypatch.setattr(premium_actions, "run_adaptive_task", fake_run_adaptive_task)

    result = asyncio.run(
        premium_actions.publish_feed_post(
            run_id="run_feed_visibility",
            cycle_index=0,
            profile_name="Vanessa Hines",
            caption="test caption",
            image_path=None,
            single_submit_guard=True,
        )
    )

    assert result["success"] is True
    assert result["evidence"]["confirmation"]["post_visible_or_permalink_resolved"] is True
