# Task: Facebook Campaign Recovery 100

## North Star
Facebook campaign delivery for every run created or executed in the last 3 days reaches 100% successful jobs. Every job in that 3-day window must end as either successful delivery or a hard non-retryable impossibility with concrete evidence, after proxy health, profile health, campaign processor stability, and forensic visibility are proven.

## Success Criteria
- [ ] The last-3-days campaign window is explicitly defined by concrete timestamps and includes pending, processing, completed, failed, cancelled, retry, and no-result campaigns.
- [ ] Every campaign run in the last 3 days is fully inventoried, with every failed or pending job mapped to one root-cause class and one recovery action.
- [ ] Proxy is proven not to be the current blocker: `/proxy/health` and `/health/deep` show `proxy_store` on port `44418`, healthy outbound IP, and zero recent proxy failures before retry execution.
- [ ] Campaign processor crash source is fixed and tested: `broadcast_update()` cannot raise `Set changed size during iteration` when websocket clients connect/disconnect during campaign processing.
- [ ] Campaign retry eligibility is corrected: campaigns that failed before job execution still generate retryable failed-job records instead of becoming exhausted with zero result evidence.
- [ ] Profile pool is recovered safely: `infra_blocked` profiles caused by the old proxy are retested under `44418` and moved back to healthy only when authenticated facebook shell is proven.
- [ ] Remaining account-auth blockers are isolated: checkpoint/video-selfie/logged-out profiles stay excluded and are not treated as proxy failures.
- [ ] Existing failed/pending campaigns from the last 3 days are recovered to 100% success or marked impossible with concrete evidence per job: deleted/unreachable post, account challenge, expired media, or platform refusal.
- [ ] Local proof captured: backend tests cover websocket broadcast concurrency, no-result failed campaign retryability, proxy-gated retry-all behavior, profile health restoration, and failure taxonomy.
- [ ] Local proof captured: local dev server plus browser verification shows campaign queue/reliability surfaces expose retryable backlog and root causes clearly.
- [ ] Production proof captured after review approval: deploy from GitHub, verify healthy proxy and processor, run recovery against the last-3-days window, and read back `/queue`, `/queue/history`, `/analytics/summary`, and `/queue/reliability-audit` showing 100% recovered delivery or documented impossible jobs for that exact window.

## Preferences / Constraints
- Do not run retries or mutate campaign/profile state during this review phase.
- Do not blame the proxy without evidence. Classify each failure from campaign history, profile health, queue state, and live health endpoints.
- Do not wipe cookies, fingerprints, sessions, or campaign history. Recovery must reuse preserved session state and only update health/status after evidence.
- Do not hide failures behind aggregate success rates. 100% means every last-3-days job has either a successful result or an explicit non-retryable proof.
- Do not run live posting recovery until code fixes pass locally and production deployment is verified.
- Keep Facebook and Reddit proxy authority centralized through the active proxy store; no session-owned proxy fallback.

## Audit Findings

| area | production evidence | current state | root cause hypothesis | required fix |
|---|---|---|---|---|
| proxy health now | `/proxy/health` healthy, outbound IP present, `/health/deep.proxy.runtime.source=proxy_store`, active port `44418` | current proxy is healthy after centralization | proxy was a blocker before the migration, but is not the current live blocker | keep proxy preflight as a hard gate before every retry |
| last-3-days target | user clarified the goal is delivery of last 3 days runs all 100% | scope is not just current backlog or today; it is every campaign run in the last 3 days | audit needs a timestamp-bounded ledger before mutation | build last-3-days recovery ledger and prove 100% for that window |
| delivery today | `/analytics/summary`: 77 jobs, 11 successes, 14.29% delivery success | today's visible delivery is badly under target | old proxy outage plus unrecovered backlog dominates delivery stats | recover failed/pending jobs after processor/profile fixes |
| attempt health after fix | `/analytics/summary.attempt_today`: 4 attempts, 4 successes, 100% | new attempts after the proxy fix are passing so far | central proxy fix appears effective for fresh attempts | keep observing current running campaign before bulk retry |
| current queue | `/queue`: processor running, 4 pending campaigns, current campaign `2344751f...` has 2/18 success and no errors yet | backlog is actively processing under the fixed proxy | old pending campaigns are now being resumed | do not interrupt; verify completion/readback before recovery mutation |
| retry backlog | `/analytics/summary`: 9 campaigns, 141 jobs in retry backlog | backlog is larger than the 100-history failed rows because pending queue contributes outstanding jobs | queue state and history need one unified backlog ledger | build/read a deterministic backlog report before retry |
| history failures | `/queue/history?limit=100`: 5 campaigns have remaining failures, 74 jobs unrecovered | three recent failed campaigns have 0 results and campaign error `Set changed size during iteration` | processor crashed before job execution, likely websocket broadcast set mutation | iterate over a snapshot of websocket connections and test concurrent disconnects |
| no-result campaigns | campaigns `34eb427f...`, `d450968b...`, `3bc194de...` are `failed`, 0/20 or 0/19, no results, no auto_retry | these 58 jobs are not retryable through normal failed-job evidence | campaign-level exception bypasses failed-job construction | add recovery path that materializes retryable jobs for no-result failed campaigns |
| profile pool | `/analytics/profiles`: 35 profiles `infra_blocked` with reason `Page.goto: Timeout 60000ms exceeded.` | many otherwise valuable accounts are excluded from healthy profile selection | old proxy caused health test/navigation timeouts and left stale profile health states | retest infra-blocked profiles under `44418` and clear only when auth shell is proven |
| true auth blockers | 6 profiles need attention/deletion: 3 checkpoint, 2 video_selfie, 1 logged_out | these are not proxy failures | real account auth/challenge issues | keep excluded; do not spend retry attempts on them |
| older automation failures | campaign `7d19fc95...`: 8 unrecovered jobs from `Step 1 post not visible` and `Step 5 no comments present` | not proxy-classified by current evidence | destination/post visibility or verification weakness | inspect forensic artifacts before deciding retry vs impossible |
| profile exhaustion | campaign `1181679c...`: 11/19 success, 8 `No healthy profiles available`, auto_retry in progress | profile pool was too small/blocked during retry | stale infra_blocked health shrank pool | recover profile health first, then retry |
| reliability audit | `/queue/reliability-audit?lookback_days=14&min_total_count=1`: verdict `needs fixes before trust`, 638 jobs, 564 final completed, 16 unrecovered, root causes include infra/transport, navigation, post-verification, other | audit only sees history window and does not include all pending backlog | current reliability endpoint undercounts total recovery work | use it as one signal, not the full source of truth |

## Proposed Execution Sequence
- [x] Add a read-only last-3-days recovery ledger that merges pending queue, history, retry metadata, result-level failures, and profile health into one timestamp-bounded source of truth.
- [x] Fix websocket broadcast concurrency by iterating over a snapshot of active connections and preserving disconnect cleanup.
- [x] Add failed-campaign recovery for campaigns with zero result rows: synthesize retryable failed jobs from stored `jobs`/`comments` while preserving original campaign IDs and comments.
- [ ] Add profile-health retest workflow for `infra_blocked` sessions under the active proxy; only clear state when the authenticated shell is proven.
- [x] Tighten retry selection so it targets the last-3-days recovery ledger, includes no-result failed campaigns and pending recovered campaigns, but excludes true auth blockers and impossible posts.
- [ ] Add forensic gates for older navigation/post-verification failures before reposting: inspect screenshot/artifact if available, then classify as retryable or impossible.
- [x] Run local tests and localhost/browser queue verification.
- [ ] Deploy from committed GitHub state and verify production endpoints are on the new build.
- [ ] Execute production recovery in controlled batches, reading back each batch until every last-3-days job is successful or explicitly impossible.

## Learnings (outcome quality based)
- `2026-05-22 production proxy health` -> `/proxy/health` is healthy on active proxy-store port `44418`; `/health/deep` reports `source=proxy_store`, zero recent proxy failures, and healthy status.
- `2026-05-22 production analytics` -> delivery today is 11/77 successful jobs (14.29%), week is 317/458 (69.21%), while attempt_today is 4/4 (100%) after proxy centralization.
- `2026-05-22 production queue` -> processor is running campaign `2344751f-c08e-402e-80d8-b45797c22b2f`; 4 campaigns are pending/processing, all created by `ollietr` on 2026-05-21 around 23:32-23:33.
- `2026-05-22 production history audit` -> 5 of the last 100 history campaigns have remaining failures; 74 history jobs remain unrecovered.
- `2026-05-22 no-result failure audit` -> 3 campaigns failed with campaign-level error `Set changed size during iteration`, zero job results, and 58 total unrecovered jobs.
- `2026-05-22 failure taxonomy` -> remaining history job classes: 58 missing result evidence, 8 profile-pool exhaustion, 8 automation/navigation/post-verification.
- `2026-05-22 profile health audit` -> 35 profiles are `infra_blocked` from `Page.goto: Timeout 60000ms exceeded`; 6 profiles are true auth blockers requiring attention/deletion.
- `2026-05-22 reliability audit` -> 14-day reliability endpoint verdict is `needs fixes before trust`; dominant retry triggers are infra/transport, page-load/navigation, and post-verification.
- `2026-05-22 user scope clarification` -> north star is specifically last 3 days runs all 100% delivery, not only today's visible backlog or last 100 history rows.
- `2026-05-22 local tests` -> `pytest -q backend/tests/test_broadcast_reliability.py backend/tests/test_queue_drafts_recovery.py` passed 19 tests; `pytest -q backend/tests` passed 411 tests.
- `2026-05-22 local api verification` -> local uvicorn on `127.0.0.1:8117` served `/queue/last-3-days-recovery-ledger?hours_back=72`, `/queue/retry-all-failed/status`, and `/health/deep` successfully.
- `2026-05-22 browser verification fallback` -> bundled Playwright opened `http://127.0.0.1:8117/queue/last-3-days-recovery-ledger?hours_back=72` and read the expected JSON ledger response.
