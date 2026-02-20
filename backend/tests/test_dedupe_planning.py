import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from name_dedupe_workflow import build_dedupe_plan


def test_dedupe_plan_is_deterministic_and_split_correctly():
    sessions = [
        {
            "profile_name": "alpha_profile",
            "display_name": "Alex Stone",
            "user_id": "1001",
            "has_valid_cookies": True,
        },
        {
            "profile_name": "beta_profile",
            "display_name": "Alex Stone",
            "user_id": "1002",
            "has_valid_cookies": True,
        },
        {
            "profile_name": "gamma_profile",
            "display_name": "Alex Stone",
            "user_id": "1003",
            "has_valid_cookies": False,
        },
        {
            "profile_name": "unique_profile",
            "display_name": "Unique Name",
            "user_id": "1004",
            "has_valid_cookies": True,
        },
    ]

    plan_a = build_dedupe_plan(sessions)
    plan_b = build_dedupe_plan(sessions)

    assert plan_a["plan_id"] == plan_b["plan_id"]
    assert plan_a["total_duplicate_groups"] == 1
    assert plan_a["total_profiles_to_rename"] == 2

    group = plan_a["duplicate_groups"][0]
    assert group["keep_profile"]["profile_name"] == "alpha_profile"
    assert len(group["rename_profiles"]) == 2

    target_names = [r["to_display_name"] for r in group["rename_profiles"]]
    assert len(target_names) == len(set(target_names))
    assert all(name != "Alex Stone" for name in target_names)
