# playbook

## default execution loop
- define the terminal state before experiments begin

## stable tactics
- add only proven reusable tactics here
- repo-local runtime state should either persist under `DATA_DIR` or be ignored locally; otherwise verification runs contaminate the worktree and make deploy commits harder to trust.
- when local browser control cannot launch because proxy state is missing, keep the smoke loop alive by proving the explicit error path through api output and the remote modal instead of treating the blocked environment as a code failure.
- when a websocket-backed control loop can encounter starlette closed-socket runtime errors, classify them as disconnects and break the loop; otherwise a dead client can spin error logs and hide the real lease state.

## failure patterns
- add recurring traps here

## verification patterns
- add proof rules here
- save command outputs under `docs/execution/<task>/artifacts/` rather than relying on terminal scrollback.
- separate code-level proof from environment proof: green tests/builds prove correctness, while local/prod smoke proves runtime prerequisites and operator-visible behavior.
- after a remote modal closes, expect the lease to remain alive at `viewer_count=0` until idle timeout or explicit stop; production verifiers should assert that detached state rather than assuming immediate lease deletion.

## promotion rules
- promote only evidence-backed reusable lessons
