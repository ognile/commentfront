import asyncio
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
from reddit_mission_store import RedditMissionStore
from reddit_program_store import RedditProgramStore


def test_create_reddit_mission_persists_target_comment_url(tmp_path, monkeypatch):
    temp_store = RedditMissionStore(file_path=str(tmp_path / "reddit_missions.json"))
    monkeypatch.setattr(main, "reddit_mission_store", temp_store)
    monkeypatch.setattr(
        main,
        "list_saved_reddit_sessions",
        lambda: [{"profile_name": "reddit_amy", "has_valid_session": True}],
    )

    request = main.RedditMissionCreateRequest(
        execution=main.RedditExecutionRequest(
            actors=[main.RedditExecutionActor(profile_name="reddit_amy")],
            target=main.RedditExecutionTarget(
                kind="comment",
                strategy="explicit",
                target_comment_url="https://www.reddit.com/r/womenshealth/comments/abc123/endometrial_biopsy/comment/xyz789/",
            ),
            action=main.RedditExecutionAction(
                type="reply",
                params=main.RedditExecutionActionParams(text="this is the reply"),
            ),
        ),
        cadence=main.RedditMissionCadence(type="once"),
    )

    asyncio.run(main.create_reddit_mission(request=request, current_user={"username": "tester"}))
    stored = temp_store.list_missions()[0]

    assert stored["target_comment_url"].endswith("/comment/xyz789/")
    assert stored["execution_request"]["target"]["kind"] == "comment"


def test_validate_reddit_program_payload_accepts_subreddit_policies(tmp_path, monkeypatch):
    monkeypatch.setattr(
        main,
        "list_saved_reddit_sessions",
        lambda: [
            {"profile_name": "reddit_amy", "has_valid_session": True},
            {"profile_name": "reddit_beta", "has_valid_session": True},
        ],
    )
    payload = {
        "profile_selection": {"profile_names": ["reddit_amy", "reddit_beta"]},
        "schedule": {"start_at": "2026-03-09T08:00:00Z", "duration_days": 1, "timezone": "Europe/Zurich", "random_windows": [{"start_hour": 8, "end_hour": 9}]},
        "topic_constraints": {
            "subreddits": ["AskWomenOver40", "women"],
            "keywords": ["period"],
            "subreddit_policies": [
                {
                    "subreddit": "AskWomenOver40",
                    "allocation_weight": 2,
                    "auto_user_flair": True,
                    "requires_user_flair_for": ["create_post"],
                    "profile_user_flairs": {"reddit_amy": "divorced"},
                    "keyword_overrides": ["dating", "midlife"],
                    "minimum_comment_karma": 50,
                    "minimum_comment_karma_for": ["comment_post", "reply_comment"],
                    "blocked_warmup_stages": ["new"],
                }
            ],
            "proof_matrix": [
                {
                    "mode": "per_profile_per_subreddit",
                    "action": "comment_post",
                    "day_offset": 0,
                }
            ],
        },
        "content_assignments": {"items": []},
        "engagement_quotas": {
            "posts_min_per_day": 1,
            "posts_max_per_day": 1,
            "upvotes_min_per_day": 0,
            "upvotes_max_per_day": 0,
            "comment_upvote_min_per_day": 0,
            "comment_upvote_max_per_day": 0,
            "reply_min_per_day": 0,
            "reply_max_per_day": 0,
            "random_reply_templates": [],
            "random_upvote_action": "upvote_post",
        },
        "generation_config": {
            "style_sample_count": 3,
            "writing_rule_paths": [],
            "uniqueness_scope": "program",
        },
        "notification_config": {
            "email_enabled": True,
            "email_account_mode": "default_gog_account",
            "daily_summary_hour": 20,
            "hard_failure_alerts_enabled": False,
        },
    }

    main._validate_reddit_program_payload(payload)


def test_reddit_program_status_exposes_join_and_notification_fields(tmp_path, monkeypatch):
    temp_store = RedditProgramStore(file_path=str(tmp_path / "reddit_programs.json"))
    monkeypatch.setattr(main, "reddit_program_store", temp_store)

    program = temp_store.create_program(
        {
            "profile_selection": {"profile_names": ["reddit_amy"]},
            "schedule": {"start_at": "2026-03-09T08:00:00Z", "duration_days": 1, "timezone": "Europe/Zurich", "random_windows": [{"start_hour": 8, "end_hour": 9}]},
            "topic_constraints": {
                "subreddits": ["WomensHealth"],
                "keywords": ["biopsy"],
                "mandatory_join_urls": ["https://www.reddit.com/r/WomensHealth/"],
            },
            "content_assignments": {"items": []},
            "engagement_quotas": {
                "posts_min_per_day": 1,
                "posts_max_per_day": 1,
                "upvotes_min_per_day": 1,
                "upvotes_max_per_day": 1,
                "comment_upvote_min_per_day": 0,
                "comment_upvote_max_per_day": 0,
                "reply_min_per_day": 1,
                "reply_max_per_day": 1,
                "random_reply_templates": [],
                "random_upvote_action": "upvote_post",
            },
            "generation_config": {
                "style_sample_count": 3,
                "writing_rule_paths": [],
                "uniqueness_scope": "program",
            },
            "realism_policy": {
                "forbid_own_content_interactions": True,
                "require_conversation_context": True,
                "require_subreddit_style_match": True,
                "forbid_operator_language": True,
                "forbid_meta_testing_language": True,
            },
            "notification_config": {
                "email_enabled": True,
                "email_account_mode": "default_gog_account",
                "daily_summary_hour": 20,
                "hard_failure_alerts_enabled": False,
                "recipient_email": "nikitalienov@gmail.com",
            },
            "verification_contract": {
                "require_success_confirmed": True,
                "require_attempt_id": True,
                "required_evidence_summary": True,
                "required_target_reference": True,
            },
            "execution_policy": {
                "strict_quotas": True,
                "allow_target_reuse_within_day": False,
                "cooldown_minutes": 0,
                "max_actions_per_tick": 5,
                "max_discovery_posts_per_subreddit": 4,
                "max_comment_candidates_per_post": 4,
                "retry_delay_minutes": 5,
                "max_attempts_per_item": 2,
            },
            "metadata": {},
        }
    )

    response = asyncio.run(main.get_reddit_program_status(program_id=program["id"], current_user={"username": "tester"}))

    assert "join_progress_matrix" in response
    assert "notification_log" in response
    assert "contract_totals" in response
    assert "failure_summary" in response
    assert "recent_generation_evidence" in response
    assert response["realism_policy"]["forbid_own_content_interactions"] is True


def test_reddit_program_operator_view_flattens_rows_and_proof_flags(tmp_path, monkeypatch):
    temp_store = RedditProgramStore(file_path=str(tmp_path / "reddit_programs.json"))
    monkeypatch.setattr(main, "reddit_program_store", temp_store)

    async def fake_list_forensic_attempts(*, filters=None, limit=50):
        assert filters == {"run_id": "reddit_program_testview"}
        return [
            {
                "attempt_id": "attempt_comment_latest",
                "status": "completed",
                "final_verdict": "success_confirmed",
                "failure_class": None,
                "started_at": "2026-03-09T08:10:00Z",
                "ended_at": "2026-03-09T08:11:00Z",
                "metadata": {"work_item_id": "work_comment"},
            },
            {
                "attempt_id": "attempt_comment_old",
                "status": "failed",
                "final_verdict": "failed_confirmed",
                "failure_class": "verification_failed",
                "started_at": "2026-03-09T08:00:00Z",
                "ended_at": "2026-03-09T08:01:00Z",
                "metadata": {"work_item_id": "work_comment"},
            },
            {
                "attempt_id": "attempt_post_latest",
                "status": "failed",
                "final_verdict": "failed_confirmed",
                "failure_class": "community_restricted",
                "started_at": "2026-03-09T08:20:00Z",
                "ended_at": "2026-03-09T08:21:00Z",
                "metadata": {"work_item_id": "work_post"},
            },
        ]

    async def fake_get_forensic_attempt_detail(attempt_id: str):
        details = {
            "attempt_comment_latest": {
                "attempt": {
                    "attempt_id": "attempt_comment_latest",
                    "final_verdict": "success_confirmed",
                    "metadata": {"subreddit": "Healthyhooha"},
                },
                "artifacts": [
                    {"artifact_type": "screenshot", "download_url": "/forensics/artifacts/comment-shot"},
                ],
                "verdict": {"final_verdict": "success_confirmed"},
            },
            "attempt_post_latest": {
                "attempt": {
                    "attempt_id": "attempt_post_latest",
                    "final_verdict": "failed_confirmed",
                    "metadata": {"subreddit": "WomensHealth"},
                },
                "artifacts": [],
                "verdict": {"final_verdict": "failed_confirmed"},
            },
        }
        return details[attempt_id]

    monkeypatch.setattr(main, "list_forensic_attempts", fake_list_forensic_attempts)
    monkeypatch.setattr(main, "get_forensic_attempt_detail", fake_get_forensic_attempt_detail)

    program = {
        "id": "reddit_program_testview",
        "status": "active",
        "next_run_at": "2026-03-09T08:30:00Z",
        "spec": {
            "profile_selection": {"profile_names": ["reddit_alpha", "reddit_beta"]},
            "schedule": {"timezone": "Europe/Zurich"},
            "execution_policy": {"max_attempts_per_item": 4},
        },
        "contract_totals": {"comment_post": 1, "create_post": 1},
        "remaining_contract": {"create_post": 1},
        "daily_progress": {
            "2026-03-09": {
                "reddit_alpha": {
                    "planned": {"comment_post": 1},
                    "completed": {"comment_post": 1},
                    "pending": {},
                    "blocked": {},
                },
                "reddit_beta": {
                    "planned": {"create_post": 1},
                    "completed": {},
                    "pending": {},
                    "blocked": {"create_post": 1},
                },
            }
        },
        "failure_summary": {"by_action": {"create_post": 1}, "by_profile": {"reddit_beta": 1}, "by_subreddit": {}, "by_class": {"community_restricted": 1}},
        "notification_log": [],
        "compiled": {
            "work_items": [
                {
                    "id": "work_comment",
                    "profile_name": "reddit_alpha",
                    "local_date": "2026-03-09",
                    "action": "comment_post",
                    "status": "completed",
                    "attempts": 2,
                    "scheduled_at": "2026-03-09T08:00:00Z",
                    "completed_at": "2026-03-09T08:11:00Z",
                    "target_url": "https://www.reddit.com/r/Healthyhooha/comments/thread-1/",
                    "target_comment_url": None,
                    "subreddit": "Healthyhooha",
                    "result": {
                        "attempt_id": "attempt_comment_latest",
                        "final_verdict": "success_confirmed",
                        "target_url": "https://www.reddit.com/r/Healthyhooha/comments/thread-1/",
                        "identity_evidence": {
                            "chosen_flair": "Divorced",
                            "status": "applied",
                        },
                    },
                    "generation_evidence": {
                        "combined_text": "Go back for a swab.",
                        "persona_id": "catherine_authority_frame",
                        "persona_role": "authority",
                        "case_style_applied": "proper_case",
                        "word_count": 5,
                        "rule_source_hashes": {"negative-patterns.md": "hash-neg"},
                        "novelty_validation": {"similarity_checks": {}},
                    },
                    "source": "proof_matrix",
                },
                {
                    "id": "work_post",
                    "profile_name": "reddit_beta",
                    "local_date": "2026-03-09",
                    "action": "create_post",
                    "status": "blocked",
                    "attempts": 1,
                    "scheduled_at": "2026-03-09T08:20:00Z",
                    "completed_at": None,
                    "target_url": "https://www.reddit.com/r/WomensHealth/comments/thread-2/",
                    "target_comment_url": None,
                    "subreddit": "WomensHealth",
                    "error": "community restriction",
                    "result": {
                        "attempt_id": "attempt_post_latest",
                        "final_verdict": "failed_confirmed",
                        "target_url": "https://www.reddit.com/r/WomensHealth/comments/thread-2/",
                    },
                    "generation_evidence": {
                        "combined_text": "two weeks with open skin is a bad plan.",
                        "persona_id": "amy_blunt_triage",
                        "persona_role": "blunt_critique",
                        "case_style_applied": "lowercase",
                        "word_count": 9,
                        "rule_source_hashes": {"negative-patterns.md": "hash-neg"},
                        "novelty_validation": {
                            "similarity_checks": {
                                "same_thread": {
                                    "sequence_ratio": 0.81,
                                    "token_overlap": 0.7,
                                    "ngram_overlap": 0.5,
                                    "opening_overlap": True,
                                    "exact_duplicate": False
                                }
                            }
                        },
                    },
                },
            ]
        },
    }
    temp_store.save_program(program)

    response = asyncio.run(
        main.get_reddit_program_operator_view(
            program_id="reddit_program_testview",
            local_date="2026-03-09",
            profile_name=None,
            current_user={"username": "tester"},
        )
    )

    assert response["program"]["selected_local_date"] == "2026-03-09"
    assert response["program"]["available_actions"] == ["comment_post", "create_post"]
    assert len(response["profiles_by_day"]) == 2

    alpha = next(row for row in response["profiles_by_day"] if row["profile_name"] == "reddit_alpha")
    assert alpha["proof_coverage"] == {
        "required_actions": 1,
        "with_url": 1,
        "with_screenshot": 1,
        "with_attempt": 1,
        "success_confirmed": 1,
        "unsafe_rollout": 0,
    }

    comment_row = next(row for row in response["action_rows"] if row["work_item_id"] == "work_comment")
    assert comment_row["screenshot_artifact_url"] == "/forensics/artifacts/comment-shot"
    assert comment_row["proof_flags"]["success_confirmed"] is True
    assert comment_row["persona_role"] == "authority"
    assert comment_row["word_count"] == 5
    assert comment_row["user_flair"] == "Divorced"
    assert [entry["attempt_id"] for entry in comment_row["attempt_history"]] == [
        "attempt_comment_latest",
        "attempt_comment_old",
    ]

    post_row = next(row for row in response["action_rows"] if row["work_item_id"] == "work_post")
    assert post_row["proof_flags"]["has_screenshot"] is False
    assert post_row["proof_flags"]["unsafe_rollout"] is True
    assert post_row["error"] == "community restriction"
    assert post_row["final_verdict"] == "failed_confirmed"
    assert post_row["semantic_similarity_flags"] == ["same_thread"]
    assert response["program"]["unsafe_rollout_flags"]["rows"] == 1
    assert response["proof_matrix"][0]["user_flair"] == "Divorced"

    filtered = asyncio.run(
        main.get_reddit_program_operator_view(
            program_id="reddit_program_testview",
            local_date="2026-03-09",
            profile_name="reddit_beta",
            current_user={"username": "tester"},
        )
    )
    assert [row["profile_name"] for row in filtered["profiles_by_day"]] == ["reddit_beta"]
    assert [row["work_item_id"] for row in filtered["action_rows"]] == ["work_post"]


def test_reddit_program_operator_view_does_not_flag_duplicate_upvote_targets_as_unsafe(tmp_path, monkeypatch):
    temp_store = RedditProgramStore(file_path=str(tmp_path / "reddit_programs.json"))
    monkeypatch.setattr(main, "reddit_program_store", temp_store)

    async def fake_list_forensic_attempts(*, filters=None, limit=50):
        assert filters == {"run_id": "reddit_program_dupe_upvotes"}
        return []

    monkeypatch.setattr(main, "list_forensic_attempts", fake_list_forensic_attempts)
    monkeypatch.setattr(main, "get_forensic_attempt_detail", lambda *_args, **_kwargs: asyncio.sleep(0, result={}))

    temp_store.save_program(
        {
            "id": "reddit_program_dupe_upvotes",
            "status": "active",
            "next_run_at": "2026-03-09T08:30:00Z",
            "spec": {
                "profile_selection": {"profile_names": ["reddit_alpha", "reddit_beta"]},
                "schedule": {"timezone": "Europe/Zurich"},
                "execution_policy": {"max_attempts_per_item": 4},
            },
            "contract_totals": {"upvote_post": 2},
            "remaining_contract": {"upvote_post": 2},
            "daily_progress": {
                "2026-03-09": {
                    "reddit_alpha": {"planned": {"upvote_post": 1}, "completed": {}, "pending": {"upvote_post": 1}, "blocked": {}},
                    "reddit_beta": {"planned": {"upvote_post": 1}, "completed": {}, "pending": {"upvote_post": 1}, "blocked": {}},
                }
            },
            "failure_summary": {"by_action": {}, "by_profile": {}, "by_subreddit": {}, "by_class": {}},
            "notification_log": [],
            "compiled": {
                "work_items": [
                    {
                        "id": "work_alpha",
                        "profile_name": "reddit_alpha",
                        "local_date": "2026-03-09",
                        "action": "upvote_post",
                        "status": "pending",
                        "attempts": 0,
                        "scheduled_at": "2026-03-09T08:00:00Z",
                        "target_url": "https://www.reddit.com/r/Healthyhooha/comments/thread-1/",
                    },
                    {
                        "id": "work_beta",
                        "profile_name": "reddit_beta",
                        "local_date": "2026-03-09",
                        "action": "upvote_post",
                        "status": "pending",
                        "attempts": 0,
                        "scheduled_at": "2026-03-09T08:05:00Z",
                        "target_url": "https://www.reddit.com/r/Healthyhooha/comments/thread-1/",
                    },
                ]
            },
        }
    )

    response = asyncio.run(
        main.get_reddit_program_operator_view(
            program_id="reddit_program_dupe_upvotes",
            local_date="2026-03-09",
            profile_name=None,
            current_user={"username": "tester"},
        )
    )

    assert response["program"]["unsafe_rollout_flags"]["rows"] == 0
    assert all(row["proof_flags"]["unsafe_rollout"] is False for row in response["action_rows"])


def test_run_reddit_program_now_rejects_overlapping_execution(tmp_path, monkeypatch):
    temp_store = RedditProgramStore(file_path=str(tmp_path / "reddit_programs.json"))
    monkeypatch.setattr(main, "reddit_program_store", temp_store)

    program = temp_store.create_program(
        {
            "profile_selection": {"profile_names": ["reddit_amy"]},
            "schedule": {"start_at": "2026-03-09T08:00:00Z", "duration_days": 1, "timezone": "Europe/Zurich", "random_windows": [{"start_hour": 8, "end_hour": 9}]},
            "topic_constraints": {"subreddits": ["WomensHealth"], "keywords": ["biopsy"]},
            "content_assignments": {"items": []},
            "engagement_quotas": {
                "posts_min_per_day": 0,
                "posts_max_per_day": 0,
                "upvotes_min_per_day": 0,
                "upvotes_max_per_day": 0,
                "comment_upvote_min_per_day": 0,
                "comment_upvote_max_per_day": 0,
                "reply_min_per_day": 0,
                "reply_max_per_day": 0,
                "random_reply_templates": [],
                "random_upvote_action": "upvote_post",
            },
            "generation_config": {
                "style_sample_count": 3,
                "writing_rule_paths": [],
                "uniqueness_scope": "program",
            },
            "realism_policy": {
                "forbid_own_content_interactions": True,
                "require_conversation_context": True,
                "require_subreddit_style_match": True,
                "forbid_operator_language": True,
                "forbid_meta_testing_language": True,
            },
            "notification_config": {
                "email_enabled": False,
                "hard_failure_alerts_enabled": False,
            },
            "verification_contract": {
                "require_success_confirmed": True,
                "require_attempt_id": True,
                "required_evidence_summary": True,
                "required_target_reference": True,
            },
            "execution_policy": {
                "strict_quotas": True,
                "allow_target_reuse_within_day": False,
                "cooldown_minutes": 0,
                "max_actions_per_tick": 5,
                "max_discovery_posts_per_subreddit": 4,
                "max_comment_candidates_per_post": 4,
                "retry_delay_minutes": 5,
                "max_attempts_per_item": 2,
            },
            "metadata": {},
        }
    )

    class _BusyOrchestrator:
        async def process_program(self, program_id: str, *, force_due: bool = True):
            raise main.RedditProgramAlreadyRunningError(f"reddit program already running: {program_id}")

    monkeypatch.setattr(main, "reddit_program_orchestrator", _BusyOrchestrator())

    with pytest.raises(main.HTTPException) as excinfo:
        asyncio.run(main.run_reddit_program_now(program_id=program["id"], current_user={"username": "tester"}))

    assert excinfo.value.status_code == 409
    assert "already running" in str(excinfo.value.detail)
