import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'

import { RedditTab } from '@/components/reddit/RedditTab'

const { apiFetchMock } = vi.hoisted(() => ({
  apiFetchMock: vi.fn(),
}))

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api')
  return {
    ...actual,
    apiFetch: apiFetchMock,
  }
})

describe('RedditTab', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
  })

  it('defaults to the active reddit rollout and renders the operator board with utility rail', async () => {
    apiFetchMock.mockImplementation(async (endpoint: string) => {
      if (endpoint === '/reddit/credentials') return []
      if (endpoint === '/reddit/sessions') return [{ profile_name: 'reddit_alpha', display_name: 'Reddit Alpha', valid: true }]
      if (endpoint === '/reddit/missions') return { missions: [] }
      if (endpoint === '/reddit/programs') {
        return {
          programs: [
            { id: 'reddit_program_archived', status: 'completed', created_at: '2026-03-09T10:00:00Z' },
            { id: 'reddit_program_active', status: 'active', created_at: '2026-03-10T10:00:00Z' },
          ],
        }
      }
      if (
        endpoint === '/reddit/programs/reddit_program_active/operator-view'
        || endpoint === '/reddit/programs/reddit_program_active/operator-view?local_date=2026-03-10'
      ) {
        return {
          program: {
            id: 'reddit_program_active',
            status: 'active',
            next_run_at: '2026-03-10T15:30:00Z',
            contract_totals: { create_post: 1, reply_comment: 1 },
            remaining_contract: { create_post: 1 },
            available_days: ['2026-03-10', '2026-03-11'],
            selected_local_date: '2026-03-10',
            available_actions: ['create_post', 'reply_comment'],
            notification_log: [],
            failure_summary: { by_action: {}, by_profile: {}, by_subreddit: {}, by_class: {} },
          },
          profiles_by_day: [
            {
              profile_name: 'reddit_alpha',
              planned: { create_post: 1, reply_comment: 1 },
              completed: { reply_comment: 1 },
              pending: { create_post: 1 },
              blocked: {},
              planned_total: 2,
              completed_total: 1,
              pending_total: 1,
              blocked_total: 0,
              proof_coverage: {
                required_actions: 2,
                with_url: 2,
                with_screenshot: 1,
                with_attempt: 1,
                success_confirmed: 1,
              },
            },
          ],
          action_rows: [
            {
              work_item_id: 'work_reply',
              local_date: '2026-03-10',
              profile_name: 'reddit_alpha',
              action: 'reply_comment',
              subreddit: 'Healthyhooha',
              status: 'completed',
              final_verdict: 'success_confirmed',
              attempts: 1,
              attempt_id: 'attempt_reply',
              target_url: 'https://reddit.com/thread',
              target_comment_url: 'https://reddit.com/comment',
              target_ref: 'https://reddit.com/comment',
              screenshot_artifact_url: '/forensics/artifacts/reply-shot',
              scheduled_at: '2026-03-10T10:00:00Z',
              completed_at: '2026-03-10T10:02:00Z',
              error: null,
              proof_flags: {
                has_url: true,
                has_screenshot: true,
                has_attempt: true,
                success_confirmed: true,
              },
              attempt_history: [
                {
                  attempt_id: 'attempt_reply',
                  status: 'completed',
                  final_verdict: 'success_confirmed',
                  failure_class: null,
                  started_at: '2026-03-10T10:00:00Z',
                  ended_at: '2026-03-10T10:02:00Z',
                },
              ],
            },
            {
              work_item_id: 'work_post',
              local_date: '2026-03-10',
              profile_name: 'reddit_alpha',
              action: 'create_post',
              subreddit: 'Healthyhooha',
              status: 'pending',
              final_verdict: null,
              attempts: 0,
              attempt_id: null,
              target_url: null,
              target_comment_url: null,
              target_ref: null,
              screenshot_artifact_url: null,
              scheduled_at: '2026-03-10T11:00:00Z',
              completed_at: null,
              error: null,
              proof_flags: {
                has_url: false,
                has_screenshot: false,
                has_attempt: false,
                success_confirmed: false,
              },
              attempt_history: [],
            },
          ],
        }
      }
      throw new Error(`unexpected endpoint ${endpoint}`)
    })

    render(<RedditTab />)

    expect(await screen.findByText('reddit_program_active')).toBeInTheDocument()
    expect(await screen.findByText('confirmed 1/2')).toBeInTheDocument()
    expect(screen.getByText('sessions & credentials')).toBeInTheDocument()
    expect(screen.getAllByText('reddit_alpha').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Reddit Alpha').length).toBeGreaterThan(0)
  })

  it('switches the operator board when a different day is selected', async () => {
    apiFetchMock.mockImplementation(async (endpoint: string) => {
      if (endpoint === '/reddit/credentials') return []
      if (endpoint === '/reddit/sessions') return []
      if (endpoint === '/reddit/missions') return { missions: [] }
      if (endpoint === '/reddit/programs') return { programs: [{ id: 'reddit_program_active', status: 'active', created_at: '2026-03-10T10:00:00Z' }] }
      if (
        endpoint === '/reddit/programs/reddit_program_active/operator-view'
        || endpoint === '/reddit/programs/reddit_program_active/operator-view?local_date=2026-03-10'
      ) {
        return {
          program: {
            id: 'reddit_program_active',
            status: 'active',
            next_run_at: null,
            contract_totals: { upvote_post: 1 },
            remaining_contract: { upvote_post: 1 },
            available_days: ['2026-03-10', '2026-03-11'],
            selected_local_date: '2026-03-10',
            available_actions: ['upvote_post'],
            notification_log: [],
            failure_summary: { by_action: {}, by_profile: {}, by_subreddit: {}, by_class: {} },
          },
          profiles_by_day: [
            {
              profile_name: 'reddit_day_one',
              planned: { upvote_post: 1 },
              completed: {},
              pending: { upvote_post: 1 },
              blocked: {},
              planned_total: 1,
              completed_total: 0,
              pending_total: 1,
              blocked_total: 0,
              proof_coverage: { required_actions: 1, with_url: 0, with_screenshot: 0, with_attempt: 0, success_confirmed: 0 },
            },
          ],
          action_rows: [],
        }
      }
      if (endpoint === '/reddit/programs/reddit_program_active/operator-view?local_date=2026-03-11') {
        return {
          program: {
            id: 'reddit_program_active',
            status: 'active',
            next_run_at: null,
            contract_totals: { upvote_post: 1 },
            remaining_contract: {},
            available_days: ['2026-03-10', '2026-03-11'],
            selected_local_date: '2026-03-11',
            available_actions: ['upvote_post'],
            notification_log: [],
            failure_summary: { by_action: {}, by_profile: {}, by_subreddit: {}, by_class: {} },
          },
          profiles_by_day: [
            {
              profile_name: 'reddit_day_two',
              planned: { upvote_post: 1 },
              completed: { upvote_post: 1 },
              pending: {},
              blocked: {},
              planned_total: 1,
              completed_total: 1,
              pending_total: 0,
              blocked_total: 0,
              proof_coverage: { required_actions: 1, with_url: 1, with_screenshot: 1, with_attempt: 1, success_confirmed: 1 },
            },
          ],
          action_rows: [],
        }
      }
      throw new Error(`unexpected endpoint ${endpoint}`)
    })

    render(<RedditTab />)

    expect(await screen.findByText('reddit_day_one')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /2026-03-11/i }))

    await waitFor(() => {
      expect(screen.getByText('reddit_day_two')).toBeInTheDocument()
    })
  })
})
