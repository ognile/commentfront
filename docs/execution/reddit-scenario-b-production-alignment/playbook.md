# playbook

## default execution loop
- verify the live failure mode with actual program/evidence data before changing prompts or orchestration logic.
- freeze the approved artifact first, then map runtime behavior to that artifact. do not “wing it” from taste.
- patch the generator, orchestrator, operator surface, and tests as one system when the failure mode crosses all four layers.

## stable tactics
- mirror user-owned rule files into the repo and hash them so production can load stable exact contents without depending on out-of-repo paths.
- keep persona policy in one registry keyed by rollout profile, and emit the registry version into generation evidence.
- reserve targets as soon as a work item resolves them, before later work items execute.
- rank candidate reply targets with penalties for thread reuse, subreddit concentration, and repeated target authors.

## failure patterns
- first-ranked discovery causes dogpiles when all profiles see the same “best” thread.
- profile-local reuse guards are too weak for multi-profile rollouts.
- tests that hit the real generator are flaky and hide runtime regressions behind environment noise.
- operator views that only show success counts hide clustered bad behavior.
- deep reddit comment urls do not reliably land with the target comment already visible, so row-level reply/upvote controls can be missing until the bot actively scrolls the comment into view.
- comment upvote geometry is not “reply button minus a little bit”; on mobile reddit that often lands on the score, not the upvote arrow.
- interrupted reddit actions can leave zombie forensic attempts unless the action wrapper finalizes timeout/cancel paths explicitly.
- manual `run-now` is part of production too. if the same rollout can be processed twice at once, the evidence is invalid even when individual attempts succeed.
- on mobile reddit, an inline reply composer can exist even when the only obvious visible controls are `cancel` and `comment`; treat generic textbox roles as first-class editor surfaces instead of assuming textarea/contenteditable only.
- operator actions like `cancel` are part of runtime correctness too. if an older in-flight snapshot can save over a newer `cancelled` state, the rollout state model is untrustworthy even when individual attempts succeed.

## verification patterns
- unit and integration coverage must prove persona/rule hashes, semantic similarity rejection, and cross-profile target blocking.
- operator view must expose unsafe-rollout flags directly, not only bury them in raw evidence blobs.
- frontend verification should confirm the new persona/text/unsafe columns render correctly on the reddit ops page.
- when a row-level action is geometry-driven, test the fallback click sequence explicitly so future refactors do not silently drift back onto the score/downvote region.
- when a production scheduler exists, add explicit overlap tests for manual triggers too. scheduler serialization alone is not enough protection.
- when reply/comment typing fails after the composer opens, inspect whether the editor is a `role="textbox"` surface and whether `.fill()` is unsupported before assuming the composer never opened.
- when runtime state can change outside the worker loop, regression-test stale snapshot saves and mid-run cancellation explicitly instead of assuming the last `save_program(...)` call is harmless.

## promotion rules
- do not trust a rollout as methodology evidence unless it runs on the deployed scenario-b runtime and shows zero reply target collisions.
- do not promote generated text changes without both rule-hash evidence and persona metadata in the proof payload.
- keep open risks explicit in the tracker when a path is still manual-text rather than generated.
- if operator events show impossible bursts of `work_item_start` timestamps, assume program-level concurrency is broken until proved otherwise and replace the rollout after the lock fix.
- if a cancelled rollout ever reappears as `active`, treat all current live evidence as suspect until cancellation durability is fixed and a fresh rollout replaces the contaminated one.
