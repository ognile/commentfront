# reddit capability proof

## north star
- use one production reddit session unless a real platform block forces a switch
- every action uses an explicit target
- every action follows an operator-like path: anchor, pointer click, verify state
- every action is only done after a production success with forensic evidence

## success criteria
- `comment_post`: already proven baseline
- `upvote_post`: exact target post is upvoted and the forensic timeline shows the target row interaction plus post-click state change
- `upvote_comment`: exact target comment is upvoted and the forensic timeline shows the target comment row interaction plus post-click state change
- `join_subreddit`: target women’s-health-related subreddit moves from `join` to `joined`
- `reply_comment`: reply lands under the intended comment, not just somewhere on the thread

## active todo
1. harden `join_subreddit`
expected output: deterministic subreddit-header join path
verification: one production success with `final_verdict=success_confirmed`, screenshot showing `joined`, and matching forensic timeline

2. harden `upvote_post`
expected output: deterministic post action-row vote path after scrolling into the target row
verification: one production success with target-post screenshot evidence and matching forensic timeline

3. harden `upvote_comment`
expected output: dedicated comment-row vote path anchored to the target comment
verification: one production success on a concrete comment permalink with screenshot evidence and matching forensic timeline

4. harden `reply_comment`
expected output: anchored reply path that opens the reply box on the intended comment row
verification: one production success with screenshot showing the reply under the target comment and matching forensic timeline

5. lock shared learnings
expected output: reusable helpers for anchor detection, pointer clicks, row-state verification, and failure-state capture
verification: all four actions proven in production without manual rescue

## execution rule
- if a production proof fails, inspect its forensics, adapt the code, redeploy, and rerun until the target action is genuinely proven
