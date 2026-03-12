import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'

import { apiFetch } from '@/lib/api'
import { RedditOpsWorkspace } from '@/components/reddit/RedditOpsWorkspace'
import { RedditUtilityRail } from '@/components/reddit/RedditUtilityRail'
import type { RemoteSessionTarget } from '@/components/remote/types'
import type {
  RedditCredential,
  RedditMission,
  RedditOperatorViewResponse,
  RedditProgramListItem,
  RedditSession,
} from '@/components/reddit/types'

const ACTIONS = [
  'browse',
  'open',
  'join',
  'upvote',
  'create_post',
  'comment',
  'reply',
] as const

type RedditExecutionActionType = typeof ACTIONS[number]
type RedditExecutionTargetKind = 'subreddit' | 'post' | 'comment'
type RedditExecutionTargetStrategy = 'explicit' | 'discover'

interface RedditTabProps {
  onOpenRemoteControl?: (session: RemoteSessionTarget) => void
}

type RedditProgramMetadata = NonNullable<NonNullable<RedditProgramListItem['spec']>['metadata']>

function metadata(program: RedditProgramListItem): RedditProgramMetadata {
  return program.spec?.metadata || {}
}

function programPriority(program: RedditProgramListItem): number {
  const details = metadata(program)
  const isTrackerProgram = details.tracker_slug === 'reddit-3-day-growth-program'
  const isCurrentRollout = program.status === 'active' && details.mode === 'production' && typeof details.proof_gate_program_id === 'string'
  const isProofPacket = details.proof_gate === 'single_profile_latest_runtime'
  const isSummaryOnlyCheck = details.purpose === 'summary_only_failure_check'
  const isActive = program.status === 'active'

  if (isCurrentRollout) return 500
  if (isProofPacket) return 400
  if (isActive && isTrackerProgram) return 300
  if (isActive && !isSummaryOnlyCheck) return 200
  if (isActive && isSummaryOnlyCheck) return 100
  return 0
}

function sortPrograms(programs: RedditProgramListItem[]): RedditProgramListItem[] {
  return [...programs].sort((left, right) => {
    const priorityDelta = programPriority(right) - programPriority(left)
    if (priorityDelta !== 0) return priorityDelta
    return String(right.updated_at || right.created_at || '').localeCompare(String(left.updated_at || left.created_at || ''))
  })
}

function preferredProgramId(programs: RedditProgramListItem[], currentProgramId: string): string {
  if (currentProgramId && programs.some((program) => program.id === currentProgramId)) {
    return currentProgramId
  }
  const active = programs.find((program) => program.status === 'active')
  return active?.id || programs[0]?.id || ''
}

function defaultTargetKindForAction(action: string): RedditExecutionTargetKind {
  if (action === 'comment') return 'post'
  if (action === 'reply') return 'comment'
  if (action === 'browse' || action === 'join' || action === 'create_post') return 'subreddit'
  return 'post'
}

function buildExecutionRequest({
  profileName,
  action,
  targetKind,
  targetStrategy,
  targetUrl,
  targetCommentUrl,
  subreddit,
  text,
  title,
  body,
  imageId,
  scrolls,
}: {
  profileName: string
  action: string
  targetKind: RedditExecutionTargetKind
  targetStrategy: RedditExecutionTargetStrategy
  targetUrl: string
  targetCommentUrl: string
  subreddit: string
  text: string
  title: string
  body: string
  imageId: string
  scrolls: string
}) {
  const normalizedSubreddit = subreddit.trim()
  const normalizedTargetUrl = targetUrl.trim()
  const normalizedTargetCommentUrl = targetCommentUrl.trim()
  return {
    actors: [{ profile_name: profileName }],
    target: {
      kind: targetKind,
      strategy: targetStrategy,
      subreddit: normalizedSubreddit || undefined,
      target_url: normalizedTargetUrl || undefined,
      target_comment_url: normalizedTargetCommentUrl || undefined,
      discovery_constraints: {
        subreddits: normalizedSubreddit ? [normalizedSubreddit] : [],
        keywords: [],
        explicit_post_targets: targetStrategy === 'discover' && targetKind === 'post' && normalizedTargetUrl ? [normalizedTargetUrl] : [],
        explicit_comment_targets: targetStrategy === 'discover' && targetKind === 'comment' && normalizedTargetCommentUrl ? [normalizedTargetCommentUrl] : [],
        allow_own_content_targets: false,
        mandatory_join_urls: [],
      },
    },
    action: {
      type: action,
      params: {
        text: text.trim() || undefined,
        title: title.trim() || undefined,
        body: body.trim() || undefined,
        scrolls: action === 'browse' ? Number(scrolls || '3') : undefined,
        attachments: imageId.trim() ? [{ image_id: imageId.trim() }] : [],
      },
    },
    verification: {
      require_success_confirmed: true,
      require_attempt_id: true,
      required_evidence_summary: true,
      required_target_reference: true,
    },
  }
}

export function RedditTab({ onOpenRemoteControl }: RedditTabProps) {
  const [credentials, setCredentials] = useState<RedditCredential[]>([])
  const [sessions, setSessions] = useState<RedditSession[]>([])
  const [missions, setMissions] = useState<RedditMission[]>([])
  const [utilityLoading, setUtilityLoading] = useState(true)

  const [programs, setPrograms] = useState<RedditProgramListItem[]>([])
  const [programsLoading, setProgramsLoading] = useState(true)
  const [operatorLoading, setOperatorLoading] = useState(false)
  const [operatorView, setOperatorView] = useState<RedditOperatorViewResponse | null>(null)
  const [selectedProgramId, setSelectedProgramId] = useState('')
  const [selectedLocalDate, setSelectedLocalDate] = useState('')
  const [profileQuery, setProfileQuery] = useState('')
  const [expandedProfile, setExpandedProfile] = useState<string | null>(null)

  const [seedLines, setSeedLines] = useState('')
  const [seeding, setSeeding] = useState(false)
  const [selectedCredentialId, setSelectedCredentialId] = useState('')
  const [creatingSession, setCreatingSession] = useState(false)

  const [selectedSession, setSelectedSession] = useState('')
  const [action, setAction] = useState<RedditExecutionActionType>(ACTIONS[0])
  const [targetKind, setTargetKind] = useState<RedditExecutionTargetKind>(defaultTargetKindForAction(ACTIONS[0]))
  const [targetStrategy, setTargetStrategy] = useState<RedditExecutionTargetStrategy>('explicit')
  const [targetUrl, setTargetUrl] = useState('')
  const [targetCommentUrl, setTargetCommentUrl] = useState('')
  const [subreddit, setSubreddit] = useState('')
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [actionText, setActionText] = useState('')
  const [imageId, setImageId] = useState('')
  const [browseScrolls, setBrowseScrolls] = useState('3')
  const [runningAction, setRunningAction] = useState(false)

  const [missionProfile, setMissionProfile] = useState('')
  const [missionAction, setMissionAction] = useState<RedditExecutionActionType>(ACTIONS[0])
  const [missionTargetKind, setMissionTargetKind] = useState<RedditExecutionTargetKind>(defaultTargetKindForAction(ACTIONS[0]))
  const [missionTargetStrategy, setMissionTargetStrategy] = useState<RedditExecutionTargetStrategy>('explicit')
  const [missionUrl, setMissionUrl] = useState('')
  const [missionTargetCommentUrl, setMissionTargetCommentUrl] = useState('')
  const [missionSubreddit, setMissionSubreddit] = useState('')
  const [missionBrief, setMissionBrief] = useState('')
  const [missionExactText, setMissionExactText] = useState('')
  const [missionTitle, setMissionTitle] = useState('')
  const [missionBody, setMissionBody] = useState('')
  const [missionImageId, setMissionImageId] = useState('')
  const [missionBrowseScrolls, setMissionBrowseScrolls] = useState('3')
  const [missionCadenceType, setMissionCadenceType] = useState<'once' | 'daily' | 'interval_hours'>('once')
  const [missionHour, setMissionHour] = useState('9')
  const [missionMinute, setMissionMinute] = useState('0')
  const [missionIntervalHours, setMissionIntervalHours] = useState('24')
  const [savingMission, setSavingMission] = useState(false)

  const fetchUtilityData = useCallback(async () => {
    setUtilityLoading(true)
    try {
      const [credentialData, sessionData, missionData] = await Promise.all([
        apiFetch<RedditCredential[]>('/reddit/credentials'),
        apiFetch<RedditSession[]>('/reddit/sessions'),
        apiFetch<{ missions: RedditMission[] }>('/reddit/missions'),
      ])
      setCredentials(credentialData)
      setSessions(sessionData)
      setMissions(missionData.missions || [])
      if (!selectedCredentialId && credentialData.length > 0) setSelectedCredentialId(credentialData[0].credential_id)
      if (!selectedSession && sessionData.length > 0) setSelectedSession(sessionData[0].profile_name)
      if (!missionProfile && sessionData.length > 0) setMissionProfile(sessionData[0].profile_name)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'failed to load reddit utility data')
    } finally {
      setUtilityLoading(false)
    }
  }, [missionProfile, selectedCredentialId, selectedSession])

  const fetchPrograms = useCallback(async () => {
    setProgramsLoading(true)
    try {
      const response = await apiFetch<{ programs: RedditProgramListItem[] }>('/reddit/programs')
      const nextPrograms = sortPrograms(response.programs || [])
      setPrograms(nextPrograms)
      setSelectedProgramId((current) => preferredProgramId(nextPrograms, current))
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'failed to load reddit programs')
    } finally {
      setProgramsLoading(false)
    }
  }, [])

  const fetchOperatorView = useCallback(async (programId: string, localDate?: string) => {
    if (!programId) {
      setOperatorView(null)
      return
    }
    setOperatorLoading(true)
    try {
      const params = new URLSearchParams()
      if (localDate) params.set('local_date', localDate)
      const suffix = params.toString() ? `?${params.toString()}` : ''
      const data = await apiFetch<RedditOperatorViewResponse>(`/reddit/programs/${programId}/operator-view${suffix}`)
      setOperatorView(data)
      if (!localDate && data.program.selected_local_date) {
        setSelectedLocalDate(data.program.selected_local_date)
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'failed to load reddit operator view')
      setOperatorView(null)
    } finally {
      setOperatorLoading(false)
    }
  }, [])

  useEffect(() => {
    void fetchUtilityData()
    void fetchPrograms()
  }, [fetchPrograms, fetchUtilityData])

  useEffect(() => {
    if (!selectedProgramId) return
    void fetchOperatorView(selectedProgramId, selectedLocalDate || undefined)
  }, [fetchOperatorView, selectedLocalDate, selectedProgramId])

  const refreshAll = async () => {
    await Promise.all([fetchUtilityData(), fetchPrograms()])
    if (selectedProgramId) {
      await fetchOperatorView(selectedProgramId, selectedLocalDate || undefined)
    }
  }

  const handleSeed = async () => {
    const lines = seedLines
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean)
    if (lines.length === 0) return

    setSeeding(true)
    try {
      const response = await apiFetch<{ imported: number; errors: string[] }>('/reddit/credentials/seed', {
        method: 'POST',
        body: JSON.stringify({ lines, fixture: true }),
      })
      if (response.errors?.length) {
        toast.error(response.errors.join(' | '))
      } else {
        toast.success(`imported ${response.imported} reddit credential(s)`)
      }
      setSeedLines('')
      await fetchUtilityData()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'failed to import reddit credentials')
    } finally {
      setSeeding(false)
    }
  }

  const handleCreateSession = async () => {
    if (!selectedCredentialId) return
    setCreatingSession(true)
    try {
      const result = await apiFetch<{ success: boolean; error?: string }>('/reddit/sessions/create', {
        method: 'POST',
        body: JSON.stringify({ credential_id: selectedCredentialId }),
      })
      if (result.success) {
        toast.success('reddit session created')
      } else {
        toast.error(result.error || 'reddit session creation failed')
      }
      await fetchUtilityData()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'failed to create reddit session')
    } finally {
      setCreatingSession(false)
    }
  }

  const handleActionChange = (value: string) => {
    const nextAction = value as RedditExecutionActionType
    setAction(nextAction)
    setTargetKind(defaultTargetKindForAction(nextAction))
  }

  const handleExecutionRun = async () => {
    if (!selectedSession) return
    setRunningAction(true)
    try {
      const result = await apiFetch<{ success?: boolean; results?: Array<{ error?: string }>; proof_summary?: { success_confirmed?: number } }>(
        '/reddit/executions/run',
        {
          method: 'POST',
          body: JSON.stringify(
            buildExecutionRequest({
              profileName: selectedSession,
              action,
              targetKind,
              targetStrategy,
              targetUrl,
              targetCommentUrl,
              subreddit,
              text: actionText,
              title,
              body,
              imageId,
              scrolls: browseScrolls,
            })
          ),
        }
      )
      if (result.success) {
        toast.success(`${action} confirmed`)
      } else {
        const error = result.results?.find((entry) => entry.error)?.error
        toast.error(error || `${action} failed`)
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'failed to run reddit execution')
    } finally {
      setRunningAction(false)
    }
  }

  const handleMissionActionChange = (value: string) => {
    const nextAction = value as RedditExecutionActionType
    setMissionAction(nextAction)
    setMissionTargetKind(defaultTargetKindForAction(nextAction))
  }

  const handleSaveMission = async () => {
    if (!missionProfile) return
    setSavingMission(true)
    try {
      await apiFetch('/reddit/missions', {
        method: 'POST',
        body: JSON.stringify({
          execution: buildExecutionRequest({
            profileName: missionProfile,
            action: missionAction,
            targetKind: missionTargetKind,
            targetStrategy: missionTargetStrategy,
            targetUrl: missionUrl,
            targetCommentUrl: missionTargetCommentUrl,
            subreddit: missionSubreddit,
            text: missionExactText || missionBrief,
            title: missionTitle,
            body: missionBody,
            imageId: missionImageId,
            scrolls: missionBrowseScrolls,
          }),
          cadence: {
            type: missionCadenceType,
            hour: missionCadenceType === 'daily' ? Number(missionHour) : undefined,
            minute: missionCadenceType === 'daily' ? Number(missionMinute) : undefined,
            interval_hours: missionCadenceType === 'interval_hours' ? Number(missionIntervalHours) : undefined,
          },
        }),
      })
      toast.success('reddit mission saved')
      await fetchUtilityData()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'failed to save reddit mission')
    } finally {
      setSavingMission(false)
    }
  }

  const handleRunMission = async (missionId: string) => {
    try {
      const result = await apiFetch<{ success: boolean; result?: { error?: string } }>(`/reddit/missions/${missionId}/run-now`, {
        method: 'POST',
      })
      if (result.success) {
        toast.success('mission executed')
      } else {
        toast.error(result.result?.error || 'mission run failed')
      }
      await fetchUtilityData()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'failed to run mission')
    }
  }

  const handleProgramSelect = (programId: string) => {
    setSelectedProgramId(programId)
    setSelectedLocalDate('')
    setExpandedProfile(null)
    setProfileQuery('')
  }

  const handleDaySelect = (localDate: string) => {
    setSelectedLocalDate(localDate)
    setExpandedProfile(null)
  }

  const handleToggleProfile = (profileName: string) => {
    setExpandedProfile((current) => current === profileName ? null : profileName)
  }

  return (
    <div className="mt-6 space-y-6">
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px] xl:items-start">
        <RedditOpsWorkspace
          programs={programs}
          selectedProgramId={selectedProgramId}
          selectedLocalDate={selectedLocalDate}
          profileQuery={profileQuery}
          operatorView={operatorView}
          loadingPrograms={programsLoading}
          loadingOperatorView={operatorLoading}
          expandedProfile={expandedProfile}
          onSelectProgram={handleProgramSelect}
          onSelectLocalDate={handleDaySelect}
          onProfileQueryChange={setProfileQuery}
          onToggleProfile={handleToggleProfile}
          onRefresh={() => void refreshAll()}
        />

        <RedditUtilityRail
          credentials={credentials}
          sessions={sessions}
          missions={missions}
          loading={utilityLoading}
          seedLines={seedLines}
          seeding={seeding}
          onSeedLinesChange={setSeedLines}
          onSeed={() => void handleSeed()}
          selectedCredentialId={selectedCredentialId}
          creatingSession={creatingSession}
          onSelectCredential={setSelectedCredentialId}
          onCreateSession={() => void handleCreateSession()}
          onOpenRemoteControl={onOpenRemoteControl}
          selectedSession={selectedSession}
          action={action}
          targetKind={targetKind}
          targetStrategy={targetStrategy}
          targetUrl={targetUrl}
          targetCommentUrl={targetCommentUrl}
          subreddit={subreddit}
          title={title}
          body={body}
          actionText={actionText}
          imageId={imageId}
          browseScrolls={browseScrolls}
          runningAction={runningAction}
          onSelectSession={setSelectedSession}
          onActionChange={handleActionChange}
          onTargetKindChange={setTargetKind}
          onTargetStrategyChange={setTargetStrategy}
          onTargetUrlChange={setTargetUrl}
          onTargetCommentUrlChange={setTargetCommentUrl}
          onSubredditChange={setSubreddit}
          onTitleChange={setTitle}
          onBodyChange={setBody}
          onActionTextChange={setActionText}
          onImageIdChange={setImageId}
          onBrowseScrollsChange={setBrowseScrolls}
          onRunAction={() => void handleExecutionRun()}
          missionProfile={missionProfile}
          missionAction={missionAction}
          missionTargetKind={missionTargetKind}
          missionTargetStrategy={missionTargetStrategy}
          missionUrl={missionUrl}
          missionTargetCommentUrl={missionTargetCommentUrl}
          missionSubreddit={missionSubreddit}
          missionBrief={missionBrief}
          missionExactText={missionExactText}
          missionTitle={missionTitle}
          missionBody={missionBody}
          missionImageId={missionImageId}
          missionBrowseScrolls={missionBrowseScrolls}
          missionCadenceType={missionCadenceType}
          missionHour={missionHour}
          missionMinute={missionMinute}
          missionIntervalHours={missionIntervalHours}
          savingMission={savingMission}
          onMissionProfileChange={setMissionProfile}
          onMissionActionChange={handleMissionActionChange}
          onMissionTargetKindChange={setMissionTargetKind}
          onMissionTargetStrategyChange={setMissionTargetStrategy}
          onMissionUrlChange={setMissionUrl}
          onMissionTargetCommentUrlChange={setMissionTargetCommentUrl}
          onMissionSubredditChange={setMissionSubreddit}
          onMissionBriefChange={setMissionBrief}
          onMissionExactTextChange={setMissionExactText}
          onMissionTitleChange={setMissionTitle}
          onMissionBodyChange={setMissionBody}
          onMissionImageIdChange={setMissionImageId}
          onMissionBrowseScrollsChange={setMissionBrowseScrolls}
          onMissionCadenceTypeChange={setMissionCadenceType}
          onMissionHourChange={setMissionHour}
          onMissionMinuteChange={setMissionMinute}
          onMissionIntervalHoursChange={setMissionIntervalHours}
          onSaveMission={() => void handleSaveMission()}
          onRunMission={(missionId) => void handleRunMission(missionId)}
        />
      </div>
    </div>
  )
}
