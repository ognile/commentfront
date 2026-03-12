# playbook

## default execution loop
- define the terminal state before experiments begin

## stable tactics
- add only proven reusable tactics here
- for explicit reddit text actions, run operator copy through the same subreddit/thread-context gate as generated copy before posting. if the draft is repaired, treat the repair as provisional until deterministic validation passes again.

## failure patterns
- add recurring traps here
- action success without rendered-artifact review is a false finish. duplicated replies, clinical drift, and operator/meta copy can all survive a naive success flag.
- review-time repairs can still be wrong in a different way. a repaired draft that sounds formal, synthetic, or detached from the nearby conversation must still be blocked.

## verification patterns
- add proof rules here
- `success_confirmed` is not enough for reddit text actions unless the final artifact matches the effective posted copy exactly once and the duplicate/echo checks pass.
- for attachment proofs, exercise the actual upload endpoint before the run so the title/body gate and the media path are both verified from the same workspace state.

## promotion rules
- promote only evidence-backed reusable lessons
