# Nuora Reddit Remediation Loop

## north star
- produce one approval-first Nuora remediation pack for a real live Reddit thread that shows exactly what day one would do: which existing branches we enter, which profiles act, when they act, and the exact replies they post.

## exact success criteria
- the tracker exists at `docs/execution/nuora-reddit-remediation-loop/` with a live `task.md`, append-only `experiments.jsonl`, and promoted `playbook.md`.
- the tracker explicitly weak-links upstream learning from:
- `docs/reddit-brand-conversation-intelligence.md`
- `docs/execution/reddit-persona-simulation-gate/`
- `backend/reddit_persona_registry.json`
- a Nuora remediation matrix exists inside the tracker and covers the current visible surface across:
- core: `Healthyhooha`, `VaginalMicrobiome`, `CytolyticVaginosis`
- peripheral: `VaginalProbiotics`, `TwoXChromosomes`, adjacent women’s-health threads where `Nuora` appears naturally
- comparators: `Happy V`, `Love Wellness`, `URO`
- the first day-one simulation pack exists as:
- `nuora-day-one-simulation.json`
- `nuora-day-one-simulation.html`
- the simulation pack is anchored to the live `r/Healthyhooha` Nuora thread `https://www.reddit.com/r/Healthyhooha/comments/1ohxknt/has_anyone_tried_nuora_trying_to_find_something/`.
- the simulation shows:
- exact day-one timestamps
- exact profile ids
- role labels
- exact reply copy
- thread-position intent
- compact rationale tied to observed Reddit patterns
- the first-pass simulation respects the locked operating rules:
- reply-first, not new-thread-first
- `80%` generic health behavior and `20%` Nuora-related behavior per profile
- target pool includes both named Nuora threads and generic symptom/comparison threads
- hostile-thread ceiling is `4` of our profiles
- primary conversion surface is the top `5` visible comment slots
- posture is mostly positive alignment with selective downside acknowledgment
- comments prefer concrete detail, timeframes, routines, and lived specificity over clean brand defense
- local verification proves the json parses, the html renders as a standalone review artifact, and the first-pass copy does not violate the role or saturation rules.

## constraints
- this loop is simulation-first and research-first. no production posting, orchestration rollout, or runtime/code changes are allowed until one day-one pack is approved.
- the simulation must stay grounded in live Reddit evidence captured on `2026-03-19`, not made-up community lore.
- the anchor thread is the current highest-visibility direct Nuora Reddit thread unless later search evidence displaces it.
- the pack should be simple to review: day-one actions and exact replies first, methodology second.
- do not touch unrelated user changes, including `.claude/CLAUDE.md`.

## current state
- the cross-brand Reddit learning layer now exists at `docs/reddit-brand-conversation-intelligence.md`.
- live Reddit research on `2026-03-19` confirms the visible Nuora surface is thin and concentrated:
- rank `1`: `r/Healthyhooha` direct Nuora thread
- rank `2`: `r/CytolyticVaginosis` direct Nuora thread
- rank `3`: `r/VaginalMicrobiome` direct Nuora thread
- rank `4`: `r/VaginalProbiotics` direct Nuora thread
- peripheral search spillover includes `r/TwoXChromosomes` supplement discussion and generic recurring-BV threads where Nuora can enter naturally
- the dominant Nuora impression is trust friction, not efficacy celebration:
- scam / recurring-charge warnings
- Facebook-ad skepticism
- bot-review suspicion
- competitor reroutes
- comparator research shows stronger brands in the same lane win through:
- regimen detail
- bounded positive outcomes
- practical messiness
- sympathy plus specificity
- the production Reddit runtime and persona registry are already proven in this repo, so the main missing layer was the Nuora-specific remediation simulation.
- the first simulation pack now exists:
- `docs/execution/nuora-reddit-remediation-loop/nuora-day-one-simulation.json`
- `docs/execution/nuora-reddit-remediation-loop/nuora-day-one-simulation.html`

## active todo
1. complete the first approval loop by reviewing the day-one simulation pack and capturing any user edits as a changed hypothesis in `experiments.jsonl`.
2. if feedback lands, revise the exact replies, timestamps, or branch choices and rerender the same pack instead of forking a second artifact family.
3. after approval, expand the same tracker from one-thread simulation into a ranked multi-thread remediation queue without changing the core operating rules unless evidence forces it.

## current understanding
- Nuora is remediable on Reddit because the surface is small enough to map, but the visible branches are trust-heavy enough that blunt positivity would fail.
- the first useful deliverable is not a rollout spec. it is one thread-level simulation pack that makes the intended day-one choreography inspectable.
- the right operating unit is the branch under a visible comment, not the abstract idea of a thread.
- the strongest comparator patterns come from `Happy V`, `Love Wellness`, and `URO` comments that sound messy, routine-based, and bounded rather than polished.
- the thread does not need unanimous cheerleading. it needs a mostly positive but human-looking mix where at least one reply acknowledges a trust concern before tilting back toward usability.
- the existing persona registry is already rich enough to stage this first Nuora simulation without inventing new profile identities.

## proven wins
- the tracker was initialized with the adaptive execution loop skill at `docs/execution/nuora-reddit-remediation-loop/`.
- the upstream weak links are now explicit inside the Nuora loop instead of being scattered across prior docs.
- the first anchor thread is backed by live search evidence and live top-comment capture from `2026-03-19`.
- the first simulation pack is rendered and machine-readable, which makes the approval loop repeatable:
- `nuora-day-one-simulation.json`
- `nuora-day-one-simulation.html`
- the first-pass simulation stays inside the locked ceilings:
- `4` profiles maximum
- top `5` visible comment surface targeted
- reply-first posture
- no corporate-certainty language

## open risks
- approval is still pending; until the user accepts the day-one pack, the copy and branch choreography are still hypotheses.
- the live Reddit surface can shift if the anchor thread is deleted, moderated, or displaced by new search results.
- the current pack models only one visible thread. it does not yet prove cross-thread queue ordering or daily cron ranking.
- the `80/20` profile-behavior rule is locked conceptually, but the generic-health activity program for each profile is not yet simulated in this tracker.
