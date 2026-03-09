# playbook

## default execution loop
- define the terminal state before experiments begin

## stable tactics
- for reddit mobile, fix the shared executor first; do not fork duplicate action paths to work around wrapper-state bugs
- when a reddit action fails, inspect the screenshot before changing selectors; navigation drift, modal overlays, and verification drift look different and need different fixes
- after a failed production run, rotate the next run to a new profile or a new target set even when the code changed; that keeps recovery paths unique and gives cleaner evidence

## failure patterns
- thread urls can silently degrade into subreddit feeds on some sessions; verify thread context before assuming a missing composer means the post page is loaded
- the mobile `view in reddit app` sheet can block reply flows even when the target comment and reply button are visible
- vote verification cannot assume the old orange reddit accent; cool-color active states can still represent a real upvote

## verification patterns
- treat prod forensic screenshots as the fastest source of truth when reddit mobile state is ambiguous

## promotion rules
- promote only evidence-backed reusable lessons
