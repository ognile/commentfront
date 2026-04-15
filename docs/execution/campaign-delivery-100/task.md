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
- the retry comment-reconstruction fix is shipped locally and already produced fresh production successes with real `comment_excerpt` values on `85ee0d53`, `22864f6b`, `7d8d9a83`, `9777d21e`, `0c54f09a`, `52a5ac00`, `b1f7572d`, and `7a0ca07e`.
- the latest production proof isolated a second replay-orchestration bug: concurrent replay of the same campaign can post the same `job_index` twice when repeated manual retries overlap.
- a shared campaign replay claim is now implemented locally across manual bulk-retry, retry-all, and auto-retry, and the focused regression suite passed `23/23`.

## active todo
1. deploy the replay-claim guard together with the retry comment-reconstruction fix from committed github state.
2. verify railway is running the guarded replay code, then resume recovery only on the campaigns still below 100%.
3. rerun duplicate-control checks on all recovered campaigns and confirm no new duplicate successful `job_index` rows are created.
4. continue replaying the remaining failed campaigns until they are 100% delivered or explicitly proven blocked by the post/composer state itself.

## current understanding
- the original non-100% behavior was caused first by dead-session reuse, then by a wrong retry-all verifier, then by retry job reconstruction dropping comment text after `profile busy` rows, and finally by overlapping replay ownership on the same campaign.
- healthy-profile proof is real: sampled winners authenticate cleanly and sampled failure-cluster profiles classify as logged-out/checkpoint/video-selfie.
- the remaining failures are no longer session-pool problems; they now split into two concrete classes: blank-comment retries from poisoned history rows, and duplicate risk when the same campaign replay is started twice before the first one finishes.
- production history is the only trustworthy progress surface for retries; the in-memory retry-all status endpoint is not useful mid-run.

## proven wins
- `/sessions` and `/health/deep` in production now return auth-valid/auth-invalid fields and health metadata.
- live production session tests confirmed bad profiles are really bad and healthy controls are really healthy.
- `d30e8a3f` recovered all the way to 18/18 after the session-health and verifier fixes.
- `2926e310` recovered all the way to 19/19.
- post-deploy history and forensics show `85ee0d53` and `22864f6b` now retry with real comment text instead of blank placeholders.
- local proof now covers the replay guard: manual bulk-retry rejects a second overlapping run with `409`, and retry-all skips a claimed campaign instead of reposting it.

## open risks
- one already-posted duplicate remains in historical results for `7d8d9a83`; it is proof of the prior race, not yet proof that the deployed guard has eliminated future duplicates.
- several campaigns still need replay after the guarded deploy.
- if any campaign still stalls after both fixes, the next blocker must be proven from production forensics before changing more code.
