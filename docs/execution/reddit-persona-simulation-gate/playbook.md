# playbook

## default execution loop
- when the user wants to approve a content system, show the output in the exact operator surface they care about, not only as samples in markdown
- ground the approval artifact in one real production thread before inventing synthetic scenarios

## stable tactics
- keep the underlying post and timestamp pattern fixed when the review goal is to compare persona intensity
- pair a human-readable review surface with a machine-readable dossier so the approved output can map directly into implementation
- show rationale inline but keep it collapsible so the first read is about thread feel, not analyst notes
- encode in-thread social role and case policy explicitly in the dossier instead of assuming they will emerge from prose style alone
- show per-comment word counts or an equivalent visible length signal when the user is judging whether the thread is still too uniform

## failure patterns
- abstract comment lists are too easy to approve by mistake because they hide how repetitive the thread feels when rendered together
- proving rule-file paths without proving exact hashes is too weak when the user explicitly forbids paraphrase drift
- cadence diversity without role diversity still reads like one author wearing ten masks
- role diversity still fails if too many comments open with the same helper move or stay trapped in the same medium-length band

## verification patterns
- validate the dossier with a real json parser before calling the artifact done
- open the standalone html in a browser and verify the scenario switch plus all 10 rendered comments
- keep a screenshot of the rendered artifact so the review surface itself is evidence-backed

## promotion rules
- only promote approval-pack patterns that make content quality legible before production execution
