# Reddit Auto Subreddit Adaptation

## north star
- production can automatically compile and execute subreddit-specific proof work so each rollout profile can comment under a separate post in each configured subreddit, while adapting to community-specific identity requirements such as user flair without manual per-program setup.

## exact success criteria
- reddit program specs can declare `topic_constraints.proof_matrix` and `topic_constraints.subreddit_policies`, and the compiler emits the expected per-profile-per-subreddit work items.
- subreddit-specific identity handling is automatic and policy-driven: the runtime can discover/apply user flair when needed, but direct actions without that policy do not get unexpected navigation side effects.
- local verification passes across the broader reddit regression slice.
- deployed production can create and run a proof vehicle that demonstrates automatic subreddit adaptation with real proof artifacts.

## constraints
- do not modify the user’s dirty `/Users/nikitalienov/Documents/commentfront/.claude/CLAUDE.md`.
- production deploys must come from committed github state only.
- no untracked tracker junk can be left behind.

## current state
- the backend now has a real subreddit policy surface: `auto_user_flair`, keyword overrides, enabled actions, profile flair hints, and `proof_matrix`.
- `comment_post` generation is now available for discovered-post work items, which is required for per-profile-per-subreddit proof comments.
- subreddit identity state persists on the reddit session, and the bot can open the flair dialog, inspect options, choose a flair, and record identity evidence.
- the broader local reddit regression slice is green after the thread-context recovery, comment-surface scroll, and explicit-policy inheritance patches: `134 passed`.
- live proof vehicle `reddit_program_843e09725c` proved the first production bottleneck honestly: `comment_post` can drift from a thread into a subreddit listing during composer opening and then fail as `Reddit comment composer not found`.
- exact-thread smoke `reddit_program_32af52931a` proved the next bottleneck honestly on the patched thread-context build: the target thread is valid and commentable, but the mobile page can load with the thread header in view and the actual comment/share surface below the fold.
- exact-thread smoke `reddit_program_cd320ed4d1` on the latest build then passed on the same previously failing Healthyhooha thread, proving the thread-context recovery plus scroll-to-comment-surface fix in production.
- explicit proof items now inherit subreddit policy metadata during target resolution, so an exact target assignment can still trigger policy-driven behavior such as automatic flair handling or a configured flair hint.
- exact `AskWomenOver40` smoke `reddit_program_3e844ab292` reached the next real bottleneck honestly: automatic flair handling currently navigates to the subreddit root first, and for `reddit_amy_schaefera` that root returned repeated `net::ERR_EMPTY_RESPONSE` before the actual comment thread was even loaded.
- the executor is now patched to try flair from the target thread url first and only fall back to the subreddit root if the dialog is unavailable there; the local reddit regression slice is green again after this patch: `136 passed`.
- rerun smoke `reddit_program_80a6d043c8` on the thread-first flair build proved the next real bottleneck honestly: flair automation reached the target thread and opened the community menu, but the generic named-control matcher falsely matched page content that mentioned `user flair` and `apply`, so it never interacted with the real flair dialog.
- the flair dialog path is now tightened to reject oversized text matches and to require visible dialog-state signals after clicking the opener; the local reddit regression slice is green again after this patch: `137 passed`.
- rerun smoke `reddit_program_847a500d01` on the tightened matcher build proved the next real bottleneck honestly: the flair flow no longer fake-clicks page content, but the thread-context recovery path still used fuzzy title clicks and drifted to `https://www.reddit.com/user/daffodilmachete/` instead of deterministically reloading the target thread.
- thread-context recovery is now deterministic: it reloads the exact target thread and dismisses the open-app sheet instead of clicking arbitrary visible title text. the local reddit regression slice is green again after this patch: `138 passed`.
- rerun smoke `reddit_program_04a757c30a` on the deterministic thread-recovery build proved the next real bottleneck honestly: the target thread itself loads with `200`, but the open-app-sheet dismissal helper keeps causing unintended navigation into unrelated pages like `https://www.reddit.com/user/daffodilmachete/` and `https://www.reddit.com/r/askvan/`.
- the open-app-sheet dismissal helper is now tightened to dismiss only when it finds a compact close button inside the same bottom-sheet container as the `open` cta. the local reddit regression slice is green again after this patch: `139 passed`.

## active todo
1. commit the explicit-target policy inheritance patch and redeploy it to railway.
2. deploy the tightened open-app-sheet dismissal fix to railway.
3. rerun the exact-target `AskWomenOver40` production smoke on the deployed build with policy-driven flair automation enabled and verify identity evidence plus proof artifacts.
4. recreate the broader proof-matrix program on the new build and verify real proof rows across the configured subreddit set, including identity evidence for `AskWomenOver40`.

## current understanding
- the right architecture is split across three layers:
- compiler: emit hard proof work per `(profile, subreddit, action)` via `proof_matrix`.
- orchestrator: decide when subreddit-specific identity work is required and pass that intent into the executor.
- bot: execute flair discovery/application only when the orchestrator or caller explicitly requested it.
- if the bot probes flair whenever it merely knows the subreddit, it breaks otherwise-correct direct action flows and hides the real policy boundary.
- balancing create-post allocation only by global subreddit counts is not enough; per-profile load has to be included or the same profiles stay stuck on the same small subset.

## proven wins
- `RedditSession` now persists per-subreddit identity state, so discovered flair choices are durable across actions.
- the generator can now produce top-level comments and choose a subreddit flair option from visible community options.
- the compiler can now emit `proof_matrix` work items for `comment_post`, `reply_comment`, and `create_post`.
- the bot regression caused by unconditional flair probing was fixed by keeping the executor opt-in and policy-driven.
- the next production bottleneck is no longer vague: attempt `9dcaaf8b-be25-4aab-8450-34f9eeafba65` on `reddit_program_843e09725c` showed a thread-context drift bug, and the local fix for that bug is now covered by a dedicated regression test.
- attempt `a805e435-e942-48b3-ac7e-e7648a0adde0` on `reddit_program_32af52931a` proved that some commentable reddit threads require scrolling to reveal the comment surface before any composer trigger exists in the viewport.
- attempt `172d7e61-d2bb-4208-8816-2aae9f0dbb69` on `reddit_program_cd320ed4d1` reached `success_confirmed` on that same previously failing Healthyhooha thread, so the direct `comment_post` recovery path is proven in live production.
- explicit target assignments now carry subreddit policy metadata through `_resolve_target(...)`, which is required to prove automatic flair handling against exact smoke targets instead of only discovered targets.
- attempt `789c5d2a-f227-4f87-b054-7334a17fc5e6` on `reddit_program_3e844ab292` proved that `AskWomenOver40` is not currently blocked by missing compiler policy or missing proof plumbing; the real failure was the executor’s assumption that flair must always start from the subreddit root.
- attempt `c13f8d00-8fa2-44d0-acac-6ef69a81987c` on `reddit_program_80a6d043c8` proved that the thread-first flair entry path works well enough to get past the old root-network failure, but the next mismatch is selector quality inside the flair dialog workflow rather than infra or compiler policy.
- attempt `b81263a0-04f6-4d51-bc05-8f5b73327622` on `reddit_program_847a500d01` proved that the tightened flair matcher got us past the old fake dialog clicks, and that the next mismatch is a fuzzy thread-recovery heuristic rather than flair or infra.
- attempt `f056ce90-e5c6-4cd7-a0e2-cc006314091a` on `reddit_program_04a757c30a` proved that even with deterministic thread reload, the generic open-app-sheet dismissal helper is still loose enough to trigger unrelated navigation on this subreddit surface.

## open risks
- production still needs proof that all 10 valid sessions can execute the new proof-matrix flow against real subreddit conditions.
- `AskWomenOver40` may still have community-specific posting/commenting friction beyond flair, so production evidence needs to show the actual limiting factor after the new thread-first flair path lands.
- the current runtime can adapt to flair, but other community-specific identity requirements may still need additional surface discovery rules.
