# Local UI Smoke

- frontend verified at `http://127.0.0.1:4175/` against the local backend on `http://127.0.0.1:8000/`.
- authenticated as the local `admin` user and confirmed the `Sessions` tab rendered the two saved facebook sessions.
- opened remote control for `FB_Android_1` and confirmed the new modal renders the lease-oriented state:
  - platform badge `facebook`
  - session label `FB_Android_1`
  - viewer count
  - disabled restart/go controls until the browser becomes ready
  - action-log empty state mentioning keyboard capture
- the modal then surfaced the expected local environment blocker as an operator-visible error:
  - toast: `no proxy available. configure PROXY_URL or persist a session proxy.`
  - connection state flipped from `connecting` to `disconnected`
- this confirms the frontend does not hang silently when the local environment lacks proxy configuration for remote browser startup.
