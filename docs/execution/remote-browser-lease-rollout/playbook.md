# playbook

## default execution loop
- define the terminal state before experiments begin

## stable tactics
- add only proven reusable tactics here
- repo-local runtime state should either persist under `DATA_DIR` or be ignored locally; otherwise verification runs contaminate the worktree and make deploy commits harder to trust.
- when local browser control cannot launch because proxy state is missing, keep the smoke loop alive by proving the explicit error path through api output and the remote modal instead of treating the blocked environment as a code failure.

## failure patterns
- add recurring traps here

## verification patterns
- add proof rules here
- save command outputs under `docs/execution/<task>/artifacts/` rather than relying on terminal scrollback.
- separate code-level proof from environment proof: green tests/builds prove correctness, while local/prod smoke proves runtime prerequisites and operator-visible behavior.

## promotion rules
- promote only evidence-backed reusable lessons
