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
- production is live on [commentfront.vercel.app](https://commentfront.vercel.app) with commit `d9188997214abad13243fcc9013812049f40f0ed`.
- the reddit tab now behaves like a real monitor instead of a generic form page:
  - selected day reads in one line as `day x of y`
  - the monitor header is denser and no longer wastes space on oversized cards
  - the per-profile board compresses progress/proof into fewer, more legible columns
  - proof drilldown renders compact target, screenshot, and attempt links instead of dumping raw urls
  - the utility rail leads with session health and credential linkage, while setup/manual tools are collapsed
- live backend state still includes one real rollout, one proof packet, and extra old/check programs, so the ui must continue to filter and demote that noise.

## active todo
1. complete. experiment 1 shipped in `d9188997214abad13243fcc9013812049f40f0ed` and was verified locally and in production.
2. complete. experiment 2 shipped in `d9188997214abad13243fcc9013812049f40f0ed` and preserves clickable proof access.
3. complete. experiment 3 shipped in `d9188997214abad13243fcc9013812049f40f0ed` and keeps setup/manual tools available behind collapsible sections.
4. complete. local tests, github push, deploy verification, and production browser verification all passed.

## current understanding
- the main problem is not missing data; it is poor information hierarchy.
- the current layout exposes operator-critical proof, but it presents it with consumer-dashboard spacing rather than dense operational scanning patterns.
- the raw-link treatment is one of the biggest readability failures: long urls are technically correct but visually stupid for an operator board.
- official design-system guidance points in the same direction: compact spacing for dense ui, spoken/nonnumeric dates for readability, and custom tables tuned to the exact workflow rather than generic reusable table chrome.

## proven wins
- rollout-first program sorting is already working in production, keeping `reddit_program_6f091d39c2` selected by default.
- the current board already proves the backend surface is sufficient for a high-leverage monitor: per-day profile rows plus clickable proof artifacts exist and load against live data.
- density and hierarchy fixes are now proven in both local and production browser checks:
  - single-line selected-day label
  - denser profile table with compact progress/proof summaries
  - compact target, screenshot, and attempt links in the expanded proof rows
  - utility rail reordered around session validity and credential linkage
- screenshots and live browser verification are available for both baseline and shipped redesign:
  - `/var/folders/66/tj1q_3hd6bq6xyzyszqswq000000gn/T/playwright-mcp-output/1773157785088/page-2026-03-10T16-08-14-780Z.png`
  - `/var/folders/66/tj1q_3hd6bq6xyzyszqswq000000gn/T/playwright-mcp-output/1773157785088/page-2026-03-10T17-55-19-361Z.png`
  - `/var/folders/66/tj1q_3hd6bq6xyzyszqswq000000gn/T/playwright-mcp-output/1773157785088/page-2026-03-10T17-57-07-693Z.png`
  - commit `d9188997214abad13243fcc9013812049f40f0ed`

## open risks
- prod still contains extra historical/check programs, so program-switcher copy and grouping must keep demoting that noise clearly.
- future proof rows with unusually long reddit paths still need ongoing scrutiny so compact link labels stay readable without hiding the actual destination.
- the websocket 403 noise in browser verification is orthogonal to the redesign, but it pollutes console-driven verification and should not be confused with monitor regressions.
