import { Mouse, Wrench } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'

import type { RemoteSessionTarget } from '@/components/remote/types'
import type { RedditCredential, RedditMission, RedditSession } from '@/components/reddit/types'

const ACTIONS = [
  'browse_feed',
  'upvote',
  'open_target',
  'create_post',
  'comment_post',
  'reply_comment',
  'upload_media',
] as const

interface RedditUtilityRailProps {
  credentials: RedditCredential[]
  sessions: RedditSession[]
  missions: RedditMission[]
  loading: boolean
  seedLines: string
  seeding: boolean
  onSeedLinesChange: (value: string) => void
  onSeed: () => void
  selectedCredentialId: string
  creatingSession: boolean
  onSelectCredential: (value: string) => void
  onCreateSession: () => void
  onOpenRemoteControl?: (session: RemoteSessionTarget) => void
  selectedSession: string
  action: string
  targetUrl: string
  subreddit: string
  title: string
  body: string
  actionText: string
  imageId: string
  runningAction: boolean
  onSelectSession: (value: string) => void
  onActionChange: (value: string) => void
  onTargetUrlChange: (value: string) => void
  onSubredditChange: (value: string) => void
  onTitleChange: (value: string) => void
  onBodyChange: (value: string) => void
  onActionTextChange: (value: string) => void
  onImageIdChange: (value: string) => void
  onRunAction: () => void
  missionProfile: string
  missionAction: string
  missionUrl: string
  missionSubreddit: string
  missionBrief: string
  missionExactText: string
  missionTitle: string
  missionBody: string
  missionImageId: string
  missionCadenceType: 'once' | 'daily' | 'interval_hours'
  missionHour: string
  missionMinute: string
  missionIntervalHours: string
  savingMission: boolean
  onMissionProfileChange: (value: string) => void
  onMissionActionChange: (value: string) => void
  onMissionUrlChange: (value: string) => void
  onMissionSubredditChange: (value: string) => void
  onMissionBriefChange: (value: string) => void
  onMissionExactTextChange: (value: string) => void
  onMissionTitleChange: (value: string) => void
  onMissionBodyChange: (value: string) => void
  onMissionImageIdChange: (value: string) => void
  onMissionCadenceTypeChange: (value: 'once' | 'daily' | 'interval_hours') => void
  onMissionHourChange: (value: string) => void
  onMissionMinuteChange: (value: string) => void
  onMissionIntervalHoursChange: (value: string) => void
  onSaveMission: () => void
  onRunMission: (missionId: string) => void
}

export function RedditUtilityRail({
  credentials,
  sessions,
  missions,
  loading,
  seedLines,
  seeding,
  onSeedLinesChange,
  onSeed,
  selectedCredentialId,
  creatingSession,
  onSelectCredential,
  onCreateSession,
  onOpenRemoteControl,
  selectedSession,
  action,
  targetUrl,
  subreddit,
  title,
  body,
  actionText,
  imageId,
  runningAction,
  onSelectSession,
  onActionChange,
  onTargetUrlChange,
  onSubredditChange,
  onTitleChange,
  onBodyChange,
  onActionTextChange,
  onImageIdChange,
  onRunAction,
  missionProfile,
  missionAction,
  missionUrl,
  missionSubreddit,
  missionBrief,
  missionExactText,
  missionTitle,
  missionBody,
  missionImageId,
  missionCadenceType,
  missionHour,
  missionMinute,
  missionIntervalHours,
  savingMission,
  onMissionProfileChange,
  onMissionActionChange,
  onMissionUrlChange,
  onMissionSubredditChange,
  onMissionBriefChange,
  onMissionExactTextChange,
  onMissionTitleChange,
  onMissionBodyChange,
  onMissionImageIdChange,
  onMissionCadenceTypeChange,
  onMissionHourChange,
  onMissionMinuteChange,
  onMissionIntervalHoursChange,
  onSaveMission,
  onRunMission,
}: RedditUtilityRailProps) {
  const linkedCredentials = credentials.filter((credential) => credential.session_connected).length
  const validSessions = sessions.filter((session) => session.valid).length

  return (
    <div className="space-y-4 xl:sticky xl:top-6">
      <Card className="border-[#d8d3c5] bg-[linear-gradient(180deg,rgba(255,255,255,0.98),rgba(247,242,232,0.98))] shadow-sm">
        <CardHeader className="pb-3">
          <CardTitle className="text-base text-[#1f1f1a]">sessions & credentials</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap gap-2 text-xs">
            <Badge variant="secondary">{validSessions}/{sessions.length || 0} valid sessions</Badge>
            <Badge variant="outline">{linkedCredentials}/{credentials.length || 0} linked credentials</Badge>
          </div>

          <div className="space-y-2">
            <div className="text-xs font-semibold uppercase tracking-[0.14em] text-[#8a7f6a]">sessions</div>
            <div className="max-h-[320px] space-y-2 overflow-y-auto pr-1">
              {sessions.length === 0 ? (
                <div className="rounded-xl border border-dashed border-[#d8d3c5] p-3 text-sm text-[#6e6759]">
                  {loading ? 'loading...' : 'no reddit sessions yet'}
                </div>
              ) : sessions.map((session) => (
                <div key={session.profile_name} className="rounded-xl border border-[#e6decd] bg-white p-2.5">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="truncate font-medium text-[#1f1f1a]">{session.display_name || session.profile_name}</div>
                      <div className="truncate text-xs text-[#7a7365]">{session.profile_url || session.username || session.profile_name}</div>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge variant={session.valid ? 'default' : 'destructive'}>
                        {session.valid ? 'valid' : 'needs attention'}
                      </Badge>
                      <Button
                        size="icon"
                        variant="outline"
                        onClick={() =>
                          onOpenRemoteControl?.({
                            platform: 'reddit',
                            profileName: session.profile_name,
                            displayName: session.display_name || session.profile_name,
                            valid: session.valid,
                          })
                        }
                        disabled={!session.valid || !onOpenRemoteControl}
                        className="h-7 w-7 border-[#d8d3c5] bg-white"
                      >
                        <Mouse className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="space-y-2">
            <div className="text-xs font-semibold uppercase tracking-[0.14em] text-[#8a7f6a]">credentials</div>
            <div className="max-h-[280px] space-y-2 overflow-y-auto pr-1">
              {credentials.length === 0 ? (
                <div className="rounded-xl border border-dashed border-[#d8d3c5] p-3 text-sm text-[#6e6759]">
                  {loading ? 'loading...' : 'no reddit credentials yet'}
                </div>
              ) : credentials.map((credential) => (
                <div key={credential.credential_id} className="rounded-xl border border-[#e6decd] bg-white p-2.5">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="truncate font-medium text-[#1f1f1a]">{credential.username || credential.uid}</div>
                      <div className="truncate text-xs text-[#7a7365]">{credential.email || 'no email stored'}</div>
                    </div>
                    <Badge variant={credential.session_connected ? 'default' : 'outline'}>
                      {credential.session_connected ? 'linked' : 'unlinked'}
                    </Badge>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <details className="group rounded-2xl border border-[#e6decd] bg-white">
            <summary className="flex cursor-pointer list-none items-center justify-between px-3 py-2.5 text-sm font-semibold text-[#1f1f1a]">
              <span>credential setup</span>
              <span className="text-[11px] uppercase tracking-[0.14em] text-[#8a7f6a] group-open:hidden">show</span>
              <span className="hidden text-[11px] uppercase tracking-[0.14em] text-[#8a7f6a] group-open:inline">hide</span>
            </summary>
            <div className="space-y-4 border-t border-[#ede5d5] p-3">
              <div className="space-y-2">
                <Label>link credential to session</Label>
                <Select value={selectedCredentialId} onValueChange={onSelectCredential}>
                  <SelectTrigger className="border-[#d8d3c5] bg-white">
                    <SelectValue placeholder="choose credential" />
                  </SelectTrigger>
                  <SelectContent>
                    {credentials.map((credential) => (
                      <SelectItem key={credential.credential_id} value={credential.credential_id}>
                        {credential.username || credential.uid}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button onClick={onCreateSession} disabled={creatingSession || !selectedCredentialId} className="w-full">
                  {creatingSession ? 'creating session...' : 'create reddit session'}
                </Button>
              </div>

              <div className="space-y-2">
                <Label>import credentials</Label>
                <Textarea
                  value={seedLines}
                  onChange={(event) => onSeedLinesChange(event.target.value)}
                  placeholder="username:password:email:email_password:totp_secret:https://www.reddit.com/user/username/"
                  className="min-h-[96px] border-[#d8d3c5] bg-white"
                />
                <Button onClick={onSeed} disabled={seeding || !seedLines.trim()} className="w-full">
                  {seeding ? 'importing...' : 'import reddit credentials'}
                </Button>
              </div>
            </div>
          </details>
        </CardContent>
      </Card>

      <details className="group rounded-2xl border border-[#d8d3c5] bg-white shadow-sm">
        <summary className="flex cursor-pointer list-none items-center justify-between px-4 py-3 text-sm font-semibold text-[#1f1f1a]">
          <span className="flex items-center gap-2">
            <Wrench className="h-4 w-4 text-[#8a7f6a]" />
            advanced tools
          </span>
          <span className="text-xs uppercase tracking-[0.14em] text-[#8a7f6a] group-open:hidden">show</span>
          <span className="hidden text-xs uppercase tracking-[0.14em] text-[#8a7f6a] group-open:inline">hide</span>
        </summary>
        <div className="space-y-4 border-t border-[#ede5d5] p-4">
          <Card className="border-[#eee6d6] shadow-none">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">run reddit action</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="space-y-2">
                <Label>session</Label>
                <Select value={selectedSession} onValueChange={onSelectSession}>
                  <SelectTrigger className="border-[#d8d3c5] bg-white">
                    <SelectValue placeholder="choose session" />
                  </SelectTrigger>
                  <SelectContent>
                    {sessions.map((session) => (
                      <SelectItem key={session.profile_name} value={session.profile_name}>
                        {session.display_name || session.profile_name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>action</Label>
                <Select value={action} onValueChange={onActionChange}>
                  <SelectTrigger className="border-[#d8d3c5] bg-white">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {ACTIONS.map((item) => (
                      <SelectItem key={item} value={item}>{item}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Input value={targetUrl} onChange={(event) => onTargetUrlChange(event.target.value)} placeholder="target url" className="border-[#d8d3c5] bg-white" />
              <Input value={subreddit} onChange={(event) => onSubredditChange(event.target.value)} placeholder="subreddit" className="border-[#d8d3c5] bg-white" />
              <Input value={imageId} onChange={(event) => onImageIdChange(event.target.value)} placeholder="image id" className="border-[#d8d3c5] bg-white" />
              <Input value={title} onChange={(event) => onTitleChange(event.target.value)} placeholder="title" className="border-[#d8d3c5] bg-white" />
              <Textarea value={body} onChange={(event) => onBodyChange(event.target.value)} placeholder="body" className="border-[#d8d3c5] bg-white" />
              <Textarea value={actionText} onChange={(event) => onActionTextChange(event.target.value)} placeholder="comment or reply text" className="border-[#d8d3c5] bg-white" />
              <Button onClick={onRunAction} disabled={runningAction || !selectedSession} className="w-full">
                {runningAction ? 'running...' : 'run reddit action'}
              </Button>
            </CardContent>
          </Card>

          <Card className="border-[#eee6d6] shadow-none">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">recurring missions</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="space-y-2">
                <Label>profile</Label>
                <Select value={missionProfile} onValueChange={onMissionProfileChange}>
                  <SelectTrigger className="border-[#d8d3c5] bg-white">
                    <SelectValue placeholder="choose session" />
                  </SelectTrigger>
                  <SelectContent>
                    {sessions.map((session) => (
                      <SelectItem key={session.profile_name} value={session.profile_name}>
                        {session.display_name || session.profile_name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label>action</Label>
                <Select value={missionAction} onValueChange={onMissionActionChange}>
                  <SelectTrigger className="border-[#d8d3c5] bg-white">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {ACTIONS.map((item) => (
                      <SelectItem key={item} value={item}>{item}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Textarea value={missionBrief} onChange={(event) => onMissionBriefChange(event.target.value)} placeholder="brief" className="border-[#d8d3c5] bg-white" />
              <Textarea value={missionExactText} onChange={(event) => onMissionExactTextChange(event.target.value)} placeholder="exact text override" className="border-[#d8d3c5] bg-white" />
              <Input value={missionUrl} onChange={(event) => onMissionUrlChange(event.target.value)} placeholder="target url" className="border-[#d8d3c5] bg-white" />
              <Input value={missionSubreddit} onChange={(event) => onMissionSubredditChange(event.target.value)} placeholder="subreddit" className="border-[#d8d3c5] bg-white" />
              <Input value={missionImageId} onChange={(event) => onMissionImageIdChange(event.target.value)} placeholder="image id" className="border-[#d8d3c5] bg-white" />
              <Input value={missionTitle} onChange={(event) => onMissionTitleChange(event.target.value)} placeholder="title" className="border-[#d8d3c5] bg-white" />
              <Textarea value={missionBody} onChange={(event) => onMissionBodyChange(event.target.value)} placeholder="body" className="border-[#d8d3c5] bg-white" />
              <div className="space-y-2">
                <Label>cadence</Label>
                <Select value={missionCadenceType} onValueChange={(value) => onMissionCadenceTypeChange(value as 'once' | 'daily' | 'interval_hours')}>
                  <SelectTrigger className="border-[#d8d3c5] bg-white">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="once">once</SelectItem>
                    <SelectItem value="daily">daily</SelectItem>
                    <SelectItem value="interval_hours">interval hours</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {missionCadenceType === 'daily' ? (
                <div className="grid grid-cols-2 gap-3">
                  <Input value={missionHour} onChange={(event) => onMissionHourChange(event.target.value)} placeholder="hour" className="border-[#d8d3c5] bg-white" />
                  <Input value={missionMinute} onChange={(event) => onMissionMinuteChange(event.target.value)} placeholder="minute" className="border-[#d8d3c5] bg-white" />
                </div>
              ) : null}
              {missionCadenceType === 'interval_hours' ? (
                <Input value={missionIntervalHours} onChange={(event) => onMissionIntervalHoursChange(event.target.value)} placeholder="interval hours" className="border-[#d8d3c5] bg-white" />
              ) : null}
              <Button onClick={onSaveMission} disabled={savingMission || !missionProfile} className="w-full">
                {savingMission ? 'saving...' : 'save reddit mission'}
              </Button>

              <div className="space-y-2 pt-2">
                {missions.length === 0 ? (
                  <div className="rounded-xl border border-dashed border-[#d8d3c5] p-3 text-sm text-[#6e6759]">
                    no saved missions
                  </div>
                ) : missions.map((mission) => (
                  <div key={mission.id} className="rounded-xl border border-[#e6decd] bg-white p-3">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="truncate font-medium text-[#1f1f1a]">{mission.action} · {mission.profile_name}</div>
                        <div className="truncate text-xs text-[#7a7365]">{mission.brief || mission.exact_text || mission.target_url || 'no brief'}</div>
                      </div>
                      <div className="flex items-center gap-2">
                        <Badge variant="outline">{mission.status}</Badge>
                        <Button size="sm" variant="outline" onClick={() => onRunMission(mission.id)} className="border-[#d8d3c5] bg-white">
                          run
                        </Button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </div>
      </details>
    </div>
  )
}
