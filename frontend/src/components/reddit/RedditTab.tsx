import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Mouse } from 'lucide-react'

import { apiFetch } from '@/lib/api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import type { RemoteSessionTarget } from '@/components/remote/types'

interface RedditCredential {
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

interface RedditSession {
  profile_name: string
  display_name?: string | null
  username?: string | null
  email?: string | null
  profile_url?: string | null
  valid: boolean
  tags?: string[]
  fixture?: boolean
}

interface RedditMission {
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

const ACTIONS = [
  'browse_feed',
  'upvote',
  'open_target',
  'create_post',
  'comment_post',
  'reply_comment',
  'upload_media',
] as const

interface RedditTabProps {
  onOpenRemoteControl?: (session: RemoteSessionTarget) => void
}

export function RedditTab({ onOpenRemoteControl }: RedditTabProps) {
  const [credentials, setCredentials] = useState<RedditCredential[]>([])
  const [sessions, setSessions] = useState<RedditSession[]>([])
  const [missions, setMissions] = useState<RedditMission[]>([])
  const [loading, setLoading] = useState(true)

  const [seedLines, setSeedLines] = useState('')
  const [seeding, setSeeding] = useState(false)

  const [selectedCredentialId, setSelectedCredentialId] = useState('')
  const [creatingSession, setCreatingSession] = useState(false)

  const [selectedSession, setSelectedSession] = useState('')
  const [action, setAction] = useState<typeof ACTIONS[number]>('browse_feed')
  const [targetUrl, setTargetUrl] = useState('')
  const [subreddit, setSubreddit] = useState('')
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [actionText, setActionText] = useState('')
  const [imageId, setImageId] = useState('')
  const [runningAction, setRunningAction] = useState(false)

  const [missionProfile, setMissionProfile] = useState('')
  const [missionAction, setMissionAction] = useState<typeof ACTIONS[number]>('browse_feed')
  const [missionUrl, setMissionUrl] = useState('')
  const [missionSubreddit, setMissionSubreddit] = useState('')
  const [missionBrief, setMissionBrief] = useState('')
  const [missionExactText, setMissionExactText] = useState('')
  const [missionTitle, setMissionTitle] = useState('')
  const [missionBody, setMissionBody] = useState('')
  const [missionImageId, setMissionImageId] = useState('')
  const [missionCadenceType, setMissionCadenceType] = useState<'once' | 'daily' | 'interval_hours'>('once')
  const [missionHour, setMissionHour] = useState('9')
  const [missionMinute, setMissionMinute] = useState('0')
  const [missionIntervalHours, setMissionIntervalHours] = useState('24')
  const [savingMission, setSavingMission] = useState(false)

  const fetchAll = async () => {
    setLoading(true)
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
      toast.error(error instanceof Error ? error.message : 'Failed to load Reddit data')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void fetchAll()
  }, [])

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
        toast.success(`Imported ${response.imported} Reddit credential(s)`)
      }
      setSeedLines('')
      await fetchAll()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to import Reddit credentials')
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
        toast.success('Reddit session created')
      } else {
        toast.error(result.error || 'Reddit session creation failed')
      }
      await fetchAll()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to create Reddit session')
    } finally {
      setCreatingSession(false)
    }
  }

  const handleRunAction = async () => {
    if (!selectedSession) return
    setRunningAction(true)
    try {
      const result = await apiFetch<{ success: boolean; error?: string }>('/reddit/actions/run', {
        method: 'POST',
        body: JSON.stringify({
          profile_name: selectedSession,
          action,
          url: targetUrl || undefined,
          subreddit: subreddit || undefined,
          title: title || undefined,
          body: body || undefined,
          text: actionText || undefined,
          image_id: imageId || undefined,
        }),
      })
      if (result.success) {
        toast.success(`Action ${action} completed`)
      } else {
        toast.error(result.error || `${action} failed`)
      }
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to run Reddit action')
    } finally {
      setRunningAction(false)
    }
  }

  const handleSaveMission = async () => {
    if (!missionProfile) return
    setSavingMission(true)
    try {
      await apiFetch('/reddit/missions', {
        method: 'POST',
        body: JSON.stringify({
          profile_name: missionProfile,
          action: missionAction,
          target_url: missionUrl || undefined,
          subreddit: missionSubreddit || undefined,
          brief: missionBrief || undefined,
          exact_text: missionExactText || undefined,
          title: missionTitle || undefined,
          body: missionBody || undefined,
          image_id: missionImageId || undefined,
          cadence: {
            type: missionCadenceType,
            hour: missionCadenceType === 'daily' ? Number(missionHour) : undefined,
            minute: missionCadenceType === 'daily' ? Number(missionMinute) : undefined,
            interval_hours: missionCadenceType === 'interval_hours' ? Number(missionIntervalHours) : undefined,
          },
        }),
      })
      toast.success('Reddit mission saved')
      await fetchAll()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to save Reddit mission')
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
        toast.success('Mission executed')
      } else {
        toast.error(result.result?.error || 'Mission run failed')
      }
      await fetchAll()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Failed to run mission')
    }
  }

  return (
    <div className="space-y-6 mt-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-[#222222]">Reddit Operations</h2>
          <p className="text-sm text-[#666666]">Seed credentials, create proxy-backed sessions, run live actions, and manage recurring Reddit briefs.</p>
        </div>
        <Button variant="outline" onClick={() => void fetchAll()} disabled={loading}>
          Refresh
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Seed Reddit Credentials</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Label>Paste one Reddit account per line</Label>
          <Textarea
            value={seedLines}
            onChange={(event) => setSeedLines(event.target.value)}
            placeholder="username:password:email:email_password:totp_secret:https://www.reddit.com/user/username/"
            className="min-h-[120px]"
          />
          <Button onClick={handleSeed} disabled={seeding || !seedLines.trim()}>
            {seeding ? 'Importing...' : 'Import Reddit Credentials'}
          </Button>
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Reddit Credentials</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {credentials.length === 0 ? (
              <p className="text-sm text-[#666666]">No Reddit credentials loaded yet.</p>
            ) : credentials.map((credential) => (
              <div key={credential.credential_id} className="rounded-xl border border-[#ebebeb] p-3">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="font-medium text-[#222222]">{credential.username || credential.uid}</div>
                    <div className="text-xs text-[#666666]">{credential.email || 'No email stored'}</div>
                  </div>
                  <div className="flex items-center gap-2">
                    {credential.fixture ? <Badge variant="secondary">Fixture</Badge> : null}
                    <Badge variant={credential.session_connected ? 'default' : 'outline'}>
                      {credential.session_connected ? 'Session linked' : 'No session'}
                    </Badge>
                  </div>
                </div>
              </div>
            ))}
            <div className="space-y-2">
              <Label>Create session for credential</Label>
              <Select value={selectedCredentialId} onValueChange={setSelectedCredentialId}>
                <SelectTrigger>
                  <SelectValue placeholder="Choose credential" />
                </SelectTrigger>
                <SelectContent>
                  {credentials.map((credential) => (
                    <SelectItem key={credential.credential_id} value={credential.credential_id}>
                      {credential.username || credential.uid}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button onClick={handleCreateSession} disabled={creatingSession || !selectedCredentialId}>
                {creatingSession ? 'Creating Session...' : 'Create Reddit Session'}
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-lg">Reddit Sessions</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {sessions.length === 0 ? (
              <p className="text-sm text-[#666666]">No Reddit sessions yet.</p>
            ) : sessions.map((session) => (
              <div key={session.profile_name} className="rounded-xl border border-[#ebebeb] p-3">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="font-medium text-[#222222]">{session.display_name || session.profile_name}</div>
                    <div className="text-xs text-[#666666]">{session.profile_url || session.username}</div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge variant={session.valid ? 'default' : 'destructive'}>
                      {session.valid ? 'Valid' : 'Needs attention'}
                    </Badge>
                    <Button
                      size="sm"
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
                      className="h-8 px-2"
                    >
                      <Mouse className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Run Reddit Action</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-2">
          <div className="space-y-2">
            <Label>Session</Label>
            <Select value={selectedSession} onValueChange={setSelectedSession}>
              <SelectTrigger>
                <SelectValue placeholder="Choose session" />
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
            <Label>Action</Label>
            <Select value={action} onValueChange={(value) => setAction(value as typeof ACTIONS[number])}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ACTIONS.map((item) => (
                  <SelectItem key={item} value={item}>{item}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2 md:col-span-2">
            <Label>Target URL</Label>
            <Input value={targetUrl} onChange={(event) => setTargetUrl(event.target.value)} placeholder="https://www.reddit.com/..." />
          </div>
          <div className="space-y-2">
            <Label>Subreddit</Label>
            <Input value={subreddit} onChange={(event) => setSubreddit(event.target.value)} placeholder="womenshealth" />
          </div>
          <div className="space-y-2">
            <Label>Image ID</Label>
            <Input value={imageId} onChange={(event) => setImageId(event.target.value)} placeholder="media upload id" />
          </div>
          <div className="space-y-2 md:col-span-2">
            <Label>Title</Label>
            <Input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Question-style title" />
          </div>
          <div className="space-y-2 md:col-span-2">
            <Label>Body / Post Content</Label>
            <Textarea value={body} onChange={(event) => setBody(event.target.value)} placeholder="Optional post body" />
          </div>
          <div className="space-y-2 md:col-span-2">
            <Label>Comment / Reply Text</Label>
            <Textarea value={actionText} onChange={(event) => setActionText(event.target.value)} placeholder="Supportive comment or reply text" />
          </div>
          <div className="md:col-span-2">
            <Button onClick={handleRunAction} disabled={runningAction || !selectedSession}>
              {runningAction ? 'Running...' : 'Run Reddit Action'}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Recurring Reddit Missions</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-2">
          <div className="space-y-2">
            <Label>Profile</Label>
            <Select value={missionProfile} onValueChange={setMissionProfile}>
              <SelectTrigger>
                <SelectValue placeholder="Choose profile" />
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
            <Label>Mission Action</Label>
            <Select value={missionAction} onValueChange={(value) => setMissionAction(value as typeof ACTIONS[number])}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ACTIONS.map((item) => (
                  <SelectItem key={item} value={item}>{item}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2 md:col-span-2">
            <Label>Brief</Label>
            <Textarea value={missionBrief} onChange={(event) => setMissionBrief(event.target.value)} placeholder="Go to this URL, support this person, write valuable information..." />
          </div>
          <div className="space-y-2 md:col-span-2">
            <Label>Exact Text Override</Label>
            <Textarea value={missionExactText} onChange={(event) => setMissionExactText(event.target.value)} placeholder="Optional exact content to post" />
          </div>
          <div className="space-y-2 md:col-span-2">
            <Label>Target URL</Label>
            <Input value={missionUrl} onChange={(event) => setMissionUrl(event.target.value)} placeholder="https://www.reddit.com/..." />
          </div>
          <div className="space-y-2">
            <Label>Subreddit</Label>
            <Input value={missionSubreddit} onChange={(event) => setMissionSubreddit(event.target.value)} placeholder="womenshealth" />
          </div>
          <div className="space-y-2">
            <Label>Image ID</Label>
            <Input value={missionImageId} onChange={(event) => setMissionImageId(event.target.value)} placeholder="Optional media id" />
          </div>
          <div className="space-y-2 md:col-span-2">
            <Label>Mission Title</Label>
            <Input value={missionTitle} onChange={(event) => setMissionTitle(event.target.value)} placeholder="Optional title" />
          </div>
          <div className="space-y-2 md:col-span-2">
            <Label>Mission Body</Label>
            <Textarea value={missionBody} onChange={(event) => setMissionBody(event.target.value)} placeholder="Optional body" />
          </div>
          <div className="space-y-2">
            <Label>Cadence</Label>
            <Select value={missionCadenceType} onValueChange={(value) => setMissionCadenceType(value as 'once' | 'daily' | 'interval_hours')}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="once">once</SelectItem>
                <SelectItem value="daily">daily</SelectItem>
                <SelectItem value="interval_hours">interval_hours</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {missionCadenceType === 'daily' ? (
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-2">
                <Label>Hour</Label>
                <Input value={missionHour} onChange={(event) => setMissionHour(event.target.value)} />
              </div>
              <div className="space-y-2">
                <Label>Minute</Label>
                <Input value={missionMinute} onChange={(event) => setMissionMinute(event.target.value)} />
              </div>
            </div>
          ) : null}
          {missionCadenceType === 'interval_hours' ? (
            <div className="space-y-2">
              <Label>Interval Hours</Label>
              <Input value={missionIntervalHours} onChange={(event) => setMissionIntervalHours(event.target.value)} />
            </div>
          ) : null}
          <div className="md:col-span-2">
            <Button onClick={handleSaveMission} disabled={savingMission || !missionProfile}>
              {savingMission ? 'Saving...' : 'Save Reddit Mission'}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Saved Missions</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {missions.length === 0 ? (
            <p className="text-sm text-[#666666]">No Reddit missions saved yet.</p>
          ) : missions.map((mission) => (
            <div key={mission.id} className="rounded-xl border border-[#ebebeb] p-3">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="font-medium text-[#222222]">{mission.action} · {mission.profile_name}</div>
                  <div className="text-xs text-[#666666]">{mission.brief || mission.exact_text || mission.target_url || 'No brief supplied'}</div>
                  <div className="mt-1 text-[11px] text-[#888888]">
                    next run: {mission.next_run_at || 'none'} · last run: {mission.last_run_at || 'never'}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Badge variant="outline">{mission.status}</Badge>
                  <Button size="sm" variant="outline" onClick={() => void handleRunMission(mission.id)}>
                    Run Now
                  </Button>
                </div>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  )
}
