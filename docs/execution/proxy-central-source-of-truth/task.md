# Task: Proxy Central Source Of Truth

## North Star
All browser automation uses one active proxy selected from the UI-managed proxy store. Facebook sessions, Reddit sessions, credentials, bot code, and remote browser leases never own or override raw proxy URLs. The only active production proxy after migration is the new HTTP endpoint on port `44418` for the current proxy host.

## Success Criteria
- [ ] Runtime proxy authority is centralized in one backend resolver, verified by code search showing no direct browser-launch path reads `session.get_proxy()`, `PROXY_URL`, or request-level proxy URL except through the resolver.
- [ ] UI proxy management is the operational control plane: add proxy, test proxy, set active proxy, clear/deactivate proxy, and read active status all map to the same proxy store and resolver.
- [ ] Session JSON for Facebook and Reddit no longer persists raw `proxy` values, verified by migration dry-run output and post-migration readback showing zero session-owned proxy URLs.
- [ ] Existing expired proxy endpoints `209.145.57.39:44416`, `209.145.57.39:44419`, and `209.145.57.39:45456` are removed from runtime authority and session state.
- [ ] The new active proxy is represented once in the proxy store as `http://209.145.57.39:44418` if the host remains unchanged, verified by `/proxies` and active resolver diagnostics.
- [ ] Local proof captured: backend unit tests cover resolver precedence, session migration, no session override behavior, proxy UI endpoints, and launch-path use of the resolver.
- [ ] Local proof captured: local backend plus frontend/browser verification shows the UI can add/test/set the active proxy and sessions display service-level proxy state without per-session proxy ownership.
- [ ] Production proof captured only after explicit approval: GitHub deployment completes, `/proxies` shows the active `44418` proxy, `/sessions` and `/reddit/sessions` show zero `proxy_source=session`, and a safe browser launch uses the active resolver.

## Preferences / Constraints
- Current instruction: audit only; do not mutate production, do not deploy, do not run cleanup, do not run sync endpoints.
- `PROXY_URL` may be a bootstrap fallback only when the proxy store is empty; it must not be a competing runtime truth once UI-managed proxies exist.
- Do not fix this by copying `PROXY_URL` into session files. That preserves the broken model.
- Do not add hidden proxy judgment to bot code. Bot code should receive an already-resolved proxy config from the canonical resolver.
- Do not preserve backward-compatible session proxy behavior. Session-level proxy ownership is the bug.
- Protect account/session auth state. Proxy cleanup must be a migration with read-only audit, dry-run, backup path, then mutation after approval.
- No untracked implementation files should remain after execution work begins. This audit file is the only new artifact for the current probing phase.

## Audit Findings

| area | current state | root cause | required fix | final state |
|---|---|---|---|---|
| production proxy store | `/proxies` returns only `system` from `PROXY_URL`: `209.145.57.39:44419`; no user-managed proxies are present | env proxy is treated as a first-class UI proxy but cannot be edited or set active from the proxy store | seed or create `44418` in `proxies.json`/volume-backed proxy store and make that store the active source | `/proxies` shows one active UI-managed proxy on `44418`; env is bootstrap-only |
| facebook sessions | production `/sessions`: 81 total; 18 use `env 44419`, 33 use session `44416`, 30 use session `45456` | `FacebookSession` persists `proxy`; `/sessions` reports stored proxy first | remove raw proxy from session schema and migrate existing session files | facebook sessions show `proxy_source=active_proxy` or equivalent service label, never `session` |
| reddit sessions | production `/reddit/sessions`: 20 total; all 20 use session `44416` | `RedditSession` persists `proxy`; reddit session listing and launch helpers prefer stored proxy in places | remove raw proxy from reddit session schema and migrate existing files | reddit sessions use central active proxy only |
| central resolver | `get_system_proxy()` exists and reads default `proxies.json` then `PROXY_URL` | resolver name and behavior are halfway centralized, but launch paths and session views still allow overrides | replace with explicit `get_active_proxy()` / `ProxyResolver` that reads proxy store active/default first, env only as bootstrap | all launches and diagnostics call the same resolver |
| remote browser | `_resolve_remote_proxy_plan()` prefers stored session proxy and uses env as fallback | this intentionally encodes stale session proxy authority | remove session proxy input; remote leases resolve active proxy once and log proxy id/source | remote browser source is active proxy store only |
| premium safety precheck | `_resolve_precheck_proxy()` prefers `session.get_proxy()` | old session-affinity assumption is embedded in precheck | call central resolver only | safety precheck uses same proxy as runtime |
| reddit browser helpers | `_session_page()` uses `proxy_url or session.get_proxy()` | helper can silently fall back to session-owned stale proxy | require explicit resolved proxy from central resolver | helper fails if caller did not pass canonical active proxy |
| reddit reference bootstrap | `reddit_login_bot` uses Facebook reference session proxy before system proxy | cross-platform bootstrap leaks Facebook session proxy into Reddit creation | resolve proxy independently from active proxy store | reference identity uses cookies/fingerprint from session but active proxy from resolver |
| proxy UI | UI supports add/test/set-default for user proxies, but system env proxy is shown as undeletable/non-default | UI concept is close, backend store is not actually the only authority | make UI proxy store own active proxy; system env should be labeled bootstrap/fallback, not active managed proxy | operator can add `44418`, test it, set active, and all automation immediately uses it |
| cleanup endpoint | `/sessions/sync-all-to-env-proxy` copies `PROXY_URL` into every Facebook session | endpoint solves mismatch by spreading env value into session state | deprecate/remove this endpoint; replace with `sessions/proxy-migration/dry-run` and approved cleanup that removes session proxy fields | cleanup reduces stored proxy authority to zero |

## Code Inventory
- `backend/proxy_manager.py`: owns `ProxyManager`, `proxies.json`, default proxy, health testing, assignment lists, and `get_system_proxy()`.
- `backend/main.py`: imports `PROXY_URL` directly, creates global `proxy_manager`, lists sessions with stored proxy precedence, exposes proxy CRUD, exposes session proxy assignment, and contains the env-copy cleanup endpoint.
- `backend/fb_session.py`: persists `"proxy": proxy` during extraction/import and exposes `get_proxy()`.
- `backend/reddit_session.py`: persists `"proxy": proxy` during extraction and exposes `get_proxy()`.
- `backend/remote_lease_service.py`: remote session spec accepts `stored_proxy`, prefers it, and only falls back to env/system proxy.
- `backend/premium_safety.py`: precheck prefers session proxy.
- `backend/reddit_bot.py`: page context helper falls back to `session.get_proxy()`.
- `backend/reddit_login_bot.py`: reference Facebook identity path prefers reference session proxy.
- `frontend/src/App.tsx`: proxy tab can add/test/set default user proxies, but cannot edit the env system proxy and still displays session proxy status.
- Tests currently encode the wrong behavior in `backend/tests/test_remote_lease_service.py` and `backend/tests/test_premium_safety.py` by asserting session proxy precedence.

## Proposed Implementation Sequence
- [ ] Add read-only diagnostics: active resolver response, proxy store contents, session proxy leak counts, launch-path source labels.
- [ ] Refactor proxy model: define `ProxyResolver`/`get_active_proxy()` with one contract: active proxy from `ProxyManager` default; env only if no stored proxies exist.
- [ ] Remove session proxy authority from launch paths: remote lease, premium safety, reddit helper, reddit reference bootstrap, Facebook login/create/comment flows.
- [ ] Remove user-facing session proxy assignment concepts: backend endpoint, proxy manager assigned session authority, UI session proxy filters/status labels that imply session ownership.
- [ ] Add migration dry-run: enumerate Facebook and Reddit session files with `proxy`, summarize endpoints, and write no state.
- [ ] Add approved migration: backup session files, delete `proxy` fields, preserve cookies/user-agent/viewport/device/tags, and report exact changed files.
- [ ] Seed or create the active proxy entry for `http://209.145.57.39:44418` through the proxy store, then set it active.
- [ ] Update tests to assert central active proxy precedence and no session fallback.
- [ ] Verify locally with API calls, unit tests, backend launch dry-run, and Browser Use frontend check.
- [ ] After explicit approval, deploy from committed GitHub state and verify production readbacks plus one safe browser-launch proof.

## Verification Plan
- `rg -n "session\\.get_proxy\\(|PROXY_URL|\\[\"proxy\"\\]|proxy_source=\"session\"|_resolve_remote_proxy_plan|assign-proxy|sync-all-to-env-proxy" backend frontend/src` should show no runtime authority leaks outside migration/diagnostics/tests that intentionally check absence.
- Backend tests: resolver, proxy CRUD, migration dry-run/apply, remote lease, premium safety, reddit session page, reddit reference bootstrap, Facebook session creation.
- API local: `GET /proxies`, active resolver diagnostic, `GET /sessions`, `GET /reddit/sessions`, migration dry-run.
- Browser Use local: proxy tab adds `44418`, tests it, sets active, refreshes active status, and session screens do not display stale session proxy ownership.
- Production read-only before mutation: confirm stale counts still match audit or capture drift.
- Production after approval: `/proxies` active `44418`; `/sessions` and `/reddit/sessions` have zero `session` proxy source; launch logs show central resolver source.

## Learnings (outcome quality based)
- `2026-05-22 production /proxies read-only` -> only proxy returned is env-backed system proxy `http://209.145.57.39:44419`; no user-managed proxy entries currently exist.
- `2026-05-22 production /sessions read-only` -> 81 Facebook sessions: 18 env `44419`, 33 session `44416`, 30 session `45456`.
- `2026-05-22 production /reddit/sessions read-only` -> 20 Reddit sessions: all 20 session `44416`.
- `2026-05-22 code audit` -> central resolver exists, but session persistence and multiple launch helpers still make session proxy a competing source of truth.
