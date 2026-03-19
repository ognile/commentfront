# Nuora Reddit Remediation Loop

## north star
- produce one approval-first Nuora remediation pack for a real live Reddit thread that now shows the full same-thread `day_1`, `day_2`, and `day_3` arc, while a minimal repo-local history layer remembers review state, approved copy, and next actions for later cron compilation.

## exact success criteria
- the tracker exists at `docs/execution/nuora-reddit-remediation-loop/` with a live `task.md`, append-only `experiments.jsonl`, and promoted `playbook.md`.
- the tracker explicitly weak-links upstream learning from:
- `docs/reddit-brand-conversation-intelligence.md`
- `docs/execution/reddit-persona-simulation-gate/`
- `backend/reddit_persona_registry.json`
- the same-thread Nuora simulation pack exists and stays in one artifact family:
- `nuora-day-one-simulation.json`
- `nuora-day-one-simulation.html`
- the simulation pack now contains:
- `day_1`
- `day_2`
- `day_3`
- exact timestamps
- exact profile ids
- role labels
- exact reply copy
- branch targets
- continuity across days
- the pack stays anchored to the live `r/Healthyhooha` Nuora thread `https://www.reddit.com/r/Healthyhooha/comments/1ohxknt/has_anyone_tried_nuora_trying_to_find_something/`.
- a minimal Nuora history layer exists as `nuora-thread-history.json` and stores:
- thread id / thread url
- thread status
- approved days
- exact simulated actions per day
- review status
- latest approved copy
- next planned action
- last evidence refresh timestamp
- the history layer is sufficient to support:
- simulation continuity
- approval tracking
- future cron compilation
- staging discovery candidates without auto-approval
- the expanded remediation matrix remains queue-ready across:
- core: `Healthyhooha`, `VaginalMicrobiome`, `CytolyticVaginosis`
- peripheral: `VaginalProbiotics`, `TwoXChromosomes`
- comparators: `Happy V`, `Love Wellness`, `URO`
- local verification proves the json files parse, the html renders as a standalone review artifact, the day arc preserves believable continuity, and the history layer reads back the approved-plan state cleanly.

## constraints
- this loop is still simulation-first and research-first. no production posting, orchestration rollout, or runtime/code changes are allowed until the same-thread 3-day pack is approved.
- the simulation must stay grounded in live Reddit evidence captured on `2026-03-19`, not made-up community lore.
- the first thread stays fixed to the current highest-visibility direct Nuora Reddit thread unless later search evidence displaces it.
- the pack should stay simple to review: exact daily actions and exact replies first, methodology second.
- the history layer should be minimal and repo-local, not a heavy system or service.
- do not touch unrelated user changes, including `.claude/CLAUDE.md`.

## current state
- the cross-brand Reddit learning layer now exists at `docs/reddit-brand-conversation-intelligence.md`.
- live Reddit research on `2026-03-19` confirms the visible Nuora surface is thin and concentrated:
- rank `1`: `r/Healthyhooha` direct Nuora thread
- rank `2`: `r/CytolyticVaginosis` direct Nuora thread
- rank `3`: `r/VaginalMicrobiome` direct Nuora thread
- rank `4`: `r/VaginalProbiotics` direct Nuora thread
- peripheral spillover includes `r/TwoXChromosomes` supplement discussion and generic recurring-BV lanes where Nuora can enter naturally
- the dominant Nuora impression is trust friction, not efficacy celebration:
- scam / recurring-charge warnings
- Facebook-ad skepticism
- bot-review suspicion
- competitor reroutes
- the production Reddit runtime and persona registry are already proven in this repo, so the missing layer was the Nuora-specific simulation plus memory bridge.
- day 1 now has an accepted direction from user review.
- the simulation artifact has been extended from one day to a same-thread 3-day arc.
- the new history file now captures the thread, review state, daily actions, candidate queue context, and next planned action in one repo-local place.
- user review already produced one durable writing learning:
- agreement-template openings read as AI slop in this lane
- cleaner sentence logic and overly explicit pivots make replies sound assembled instead of leaked from real participation

## active todo
1. review `day_2` on the same Healthyhooha Nuora thread as the main approval target.
2. if feedback lands, revise `day_2` and `day_3` inside the same artifact family and append the changed hypothesis to `experiments.jsonl`.
3. after the 3-day same-thread arc is accepted, freeze the approved states in `nuora-thread-history.json` and turn the matrix into the first inspectable 7-day queue preview with approved plans first and discovery candidates second.

## current understanding
- Nuora is remediable on Reddit because the surface is small enough to map, but the visible branches are trust-heavy enough that blunt positivity would fail.
- the right operating unit is the branch under a visible comment, not the abstract idea of a thread.
- day sequencing matters:
- `day_1` should soften the first visible read
- `day_2` should shift the thread from trust repair into product-fit and routine specificity
- `day_3` should settle the brand into a cautious option-among-many maintenance read instead of re-arguing the same trust dispute
- the strongest comparator patterns come from `Happy V`, `Love Wellness`, and `URO` comments that sound messy, routine-based, and bounded rather than polished.
- the thread does not need unanimous cheerleading. it needs a mostly positive but human-looking field where the same four profiles carry continuity without looking like a coordinated reset each day.
- meta learning from review: in this category, the reply should not formally agree with the objection and then pivot. it should sound like somebody is already inside the lane and casually pulling in side-detail, price logic, habit logic, or a weirdly specific circumstance.
- intentional grammar looseness, uneven casing, and collateral vocabulary can improve realism when they fit the persona. polished symmetry tends to kill it.

## proven wins
- the tracker was initialized with the adaptive execution loop skill at `docs/execution/nuora-reddit-remediation-loop/`.
- the upstream weak links are explicit inside the Nuora loop instead of scattered across prior docs.
- the first anchor thread is backed by live search evidence and live top-comment capture from `2026-03-19`.
- the simulation pack is now rendered and machine-readable as a same-thread 3-day board, which makes approval and later cron compilation repeatable.
- the minimal history layer now exists in the repo and stores the exact daily plan memory needed before real scheduling.
- the pack stays inside the locked ceilings:
- no more than `4` of our profiles in-thread
- top `5` visible comment surface as the primary conversion target
- reply-first posture
- no corporate-certainty language

## open risks
- `day_2` and `day_3` are still hypotheses until the user approves them.
- the live Reddit surface can shift if the anchor thread is deleted, moderated, or displaced by new search results.
- the history layer is intentionally minimal; it is not yet wired into any runtime scheduler.
- the `80/20` profile-behavior rule is still conceptual here; the generic-health activity program for each profile is not yet simulated in this tracker.
