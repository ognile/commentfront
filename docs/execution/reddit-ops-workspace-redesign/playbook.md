# playbook

## default execution loop
- define the terminal state before experiments begin
- audit the live surface first so the loop starts from an actual screenshot, not taste-based guesses
- run layout experiments against real production data, then re-verify the shipped design on production before closing the loop

## stable tactics
- keep operator boards dense; remove decorative card chrome before adding more data
- render proof as short action links plus truncated destination text, not raw full urls pasted into cells
- put ongoing health visibility ahead of setup forms in ops sidebars
- use compact spoken labels for day/date state so controls scan quickly and do not wrap awkwardly

## failure patterns
- exposing correct data with weak hierarchy still fails the operator use case; "technically present" is not enough
- raw urls and full ids create false precision while destroying scannability
- setup/import affordances tend to take over the surface unless they are explicitly collapsed below monitoring tasks

## verification patterns
- always compare a baseline screenshot against the redesigned screenshot, not just before/after code diffs
- verify compact links remain clickable and still expose url, screenshot, attempt id, and verdict against live production data
- run both local browser verification and production browser verification for frontend ops surfaces

## promotion rules
- only promote ui rules that survived local tests, deployment, and production screenshot review
