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
- github commit `e78ff2f8151f97dba867302516750cfa276698b5` shipped the canonical reddit execution contract, and railway deployed it successfully.
- github commit `1448195438f15a8e2172b4b22f512d134ef7357d` shipped the follow-up create-post verifier hardening, and railway deployment `7d7764dd-127e-4870-abf9-482557ee3141` is live with `status=SUCCESS`.
- the live backend now serves `/reddit/executions/preview`, `/reddit/executions/run`, and `/reddit/executions/{run_id}` from committed github state.
- the production proof matrix is complete for browse, open, upvote post, upvote comment, comment, reply, join, create post, and create post with attachment.
- clickable review surface for the final production proof packets lives in `docs/execution/reddit-unified-execution/proof-review.md`.

## active todo
- none. the requested implementation and production proof bar are complete.

## current understanding
- the safest cut was to keep reddit discovery and execution logic anchored in the existing orchestrator/runtime, and make the canonical execution spec the shared data model beneath one-shot runs and scheduled runs.
- the late production blockers were verifier- and artifact-shaped, not contract-shaped:
  - `create_post` could semantically fill the title field but still self-fail because the fallback verifier only trusted a global typed-text probe.
  - `create_post(with attachment)` could reach the right media compose surface and still fail if the uploaded image itself was too synthetic for reddit’s own media processor.
  - `upvote_post` proofs are most reliable on fresh profile/target pairs; stale already-upvoted pairs can still look visually active without producing a new vote mutation.

## proven wins
- the execution tracker for this task exists at `docs/execution/reddit-unified-execution/`.
- the backend now has a canonical reddit execution module, a persistent execution run store, preview/run/get endpoints, and mission execution routed through the shared temporary-program executor.
- local reddit backend coverage passed after the refactor, and passed again after the create-post hardening (`145 passed` across reddit execution/program/mission/session/login/rollout/convergence/bot suites).
- local curl verification passed for the full preview matrix across browse, open, join, upvote(post), upvote(comment), comment, reply, create_post(text), and create_post(with attachment).
- local live execution passed for browse(subreddit), open(subreddit), open(post), and open(comment) against real public reddit targets.
- the updated reddit advanced-tools ui rendered locally with explicit target kind, target strategy, comment-target input, and canonical execution controls for one-shot runs and missions.
- frontend verification stayed green after the backend follow-up patch: `npm test` passed and `npm run build` passed.
- the production proof matrix is fully evidenced:

| action | profile | subreddit | target / permalink | screenshot artifact | attempt id | final verdict | parameter proof |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `browse` | `reddit_amy_schaefera` | `womenshealth` | `https://www.reddit.com/r/womenshealth/` | `https://commentbot-production.up.railway.app/forensics/artifacts/38e324cd-afff-433b-9819-6e5915156569` | `0bfb42b9-cae3-426b-a588-7ef1c18cb633` | `success_confirmed` | `action.params.scrolls=3` browsed the explicit subreddit root |
| `open` | `reddit_catherine_emmar` | `Healthyhooha` | `https://www.reddit.com/r/Healthyhooha/comments/1r27x9r/help_insane_vaginitis_yes_a_doctor_already_saw_me/` | `https://commentbot-production.up.railway.app/forensics/artifacts/8626082a-709b-43f0-b0bd-f5f51a5dc488` | `9b80dda4-f61c-4bd6-a7e0-aca941428e36` | `success_confirmed` | `target.strategy=discover` resolved a live post before execution |
| `upvote_post` | `reddit_amy_schaefera` | `WomensHealth` | `https://www.reddit.com/r/WomensHealth/comments/1ojnobq/abortion_is_healthcare/` | `https://commentbot-production.up.railway.app/forensics/artifacts/44c34e3a-ee44-4466-addb-5367f8d6185e` | `d50fa465-4389-4162-9e10-0dc4dbbaf482` | `success_confirmed` | network bundle captured `UpdatePostVoteState` with `voteState=UP` |
| `upvote_comment` | `reddit_connor_esla` | `PCOS` | `https://www.reddit.com/r/PCOS/comments/1rcxg3c/transvaginal_ultrasound_for_pcos_diagnosis/o71lux0/` | `https://commentbot-production.up.railway.app/forensics/artifacts/48afdbb7-b747-4af6-a068-534dbf8d4598` | `72940094-1e81-4f1a-a3f6-c337801ddece` | `success_confirmed` | network bundle captured `UpdateCommentVoteState` with `commentId=t1_o71lux0` |
| `comment` | `reddit_jenee_waters` | `PCOS` | `https://www.reddit.com/r/PCOS/comments/1rmzwml/endocrinologist_says_pcos_doesnt_exist/` | `https://commentbot-production.up.railway.app/forensics/artifacts/47834797-c57a-484f-a5c8-b775c36f79aa` | `6f61b698-d96d-4876-b729-2ef78a647dbd` | `success_confirmed` | explicit `action.params.text` posted on the target thread; timeline shows composer activation and submit |
| `reply` | `reddit_neera_allvere` | `PCOS` | `https://www.reddit.com/r/PCOS/comments/1rmzwml/endocrinologist_says_pcos_doesnt_exist/o93b9bu/` | `https://commentbot-production.up.railway.app/forensics/artifacts/5e507e6e-045a-4aa7-9a52-bd189e0bf1f0` | `52e79ef8-90ec-4981-8c5c-ec5f28ce20ba` | `success_confirmed` | explicit `action.params.text` posted inline; timeline shows reply activation plus submit |
| `join` | `reddit_amy_schaefera` | `tea` | `https://www.reddit.com/r/tea/` | `https://commentbot-production.up.railway.app/forensics/artifacts/8d4e2db3-a93f-4790-a27d-15169d96114f` | `3596a2e7-d6fe-4b52-a720-c6e23dbde6b1` | `success_confirmed` | network bundle captured `UpdateSubredditSubscriptions` with `subscribeState=SUBSCRIBED` |
| `create_post` | `reddit_catherine_emmar` | `Healthyhooha` | `https://www.reddit.com/r/Healthyhooha/comments/1rrp5j8/did_anyone_else_feel_unusually_dry_for_a_few_days/` | `https://commentbot-production.up.railway.app/forensics/artifacts/cc1fc1a8-91e9-45c7-a20b-c81960a22ea2` | `14fa17a5-090f-4535-a5e0-25279b423281` | `success_confirmed` | `title` and `body` params persisted into the created thread permalink |
| `create_post` + attachment | `reddit_victor_saunders` | `test` | `https://www.reddit.com/r/test/comments/1rrp6tq/image_post_api_verification_retry/` | `https://commentbot-production.up.railway.app/forensics/artifacts/0ed03ad0-5ddf-4d01-9bdd-1edc3edc3d16` | `378f7f24-47e3-4321-9aac-cfb0d296dda7` | `success_confirmed` | `attachments[0].image_id=73b636436525` from `/media/upload` produced the created media-backed thread |

## open risks
- tiny synthetic images can still fail on reddit’s own media-processing step even when `/media/upload` and the compose flow are correct; production attachment proof should use a normal image asset.
- stale already-upvoted post/profile combinations can still be noisier than fresh pairs when the goal is a clean proof packet with a new vote mutation.
