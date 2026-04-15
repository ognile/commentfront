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
- `85ee0d53` and `22864f6b` remain stuck on a composer-activation bug: healthy profiles reach step 4/5 but the comment field stays on placeholder and send stays hidden.
- the latest patch requiring a real editable composer has been pushed and is awaiting production proof.

## active todo
1. verify the latest composer-activation fix on `85ee0d53` and `22864f6b` in production.
2. if those two still stall, capture the next distinct state transition and patch the smallest reproducible input-activation gap.
3. run duplicate-control checks on recovered campaigns and confirm no duplicate successful profile assignments.
4. continue replaying the remaining failed campaigns until they are 100% delivered or explicitly proven blocked by the post/composer state itself.

## current understanding
- the original non-100% behavior was caused first by dead-session reuse, then by a wrong retry-all verifier, and now by a narrower composer activation failure on some post variants.
- healthy-profile proof is real: sampled winners authenticate cleanly and sampled failure-cluster profiles classify as logged-out/checkpoint/video-selfie.
- the remaining failures are no longer session-pool problems; they happen with healthy profiles after comments are opened.
- production history is the only trustworthy progress surface for retries; the in-memory retry-all status endpoint is not useful mid-run.

## proven wins
- `/sessions` and `/health/deep` in production now return auth-valid/auth-invalid fields and health metadata.
- live production session tests confirmed bad profiles are really bad and healthy controls are really healthy.
- `d30e8a3f` recovered all the way to 18/18 after the session-health and verifier fixes.
- `2926e310` recovered all the way to 19/19.
- post-deploy history shows `85ee0d53` and `22864f6b` no longer get freshly mislabeled as dead posts; they now fail explicitly at the empty-composer stage.

## open risks
- the composer-activation fix may still be insufficient for some Facebook post variants if the editable node appears outside the currently searched selectors.
- duplicate-proof still needs the final control pass on recovered campaigns.
- several campaigns still need replay after the last composer-focused production fix.
