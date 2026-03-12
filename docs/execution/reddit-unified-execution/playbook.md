# playbook

## default execution loop
- define the terminal state before experiments begin

## stable tactics
- add only proven reusable tactics here
- carry canonical `execution_spec` directly on compiled work items, then derive any runtime-facing legacy fields from that spec at refresh time. this migrates stored programs without a destructive one-shot rewrite.
- execute ad hoc reddit runs by compiling a temporary single-day program and running it through the existing orchestrator. this reuses discovery, generation, verification, forensic evidence, and target-history logic instead of cloning it into a second executor.

## failure patterns
- add recurring traps here
- if explicit program assignments do not persist `execution_spec`, the compiler silently falls back to the old `comment_post` default and preview/run requests collapse into the wrong shape.
- if `create_post` items derive `target_url` from the subreddit before execution, the real created post permalink gets wiped on save and proof rows lose their durable target reference.

## verification patterns
- add proof rules here
- use `/reddit/executions/preview` to verify the full action/target capability matrix locally, including runtime-action mapping and target-mode derivation, before attempting live runs.
- use fixture sessions plus public reddit targets for local `run` coverage on browse/open flows; reserve auth-required proof for production with real persisted sessions.
- for ui verification, authenticate locally with generated jwt tokens in local storage, then inspect the reddit advanced-tools rail to confirm the canonical target kind/strategy controls and comment-target field are actually rendered.

## promotion rules
- promote only evidence-backed reusable lessons
