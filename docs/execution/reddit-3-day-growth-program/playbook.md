# playbook

## default execution loop
- define the terminal state before experiments begin

## stable tactics
- add only proven reusable tactics here
- keep explicit target references intact across retries; only clear discovered targets that can be safely rediscovered
- for `create_post`, persist the created reddit thread url as the target reference immediately; otherwise a successful browser action can be incorrectly rejected by higher-level quota accounting
- when a vote mutation comes back as `NONE` after an intended upvote, treat it as a missed already-active state and recover immediately; don’t let a detector miss exhaust the contractual action

## failure patterns
- add recurring traps here
- retry logic that treats explicit targets like disposable discovery targets can silently destroy the only valid action input and turn a transient failure into a permanent `url is required` loop
- item-level failure notifications in a large orchestrated program create operator-noise, not useful signal; hard failures should be summary-only unless they imply a real program-level emergency

## verification patterns
- add proof rules here
- inspect live work-item state after a retry failure, not just the leaf-action error; if the persisted target fields changed, the bug is in orchestration state mutation
- when a generated action is marked failed, compare the leaf result against the program contract; sometimes the browser path succeeded and the bookkeeping layer is what rejected it

## promotion rules
- promote only evidence-backed reusable lessons
