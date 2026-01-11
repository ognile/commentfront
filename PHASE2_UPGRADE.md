# Facebook Comment Automation - Phase 2 Upgrade

> **Status:** Phase 2 Complete - Smart Automation & Live View
> **Deployable:** Yes (Railway/Vercel compatible)

---

## 🚀 Key Upgrades

### 1. "Smart" Automation Core
We replaced the fragile coordinate-based clicking with a robust **Visual State Machine**.
- **Before:** Blindly clicked `x=200, y=500` (broke on ads/popups).
- **After:** Scans for "Write a comment", "Post", or the blue airplane icon. Scrolls elements into view. Verifies success visually.
- **Fail-Safe:** If a step fails, it takes a screenshot and saves it to `backend/debug/failed_*.png`.

### 2. Live Automation View
You can now watch the bot "think" in real-time.
- **Backend:** Saves a `latest.png` snapshot after every action (click, type, scroll).
- **Frontend:** Refreshes this image every 1 second to show a live feed of the active bot.
- **Benefit:** instant verification that profiles are working correctly.

### 3. Security & Anti-Detection
- **Session-Based Proxies:** The system now strictly enforces the proxy saved in the session file. It no longer relies on a dangerous global proxy that could link all your accounts.
- **Proxy Indicator:** The frontend now shows a Green/Red indicator for whether a session has a valid proxy attached.

### 4. Stability Fixes (502 Errors)
- Added `nest_asyncio` to fix the Railway crash loop.
- Added `playwright-stealth` to `requirements.txt`.
- Added explicit startup checks.

---

## 🛠️ How to Use

### 1. Extract Sessions
Use the "Extract" button in the frontend (when running locally with AdsPower) to save sessions.
**IMPORTANT:** Ensure your AdsPower profile has a proxy set. The extraction tool now saves this proxy into the session file.

### 2. Deploy
Push to Railway/Vercel. The new `requirements.txt` ensures a smooth build.

### 3. Run Campaign
1. Enter Post URL.
2. Enter Comments.
3. Click "Preview Jobs" -> "Run Campaign".
4. **Watch the "Live Automation View" card** to see the bot in action!

---

## ⚠️ Critical Notes for Production

1. **Proxy Requirement:** If a session file shows "No Proxy" in the UI, **do not use it** for high-volume campaigns. Re-extract it from AdsPower with a proxy attached.
2. **Mobile Viewport:** The bot forces a mobile viewport (393x873). Do not verify links on Desktop; they will look different.
