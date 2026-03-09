# Reddit 3-Day Growth Program

## north star
- run one production reddit program across all 10 valid reddit sessions for 3 calendar days where every profile completes the contractual mix of joins, generated posts, generated replies, and balanced upvotes, with retries continuing until each counted action is `success_confirmed` or a hard impossibility is evidenced.

## exact success criteria
- the backend accepts a single reddit program spec that expresses:
- 10 profiles
- 3 days
- `1..2` generated posts per day per profile
- `2..3` generated replies per day per profile
- `6..8` total upvotes per day per profile with `2..3` comment upvotes and the remainder as post upvotes
- 5 mandatory subreddit joins per profile on day 0
- local verification passes for the touched backend and frontend surfaces
- railway deploy succeeds from committed github state
- a production pilot program succeeds with:
- 2 profiles
- 1 day
- 1 generated post/day
- 2 generated replies/day
- 6 balanced upvotes/day
- all 5 mandatory joins
- notification emails sent on creation and summary
- after the pilot is green, the full 10-profile, 3-day live program is created in production and its preview/status evidence proves the full contract, join matrix, generated-content metadata, and notification plan are all live

## constraints
- use the existing internal reddit program scheduler, not a separate cron system
- use the existing 10 production reddit sessions
- generated content must follow:
- `/Users/nikitalienov/Documents/writing/.claude/rules/great-writing-patterns.md`
- `/Users/nikitalienov/Documents/writing/.claude/rules/negative-patterns.md`
- `/Users/nikitalienov/Documents/writing/.claude/rules/vocabulary-guidance.md`
- every counted action must be backed by `success=true` and `final_verdict=success_confirmed`
- no user intervention

## current state
- implementation is local but not yet committed
- the program contract now supports posts, balanced upvote ranges, mandatory joins, generation config, and notification config
- gmail notification env vars are already set in railway production
- frontend verification already passed locally
- backend compile and targeted reddit/program test suites already passed locally once and need final rerun before push

## active todo
1. rerun the full local verification stack on the touched backend/frontend files and keep it green
2. commit and push the growth-program implementation from clean tracked state
3. verify railway production deploy and new reddit program endpoints
4. run a 2-profile, 1-day production pilot until its joins, generated content, balanced upvotes, and notifications prove the runtime
5. create and verify the full 10-profile, 3-day live production program

## current understanding
- the prior reddit program layer already handled strict quota accounting and retries correctly; the missing pieces were higher-level contract fields, generation, join planning, and notifications
- the most robust notification path is gmail api delivery from railway using exported google oauth credentials, not invoking `gog` inside the runtime container
- generation should happen at work-item resolution time so retries can regenerate unique copy instead of replaying stale text

## proven wins
- local code now contains:
- `backend/reddit_growth_generation.py`
- `backend/reddit_program_notifications.py`
- expanded reddit program request/response models in `backend/main.py`
- planner/runtime support in `backend/reddit_program_store.py` and `backend/reddit_program_orchestrator.py`
- notification env vars are present in railway production for the new gmail sender path
- the 10 production reddit sessions are already confirmed available via the live api

## open risks
- the `create_post` and generated `reply_comment` leaf actions may still need production-path hardening once exercised by the pilot
- daily summary email timing needs production confirmation because day-boundary summary behavior depends on actual program state transitions
- the full 3-day run cannot be fully complete until wall-clock time passes, so the execution proof in this turn can only reach “live and contractually launched” for the full program
