# reddit scenario b production alignment

## north star
- replace the old reddit growth runtime with one that matches the approved `scenario_b` methodology in production: stable persona registry, exact writing-rule ingestion, program-wide target uniqueness, semantic anti-cloning, and operator proof that surfaces persona/collision/similarity state.

## exact success criteria
- generated `create_post` and `reply_comment` use the locked scenario-b persona registry and exact mirrored writing-rule files, and expose registry/rule hashes in generation evidence.
- reply target selection is program-wide safe: no duplicate `target_comment_url` reuse across profiles and no same-thread reply dogpiles.
- operator view exposes persona metadata, generated text, word count, similarity flags, and unsafe-rollout flags without losing existing proof fields.
- local verification passes on the targeted and broad reddit/backend suites, frontend tests, and frontend build.
- the old active rollout is paused/replaced only after the new committed github state is deployed and verified in production.

## constraints
- production deploys must come from committed github state only.
- keep the existing proof contract intact: persisted url, screenshot artifact, attempt id, `success_confirmed`.
- do not touch the user’s existing `.claude/CLAUDE.md` worktree change.
- no fake backward-compat shim that hides collisions; operator surfaces must show deficits honestly.

## current state
- implemented a repo-tracked global persona registry in `/Users/nikitalienov/Documents/commentfront/backend/reddit_persona_registry.json` with scenario-b personas for all ten rollout profiles.
- implemented persona loading and registry hashing in `/Users/nikitalienov/Documents/commentfront/backend/reddit_persona_registry.py`.
- mirrored the exact writing-rule files into `/Users/nikitalienov/Documents/commentfront/backend/rules/reddit/` and added hash/snapshot loading in `/Users/nikitalienov/Documents/commentfront/backend/reddit_writing_rules.py`.
- replaced `/Users/nikitalienov/Documents/commentfront/backend/reddit_growth_generation.py` so generation and validation now use persona case style, length bands, real rule snapshots, and semantic similarity checks across program/thread/profile scopes.
- upgraded `/Users/nikitalienov/Documents/commentfront/backend/reddit_program_orchestrator.py` so target reuse is program-wide, reply discovery scores alternatives instead of taking the first viable candidate, and generation evidence stores persona/rule/similarity metadata plus scoped text history.
- upgraded `/Users/nikitalienov/Documents/commentfront/backend/main.py` operator-view rows and the reddit ops frontend so unsafe rollout, persona, generated text, and similarity/collision signals are visible.
- local verification is green:
  - `pytest backend/tests/test_reddit_growth_generation.py backend/tests/test_reddit_program_orchestrator.py backend/tests/test_reddit_program_api.py -q`
  - `pytest backend/tests/test_reddit_bot.py backend/tests/test_reddit_program_store.py backend/tests/test_reddit_program_notifications.py backend/tests/test_reddit_program_orchestrator.py backend/tests/test_reddit_program_api.py backend/tests/test_reddit_growth_generation.py backend/tests/test_forensics.py -q`
  - `cd frontend && npm test`
  - `cd frontend && npm run build`
- production replacement already happened:
  - cancelled invalid active programs `reddit_program_99e59a50a8`, `reddit_program_380923c46b`, `reddit_program_011dba1ce7`, and `reddit_program_6f091d39c2`
  - created the fresh replacement rollout `reddit_program_1298b32d92`
  - verified operator-view in production exposes `unsafe_rollout_flags`, persona columns, similarity/collision fields, and the fresh run is consuming work
- first live generated attempt on the replacement rollout exists in production:
  - attempt `ef7eae78-c885-4cd2-a73b-44c01e5bf880`
  - action `reply_comment`
  - profile `reddit_jenee_waters`
  - persona metadata and rule hashes were persisted correctly
  - failure reason was executor-side: `Reddit Reply button not found`
- added a follow-up executor fix in `/Users/nikitalienov/Documents/commentfront/backend/reddit_bot.py` so reply/comment actions scroll the target comment into view before looking for row-level controls.
- added a second executor/runtime hardening pass in `/Users/nikitalienov/Documents/commentfront/backend/reddit_bot.py` so comment-row targeting prefers the exact permalink root, comment upvotes try real fallback vote points instead of one bad score click, and timed-out reddit actions finalize forensics instead of leaving zombie `running` attempts.
- narrowed operator duplicate-target unsafe flags in `/Users/nikitalienov/Documents/commentfront/backend/main.py` so repeated `upvote_post` rows do not show as rollout collisions.
- found and fixed a program-level concurrency bug in `/Users/nikitalienov/Documents/commentfront/backend/reddit_program_orchestrator.py`: overlapping `run-now` calls could process the same rollout concurrently because execution was only scheduler-serialized, not program-serialized. the runtime now uses a hard per-program async lock and `/reddit/programs/{id}/run-now` returns `409` when the same program is already executing.
- found a second live executor gap on the clean replacement rollout `reddit_program_65afd0d7c8`: scenario-b `reply_comment` generation reached production with correct persona/rule metadata, but the browser layer still missed the reply editor because the inline box exposed `cancel`/`comment` controls without matching our fillable-input surface. patched `/Users/nikitalienov/Documents/commentfront/backend/reddit_selectors.py` and `/Users/nikitalienov/Documents/commentfront/backend/reddit_bot.py` so reply/comment input detection now includes `role="textbox"` surfaces, active-editable detection treats textbox roles as real editors, and `_fill_first(...)` falls back to click+keyboard when `.fill()` is unsupported on a matched node.
- found a third live runtime bug on the next clean replacement rollout `reddit_program_3bac6f98e3`: cancelling a rollout could be undone by an older in-flight program snapshot saving back over the newer `cancelled` state, which is why `reddit_program_65afd0d7c8` reappeared as `active` after being cancelled. patched `/Users/nikitalienov/Documents/commentfront/backend/reddit_program_store.py` so stale runtime saves preserve newer `paused`/`cancelled` state, and patched `/Users/nikitalienov/Documents/commentfront/backend/reddit_program_orchestrator.py` so it re-checks live program status before every selected work item and stops immediately once the program is no longer `active`.
- `cd frontend && npm run lint` has only the pre-existing hook-dependency warnings in `/Users/nikitalienov/Documents/commentfront/frontend/src/App.tsx`.
- local verification is green after the lock fix too:
  - `pytest backend/tests/test_reddit_program_orchestrator.py backend/tests/test_reddit_program_api.py -q`
  - `pytest backend/tests/test_reddit_bot.py backend/tests/test_reddit_program_store.py backend/tests/test_reddit_program_notifications.py backend/tests/test_reddit_program_orchestrator.py backend/tests/test_reddit_program_api.py backend/tests/test_reddit_growth_generation.py backend/tests/test_forensics.py -q`
  - `python -m py_compile backend/reddit_program_orchestrator.py backend/main.py backend/reddit_bot.py backend/reddit_program_store.py`
  - `cd frontend && npm run build`
  - `cd frontend && npm run lint`
- local verification is green after the reply-input patch too:
  - `pytest backend/tests/test_reddit_bot.py backend/tests/test_reddit_program_orchestrator.py backend/tests/test_reddit_program_api.py -q`
  - `pytest backend/tests/test_reddit_bot.py backend/tests/test_reddit_program_store.py backend/tests/test_reddit_program_notifications.py backend/tests/test_reddit_program_orchestrator.py backend/tests/test_reddit_program_api.py backend/tests/test_reddit_growth_generation.py backend/tests/test_forensics.py -q`
  - `python -m py_compile backend/reddit_bot.py backend/reddit_program_orchestrator.py backend/main.py backend/reddit_program_store.py`
  - `cd frontend && npm run build`
  - `cd frontend && npm run lint`
- local verification is green after the stale-cancel fix too:
  - `pytest backend/tests/test_reddit_program_store.py backend/tests/test_reddit_program_orchestrator.py -q`
  - `pytest backend/tests/test_reddit_bot.py backend/tests/test_reddit_program_store.py backend/tests/test_reddit_program_notifications.py backend/tests/test_reddit_program_orchestrator.py backend/tests/test_reddit_program_api.py backend/tests/test_reddit_growth_generation.py backend/tests/test_forensics.py -q`
  - `python -m py_compile backend/reddit_program_store.py backend/reddit_program_orchestrator.py`
  - `cd frontend && npm run build`
  - `cd frontend && npm run lint`

## active todo
1. commit the stale-cancel runtime fix and tracker updates.
2. push to github and verify railway deployment completion on the new commit.
3. cancel the stale old rollout `reddit_program_65afd0d7c8` again and prove it stays cancelled while the current rollout keeps progressing.
4. because this is another runtime change, replace the current rollout too after the cancel-durability proof and create a fresh `10-profile, 3-day` replacement rollout on the newly deployed runtime.
5. prove generated actions on the fresh rollout reach `success_confirmed` with persona metadata, rule hashes, persisted url, screenshot artifact, and attempt id.
6. re-check operator view for zero unsafe rollout flags, one active rollout only, and no duplicate generated target/thread collisions after the replacement.

## current understanding
- the original live failure mode was structural, not cosmetic: persona-less generation plus profile-local reuse controls allowed multiple profiles to pile into the same thread/comment with clone-shaped text.
- the right fix is not a single prompt tweak. it requires aligning generation, orchestration, proof surfaces, and operator visibility around the approved scenario-b artifact.
- the runtime now enforces scenario-b for generated paths (`create_post`, `reply_comment`). `comment_post` remains an explicit-text assignment path in this product today, so it is not yet a generated scenario-b lane.
- the fresh rollout `reddit_program_8a78ca51d1` already proved one live generated `create_post` success (`95823e67-0ad5-4da7-9fa7-564b3f18e629`) with persona metadata, rule hashes, screenshot, and persisted reddit url, but that rollout is no longer acceptance-grade because overlapping `run-now` calls were able to start multiple work items concurrently before the lock fix.
- the current clean rollout `reddit_program_65afd0d7c8` proved a second important part of the stack before failing: live `reply_comment` generation is now context-aware and scenario-b aligned in production, with persona `amy_blunt_triage`, role `blunt_critique`, lowercase case style, `word_count=7`, exact rule hashes, thread/comment context, screenshot, and attempt id `fe6f5ad4-266a-4ad0-ba35-18ca35d07ba1`. the remaining miss was purely the reply editor surface, not the generator.
- the newest clean rollout `reddit_program_3bac6f98e3` has already gone further: it produced multi-profile `success_confirmed` actions, including generated `create_post` rows with persona metadata, rule hashes, and persisted reddit urls while operator unsafe flags remained clear. its remaining invalidation risk is runtime state integrity around cancellation, not scenario-b content quality.

## proven wins
- exact rule-file paths and hashes are now emitted by the runtime, so generation evidence can prove which rule corpus was active.
- semantic duplicate checks now reject same-thread and same-profile sibling text reuse.
- target selection now ranks candidates with thread/subreddit/author concentration penalties instead of always taking the first viable match.
- operator view and the reddit ops ui now surface unsafe rollout signals, persona role, case style, word count, generated text, and similarity/collision badges.
- all local regression gates relevant to this scope are green.
- the executor/runtime hardening pass is locally green too:
  - `pytest backend/tests/test_reddit_bot.py backend/tests/test_reddit_program_api.py -q`
  - `pytest backend/tests/test_reddit_bot.py backend/tests/test_reddit_program_store.py backend/tests/test_reddit_program_notifications.py backend/tests/test_reddit_program_orchestrator.py backend/tests/test_reddit_program_api.py backend/tests/test_reddit_growth_generation.py backend/tests/test_forensics.py -q`
  - `cd frontend && npm run build`
  - `cd frontend && npm run lint`
- the current replacement rollout also proved the operator unsafe flags stay at zero while scenario-b generation evidence lands in production; the invalidation reason is execution overlap, not persona/rule drift.
- the latest clean rollout also proved the operator unsafe flags still stay at zero while scenario-b reply generation lands in production; the invalidation reason is the pre-fix reply input surface, not target collisions or persona drift.
- the newest clean rollout also proved scenario-b generated `create_post` evidence can land `success_confirmed` in production with persisted reddit urls, persona ids, roles, and exact rule hashes; the remaining invalidation reason is cancel-state durability, not generation drift.

## open risks
- `comment_post` is still a manual-text path, not a generated scenario-b path.
- live subreddit behavior can still reject or constrain targets; operator visibility will expose that, but it does not eliminate subreddit-side moderation variability.
- production proof is still open until the stale-cancel runtime fix is deployed, validated against real cancellation behavior, and then exercised on a fresh rollout.
