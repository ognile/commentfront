# playbook

## default execution loop
- define the terminal state before experiments begin

## stable tactics
- add only proven reusable tactics here
- for remote-controller input changes, add focused shortcut tests first and only then run the full backend/frontend gate; the shortcut tests catch modifier-state and user-gesture bugs before the broader suite does.

## failure patterns
- add recurring traps here

## verification patterns
- add proof rules here
- if production `main` moves while a proof run is in flight, rerun the user-facing proof on the final deployed commit after railway marks that commit successful; do not claim success from a flow that straddled deployment cutover.

## promotion rules
- promote only evidence-backed reusable lessons
