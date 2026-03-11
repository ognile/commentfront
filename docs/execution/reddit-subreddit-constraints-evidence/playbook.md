# playbook

## default execution loop
- check compiled work-item allocation before blaming runtime execution. sometimes a “subreddit failure” is actually a compiler starvation pattern.
- compare subreddit `search/.json` yield and `hot/.json` yield with the exact same user-agent the runtime uses.
- separate three cases explicitly: never allocated, allocated but discovery-starved, allocated and blocked by subreddit-side posting friction.

## stable tactics
- if generated-post volume is low, an ordered subreddit pool can starve later subreddits entirely when allocation resets per profile/day.
- broad communities need a different keyword strategy than narrow health-focused communities. measure query yield before trusting the same keyword pack everywhere.
- treat visible flair-setting guidance in a subreddit’s own hot feed as a posting-risk signal worth tracking.

## failure patterns
- round-robin assignment from the front of the subreddit list creates fake “coverage” on paper while later communities get zero generated-post work.
- `target_resolution_timeout` on generated posts can hide two different realities: poor discovery yield or a higher-friction posting surface.
- a keyword pack tuned for `healthyhooha` or `womenshealth` can return `0` results in broader support communities like `women` and `askwomenover40`.

## verification patterns
- for subreddit spread questions, inspect all three surfaces together:
  - `/reddit/programs/{id}/operator-view`
  - `/reddit/programs` compiled work items
  - live reddit `search/.json` and `hot/.json` with runtime headers
- when `by_subreddit` failures cluster on one subreddit, compare that subreddit’s search yield to the other configured subreddits before changing prompts.
- record at least one live permalink when a subreddit advertises its own posting constraint, such as required user flair.

## promotion rules
- promote only rules that explain both the rollout data and the runtime code path.
- do not call a subreddit “bad” unless the evidence distinguishes allocation bias from real posting friction.
