# reddit order login rollout

## north star
- status=pass only when production has 10 valid reddit sessions for:
- dalila_danzy
- jewel_resendes
- luana_cutting
- darci_hardgrove
- kattie_nicklas
- odette_linke
- fredericka_worsley
- denyse_cowans
- fairy_rodgers
- lashawnda_gallion
- and the production rollout report for this order shows `active_sessions_count=10` and `blocked_accounts_count=0`.

## exact success criteria
- local backend tests for reddit import, email challenge, audit classification, login bot, and rollout all pass.
- a local rollout of the exact order file completes with `total_accounts=10`, `active_sessions_count=10`, `blocked_accounts_count=0`.
- production deploy is verified on the pushed github commit before production rollout starts.
- production rollout of the exact order file completes with `total_accounts=10`, `active_sessions_count=10`, `blocked_accounts_count=0`.
- production `/reddit/credentials` and `/reddit/sessions` contain the exact 10 order usernames as valid, additive records.

## constraints
- use the existing system proxy in production. do not set `proxy_id`.
- do not add reddit tab ui or new public api surface for this one-time rollout.
- use the existing rollout endpoints only:
- `/reddit/sessions/bulk-create`
- `/reddit/sessions/bulk-create/{run_id}`
- keep the rollout report contract stable unless a proven blocker requires an internal-only hardening change.

## current state
- local repo already contains dirty backend changes for 4-field reddit import, source-tag defaults, rollout wiring, audit classification, and email challenge resolution.
- local repo already contains targeted backend tests for the new behavior.
- tracker files existed but were still placeholder-only before this update.
- production health endpoint returned healthy on 2026-03-19.
- production has an effective system proxy at `209.145.57.39:44416`.
- production currently has 10 valid reddit fixture sessions, but none of the 10 order usernames exist there yet.
- the order file is `/Users/nikitalienov/Downloads/3-Business/Orders/business-order-transaction-details.txt`.
- the order file contains 10 reddit credentials in 4-field format:
- `username:password:email:email_password`

## active todo
1. ship the backend hardening changes, verify the production deploy commit, and run the exact 10-account production rollout.
2. confirm whether `Lashawnda_Gallion` is a true external blocker in production or only a local exhaustion artifact.

## current understanding
- the execution-critical gaps were importer field-count widening and outlook-backed reddit email challenge handling.
- the rollout/report machinery already existed and is the correct stable execution surface.
- because production has no overlap with the order usernames, this rollout is additive rather than a convergence/reconciliation task.
- the correct operating mode is `fixture=false`, `source_label=<exact order path>`, `max_create_attempts=3`, no `proxy_id`.
- reruns need to reuse already-valid reddit sessions before forcing another login attempt, otherwise the same account can be churned into avoidable login failures.
- reused-session verification also needs retry tolerance for transient `ERR_EMPTY_RESPONSE` / `ERR_ABORTED` noise before falling back to a fresh login.

## proven wins
- production proxy resolution is available via the existing system proxy response from `/proxies`.
- production reddit inventory was checked and confirmed to have zero overlap with the 10 order usernames.
- the local dirty test additions already describe and defend the intended import and rollout behavior for 4-field lines.
- local targeted backend tests passed after the hardening work:
- `pytest backend/tests/test_reddit_rollout.py backend/tests/test_reddit_bot.py backend/tests/test_reddit_login_bot.py`
- result: `109 passed`
- local rollout `20260319T153013Z_7568d050` on 2026-03-19 completed with:
- `total_accounts=10`
- `active_sessions_count=9`
- `blocked_accounts_count=1`
- the 9 successful usernames were:
- `Dalila_Danzy`
- `Jewel_Resendes`
- `Luana_Cutting`
- `Darci_Hardgrove`
- `Kattie_Nicklas`
- `Odette_Linke`
- `Fredericka_Worsley`
- `Denyse_Cowans`
- `Fairy_Rodgers`
- `Kattie_Nicklas` was recovered by the new `open_target` fresh-page retry path; the prior false block was action-only, not login-only.
- `Denyse_Cowans` was recovered by the new existing-session reuse path plus retryable verification.
- the only local blocker after exhausting the full strategy ladder was `Lashawnda_Gallion`, with rollout result:
- `failure_bucket=user_interaction_failed`
- `attempt_id=20260319T154523Z_standalone_reddit_identity_lashawnda_gallion_reddit_lashawnda_gallion_d5d45f7f`
- `audit_json_url=/screenshots/reddit_login_audit/20260319T154523Z_standalone_reddit_identity_lashawnda_gallion_reddit_lashawnda_gallion_d5d45f7f/audit.json`

## open risks
- `Lashawnda_Gallion` may be a real external reddit-side blocker rather than a remaining backend gap; the local evidence is repeated `401 user-interaction-failed` across baseline, `settle_home`, `email_identifier_dwell`, `email_identifier_fast_otp`, `otp_retry_fresh_cycle`, and `acquire_form_reload`, plus both deterministic reference bootstraps.
- production may still diverge from local because the final proof target is first-pass production behavior, not only local convergence.
- if any account blocks, the next action must come from rollout report evidence, audit checkpoints, screenshots, and logs rather than guesses.
