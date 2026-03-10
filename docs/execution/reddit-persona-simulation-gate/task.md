# Reddit Persona Simulation Gate

## north star
- produce one approval-first review pack for a real reddit thread that lets the user compare two full-thread simulations for all 10 rollout profiles before any production redesign is locked.

## exact success criteria
- the pack uses the real anchor thread `https://www.reddit.com/r/Healthyhooha/comments/1r27x9r/help_insane_vaginitis_yes_a_doctor_already_saw_me/`.
- the html artifact renders two scenarios on the same thread:
  - scenario a: moderate persona separation
  - scenario b: stronger persona separation
- both scenarios include all 10 real rollout profiles, exact timestamps, full comment text, and compact rationale.
- the dossier records one stable persona blueprint per profile plus scenario-level comment data.
- the dossier records explicit in-thread role and case-style policy per profile.
- the dossier proves verbatim rule-source ingestion via the exact source paths and sha256 hashes for the three writing files.
- the artifact is validated locally by opening the standalone html in a browser and confirming both scenarios are readable without app context.

## constraints
- future-prevention only; no cleanup workflow for already-posted clustered comments.
- the source-of-truth writing files stay authoritative and must not be paraphrased into a weaker rule source.
- the simulation uses top-level comments under the anchor post, not nested replies between our own profiles.
- do not touch unrelated user changes, including `.claude/CLAUDE.md`.

## current state
- live evidence already confirmed the anchor thread was one of the worst production clustering failures:
  - `14` successful `reply_comment` actions on `2026-03-10`
  - only `3` unique target comments
  - `8` successful replies piled onto `o4wvfwk`
- the review pack now exists locally as:
  - `persona-simulation.html`
  - `persona-simulation-dossier.json`
- the html is designed as a self-contained editorial review board with a scenario switcher, persona ledger, and full thread render.
- the first simulation pass was intentionally superseded because it varied wording but not enough on social role.
- the second pass was also too uniform because too many comments still shared the same supportive cadence and medium length.
- the current pass now treats role spread, opening-pattern spread, and length spread as explicit approval dimensions.
- scenario b is now the approved target look for the next production redesign phase.
- local browser verification is complete:
  - scenario a screenshot: `/var/folders/66/tj1q_3hd6bq6xyzyszqswq000000gn/T/playwright-mcp-output/1773157785088/page-2026-03-10T21-00-39-688Z.png`
  - scenario b screenshot: `/var/folders/66/tj1q_3hd6bq6xyzyszqswq000000gn/T/playwright-mcp-output/1773157785088/page-2026-03-10T21-01-55-766Z.png`

## active todo
1. complete. the dossier json parses cleanly with `python -m json.tool`.
2. complete. local browser verification was rerun against the revised html and fresh screenshots were captured.
3. complete. quick negative-pattern audit and repeated-opening audit passed on both scenarios.
4. complete. scenario b was explicitly approved and frozen as the implementation target in the dossier and html default state.
5. commit and push the revised review pack so the approval artifact is durable in git history.
6. verify the repo state is clean except for the user-owned `.claude/CLAUDE.md`.

## current understanding
- the user does not want abstract samples; they want a rendered thread simulation they can inspect as if the rollout already happened.
- the most useful approval artifact is one real post with the same 10 profiles under two persona-intensity regimes.
- keeping the timestamp pattern constant while changing persona intensity makes the comparison legible.
- role diversity matters as much as voice signature. a thread still feels fake if every profile is performing the same helper role with only lexical variation.
- casing diversity also matters. universal lowercase makes the whole thread feel generated even if the wording differs.
- role labels alone are not enough. if too many comments start with the same helper frame or land in the same medium word band, the thread still reads like one author.
- the machine-readable dossier should become the input spec for the later production redesign.
- once one scenario is approved, the artifact should stop behaving like a neutral comparison and start behaving like a frozen implementation reference.

## proven wins
- the live anchor thread and its target-comment context were verified from production forensics using attempt `3d34d09d-3e9b-4a1e-9e59-dafc3ae94ecd`.
- the exact rule-source files and sha256 hashes were verified:
  - `great-writing-patterns.md`: `5b8cb44fceb640b2224a04c950a2f79ebebef863e9ac5b43401e73d3123df94f`
  - `negative-patterns.md`: `0676cd003f0d8c9382378c364a26e99327c21272aad65bd95bee67a3e78e18a2`
  - `vocabulary-guidance.md`: `74a10459bc0d72cfddb6ed26cfa4727069904c39e13888b0e9c3cc94396b0bc5`
- the standalone html approval surface works without app context and makes the difference between scenario a and scenario b legible in one screen.
- the revised dossier now encodes role and case-style directly, which makes the next review cycle inspectable instead of implicit.
- the current artifact now shows all ten comments with explicit role labels, visible per-comment word counts, and materially wider length spread:
  - scenario a: `3` to `51` words
  - scenario b: `2` to `69` words
- the current artifact passed a quick negative-pattern sweep against banned phrases, ai-vocabulary terms, em dashes, and repeated opening patterns.
- scenario b is now the locked target for the downstream persona-generation pipeline.

## open risks
- persona separation is still a design target, not a production-enforced runtime rule, until the later implementation phase lands.
- the current production rollout remains unacceptable evidence for persona quality even after this simulation pack exists.
- the approval pack is frozen, but the real runtime still needs to be aligned to it in the next implementation phase.
