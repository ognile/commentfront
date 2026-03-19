# Reddit 10 Profile Upvote Proof

## north star
- each of the 10 newly activated reddit profiles completes exactly 1 successful post upvote and 1 successful comment upvote in production, and every one of those 20 actions has screenshot proof.

## exact success criteria
- the 10 target profiles are `reddit_dalila_danzy`, `reddit_jewel_resendes`, `reddit_luana_cutting`, `reddit_darci_hardgrove`, `reddit_kattie_nicklas`, `reddit_odette_linke`, `reddit_fredericka_worsley`, `reddit_denyse_cowans`, `reddit_fairy_rodgers`, and `reddit_lashawnda_gallion`.
- each target profile has one production `upvote_post` result with `success=true`, `final_verdict=success_confirmed`, a non-empty `attempt_id`, and a non-empty screenshot proof url or artifact reference.
- each target profile has one production `upvote_comment` result with `success=true`, `final_verdict=success_confirmed`, a non-empty `attempt_id`, and a non-empty screenshot proof url or artifact reference.
- every comment upvote uses an explicit concrete comment permalink so proof can be tied to a specific target.
- the final proof matrix clearly maps profile -> post target -> comment target -> attempt ids -> screenshot artifacts.

## constraints
- use the existing production reddit sessions and the existing default proxy only.
- do not add backend or frontend code unless a live blocker proves the current execution surfaces are insufficient.
- prefer deterministic single-profile production actions over batch preview heuristics when proof mapping matters.

## current state
- the tracker folder exists but started as placeholder-only.
- production already has the 10 new reddit sessions from the prior rollout.
- `POST /reddit/actions/run` and `POST /reddit/executions/run` already support `upvote_post` and `upvote_comment`.
- live preview proved discovery has no eligible post/comment targets for this scope, while explicit pools resolve cleanly.
- multi-actor preview reused the first explicit target for every actor, so per-profile direct execution is the safer proof path.

## active todo
1. execute 10 production `upvote_post` actions, one per target profile, and capture attempt ids plus screenshot proof.
2. execute 10 production `upvote_comment` actions, one per target profile, with explicit comment permalinks and capture attempt ids plus screenshot proof.
3. verify the final 10x2 proof matrix, then update the tracker artifacts and commit the useful task records.

## current understanding
- the existing production execution surface is already enough for the north star.
- discovery mode is not viable for this task because it starves on eligible targets.
- explicit target pools are viable, but the cleanest proof path is single-profile direct actions so each success maps to one profile and one target without resolver ambiguity.

## proven wins
- production preview over all 10 actors already confirmed the current execution surface accepts the new profiles and can resolve explicit post/comment targets.
- the prior reddit 10-account rollout completed successfully, so the required production sessions already exist and are ready for action execution.

## open risks
- a live reddit anti-abuse or transient navigation failure could still affect individual actions, in which case the next hypothesis must come from attempt evidence rather than assumptions.
- screenshot proof may come back as an artifact reference path rather than a pre-expanded public url, so verification must accept the stored artifact field actually returned by the live endpoint.
