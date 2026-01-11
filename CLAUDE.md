# Facebook Comment Automation - Project Documentation

> **Last Updated:** 2026-01-10
> **Status:** Phase 1 Complete - Session Integration Done

---

## WHAT WORKS (Verified)

### Session Persistence System
| Feature | Status | Notes |
|---------|--------|-------|
| Extract cookies from AdsPower profile | **WORKING** | 18 cookies extracted including c_user, xs |
| Save session to JSON file | **WORKING** | `sessions/fb_android_1.json` |
| Load session in fresh Playwright | **WORKING** | No AdsPower needed |
| Verify logged-in state | **WORKING** | Detects feed, stories, create buttons |
| User agent preservation | **WORKING** | Android mobile UA saved and applied |
| Viewport preservation | **WORKING** | 393x873 mobile dimensions |

**Test Command:**
```bash
cd backend
python test_session_poc.py test "FB_Android_1" --headless
# Result: SUCCESS! Session is valid!
```

### Backend API
| Endpoint | Status | Notes |
|----------|--------|-------|
| GET /unified_profiles | **WORKING** | Lists AdsPower profiles with credential links |
| GET /credentials | **WORKING** | Lists stored accounts |
| POST /credentials | **WORKING** | Adds new account |
| DELETE /credentials/{uid} | **WORKING** | Removes account |
| GET /otp/{uid} | **WORKING** | Generates TOTP code |
| POST /launch | **WORKING** | Starts AdsPower profile |
| POST /start_campaign | **WORKING** | Starts job queue (now tries session first!) |
| GET /status | **WORKING** | Returns job statuses |
| GET /sessions | **NEW** | Lists all saved sessions |
| POST /sessions/extract/{id} | **NEW** | Extracts session from profile |
| DELETE /sessions/{name} | **NEW** | Deletes saved session |
| GET /sessions/{name}/validate | **NEW** | Tests if session still works |

### Frontend UI
| Feature | Status | Notes |
|---------|--------|-------|
| Profile Dashboard | **WORKING** | Shows all profiles with status |
| Credential Vault | **WORKING** | Add/delete accounts |
| Campaign Runner | **WORKING** | URL + comments → job queue |
| Session Manager | **NEW** | Extract/validate/delete sessions |
| Real-time status | **WORKING** | 1-second polling |
| OTP copy to clipboard | **WORKING** | Auto-copies 2FA code |

### AdsPower Integration
| Feature | Status | Notes |
|---------|--------|-------|
| List profiles | **WORKING** | 10 profiles detected |
| Start profile | **WORKING** | Returns CDP endpoint |
| Stop profile | **WORKING** | Cleans up browser |
| CDP connection | **WORKING** | Playwright connects via WebSocket |

---

## WHAT DOESN'T WORK / NEEDS FIX

### Critical Issues (Fixed in Phase 1)
| Issue | Status | Notes |
|-------|--------|-------|
| ~~Session NOT integrated~~ | **FIXED** | queue_manager.py now tries session first |
| ~~No session API~~ | **FIXED** | 4 new endpoints in main.py |
| ~~No session UI~~ | **FIXED** | Sessions tab added to frontend |

### Remaining Issues
| Issue | Severity | Location |
|-------|----------|----------|
| **Login function never called** | CRITICAL | fb_login.py exists but unused (Phase 2) |
| **No authentication on API** | HIGH | main.py - all endpoints open |
| **Passwords in plaintext** | HIGH | accounts info.txt |
| **Sequential job processing only** | HIGH | queue_manager.py |
| **Hardcoded API URL** | MEDIUM | App.tsx line 63 |
| ~~**No error handling in frontend**~~ | ~~MEDIUM~~ | **FIXED** - Added loading states |
| **1-second polling inefficient** | MEDIUM | Should use WebSocket |

### Fixed Issues (Phase 1.5)
| Issue | Fix |
|-------|-----|
| **Frontend requires Refresh before Campaign works** | Added async fetchData with Promise.all + loading state |
| **Automation clicks random posts when target not visible** | Added target post verification (checks for "From your link") |
| **No indication of data loading** | Added yellow "Loading..." indicator in header |

---

## ARCHITECTURE

```
┌─────────────────────────────────────────────────────────────┐
│                        FRONTEND                              │
│  React + Tailwind + shadcn/ui                               │
│  App.tsx (4 tabs: Dashboard, Campaign, Sessions, Vault)    │
│  Polls /status every 1 second                               │
└─────────────────────────┬───────────────────────────────────┘
                          │ HTTP (localhost:8000)
┌─────────────────────────▼───────────────────────────────────┐
│                        BACKEND                               │
│  FastAPI + Playwright + AdsPower                            │
│                                                              │
│  main.py ─────► queue_manager.py ─────► automation.py       │
│     │                  │                     │               │
│     │                  │                     ▼               │
│     │                  │           [UNUSED] fb_login.py     │
│     │                  │           [UNUSED] fb_warmup.py    │
│     │                  │                                     │
│     │                  ├──► fb_session.py  ◄── INTEGRATED!  │
│     │                  │    (FAST PATH ~2s)                 │
│     │                  │                                     │
│     ▼                  ▼                                     │
│  credentials.py    adspower.py                              │
│  (../accounts      (local API                               │
│   info.txt)        :50325)                                  │
└─────────────────────────────────────────────────────────────┘

JOB FLOW (Phase 1):
1. Job starts
2. Try load saved session from sessions/*.json
3. If session valid → run_with_session() (FAST ~2 sec)
4. If session invalid/missing → AdsPower fallback (SLOW ~60 sec)
```

---

## FILE REFERENCE

### Backend Files
| File | Purpose | Status |
|------|---------|--------|
| `main.py` | FastAPI server, all endpoints | Working + Session APIs |
| `automation.py` | Comment posting logic | Working + run_with_session() |
| `queue_manager.py` | Job queue processing | Working - tries session first |
| `credentials.py` | Account storage | Working |
| `adspower.py` | AdsPower API client | Working |
| `fb_login.py` | Facebook login automation | **UNUSED** (Phase 2) |
| `fb_warmup.py` | Human behavior simulation | **UNUSED** |
| `fb_session.py` | Session extraction/persistence | **INTEGRATED** |
| `debug_logger.py` | Debug logging with screenshots/HTML | **NEW** |
| `test_session_poc.py` | Session testing CLI | Working |
| `sessions/*.json` | Saved session data | Working |
| `debug/job_*/` | Per-job debug directories | Auto-generated |

### Frontend Files
| File | Purpose | Status |
|------|---------|--------|
| `App.tsx` | Entire UI (monolithic) | Working - needs split |
| `components/ui/*` | shadcn/ui components | Working |

---

## KEY LEARNINGS

### How SMM Panels Actually Work
1. **They don't use browser automation** for scale
2. Use **mobile API** (instagrapi) or **cloud phone farms** (GeeLark)
3. Sessions persist for **90 days** - login once, reuse forever
4. Distribute work across **10,000+ accounts** (5-20 actions each)
5. **1 proxy per 1-3 accounts** maximum

### Why Our Approach Works (For Now)
1. Cookie-based session persistence is valid for Facebook
2. `c_user` + `xs` cookies = authenticated session
3. Can reuse session **without AdsPower** in plain Playwright
4. Session survives IP changes (tested without proxy)

### What Triggers Checkpoints
1. Device fingerprint changes
2. IP changes while logged in
3. Too many logins (re-authenticating frequently)
4. Browser automation detection
5. Behavioral patterns (all accounts same action same time)

---

## COMMENT AUTOMATION BEHAVIOR (CRITICAL)

### Use Coordinates, NOT Selectors
Selectors like `[data-action-id="32607"]` match multiple elements and click the wrong one (opens "Create post" instead of comment). **Always use coordinates.**

### Comment Button Position (VERIFIED WORKING)
```python
viewport = page.viewport_size  # 393x873
center_x = viewport['width'] // 2   # 196px - center of screen
comment_y = 560                      # Action bar Y position (NOT 533 - hits image!)
await page.mouse.click(center_x, comment_y)
```

**Y coordinate learnings:**
- Y=533 → clicks on image (opens fullscreen photo)
- Y=505 → clicks on image (opens fullscreen photo)
- Y=565 → may click on Reels section if visible
- Y=560 → **CORRECT** - hits action bar

### Send Button Position (VERIFIED WORKING)
```python
send_x = viewport['width'] - 70     # 323px - inside input area (NOT 365!)
send_y = viewport['height'] - 35    # 838px - bottom area
await page.mouse.click(send_x, send_y)
await page.keyboard.press("Enter")  # Backup - also works
```

**Send button learnings:**
- X=365 is too far right (off the button)
- X=323 hits the blue send arrow correctly

### Permalink Navigation Behavior
- Navigate to permalink URL → Facebook **ALWAYS** redirects to feed
- BUT target post appears at **TOP** of feed with "From your link" label
- This is **NORMAL** - do NOT try to change URL format
- Coordinates work because target post is always at top position

### Target Post Verification (CRITICAL)
**Added in Phase 1.5** - automation now verifies target post is visible before clicking.

The automation checks for:
1. "From your link" banner in page content
2. URL contains `story.php` or `posts/` (direct post page)

If neither is found, automation **aborts with clear error** instead of clicking random posts.

**Why this matters:** When Facebook doesn't show the target post (account issues, rate limits, etc.), the old code would blindly click at Y=560 and post comments on random posts or even send friend requests!

### Account Suspension Detection
If automation shows "Your account is no longer suspended" page:
1. The session cookies are STALE
2. Must extract a FRESH session from AdsPower
3. Wait for Facebook to fully reinstate account features
4. May need to wait hours/days before permalink redirect works

### What DOESN'T Fix Permalink Issues
Tested and confirmed these do NOT fix "From your link" not appearing:
- Changing user agent to iOS Safari
- Using `m.facebook.com/story.php` URL format
- Using `/share/p/` URL format
- Extracting fresh session (if account recently reinstated)

The issue is Facebook-side, likely:
- Account rate-limited for this URL
- Account trust level too low
- Too many visits to same permalink

### DO NOT
- Convert URL to mobile format (unnecessary)
- Change the permalink URL in any way
- Use selectors for comment/send buttons (unreliable)
- Skip session verification in production
- Change user agent to "fix" permalink issues (doesn't work)

---

## DEBUG LOGGING SYSTEM

### How It Works
Every job creates a timestamped directory with full audit trail:
```
backend/debug/
└── job_FB_Android_1_2026-01-10_143052/
    ├── attempt_1/
    │   ├── 01_page_loaded.png      # Screenshot after navigation
    │   ├── 01_page_loaded.html     # Full HTML snapshot
    │   ├── 02_comment_clicked.png  # After clicking comment button
    │   ├── 02_comment_clicked.html
    │   ├── 03_input_focused.png    # After clicking input field
    │   ├── 03_input_focused.html
    │   ├── 04_text_typed.png       # After typing comment
    │   ├── 04_text_typed.html
    │   ├── 05_comment_sent.png     # After pressing Enter
    │   ├── 05_comment_sent.html
    │   └── log.json                # Step-by-step JSON log
    └── summary.json                # Full job summary
```

### summary.json Contains
- Job metadata (profile, URL, comment)
- Each attempt with timestamps and duration
- Each step with screenshot path, HTML path, coordinates
- Browser console logs (JS errors, warnings)
- Error messages if failed

### Retention
- Keeps last **20 job directories**
- Older directories auto-deleted on new job start

### Debugging a Failure
1. Find the job directory: `ls -la backend/debug/`
2. Open `summary.json` to see what failed
3. Check the screenshot at the failed step
4. Check the HTML to understand page state
5. Check console_logs for JS errors

---

## COMMANDS REFERENCE

### Test Comment Posting
```bash
# Start backend first
cd backend && source venv/bin/activate && uvicorn main:app --reload

# Post a test comment (in another terminal)
curl -X POST http://localhost:8000/start_campaign \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://www.facebook.com/permalink.php?story_fbid=...",
    "jobs": [{
      "profileId": "k18q0l5d",
      "profileName": "FB_Android_1",
      "comment": "Test comment"
    }]
  }'

# Check debug output
ls -la backend/debug/
cat backend/debug/job_FB_Android_1_*/summary.json
```

### Session Management
```bash
# List AdsPower profiles
python test_session_poc.py list-profiles

# Extract session from logged-in profile
python test_session_poc.py extract "FB_Android_1"

# Test if saved session works
python test_session_poc.py test "FB_Android_1" --headless

# List saved sessions
python test_session_poc.py list
```

### Start Backend
```bash
cd backend
source venv/bin/activate
uvicorn main:app --reload
```

### Start Frontend
```bash
cd frontend
npm run dev
```

---

## MILESTONES COMPLETED

### Research & Discovery
- [x] Research: How SMM panels work at scale
- [x] Audit: Current frontend/backend architecture

### Session PoC (Complete)
- [x] PoC: Session extraction from AdsPower
- [x] PoC: Session reuse in plain Playwright
- [x] Verify: Session works without re-login

### Phase 1: Session Integration (Complete)
- [x] Integrate session loading into queue_manager.py
- [x] Add run_with_session() to automation.py
- [x] Create session API endpoints (4 new endpoints)
- [x] Add session management UI to frontend (Sessions tab)

## MILESTONES PENDING

### Phase 2: Login Integration
- [ ] Integrate fb_login.py into automation flow
- [ ] Auto-extract session after successful login

### Phase 3: Performance & UX
- [ ] Add parallel job processing
- [ ] Add error handling/toasts to frontend
- [ ] Add WebSocket for real-time updates
- [ ] Component decomposition in frontend

### Phase 4: Security & Production
- [ ] Add authentication to API
- [ ] Encrypt credential storage
- [ ] Environment-based API URL

---

## SOURCES & RESEARCH

- [instagrapi best practices](https://subzeroid.github.io/instagrapi/usage-guide/best-practices.html)
- [GeeLark automation guide](https://www.geelark.com/blog/the-ultimate-instagram-bots-guide-to-no-code-automation/)
- [Cloud Phone Farms 2025](https://www.proxyfella.com/2025/08/25/cloud-phone-farms-the-complete-2025-guide-to-mobile-automation-from-beginner-to-10k-month/)
- [Instagram Fingerprint Detection 2025](https://multiaccounts.com/blog/instagram-fingerprint-detection-avoidance-guide-2025)
