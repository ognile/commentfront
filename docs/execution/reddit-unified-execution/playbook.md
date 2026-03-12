# playbook

## default execution loop
- define the terminal state before experiments begin

## stable tactics
- add only proven reusable tactics here
- carry canonical `execution_spec` directly on compiled work items, then derive any runtime-facing legacy fields from that spec at refresh time. this migrates stored programs without a destructive one-shot rewrite.
- execute ad hoc reddit runs by compiling a temporary single-day program and running it through the existing orchestrator. this reuses discovery, generation, verification, forensic evidence, and target-history logic instead of cloning it into a second executor.
- for `join_subreddit`, trust the network bundle more than timeline click events when the executor uses selector-based clicks. a successful `UpdateSubredditSubscriptions` mutation with `subscribeState=SUBSCRIBED` is the durable proof that the join actually happened.
- action-trace success is not proof if the rendered artifact is misaligned, synthetic, duplicated, or otherwise unacceptable on manual review.
- when a proof packet is later found invalid, keep the raw forensics immutable but invalidate it in the review layer and point to the superseding packet.

## failure patterns
- add recurring traps here
- if explicit program assignments do not persist `execution_spec`, the compiler silently falls back to the old `comment_post` default and preview/run requests collapse into the wrong shape.
- if `create_post` items derive `target_url` from the subreddit before execution, the real created post permalink gets wiped on save and proof rows lose their durable target reference.
- if the semantic title/body fallback only verifies via a global typed-text probe, reddit can visibly accept the field value while the executor still self-fails. confirm the exact field that was filled, not just the whole-page text surface.
- if a reply packet shows duplicated rendered text, the packet is invalid even if the run ended `success_confirmed`.
- if a post only passes on a synthetic or obviously off-community target, do not count it as production proof for a supported capability.

## verification patterns
- add proof rules here
- use `/reddit/executions/preview` to verify the full action/target capability matrix locally, including runtime-action mapping and target-mode derivation, before attempting live runs.
- use fixture sessions plus public reddit targets for local `run` coverage on browse/open flows; reserve auth-required proof for production with real persisted sessions.
- for ui verification, authenticate locally with generated jwt tokens in local storage, then inspect the reddit advanced-tools rail to confirm the canonical target kind/strategy controls and comment-target field are actually rendered.
- for `upvote_post`, prefer fresh profile/target pairs when the proof goal requires a clean `voteState=UP` mutation. stale already-upvoted pairs can look visually correct without producing the new mutation evidence you want.
- for reddit text actions, accept proof only when the screenshot, attempt json, payload, and response all tell the same story and the rendered artifact is community-aligned on sight.

## promotion rules
- promote only evidence-backed reusable lessons
