import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { CampaignReliabilityAuditCard, type CampaignReliabilityAuditReport } from "@/components/analytics/CampaignReliabilityAuditCard";

const report: CampaignReliabilityAuditReport = {
  generated_at: "2026-03-05T20:55:00Z",
  window: {
    date_from: "2026-03-04",
    date_to: "2026-03-05",
    lookback_days: 2,
    min_total_count: 6,
  },
  summary: {
    verdict: "go with watchlist",
    campaign_count: 10,
    original_jobs: 172,
    final_completed_jobs: 172,
    first_pass_failure_count: 18,
    retry_attempt_count: 25,
    retry_overhead_rate: 14.5,
    recovered_job_count: 18,
    unrecovered_job_count: 0,
    manual_intervention_campaigns: 0,
    current_restricted_profiles: 0,
    current_appeal_backlog: 0,
    dominant_retry_triggers: ["infra/transport", "comment-open"],
  },
  campaigns: [
    {
      campaign_id: "2d233b50-clean-run",
      completed_at: "2026-03-05T18:10:00Z",
      created_by: "ops",
      total_jobs: 12,
      final_completed_jobs: 12,
      retry_attempt_count: 0,
      recovered_job_count: 0,
      unrecovered_job_count: 0,
      operator_intervention_required: false,
      top_failure_classes: [],
    },
    {
      campaign_id: "7f1f44c1-recovered",
      completed_at: "2026-03-05T17:40:00Z",
      created_by: "ops",
      total_jobs: 18,
      final_completed_jobs: 18,
      retry_attempt_count: 3,
      recovered_job_count: 2,
      unrecovered_job_count: 0,
      operator_intervention_required: false,
      top_failure_classes: [
        {
          category: "infra/transport",
          count: 2,
          sample_error: "Page.goto: net::ERR_TUNNEL_CONNECTION_FAILED",
        },
        {
          category: "comment-open",
          count: 1,
          sample_error: "Comments not opened",
        },
      ],
    },
  ],
  root_causes: [
    {
      category: "infra/transport",
      count: 12,
      campaigns_affected: 6,
      profiles_affected: 8,
      explanation: "Network, proxy, or navigation transport errors blocked the first attempt.",
      sample_errors: ["Page.goto: net::ERR_TUNNEL_CONNECTION_FAILED"],
    },
    {
      category: "comment-open",
      count: 4,
      campaigns_affected: 3,
      profiles_affected: 4,
      explanation: "The bot reached the post but failed to open the comments composer.",
      sample_errors: ["Comments not opened"],
    },
  ],
  fix_matrix: [
    {
      area: "overall delivery path",
      status: "watch only",
      reason: "Campaigns self-healed, but retry patterns deserve active watch.",
    },
    {
      area: "comment-open robustness",
      status: "fix recommended",
      reason: "Comment-open failures repeated across multiple campaigns and are no longer isolated noise.",
    },
  ],
  recommended_fixes: [
    {
      priority: 1,
      title: "Harden the deterministic comment-open path before vision fallback",
      status: "fix recommended",
      expected_impact: "Improve first-pass success rate on posts where the comments composer is slow or inconsistent.",
    },
  ],
};

describe("CampaignReliabilityAuditCard", () => {
  it("renders the operator verdict, top metrics, and campaign rows", () => {
    render(
      <CampaignReliabilityAuditCard
        report={report}
        loading={false}
        error={null}
        onRefresh={vi.fn()}
      />,
    );

    expect(screen.getByText("Campaign Reliability Audit")).toBeInTheDocument();
    expect(screen.getByText("go with watchlist")).toBeInTheDocument();
    expect(screen.getByText("172")).toBeInTheDocument();
    expect(screen.getByText("14.5%")).toBeInTheDocument();
    expect(screen.getByText("2d233b50")).toBeInTheDocument();
    expect(screen.getByText("7f1f44c1")).toBeInTheDocument();
    expect(screen.getByText("infra / transport")).toBeInTheDocument();
    expect(screen.getByText("fix recommended")).toBeInTheDocument();
    expect(screen.getByText(/p1 harden the deterministic comment-open path before vision fallback/i)).toBeInTheDocument();
  });
});
