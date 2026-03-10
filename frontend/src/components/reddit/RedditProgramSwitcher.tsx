import { Layers3 } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

import type { RedditProgramListItem, RedditOperatorProgramSummary } from '@/components/reddit/types'

type RedditProgramMetadata = NonNullable<NonNullable<RedditProgramListItem['spec']>['metadata']>

function metadata(program: RedditProgramListItem): RedditProgramMetadata {
  return program.spec?.metadata || {}
}

function metadataLabel(program: RedditProgramListItem): string | null {
  const details = metadata(program)
  const proofGateProgramId = typeof details.proof_gate_program_id === 'string' ? details.proof_gate_program_id : null
  const supersedesProgramId = typeof details.supersedes_program_id === 'string' ? details.supersedes_program_id : null
  if (proofGateProgramId) return `proof ${proofGateProgramId}`
  if (supersedesProgramId) return `supersedes ${supersedesProgramId}`
  return null
}

function isCurrentRollout(program: RedditProgramListItem): boolean {
  const details = metadata(program)
  return program.status === 'active' && details.mode === 'production' && typeof details.proof_gate_program_id === 'string'
}

function isProofPacket(program: RedditProgramListItem): boolean {
  return metadata(program).proof_gate === 'single_profile_latest_runtime'
}

function isArchived(program: RedditProgramListItem): boolean {
  return ['completed', 'cancelled', 'exhausted', 'paused'].includes(program.status)
}

function statusTone(status: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (status === 'completed') return 'default'
  if (status === 'active') return 'secondary'
  if (status === 'exhausted' || status === 'cancelled') return 'destructive'
  return 'outline'
}

interface RedditProgramSwitcherProps {
  programs: RedditProgramListItem[]
  selectedProgramId: string
  onSelectProgram: (programId: string) => void
  onRefresh: () => void
  loading: boolean
  programSummary: RedditOperatorProgramSummary | null
}

export function RedditProgramSwitcher({
  programs,
  selectedProgramId,
  onSelectProgram,
  onRefresh,
  loading,
  programSummary,
}: RedditProgramSwitcherProps) {
  const currentRollout = programs.find(isCurrentRollout) || null
  const proofPackets = programs.filter(isProofPacket)
  const proofPacket = proofPackets[0] || null
  const otherActivePrograms = programs.filter((program) => program.status === 'active' && !isCurrentRollout(program))
  const archivedPrograms = programs.filter((program) => isArchived(program) && !isProofPacket(program))

  return (
    <Card className="border-[#d8d3c5] bg-[linear-gradient(180deg,rgba(255,255,255,0.98),rgba(245,241,232,0.98))] shadow-sm">
      <CardContent className="flex flex-col gap-4 p-4 lg:flex-row lg:items-center lg:justify-between">
        <div className="space-y-2">
          <div className="text-xs font-semibold uppercase tracking-[0.18em] text-[#8a7f6a]">active monitor</div>
          <div className="flex flex-wrap items-center gap-2">
            <div className="text-lg font-semibold text-[#1f1f1a]">
              {programSummary ? programSummary.id : 'select a reddit program'}
            </div>
            {programSummary ? (
              <Badge variant={statusTone(programSummary.status)} className="capitalize">
                {programSummary.status}
              </Badge>
            ) : null}
          </div>
          <div className="text-sm text-[#6e6759]">
            rollout first, proof packet second, one-off checks and old runs pushed into history.
          </div>
          <div className="flex flex-wrap gap-2 text-xs text-[#6e6759]">
            {currentRollout ? <Badge variant="secondary">1 rollout</Badge> : null}
            {proofPacket ? <Badge variant="outline">1 proof packet</Badge> : null}
            {otherActivePrograms.length > 0 ? (
              <Badge variant="destructive">{otherActivePrograms.length} other active</Badge>
            ) : null}
            {archivedPrograms.length > 0 ? (
              <Badge variant="outline">{archivedPrograms.length} archived</Badge>
            ) : null}
          </div>
        </div>

        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <Select value={selectedProgramId} onValueChange={onSelectProgram}>
            <SelectTrigger className="min-w-[320px] border-[#d8d3c5] bg-white">
              <SelectValue placeholder="choose reddit program" />
            </SelectTrigger>
            <SelectContent>
              {currentRollout ? (
                <SelectGroup>
                  <SelectLabel>current rollout</SelectLabel>
                  <SelectItem value={currentRollout.id}>
                    {currentRollout.id} · {currentRollout.status}
                  </SelectItem>
                </SelectGroup>
              ) : null}
              {proofPacket ? (
                <>
                  <SelectSeparator />
                  <SelectGroup>
                    <SelectLabel>proof packet</SelectLabel>
                    <SelectItem value={proofPacket.id}>
                      {proofPacket.id} · {proofPacket.status}
                    </SelectItem>
                  </SelectGroup>
                </>
              ) : null}
              {otherActivePrograms.length > 0 ? (
                <>
                  <SelectSeparator />
                  <SelectGroup>
                    <SelectLabel>other active programs</SelectLabel>
                    {otherActivePrograms.map((program) => (
                      <SelectItem key={program.id} value={program.id}>
                        {program.id} · {program.status}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </>
              ) : null}
              {archivedPrograms.length > 0 ? (
                <>
                  <SelectSeparator />
                  <SelectGroup>
                    <SelectLabel>history</SelectLabel>
                    {archivedPrograms.map((program) => {
                      const note = metadataLabel(program)
                      return (
                        <SelectItem key={program.id} value={program.id}>
                          {program.id} · {program.status}{note ? ` · ${note}` : ''}
                        </SelectItem>
                      )
                    })}
                  </SelectGroup>
                </>
              ) : null}
            </SelectContent>
          </Select>
          <Button variant="outline" onClick={onRefresh} disabled={loading} className="border-[#d8d3c5] bg-white">
            {loading ? 'refreshing...' : 'refresh'}
          </Button>
        </div>

        {otherActivePrograms.length > 0 ? (
          <div className="rounded-2xl border border-[#ead2c9] bg-[#fff7f3] px-3 py-2 text-sm text-[#8f3f28] lg:max-w-[360px]">
            <div className="flex items-center gap-2 font-medium">
              <Layers3 className="h-4 w-4" />
              extra active programs exist
            </div>
            <div className="mt-1 text-xs leading-5">
              these look like older checks or partial runs. the monitor is pinned to the current rollout by default so they stop cluttering the page.
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  )
}
