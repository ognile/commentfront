# Community Simulation

## north star
15 profiles autonomously participate in sister's private FB community on schedule — warmup on personal timelines, then coordinated group actions from a google sheet plan.

## exact success criteria
- supabase tables created and accessible via REST
- persona upsert endpoint works (POST /community/personas → 200)
- warmup plan generates ~150 tasks with distributed random times in supabase
- scheduler picks up due tasks every 60s and claims them
- join_group: profile navigates to group → clicks Join → screenshot proof
- warmup_post: profile posts AI-generated content on own timeline → screenshot proof
- post_in_group: profile posts in real group `groups/793232133423520` → screenshot proof
- like_post: profile likes real post `posts/917730744306991` → screenshot proof
- reply_to_post: profile replies to real post → screenshot proof
- failed tasks retry (attempts increment, reschedule)
- sheet import creates correctly timed community tasks
- scheduler starts automatically on Railway deploy

## constraints
- all FB actions run on PRODUCTION only (real sessions, real fingerprints via Railway)
- local = code functionality + supabase CRUD only. never test FB actions locally.
- reuse existing premium_actions.py functions — do NOT rebuild facebook automation
- no over-engineering. LLMs orchestrate, not bespoke comparison code.
- content rules from /Users/nikitalienov/Documents/writing/.claude/rules/ enforced on AI-generated warmup posts
- every executed action must have a screenshot as canonical proof

## current state
- premium_actions.py has all 4 FB action functions (publish_feed_post, discover_group_and_publish, perform_likes, perform_comment_replies)
- forensics.py has working supabase REST client pattern
- premium_scheduler.py has working 60s polling loop pattern
- gemini_image_gen.py has AI image generation
- no community-specific code exists yet

## active todo
1. create supabase tables
2. build community_store.py
3. build community_models.py
4. build community_content.py
5. build community_orchestrator.py
6. build community_scheduler.py
7. build community_plan_generator.py
8. add API endpoints to main.py + wire scheduler
9. deploy + verify each action on production

## open risks
- adaptive agent reliability for new action types (join_group, warmup_post)
- group join approval timing (depends on sister)
- proxy/session health for 15 profiles running multiple daily actions
- rate limiting if too many actions too fast
