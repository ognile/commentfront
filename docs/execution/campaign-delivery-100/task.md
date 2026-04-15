# campaign delivery 100

## north star
- campaign delivery reaches 100% for every recoverable campaign from 2026-04-13 onward, with no duplicate successful profile use on the same post and no dead session ever assigned to work.

## exact success criteria
- production `/sessions` and `/health/deep` reflect auth health, not cookie presence, for sampled bad and good profiles.
- queue assignment and retry paths only use healthy or infra-unknown sessions, never checkpoint/logged-out/video-selfie sessions.
- no campaign is marked dead because of non-step-1 verifier noise.
- no profile is used successfully twice on the same campaign.
- every campaign from 2026-04-13 onward is either 100% delivered or has explicit production evidence of a remaining external blocker.

## constraints
- no new service or datastore; fixes stay inside the current backend stack.
- production changes must be proven by local tests, deployed through github, railway success, and live production verification.
- do not mutate scarce state for debugging unless there is rollback confidence; prefer read-only or replay-safe verification.

## current state
- auth-health gating is shipped and proven in production.
- retry-all dead-post inference bug is shipped and proven in production.
- recovered campaigns now include `d30e8a3f` at 18/18 and `2926e310` at 19/19.
- the latest production proof isolated a narrower retry-data bug: some failed jobs inherit an empty comment after an early `profile busy` row, so retries keep attempting blank submissions and the composer stays on placeholder.
- the textarea native-setter fallback is shipped but is no longer treated as the primary fix path for these campaigns.

## active todo
1. deploy the retry comment-reconstruction fix that prefers `campaign.comments[job_index]` over sparse historical rows.
2. replay `85ee0d53` and `22864f6b` from the corrected state and verify they no longer burn blank comments.
3. run duplicate-control checks on recovered campaigns and confirm no duplicate successful profile assignments.
4. continue replaying the remaining failed campaigns until they are 100% delivered or explicitly proven blocked by the post/composer state itself.

## current understanding
- the original non-100% behavior was caused first by dead-session reuse, then by a wrong retry-all verifier, and now by a retry job reconstruction bug that can drop comment text after `profile busy` rows.
- healthy-profile proof is real: sampled winners authenticate cleanly and sampled failure-cluster profiles classify as logged-out/checkpoint/video-selfie.
- the remaining failures are no longer session-pool problems; they happen when retry logic replays a blank comment into an otherwise healthy session.
- production history is the only trustworthy progress surface for retries; the in-memory retry-all status endpoint is not useful mid-run.

## proven wins
- `/sessions` and `/health/deep` in production now return auth-valid/auth-invalid fields and health metadata.
- live production session tests confirmed bad profiles are really bad and healthy controls are really healthy.
- `d30e8a3f` recovered all the way to 18/18 after the session-health and verifier fixes.
- `2926e310` recovered all the way to 19/19.
- post-deploy history shows `85ee0d53` and `22864f6b` no longer get freshly mislabeled as dead posts; they now fail explicitly at the empty-composer stage.

## open risks
- there may still be a secondary composer-activation issue on some Facebook variants after the blank-comment bug is removed, but it is no longer the leading explanation for the current failed jobs.
- duplicate-proof still needs the final control pass on recovered campaigns.
- several campaigns still need replay after the retry-comment reconstruction fix.
