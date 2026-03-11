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
- the broader local reddit regression slice is green after the thread-context recovery patch: `132 passed`.
- live proof vehicle `reddit_program_843e09725c` proved the first production bottleneck honestly: `comment_post` can drift from a thread into a subreddit listing during composer opening and then fail as `Reddit comment composer not found`.
- that old-build proof vehicle has been cancelled so the next proof run can happen only on the patched build.

## active todo
1. commit the thread-context recovery patch and redeploy it to railway.
2. recreate the proof-matrix program on the patched build.
3. verify real production proof rows across the configured subreddit set, including identity evidence for `AskWomenOver40`.

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

## open risks
- production still needs proof that all 10 valid sessions can execute the new proof-matrix flow against real subreddit conditions.
- `AskWomenOver40` may still have community-specific posting/commenting friction beyond flair, so production evidence needs to show the actual limiting factor rather than assuming flair solved everything.
- the current runtime can adapt to flair, but other community-specific identity requirements may still need additional surface discovery rules.
