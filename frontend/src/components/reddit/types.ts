export interface RedditCredential {
  credential_id: string
  uid: string
  platform: 'reddit'
  username?: string | null
  email?: string | null
  profile_name?: string | null
  display_name?: string | null
  profile_url?: string | null
  tags?: string[]
  fixture?: boolean
  has_secret: boolean
  session_connected: boolean
  session_valid?: boolean | null
  session_profile_name?: string | null
}

export interface RedditSession {
  profile_name: string
  display_name?: string | null
  username?: string | null
  email?: string | null
  profile_url?: string | null
  valid: boolean
  tags?: string[]
  fixture?: boolean
}

export interface RedditMission {
  id: string
  profile_name: string
  action: string
  status: string
  brief?: string | null
  exact_text?: string | null
  target_url?: string | null
  subreddit?: string | null
  title?: string | null
  body?: string | null
  image_id?: string | null
  next_run_at?: string | null
  last_run_at?: string | null
}

export interface RedditProgramListItem {
  id: string
  status: string
  created_at?: string | null
  updated_at?: string | null
  spec?: {
    metadata?: {
      tracker_slug?: string
      mode?: string
      purpose?: string
      proof_gate?: string
      proof_gate_program_id?: string
      proof_gate_commit?: string
      supersedes_program_id?: string
      deploy_commit?: string
      created_by?: string
      [key: string]: unknown
    }
  }
}

export interface RedditOperatorAttemptHistoryEntry {
  attempt_id: string
  status: string | null
  final_verdict: string | null
  failure_class: string | null
  started_at: string | null
  ended_at: string | null
}

export interface RedditOperatorActionRow {
  work_item_id: string
  local_date: string
  profile_name: string
  action: string
  subreddit?: string | null
  status: string
  final_verdict?: string | null
  attempts: number
  attempt_id?: string | null
  target_url?: string | null
  target_comment_url?: string | null
  target_ref?: string | null
  screenshot_artifact_url?: string | null
  scheduled_at?: string | null
  completed_at?: string | null
  error?: string | null
  proof_flags: {
    has_url: boolean
    has_screenshot: boolean
    has_attempt: boolean
    success_confirmed: boolean
  }
  attempt_history: RedditOperatorAttemptHistoryEntry[]
}

export interface RedditOperatorProfileRow {
  profile_name: string
  planned: Record<string, number>
  completed: Record<string, number>
  pending: Record<string, number>
  blocked: Record<string, number>
  planned_total: number
  completed_total: number
  pending_total: number
  blocked_total: number
  proof_coverage: {
    required_actions: number
    with_url: number
    with_screenshot: number
    with_attempt: number
    success_confirmed: number
  }
}

export interface RedditOperatorProgramSummary {
  id: string
  status: string
  next_run_at?: string | null
  contract_totals: Record<string, number>
  remaining_contract: Record<string, number>
  available_days: string[]
  selected_local_date?: string | null
  available_actions: string[]
  notification_log: Array<Record<string, unknown>>
  failure_summary: {
    by_action?: Record<string, number>
    by_profile?: Record<string, number>
    by_subreddit?: Record<string, number>
    by_class?: Record<string, number>
  }
}

export interface RedditOperatorViewResponse {
  program: RedditOperatorProgramSummary
  profiles_by_day: RedditOperatorProfileRow[]
  action_rows: RedditOperatorActionRow[]
}
