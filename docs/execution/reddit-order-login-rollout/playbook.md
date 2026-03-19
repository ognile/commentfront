# playbook

## default execution loop
- define the terminal state before experiments begin

## stable tactics
- add only proven reusable tactics here
- for reddit reruns, verify and reuse any existing session file for the deterministic reddit profile name before forcing another login. this avoids burning already-proven accounts into avoidable login failures.
- give reused-session verification a retry budget when the failure is `ERR_EMPTY_RESPONSE`, `ERR_ABORTED`, or timeout noise. a single transient protected-route miss is not strong enough evidence to discard a valid session.
- when a rollout false-blocks on post-login profile navigation, harden the action path first; `open_target` needed the same fresh-page `ERR_EMPTY_RESPONSE` recovery that login already had.

## failure patterns
- add recurring traps here
- `user_interaction_failed` on a brand-new account can still converge after escalating from `baseline_humanized` to `settle_home` or the email-identifier strategies.
- a single account returning the same `401 user-interaction-failed` across the full ladder and both reference bootstraps is materially different from normal convergence noise and should be treated as a likely external account-quality blocker until contrary evidence appears.

## verification patterns
- add proof rules here
- record both the rollout report and the per-account audit attempt id. the report tells you fleet status; the audit trail tells you whether the blocker is transport noise, verifier brittleness, or repeated credential rejection.
- if a reused session fails verification, probe it directly with both `verify_reddit_session_logged_in` and `open_target` before concluding the session is dead. `Denyse_Cowans` proved that reuse can fail once and still be valid on retry.

## promotion rules
- promote only evidence-backed reusable lessons
