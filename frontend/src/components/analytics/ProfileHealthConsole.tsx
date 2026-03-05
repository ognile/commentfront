import { useMemo } from "react";
import { AlertCircle, CheckCircle, ChevronRight, Loader2, Play, RefreshCw, RotateCw, Shield, User, XCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type RecoveryHistoryEntry = {
  timestamp: string;
  event: string;
  state: string;
  details?: Record<string, unknown>;
};

type UsageHistoryEntry = {
  timestamp: string;
  campaign_id: string | null;
  comment: string | null;
  success: boolean;
};

export interface ProfileHealthEntry {
  profile_name: string;
  display_name?: string;
  status: string;
  is_reserved: boolean;
  last_used_at: string | null;
  usage_count: number;
  success_rate: number;
  restriction_expires_at: string | null;
  restriction_reason: string | null;
  recovery_state?: string;
  recovery_last_event?: string | null;
  recovery_last_event_at?: string | null;
  recovery_history?: RecoveryHistoryEntry[];
  usage_history?: UsageHistoryEntry[];
  appeal_status?: string;
  appeal_last_attempt_at?: string | null;
  appeal_last_error?: string | null;
}

type HealthAction = "verify" | "appeal" | "unblock" | "restrict";

interface ProfileHealthConsoleProps {
  profiles: ProfileHealthEntry[];
  loading: boolean;
  expandedProfile: string | null;
  onExpandProfile: (profileName: string | null) => void;
  onRefresh: () => void;
  onVerify: (profileName: string) => void;
  onAppeal: (profileName: string) => void;
  onUnblock: (profileName: string) => void;
  onRestrict: (profileName: string, hours?: number) => void;
  isActionRunning: (profileName: string, action: HealthAction) => boolean;
}

const RECOVERY_STATE_LABELS: Record<string, string> = {
  none: "No recovery needed",
  restricted: "Restricted",
  resolved: "Resolved",
  in_review: "In review",
  needs_followup: "Needs follow-up",
  checkpoint: "Checkpoint",
};

const RECOVERY_STATE_STYLES: Record<string, string> = {
  none: "bg-[#f4f4f4] text-[#555555] border-[#dfdfdf]",
  restricted: "bg-red-50 text-red-700 border-red-200",
  resolved: "bg-green-50 text-green-700 border-green-200",
  in_review: "bg-blue-50 text-blue-700 border-blue-200",
  needs_followup: "bg-amber-50 text-amber-700 border-amber-200",
  checkpoint: "bg-orange-50 text-orange-700 border-orange-200",
};

const formatEventLabel = (event?: string | null): string => {
  if (!event) return "No recovery events yet";
  const known: Record<string, string> = {
    manual_unblock: "Manual unblock",
    verify_auto_unblock: "Verify resolved restriction",
    comment_check_auto_unblock: "Comment check resolved restriction",
    verify_in_review: "Verification found appeal in review",
    verify_confirmed_restricted: "Verification confirmed restriction",
    verify_followup_required: "Verification needs follow-up",
    verify_error: "Verification error",
    verify_unexpected_status: "Unexpected verification status",
    restriction_marked: "Restriction marked",
    restriction_expired: "Restriction expired",
    appeal_reset: "Appeal window reset",
    appeal_submitted: "Appeal submitted",
    appeal_already_in_review: "Appeal already in review",
    appeal_failed: "Appeal failed",
    appeal_max_steps: "Appeal hit max steps",
    appeal_error: "Appeal error",
    appeal_resolved: "Appeal resolved restriction",
    appeal_expired_unblock: "Expired restriction cleared",
    appeal_checkpoint_blocked: "Checkpoint requires manual help",
    scheduler_followup_queued: "Queued for next scheduler follow-up",
    scheduler_busy_skipped: "Scheduler skipped busy profile",
    comment_check_confirmed_restricted: "Comment check confirmed restriction",
  };
  return known[event] || event.replace(/_/g, " ");
};

const formatDateTime = (value?: string | null): string => {
  if (!value) return "Never";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
};

const profilePriority = (profile: ProfileHealthEntry): number => {
  if (profile.recovery_state === "needs_followup") return 0;
  if (profile.status === "restricted") return 1;
  if (profile.recovery_state === "in_review") return 2;
  if (profile.is_reserved) return 3;
  return 4;
};

export function ProfileHealthConsole({
  profiles,
  loading,
  expandedProfile,
  onExpandProfile,
  onRefresh,
  onVerify,
  onAppeal,
  onUnblock,
  onRestrict,
  isActionRunning,
}: ProfileHealthConsoleProps) {
  const orderedProfiles = useMemo(
    () =>
      [...profiles].sort((left, right) => {
        const priorityDelta = profilePriority(left) - profilePriority(right);
        if (priorityDelta !== 0) return priorityDelta;

        const leftUsed = left.last_used_at ? new Date(left.last_used_at).getTime() : 0;
        const rightUsed = right.last_used_at ? new Date(right.last_used_at).getTime() : 0;
        if (leftUsed !== rightUsed) return rightUsed - leftUsed;

        const leftName = left.display_name || left.profile_name;
        const rightName = right.display_name || right.profile_name;
        return leftName.localeCompare(rightName);
      }),
    [profiles],
  );

  const counts = useMemo(() => {
    let followup = 0;
    let inReview = 0;
    let reserved = 0;
    let restricted = 0;
    for (const profile of profiles) {
      if (profile.recovery_state === "needs_followup") followup += 1;
      if (profile.recovery_state === "in_review") inReview += 1;
      if (profile.is_reserved) reserved += 1;
      if (profile.status === "restricted") restricted += 1;
    }
    return {
      total: profiles.length,
      followup,
      inReview,
      reserved,
      restricted,
    };
  }, [profiles]);

  return (
    <Card>
      <CardHeader className="bg-[rgba(51,51,51,0.04)] border-b border-[rgba(0,0,0,0.1)] pb-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <CardTitle className="text-lg flex items-center gap-2">
              <User className="w-5 h-5" />
              Profile Health Console
            </CardTitle>
            <p className="text-sm text-[#666666] mt-2">
              Every session-backed profile with backend recovery truth, usage signals, and operator actions.
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={onRefresh}
            disabled={loading}
          >
            {loading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <RefreshCw className="w-4 h-4" />
            )}
            Refresh
          </Button>
        </div>
      </CardHeader>
      <CardContent className="pt-4 space-y-4">
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
          <div className="rounded-xl border border-[rgba(0,0,0,0.08)] bg-white px-3 py-2">
            <div className="text-xs text-[#999999]">Total Profiles</div>
            <div className="text-xl font-semibold">{counts.total}</div>
          </div>
          <div className="rounded-xl border border-amber-200 bg-amber-50/70 px-3 py-2">
            <div className="text-xs text-amber-700">Needs Follow-up</div>
            <div className="text-xl font-semibold text-amber-800">{counts.followup}</div>
          </div>
          <div className="rounded-xl border border-blue-200 bg-blue-50/70 px-3 py-2">
            <div className="text-xs text-blue-700">In Review</div>
            <div className="text-xl font-semibold text-blue-800">{counts.inReview}</div>
          </div>
          <div className="rounded-xl border border-red-200 bg-red-50/70 px-3 py-2">
            <div className="text-xs text-red-700">Restricted</div>
            <div className="text-xl font-semibold text-red-800">{counts.restricted}</div>
          </div>
          <div className="rounded-xl border border-[#dfdfdf] bg-[#f7f7f7] px-3 py-2">
            <div className="text-xs text-[#666666]">Busy / Reserved</div>
            <div className="text-xl font-semibold text-[#333333]">{counts.reserved}</div>
          </div>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-10">
            <Loader2 className="w-6 h-6 animate-spin text-[#999999]" />
            <span className="ml-2 text-[#666666]">Loading profile health...</span>
          </div>
        ) : orderedProfiles.length === 0 ? (
          <div className="text-center py-10 text-[#999999]">
            <Shield className="w-12 h-12 mx-auto mb-2 opacity-50" />
            <p>No session-backed profiles found.</p>
          </div>
        ) : (
          <div className="space-y-3 max-h-[720px] overflow-y-auto pr-1">
            {orderedProfiles.map((profile) => {
              const isExpanded = expandedProfile === profile.profile_name;
              const displayName = profile.display_name || profile.profile_name;
              const recoveryState = profile.recovery_state || "none";
              const recoveryStyle = RECOVERY_STATE_STYLES[recoveryState] || RECOVERY_STATE_STYLES.none;
              const availabilityLabel = profile.status === "restricted" ? "Restricted" : "Active";

              return (
                <div
                  key={profile.profile_name}
                  className={`rounded-2xl border transition-colors ${
                    isExpanded
                      ? "border-[rgba(0,0,0,0.12)] bg-[rgba(51,51,51,0.04)]"
                      : "border-[rgba(0,0,0,0.08)] bg-white hover:bg-[rgba(51,51,51,0.02)]"
                  }`}
                  style={{ contentVisibility: "auto", containIntrinsicSize: "220px" }}
                >
                  <button
                    type="button"
                    className="w-full text-left px-4 py-4"
                    onClick={() => onExpandProfile(isExpanded ? null : profile.profile_name)}
                  >
                    <div className="flex items-start justify-between gap-4">
                      <div className="min-w-0 flex-1 space-y-3">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-semibold text-[#222222]">{displayName}</span>
                          {displayName !== profile.profile_name && (
                            <span className="text-xs text-[#999999]">{profile.profile_name}</span>
                          )}
                          <Badge
                            variant="outline"
                            className={profile.status === "restricted" ? "bg-red-50 text-red-700 border-red-200" : "bg-green-50 text-green-700 border-green-200"}
                          >
                            {availabilityLabel}
                          </Badge>
                          {profile.is_reserved && (
                            <Badge variant="outline" className="bg-[#f4f4f4] text-[#444444] border-[#d5d5d5]">
                              Busy
                            </Badge>
                          )}
                          <Badge variant="outline" className={recoveryStyle}>
                            {RECOVERY_STATE_LABELS[recoveryState] || recoveryState.replace(/_/g, " ")}
                          </Badge>
                        </div>

                        <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 text-sm">
                          <div>
                            <div className="text-xs text-[#999999]">Usage</div>
                            <div className="font-medium text-[#333333]">{profile.usage_count}</div>
                          </div>
                          <div>
                            <div className="text-xs text-[#999999]">Success Rate</div>
                            <div className="font-medium text-[#333333]">{profile.success_rate.toFixed(0)}%</div>
                          </div>
                          <div>
                            <div className="text-xs text-[#999999]">Last Success</div>
                            <div className="font-medium text-[#333333]">{formatDateTime(profile.last_used_at)}</div>
                          </div>
                          <div>
                            <div className="text-xs text-[#999999]">Last Recovery</div>
                            <div className="font-medium text-[#333333]">{formatEventLabel(profile.recovery_last_event)}</div>
                          </div>
                          <div>
                            <div className="text-xs text-[#999999]">Recovery Updated</div>
                            <div className="font-medium text-[#333333]">{formatDateTime(profile.recovery_last_event_at)}</div>
                          </div>
                        </div>
                      </div>

                      <ChevronRight
                        className={`mt-1 h-4 w-4 text-[#777777] transition-transform ${isExpanded ? "rotate-90" : ""}`}
                      />
                    </div>
                  </button>

                  {isExpanded && (
                    <div className="border-t border-[rgba(0,0,0,0.08)] px-4 py-4 space-y-4">
                      <div className="flex flex-wrap gap-2">
                        {profile.status === "restricted" && (
                          <>
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={profile.is_reserved || isActionRunning(profile.profile_name, "verify")}
                              onClick={() => onVerify(profile.profile_name)}
                            >
                              {isActionRunning(profile.profile_name, "verify") ? (
                                <Loader2 className="w-4 h-4 mr-1 animate-spin" />
                              ) : (
                                <RotateCw className="w-4 h-4 mr-1" />
                              )}
                              Verify now
                            </Button>
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={profile.is_reserved || isActionRunning(profile.profile_name, "appeal")}
                              onClick={() => onAppeal(profile.profile_name)}
                            >
                              {isActionRunning(profile.profile_name, "appeal") ? (
                                <Loader2 className="w-4 h-4 mr-1 animate-spin" />
                              ) : (
                                <Play className="w-4 h-4 mr-1" />
                              )}
                              Appeal now
                            </Button>
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={isActionRunning(profile.profile_name, "unblock")}
                              onClick={() => onUnblock(profile.profile_name)}
                            >
                              {isActionRunning(profile.profile_name, "unblock") ? (
                                <Loader2 className="w-4 h-4 mr-1 animate-spin" />
                              ) : (
                                <CheckCircle className="w-4 h-4 mr-1" />
                              )}
                              Unblock
                            </Button>
                          </>
                        )}
                        {profile.status !== "restricted" && (
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={isActionRunning(profile.profile_name, "restrict")}
                            onClick={() => onRestrict(profile.profile_name, 24)}
                          >
                            {isActionRunning(profile.profile_name, "restrict") ? (
                              <Loader2 className="w-4 h-4 mr-1 animate-spin" />
                            ) : (
                              <XCircle className="w-4 h-4 mr-1" />
                            )}
                            Restrict 24h
                          </Button>
                        )}
                      </div>

                      <div className="grid gap-3 lg:grid-cols-2">
                        <div className="rounded-xl border border-[rgba(0,0,0,0.08)] bg-white px-3 py-3 space-y-2">
                          <div className="text-xs font-medium tracking-[0.08em] uppercase text-[#777777]">
                            Restriction / Appeal
                          </div>
                          <div className="text-sm text-[#333333]">
                            {profile.restriction_expires_at ? (
                              <>Expires {formatDateTime(profile.restriction_expires_at)}</>
                            ) : (
                              "No active expiry"
                            )}
                          </div>
                          {profile.restriction_reason && (
                            <div className="text-sm text-red-600">{profile.restriction_reason}</div>
                          )}
                          <div className="text-sm text-[#666666]">
                            Appeal state: {profile.appeal_status || "none"}
                          </div>
                          {profile.appeal_last_attempt_at && (
                            <div className="text-xs text-[#999999]">
                              Last appeal attempt {formatDateTime(profile.appeal_last_attempt_at)}
                            </div>
                          )}
                          {profile.appeal_last_error && (
                            <div className="flex items-start gap-2 text-xs text-amber-700">
                              <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                              <span>{profile.appeal_last_error}</span>
                            </div>
                          )}
                        </div>

                        <div className="rounded-xl border border-[rgba(0,0,0,0.08)] bg-white px-3 py-3 space-y-2">
                          <div className="text-xs font-medium tracking-[0.08em] uppercase text-[#777777]">
                            Recovery Evidence
                          </div>
                          <div className="text-sm text-[#333333]">
                            {formatEventLabel(profile.recovery_last_event)}
                          </div>
                          <div className="text-xs text-[#999999]">
                            Updated {formatDateTime(profile.recovery_last_event_at)}
                          </div>
                          {(profile.recovery_history || []).slice(-3).reverse().map((entry) => (
                            <div key={`${entry.timestamp}:${entry.event}`} className="rounded-lg bg-[#f7f7f7] px-2 py-2 text-xs text-[#555555]">
                              <div className="font-medium text-[#333333]">{formatEventLabel(entry.event)}</div>
                              <div>{formatDateTime(entry.timestamp)}</div>
                            </div>
                          ))}
                        </div>
                      </div>

                      {(profile.usage_history || []).length > 0 && (
                        <div>
                          <p className="text-xs font-medium tracking-[0.08em] uppercase text-[#777777] mb-2">
                            Recent Activity
                          </p>
                          <div className="space-y-2">
                            {(profile.usage_history || []).slice(-5).reverse().map((entry) => (
                              <div
                                key={`${entry.timestamp}:${entry.comment || "no-comment"}`}
                                className="flex items-start gap-2 rounded-lg bg-white border border-[rgba(0,0,0,0.08)] px-3 py-2 text-xs"
                              >
                                {entry.success ? (
                                  <CheckCircle className="w-3.5 h-3.5 text-green-500 mt-0.5 shrink-0" />
                                ) : (
                                  <XCircle className="w-3.5 h-3.5 text-red-500 mt-0.5 shrink-0" />
                                )}
                                <div className="min-w-0">
                                  <div className="text-[#999999]">{formatDateTime(entry.timestamp)}</div>
                                  {entry.comment && <div className="text-[#333333] truncate">"{entry.comment}"</div>}
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
