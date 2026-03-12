# reddit unified execution contract

## north star
- reddit has one canonical execution contract for explicit actors, typed targets, typed actions, and verification rules; the one-shot api, missions, and program runtime all execute through that contract; local verification passes; production deployment is live; and production proof packets exist for every in-scope reddit action.

## exact success criteria
- new canonical reddit execution endpoints exist for preview, run, and run lookup.
- the backend enforces one capability matrix for actor/action/target compatibility.
- reddit program work items carry canonical execution specs and the runtime executes them through the shared executor.
- reddit mission execution uses the same shared executor.
- the reddit frontend tools submit canonical execution payloads, including correct comment-target payloads.
- local backend tests pass for capability validation, canonical result normalization, program compilation, and executor behavior.
- local frontend build passes and the updated reddit execution ui renders correctly.
- production is updated from committed github state and the live backend serves the new execution endpoints.
- proof artifacts are recorded under this task for browse, open, upvote post, upvote comment, comment, reply, join, create post, and create post with attachment.

## constraints
- actor scope is explicit reddit profiles only in v1.
- flair and flair-definition creation are out of scope for this delivery.
- proof must use real reddit sessions and real production endpoints, not fixture-only verification.
- do not regress the existing reddit operator/program reporting surfaces.

## current state
- reddit already has strong action primitives, target discovery, program scheduling, forensic evidence, and a frontend utility rail.
- the current backend contract is split across legacy fields like `url`, `target_url`, `target_comment_url`, and action-specific request models.
- the current frontend advanced tools do not expose the full backend reddit action contract and mis-shape comment-target payloads.
- no canonical reddit execution run store exists yet.

## active todo
1. deploy the canonical reddit execution contract from committed github state and verify the live endpoints are serving the new shape.
2. produce production proof packets for browse, open, upvote post, upvote comment, comment, reply, join, create post, and create post with attachment.

## current understanding
- the safest cut is to keep reddit discovery and execution logic anchored in the existing orchestrator/runtime, and make the canonical execution spec the shared data model beneath one-shot runs and scheduled runs.

## proven wins
- the execution tracker for this task exists at `docs/execution/reddit-unified-execution/`.
- the existing reddit-focused backend test suites passed before refactor, confirming a stable baseline for bot, api, missions, sessions, rollout, convergence, and program runtime behavior.
- the backend now has a canonical reddit execution module, a persistent execution run store, preview/run/get endpoints, and mission execution routed through the shared temporary-program executor.
- local reddit backend coverage passed after the refactor, including new tests for capability matrix enforcement, execution-spec migration, result normalization, mission creation, program compilation, and orchestrator behavior.
- local curl verification passed for the full preview matrix across browse, open, join, upvote(post), upvote(comment), comment, reply, create_post(text), and create_post(with attachment).
- local live execution passed for browse(subreddit), open(subreddit), open(post), and open(comment) against real public reddit targets.
- the updated reddit advanced-tools ui rendered locally with explicit target kind, target strategy, comment-target input, and canonical execution controls for one-shot runs and missions.

## open risks
- production proof depends on at least one valid reddit session with permissions compatible with join, comment, reply, and post creation.
- deployment verification may require polling production until the new endpoints appear because the app does not expose a commit hash endpoint today.
