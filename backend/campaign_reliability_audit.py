"""
Campaign reliability audit helpers.

Builds an operator-focused reliability report from queue history plus current
system health snapshots. The goal is to distinguish harmless self-healed retry
noise from issues likely to cause manual operator pain.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Set


VERDICT_GO = "go"
VERDICT_WATCHLIST = "go with watchlist"
VERDICT_FIXES = "needs fixes before trust"

FIX_NONE = "no fix needed right now"
FIX_WATCH = "watch only"
FIX_RECOMMENDED = "fix recommended"
FIX_REQUIRED = "fix required"

FAILURE_CATEGORY_ORDER = [
    "infra/transport",
    "page-load/navigation",
    "comment-open",
    "input-visibility",
    "post-verification",
    "restriction/checkpoint",
    "other",
]

FAILURE_CATEGORY_EXPLANATIONS = {
    "infra/transport": "Network, proxy, or navigation transport errors blocked the first attempt.",
    "page-load/navigation": "The destination post or page state did not become reliably reachable or visible.",
    "comment-open": "The bot reached the post but failed to open the comments composer.",
    "input-visibility": "The bot could not confirm the comment input or typed text was visible.",
    "post-verification": "The bot typed successfully but post confirmation failed.",
    "restriction/checkpoint": "Facebook restriction, checkpoint, or lock signals interrupted posting.",
    "other": "A failure occurred but did not map cleanly to a known recovery class.",
}


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _short_error(value: Optional[str], limit: int = 140) -> str:
    text = (value or "").strip()
    if not text:
        return "unknown"
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _normalize_profile_name(name: Optional[str]) -> str:
    return (name or "").replace(" ", "_").replace("/", "_").lower()


def classify_failure_category(error: Optional[str]) -> str:
    text = (error or "").lower()
    if not text:
        return "other"

    if any(token in text for token in [
        "checkpoint",
        "account has been locked",
        "account locked",
        "restriction",
        "restricted",
        "throttle",
        "throttled",
        "banned",
        "ban ",
    ]):
        return "restriction/checkpoint"

    if any(token in text for token in [
        "err_tunnel_connection_failed",
        "err_empty_response",
        "net::",
        "proxy",
        "connection",
        "network",
        "timeout",
        "timed out",
        "tunnel",
    ]):
        return "infra/transport"

    if any(token in text for token in [
        "comments not opened",
        "write a comment",
        "could not open comments",
        "comment button",
    ]):
        return "comment-open"

    if any(token in text for token in [
        "typed text not visible",
        "text not visible",
        "comment input field",
        "input field not visible",
    ]):
        return "input-visibility"

    if any(token in text for token in [
        "comment not posted",
        "no comments are visible",
        "does not display any posted comments",
        "no posted comments",
    ]):
        return "post-verification"

    if any(token in text for token in [
        "post not visible",
        "page.goto",
        "navigation to",
        "comments section is empty",
        "could not determine if post loaded",
    ]):
        return "page-load/navigation"

    return "other"


def _result_retry_mode(result: Dict[str, Any]) -> str:
    if not result.get("is_retry"):
        return "none"
    if result.get("method") == "auto_retry" or result.get("auto_retry_round") is not None:
        return "auto"
    return "manual"


def _get_total_jobs(campaign: Dict[str, Any]) -> int:
    total_count = campaign.get("total_count")
    if isinstance(total_count, int) and total_count > 0:
        return total_count
    comments = campaign.get("comments") or []
    results = campaign.get("results") or []
    return len(comments) or len(results)


def _get_final_completed_jobs(campaign: Dict[str, Any], per_job_results: Dict[int, List[Dict[str, Any]]]) -> int:
    success_count = campaign.get("success_count")
    if isinstance(success_count, int) and success_count >= 0:
        return success_count
    return sum(1 for results in per_job_results.values() if any(item.get("success") for item in results))


def _collect_current_restricted_names(appeal_status: Optional[Dict[str, Any]]) -> Set[str]:
    restricted: Set[str] = set()
    for profile in (appeal_status or {}).get("profiles", []):
        if profile.get("status") == "restricted":
            restricted.add(_normalize_profile_name(profile.get("profile_name")))
    return restricted


def _build_fix_matrix(
    *,
    verdict: str,
    retry_overhead_rate: float,
    root_cause_index: Dict[str, Dict[str, Any]],
    restriction_fallout_linked: List[str],
) -> List[Dict[str, Any]]:
    infra = root_cause_index.get("infra/transport", {})
    comment_open = root_cause_index.get("comment-open", {})
    input_visibility = root_cause_index.get("input-visibility", {})
    post_verification = root_cause_index.get("post-verification", {})
    restriction = root_cause_index.get("restriction/checkpoint", {})

    matrix: List[Dict[str, Any]] = []

    matrix.append({
        "area": "overall delivery path",
        "status": FIX_NONE if verdict == VERDICT_GO else (FIX_WATCH if verdict == VERDICT_WATCHLIST else FIX_REQUIRED),
        "reason": (
            "All qualifying campaigns fully completed with acceptable retry overhead."
            if verdict == VERDICT_GO
            else "Campaigns self-healed, but retry patterns deserve active watch."
            if verdict == VERDICT_WATCHLIST
            else "Current retry behavior is too risky to trust without further hardening."
        ),
    })

    if infra.get("count", 0) == 0:
        infra_status = FIX_NONE
        infra_reason = "No meaningful infrastructure-triggered recovery was observed."
    elif retry_overhead_rate <= 15:
        infra_status = FIX_WATCH
        infra_reason = "Infrastructure noise dominated recoveries, but the system self-healed and stayed within the retry overhead budget."
    elif retry_overhead_rate <= 25:
        infra_status = FIX_RECOMMENDED
        infra_reason = "Infrastructure-triggered retries are noticeable enough to justify session/proxy quarantine improvements."
    else:
        infra_status = FIX_REQUIRED
        infra_reason = "Infrastructure-triggered retries exceed the acceptable retry overhead budget."
    matrix.append({"area": "infra stability", "status": infra_status, "reason": infra_reason})

    if comment_open.get("campaigns_affected", 0) >= 3:
        comment_open_status = FIX_RECOMMENDED
        comment_open_reason = "Comment-open failures repeated across multiple campaigns and are no longer isolated noise."
    elif comment_open.get("count", 0) > 0:
        comment_open_status = FIX_WATCH
        comment_open_reason = "Comment-open failures appeared but stayed isolated and self-healed."
    else:
        comment_open_status = FIX_NONE
        comment_open_reason = "No recurring comment-open weakness was observed."
    matrix.append({"area": "comment-open robustness", "status": comment_open_status, "reason": comment_open_reason})

    verification_campaigns = max(
        input_visibility.get("campaigns_affected", 0),
        post_verification.get("campaigns_affected", 0),
    )
    verification_count = input_visibility.get("count", 0) + post_verification.get("count", 0)
    if verification_campaigns >= 3:
        verification_status = FIX_RECOMMENDED
        verification_reason = "Input/post verification failures repeated across multiple campaigns and are worth hardening."
    elif verification_count > 0:
        verification_status = FIX_WATCH
        verification_reason = "Verification failures appeared but stayed isolated and were recovered automatically."
    else:
        verification_status = FIX_NONE
        verification_reason = "No recurring input/post verification weakness was observed."
    matrix.append({"area": "input/post verification", "status": verification_status, "reason": verification_reason})

    if restriction_fallout_linked:
        restriction_status = FIX_REQUIRED
        restriction_reason = "Current restricted profiles overlap with the audited campaign set and need stronger protection."
    elif restriction.get("count", 0) > 0:
        restriction_status = FIX_WATCH
        restriction_reason = "Restriction-like signals appeared during posting, but no current fallout remains."
    else:
        restriction_status = FIX_NONE
        restriction_reason = "No current restriction fallout is attributable to the audited campaign window."
    matrix.append({"area": "restriction protection", "status": restriction_status, "reason": restriction_reason})

    return matrix


def _build_recommended_fixes(fix_matrix: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    recommendations: List[Dict[str, Any]] = []
    priority = 1

    for item in fix_matrix:
        status = item.get("status")
        area = item.get("area")
        if status not in (FIX_RECOMMENDED, FIX_REQUIRED):
            continue

        if area == "infra stability":
            recommendations.append({
                "priority": priority,
                "title": "Add infrastructure failure budgets and temporary quarantine for flapping sessions",
                "status": status,
                "expected_impact": "Reduce retry overhead and operator confusion from repeated transport errors.",
            })
        elif area == "comment-open robustness":
            recommendations.append({
                "priority": priority,
                "title": "Harden the deterministic comment-open path before vision fallback",
                "status": status,
                "expected_impact": "Improve first-pass success rate on posts where the comments composer is slow or inconsistent.",
            })
        elif area == "input/post verification":
            recommendations.append({
                "priority": priority,
                "title": "Loosen screenshot-only verification into a hybrid DOM + visual confirmation path",
                "status": status,
                "expected_impact": "Reduce false negatives that force retry recovery even when the post flow mostly succeeded.",
            })
        elif area == "restriction protection":
            recommendations.append({
                "priority": priority,
                "title": "Quarantine profiles immediately after restriction-like UI signals during posting",
                "status": status,
                "expected_impact": "Reduce operator pain from campaign-linked restriction fallout.",
            })
        else:
            recommendations.append({
                "priority": priority,
                "title": "Investigate recurring campaign delivery instability",
                "status": status,
                "expected_impact": "Reduce operator-visible delivery risk.",
            })

        priority += 1

    return recommendations


def build_campaign_reliability_audit(
    *,
    history: Iterable[Dict[str, Any]],
    analytics_summary: Optional[Dict[str, Any]] = None,
    appeal_status: Optional[Dict[str, Any]] = None,
    health_deep: Optional[Dict[str, Any]] = None,
    lookback_days: int = 2,
    min_total_count: int = 6,
) -> Dict[str, Any]:
    date_to = datetime.now(UTC).date()
    date_from = date_to - timedelta(days=max(lookback_days - 1, 0))

    qualifying_campaigns: List[Dict[str, Any]] = []
    root_cause_counts: Counter[str] = Counter()
    root_cause_campaigns: Dict[str, Set[str]] = defaultdict(set)
    root_cause_profiles: Dict[str, Set[str]] = defaultdict(set)
    root_cause_samples: Dict[str, Counter[str]] = defaultdict(Counter)

    total_original_jobs = 0
    total_final_completed = 0
    total_retry_attempts = 0
    total_first_pass_failures = 0
    total_recovered_jobs = 0
    total_unrecovered_jobs = 0
    manual_intervention_campaigns = 0

    campaign_profile_names: Set[str] = set()

    for campaign in history:
        total_jobs = _get_total_jobs(campaign)
        if total_jobs < min_total_count:
            continue

        completed_at = _parse_iso_datetime(campaign.get("completed_at") or campaign.get("created_at"))
        if not completed_at:
            continue
        if completed_at.date() < date_from or completed_at.date() > date_to:
            continue

        results = campaign.get("results") or []
        per_job_results: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
        for result in results:
            job_index = result.get("job_index")
            if isinstance(job_index, int):
                per_job_results[job_index].append(result)

        original_results = [result for result in results if not result.get("is_retry")]
        retry_results = [result for result in results if result.get("is_retry")]
        failed_original = [result for result in original_results if not result.get("success")]

        first_pass_failures = {result.get("job_index") for result in failed_original if isinstance(result.get("job_index"), int)}
        recovered_jobs = {
            job_index for job_index, job_results in per_job_results.items()
            if any(not item.get("success") for item in job_results) and any(item.get("success") for item in job_results)
        }
        unrecovered_jobs = {
            job_index for job_index, job_results in per_job_results.items()
            if job_results and all(not item.get("success") for item in job_results)
        }
        final_completed_jobs = _get_final_completed_jobs(campaign, per_job_results)

        failure_categories = Counter()
        failure_samples: Dict[str, Counter[str]] = defaultdict(Counter)
        for result in failed_original:
            category = classify_failure_category(result.get("error"))
            failure_categories[category] += 1
            failure_samples[category][_short_error(result.get("error"))] += 1
            profile_name = result.get("profile_name")
            if profile_name:
                campaign_profile_names.add(_normalize_profile_name(profile_name))
                root_cause_profiles[category].add(_normalize_profile_name(profile_name))
            root_cause_counts[category] += 1
            root_cause_campaigns[category].add(campaign.get("id", "unknown"))
            root_cause_samples[category][_short_error(result.get("error"))] += 1

        retry_modes = Counter(_result_retry_mode(result) for result in retry_results)
        manual_intervention_required = bool(
            unrecovered_jobs or retry_modes.get("manual", 0) > 0 or campaign.get("auto_retry", {}).get("status") == "exhausted"
        )

        if manual_intervention_required:
            manual_intervention_campaigns += 1

        qualifying_campaigns.append({
            "campaign_id": campaign.get("id"),
            "completed_at": campaign.get("completed_at"),
            "created_by": campaign.get("created_by"),
            "url": campaign.get("url"),
            "status": campaign.get("status"),
            "total_jobs": total_jobs,
            "final_completed_jobs": final_completed_jobs,
            "first_pass_failure_count": len(first_pass_failures),
            "retry_attempt_count": len(retry_results),
            "recovered_job_count": len(recovered_jobs),
            "unrecovered_job_count": len(unrecovered_jobs),
            "operator_intervention_required": manual_intervention_required,
            "auto_retry_status": campaign.get("auto_retry", {}).get("status"),
            "top_failure_classes": [
                {
                    "category": category,
                    "count": count,
                    "sample_error": failure_samples[category].most_common(1)[0][0],
                }
                for category, count in sorted(
                    failure_categories.items(),
                    key=lambda item: (-item[1], FAILURE_CATEGORY_ORDER.index(item[0]) if item[0] in FAILURE_CATEGORY_ORDER else 999),
                )[:3]
            ],
        })

        total_original_jobs += total_jobs
        total_final_completed += final_completed_jobs
        total_retry_attempts += len(retry_results)
        total_first_pass_failures += len(first_pass_failures)
        total_recovered_jobs += len(recovered_jobs)
        total_unrecovered_jobs += len(unrecovered_jobs)

    qualifying_campaigns.sort(key=lambda item: item.get("completed_at") or "", reverse=True)

    retry_overhead_rate = (total_retry_attempts / total_original_jobs * 100) if total_original_jobs else 0.0
    current_restricted_profiles = (health_deep or {}).get("checks", {}).get("profiles", {}).get("restricted", 0)
    appeal_profiles = (appeal_status or {}).get("profiles", [])
    restricted_profile_names = _collect_current_restricted_names(appeal_status)
    linked_restriction_fallout = sorted(restricted_profile_names.intersection(campaign_profile_names))

    automation_categories = {"page-load/navigation", "comment-open", "input-visibility", "post-verification", "restriction/checkpoint", "other"}
    repeated_automation_categories = [
        category
        for category in automation_categories
        if len(root_cause_campaigns.get(category, set())) >= 3
    ]
    automation_failure_count = sum(root_cause_counts.get(category, 0) for category in automation_categories)
    infra_failure_count = root_cause_counts.get("infra/transport", 0)

    if (
        total_unrecovered_jobs > 0
        or manual_intervention_campaigns > 0
        or linked_restriction_fallout
        or retry_overhead_rate > 25
    ):
        verdict = VERDICT_FIXES
    elif retry_overhead_rate > 15 or repeated_automation_categories:
        verdict = VERDICT_WATCHLIST
    else:
        verdict = VERDICT_GO

    root_causes = [
        {
            "category": category,
            "count": root_cause_counts.get(category, 0),
            "campaigns_affected": len(root_cause_campaigns.get(category, set())),
            "profiles_affected": len(root_cause_profiles.get(category, set())),
            "explanation": FAILURE_CATEGORY_EXPLANATIONS[category],
            "sample_errors": [sample for sample, _count in root_cause_samples.get(category, Counter()).most_common(3)],
        }
        for category in FAILURE_CATEGORY_ORDER
        if root_cause_counts.get(category, 0) > 0
    ]
    root_cause_index = {item["category"]: item for item in root_causes}

    fix_matrix = _build_fix_matrix(
        verdict=verdict,
        retry_overhead_rate=retry_overhead_rate,
        root_cause_index=root_cause_index,
        restriction_fallout_linked=linked_restriction_fallout,
    )
    recommended_fixes = _build_recommended_fixes(fix_matrix)

    summary = {
        "verdict": verdict,
        "campaign_count": len(qualifying_campaigns),
        "original_jobs": total_original_jobs,
        "final_completed_jobs": total_final_completed,
        "first_pass_failure_count": total_first_pass_failures,
        "retry_attempt_count": total_retry_attempts,
        "retry_overhead_rate": round(retry_overhead_rate, 1),
        "recovered_job_count": total_recovered_jobs,
        "unrecovered_job_count": total_unrecovered_jobs,
        "manual_intervention_campaigns": manual_intervention_campaigns,
        "current_restricted_profiles": current_restricted_profiles,
        "current_appeal_backlog": len(appeal_profiles),
        "dominant_retry_triggers": [item["category"] for item in root_causes[:3]],
        "automation_failure_count": automation_failure_count,
        "infra_failure_count": infra_failure_count,
    }

    return {
        "generated_at": _utcnow_iso(),
        "window": {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "lookback_days": lookback_days,
            "min_total_count": min_total_count,
        },
        "summary": summary,
        "campaigns": qualifying_campaigns,
        "root_causes": root_causes,
        "repeated_automation_categories": repeated_automation_categories,
        "fix_matrix": fix_matrix,
        "recommended_fixes": recommended_fixes,
        "fallout": {
            "analytics_summary": analytics_summary or {},
            "health_status": (health_deep or {}).get("status"),
            "current_restricted_profiles": current_restricted_profiles,
            "current_appeal_backlog": len(appeal_profiles),
            "linked_restriction_fallout": linked_restriction_fallout,
        },
        "notes": [
            "success_count is treated as final unique-job delivery, not first-pass success.",
            "results_len greater than total_jobs indicates retry activity, not corrupted history.",
            "API audit excludes Railway log correlation; use external production tooling if log-level matching is required.",
        ],
    }
