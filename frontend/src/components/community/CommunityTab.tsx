import { useState, useEffect, useCallback } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '../ui/card'
import { Button } from '../ui/button'
import { Badge } from '../ui/badge'
import { Textarea } from '../ui/textarea'
import { apiFetch } from '../../lib/api'
import { toast } from 'sonner'
import {
  Heart, MessageSquare, FileText, ThumbsUp, ChevronLeft,
  ChevronRight, Play, Settings, BookOpen, Image,
  RefreshCw, Loader2
} from 'lucide-react'

// ── Types ──

interface Persona {
  profile_name: string
  display_name: string | null
  age: number | null
  persona_prompt: string
}

interface Arc {
  profile_name: string
  current_stage: string
  stage_started_at: string
}

interface Task {
  id: string
  profile_name: string
  action: string
  status: string
  text: string | null
  target_url: string | null
  image_prompt: string | null
  completed_at: string | null
  scheduled_at: string
  result: Record<string, unknown> | null
  attempts: number
  error: string | null
}

interface ProfileStats {
  persona: Persona | null
  arc: Arc | null
  stats: {
    this_week: { group_posts: number; likes: number; replies: number; timeline: number }
    all_time: { total_actions: number; success_rate: number; failed: number }
    recent_group_activity: Array<{
      action: string; text: string; completed_at: string; target_url: string
    }>
  }
}

interface TaskCounts {
  pending: number; running: number; completed: number; failed: number; total: number
}

// ── Main Component ──

export default function CommunityTab() {
  const [view, setView] = useState<'feed' | 'profile' | 'kb' | 'config'>('feed')
  const [selectedProfile, setSelectedProfile] = useState<string | null>(null)

  // Global state
  const [personas, setPersonas] = useState<Persona[]>([])
  const [arcs, setArcs] = useState<Arc[]>([])
  const [taskCounts, setTaskCounts] = useState<TaskCounts>({ pending: 0, running: 0, completed: 0, failed: 0, total: 0 })
  const [feed, setFeed] = useState<Task[]>([])
  const [loading, setLoading] = useState(true)
  const [schedulerRunning, setSchedulerRunning] = useState(false)

  const arcMap = Object.fromEntries(arcs.map(a => [a.profile_name, a.current_stage]))

  const loadData = useCallback(async () => {
    try {
      const [personasRes, arcsRes, statusRes, feedRes] = await Promise.all([
        apiFetch<Persona[]>('/community/personas'),
        apiFetch<Arc[]>('/community/arcs'),
        apiFetch<{ scheduler: { running: boolean }; tasks: TaskCounts }>('/community/status'),
        apiFetch<Task[]>('/community/feed?limit=50'),
      ])
      setPersonas(personasRes)
      setArcs(arcsRes)
      setTaskCounts(statusRes.tasks)
      setSchedulerRunning(statusRes.scheduler.running)
      setFeed(feedRes)
    } catch (e) {
      toast.error('failed to load community data')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadData() }, [loadData])

  // Auto-refresh every 30s
  useEffect(() => {
    const interval = setInterval(loadData, 30000)
    return () => clearInterval(interval)
  }, [loadData])

  // Per-profile completed today count
  const todayStr = new Date().toISOString().slice(0, 10)
  const todayCounts: Record<string, number> = {}
  for (const t of feed) {
    if (t.completed_at?.startsWith(todayStr)) {
      todayCounts[t.profile_name] = (todayCounts[t.profile_name] || 0) + 1
    }
  }

  if (loading) {
    return (
      <Card><CardContent className="p-12 flex justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-pearl-accent" />
      </CardContent></Card>
    )
  }

  return (
    <div className="flex gap-4 h-[calc(100vh-200px)]">
      {/* Sidebar */}
      <div className="w-56 flex-shrink-0 flex flex-col gap-2">
        <div className="flex gap-1">
          <Button variant="outline" size="sm" className="flex-1 text-xs" onClick={() => setView('kb')}>
            <BookOpen className="h-3 w-3 mr-1" /> KB
          </Button>
          <Button variant="outline" size="sm" className="flex-1 text-xs" onClick={() => setView('config')}>
            <Settings className="h-3 w-3 mr-1" /> Config
          </Button>
        </div>

        <div className="text-xs text-pearl-secondary px-1 flex items-center gap-1">
          <div className={`w-1.5 h-1.5 rounded-full ${schedulerRunning ? 'bg-green-500' : 'bg-red-500'}`} />
          {schedulerRunning ? 'scheduler running' : 'scheduler stopped'}
        </div>

        <div className="flex flex-col gap-0.5 overflow-y-auto">
          {personas.map(p => {
            const done = todayCounts[p.profile_name] || 0
            const stage = arcMap[p.profile_name] || 'newcomer'
            const isActive = done > 0
            return (
              <button
                key={p.profile_name}
                onClick={() => { setSelectedProfile(p.profile_name); setView('profile') }}
                className={`flex items-center gap-2 px-2 py-1.5 rounded text-left text-xs hover:bg-pearl-card transition-colors ${
                  selectedProfile === p.profile_name && view === 'profile' ? 'bg-pearl-card border border-pearl-border' : ''
                }`}
              >
                <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${isActive ? 'bg-green-500' : 'bg-gray-300'}`} />
                <div className="flex-1 min-w-0">
                  <div className="font-medium truncate">{p.display_name || p.profile_name}</div>
                  <div className="text-pearl-tertiary text-[10px]">{stage}</div>
                </div>
                <span className="text-pearl-tertiary text-[10px]">{done}</span>
              </button>
            )
          })}
        </div>
      </div>

      {/* Main content */}
      <div className="flex-1 min-w-0">
        {view === 'feed' && <FeedView feed={feed} taskCounts={taskCounts} onRefresh={loadData} />}
        {view === 'profile' && selectedProfile && (
          <ProfileView
            profileName={selectedProfile}
            onBack={() => setView('feed')}
            onRefresh={loadData}
          />
        )}
        {view === 'kb' && <KBEditor onBack={() => setView('feed')} />}
        {view === 'config' && <PlannerConfig onBack={() => setView('feed')} />}
      </div>
    </div>
  )
}

// ── Feed View ──

function FeedView({ feed, taskCounts, onRefresh }: { feed: Task[]; taskCounts: TaskCounts; onRefresh: () => void }) {
  const [filter, setFilter] = useState<string>('all')
  const [generating, setGenerating] = useState(false)

  const filtered = filter === 'all' ? feed : feed.filter(t => t.action === filter)

  const handleGenerate = async () => {
    setGenerating(true)
    try {
      const result = await apiFetch<{ tasks_created: number; date: string }>('/community/planner/generate', { method: 'POST', body: JSON.stringify({}) })
      toast.success(`generated ${result.tasks_created} tasks for ${result.date}`)
      onRefresh()
    } catch (e: any) {
      toast.error(e.message || 'plan generation failed')
    } finally {
      setGenerating(false)
    }
  }

  return (
    <Card className="h-full flex flex-col">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">Activity Feed</CardTitle>
          <div className="flex items-center gap-2">
            <div className="flex gap-1 text-xs">
              <Badge variant="outline" className="text-green-600">{taskCounts.completed} done</Badge>
              <Badge variant="outline" className="text-blue-600">{taskCounts.running} running</Badge>
              <Badge variant="outline" className="text-gray-500">{taskCounts.pending} pending</Badge>
              {taskCounts.failed > 0 && <Badge variant="outline" className="text-red-600">{taskCounts.failed} failed</Badge>}
            </div>
            <Button variant="outline" size="sm" onClick={onRefresh}><RefreshCw className="h-3 w-3" /></Button>
            <Button variant="default" size="sm" onClick={handleGenerate} disabled={generating}>
              {generating ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : <Play className="h-3 w-3 mr-1" />}
              Generate Plan
            </Button>
          </div>
        </div>
        <div className="flex gap-1 mt-2">
          {['all', 'warmup_post', 'post_in_group', 'like_post', 'reply_to_post'].map(f => (
            <Button key={f} variant={filter === f ? 'default' : 'ghost'} size="sm" className="text-xs h-7"
              onClick={() => setFilter(f)}>
              {f === 'all' ? 'All' : f.replace(/_/g, ' ')}
            </Button>
          ))}
        </div>
      </CardHeader>
      <CardContent className="flex-1 overflow-y-auto space-y-1">
        {filtered.length === 0 ? (
          <div className="text-pearl-tertiary text-sm text-center py-8">no activity yet</div>
        ) : (
          filtered.map(task => <FeedItem key={task.id} task={task} />)
        )}
      </CardContent>
    </Card>
  )
}

function FeedItem({ task }: { task: Task }) {
  const icon = {
    warmup_post: <FileText className="h-3.5 w-3.5 text-blue-500" />,
    post_in_group: <MessageSquare className="h-3.5 w-3.5 text-purple-500" />,
    like_post: <Heart className="h-3.5 w-3.5 text-red-500" />,
    reply_to_post: <ThumbsUp className="h-3.5 w-3.5 text-green-500" />,
  }[task.action] || <FileText className="h-3.5 w-3.5" />

  const label = {
    warmup_post: 'posted on timeline',
    post_in_group: 'posted in group',
    like_post: 'liked post in group',
    reply_to_post: 'replied in group',
  }[task.action] || task.action

  const time = task.completed_at
    ? new Date(task.completed_at).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
    : ''

  return (
    <div className="flex gap-3 p-2 rounded hover:bg-pearl-card/50 border-b border-pearl-border/30">
      <div className="pt-0.5">{icon}</div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium">{task.profile_name}</span>
          <span className="text-[10px] text-pearl-tertiary">{time}</span>
        </div>
        <div className="text-xs text-pearl-secondary">{label}</div>
        {task.text && (
          <div className="text-xs text-pearl-primary mt-0.5 line-clamp-2">"{task.text}"</div>
        )}
        {task.image_prompt && (
          <div className="flex items-center gap-1 mt-0.5 text-[10px] text-pearl-tertiary">
            <Image className="h-2.5 w-2.5" /> with AI image
          </div>
        )}
      </div>
    </div>
  )
}

// ── Profile View ──

function ProfileView({ profileName, onBack, onRefresh }: {
  profileName: string; onBack: () => void; onRefresh: () => void
}) {
  const [data, setData] = useState<ProfileStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [advancing, setAdvancing] = useState(false)

  useEffect(() => {
    setLoading(true)
    apiFetch<ProfileStats>(`/community/profile/${encodeURIComponent(profileName)}/stats`)
      .then(setData)
      .catch(() => toast.error('failed to load profile'))
      .finally(() => setLoading(false))
  }, [profileName])

  const handleAdvance = async () => {
    setAdvancing(true)
    try {
      const res = await apiFetch<{ new_stage: string }>(`/community/arcs/${encodeURIComponent(profileName)}/advance`, { method: 'POST' })
      toast.success(`advanced to ${res.new_stage}`)
      onRefresh()
      // Reload profile
      const updated = await apiFetch<ProfileStats>(`/community/profile/${encodeURIComponent(profileName)}/stats`)
      setData(updated)
    } catch (e: any) {
      toast.error(e.message)
    } finally {
      setAdvancing(false)
    }
  }

  const handleRevert = async () => {
    try {
      const res = await apiFetch<{ new_stage: string }>(`/community/arcs/${encodeURIComponent(profileName)}/revert`, { method: 'POST' })
      toast.success(`reverted to ${res.new_stage}`)
      onRefresh()
      const updated = await apiFetch<ProfileStats>(`/community/profile/${encodeURIComponent(profileName)}/stats`)
      setData(updated)
    } catch (e: any) {
      toast.error(e.message)
    }
  }

  if (loading || !data) {
    return <Card className="h-full flex items-center justify-center"><Loader2 className="h-6 w-6 animate-spin" /></Card>
  }

  const { persona, arc, stats } = data
  const stageBadgeColor: Record<string, string> = {
    newcomer: 'bg-gray-100 text-gray-700',
    exploring: 'bg-blue-100 text-blue-700',
    trying_product: 'bg-yellow-100 text-yellow-700',
    seeing_results: 'bg-green-100 text-green-700',
    advocate: 'bg-purple-100 text-purple-700',
  }

  return (
    <Card className="h-full flex flex-col">
      <CardHeader className="pb-3">
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={onBack}><ChevronLeft className="h-4 w-4" /></Button>
          <div className="flex-1">
            <CardTitle className="text-base">{persona?.display_name || profileName}</CardTitle>
            <div className="text-xs text-pearl-secondary">
              {persona?.age && `${persona.age} | `}{persona?.persona_prompt?.slice(0, 60)}
            </div>
          </div>
          <Badge className={stageBadgeColor[arc?.current_stage || 'newcomer'] || ''}>
            {arc?.current_stage || 'newcomer'}
          </Badge>
          <Button variant="outline" size="sm" onClick={handleAdvance} disabled={advancing}>
            {advancing ? <Loader2 className="h-3 w-3 animate-spin" /> : <ChevronRight className="h-3 w-3" />}
            Advance
          </Button>
          <Button variant="ghost" size="sm" onClick={handleRevert}>Revert</Button>
        </div>
      </CardHeader>
      <CardContent className="flex-1 overflow-y-auto">
        {/* Stats Grid */}
        <div className="grid grid-cols-2 gap-4 mb-4">
          <div className="border border-pearl-border rounded p-3">
            <div className="text-xs text-pearl-tertiary mb-2 font-medium">THIS WEEK</div>
            <div className="grid grid-cols-2 gap-y-1 text-xs">
              <span className="text-pearl-secondary">Group posts:</span><span className="font-medium">{stats.this_week.group_posts}</span>
              <span className="text-pearl-secondary">Likes:</span><span className="font-medium">{stats.this_week.likes}</span>
              <span className="text-pearl-secondary">Replies:</span><span className="font-medium">{stats.this_week.replies}</span>
              <span className="text-pearl-secondary">Timeline:</span><span className="font-medium">{stats.this_week.timeline}</span>
            </div>
          </div>
          <div className="border border-pearl-border rounded p-3">
            <div className="text-xs text-pearl-tertiary mb-2 font-medium">ALL TIME</div>
            <div className="grid grid-cols-2 gap-y-1 text-xs">
              <span className="text-pearl-secondary">Total:</span><span className="font-medium">{stats.all_time.total_actions}</span>
              <span className="text-pearl-secondary">Success:</span><span className="font-medium">{stats.all_time.success_rate}%</span>
              <span className="text-pearl-secondary">Failed:</span><span className="font-medium">{stats.all_time.failed}</span>
            </div>
          </div>
        </div>

        {/* Recent Group Activity */}
        <div className="text-xs text-pearl-tertiary font-medium mb-2">RECENT GROUP ACTIVITY</div>
        <div className="space-y-1">
          {stats.recent_group_activity.length === 0 ? (
            <div className="text-xs text-pearl-tertiary text-center py-4">no group activity yet</div>
          ) : (
            stats.recent_group_activity.map((a, i) => (
              <div key={i} className="flex gap-2 p-2 border-b border-pearl-border/30 text-xs">
                <span className="text-pearl-tertiary w-16 flex-shrink-0">
                  {a.completed_at ? new Date(a.completed_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : ''}
                </span>
                <span className="text-pearl-secondary w-24 flex-shrink-0">{a.action.replace(/_/g, ' ')}</span>
                <span className="text-pearl-primary truncate">{a.text || '—'}</span>
              </div>
            ))
          )}
        </div>
      </CardContent>
    </Card>
  )
}

// ── Knowledge Base Editor ──

function KBEditor({ onBack }: { onBack: () => void }) {
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    apiFetch<{ content: string }>('/community/kb')
      .then(res => setContent(res.content))
      .catch(() => toast.error('failed to load knowledge base'))
      .finally(() => setLoading(false))
  }, [])

  const handleSave = async () => {
    setSaving(true)
    try {
      await apiFetch('/community/kb', {
        method: 'PUT',
        body: JSON.stringify({ content, updated_by: 'ui' }),
      })
      toast.success('knowledge base saved')
    } catch (e: any) {
      toast.error(e.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <Card className="h-full flex flex-col">
      <CardHeader className="pb-3">
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={onBack}><ChevronLeft className="h-4 w-4" /></Button>
          <CardTitle className="text-base flex-1">Knowledge Base</CardTitle>
          <Button variant="default" size="sm" onClick={handleSave} disabled={saving}>
            {saving ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : null}
            Save
          </Button>
        </div>
      </CardHeader>
      <CardContent className="flex-1 overflow-hidden">
        {loading ? (
          <div className="flex justify-center py-8"><Loader2 className="h-6 w-6 animate-spin" /></div>
        ) : (
          <Textarea
            value={content}
            onChange={e => setContent(e.target.value)}
            className="h-full min-h-[400px] font-mono text-xs resize-none"
            placeholder="## Brand Voice&#10;&#10;## Product Talking Points&#10;&#10;## Writing Rules"
          />
        )}
      </CardContent>
    </Card>
  )
}

// ── Planner Config ──

function PlannerConfig({ onBack }: { onBack: () => void }) {
  const [config, setConfig] = useState<Record<string, any>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    apiFetch<Record<string, any>>('/community/planner/config')
      .then(setConfig)
      .catch(() => toast.error('failed to load config'))
      .finally(() => setLoading(false))
  }, [])

  const handleSave = async () => {
    setSaving(true)
    try {
      await apiFetch('/community/planner/config', {
        method: 'PUT',
        body: JSON.stringify({ config }),
      })
      toast.success('planner config saved')
    } catch (e: any) {
      toast.error(e.message)
    } finally {
      setSaving(false)
    }
  }

  const updateRange = (key: string, idx: number, value: number) => {
    const range = [...(config[key] || [0, 0])]
    range[idx] = value
    setConfig({ ...config, [key]: range })
  }

  const updateValue = (key: string, value: any) => {
    setConfig({ ...config, [key]: value })
  }

  if (loading) {
    return <Card className="h-full flex items-center justify-center"><Loader2 className="h-6 w-6 animate-spin" /></Card>
  }

  return (
    <Card className="h-full flex flex-col">
      <CardHeader className="pb-3">
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={onBack}><ChevronLeft className="h-4 w-4" /></Button>
          <CardTitle className="text-base flex-1">Planner Config</CardTitle>
          <Button variant="default" size="sm" onClick={handleSave} disabled={saving}>
            {saving ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : null}
            Save
          </Button>
        </div>
      </CardHeader>
      <CardContent className="flex-1 overflow-y-auto space-y-4">
        <ConfigRange label="Timeline posts/day" configKey="timeline_posts_per_day" config={config} onChange={updateRange} />
        <ConfigRange label="Group posts/day" configKey="group_posts_per_day" config={config} onChange={updateRange} />
        <ConfigRange label="Group likes/day" configKey="group_likes_per_day" config={config} onChange={updateRange} />
        <ConfigRange label="Group replies/day" configKey="group_replies_per_day" config={config} onChange={updateRange} />

        <div className="border-t border-pearl-border pt-4">
          <ConfigSlider label="Image ratio" value={Math.round((config.image_ratio || 0.5) * 100)} suffix="%"
            onChange={v => updateValue('image_ratio', v / 100)} />
          <ConfigSlider label="Product mention ratio" value={Math.round((config.product_mention_ratio || 0.3) * 100)} suffix="%"
            onChange={v => updateValue('product_mention_ratio', v / 100)} />
        </div>

        <div className="border-t border-pearl-border pt-4">
          <div className="flex items-center justify-between text-xs mb-2">
            <span className="text-pearl-secondary">Schedule</span>
            <select
              value={config.planner_schedule || 'daily_midnight'}
              onChange={e => updateValue('planner_schedule', e.target.value)}
              className="border border-pearl-border rounded px-2 py-1 text-xs bg-white"
            >
              <option value="daily_midnight">Daily at midnight</option>
              <option value="weekly_sunday">Weekly on Sunday</option>
              <option value="manual">Manual only</option>
            </select>
          </div>
          <div className="flex items-center justify-between text-xs">
            <span className="text-pearl-secondary">Active hours</span>
            <span className="font-medium">{config.start_hour || 8}:00 – {config.end_hour || 22}:00 ET</span>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function ConfigRange({ label, configKey, config, onChange }: {
  label: string; configKey: string; config: Record<string, any>
  onChange: (key: string, idx: number, value: number) => void
}) {
  const range = config[configKey] || [0, 0]
  return (
    <div className="flex items-center justify-between text-xs">
      <span className="text-pearl-secondary">{label}</span>
      <div className="flex items-center gap-1">
        <input type="number" min={0} max={10} value={range[0]}
          onChange={e => onChange(configKey, 0, parseInt(e.target.value) || 0)}
          className="w-12 border border-pearl-border rounded px-1.5 py-0.5 text-center text-xs" />
        <span className="text-pearl-tertiary">–</span>
        <input type="number" min={0} max={10} value={range[1]}
          onChange={e => onChange(configKey, 1, parseInt(e.target.value) || 0)}
          className="w-12 border border-pearl-border rounded px-1.5 py-0.5 text-center text-xs" />
      </div>
    </div>
  )
}

function ConfigSlider({ label, value, suffix, onChange }: {
  label: string; value: number; suffix: string; onChange: (v: number) => void
}) {
  return (
    <div className="flex items-center justify-between text-xs mb-2">
      <span className="text-pearl-secondary">{label}</span>
      <div className="flex items-center gap-2">
        <input type="range" min={0} max={100} value={value}
          onChange={e => onChange(parseInt(e.target.value))}
          className="w-24 h-1" />
        <span className="w-10 text-right font-medium">{value}{suffix}</span>
      </div>
    </div>
  )
}
