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
- `cd frontend && npm run lint` has only the pre-existing hook-dependency warnings in `/Users/nikitalienov/Documents/commentfront/frontend/src/App.tsx`.

## active todo
1. run `cd frontend && npm run lint` result capture and keep the known warnings recorded as pre-existing non-blockers.
2. commit the reply-target scroll fix and tracker updates.
3. push to github and verify railway deployment completion on the new commit.
4. verify the existing fresh rollout `reddit_program_1298b32d92` on the redeployed runtime.
5. prove at least one generated action on `reddit_program_1298b32d92` reaches `success_confirmed` with persona metadata, rule hashes, persisted url, screenshot artifact, and attempt id.
6. re-check operator view for zero unsafe rollout flags and zero duplicate reply-target/thread collisions after the redeploy.

## current understanding
- the original live failure mode was structural, not cosmetic: persona-less generation plus profile-local reuse controls allowed multiple profiles to pile into the same thread/comment with clone-shaped text.
- the right fix is not a single prompt tweak. it requires aligning generation, orchestration, proof surfaces, and operator visibility around the approved scenario-b artifact.
- the runtime now enforces scenario-b for generated paths (`create_post`, `reply_comment`). `comment_post` remains an explicit-text assignment path in this product today, so it is not yet a generated scenario-b lane.

## proven wins
- exact rule-file paths and hashes are now emitted by the runtime, so generation evidence can prove which rule corpus was active.
- semantic duplicate checks now reject same-thread and same-profile sibling text reuse.
- target selection now ranks candidates with thread/subreddit/author concentration penalties instead of always taking the first viable match.
- operator view and the reddit ops ui now surface unsafe rollout signals, persona role, case style, word count, generated text, and similarity/collision badges.
- all local regression gates relevant to this scope are green.

## open risks
- `comment_post` is still a manual-text path, not a generated scenario-b path.
- live subreddit behavior can still reject or constrain targets; operator visibility will expose that, but it does not eliminate subreddit-side moderation variability.
- the fresh replacement rollout is live, but it still needs one post-redeploy `success_confirmed` generated action to close the production proof loop cleanly.
