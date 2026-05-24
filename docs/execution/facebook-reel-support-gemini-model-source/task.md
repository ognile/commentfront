# Task: Facebook Reel Support And Gemini Model Source

## North Star
Facebook campaign delivery supports both regular text/post URLs and reel URLs as first-class intended targets, while still rejecting accidental target drift. Gemini text and vision calls use one runtime source of truth set to `gemini-3.5-flash`, with image generation kept on an explicitly image-capable model.

## Pilot Case
Recover the exact failed reel campaign `6f1e3b3a-363d-49f9-86be-59c6f518fe4f` targeting `https://www.facebook.com/reel/1548728006815458`. The first real proof is that this campaign's two original failed `post_comment` jobs become successful through the production-backed retry path, using preserved Railway sessions/fingerprints and the active central proxy. This is the pilot because it is the live failure that exposed the missing reel support and already has forensic evidence proving the failure happened before any comment interaction.

## Success Criteria
- [ ] Target classification is explicit and target-aware: `/reel/` is valid only when the submitted campaign URL is a reel, while a text/post campaign that lands on `/reel/`, `/watch/`, or `/videos/` still fails as unexpected drift. Verify with unit tests for post, reel, watch, video, redirect, and malformed URLs.
- [ ] Reel comment posting has a first-class automation path, not a disabled-post workaround. Verify selectors by launching one currently authenticated saved Facebook session through the preserved fingerprint schema and active proxy, then dumping interactive elements, opening the reel comment surface, typing, submitting, and visually verifying the posted comment.
- [ ] Pilot campaign `6f1e3b3a-363d-49f9-86be-59c6f518fe4f` is recovered from `0/2` to `2/2` successful delivery after local proof and explicit production approval. Verify with `/queue/history`, campaign results, forensic timeline, and visual verification evidence for both original job indexes.
- [ ] Existing text-post delivery remains intact. Verify locally with a known regular Facebook post campaign path and tests that prove text-post selectors and verification still pass.
- [ ] No successful duplicate profile comments occur on the same target. Verify with retry-path tests showing profiles that already succeeded on a campaign target are excluded from later successful jobs, while failed non-posted attempts may reuse the profile when the failure is not profile-specific.
- [ ] Proxy and session safety remain unchanged: every browser context uses the active proxy store and preserves each profile's cookies, user agent, viewport, timezone, locale, and fingerprint. Verify with code search, session readback, and a no-cookie-mutation audit before and after local tests.
- [ ] Gemini model selection is centralized by capability. Verify code search shows raw `gemini-*` model ids only in the model registry/test allowlist, not in workflow call sites.
- [ ] Gemini text and vision capability defaults to live-verified `gemini-3.5-flash`. Verify with a live model-list probe and one local Gemini call through the centralized registry.
- [ ] Gemini image generation remains on an explicitly image-capable model and is not silently switched to `gemini-3.5-flash`. Verify image workflows call the `image` capability from the same registry.
- [ ] Local proof captured: backend tests pass for target classification, reel workflow, regular post regression, duplicate-success profile exclusion, proxy/session preservation, and Gemini model registry drift detection.
- [ ] Local proof captured: local dev server plus Browser Use verifies the UI/API can accept, classify, validate, and display a reel-target campaign without treating the reel URL as a malformed post; local browser proof is plumbing only, not Facebook selector proof.
- [ ] Railway/production proof captured only after explicit alignment: deploy from committed GitHub state, verify production health/model/proxy/session readbacks, then run a small approved reel campaign and a regular post regression campaign using the real Railway proxy store and preserved production sessions.

## Preferences / Constraints
- Current phase is task definition and audit alignment; no code execution, retry, live posting, production mutation, or deploy is authorized by this task file alone.
- The failed reel campaign is the pilot case. Do not substitute a new easier reel unless this campaign is proven impossible with concrete evidence.
- Local development cannot prove real Facebook delivery with production cookies/proxy state because those live on Railway volume. Local proof is for code behavior, API/UI flow, target classification, tests, and mocked/isolated browser mechanics; real delivery proof belongs to Railway/production after approval.
- Facebook selector discovery and adaptive UI learning must not use a clean local browser. Use one healthy saved session, its preserved fingerprint, and the active proxy path; if that state lives on Railway, selector proof belongs on Railway or a production-backed diagnostic path after approval.
- The old reels rejection had a valid safety purpose: it protected regular post campaigns from accidental navigation into reels. The replacement must be target-aware, not a blanket removal.
- Do not solve reel support by pretending reels are posts. Reels need either a dedicated target adapter or a shared comment workflow with target-specific load/comment verification.
- Do not wipe, refresh, replace, or relogin sessions as part of reel support. Cookies and fingerprints are scarce state and must be protected.
- Do not blame proxy for reel failures unless proxy/network evidence proves it. The audited reel campaign failed before any comment interaction and had healthy network/proxy evidence.
- Do not enforce subjective LLM quality with hidden deterministic validators. For Gemini, code may enforce objective model ids, capability routing, schema shape, and runtime errors only.
- Align with the user before live production proof. The alignment question should be specific: approve a small reel campaign and one regular post regression after local proof passes.
- Keep this task compact. Add learnings only when they prove or disprove one of the success criteria or prevent a repeated architectural mistake.

## Learnings (outcome quality based)
- `2026-05-24T06:42:47Z production campaign` -> campaign `6f1e3b3a-363d-49f9-86be-59c6f518fe4f` targeted `https://www.facebook.com/reel/1548728006815458`, completed `0/2`, and both jobs failed with `Step 1 FAILED - Navigated to Reels instead of post`.
- `2026-05-24T06:51:09Z production history readback` -> both failed jobs had no successful delivery, no verification, and the same pre-comment failure before type/send phases.
- `2026-05-24 forensic timeline` -> each attempt recorded only setup, navigate, screenshot artifact, Gemini restriction check, and network bundle; there was no comment-button, input, type, submit, or post-submit verification event.
- `2026-05-24 forensic screenshot readback` -> `error_state.png` showed the Facebook/Meta splash screen, proving the current code aborts on the `/reel/` URL classification before allowing reel UI load and selector audit.
- `2026-05-24 forensic network bundle` -> both attempts had hundreds of successful Facebook/FBCDN/Google requests, zero failed network records, and document requests to `https://www.facebook.com/reel/1548728006815458`; this does not support proxy failure as the root cause.
- `2026-05-24 restriction check` -> Gemini classified the account state as `NOT_RESTRICTED confidence=0.99` on both attempts, so the audited failure was not an account restriction signal.
- `2026-05-24 code audit` -> `backend/comment_bot.py` defines `is_reels_page()` as `/reel/`, `/watch/`, or `/videos/`, and `post_comment_verified()` raises immediately when that predicate is true after navigation.
- `2026-05-24 code audit` -> `backend/fb_selectors.py` already contains a `REELS` selector group, but the campaign comment workflow does not use it for reel-target commenting.
- `2026-05-24 profile uniqueness clarification` -> uniqueness means no duplicate successful profile comments on the same target. Failed non-posted attempts may reuse a profile when evidence says the failure is not profile-specific.
- `2026-05-24 retry audit` -> bulk retry already excludes profiles that succeeded in the same campaign, while the primary queue path relies mostly on success-updated LRU and does not hard-cover successful-profile exclusion per target.
- `2026-05-24 Gemini live model probe` -> the live Gemini API lists `models/gemini-3.5-flash`, version `3.5-flash-05-2026`, with `generateContent`, `countTokens`, `createCachedContent`, and `batchGenerateContent`, plus `1048576` input and `65536` output token limits.
- `2026-05-24 Gemini docs probe` -> official Google Gemini 3.5 materials announce the new model family, while local project defaults still point text/vision paths at older or scattered model ids.
- `2026-05-24 model drift audit` -> `backend/config.py` defaults `GEMINI_MODEL` to `gemini-3-flash-preview`; community text/planner paths default to `gemini-2.5-flash`; reddit generation has its own override/fallback; image paths use `gemini-3-pro-image-preview`.
- `2026-05-24 capability boundary` -> `gemini-3.5-flash` is verified for text/multimodal generation, not as an image-generation replacement. Image workflows need their own `image` capability model in the same registry.
- `2026-05-24 local/prod proof boundary` -> local dev server and Browser Use can prove classification, validation, and UI/API wiring, but cannot prove Facebook selectors or production proxy/cookie-backed delivery because production sessions, fingerprints, and the active proxy store are on Railway volume.
