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
- `upvote_post`, `upvote_comment`, and `join_subreddit` have prior production successes
- the new program orchestrator is live and correctly tracks quotas, retries, and forensic evidence
- a fresh direct prod probe against `r/PCOS` exposed three concrete runtime issues:
  - `comment_post` can lose thread context and land on a subreddit feed (`e6acab9e-fd47-460f-9ada-82f68c689edb`)
  - `reply_comment` can be blocked by the mobile `view in reddit app` bottom sheet (`35a13ccd-089c-4690-ac6a-6791906319cf`)
  - `upvote_post` and `upvote_comment` can visually succeed while verification still misses the current cool-color vote state (`f4bf79f6-7b0d-4167-9a54-afb2e3d13d60`, `9492abf6-706c-43fa-82aa-fd2756701dc7`)
- the shared reddit leaf executor is now patched locally for those three runtime issues and local verification is green; production verification is next

## active todo
1. push the reddit leaf-executor hardening after the now-passing full local verification
2. wait for production deployment and verify the target commit is live
3. run a fresh unique direct-prod sequence across the five reddit actions with new pcos/healthyhooha targets and fresh profiles
4. if any action still fails, inspect its forensic screenshot/dom immediately and adapt with a changed profile or target instead of reusing the failed path
5. finish only when all five actions are green in the same final evidence packet

## current understanding
- there is one real reddit leaf executor in `run_reddit_action(...)`; the overlap is in the orchestration surfaces above it, not in duplicated action implementations
- the program orchestrator is behaving correctly: failed actions remain pending and only `success_confirmed` advances quota
- the remaining gap is in current production execution proof, not in local architecture:
  - thread navigation needs explicit context recovery before comment posting
  - reply flow needs to dismiss the mobile `view in reddit app` sheet before demanding an inline reply box
  - reddit vote verification cannot assume the old orange active state only

## proven wins
- production program `reddit_program_a3ca6c1dbf` proved contractual accounting: only `dc3df113-b8bb-442e-983d-fc5ee9c60201` advanced quota because it was `success_confirmed`
- production preview `reddit_program_preview_6134e2d9` proved the full 10-profile, 3-day, 240-work-item program shape is expressible and contract-tracked
- prior direct prod evidence already exists for `comment_post`, `upvote_post`, `upvote_comment`, and `join_subreddit`; the open issue is making the full success bar hold under the current end-to-end sequence
- the new local patch is fully verified:
  - `python3 -m py_compile backend/reddit_bot.py backend/tests/test_reddit_bot.py`
  - `pytest backend/tests/test_reddit_program_store.py backend/tests/test_reddit_program_orchestrator.py backend/tests/test_reddit_program_api.py backend/tests/test_reddit_mission_store.py backend/tests/test_reddit_bot.py backend/tests/test_reddit_rollout.py -q`
  - `npm test`
  - `npm run build`
  - `npm run lint`

## open risks
- the reddit ui may expose different composer/reply controls depending on subtle state changes or session differences
- some failures may still be infra (`net::ERR_EMPTY_RESPONSE`) rather than selector logic, so verification needs to separate transport noise from true workflow misses
- if the old reddit mission surface remains operator-visible, it can continue to create confusion against the new program surface until the reddit control plane is consolidated
