# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

Key files:
- `main.py` - API endpoints, WebSocket broadcast
- `comment_bot.py` - Core automation logic (navigation, clicking, typing, verification)
- `fb_session.py` - Session persistence, cookie extraction/validation
- `credentials.py` - Credential CRUD, TOTP generation (pyotp)
- `gemini_vision.py` - Vision prompts, element detection
- `fb_selectors.py` - Mobile Facebook CSS selectors
- `url_utils.py` - Facebook URL parsing, redirect resolution

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
