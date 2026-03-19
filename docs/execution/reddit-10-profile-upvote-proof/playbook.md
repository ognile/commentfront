# playbook

## default execution loop
- define the terminal state before experiments begin
- verify the proof matrix itself after every rerun batch; do not trust stale assumptions about which profiles are still incomplete.

## stable tactics
- when mobile reddit thread verification keeps false-failing, use the production endpoint only for forensics and switch the actual proof run to the existing production session files.
- for mobile reddit post upvotes, anchor on the visible share row and target the vote column immediately to its left instead of trusting older hard-coded geometry.
- for mobile reddit comment upvotes, load the parent thread, scroll the exact comment permalink into view, and click the row-local vote region.

## failure patterns
- do not treat `thread did not load` as a real navigation failure until the screenshot and dom artifacts agree; this run produced repeated false negatives.
- do not keep retrying discovery mode for proof work when it already showed zero eligible targets.

## verification patterns
- require one screenshot artifact per successful action and keep the artifact path in the proof matrix.
- verify success from the stored matrix, not from terminal scrollback.
- for visual vote-state confirmation, compare a tight crop around the clicked vote region before and after the click.

## promotion rules
- promote only evidence-backed reusable lessons
