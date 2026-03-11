# playbook

## default execution loop
- define the terminal state before experiments begin
- separate compiler proof requirements, orchestrator policy, and executor behavior before changing subreddit adaptation logic.

## stable tactics
- add only proven reusable tactics here
- persist per-subreddit identity state on the session so automatically discovered flair choices can be reused.
- use a proof-matrix compiler contract when you need guaranteed `(profile, subreddit)` coverage instead of hoping weighted random allocation will prove it.
- on reddit mobile threads, scroll until the comment surface or action row is visible before trying composer selectors.

## failure patterns
- add recurring traps here
- do not let the bot probe subreddit identity just because it can infer the subreddit from a url; that creates direct-action regressions and hides whether the policy layer asked for identity work.
- do not balance subreddit assignment only with global counts; profiles will still drift into repetitive coverage patterns.
- do not treat a missing comment composer as a surface-only failure until you verify the page is still on the target thread; composer heuristics can drift onto listing surfaces.

## verification patterns
- add proof rules here
- keep a focused bot regression suite around direct `create_post` and `reply_comment` calls whenever subreddit identity automation changes.
- prove subreddit adaptation in production with compiler-emitted proof items, not by eyeballing incidental coverage in a long-running rollout.
- when a comment flow fails in production, inspect both the pre-action and failure dom snapshots to distinguish a real missing composer from thread-context drift.
- verify exact smoke targets through the same policy path as discovered targets; otherwise explicit production proofs can silently skip the very subreddit policy you are trying to validate.

## promotion rules
- promote only evidence-backed reusable lessons
