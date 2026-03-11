# reddit subreddit constraints evidence

## north star
- explain, with live production evidence, why the current reddit rollout under-serves `women` and `askwomenover40`, and turn those findings into durable operating knowledge.

## exact success criteria
- prove whether the underrepresentation comes from rollout compilation, runtime discovery, or subreddit-side posting friction.
- record reusable lessons for future subreddit balancing and target discovery work.

## constraints
- use real production evidence from the active rollout `reddit_program_9283c65ece`.
- no guessed subreddit lore; every claim must tie back to live operator data, runtime code, or live reddit discovery surfaces.

## current state
- the active rollout is concentrated in `healthyhooha`, `womenshealth`, and `vaginalmicrobiome`; `women` and `askwomenover40` are materially underrepresented.
- this is not one failure mode:
  - `women` is mostly a compilation/allocation problem.
  - `askwomenover40` is a compilation plus runtime/discovery problem.
- the compiler currently assigns `create_post` by iterating the ordered subreddit list from the front for every profile/day. with `posts_min_per_day=1` and `posts_max_per_day=2`, the early subreddits dominate.

## active todo
1. carry these findings into the next runtime-balancing implementation so subreddit allocation becomes intentional instead of incidental.

## current understanding
- in the active program, compiled `create_post` work items only target `healthyhooha` and `askwomenover40`. `women`, `womenshealth`, and `vaginalmicrobiome` receive zero generated-post allocation because the round-robin resets per profile/day and never reaches later subreddits when daily post volume is low.
- `women` is not “failing to post” in the current rollout. it only has one completed `upvote_post` row and zero generated rows because the rollout barely assigned it any meaningful work.
- `askwomenover40` does receive generated-post allocation, but those rows are stalling at target resolution:
  - `reddit_amy_schaefera`, `reddit_cloudia_merra`, and `reddit_neera_allvere` are `blocked`
  - all three show `reddit target resolution timed out after 90s`
- the current keyword pack is a poor fit for `askwomenover40` and `women` discovery:
  - `askwomenover40` subreddit search with the runtime query returned `0` posts
  - `women` subreddit search with the same query returned `0` posts
  - by contrast, `womenshealth` returned `6`, `vaginalmicrobiome` returned `1`, and `healthyhooha` returned `23`
- `askwomenover40` also shows visible posting-friction evidence in its own hot feed: one of the top posts is `update: how to set your required user flair in r/askwomenover40`, which is a strong sign that posting there is more constrained than the narrower health subs.

## proven wins
- proved the allocation bias with live compiled data:
  - active program `reddit_program_9283c65ece`
  - compiled work items by pinned subreddit: `healthyhooha=30 create_post`, `askwomenover40=16 create_post`, all other pinned subreddit allocations live in discovery-time items with `subreddit=null`
- proved the live failure concentration:
  - `/status` failure summary shows `create_post=7`, with `by_subreddit.askwomenover40=3`
  - failure class is `target_resolution_timeout`
- proved the discovery mismatch with the same user-agent the orchestrator uses:
  - `askwomenover40 search = 0`
  - `women search = 0`
  - `womenshealth search = 6`
  - `vaginalmicrobiome search = 1`
  - `healthyhooha search = 23`
- proved `askwomenover40` has a visible flair/participation signal in the live community feed:
  - hot post: `update: how to set your required user flair in r/askwomenover40`
  - permalink: `https://www.reddit.com/r/AskWomenOver40/comments/1muws4r/update_how_to_set_your_required_user_flair_in/`

## open risks
- the rollout still has no hard subreddit-balancing policy, so future runs can drift into the same concentration even if reply dogpiles are fixed.
- `askwomenover40` may need explicit subreddit-specific handling for posting eligibility or flair requirements; this evidence pass does not yet implement that runtime adaptation.
- the current keyword pack is still biased toward health-specific communities and can starve broader support communities during discovery.
