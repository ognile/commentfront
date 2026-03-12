# Remote Browser Lease Rollout

## north star
- facebook and reddit remote control run on per-lease browser workers with one controller plus observers, instant takeover, hard capacity limit 2, shared profile reservation, persistent proof artifacts, and no leftover singleton or rollout-only code.

## exact success criteria
- two different profiles can hold active remote leases concurrently without interfering with each other.
- a second client attaching to the same profile becomes an observer until takeover, and takeover is immediate, logged, and deterministic.
- paste, scroll, click, drag, and reconnect work through the new lease engine without stream stalls or global-session resets.
- facebook upload flow still works through remote control.
- login/session creation, refresh-name, refresh-picture, and reddit session creation/testing remain behaviorally unchanged.
- backend and frontend test/build pipelines pass locally.
- backend-first and frontend-second production deploys complete from github state and pass production smoke verification.
- the final codebase contains one remote engine and no dead singleton or rollout shim code.

## constraints
- production deploys must come only from committed github state.
- local verification must pass before any push.
- no changes may regress login flow, profile refresh flow, or profile picture refresh flow.
- proof artifacts must be written under this execution tracker and referenced from experiments.
- do not modify the user's existing change in `.claude/CLAUDE.md`.

## current state
- the lease service remains the only remote runtime; the final startup-latency fix is deployed in github commit `9385d1f` and railway deployment `81db6510-5f11-4375-92c6-23d9c0006828`.
- vercel is green on `9385d1f`; the frontend bundle remains `index-BB8jDaND.js` because the final iterations were backend-only.
- production now proves the reported vanessa hang is resolved at the actual websocket attach layer: `browser_ready` improved from `9.257s` to `4.778s`, first frame improved from `9.705s` to `5.599s`, and the dead saved proxy is cut off after one short startup attempt before env fallback.
- the only pre-existing worktree change outside this task remains `.claude/CLAUDE.md`.

## active todo
- none.

## current understanding
- the main correctness bug is architectural, not cosmetic: one global browser slot plus direct websocket-to-page mutation creates unavoidable interference and poor input fidelity.
- a non-breaking rollout requires preserving the current remote routes while swapping their internals first, then upgrading the frontend, then removing compatibility code.
- remote leases must own reservation state, browser lifecycle, proof logs, and upload state.
- the final startup-latency problem was not the renderability gate alone. production timing proved the real operator-visible floor came from dead saved session proxies spending too long in startup before env fallback began.
- the durable fix has three layers:
  - reject blank shells as non-renderable
  - abort a proxy once the startup page health is a proven dead shell
  - apply a shorter startup navigation budget only to saved session proxies that already have an env fallback available

## proven wins
- the adaptive execution tracker is initialized and baseline production health/remote artifacts are saved.
- the backend cutover and reservation changes are implemented locally with the backend suite passing: `327 passed`.
- the frontend remote-client rewrite and tests/build are green locally.
- local api smoke proves the new reservation metadata is exposed and a remote start without proxy fails cleanly instead of hanging.
- local browser-level verification proves the new remote modal renders and surfaces the proxy failure state to the operator.
- the final cleanup is implemented locally: `backend/browser_manager.py` is deleted, `backend/main.py` only accepts canonical remote actions, dead websocket sends prune viewers, and the full backend suite now passes at `333 passed`.
- railway serves commit `8a6f8f31e2205917d860c4d04f0350329e92628d` and vercel serves deployment `dpl_CvTB2NLLifm4iiAX19JLfLPr2Ee3` on `commentfront.vercel.app`.
- production capacity, reservation, takeover, disconnect, health, and frontend-ui smokes are all confirmed with saved artifacts.
- fresh production probes now prove the remaining defect shape instead of leaving it anecdotal: the saved facebook probe images are blank white frames, and the earlier stop window shows a new browser start after a user-initiated stop path.
- the final hotfix is locally green on the current commit: backend `336 passed`; frontend `14 passed`; frontend build passed; lint stayed at the same 7 pre-existing `App.tsx` warnings with no new warnings in remote-control code. evidence: `artifacts/local/backend-pytest-lifecycle-fix.txt`, `artifacts/local/frontend-test-lifecycle-fix.txt`, `artifacts/local/frontend-build-lifecycle-fix.txt`, `artifacts/local/frontend-lint-lifecycle-fix.txt`
- production facebook control is fixed for the reported failure path:
  - vanessa now stays in `starting` through the dead session proxy, then falls back to env proxy and only declares `browser_ready` once page health has a real title and `htmlLength=23287`, instead of promoting the earlier blank shell.
  - explicit modal close returns production remote status to zero active leases with no reconnect.
  - page reload during startup now issues `manual_stop`, cancels `browser_start`, and returns status to zero active leases instead of leaking a `starting` lease.
  evidence: `artifacts/production/prod-vanessa-logs-ready-after-renderable-gate.json`, `artifacts/production/prod-remote-status-after-pagehide-stop-fix.json`, `artifacts/production/prod-vanessa-logs-after-pagehide-stop-fix.json`
- production reddit control is still healthy after the hotfix: `reddit_amy_schaefera` reaches `browser_ready` on the first session-proxy attempt with a live frame and closes cleanly back to zero active leases. evidence: `artifacts/production/prod-reddit-amy-logs-after-fix.json`, `artifacts/production/prod-remote-status-after-reddit-close-fix.json`
- the refreshed production frontend no longer emitted the earlier passive wheel errors during verification, matching the non-passive wheel listener change in the hook.
- the backend startup-latency iterations are fully proven:
  - commit `e23f914` introduced dead-shell proxy aborts and kept the local gate green.
  - commit `943bf01` proved the render-wait early exit was live in production but also proved the next bottleneck was session-proxy navigation time.
  - commit `9385d1f` successfully deployed the shorter saved-proxy startup timeout and cut vanessa from `9.257s` ready / `9.705s` first frame to `4.778s` ready / `5.599s` first frame.
  evidence: `artifacts/production/prod-remote-startup-regression-summary-e23f914.json`, `artifacts/production/prod-remote-startup-regression-summary-943bf01.json`, `artifacts/production/prod-remote-startup-regression-summary-9385d1f.json`
- the final live vanessa logs show the dead session proxy was abandoned after about `2.8s` instead of the earlier `6.7s+`, then env fallback reached `browser_ready` about `1.6s` later. evidence: `artifacts/production/prod-facebook-vanessa-hines-logs-final-startup-fix.json`
- the final live reddit regression probe stayed healthy on the same deployment: `reddit_amy_schaefera` reached `browser_ready` in `3.96s` on the session proxy and closed cleanly. evidence: `artifacts/production/prod-reddit-reddit-amy-schaefera-logs-final-startup-fix.json`
- railway deployment `81db6510-5f11-4375-92c6-23d9c0006828` and github commit status for `9385d1f` are both green. evidence: `artifacts/production/prod-railway-deployments-9385d1f.txt`, `artifacts/production/prod-github-status-9385d1f.json`

## open risks
- none in scope for this task. the only operational wrinkle was one transient railway deployment miss on `1aabc9c`; the next github-state deploy `9385d1f` succeeded and is the authoritative production proof point.

## final pass/fail matrix
- `[pass]` two different profiles can hold active leases concurrently. evidence: `artifacts/production/prod-capacity-and-reservation-after-cleanup.json`
- `[pass]` a third lease is rejected at the hard capacity limit with `409 remote_capacity_full`. evidence: `artifacts/production/prod-capacity-and-reservation-after-cleanup.json`
- `[pass]` a leased profile blocks refresh work with `409` and explicit reservation metadata. evidence: `artifacts/production/prod-capacity-and-reservation-after-cleanup.json`
- `[pass]` observer attach plus instant takeover work on production and controller ownership changes to the takeover user. evidence: `artifacts/production/prod-facebook-takeover-after-cleanup.json`
- `[pass]` after the original controller disconnects, the surviving controller can still execute a canonical scroll action successfully. evidence: `artifacts/production/prod-facebook-takeover-after-cleanup.json`
- `[pass]` after all viewers disconnect, the lease remains detached at `viewer_count=0` until explicit stop, then returns to zero active leases. evidence: `artifacts/production/prod-facebook-takeover-after-cleanup.json`
- `[pass]` production backend health is healthy and remote status is zero-active after cleanup verification. evidence: `artifacts/production/backend-health-after-cleanup.json`, `artifacts/production/prod-remote-status-after-cleanup.json`
- `[pass]` recent railway logs contain zero matches for the previous closed-websocket spam signatures. evidence: `artifacts/production/prod-remote-log-check-after-cleanup.json`
- `[pass]` the shipped frontend sessions UI opens a live facebook remote modal and renders a connected browser frame against the cleaned-up backend. evidence: `artifacts/production/prod-frontend-ui-smoke.md`
- `[pass]` vanessa no longer promotes a blank facebook shell to `browser_ready`; the live backend now waits until env-proxy fallback returns a page with `title=Facebook` and `htmlLength=23287`. evidence: `artifacts/production/prod-vanessa-detached-ready-after-renderable-gate.json`, `artifacts/production/prod-vanessa-logs-ready-after-renderable-gate.json`
- `[pass]` closing the facebook remote modal releases the slot immediately and leaves production at zero active leases. evidence: `artifacts/production/prod-remote-status-after-closing-verifier-ui.json`, `artifacts/production/prod-remote-status-after-pagehide-stop-fix.json`
- `[pass]` reloading the page during facebook startup triggers `manual_stop` and `browser_start_cancelled` instead of leaving a zombie `starting` lease behind. evidence: `artifacts/production/prod-vanessa-logs-after-pagehide-stop-fix.json`, `artifacts/production/prod-remote-status-after-pagehide-stop-fix.json`
- `[pass]` reddit remote control still reaches a live browser frame and closes cleanly after the lifecycle hotfix. evidence: `artifacts/production/prod-reddit-amy-logs-after-fix.json`, `artifacts/production/prod-remote-status-after-reddit-close-fix.json`
- `[pass]` the reported vanessa startup wait is materially reduced in production. evidence: `artifacts/production/prod-remote-startup-regression-summary-e23f914.json`, `artifacts/production/prod-remote-startup-regression-summary-9385d1f.json`
- `[pass]` the final production backend deploy for the startup-latency fix is green on github and railway. evidence: `artifacts/production/prod-github-status-9385d1f.json`, `artifacts/production/prod-railway-deployments-9385d1f.txt`
- `[pass]` after the final production probes, remote status returns to zero active leases. evidence: `artifacts/production/prod-remote-status-after-final-startup-fix.json`
