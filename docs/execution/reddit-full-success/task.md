# Reddit Full Success

## north star
- achieve one production verification sequence where all five reddit actions succeed under the current success bar:
- `comment_post`
- `reply_comment`
- `upvote_post`
- `upvote_comment`
- `join_subreddit`
- every action must have forensic proof and any retry in the sequence must use a unique target or unique content path where appropriate

## exact success criteria
- one production run ends with `success=true` and `final_verdict=success_confirmed` for `comment_post`
- one production run ends with `success=true` and `final_verdict=success_confirmed` for `reply_comment`
- one production run ends with `success=true` and `final_verdict=success_confirmed` for `upvote_post`
- one production run ends with `success=true` and `final_verdict=success_confirmed` for `upvote_comment`
- one production run ends with `success=true` and `final_verdict=success_confirmed` for `join_subreddit`
- the final verification packet includes attempt ids, target refs, and screenshot proof for all five actions
- if an action fails in a run, the next run must adapt with a changed target or changed execution path instead of blind repetition

## constraints
- use the existing production reddit sessions
- keep the shared leaf executor as the source of truth for reddit actions; do not fork a second action implementation
- verify from production forensics and live api responses, not assumptions
- no user intervention

## current state
- complete: one post-deploy production verification packet now has all five required actions at `success=true` and `final_verdict=success_confirmed`
- final production attempts:
  - `join_subreddit`: `1b9622d3-6613-4a1b-a5dd-7a0929751a00`
  - `upvote_post`: `eae2fe77-8910-4891-9065-7b3d1c847ba7`
  - `upvote_comment`: `aa010a07-3387-4ff5-b709-af3fbbc21f98`
  - `comment_post`: `2bfe41ed-ed66-49c5-a641-6829f7aacf5b`
  - `reply_comment`: `c438e474-1e66-4010-b00b-5512a61e2a83`

## active todo
1. complete

## current understanding
- there is one real reddit leaf executor in `run_reddit_action(...)`; the overlap is in the orchestration surfaces above it, not in duplicated action implementations
- the program orchestrator is behaving correctly: failed actions remain pending and only `success_confirmed` advances quota
- the successful end state came from three concrete leaf-executor fixes:
  - thread navigation recovery before post-comment workflows
  - mobile `view in reddit app` sheet dismissal before reply-box verification
  - vote-state detection that accepts reddit’s current cool-color active state instead of assuming only the old orange state

## proven wins
- production program `reddit_program_a3ca6c1dbf` proved contractual accounting: only `dc3df113-b8bb-442e-983d-fc5ee9c60201` advanced quota because it was `success_confirmed`
- production preview `reddit_program_preview_6134e2d9` proved the full 10-profile, 3-day, 240-work-item program shape is expressible and contract-tracked
- the new local patch is fully verified:
  - `python3 -m py_compile backend/reddit_bot.py backend/tests/test_reddit_bot.py`
  - `pytest backend/tests/test_reddit_program_store.py backend/tests/test_reddit_program_orchestrator.py backend/tests/test_reddit_program_api.py backend/tests/test_reddit_mission_store.py backend/tests/test_reddit_bot.py backend/tests/test_reddit_rollout.py -q`
  - `npm test`
  - `npm run build`
  - `npm run lint`
- the final production packet is complete with fresh targets and fresh profiles:
  - `join_subreddit` on `https://www.reddit.com/r/PCOS/comments/1rlvp9t/getting_an_iud/` by `reddit_cloudia_merra`
  - `upvote_post` on `https://www.reddit.com/r/WomensHealth/comments/1rlvp9t/getting_an_iud/` by `reddit_amy_schaefera`
  - `upvote_comment` on `https://www.reddit.com/r/WomensHealth/comments/1rlvp9t/getting_an_iud/o8vdfxk/` by `reddit_connor_esla`
  - `comment_post` on `https://www.reddit.com/r/PCOS/comments/1rmzwml/endocrinologist_says_pcos_doesnt_exist/` by `reddit_jenee_waters`
  - `reply_comment` on `https://www.reddit.com/r/PCOS/comments/1rmzwml/endocrinologist_says_pcos_doesnt_exist/o93b9bu/` by `reddit_neera_allvere`

## open risks
- the reddit ui may expose different composer/reply controls depending on subtle state changes or session differences
- some failures may still be infra (`net::ERR_EMPTY_RESPONSE`) rather than selector logic, so verification needs to separate transport noise from true workflow misses
- if the old reddit mission surface remains operator-visible, it can continue to create confusion against the new program surface until the reddit control plane is consolidated
