import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from premium_models import PremiumRunCreateRequest
from premium_models import PremiumProfileConfig


def test_run_spec_validation_rejects_invalid_feed_mix():
    with pytest.raises(ValidationError):
        PremiumRunCreateRequest(
            run_spec={
                "profile_name": "Vanessa Hines",
                "feed_plan": {"total_posts": 4, "character_posts": 4, "ambient_posts": 1},
                "group_discovery": {"topic_seed": "menopause groups", "allow_join_new": True, "join_pending_policy": "try_next_group"},
                "engagement_recipe": {"likes_per_cycle": 2, "shares_per_cycle": 1, "replies_per_cycle": 1, "share_target": "own_feed"},
                "schedule": {
                    "duration_days": 7,
                    "timezone": "America/New_York",
                    "random_windows": [{"start_hour": 8, "end_hour": 22}],
                },
                "verification_contract": {},
            }
        )


def test_run_spec_validation_rejects_invalid_random_window():
    with pytest.raises(ValidationError):
        PremiumRunCreateRequest(
            run_spec={
                "profile_name": "Vanessa Hines",
                "feed_plan": {"total_posts": 4, "character_posts": 3, "ambient_posts": 1},
                "group_discovery": {"topic_seed": "menopause groups", "allow_join_new": True, "join_pending_policy": "try_next_group"},
                "engagement_recipe": {"likes_per_cycle": 2, "shares_per_cycle": 1, "replies_per_cycle": 1, "share_target": "own_feed"},
                "schedule": {
                    "duration_days": 7,
                    "timezone": "America/New_York",
                    "random_windows": [{"start_hour": 22, "end_hour": 22}],
                },
                "verification_contract": {},
            }
        )


def test_run_spec_defaults_match_strict_pilot_baseline():
    payload = PremiumRunCreateRequest(
        run_spec={
            "profile_name": "Vanessa Hines",
        }
    )
    spec = payload.run_spec
    assert spec.feed_plan.total_posts == 4
    assert spec.feed_plan.character_posts == 3
    assert spec.feed_plan.ambient_posts == 1
    assert spec.engagement_recipe.likes_per_cycle == 2
    assert spec.engagement_recipe.shares_per_cycle == 1
    assert spec.engagement_recipe.replies_per_cycle == 1
    assert spec.verification_contract.require_evidence is True


def test_execution_policy_defaults_enable_safety_gates():
    cfg = PremiumProfileConfig(
        character_profile={"persona_description": "supportive member"},
    )
    policy = cfg.execution_policy
    assert policy.dedupe_precheck_enabled is True
    assert policy.dedupe_recent_feed_posts == 5
    assert policy.dedupe_threshold == 0.90
    assert policy.block_on_duplicate is True
    assert policy.single_submit_guard is True
    assert policy.tunnel_recovery_cycles == 2
    assert policy.tunnel_recovery_delay_seconds == 90
