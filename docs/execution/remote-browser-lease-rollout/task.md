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
- current remote control is implemented as a singleton `PersistentBrowserManager` with an explicit one-session-at-a-time model.
- production baseline artifacts are saved under `artifacts/baseline/`.
- current production already exhibited failed remote-stream states and idle-close cleanup.
- the only pre-existing worktree change is `.claude/CLAUDE.md`.

## active todo
1. cut the backend-only commit, push it, and verify railway deployment plus prod compatibility with the old frontend.
2. cut the frontend commit, push it, and verify vercel plus railway plus production remote smoke.
3. remove rollout-only action translation and the legacy singleton file, rerun verification, and push the cleanup commit.
4. re-run production smoke after cleanup and close the pass/fail matrix with evidence.

## current understanding
- the main correctness bug is architectural, not cosmetic: one global browser slot plus direct websocket-to-page mutation creates unavoidable interference and poor input fidelity.
- a non-breaking rollout requires preserving the current remote routes while swapping their internals first, then upgrading the frontend, then removing compatibility code.
- remote leases must own reservation state, browser lifecycle, proof logs, and upload state.

## proven wins
- the adaptive execution tracker is initialized and baseline production health/remote artifacts are saved.
- the backend cutover and reservation changes are implemented locally with the backend suite passing: `327 passed`.
- the frontend remote-client rewrite and tests/build are green locally.
- local api smoke proves the new reservation metadata is exposed and a remote start without proxy fails cleanly instead of hanging.
- local browser-level verification proves the new remote modal renders and surfaces the proxy failure state to the operator.

## open risks
- the production deploy window can still expose frontend/backend skew if compatibility coverage is incomplete.
- local end-to-end interaction quality for actual facebook/reddit browser control remains blocked by missing local proxy config, so final interaction proof must come from production.
