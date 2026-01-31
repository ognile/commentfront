
## CRITICAL: Principles
1. always reply in lowercase, be direct, unapologetic. first principles thinking.
2. test EVERY FUNCTIONALITY locally until verified passing all success criteria, before pushing to GitHub. NEVER SKIP.
3. Always build concise, efficient solutions. 100 lines of clean code is better than 1000 lines of over-abstracted code. Be resourceful — reuse existing functions. But make sure you always have proper logging and error handling for easy debugging.
4. YOU execute everything end-to-end. YOU test vigorously. YOU verify with real API calls, real data, real logs.
5. every change, addition & decision MUST be based on verified data from the codebase, API docs, and tested behavior. when working with external APIs, always fetch API docs and verify behavior.
6. when debugging, start from source of data and trace forward with actual values. don't guess backwards from the error.
7. always ask "what's different between when this works and when it doesn't?" before diving into code.
8. test assumptions with real data before changing code.
9. always have a hyper-specific TODO list active. each item: action + expected output + verification method.
10. if a fix requires touching multiple files/layers, re-check if you understood the root cause correctly. usually the real fix is surgical.
11. check for competing processes FIRST before debugging "not responding" issues.
12. verify deployment is complete before running production tests. never assume deployment is done based on timeouts.
13. this codebase will outlive you. every shortcut becomes someone else's burden. every hack compounds. leave the codebase better than you found it.
14. always take advantage of subagents to delegate tasks and orchestrate them. YOU ARE THE STRATEGIST AND ORCHESTRATOR. make sure every subagent has enough context and specific success criteria.
15. prioritize commands and MCPs, CLIs, curl, bash etc to test quickly. use Claude Chrome extension when everything works with commands to verify frontend.
16. when asking user a question or giving options, ALWAYS use AskUserQuestion tool.

---

## CRITICAL: Plan Mode

- explore current state of relevant files, THEN fetch API docs for any external service being used.
- never write plan without a deep interview. only start writing plan when user explicitly agrees via AskUserQuestion.
- make the plan extremely concise. sacrifice grammar for concision.
- at the end of each plan, list unresolved questions if any.
- when editing a plan, verify changes were not already implemented. plan should only contain NOT-YET-IMPLEMENTED items.
- always use AskUserQuestion for in-depth interview to clarify intent and expectations. if unsure, interview user.
- deliver findings in table format: current state, root cause, fix, final state.
- ALWAYS include SPECIFIC 'success criteria' and LOCAL testing before production deployment. EVERY plan MUST end with exact verification steps that prove the change works on local dev server.
- ALWAYS require complete end-to-end execution (changes + local testing + production verification). agent must not stop until e2e is complete.
- always require having local testing and production verification in TODO list. 'push to production' is blocked until ALL local tests pass.
- after coding, create a verification task for EACH plan item and execute them.
- if any local test fails → fix → rerun local test.
- after ALL local verification criteria PASS, push to production and verify deployment.
- after e2e execution, require a [PASS/FAIL] criterion list with evidence (API response, log output, or screenshot).

## Project Overview

CommentBot is a Facebook comment automation system with a React frontend and FastAPI backend. It uses Playwright for browser automation and Google Gemini Vision for visual element detection.

## Development Commands

### Frontend (in `/frontend`)
```bash
npm run dev          # Start Vite dev server (port 5173)
npm run build        # TypeScript check + Vite production build
npm run lint         # ESLint
npm run preview      # Preview production build
```

### Backend (in `/backend`)
```bash
uvicorn main:app --reload    # Start FastAPI dev server (port 8000)
playwright install chromium  # Install browser (first time setup)
```

## Architecture

### Frontend (`/frontend`)
- **React 19 + TypeScript + Vite** (using rolldown-vite)
- **UI**: shadcn/ui components (Radix UI primitives) in `src/components/ui/`
- **Styling**: Tailwind CSS
- **Main app**: `src/App.tsx` - single-file app with 4 tabs (Campaign, Live View, Sessions, Credentials)
- **Real-time**: WebSocket connection to `/ws/live` for campaign progress updates

### Backend (`/backend`)
- **FastAPI** with async/await throughout
- **Browser**: Playwright with stealth mode, mobile viewport (393x873)
- **Vision**: Gemini 3 Flash for element detection with CSS selector fallback

### Automation Flow
1. Load session from JSON file (cookies, proxy, user_agent)
2. Launch Playwright browser in mobile viewport
3. Navigate to Facebook post URL
4. Use Vision API to find elements (comment button, input, send)
5. Fall back to CSS selectors if Vision fails
6. Verify comment posted visually
7. Broadcast progress via WebSocket

## Key Patterns

### Element Detection
The system uses a two-tier approach:
1. **Primary**: Gemini Vision analyzes screenshots to find clickable elements
2. **Fallback**: CSS selector lists in `fb_selectors.py` (15+ selectors per action)

### Session Management
Sessions are JSON files in `/backend/sessions/` containing:
- Facebook cookies (requires `c_user`, `xs`)
- Per-session proxy URL (not global)
- User agent string
- Viewport dimensions

### Debug Screenshots
In production, screenshots are saved to Railway's ephemeral container filesystem (`/app/debug/`) and served via FastAPI StaticFiles at `/debug/latest.png`. The frontend (on Vercel) polls this endpoint every 1 second with cache-busting timestamps. Screenshots are lost on container restart.

## Deployment

- Always push to Github first, so that vercel and railway pick up and auto depoy. do not push directly to vercel/railway.
- **Frontend**: Vercel → connects to Railway backend
- **Backend**: Railway at `https://commentbot-production.up.railway.app`
- **WebSocket**: `wss://commentbot-production.up.railway.app/ws/live`
- Frontend uses `VITE_API_BASE` env var (defaults to Railway URL above)
- Backend requires `nest_asyncio` for Railway async compatibility

## MCP Tools

This project has **Railway MCP** access configured. You can use Railway MCP tools for maximum access and testing.
Use these tools when debugging production issues.

## Developer/Testing Access (Claude "God Mode")

Claude has permanent API access for testing the backend independently of the frontend UI.

### API Key Authentication
- **Header**: `X-API-Key: <key>`
- **Env var**: `CLAUDE_API_KEY` (stored in Railway)
- Works alongside JWT auth - no expiration, no login needed

### Test Campaign Endpoint
Use `/test-campaign` to run campaigns without affecting the main queue:

```bash
curl -X POST "https://commentbot-production.up.railway.app/test-campaign" \
  -H "X-API-Key: $CLAUDE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://facebook.com/permalink.php?story_fbid=...",
    "comments": ["Test comment 1", "Test comment 2"],
    "enable_warmup": true
  }'
```

**Response includes:**
- `test_id`: Unique test campaign ID
- `results`: Array with per-profile success/failure, warmup stats, errors
- Profile rotation (LRU), warmup, analytics tracking all work

### Useful Endpoints for Testing

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/test-campaign` | POST | Run isolated test campaign |
| `/adaptive-agent` | POST | Run AI-guided multi-step task (see `.claude/rules/backend/adaptive-agent.md`) |
| `/workflow/update-profile-photo` | POST | Generate AI photo + upload to profile (see `.claude/rules/backend/workflows.md`) |
| `/workflow/regenerate-profile-photo` | POST | Regenerate photo preserving identity (new pose/setting) |
| `/workflow/regenerate-all-imported-photos` | POST | Batch regenerate all "imported" tagged profiles |

### Common Operations

For **restriction appeals**, see `.claude/rules/backend/restriction-appeals.md` - includes workflow, common failures (FALLBACK_TOUCH loop, click not registering), and solutions.
| `/analytics/summary` | GET | Today/week stats, active/restricted counts |
| `/analytics/profiles` | GET | All profiles with status, usage history |
| `/analytics/profiles/{name}` | GET | Single profile detailed history |
| `/analytics/profiles/{name}/unblock` | POST | Manually unblock restricted profile |
| `/queue/history` | GET | Recent campaign results |
| `/sessions` | GET | All session profiles |

### Checking Warm-up & System Health
```bash
# Get recent logs with warm-up activity
mcp__railway__get-logs --filter "warmup OR Warm-up OR scroll"

# Check profile statuses
curl -s "https://commentbot-production.up.railway.app/analytics/profiles" \
  -H "X-API-Key: $CLAUDE_API_KEY"

# Check overall success rate
curl -s "https://commentbot-production.up.railway.app/analytics/summary" \
  -H "X-API-Key: $CLAUDE_API_KEY"
```

### Data Storage
All data is stored as JSON files on Railway Volume (`/data`):
- `campaign_queue.json` - Campaign queue and history
- `profile_state.json` - Profile rotation, restrictions, analytics
- `sessions/*.json` - Browser session cookies/proxies
- `users.json` - User accounts
- `credentials.json` - Facebook credentials
- `proxies.json` - Proxy list
