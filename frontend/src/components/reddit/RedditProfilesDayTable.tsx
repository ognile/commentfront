import { Fragment } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

import { RedditProfileActionRows } from '@/components/reddit/RedditProfileActionRows'
import type { RedditOperatorActionRow, RedditOperatorProfileRow } from '@/components/reddit/types'

function actionLabel(action: string): string {
  return action.replace(/_/g, ' ')
}

function countTone(value: number): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (value > 0) return 'secondary'
  return 'outline'
}

function proofTone(value: number, total: number): 'default' | 'secondary' | 'outline' {
  if (total > 0 && value === total) return 'default'
  if (value > 0) return 'secondary'
  return 'outline'
}

function completionRatio(completed: number, total: number): string {
  if (total <= 0) return '0%'
  return `${Math.round((completed / total) * 100)}%`
}

function renderActionMix(counts: Record<string, number>, actions: string[]) {
  return (
    <div className="flex flex-wrap gap-2">
      {actions.map((action) => {
        const value = counts[action] || 0
        if (value === 0) return null
        return (
          <Badge key={action} variant="outline" className="border-[#d8d3c5] bg-white text-[#5c564a]">
            {actionLabel(action)} {value}
          </Badge>
        )
      })}
    </div>
  )
}

interface RedditProfilesDayTableProps {
  profiles: RedditOperatorProfileRow[]
  actionRows: RedditOperatorActionRow[]
  actionKeys: string[]
  expandedProfile: string | null
  onToggleProfile: (profileName: string) => void
}

export function RedditProfilesDayTable({
  profiles,
  actionRows,
  actionKeys,
  expandedProfile,
  onToggleProfile,
}: RedditProfilesDayTableProps) {
  return (
    <div className="rounded-2xl border border-[#d8d3c5] bg-white shadow-sm">
      <Table className="table-fixed">
        <TableHeader className="sticky top-0 z-10 bg-[#f7f2e8]">
          <TableRow className="border-[#e3dccd] hover:bg-transparent">
            <TableHead className="h-10 w-[190px] px-3 text-xs uppercase tracking-[0.12em] text-[#8a7f6a]">profile</TableHead>
            <TableHead className="h-10 px-3 text-xs uppercase tracking-[0.12em] text-[#8a7f6a]">required mix</TableHead>
            <TableHead className="h-10 w-[220px] px-3 text-xs uppercase tracking-[0.12em] text-[#8a7f6a]">progress</TableHead>
            <TableHead className="h-10 w-[260px] px-3 text-xs uppercase tracking-[0.12em] text-[#8a7f6a]">proof</TableHead>
            <TableHead className="h-10 w-[110px] px-3 text-right text-xs uppercase tracking-[0.12em] text-[#8a7f6a]">details</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {profiles.map((profile) => {
            const rows = actionRows.filter((row) => row.profile_name === profile.profile_name)
            const open = expandedProfile === profile.profile_name
            return (
              <Fragment key={profile.profile_name}>
                <TableRow key={profile.profile_name} className="border-[#eee6d6] bg-white hover:bg-[#fcf8f0]">
                  <TableCell className="px-3 py-3">
                    <div className="space-y-1">
                      <div className="font-semibold text-[#1f1f1a]">{profile.profile_name}</div>
                      <div className="text-xs text-[#7a7365]">{profile.planned_total} required actions</div>
                    </div>
                  </TableCell>
                  <TableCell className="px-3 py-3">{renderActionMix(profile.planned, actionKeys)}</TableCell>
                  <TableCell className="px-3 py-3">
                    <div className="space-y-2">
                      <div className="flex flex-wrap gap-2">
                        <Badge variant={countTone(profile.completed_total)}>done {profile.completed_total}</Badge>
                        <Badge variant={countTone(profile.pending_total)}>left {profile.pending_total}</Badge>
                        <Badge variant={profile.blocked_total > 0 ? 'destructive' : 'outline'}>
                          blocked {profile.blocked_total}
                        </Badge>
                      </div>
                      <div className="text-xs text-[#7a7365]">
                        {completionRatio(profile.completed_total, profile.planned_total)} complete
                      </div>
                    </div>
                  </TableCell>
                  <TableCell className="px-3 py-3">
                    <div className="flex flex-wrap gap-1.5">
                      <Badge variant={proofTone(profile.proof_coverage.with_url, profile.proof_coverage.required_actions)}>
                        url {profile.proof_coverage.with_url}/{profile.proof_coverage.required_actions}
                      </Badge>
                      <Badge variant={proofTone(profile.proof_coverage.with_screenshot, profile.proof_coverage.required_actions)}>
                        shot {profile.proof_coverage.with_screenshot}/{profile.proof_coverage.required_actions}
                      </Badge>
                      <Badge variant={proofTone(profile.proof_coverage.with_attempt, profile.proof_coverage.required_actions)}>
                        attempt {profile.proof_coverage.with_attempt}/{profile.proof_coverage.required_actions}
                      </Badge>
                      <Badge variant={proofTone(profile.proof_coverage.success_confirmed, profile.proof_coverage.required_actions)}>
                        confirmed {profile.proof_coverage.success_confirmed}/{profile.proof_coverage.required_actions}
                      </Badge>
                    </div>
                  </TableCell>
                  <TableCell className="px-3 py-3 text-right">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => onToggleProfile(profile.profile_name)}
                      className="border-[#d8d3c5] bg-white text-[#6b6353]"
                    >
                      {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                      {open ? 'hide' : 'open'}
                    </Button>
                  </TableCell>
                </TableRow>
                {open ? (
                  <TableRow className="border-[#eee6d6] bg-[#faf6ee] hover:bg-[#faf6ee]">
                    <TableCell colSpan={5} className="p-3">
                      <RedditProfileActionRows rows={rows} />
                    </TableCell>
                  </TableRow>
                ) : null}
              </Fragment>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}
