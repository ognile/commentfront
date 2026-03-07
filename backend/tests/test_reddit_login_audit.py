import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reddit_login_audit import RedditLoginAudit, classify_reddit_failure, compare_reddit_audits


def test_classify_reddit_failure_detects_login_banner_error():
    audit = {
        "checkpoints": [
            {
                "name": "after_credential_submit",
                "visible_errors": ["Something went wrong logging in. Please try again."],
            }
        ]
    }

    assert classify_reddit_failure(audit, "Reddit login failed") == "login_banner_error"


def test_classify_reddit_failure_detects_protected_route_failure_after_public_profile():
    audit = {
        "checkpoints": [
            {
                "name": "profile_page",
                "url": "https://www.reddit.com/user/Neera_Allvere/",
                "visible_errors": [],
            },
            {
                "name": "protected_destination_verify_submit",
                "url": "https://www.reddit.com/login/?dest=https%3A%2F%2Fwww.reddit.com%2Fsubmit",
                "visible_errors": [],
            },
        ]
    }

    assert classify_reddit_failure(audit, "Reddit session failed authenticated destination verification") == "protected_routes_fail"


def test_compare_reddit_audits_surfaces_context_and_checkpoint_differences():
    reference = {
        "attempt_id": "ref",
        "context": {
            "user_agent": "iphone",
            "is_mobile": None,
            "has_touch": None,
            "launch_args": ["--disable-notifications"],
        },
        "checkpoints": [
            {
                "name": "after_credential_submit",
                "url": "https://www.reddit.com/otp",
                "otp_input_present": True,
                "login_inputs_present": False,
                "visible_errors": [],
                "cookie_names": ["csrf_token"],
            }
        ],
        "result": {"success": True},
    }
    standalone = {
        "attempt_id": "std",
        "context": {
            "user_agent": "android",
            "is_mobile": True,
            "has_touch": True,
            "launch_args": ["--disable-notifications"],
        },
        "checkpoints": [
            {
                "name": "after_credential_submit",
                "url": "https://www.reddit.com/login/",
                "otp_input_present": False,
                "login_inputs_present": True,
                "visible_errors": ["Something went wrong logging in. Please try again."],
                "cookie_names": ["csrf_token", "token_v2"],
            }
        ],
        "result": {"success": False},
    }

    diff = compare_reddit_audits(reference, standalone)

    assert diff["context_differences"]["user_agent"] == {"reference": "iphone", "standalone": "android"}
    assert diff["context_differences"]["is_mobile"] == {"reference": None, "standalone": True}
    assert "after_credential_submit" in diff["checkpoint_differences"]


def test_response_body_relevant_matches_reddit_auth_endpoints():
    assert RedditLoginAudit._response_body_relevant("https://www.reddit.com/svc/shreddit/account/login")
    assert RedditLoginAudit._response_body_relevant("https://www.reddit.com/svc/shreddit/account/login/otp")
    assert not RedditLoginAudit._response_body_relevant("https://www.reddit.com/user/mary_miaby/")


def test_normalize_body_preview_collapses_whitespace_and_truncates():
    text = "a  \n b\tc" * 400
    preview = RedditLoginAudit._normalize_body_preview(text, limit=12)

    assert preview == "a b ca b ca "
