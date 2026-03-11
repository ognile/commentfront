# reddit adaptive subreddit runtime

## north star
- make reddit programs configurable enough to express subreddit-specific participation rules, per-profile user-flair requirements, subreddit-specific discovery keywords, and logically balanced post allocation across the configured subreddit set.

## exact success criteria
- the reddit program api accepts per-subreddit policy config in the live program spec.
- compiled generated-post work no longer starves later subreddits just because daily post volume is low.
- subreddit policies can require per-profile user flair for `create_post` and keep ineligible profiles out of those lanes.
- subreddit policies can override discovery/generation keywords per subreddit.
- local backend and frontend verification passes, then production accepts and compiles a policy-driven proof program.

## constraints
- production deploys must come from committed github state only.
- do not touch the user’s existing `.claude/CLAUDE.md` worktree change.
- keep deficits honest: if a profile lacks required subreddit flair config, the runtime must not silently pretend the subreddit is usable.

## current state
- added a shared subreddit-policy module in `/Users/nikitalienov/Documents/commentfront/backend/reddit_subreddit_policies.py`.
- extended the reddit program api schema in `/Users/nikitalienov/Documents/commentfront/backend/main.py` with `topic_constraints.subreddit_policies`.
- upgraded `/Users/nikitalienov/Documents/commentfront/backend/reddit_program_store.py` so generated-post allocation uses policy-aware balancing instead of resetting a front-of-list round-robin on every profile/day.
- upgraded `/Users/nikitalienov/Documents/commentfront/backend/reddit_program_orchestrator.py` so action-specific subreddit eligibility, keyword overrides, and flair-required posting constraints are enforced at runtime.
- added regression coverage in:
  - `/Users/nikitalienov/Documents/commentfront/backend/tests/test_reddit_program_store.py`
  - `/Users/nikitalienov/Documents/commentfront/backend/tests/test_reddit_program_orchestrator.py`
  - `/Users/nikitalienov/Documents/commentfront/backend/tests/test_reddit_program_api.py`
- pushed commit `3f14913` and verified production with a future-dated proof program `reddit_program_3b70ec160b`, then cancelled the proof vehicles after inspection.

## active todo
1. next implementation pass: add automatic subreddit user-flair setting in the browser executor so flair-required communities can become fully self-sufficient instead of config-gated only.

## current understanding
- the old compiler bias came from `target_subreddit = subreddit_pool[quota_index % len(subreddit_pool)]`, which resets inside each profile/day loop and starves later subreddits when `posts_min_per_day` is low.
- a real configurable system needs three separate knobs:
  - `allocation_weight`
  - `enabled_actions`
  - `requires_user_flair_for` with `profile_user_flairs`
- subreddit-specific keyword overrides belong in the same policy layer because discovery quality differs sharply between narrower health subs and broader support subs.
- flair-required subreddit handling must happen both in compilation and runtime eligibility checks, otherwise the program keeps assigning structurally bad work.

## proven wins
- targeted store tests pass, including policy-aware balancing and flair-gated eligibility.
- targeted orchestrator tests pass, including:
  - action-specific subreddit filtering
  - keyword overrides reaching generation inputs
  - configuration error when flair-required posting is unconfigured
- api validation accepts the new `subreddit_policies` surface.
- broader local verification is green:
  - backend: `99 passed`
  - frontend tests: `6 passed`
  - frontend build passed
  - frontend lint passed with only the pre-existing warnings in `/Users/nikitalienov/Documents/commentfront/frontend/src/App.tsx`
- production proof is green:
  - smoke create program `reddit_program_55d943f307` stored the new `subreddit_policies` field in live prod
  - proof program `reddit_program_3b70ec160b` compiled create-post rows as:
    - `reddit_catherine_emmar -> women`
    - `reddit_amy_schaefera -> WomensHealth`
    - `reddit_catherine_emmar -> women`
    - `reddit_amy_schaefera -> AskWomenOver40`
    - `reddit_amy_schaefera -> WomensHealth`
    - `reddit_catherine_emmar -> women`
  - this proves `AskWomenOver40` was assigned only to the flair-configured profile while `women` received the higher-weight share
  - both proof vehicles were cancelled cleanly after verification

## open risks
- this pass introduces configuration support and balancing logic, not automatic user-flair setting in the browser executor. `askwomenover40` still needs configured per-profile flair values to be operationally usable for posting lanes.
- reply/upvote discovery still remains opportunistic across configured subreddits; this pass improves eligibility and keywords, but does not yet add hard per-subreddit quota balancing for non-post actions.
