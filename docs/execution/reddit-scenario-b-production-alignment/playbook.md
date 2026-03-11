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

## verification patterns
- unit and integration coverage must prove persona/rule hashes, semantic similarity rejection, and cross-profile target blocking.
- operator view must expose unsafe-rollout flags directly, not only bury them in raw evidence blobs.
- frontend verification should confirm the new persona/text/unsafe columns render correctly on the reddit ops page.

## promotion rules
- do not trust a rollout as methodology evidence unless it runs on the deployed scenario-b runtime and shows zero reply target collisions.
- do not promote generated text changes without both rule-hash evidence and persona metadata in the proof payload.
- keep open risks explicit in the tracker when a path is still manual-text rather than generated.
