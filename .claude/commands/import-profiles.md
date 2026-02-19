# Import Profiles

Bulk import Facebook profiles from a TXT file. Handles credentials-only (3-field), cookies-included (6-field), and mixed formats.

## Arguments (from $ARGUMENTS)
Parse the following from user input:
- **file_path** (required): path to TXT file with profiles
- **tags** (required): comma-separated demographic tags, e.g. `tags=female,white`
- **persona** (required): base persona description, e.g. `persona="middle-aged white woman"`
- **hair** (optional): hair color distribution, e.g. `hair="20 blonde, 10 redhead"`. If omitted, use "blonde" for all.

Example: `/import-profiles /path/to/profiles.txt tags=female,white persona="middle-aged white woman" hair="20 blonde, 10 redhead"`

## Pipeline

### Step 1: Analyze input file
- Read the TXT file
- Detect format by splitting first line on `:`
  - **3 fields** (uid:password:2fa_secret) → credentials only, need login
  - **6+ fields** (uid:password:dob:2fa:user_agent:cookies_base64) → has cookies, session created on import
- Count total profiles
- Report: format detected, count, any malformed lines

### Step 2: Import credentials
- Upload TXT to `POST /credentials/bulk-import` (multipart file, X-API-Key auth)
- For 3-field: stores credentials, `sessions_created=0`
- For 6-field: stores credentials AND creates sessions with cookies
- Verify: response `imported` count matches expected
- Extract UIDs from file for later use

### Step 3: Create sessions (3-field only)
Skip this step for 6-field format (sessions already created).

- **Test with 1 profile first**: `POST /sessions/create` with first UID
- Verify: session created with real FB name, valid cookies, display_name
- If fails: debug, fix, retry before proceeding
- **Batch remaining**: `POST /sessions/create-batch` with remaining UIDs
- Track failures. **ALL must succeed** — retry individually if batch has failures.
- Verify: `GET /sessions` shows all new sessions with `has_valid_cookies: true`

### Step 4: Tag all profiles
- Get new session names from `GET /sessions` (filter recently created)
- For each: `PUT /sessions/{profile_name}/tags` with `{"tags": [<parsed tags>]}`
- This REPLACES any auto-tags ("imported", "with_cookies") with demographic-only tags
- Verify: every new session has exactly the specified tags

### Step 5: Generate profile photos
- Parse hair distribution from args (e.g., "20 blonde, 10 redhead")
- Build 1 unique persona description per profile:
  - Vary: age (38-52), hair style, clothing, background, scenario
  - Scenarios: alone at home, with husband, with kids, with pet, in car, outdoor, at restaurant, coffee shop, etc.
  - Each description must include: ethnicity, age, hair color+style, clothing, setting, expression
  - ALWAYS append: "iPhone selfie, front camera. NO UI elements, NO captions, NO text, NO watermarks. Raw unedited photo only."
- Call `POST /workflow/batch-generate-photos` with all assignments
  - If >15 profiles, split into batches of 10-15 to avoid timeout
- Monitor via Railway logs
- Track: success/failure per profile

### Step 6: Verify on actual Facebook
For each profile:
1. Check session data: `profile_picture` non-null, valid cookies, display_name, correct tags
2. Use adaptive agent to navigate to `m.facebook.com/profile.php?id={user_id}`
3. Confirm profile photo is set (not default avatar)
4. If photo missing: re-generate + re-upload + re-verify

### Step 7: Final report
Generate [PASS/FAIL] table:

| # | profile_name | display_name | user_id | tags | has_photo | fb_verified | status |
|---|---|---|---|---|---|---|---|

All must be PASS.

## Edge cases
- **Login checkpoint**: retry 3x, escalate to user if still failing
- **2FA with spaces/dashes**: code normalizes automatically (strip spaces, uppercase)
- **Photo generation blocked by Gemini**: adjust prompt wording, avoid swimwear/pool scenarios, retry
- **Photo upload timeout**: retry with adaptive agent
- **Profile already exists**: login overwrites session file, re-tag
- **No proxy configured**: fail fast, prompt user to check PROXY_URL env var
- **Duplicate UIDs in file**: credentials.py updates existing entry, not a problem

## API reference
All endpoints use `X-API-Key: $CLAUDE_API_KEY` header.

| endpoint | method | purpose |
|---|---|---|
| `/credentials/bulk-import` | POST (multipart) | import TXT file |
| `/sessions/create` | POST | login single credential |
| `/sessions/create-batch` | POST | login multiple credentials (semaphore=5) |
| `/sessions` | GET | list all sessions |
| `/sessions/{name}/tags` | PUT | set tags for session |
| `/workflow/update-profile-photo` | POST | generate + upload 1 photo |
| `/workflow/batch-generate-photos` | POST | generate + upload multiple photos |
| `/analytics/profiles` | GET | check profile statuses |
