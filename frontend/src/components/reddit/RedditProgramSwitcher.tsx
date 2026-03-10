import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

import type { RedditProgramListItem, RedditOperatorProgramSummary } from '@/components/reddit/types'

function metadataLabel(program: RedditProgramListItem): string | null {
  const metadata = program.spec?.metadata || {}
  const proofGateProgramId = typeof metadata.proof_gate_program_id === 'string' ? metadata.proof_gate_program_id : null
  const supersedesProgramId = typeof metadata.supersedes_program_id === 'string' ? metadata.supersedes_program_id : null
  if (proofGateProgramId) return `proof ${proofGateProgramId}`
  if (supersedesProgramId) return `supersedes ${supersedesProgramId}`
  return null
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
  return (
    <Card className="border-[#d8d3c5] bg-[linear-gradient(180deg,rgba(255,255,255,0.98),rgba(245,241,232,0.98))] shadow-sm">
      <CardContent className="flex flex-col gap-4 p-4 lg:flex-row lg:items-center lg:justify-between">
        <div className="space-y-1">
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
            defaulting to the current rollout and keeping proof packets one switch away.
          </div>
        </div>

        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <Select value={selectedProgramId} onValueChange={onSelectProgram}>
            <SelectTrigger className="min-w-[320px] border-[#d8d3c5] bg-white">
              <SelectValue placeholder="choose reddit program" />
            </SelectTrigger>
            <SelectContent>
              {programs.map((program) => {
                const note = metadataLabel(program)
                return (
                  <SelectItem key={program.id} value={program.id}>
                    {program.id} · {program.status}{note ? ` · ${note}` : ''}
                  </SelectItem>
                )
              })}
            </SelectContent>
          </Select>
          <Button variant="outline" onClick={onRefresh} disabled={loading} className="border-[#d8d3c5] bg-white">
            {loading ? 'refreshing...' : 'refresh'}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
