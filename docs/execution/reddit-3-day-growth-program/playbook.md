# playbook

## default execution loop
- define the terminal state before experiments begin

## stable tactics
- add only proven reusable tactics here
- keep explicit target references intact across retries; only clear discovered targets that can be safely rediscovered

## failure patterns
- add recurring traps here
- retry logic that treats explicit targets like disposable discovery targets can silently destroy the only valid action input and turn a transient failure into a permanent `url is required` loop

## verification patterns
- add proof rules here
- inspect live work-item state after a retry failure, not just the leaf-action error; if the persisted target fields changed, the bug is in orchestration state mutation

## promotion rules
- promote only evidence-backed reusable lessons
