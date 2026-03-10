import { AlertCircle, CalendarDays, CheckCircle2, Clock3, Search } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'

import { RedditProgramSwitcher } from '@/components/reddit/RedditProgramSwitcher'
import { RedditProfilesDayTable } from '@/components/reddit/RedditProfilesDayTable'
import type { RedditOperatorViewResponse, RedditProgramListItem } from '@/components/reddit/types'

function formatDayLabel(day: string): { short: string; detail: string } {
  const parsed = new Date(`${day}T00:00:00`)
  if (Number.isNaN(parsed.getTime())) {
    return { short: day, detail: '' }
  }
  return {
    short: new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' }).format(parsed),
    detail: new Intl.DateTimeFormat('en-US', { weekday: 'short' }).format(parsed),
  }
}

function statusTone(status: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (status === 'completed') return 'default'
  if (status === 'active') return 'secondary'
  if (status === 'exhausted' || status === 'cancelled') return 'destructive'
  return 'outline'
}

function totalCount(counts: Record<string, number>): number {
  return Object.values(counts || {}).reduce((sum, value) => sum + value, 0)
}

function formatNextRun(value: string | null | undefined): string | null {
  if (!value) return null
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(parsed)
}

interface RedditOpsWorkspaceProps {
  programs: RedditProgramListItem[]
  selectedProgramId: string
  selectedLocalDate: string
  profileQuery: string
  operatorView: RedditOperatorViewResponse | null
  loadingPrograms: boolean
  loadingOperatorView: boolean
  expandedProfile: string | null
  onSelectProgram: (programId: string) => void
  onSelectLocalDate: (localDate: string) => void
  onProfileQueryChange: (value: string) => void
  onToggleProfile: (profileName: string) => void
  onRefresh: () => void
}

export function RedditOpsWorkspace({
  programs,
  selectedProgramId,
  selectedLocalDate,
  profileQuery,
  operatorView,
  loadingPrograms,
  loadingOperatorView,
  expandedProfile,
  onSelectProgram,
  onSelectLocalDate,
  onProfileQueryChange,
  onToggleProfile,
  onRefresh,
}: RedditOpsWorkspaceProps) {
  const program = operatorView?.program || null
  const normalizedQuery = profileQuery.trim().toLowerCase()
  const filteredProfiles = (operatorView?.profiles_by_day || []).filter((row) =>
    !normalizedQuery || row.profile_name.toLowerCase().includes(normalizedQuery),
  )
  const filteredProfileNames = new Set(filteredProfiles.map((row) => row.profile_name))
  const filteredActionRows = (operatorView?.action_rows || []).filter((row) => filteredProfileNames.has(row.profile_name))

  const totalFailures = program ? totalCount(program.failure_summary.by_action || {}) : 0
  const totalRemaining = program ? totalCount(program.remaining_contract || {}) : 0
  const totalRequiredProof = filteredProfiles.reduce((sum, row) => sum + row.proof_coverage.required_actions, 0)
  const totalConfirmedProof = filteredProfiles.reduce((sum, row) => sum + row.proof_coverage.success_confirmed, 0)
  const selectedDayIndex = Math.max(0, (program?.available_days || []).findIndex((day) => day === selectedLocalDate))
  const nextRunLabel = formatNextRun(program?.next_run_at)

  return (
    <div className="space-y-5">
      <RedditProgramSwitcher
        programs={programs}
        selectedProgramId={selectedProgramId}
        onSelectProgram={onSelectProgram}
        onRefresh={onRefresh}
        loading={loadingPrograms || loadingOperatorView}
        programSummary={program}
      />

      <Card className="border-[#d8d3c5] bg-[linear-gradient(180deg,rgba(250,246,238,0.98),rgba(255,255,255,0.98))] shadow-sm">
        <CardContent className="space-y-3 p-4">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
            <div className="space-y-2">
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-[#8a7f6a]">monitor controls</div>
              <div className="flex flex-wrap items-center gap-2">
                {program ? <Badge variant={statusTone(program.status)} className="capitalize">{program.status}</Badge> : null}
                <Badge variant="outline">remaining {totalRemaining}</Badge>
                <Badge variant={totalFailures > 0 ? 'destructive' : 'outline'}>failures {totalFailures}</Badge>
                <Badge variant={totalRequiredProof > 0 && totalConfirmedProof === totalRequiredProof ? 'default' : 'secondary'}>
                  confirmed proof {totalConfirmedProof}/{totalRequiredProof}
                </Badge>
                {nextRunLabel ? <Badge variant="outline">next {nextRunLabel}</Badge> : null}
              </div>
              <div className="text-sm text-[#6e6759]">
                selected day shows the exact per-profile packet for that date, with proof rows hidden until you open a profile.
              </div>
            </div>

            <div className="grid gap-3 xl:min-w-[560px] xl:grid-cols-[minmax(0,1fr)_220px]">
              <div className="space-y-2 xl:col-span-2">
                <div className="text-xs font-semibold uppercase tracking-[0.14em] text-[#8a7f6a]">day</div>
                <div className="overflow-x-auto pb-1">
                  <div className="flex min-w-max gap-2">
                    {(program?.available_days || []).map((day, index) => {
                      const label = formatDayLabel(day)
                      const active = day === selectedLocalDate
                      return (
                        <button
                          key={day}
                          type="button"
                          onClick={() => onSelectLocalDate(day)}
                          className={[
                            'flex min-w-[116px] items-center gap-3 rounded-2xl border px-3 py-2 text-left transition',
                            active
                              ? 'border-[#1f1f1a] bg-[#1f1f1a] text-white shadow-sm'
                              : 'border-[#d8d3c5] bg-white text-[#5c564a] hover:border-[#b7ad99]',
                          ].join(' ')}
                        >
                          <div className={[
                            'flex h-8 w-8 items-center justify-center rounded-full text-xs font-semibold',
                            active ? 'bg-white/15 text-white' : 'bg-[#f4efe4] text-[#6e6759]',
                          ].join(' ')}>
                            {index + 1}
                          </div>
                          <div className="min-w-0">
                            <div className="text-[11px] uppercase tracking-[0.14em] opacity-70">day {index + 1}</div>
                            <div className="text-sm font-semibold">{label.short}</div>
                            <div className="text-xs opacity-70">{label.detail}</div>
                          </div>
                        </button>
                      )
                    })}
                  </div>
                </div>
              </div>
              <div className="space-y-2">
                <div className="text-xs font-semibold uppercase tracking-[0.14em] text-[#8a7f6a]">search</div>
                <div className="relative">
                  <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[#8a7f6a]" />
                  <Input
                    value={profileQuery}
                    onChange={(event) => onProfileQueryChange(event.target.value)}
                    placeholder="filter profiles"
                    className="border-[#d8d3c5] bg-white pl-9"
                  />
                </div>
              </div>
              <div className="grid gap-2 sm:grid-cols-3 xl:col-span-2">
                <div className="flex items-center gap-2 rounded-xl border border-[#d8d3c5] bg-white px-3 py-2 text-sm text-[#5c564a]">
                  <Clock3 className="h-4 w-4" />
                  {filteredProfiles.length} profiles visible
                </div>
                <div className="flex items-center gap-2 rounded-xl border border-[#d8d3c5] bg-white px-3 py-2 text-sm text-[#5c564a]">
                  <CalendarDays className="h-4 w-4" />
                  day {selectedDayIndex + 1} of {(program?.available_days || []).length}
                </div>
                <div className="flex items-center gap-2 rounded-xl border border-[#d8d3c5] bg-white px-3 py-2 text-sm text-[#5c564a]">
                  <CheckCircle2 className="h-4 w-4" />
                  {totalConfirmedProof}/{totalRequiredProof || 0} proof confirmed
                </div>
              </div>
            </div>
          </div>

          <div className="grid gap-2 lg:grid-cols-4">
            <div className="rounded-2xl border border-[#e6decd] bg-white px-3 py-3">
              <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#8a7f6a]">state</div>
              <div className="mt-1 flex items-center gap-2 text-base font-semibold text-[#1f1f1a]">
                <CheckCircle2 className="h-4 w-4 text-[#2f6f3e]" />
                {program?.status || 'no program'}
              </div>
            </div>
            <div className="rounded-2xl border border-[#e6decd] bg-white px-3 py-3">
              <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#8a7f6a]">remaining</div>
              <div className="mt-1 text-base font-semibold text-[#1f1f1a]">{totalRemaining}</div>
            </div>
            <div className="rounded-2xl border border-[#e6decd] bg-white px-3 py-3">
              <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#8a7f6a]">proof</div>
              <div className="mt-1 text-base font-semibold text-[#1f1f1a]">{totalConfirmedProof}/{totalRequiredProof}</div>
            </div>
            <div className="rounded-2xl border border-[#e6decd] bg-white px-3 py-3">
              <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-[#8a7f6a]">failures</div>
              <div className="mt-1 flex items-center gap-2 text-base font-semibold text-[#1f1f1a]">
                <AlertCircle className={`h-4 w-4 ${totalFailures > 0 ? 'text-[#b42318]' : 'text-[#8a7f6a]'}`} />
                {totalFailures}
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {loadingOperatorView ? (
        <Card className="border-[#d8d3c5] bg-white shadow-sm">
          <CardContent className="p-8 text-center text-[#6e6759]">loading reddit operator view...</CardContent>
        </Card>
      ) : filteredProfiles.length > 0 && program ? (
        <div className="space-y-3">
          <div className="flex items-end justify-between">
            <div>
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-[#8a7f6a]">profiles by day</div>
              <div className="text-sm text-[#6e6759]">
                one row per profile, with exact proof drilldown behind each row.
              </div>
            </div>
          </div>
          <RedditProfilesDayTable
            profiles={filteredProfiles}
            actionRows={filteredActionRows}
            actionKeys={program.available_actions}
            expandedProfile={expandedProfile}
            onToggleProfile={onToggleProfile}
          />
        </div>
      ) : (
        <Card className="border-[#d8d3c5] bg-white shadow-sm">
          <CardContent className="p-8 text-center text-[#6e6759]">no reddit program data yet.</CardContent>
        </Card>
      )}
    </div>
  )
}
