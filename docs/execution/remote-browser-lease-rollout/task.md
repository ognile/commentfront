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
- the remote singleton is still removed and production still runs on the lease service only.
- the earlier "done" verdict was wrong. fresh production proof now shows two live defects: modal close can still lead to a reopen path, and fresh facebook remote attaches can deliver blank white frames.
- new production evidence is saved under `artifacts/production/`, including blank-frame probes and the earlier stop/reconnect log window.
- the only pre-existing worktree change outside this task remains `.claude/CLAUDE.md`.

## active todo
1. patch the frontend close lifecycle so an intentional modal close cannot trigger reconnect and sole-controller close releases capacity immediately.
2. replace the blank cdp pixel path with a screenshot-based per-lease frame pump and add better remote diagnostics.
3. run targeted local verification for the frontend hook/modal and backend remote lease tests.
4. push the hotfix through github, wait for production deploys, and re-run facebook plus reddit remote smokes on production.
5. update proof artifacts, tracker synthesis, and the final pass/fail matrix from the hotfix results.

## current understanding
- the main correctness bug is architectural, not cosmetic: one global browser slot plus direct websocket-to-page mutation creates unavoidable interference and poor input fidelity.
- a non-breaking rollout requires preserving the current remote routes while swapping their internals first, then upgrading the frontend, then removing compatibility code.
- remote leases must own reservation state, browser lifecycle, proof logs, and upload state.
- the current production failures are now narrower and concrete:
- the frontend close path closes the websocket before the modal-open refs are cleared, so `onclose` can still schedule an automatic reconnect against a session the user thought they closed.
- the backend cdp frame path can report a healthy lease while the delivered first frame is a blank white jpeg; both `Vanessa Hines` and `Wanda Lobb` reproduced that on production.

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

## open risks
- until the hotfix ships, facebook remote can still show blank frames and the ui can still leave capacity pinned after an intended close.

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
