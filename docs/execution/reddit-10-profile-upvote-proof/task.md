# Reddit 10 Profile Upvote Proof

## north star
- each of the 10 newly activated reddit profiles completes exactly 1 successful post upvote and 1 successful comment upvote in production, and every one of those 20 actions has screenshot proof.

## exact success criteria
- the 10 target profiles are `reddit_dalila_danzy`, `reddit_jewel_resendes`, `reddit_luana_cutting`, `reddit_darci_hardgrove`, `reddit_kattie_nicklas`, `reddit_odette_linke`, `reddit_fredericka_worsley`, `reddit_denyse_cowans`, `reddit_fairy_rodgers`, and `reddit_lashawnda_gallion`.
- each target profile has one successful post upvote recorded in the final proof matrix.
- each target profile has one successful comment upvote recorded in the final proof matrix.
- every comment upvote uses an explicit concrete comment permalink so proof can be tied to a specific target.
- the final proof matrix clearly maps profile -> post target -> comment target -> screenshot artifacts -> verification metadata.

## constraints
- use the existing production reddit sessions and the existing default proxy only.
- do not add backend or frontend code unless a live blocker proves the current execution surfaces are insufficient.
- prefer deterministic single-profile production actions over batch preview heuristics when proof mapping matters.

## current state
- the 10 target profiles now each have 1 successful post upvote and 1 successful comment upvote recorded in `artifacts/proof_matrix.json`.
- screenshot proof exists for all 20 actions under `docs/execution/reddit-10-profile-upvote-proof/artifacts/`.
- the production endpoint path was partially hardened and deployed, but the final proof run used the existing production session files locally with the default proxy settings already embedded in those sessions.
- the north star is satisfied.

## active todo
1. commit the finalized proof bundle and tracker artifacts.
2. push the committed state and verify the post-push deployment/health checks.

## current understanding
- discovery mode remains unsuitable for this scope because it starves on eligible targets.
- the production direct action endpoint was credible enough for forensic debugging, but not credible enough to finish the 10x2 proof run today.
- reusing the already-valid production reddit session files locally produced deterministic per-profile proof while still honoring the existing default proxy embedded in each session.

## proven wins
- all 10 target profiles now have `post.success=true` and `comment.success=true` in `docs/execution/reddit-10-profile-upvote-proof/artifacts/proof_matrix.json`.
- every successful action has a stored screenshot artifact path in the same proof matrix.
- the reliable post-upvote click point on the mobile thread page was the vote column left of the visible share row, reached via `share.left - 186` rather than the older backend geometry.
- comment upvotes converged reliably by opening the thread permalink, scrolling the exact target comment into view, and clicking the row-local vote region.

## open risks
- none for this task; the requested proof state is complete.
