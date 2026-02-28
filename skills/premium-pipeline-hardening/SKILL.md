---
name: premium-pipeline-hardening
description: Build, run, debug, and verify the premium automation pipeline with strict anti-spam safeguards, identity checks, manual-control recovery, and hard pass gates (feed/group/likes/shares/replies plus mix). Use for any premium production reliability work.
---

# Premium Pipeline Hardening

## When to use
Use this skill for any task involving premium pipeline implementation or operations:
- New premium API/scheduler/orchestrator work
- Production premium run failures
- Duplicate/spam prevention and identity hardening
- Manual control reconnect freezes (`Connected` + `Waiting for browser...`)
- Strict pilot/pass-matrix verification

## Reliability contract (non-negotiable)
Do not mark work complete unless all are true for the target run:
1. `status=completed`
2. `pass_matrix` exactly matches required totals
3. Evidence contract passes
4. No duplicate-precheck bypass for feed posts
5. No submit-loop behavior (single-submit guard intact)

Strict pilot matrix:
- `feed 4/4`
- `group posts 4/4`
- `likes 8/8`
- `shares 4/4`
- `replies 4/4`
- `mix 3 character + 1 ambient`

## Architecture map (project-specific)
- API surface: `backend/main.py`
- Orchestrator: `backend/premium_orchestrator.py`
- Actions: `backend/premium_actions.py`
- Safety checks: `backend/premium_safety.py`
- Content/rules: `backend/premium_content.py`, `backend/premium_rules.py`
- Store/scheduler: `backend/premium_store.py`, `backend/premium_scheduler.py`
- Manual control/session streaming: `backend/browser_manager.py`, `backend/main.py` session endpoints

## Required execution policy defaults
Ensure profile config contains:
- `dedupe_precheck_enabled=true`
- `dedupe_recent_feed_posts=5`
- `dedupe_threshold=0.90`
- `block_on_duplicate=true`
- `single_submit_guard=true`
- `action_timeout_seconds=420`
- `comment_replies_timeout_seconds=900`
- `tunnel_recovery_cycles=2`
- `tunnel_recovery_delay_seconds=90`

## Production runbook
Assume:
- `BASE=https://commentbot-production.up.railway.app`
- `X-API-Key` from `backend/.env` (`CLAUDE_API_KEY`)

1. Sync rules:
```bash
curl -sS -H "X-API-Key: $CLAUDE_API_KEY" \
  -X POST "$BASE/premium/rules/sync"
```

2. Create strict run:
```bash
curl -sS -H "X-API-Key: $CLAUDE_API_KEY" -H 'Content-Type: application/json' \
  -X POST "$BASE/premium/runs" \
  --data @run_spec.json
```

3. Trigger scheduler watchdog:
```bash
curl -sS -H "X-API-Key: $CLAUDE_API_KEY" \
  -X POST "$BASE/premium/scheduler/tick"
```

4. Monitor until terminal state:
```bash
curl -sS -H "X-API-Key: $CLAUDE_API_KEY" "$BASE/premium/runs/$RUN_ID" | jq \
  '{status, pass_matrix, observed:.verification_state.observed, error, last_event:.events[-1]}'
```

5. Verify strict evidence:
- Each action has `action_id/timestamp/run_id/step_id`
- `target_url/target_id` present where applicable
- `screenshot_urls.before/after` present
- Confirmation flags for post/like/share/reply semantics are true
- `profile_identity_confirmed` is true on all action evidence
- Feed evidence has `duplicate_precheck.passed=true` and `<0.90`

## Failure classification and hotfix loop
If run is not full-pass:
1. Read `run.error`, tail events, and last evidence items.
2. Classify failure:
- `duplicate_precheck_failed`
- `identity_verification_failed`
- `submit_idempotency_blocked`
- `action_timeout` (especially `comment_replies`)
- `reply evidence contract failed`
- manual stream/session freeze
3. Patch minimal root-cause code.
4. Add/adjust unit+integration tests.
5. Run tests locally.
6. Push to GitHub, wait for Railway deployment success.
7. Re-run production pilot from new run id.
8. Repeat until strict matrix passes.

## Manual control recovery checklist
For stuck manual sessions:
1. Check status:
```bash
curl -sS -H "X-API-Key: $CLAUDE_API_KEY" "$BASE/sessions/remote/status"
```
2. Force restart stream:
```bash
curl -sS -H "X-API-Key: $CLAUDE_API_KEY" \
  -X POST "$BASE/sessions/$SESSION_ID/remote/restart"
```
3. Confirm:
- first frame appears quickly after reconnect
- stream auto-heal events are emitted when needed
- idle auto-close happens after ~5 minutes with zero subscribers

## Definition of done
Done only when:
- Production run finished with strict matrix pass and evidence pass
- No duplicate posting in that completed run
- No unresolved manual-control freeze regression
- Changes are tested, pushed, and deployed
