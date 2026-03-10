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
- the growth-program implementation is committed and deployed in production on commit `04f847aa8881520fd681cd2d2e3be218fa7c6eb4`
- the prior blocked railway queue is resolved; the correct github-backed backend deployment is now live
- prod direct `create_post` is confirmed working on the live build
- the old 2-profile pilot is exhausted because it consumed retries before the create-post fix was live, so it is no longer a clean proof vehicle
- the fresh 1-profile flight-check proved that `r/WomensHealth` is a bad target for `reddit_mary_miaby` for posting/replying because reddit shows a community-ban banner there
- the next patch is locally green and does three things:
- classifies create-post community bans explicitly instead of flattening them into generic verification failures
- reroutes quota work away from profile-community blocks instead of permanently blocking the item on the first banned subreddit
- anchors `upvote_comment` on the parent thread plus target-comment context instead of the brittle permalink-only surface

## active todo
1. deploy the community-reroute + thread-anchored comment-upvote patch from committed github state
2. verify direct prod behavior for:
- `create_post` community-ban classification
- `upvote_comment` on a target comment inside a parent thread
3. create a fresh 1-profile production flight-check with safe subreddits/targets so one profile completes:
- mandatory joins
- create post
- upvote post
- upvote comment
- reply comment
4. collect screenshot proof for each successful action from that single-run flight-check
5. run a fresh 2-profile, 1-day production pilot until joins, generated posts, replies, balanced upvotes, and notifications are all green under the program runner
6. create and verify the full 10-profile, 3-day live production program

## current understanding
- the prior reddit program layer handled strict quota accounting and retries correctly; the missing pieces were higher-level contract fields, generation, join planning, and notifications
- gmail api delivery from railway is the correct notification path and is already working in production
- generation should happen at work-item resolution time so retries can regenerate unique copy instead of replaying stale text
- the `create_post` blocker was a real mobile composer mismatch, not a reddit posting prohibition
- once deployed, the semantic create-post fix works in prod; the remaining failures are now narrower and should be treated as separate runtime lanes, not as a general program failure
- community restrictions are profile-and-subreddit specific, so the orchestrator has to adapt away from bad subreddit/profile combinations instead of treating them as global runtime failure
- `upvote_comment` needs thread-context execution plus comment-context anchoring; the comment permalink alone is not a reliable action surface

## proven wins
- local code now contains:
- `backend/reddit_growth_generation.py`
- `backend/reddit_program_notifications.py`
- expanded reddit program request/response models in `backend/main.py`
- planner/runtime support in `backend/reddit_program_store.py` and `backend/reddit_program_orchestrator.py`
- notification env vars are present in railway production for the new gmail sender path
- the 10 production reddit sessions are already confirmed available via the live api
- railway production is live on commit `04f847aa8881520fd681cd2d2e3be218fa7c6eb4`
- direct prod `create_post` succeeded on attempt `a1afcd12-b72c-44dd-abb8-9d756bc4861a`
- local verification for the new reroute/upvote patch passed:
- backend compile clean
- `59` reddit/program tests green
- frontend `npm test`
- frontend `npm run build`
- frontend `npm run lint` warning-only

## open risks
- the generated `reply_comment` and `upvote_comment` program lanes still need fresh production proof after the new patch is deployed
- daily summary email timing needs production confirmation because day-boundary summary behavior depends on actual program state transitions
- the full 3-day run cannot be fully complete until wall-clock time passes, so the execution proof in this turn can only reach “live and contractually launched” for the full program
