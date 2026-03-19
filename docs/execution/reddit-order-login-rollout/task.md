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
- backend hardening for 4-field reddit import, source-tag defaults, rollout wiring, audit classification, outlook email challenge recovery, existing-session reuse, and transient-navigation retry shipped on github commit `d84895f24bc21fe37ad96d9eb82c93f0b946a987`.
- targeted backend tests for the new behavior passed locally before production deployment.
- production health endpoint returned healthy on 2026-03-19.
- railway deployed commit `d84895f24bc21fe37ad96d9eb82c93f0b946a987` successfully on 2026-03-19.
- production uses the existing system proxy at `209.145.57.39:44416`.
- production now has 10 additional non-fixture reddit credentials and 10 valid reddit sessions for the exact order usernames.
- the order file is `/Users/nikitalienov/Downloads/3-Business/Orders/business-order-transaction-details.txt`.
- the order file contains 10 reddit credentials in 4-field format:
- `username:password:email:email_password`

## active todo
- none.

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
- production deploy verification:
- `railway deployment list --json --limit 1` returned `status=SUCCESS` for deployment `4ca4ccb7-11d5-4cef-afbd-d64ef1bac3b4` on commit `d84895f24bc21fe37ad96d9eb82c93f0b946a987`.
- production single-account accidental dispatch:
- run `20260319T155343Z_489c61d2` was unintentionally sent with `line_count=1` for `Dalila_Danzy`.
- it still completed successfully and proved `Dalila_Danzy` can converge in production under the deployed ladder.
- production full rollout:
- run `20260319T160302Z_fa2af8da` completed on 2026-03-19 with:
- `total_accounts=10`
- `imported_accounts=10`
- `create_success_count=10`
- `test_success_count=10`
- `action_success_count=10`
- `active_sessions_count=10`
- `blocked_accounts_count=0`
- production endpoint verification after the completed run showed all 10 order usernames present under `/reddit/credentials` with `fixture=false`, `session_connected=true`, `session_valid=true`.
- production endpoint verification after the completed run showed all 10 order usernames present under `/reddit/sessions` with `valid=true` and `proxy_source=session`.
- `Lashawnda_Gallion` was not a true production blocker; it ultimately converged and produced `reddit_lashawnda_gallion`.

## open risks
- no open rollout risks remain for this order.
- reusable caution remains: do not overfit a local full-ladder blocker into a production impossibility without production evidence; `Lashawnda_Gallion` disproved that assumption.
