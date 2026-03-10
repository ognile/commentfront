# Reddit Ops Workspace Redesign

## north star
- the reddit tab behaves like dense operator software: one clear rollout monitor, compact day switching, fast per-profile scanning, and proof drilldown that is clickable, compact, and immediately legible.

## exact success criteria
- the default reddit view opens on the current rollout and demotes stale programs/check runs into clearly secondary history.
- the day selector reads as one horizontal control with a single-line selected-day state and no awkward wrapping or stacked labels.
- the per-profile table is dense enough to scan all ten profiles on one day quickly without large dead zones or oversized cards.
- expanded proof rows show short, clickable target/proof/attempt links instead of dumping raw full urls into the layout.
- the utility rail prioritizes session validity and credential linkage, with lower-priority setup/manual tools collapsed.
- the redesign is verified locally and on production with screenshots and live data from `reddit_program_6f091d39c2`.

## constraints
- preserve the existing backend operator endpoints; this loop is frontend-first unless a verified frontend blocker requires api changes.
- keep the proof surface complete: url, screenshot, attempt id, verdict, and retry history must remain accessible.
- do not touch unrelated user changes, including `.claude/CLAUDE.md`.
- finish end to end: local verification, github push, deployment completion, and production verification.

## current state
- production is live on [commentfront.vercel.app](https://commentfront.vercel.app) with commit `14c784d6b219042890896268c2c35021a9bc7004`.
- the tab now pins the real rollout and groups old programs better, but the current production screenshot still shows major operator-ux defects:
  - oversized control area and too many stacked cards
  - awkward selected-day presentation
  - proof drilldown wastes vertical space
  - raw urls dominate the layout instead of acting like compact action links
  - session/credential rail is still too form-heavy for the actual operator use case
- live backend state still includes one real rollout, one proof packet, and extra old/check programs, so the ui must continue to filter and demote that noise.

## active todo
1. experiment 1: collapse the monitor chrome into a denser hierarchy with cleaner day selection and smaller metric surfaces.
2. experiment 2: redesign profile proof drilldown so target/proof/attempt links are compact, clickable, and readable at a glance.
3. experiment 3: refocus the utility rail on linked credentials + valid sessions, pushing setup/manual tools into collapsible sections.
4. run full local verification, push, wait for deploy, and visually re-check production against the exact screenshot defects.

## current understanding
- the main problem is not missing data; it is poor information hierarchy.
- the current layout exposes operator-critical proof, but it presents it with consumer-dashboard spacing rather than dense operational scanning patterns.
- the raw-link treatment is one of the biggest readability failures: long urls are technically correct but visually stupid for an operator board.
- official design-system guidance points in the same direction: compact spacing for dense ui, spoken/nonnumeric dates for readability, and custom tables tuned to the exact workflow rather than generic reusable table chrome.

## proven wins
- rollout-first program sorting is already working in production, keeping `reddit_program_6f091d39c2` selected by default.
- the current board already proves the backend surface is sufficient for a high-leverage monitor: per-day profile rows plus clickable proof artifacts exist and load against live data.
- production screenshots and live browser verification are available for the pre-redesign baseline:
  - `/var/folders/66/tj1q_3hd6bq6xyzyszqswq000000gn/T/playwright-mcp-output/1773157785088/page-2026-03-10T16-08-14-780Z.png`
  - `/var/folders/66/tj1q_3hd6bq6xyzyszqswq000000gn/T/playwright-mcp-output/1773157785088/page-2026-03-10T16-06-33-999Z.png`

## open risks
- aggressive density changes can make the board harder to click if spacing collapses too far.
- the utility rail still needs to stay functional for import/session creation even while being visually demoted.
- the websocket 403 noise in browser verification is orthogonal to the redesign, but it pollutes console-driven verification and should not be confused with monitor regressions.
