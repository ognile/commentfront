---
paths:
  - "backend/adaptive_agent.py"
  - "backend/main.py"
---

# Adaptive Agent Rules

The Adaptive Agent uses Gemini Vision to decide actions + Playwright DOM matching for clicking.

## When to Use
- Restriction appeals
- Complex multi-step Facebook tasks
- Any task that can't be hardcoded

## Endpoint
```
POST /adaptive-agent
Header: X-API-Key
```

## Request Format
```json
{
  "profile_name": "Profile Name",
  "task": "Natural language description",
  "max_steps": 15
}
```

## Example: Appeal Restriction
```bash
curl -X POST "https://commentbot-production.up.railway.app/adaptive-agent" \
  -H "X-API-Key: $CLAUDE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"profile_name": "karen_monty", "task": "Appeal the Facebook restriction. Click See why, scroll, click Request review.", "max_steps": 15}'
```

## Available Actions (Gemini decides)
| Action | Syntax | Description |
|--------|--------|-------------|
| CLICK | `element="text"` | Click by label/text |
| SCROLL | `direction=up/down` | Scroll the page |
| TYPE | `text="content"` | Type into focused input |
| UPLOAD | `element="Upload"` | File upload (requires upload_file_path) |
| WAIT | `reason="..."` | Wait for content to load |
| DONE | `reason="..."` | Task completed successfully |
| FAILED | `reason="..."` | Task cannot be completed |

## Key Implementation Details
- Uses `dump_interactive_elements()` for DOM state
- Multi-strategy clicking: tap → touchscreen → CDP → mouse
- Screenshots: `/data/debug/adaptive_step_*.png`
- File chooser handler for UPLOAD action
