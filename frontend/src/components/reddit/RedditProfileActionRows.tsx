import { ArrowUpRight, Camera, FileSearch } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { openAuthenticatedApiDocument } from '@/lib/api'

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

function riskTone(ok: boolean): 'destructive' | 'outline' {
  return ok ? 'destructive' : 'outline'
}

function shortId(value: string | null | undefined): string {
  if (!value) return 'no attempt'
  return value.slice(0, 8)
}

function compactUrl(value: string | null | undefined): string {
  if (!value) return 'missing target'
  try {
    const url = new URL(value)
    const fullPath = `${url.pathname}${url.search}`
    if (fullPath.length <= 44) return fullPath
    return `${fullPath.slice(0, 26)}...${fullPath.slice(-14)}`
  } catch {
    return value.length <= 44 ? value : `${value.slice(0, 26)}...${value.slice(-14)}`
  }
}

function hostLabel(value: string | null | undefined): string {
  if (!value) return 'no link yet'
  try {
    return new URL(value).hostname.replace(/^www\./, '')
  } catch {
    return 'external link'
  }
}

function mainTarget(row: RedditOperatorActionRow): string | null {
  return row.target_ref || row.target_url || row.target_comment_url || null
}

interface RedditProfileActionRowsProps {
  rows: RedditOperatorActionRow[]
}

export function RedditProfileActionRows({ rows }: RedditProfileActionRowsProps) {
  async function handleProofOpen(url: string | null | undefined, label: string) {
    if (!url) return
    try {
      await openAuthenticatedApiDocument(url)
    } catch (error) {
      console.error(`failed to open ${label}`, error)
    }
  }

  return (
    <div className="rounded-2xl border border-[#ddd5c5] bg-[#fffdf8]">
      <Table className="table-fixed text-[13px]">
        <TableHeader>
            <TableRow className="border-[#e8e1d2] hover:bg-transparent">
              <TableHead className="h-10 w-[150px] px-3 text-xs uppercase tracking-[0.12em] text-[#8a7f6a]">action</TableHead>
              <TableHead className="h-10 px-3 text-xs uppercase tracking-[0.12em] text-[#8a7f6a]">target</TableHead>
              <TableHead className="h-10 w-[270px] px-3 text-xs uppercase tracking-[0.12em] text-[#8a7f6a]">persona + text</TableHead>
              <TableHead className="h-10 w-[220px] px-3 text-xs uppercase tracking-[0.12em] text-[#8a7f6a]">proof</TableHead>
              <TableHead className="h-10 w-[180px] px-3 text-xs uppercase tracking-[0.12em] text-[#8a7f6a]">latest</TableHead>
              <TableHead className="h-10 w-[150px] px-3 text-xs uppercase tracking-[0.12em] text-[#8a7f6a]">history</TableHead>
            </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => {
            const screenshotUrl = row.screenshot_artifact_url
            const attemptUrl = row.attempt_id ? `/forensics/attempts/${row.attempt_id}` : null
            const primaryTarget = mainTarget(row)
            const commentTarget = row.target_comment_url && row.target_comment_url !== primaryTarget ? row.target_comment_url : null
            return (
              <TableRow key={row.work_item_id} className="border-[#eee6d6] align-top">
                <TableCell className="space-y-1 px-3 py-3">
                  <div className="font-medium text-[#24231d]">{row.action}</div>
                  <div className="text-xs text-[#7a7365]">{row.subreddit || 'no subreddit'}</div>
                </TableCell>
                <TableCell className="space-y-2 px-3 py-3">
                  <div className="flex flex-wrap gap-2">
                    {primaryTarget ? (
                      <a
                        href={primaryTarget}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 rounded-full border border-[#d8d3c5] bg-white px-2 py-1 text-xs font-medium text-[#155e75] transition hover:border-[#9bc5d3]"
                      >
                        target
                        <ArrowUpRight className="h-3 w-3" />
                      </a>
                    ) : (
                      <span className="rounded-full border border-dashed border-[#d8d3c5] px-2 py-1 text-xs text-[#9a9385]">
                        missing target
                      </span>
                    )}
                    {commentTarget ? (
                      <a
                        href={commentTarget}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 rounded-full border border-[#d8d3c5] bg-white px-2 py-1 text-xs font-medium text-[#155e75] transition hover:border-[#9bc5d3]"
                      >
                        comment
                        <ArrowUpRight className="h-3 w-3" />
                      </a>
                    ) : null}
                  </div>
                  <div className="space-y-1">
                    <div className="truncate font-mono text-[11px] text-[#2f6f81]">{compactUrl(primaryTarget)}</div>
                    <div className="text-[11px] text-[#8a7f6a]">{hostLabel(primaryTarget)}</div>
                  </div>
                  {row.error ? <div className="text-xs text-[#b42318]">{row.error}</div> : null}
                </TableCell>
                <TableCell className="space-y-2 px-3 py-3">
                  <div className="flex flex-wrap gap-1.5">
                    {row.persona_role ? <Badge variant="secondary">{row.persona_role.replace(/_/g, ' ')}</Badge> : null}
                    {row.case_style_applied ? <Badge variant="outline">{row.case_style_applied.replace(/_/g, ' ')}</Badge> : null}
                    {typeof row.word_count === 'number' ? <Badge variant="outline">{row.word_count} words</Badge> : null}
                  </div>
                  {row.persona_id ? <div className="text-[11px] text-[#7a7365]">{row.persona_id}</div> : null}
                  {row.generated_text ? (
                    <div className="rounded-xl border border-[#e7e0d0] bg-white px-2.5 py-2 text-[12px] leading-5 text-[#2c2a24]">
                      {row.generated_text}
                    </div>
                  ) : (
                    <div className="text-xs text-[#9a9385]">no generated text</div>
                  )}
                </TableCell>
                <TableCell className="space-y-2 px-3 py-3">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge variant={proofBadgeTone(row.proof_flags.has_url)}>url</Badge>
                    <Badge variant={proofBadgeTone(row.proof_flags.has_screenshot)}>shot</Badge>
                    <Badge variant={proofBadgeTone(row.proof_flags.has_attempt)}>attempt</Badge>
                    <Badge variant={proofBadgeTone(row.proof_flags.success_confirmed)}>confirmed</Badge>
                    <Badge variant={riskTone(row.proof_flags.unsafe_rollout)}>unsafe</Badge>
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    <Badge variant={riskTone(row.target_collision_flags.duplicate_target_ref)}>target collision</Badge>
                    <Badge variant={riskTone(row.target_collision_flags.duplicate_reply_thread)}>thread dogpile</Badge>
                    <Badge variant={riskTone(row.semantic_similarity_flags.length > 0)}>
                      similarity {row.semantic_similarity_flags.length}
                    </Badge>
                  </div>
                  {row.semantic_similarity_flags.length > 0 ? (
                    <div className="text-[11px] text-[#b42318]">{row.semantic_similarity_flags.join(', ')}</div>
                  ) : null}
                  <div className="flex flex-wrap items-center gap-2">
                    {screenshotUrl ? (
                      <button
                        type="button"
                        onClick={() => { void handleProofOpen(screenshotUrl, `proof-shot-${row.work_item_id}`) }}
                        className="inline-flex items-center gap-1 rounded-full border border-[#d8d3c5] bg-white px-2 py-1 text-xs font-medium text-[#155e75] transition hover:border-[#9bc5d3]"
                      >
                        <Camera className="h-3 w-3" />
                        shot
                      </button>
                    ) : null}
                    {attemptUrl ? (
                      <button
                        type="button"
                        onClick={() => { void handleProofOpen(attemptUrl, `proof-attempt-${row.attempt_id}`) }}
                        className="inline-flex items-center gap-1 rounded-full border border-[#d8d3c5] bg-white px-2 py-1 text-xs font-medium text-[#155e75] transition hover:border-[#9bc5d3]"
                      >
                        <FileSearch className="h-3 w-3" />
                        attempt
                      </button>
                    ) : null}
                  </div>
                </TableCell>
                <TableCell className="space-y-2 px-3 py-3">
                  <Badge variant={statusTone(row.status)} className="capitalize">{row.status}</Badge>
                  <div className="space-y-2">
                    <Badge variant={verdictTone(row.final_verdict)}>{row.final_verdict || 'no verdict yet'}</Badge>
                    <div className="text-[11px] text-[#7a7365]">
                      {attemptUrl ? shortId(row.attempt_id) : 'no attempt'} · {row.attempts} tries
                    </div>
                  </div>
                </TableCell>
                <TableCell className="space-y-2 px-3 py-3">
                  {row.attempt_history.length > 1 ? (
                    row.attempt_history.map((entry) => (
                      <div key={entry.attempt_id} className="rounded-lg border border-[#e5dece] bg-white px-2 py-1.5">
                        <div className="truncate text-xs font-medium text-[#24231d]">{shortId(entry.attempt_id)}</div>
                        <div className="text-[11px] text-[#7a7365]">
                          {entry.status || 'unknown'} · {entry.final_verdict || 'pending'}
                        </div>
                      </div>
                    ))
                  ) : (
                    <span className="text-xs text-[#9a9385]">single attempt</span>
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
