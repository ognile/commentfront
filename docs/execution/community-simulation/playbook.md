# playbook

## default execution loop
1. implement next component from plan
2. deploy to production (push → railway auto-deploy)
3. verify via curl against production API
4. for FB actions: trigger via production API with real session → check facebook → screenshot proof
5. if fail → read railway logs → fix → redeploy → re-verify
6. if pass → mark done → next component

## stable tactics
- FastAPI body params: use `body: dict` not typed params (avoids parsing issues with X-API-Key auth)
- supabase bulk insert: all objects MUST have identical key sets (keep None values, don't strip them)
- background agent for deploy polling: launch with run_in_background=true, don't sleep in main thread

## failure patterns
- PostgREST PGRST102 "All object keys must match": bulk insert rows with different keys stripped → keep all keys with None

## verification patterns
- curl supabase REST for table existence
- curl production API endpoints for 200 responses
- check supabase for task state transitions
- railway logs for scheduler startup and task execution
- facebook verification for actual post/like/reply appearance

## promotion rules
- promote only evidence-backed reusable lessons
