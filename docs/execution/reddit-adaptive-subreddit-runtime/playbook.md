# playbook

## default execution loop
- model subreddit-specific behavior explicitly in the program spec instead of hard-coding assumptions into discovery order.
- verify compiler allocation and runtime eligibility separately; they fail in different ways.

## stable tactics
- put subreddit rules in one shared policy layer so the api, compiler, and orchestrator use the same contract.
- for low-volume generated-post programs, balance by current assigned-count divided by weight, not by a per-profile/day modulo cursor.
- treat required per-profile subreddit flair as an eligibility rule, not as a best-effort hint.
- allow subreddit-specific keyword overrides because broad support communities and narrow health communities do not respond to the same query pack.

## failure patterns
- ordered subreddit assignment inside each profile/day loop creates fake diversity while later subreddits never receive generated posts.
- a subreddit can look “bad” in production when the real failure is that the program kept assigning impossible work to flair-ineligible profiles.
- one global keyword set makes discovery look healthy in `healthyhooha` while starving broader communities like `women` and `askwomenover40`.

## verification patterns
- prove subreddit-policy behavior with compiled work-item inspection, not only with live attempts.
- add one regression that shows a flair-required subreddit is included for the configured profile and excluded for an unconfigured one.
- add one regression that shows keyword overrides reach the generator inputs, not just the stored config.

## promotion rules
- promote only rules that survive both store-level compilation tests and orchestrator-level runtime tests.
- do not call a subreddit adaptation complete until production accepts the spec shape and compiles it the same way.
