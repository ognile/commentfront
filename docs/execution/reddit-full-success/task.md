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
- `comment_post` is not yet reliable under program control; latest prod failures were `7a3da9cc-ff1f-448f-9c97-aa42152521d7` and `7ae461f0-1a8e-4839-b28b-56018c8a482e`
- `reply_comment` is not yet reliable under program control; latest prod failures were `9de63fa9-e6bf-40f8-a5f0-1ed7125ec1f5`, `2d7a5c45-f521-4dce-89c4-47ef7d940cb7`, `11be66f7-b08e-4dbf-acc4-033363671f19`, and `4a361fce-c278-4369-b33a-df64efa51b92`

## active todo
1. audit the failed prod `comment_post` attempts and identify the exact transition that loses the composer
2. audit the failed prod `reply_comment` attempts and identify why the target comment block is not exposing a resolvable reply control
3. patch the shared reddit leaf executor so both `comment_post` and `reply_comment` succeed under program control
4. re-run full local verification, commit, push, wait for deployment, and verify the fix in production
5. execute a fresh unique production sequence until all five actions are green in the same final evidence packet

## current understanding
- there is one real reddit leaf executor in `run_reddit_action(...)`; the overlap is in the orchestration surfaces above it, not in duplicated action implementations
- the program orchestrator is behaving correctly: failed actions remain pending and only `success_confirmed` advances quota
- the remaining gap is in the shared reddit action paths for comment posting and comment replying when invoked in current production conditions

## proven wins
- production program `reddit_program_a3ca6c1dbf` proved contractual accounting: only `dc3df113-b8bb-442e-983d-fc5ee9c60201` advanced quota because it was `success_confirmed`
- production preview `reddit_program_preview_6134e2d9` proved the full 10-profile, 3-day, 240-work-item program shape is expressible and contract-tracked
- prior direct prod evidence already exists for `comment_post`, `upvote_post`, `upvote_comment`, and `join_subreddit`; the open issue is making the full success bar hold under the current end-to-end sequence

## open risks
- the reddit ui may expose different composer/reply controls depending on subtle state changes or session differences
- some failures may still be infra (`net::ERR_EMPTY_RESPONSE`) rather than selector logic, so verification needs to separate transport noise from true workflow misses
- if the old reddit mission surface remains operator-visible, it can continue to create confusion against the new program surface until the reddit control plane is consolidated
