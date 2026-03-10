import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { resolveApiUrl } from '@/lib/api'

import type { RedditOperatorActionRow } from '@/components/reddit/types'

function verdictTone(verdict: string | null | undefined): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (verdict === 'success_confirmed') return 'default'
  if (verdict === 'failed_confirmed' || verdict === 'infra_failure') return 'destructive'
  if (verdict) return 'secondary'
  return 'outline'
}

function statusTone(status: string): 'default' | 'secondary' | 'destructive' | 'outline' {
  if (status === 'completed') return 'default'
  if (status === 'blocked' || status === 'exhausted' || status === 'cancelled') return 'destructive'
  if (status === 'running') return 'secondary'
  return 'outline'
}

function proofBadgeTone(ok: boolean): 'default' | 'secondary' | 'outline' {
  return ok ? 'default' : 'outline'
}

interface RedditProfileActionRowsProps {
  rows: RedditOperatorActionRow[]
}

export function RedditProfileActionRows({ rows }: RedditProfileActionRowsProps) {
  return (
    <div className="rounded-2xl border border-[#ddd5c5] bg-[#fffdf8]">
      <Table>
        <TableHeader>
          <TableRow className="border-[#e8e1d2] hover:bg-transparent">
            <TableHead className="w-[160px]">action</TableHead>
            <TableHead>target</TableHead>
            <TableHead className="w-[220px]">proof</TableHead>
            <TableHead className="w-[170px]">status</TableHead>
            <TableHead className="w-[180px]">attempt</TableHead>
            <TableHead className="w-[160px]">history</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => {
            const screenshotUrl = resolveApiUrl(row.screenshot_artifact_url)
            const attemptUrl = row.attempt_id ? resolveApiUrl(`/forensics/attempts/${row.attempt_id}`) : null
            return (
              <TableRow key={row.work_item_id} className="border-[#eee6d6] align-top">
                <TableCell className="space-y-1">
                  <div className="font-medium text-[#24231d]">{row.action}</div>
                  <div className="text-xs text-[#7a7365]">{row.subreddit || 'no subreddit'}</div>
                </TableCell>
                <TableCell className="space-y-2">
                  {row.target_ref ? (
                    <a
                      href={row.target_ref}
                      target="_blank"
                      rel="noreferrer"
                      className="block break-all text-sm text-[#155e75] underline-offset-2 hover:underline"
                    >
                      {row.target_ref}
                    </a>
                  ) : (
                    <span className="text-sm text-[#9a9385]">missing target url</span>
                  )}
                  {row.error ? <div className="text-xs text-[#b42318]">{row.error}</div> : null}
                </TableCell>
                <TableCell>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant={proofBadgeTone(row.proof_flags.has_url)}>url</Badge>
                    <Badge variant={proofBadgeTone(row.proof_flags.has_screenshot)}>shot</Badge>
                    <Badge variant={proofBadgeTone(row.proof_flags.has_attempt)}>attempt</Badge>
                    <Badge variant={proofBadgeTone(row.proof_flags.success_confirmed)}>confirmed</Badge>
                    {screenshotUrl ? (
                      <a href={screenshotUrl} target="_blank" rel="noreferrer" className="ml-1">
                        <img
                          src={screenshotUrl}
                          alt={`${row.action} proof`}
                          className="h-12 w-10 rounded-md border border-[#d8d3c5] object-cover shadow-sm"
                        />
                      </a>
                    ) : null}
                  </div>
                </TableCell>
                <TableCell className="space-y-2">
                  <Badge variant={statusTone(row.status)} className="capitalize">{row.status}</Badge>
                  <div>
                    <Badge variant={verdictTone(row.final_verdict)}>{row.final_verdict || 'no verdict yet'}</Badge>
                  </div>
                </TableCell>
                <TableCell className="space-y-2">
                  {attemptUrl ? (
                    <a
                      href={attemptUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="block break-all text-sm text-[#155e75] underline-offset-2 hover:underline"
                    >
                      {row.attempt_id}
                    </a>
                  ) : (
                    <span className="text-sm text-[#9a9385]">no attempt</span>
                  )}
                  <div className="text-xs text-[#7a7365]">{row.attempts} total tries</div>
                </TableCell>
                <TableCell className="space-y-2">
                  {row.attempt_history.length > 1 ? (
                    row.attempt_history.map((entry) => (
                      <div key={entry.attempt_id} className="rounded-lg border border-[#e5dece] bg-white px-2 py-1">
                        <div className="truncate text-xs font-medium text-[#24231d]">{entry.attempt_id}</div>
                        <div className="text-[11px] text-[#7a7365]">
                          {entry.status || 'unknown'} · {entry.final_verdict || 'pending'}
                        </div>
                      </div>
                    ))
                  ) : (
                    <span className="text-sm text-[#9a9385]">single attempt</span>
                  )}
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}
