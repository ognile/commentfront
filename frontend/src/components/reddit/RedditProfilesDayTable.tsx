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
      <Table>
        <TableHeader className="sticky top-0 z-10 bg-[#f7f2e8]">
          <TableRow className="border-[#e3dccd] hover:bg-transparent">
            <TableHead className="w-[180px]">profile</TableHead>
            <TableHead className="min-w-[320px]">planned actions</TableHead>
            <TableHead className="w-[110px]">completed</TableHead>
            <TableHead className="w-[110px]">pending</TableHead>
            <TableHead className="w-[110px]">blocked</TableHead>
            <TableHead className="w-[280px]">proof coverage</TableHead>
            <TableHead className="w-[110px] text-right">details</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {profiles.map((profile) => {
            const rows = actionRows.filter((row) => row.profile_name === profile.profile_name)
            const open = expandedProfile === profile.profile_name
            return (
              <Fragment key={profile.profile_name}>
                <TableRow key={profile.profile_name} className="border-[#eee6d6] bg-white hover:bg-[#fcf8f0]">
                  <TableCell>
                    <div className="space-y-1">
                      <div className="font-semibold text-[#1f1f1a]">{profile.profile_name}</div>
                      <div className="text-xs text-[#7a7365]">{profile.planned_total} required actions</div>
                    </div>
                  </TableCell>
                  <TableCell>{renderActionMix(profile.planned, actionKeys)}</TableCell>
                  <TableCell>
                    <Badge variant={countTone(profile.completed_total)}>{profile.completed_total}</Badge>
                  </TableCell>
                  <TableCell>
                    <Badge variant={countTone(profile.pending_total)}>{profile.pending_total}</Badge>
                  </TableCell>
                  <TableCell>
                    <Badge variant={profile.blocked_total > 0 ? 'destructive' : 'outline'}>{profile.blocked_total}</Badge>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-2">
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
                  <TableCell className="text-right">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => onToggleProfile(profile.profile_name)}
                      className="text-[#6b6353]"
                    >
                      {open ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                      {open ? 'hide' : 'open'}
                    </Button>
                  </TableCell>
                </TableRow>
                {open ? (
                  <TableRow className="border-[#eee6d6] bg-[#faf6ee] hover:bg-[#faf6ee]">
                    <TableCell colSpan={7} className="p-4">
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
