# Remote Browser Clipboard Bridge

## north star
- the production remote controller supports mac and windows `copy`, `cut`, `paste`, and `select all` against facebook and reddit browser sessions through explicit clipboard-aware remote actions, with plain typing unchanged and proof artifacts captured under this tracker.

## exact success criteria
- production facebook remote control proves `cmd+v` pastes a unique local clipboard sentinel into the live facebook comment composer after the operator clicks the composer, even when focus styling is not visibly obvious.
- production facebook remote control proves `cmd+a`, `cmd+c`, and `cmd+x` operate on the same composer correctly, with `cmd+x` deleting only after the copied text is safely written to the local clipboard.
- production reddit remote control still opens and supports normal typing after the clipboard shortcut changes.
- full local gate passes from committed worktree state: backend `pytest`, frontend `npm run test`, `npm run lint`, and `npm run build`.
- production deployment is verified from committed github state with captured deployment proof, live frontend proof, backend proof, and a final pass/fail matrix.

## constraints
- no dead code, no compatibility shim, no fallback clipboard path that bypasses the final design.
- shortcut scope is text-only clipboard for `cmd/ctrl+c`, `cmd/ctrl+v`, `cmd/ctrl+x`, and `cmd/ctrl+a`; broader editing shortcuts remain on the raw key path.
- production deploys must come only from committed github state and are not complete until railway and vercel both finish successfully.
- all meaningful work must be logged in `experiments.jsonl` with file-based evidence.

## current state
- frontend typing works because printable keys use explicit `text_input`.
- keyboard paste is broken because the controller `keydown` handler prevents default for modified keys before a native `paste` event can fire.
- keyboard copy and cut are not implemented at all in the remote action model.
- the live remote browser runs on linux chromium while the operator commonly uses mac shortcuts, so raw `meta` forwarding is the wrong shortcut model for clipboard editing.
- baseline production artifacts are captured in `artifacts/baseline/health.json`, `artifacts/baseline/remote-status.json`, and `artifacts/baseline/sessions.json`.
- the clipboard bridge implementation is now in the worktree across the remote hook, remote lease service, websocket action normalization, and focused tests.
- the full local gate is green with artifacts saved under `artifacts/local/`: backend `370 passed`, frontend tests `21 passed`, frontend build passed, and lint stayed at the same pre-existing `App.tsx` warnings.

## active todo
1. implement the clipboard bridge in frontend and backend with tests proving shortcut routing, action acknowledgements, selection handling, and focus snapshots.
2. run the full local gate and store outputs under `artifacts/local/`.
3. deploy from committed github state and prove facebook/reddit behavior in production with live artifacts under `artifacts/production/`.

## current understanding
- the root cause is split across both layers: the frontend swallows `cmd/ctrl+v` before `paste` can fire, and the backend has no explicit clipboard semantics for copy/cut/select-all.
- the existing paste test only covers a synthetic `paste` event, not the actual keyboard shortcut path used in production.
- the correct fix is a clipboard bridge with explicit remote actions plus action-result payloads that the frontend can use to update the operator clipboard.
- the frontend fix also needs delayed raw modifier dispatch so mac `command` is not leaked into the linux remote browser before the controller decides whether a shortcut is logical (`cmd+c/v/x/a`) or should stay on the raw key path.
- the backend fix is safest when delete is a separate action gated by an explicit `can_delete` selection snapshot, so cut never mutates the remote page before the local clipboard write succeeds.

## proven wins
- tracker initialized successfully at `docs/execution/remote-browser-clipboard-bridge/`.
- baseline production health, remote status, and session inventory artifacts were captured successfully before code changes.
- focused clipboard tests are green in both layers: frontend `src/hooks/useRemoteControl.test.tsx` now covers `meta+c/v/x/a`, plain typing, and clipboard read/write failures; backend remote lease tests cover input, textarea, contenteditable, and page selections plus focus snapshots.
- the full local gate is green and artifacted: `artifacts/local/backend-pytest.txt`, `artifacts/local/frontend-test.txt`, `artifacts/local/frontend-lint.txt`, and `artifacts/local/frontend-build.txt`.

## open risks
- production verification still needs a deterministic live frontend login, remote modal control against facebook and reddit, and artifact capture for the real `cmd+v` facebook composer flow.
