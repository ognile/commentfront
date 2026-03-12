# reddit alignment proof hardening

## north star
- explicit reddit `comment`, `reply`, and `create_post` executions cannot post misaligned copy or pass proof on duplicate artifacts; production proof packets for the invalid attachment and reply attempts are superseded by aligned replacements; and the repo-local playbooks record the verification lesson so future execution work does not treat raw action success as content proof.

## exact success criteria
- explicit text-bearing reddit actions run a shared alignment preflight before posting, auto-repair failing manual drafts when possible, and block unrepaired drafts.
- reddit text-action results only reach `success_confirmed` when alignment validation, render-integrity validation, and single-compose / single-submit checks all pass.
- the bad proof packets `378f7f24-47e3-4321-9aac-cfb0d296dda7` and `52e79ef8-90ec-4981-8c5c-ec5f28ce20ba` are visibly invalidated and superseded in the proof review/task ledger.
- replacement production proofs exist for explicit `reply`, explicit `create_post`, and explicit `create_post` with attachment using real community-fit subreddits and rendered artifacts that match the effective posted copy exactly once.
- repo-local playbooks capture the general execution lesson and the reddit-specific proof rule.

## constraints
- no automated reddit edit/delete remediation in this pass; historical bad live artifacts are replaced and invalidated, not cleaned up.
- historical forensic attempts remain immutable; invalidation must layer on top through proof review metadata/docs.
- this hard gate is mandatory for all reddit text-bearing actions; there is no bypass path.
- production proof replacements must not use `r/test`.

## current state
- the backend hardening is implemented: explicit reddit text actions now run shared manual-content preflight, persist `content_preflight`/`alignment_validation`/`effective_action_params`, and require proof-aware verdicts.
- the reply execution path now uses a single-compose flow with explicit composer clearing, exact text verification, and duplicate/echo proof checks before `success_confirmed` is allowed.
- the full local reddit backend suite passes from the current tree, and the hardened local endpoint has been exercised with real preview/upload calls against copied production reddit sessions.
- the production proof review is still stale and still presents the invalid attachment/reply packets as accepted proof until the replacement runs are executed and documented.

## active todo
1. push the hardening commit to github and wait for railway to deploy the exact backend commit.
2. rerun production proof packets for explicit `reply`, explicit `create_post`, explicit `create_post` with attachment, and the spot-check explicit `comment`.
3. invalidate the old proof packets in the review surface and ledger, then link each replacement packet with `superseded_by`.
4. promote the execution/proof lesson into the reddit unified execution docs and proof review.

## current understanding
- generated reddit content already goes through persona, writing-rule, novelty, and context validation, but explicit/manual text bypasses that rigor.
- the attachment miss was not a transport bug; it was a methodology miss because an operator/test title was allowed to ship and then accepted as proof.
- the reply miss was both a runtime bug and a verifier bug: the leaf executor double-fired the reply path, and the proof system still called the attempt complete.
- the new hard gate works best when the operator draft is already community-native; when the draft is too generic or clinical, the review model will repair it, but the repaired copy still has to survive deterministic validation or the execution is blocked.

## proven wins
- the invalid proof artifacts are concretely reproducible from the local review pack:
  - `/private/tmp/commentfront_prod_proofs/review/create-post-attachment.png`
  - `/private/tmp/commentfront_prod_proofs/review/reply.png`
- the reply forensic timeline shows duplicate activation/type/submit events for attempt `52e79ef8-90ec-4981-8c5c-ec5f28ce20ba`.
- the attachment proof payload for attempt `378f7f24-47e3-4321-9aac-cfb0d296dda7` explicitly posted `image post api verification retry` into `r/test`.
- local verification passed after the hardening:
  - backend reddit suite: `169 passed`
  - python compile check on all modified backend files: passed
  - frontend build: passed
- the hardened local endpoint now rejects the bad attachment-title path even after repair attempts:
  - `/tmp/reddit_bad_post_preview.json`
- final local replacement payloads were selected with clean preflight results:
  - comment: `/tmp/comment_preview_final_candidate.json`
  - reply: `/tmp/reply_preview_candidate_jenee_v6.json`
  - text-only create_post: `/tmp/create_post_text_preview_jenee_pcos.json`
  - attachment upload + attachment preview: `/tmp/local_car_upload_response.json`, `/tmp/create_post_attachment_preview_local_final_v2.json`

## open risks
- the attachment replacement depends on `r/ATBGE` still accepting a normal image post from the selected profile at production run time.
- the old proof review surface and unified execution ledger still need explicit invalidation rows and superseding links before the methodology failure is visible from the repo alone.
