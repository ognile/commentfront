# Production Frontend UI Smoke

- Date: 2026-03-12
- Frontend deployment: `dpl_CvTB2NLLifm4iiAX19JLfLPr2Ee3`
- Alias: `https://commentfront.vercel.app`
- Verified with Playwright using an `ognile` production access token stored in `commentbot_access_token`.

## Observed path

1. Loaded the production app and confirmed the authenticated shell rendered for `ognile`.
2. Opened the `Sessions` tab and verified `Adele Compton` appeared in the production session list.
3. Clicked the session's remote-control button.
4. Confirmed the remote modal connected on the live backend and rendered:
   - platform badge: `facebook`
   - role badge: `controller`
   - viewer count: `1`
   - controller label: `ognile`
   - url input: `https://m.facebook.com/`
   - browser frame image present
   - lease id visible (`c6104c4a0580` during the UI smoke)
5. Closed the modal and then explicitly stopped the zero-viewer lease through the backend API so production returned to `count=0`.

## Result

- The shipped frontend can still open the remote-control entry path against the cleaned-up backend and render a live Facebook frame without legacy transport support.
