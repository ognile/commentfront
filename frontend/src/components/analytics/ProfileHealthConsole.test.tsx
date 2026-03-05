import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ProfileHealthConsole, type ProfileHealthEntry } from "@/components/analytics/ProfileHealthConsole";

const baseProps = {
  loading: false,
  expandedProfile: null,
  onExpandProfile: vi.fn(),
  onRefresh: vi.fn(),
  onVerify: vi.fn(),
  onAppeal: vi.fn(),
  onUnblock: vi.fn(),
  onRestrict: vi.fn(),
  isActionRunning: () => false,
};

const restrictedProfile: ProfileHealthEntry = {
  profile_name: "rita_restricted",
  display_name: "Rita Restricted",
  status: "restricted",
  is_reserved: false,
  last_used_at: null,
  usage_count: 0,
  success_rate: 0,
  restriction_expires_at: "2026-03-08T12:00:00Z",
  restriction_reason: "Comment restriction",
  recovery_state: "needs_followup",
  recovery_last_event: "scheduler_followup_queued",
  recovery_last_event_at: "2026-03-05T10:00:00Z",
  recovery_history: [
    {
      timestamp: "2026-03-05T10:00:00Z",
      event: "scheduler_followup_queued",
      state: "needs_followup",
    },
  ],
  usage_history: [],
  appeal_status: "failed",
  appeal_last_attempt_at: "2026-03-05T09:00:00Z",
  appeal_last_error: "Need another verification pass",
};

const activeProfile: ProfileHealthEntry = {
  profile_name: "mia_active",
  display_name: "Mia Active",
  status: "active",
  is_reserved: false,
  last_used_at: "2026-03-05T11:30:00Z",
  usage_count: 4,
  success_rate: 100,
  restriction_expires_at: null,
  restriction_reason: null,
  recovery_state: "resolved",
  recovery_last_event: "manual_unblock",
  recovery_last_event_at: "2026-03-05T11:45:00Z",
  recovery_history: [
    {
      timestamp: "2026-03-05T11:45:00Z",
      event: "manual_unblock",
      state: "resolved",
    },
  ],
  usage_history: [
    {
      timestamp: "2026-03-05T11:30:00Z",
      campaign_id: "campaign-1",
      comment: "Everything is working",
      success: true,
    },
  ],
  appeal_status: "none",
  appeal_last_attempt_at: null,
  appeal_last_error: null,
};

describe("ProfileHealthConsole", () => {
  it("renders session-backed profiles including unused restricted entries and display names", () => {
    render(
      <ProfileHealthConsole
        {...baseProps}
        profiles={[activeProfile, restrictedProfile]}
      />,
    );

    expect(screen.getByText("Profile Health Console")).toBeInTheDocument();
    expect(screen.getByText("Rita Restricted")).toBeInTheDocument();
    expect(screen.getByText("Mia Active")).toBeInTheDocument();
    expect(screen.getByText("rita_restricted")).toBeInTheDocument();
    expect(screen.getByText("Needs Follow-up")).toBeInTheDocument();
    expect(screen.getByText("Resolved")).toBeInTheDocument();
  });

  it("updates rendered recovery state when fresh profile-health data arrives", () => {
    const { rerender } = render(
      <ProfileHealthConsole
        {...baseProps}
        expandedProfile="rita_restricted"
        profiles={[restrictedProfile]}
      />,
    );

    expect(screen.getAllByText("Queued for next scheduler follow-up").length).toBeGreaterThan(0);
    expect(screen.getByText("Verify now")).toBeInTheDocument();

    rerender(
      <ProfileHealthConsole
        {...baseProps}
        expandedProfile="rita_restricted"
        profiles={[
          {
            ...restrictedProfile,
            status: "active",
            recovery_state: "resolved",
            recovery_last_event: "manual_unblock",
            recovery_history: [
              {
                timestamp: "2026-03-05T12:00:00Z",
                event: "manual_unblock",
                state: "resolved",
              },
            ],
            appeal_status: "none",
            appeal_last_error: null,
            restriction_expires_at: null,
            restriction_reason: null,
          },
        ]}
      />,
    );

    expect(screen.getAllByText("Manual unblock").length).toBeGreaterThan(0);
    expect(screen.queryByText("Verify now")).not.toBeInTheDocument();
    expect(screen.getByText("Restrict 24h")).toBeInTheDocument();
  });
});
