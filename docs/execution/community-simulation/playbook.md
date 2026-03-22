# playbook

## default execution loop
1. implement next component from plan
2. deploy to production (push → railway auto-deploy)
3. verify via curl against production API
4. for FB actions: trigger via production API with real session → check facebook → screenshot proof
5. if fail → read railway logs → fix → redeploy → re-verify
6. if pass → mark done → next component

## stable tactics
- add only proven reusable tactics here

## failure patterns
- add recurring traps here

## verification patterns
- curl supabase REST for table existence
- curl production API endpoints for 200 responses
- check supabase for task state transitions
- railway logs for scheduler startup and task execution
- facebook verification for actual post/like/reply appearance

## promotion rules
- promote only evidence-backed reusable lessons
