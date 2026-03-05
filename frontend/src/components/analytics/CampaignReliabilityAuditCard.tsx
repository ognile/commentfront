import { AlertCircle, CheckCircle2, RefreshCw, ShieldAlert, Wrench } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export interface CampaignReliabilityAuditReport {
  generated_at: string;
  window: {
    date_from: string;
    date_to: string;
    lookback_days: number;
    min_total_count: number;
  };
  summary: {
    verdict: "go" | "go with watchlist" | "needs fixes before trust";
    campaign_count: number;
    original_jobs: number;
    final_completed_jobs: number;
    first_pass_failure_count: number;
    retry_attempt_count: number;
    retry_overhead_rate: number;
    recovered_job_count: number;
    unrecovered_job_count: number;
    manual_intervention_campaigns: number;
    current_restricted_profiles: number;
    current_appeal_backlog: number;
    dominant_retry_triggers: string[];
  };
  campaigns: Array<{
    campaign_id: string;
    completed_at?: string;
    created_by?: string;
    total_jobs: number;
    final_completed_jobs: number;
    retry_attempt_count: number;
    recovered_job_count: number;
    unrecovered_job_count: number;
    operator_intervention_required: boolean;
    top_failure_classes: Array<{
      category: string;
      count: number;
      sample_error: string;
    }>;
  }>;
  root_causes: Array<{
    category: string;
    count: number;
    campaigns_affected: number;
    profiles_affected: number;
    explanation: string;
    sample_errors: string[];
  }>;
  fix_matrix: Array<{
    area: string;
    status: "no fix needed right now" | "watch only" | "fix recommended" | "fix required";
    reason: string;
  }>;
  recommended_fixes: Array<{
    priority: number;
    title: string;
    status: string;
    expected_impact: string;
  }>;
}

interface CampaignReliabilityAuditCardProps {
  report: CampaignReliabilityAuditReport | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
}

const VERDICT_STYLES: Record<CampaignReliabilityAuditReport["summary"]["verdict"], string> = {
  go: "bg-green-50 text-green-700 border-green-200",
  "go with watchlist": "bg-amber-50 text-amber-700 border-amber-200",
  "needs fixes before trust": "bg-red-50 text-red-700 border-red-200",
};

const FIX_STYLES: Record<string, string> = {
  "no fix needed right now": "bg-green-50 text-green-700 border-green-200",
  "watch only": "bg-amber-50 text-amber-700 border-amber-200",
  "fix recommended": "bg-orange-50 text-orange-700 border-orange-200",
  "fix required": "bg-red-50 text-red-700 border-red-200",
};

const formatDateTime = (value?: string): string => {
  if (!value) return "Unknown";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
};

const labelize = (value: string): string => {
  return value
    .split("/")
    .map(part => part.replace(/-/g, " "))
    .join(" / ");
};

export function CampaignReliabilityAuditCard({
  report,
  loading,
  error,
  onRefresh,
}: CampaignReliabilityAuditCardProps) {
  const verdict = report?.summary.verdict || "go";

  return (
    <Card>
      <CardHeader className="bg-[rgba(51,51,51,0.04)] border-b border-[rgba(0,0,0,0.1)] pb-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <CardTitle className="text-lg flex items-center gap-2">
              <ShieldAlert className="w-5 h-5" />
              Campaign Reliability Audit
            </CardTitle>
            <p className="text-sm text-[#666666] mt-2">
              Production operator report for recent completed campaigns with retry-recovery root cause analysis.
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={onRefresh} disabled={loading}>
            <RefreshCw className={`w-4 h-4 mr-1 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
      </CardHeader>
      <CardContent className="pt-4 space-y-4">
        {loading && !report ? (
          <div className="flex items-center justify-center py-8 text-sm text-[#666666]">
            <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
            Building reliability audit...
          </div>
        ) : error ? (
          <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        ) : report ? (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="outline" className={VERDICT_STYLES[verdict]}>
                {verdict}
              </Badge>
              <span className="text-xs text-[#777777]">
                Window {report.window.date_from} to {report.window.date_to}
              </span>
              <span className="text-xs text-[#777777]">
                Updated {formatDateTime(report.generated_at)}
              </span>
            </div>

            <div className="grid grid-cols-2 lg:grid-cols-6 gap-3">
              <div className="rounded-xl border border-[rgba(0,0,0,0.08)] bg-white px-3 py-2">
                <div className="text-xs text-[#999999]">Campaigns</div>
                <div className="text-xl font-semibold">{report.summary.campaign_count}</div>
              </div>
              <div className="rounded-xl border border-[rgba(0,0,0,0.08)] bg-white px-3 py-2">
                <div className="text-xs text-[#999999]">Original Jobs</div>
                <div className="text-xl font-semibold">{report.summary.original_jobs}</div>
              </div>
              <div className="rounded-xl border border-[rgba(0,0,0,0.08)] bg-white px-3 py-2">
                <div className="text-xs text-[#999999]">Recovered Jobs</div>
                <div className="text-xl font-semibold">{report.summary.recovered_job_count}</div>
              </div>
              <div className="rounded-xl border border-[rgba(0,0,0,0.08)] bg-white px-3 py-2">
                <div className="text-xs text-[#999999]">Retry Attempts</div>
                <div className="text-xl font-semibold">{report.summary.retry_attempt_count}</div>
              </div>
              <div className="rounded-xl border border-[rgba(0,0,0,0.08)] bg-white px-3 py-2">
                <div className="text-xs text-[#999999]">Retry Overhead</div>
                <div className="text-xl font-semibold">{report.summary.retry_overhead_rate.toFixed(1)}%</div>
              </div>
              <div className="rounded-xl border border-[rgba(0,0,0,0.08)] bg-white px-3 py-2">
                <div className="text-xs text-[#999999]">Current Restricted</div>
                <div className="text-xl font-semibold">{report.summary.current_restricted_profiles}</div>
              </div>
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              <div className="rounded-xl border border-[rgba(0,0,0,0.08)] bg-white px-4 py-3">
                <div className="text-xs font-medium tracking-[0.08em] uppercase text-[#777777] mb-3">
                  Root Causes
                </div>
                <div className="space-y-3">
                  {report.root_causes.length === 0 ? (
                    <div className="text-sm text-[#666666]">No recovery triggers found in the selected window.</div>
                  ) : (
                    report.root_causes.map((cause) => (
                      <div key={cause.category} className="rounded-lg bg-[#f7f7f7] px-3 py-3">
                        <div className="flex items-center justify-between gap-3">
                          <div className="font-medium text-[#333333]">{labelize(cause.category)}</div>
                          <Badge variant="outline" className="bg-white text-[#555555] border-[#dddddd]">
                            {cause.count}
                          </Badge>
                        </div>
                        <div className="mt-1 text-xs text-[#666666]">
                          {cause.campaigns_affected} campaigns, {cause.profiles_affected} profiles
                        </div>
                        <div className="mt-2 text-xs text-[#666666]">{cause.explanation}</div>
                      </div>
                    ))
                  )}
                </div>
              </div>

              <div className="rounded-xl border border-[rgba(0,0,0,0.08)] bg-white px-4 py-3">
                <div className="text-xs font-medium tracking-[0.08em] uppercase text-[#777777] mb-3">
                  Fix Matrix
                </div>
                <div className="space-y-3">
                  {report.fix_matrix.map((item) => (
                    <div key={item.area} className="rounded-lg bg-[#f7f7f7] px-3 py-3">
                      <div className="flex items-center justify-between gap-3">
                        <div className="font-medium text-[#333333]">{item.area}</div>
                        <Badge variant="outline" className={FIX_STYLES[item.status] || FIX_STYLES["watch only"]}>
                          {item.status}
                        </Badge>
                      </div>
                      <div className="mt-2 text-xs text-[#666666]">{item.reason}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            <div className="rounded-xl border border-[rgba(0,0,0,0.08)] bg-white overflow-hidden">
              <div className="px-4 py-3 border-b border-[rgba(0,0,0,0.08)]">
                <div className="text-xs font-medium tracking-[0.08em] uppercase text-[#777777]">
                  Per-Campaign Audit
                </div>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="bg-[#fafafa] text-[#666666]">
                    <tr>
                      <th className="text-left px-4 py-2 font-medium">Campaign</th>
                      <th className="text-left px-4 py-2 font-medium">Completed</th>
                      <th className="text-left px-4 py-2 font-medium">Jobs</th>
                      <th className="text-left px-4 py-2 font-medium">Retries</th>
                      <th className="text-left px-4 py-2 font-medium">Recovered</th>
                      <th className="text-left px-4 py-2 font-medium">Top Causes</th>
                      <th className="text-left px-4 py-2 font-medium">Intervention</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.campaigns.map((campaign) => (
                      <tr key={campaign.campaign_id} className="border-t border-[rgba(0,0,0,0.06)] align-top">
                        <td className="px-4 py-3">
                          <div className="font-medium text-[#333333]">{campaign.campaign_id.slice(0, 8)}</div>
                          <div className="text-xs text-[#999999]">{campaign.created_by || "unknown"}</div>
                        </td>
                        <td className="px-4 py-3 text-[#555555]">{formatDateTime(campaign.completed_at)}</td>
                        <td className="px-4 py-3 text-[#333333]">
                          {campaign.final_completed_jobs}/{campaign.total_jobs}
                        </td>
                        <td className="px-4 py-3 text-[#333333]">{campaign.retry_attempt_count}</td>
                        <td className="px-4 py-3 text-[#333333]">{campaign.recovered_job_count}</td>
                        <td className="px-4 py-3">
                          <div className="flex flex-wrap gap-2">
                            {campaign.top_failure_classes.length > 0 ? campaign.top_failure_classes.map((item) => (
                              <Badge key={`${campaign.campaign_id}:${item.category}`} variant="outline" className="bg-[#f7f7f7] text-[#555555] border-[#dddddd]">
                                {labelize(item.category)} x{item.count}
                              </Badge>
                            )) : (
                              <span className="text-[#999999]">None</span>
                            )}
                          </div>
                        </td>
                        <td className="px-4 py-3">
                          {campaign.operator_intervention_required ? (
                            <Badge variant="outline" className="bg-red-50 text-red-700 border-red-200">
                              <AlertCircle className="w-3 h-3 mr-1" />
                              Yes
                            </Badge>
                          ) : (
                            <Badge variant="outline" className="bg-green-50 text-green-700 border-green-200">
                              <CheckCircle2 className="w-3 h-3 mr-1" />
                              No
                            </Badge>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="rounded-xl border border-[rgba(0,0,0,0.08)] bg-white px-4 py-3">
              <div className="text-xs font-medium tracking-[0.08em] uppercase text-[#777777] mb-3">
                Recommendations
              </div>
              {report.recommended_fixes.length === 0 ? (
                <div className="flex items-center gap-2 text-sm text-green-700">
                  <CheckCircle2 className="w-4 h-4" />
                  No fix needed right now. Keep watching retry overhead and repeated automation-state failures.
                </div>
              ) : (
                <div className="space-y-3">
                  {report.recommended_fixes.map((fix) => (
                    <div key={fix.title} className="rounded-lg bg-[#f7f7f7] px-3 py-3">
                      <div className="flex items-center gap-2 font-medium text-[#333333]">
                        <Wrench className="w-4 h-4" />
                        P{fix.priority} {fix.title}
                      </div>
                      <div className="mt-2 text-xs text-[#666666]">{fix.expected_impact}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="rounded-xl border border-[rgba(0,0,0,0.08)] bg-[#fafafa] px-4 py-4 text-sm text-[#666666]">
            No audit report available yet.
          </div>
        )}
      </CardContent>
    </Card>
  );
}
