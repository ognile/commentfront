---
paths:
  - "backend/adaptive_agent.py"
  - "backend/main.py"
---

# Facebook Restriction Appeal Workflow

## Overview

Profiles get restricted when Facebook removes comments for "spam" or "community standards" violations. Appeals must be submitted within ~174 days.

## Finding Restricted Profiles

Restriction notices appear in **Notifications** (bell icon), not in the feed. Look for:
- "We removed your comment. See why."
- "We added restrictions to your account. See why."

## Appeal Flow

1. Click notifications bell icon
2. Click "See why" on restriction notice
3. Click "Request review" button
4. Select a reason (any works): "This comment doesn't break the rules", "The restriction is too harsh", etc.
5. Click "Continue"
6. Select second reason: "It was a joke", "It was to raise awareness", etc.
7. Click "Continue"
8. Click "Submit"
9. Verify "In review" status

## Recommended Task Prompt

Use explicit step-by-step instructions:

```json
{
  "profile_name": "profile_name",
  "task": "1. Click the notifications bell icon. 2. Look for the restriction notice that says We removed your comment or We added restrictions and click See why. 3. Click the Request review button. 4. Select any reason and click Continue through all steps until submitted.",
  "max_steps": 15
}
```

## Common Failures & Solutions

### FALLBACK_TOUCH 'ok' Loop

**Symptom:** Agent enters loop of `FALLBACK_TOUCH 'ok'` actions, Gemini never responds.

**Cause:** Gemini API timeout or rate limiting, NOT a session issue.

**Solution:**
1. Wait a moment (API contention may clear)
2. Retry with a simpler task first (e.g., "Click notifications bell icon")
3. Then continue with incremental steps

### "Request review" Click Doesn't Register

**Symptom:** Agent clicks "Request review" but form doesn't open, keeps retrying same click.

**Cause:** Click reliability issue - button click didn't register or page state stale.

**Solution:**
1. Don't assume page state from previous session
2. Always navigate fresh: notifications → See why → Request review
3. Use explicit numbered steps in task prompt

### "Already In Review"

**Symptom:** Profile shows "In review" status with date.

**Cause:** Appeal was already submitted previously.

**Action:** No action needed - profile is already appealed.

## Batch Processing Tips

When appealing multiple profiles:

1. **Run in parallel** - Profiles are independent, can run 3-4 concurrent requests
2. **Expect some failures** - First pass may have 30-40% failure rate
3. **Retry failures individually** - Usually succeed on second attempt
4. **Check for "Already In Review"** - Some may have been appealed before

## Verification

After appeal, the restriction details page shows:
- "In review" status with date
- "You'll hear back from us soon"
- "Thanks for requesting a review"

## Example: Batch Appeal Script

```bash
# Appeal a single profile
curl -X POST "https://commentbot-production.up.railway.app/adaptive-agent" \
  -H "X-API-Key: $CLAUDE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "profile_name": "profile_name",
    "task": "1. Click the notifications bell icon. 2. Click See why on the restriction notice. 3. Click Request review and complete the form by selecting any reasons and clicking Continue/Submit.",
    "max_steps": 15
  }'
```

## Debugging

Screenshots are saved at `/data/debug/adaptive_step_*.png` - check these to verify:
- What page the agent is actually on
- Whether restriction notices are visible
- If buttons are present and clickable
