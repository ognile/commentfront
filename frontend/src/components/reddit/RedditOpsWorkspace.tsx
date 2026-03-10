import { AlertCircle, CalendarDays, CheckCircle2, Clock3, Search } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'

import { RedditProgramSwitcher } from '@/components/reddit/RedditProgramSwitcher'
import { RedditProfilesDayTable } from '@/components/reddit/RedditProfilesDayTable'
import type { RedditOperatorViewResponse, RedditProgramListItem } from '@/components/reddit/types'

function statusTone(status: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (status === 'completed') return 'default'
  if (status === 'active') return 'secondary'
  if (status === 'exhausted' || status === 'cancelled') return 'destructive'
  return 'outline'
}

function totalCount(counts: Record<string, number>): number {
  return Object.values(counts || {}).reduce((sum, value) => sum + value, 0)
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
        <CardContent className="space-y-4 p-4">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <div className="space-y-2">
              <div className="text-xs font-semibold uppercase tracking-[0.18em] text-[#8a7f6a]">program controls</div>
              <div className="flex flex-wrap items-center gap-2">
                {program ? <Badge variant={statusTone(program.status)} className="capitalize">{program.status}</Badge> : null}
                <Badge variant="outline">remaining {totalRemaining}</Badge>
                <Badge variant={totalFailures > 0 ? 'destructive' : 'outline'}>failures {totalFailures}</Badge>
                <Badge variant={totalRequiredProof > 0 && totalConfirmedProof === totalRequiredProof ? 'default' : 'secondary'}>
                  confirmed proof {totalConfirmedProof}/{totalRequiredProof}
                </Badge>
                {program?.next_run_at ? <Badge variant="outline">next run {program.next_run_at}</Badge> : null}
              </div>
            </div>

            <div className="grid gap-3 sm:grid-cols-[auto_auto_minmax(220px,1fr)]">
              <div className="space-y-2">
                <div className="text-xs font-semibold uppercase tracking-[0.14em] text-[#8a7f6a]">day</div>
                <div className="flex flex-wrap gap-2">
                  {(program?.available_days || []).map((day) => (
                    <Button
                      key={day}
                      variant={day === selectedLocalDate ? 'default' : 'outline'}
                      size="sm"
                      onClick={() => onSelectLocalDate(day)}
                      className={day === selectedLocalDate ? '' : 'border-[#d8d3c5] bg-white text-[#5c564a]'}
                    >
                      <CalendarDays className="mr-1 h-3.5 w-3.5" />
                      {day}
                    </Button>
                  ))}
                </div>
              </div>
              <div className="space-y-2">
                <div className="text-xs font-semibold uppercase tracking-[0.14em] text-[#8a7f6a]">profiles</div>
                <div className="flex items-center gap-2 rounded-xl border border-[#d8d3c5] bg-white px-3 py-2 text-sm text-[#5c564a]">
                  <Clock3 className="h-4 w-4" />
                  {filteredProfiles.length} visible
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
            </div>
          </div>

          <div className="grid gap-3 xl:grid-cols-4">
            <div className="rounded-2xl border border-[#e6decd] bg-white p-4">
              <div className="text-xs font-semibold uppercase tracking-[0.14em] text-[#8a7f6a]">state</div>
              <div className="mt-2 flex items-center gap-2 text-lg font-semibold text-[#1f1f1a]">
                <CheckCircle2 className="h-5 w-5 text-[#2f6f3e]" />
                {program?.status || 'no program'}
              </div>
            </div>
            <div className="rounded-2xl border border-[#e6decd] bg-white p-4">
              <div className="text-xs font-semibold uppercase tracking-[0.14em] text-[#8a7f6a]">remaining contract</div>
              <div className="mt-2 text-2xl font-semibold text-[#1f1f1a]">{totalRemaining}</div>
            </div>
            <div className="rounded-2xl border border-[#e6decd] bg-white p-4">
              <div className="text-xs font-semibold uppercase tracking-[0.14em] text-[#8a7f6a]">proof coverage</div>
              <div className="mt-2 text-2xl font-semibold text-[#1f1f1a]">{totalConfirmedProof}/{totalRequiredProof}</div>
            </div>
            <div className="rounded-2xl border border-[#e6decd] bg-white p-4">
              <div className="text-xs font-semibold uppercase tracking-[0.14em] text-[#8a7f6a]">failure count</div>
              <div className="mt-2 flex items-center gap-2 text-2xl font-semibold text-[#1f1f1a]">
                <AlertCircle className={`h-5 w-5 ${totalFailures > 0 ? 'text-[#b42318]' : 'text-[#8a7f6a]'}`} />
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
        <RedditProfilesDayTable
          profiles={filteredProfiles}
          actionRows={filteredActionRows}
          actionKeys={program.available_actions}
          expandedProfile={expandedProfile}
          onToggleProfile={onToggleProfile}
        />
      ) : (
        <Card className="border-[#d8d3c5] bg-white shadow-sm">
          <CardContent className="p-8 text-center text-[#6e6759]">no reddit program data yet.</CardContent>
        </Card>
      )}
    </div>
  )
}
