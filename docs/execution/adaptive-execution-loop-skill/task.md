# Adaptive Execution Loop Skill

## north star
- ship a reusable global skill that creates durable repo-local execution memory for uncertain tasks with minimal operational friction

## exact success criteria
- the global skill exists at `/Users/nikitalienov/.codex/skills/adaptive-execution-loop`
- the skill package includes instructions, references, templates, and working helper scripts
- a sample task folder exists under `docs/execution/adaptive-execution-loop-skill/`
- the sample task contains a real experiment log, a live synthesized task file, and a promoted playbook
- the skill validates cleanly and the sample workflow proves the loop end to end

## constraints
- keep the tracker structure simple enough for humans and llms to scan quickly
- avoid redundant files that drift out of sync
- preserve append-only experiment history

## current state
- the global skill package has been created
- the repo-local sample task folder has been initialized with the new helper script
- four structured experiment records have been appended to the sample task
- the skill package validated cleanly and the metadata was regenerated from the final skill text
- rerunning task initialization preserved the existing log instead of overwriting it

## active todo
1. none; sample workflow proof is complete

## current understanding
- `experiments.jsonl` works best as the source of truth for uncertain work
- `task.md` should be the current synthesized view, not a duplicate log
- `playbook.md` should contain only promoted reusable lessons that survived experiments
- a global skill plus repo-local task folders is the right split between reuse and project-local inspectability
- idempotence checks are part of the core proof, not an optional extra

## proven wins
- the global skill scaffold is in place and customized for adaptive execution
- `init_task.py` created the repo-local sample task folder successfully
- `append_experiment.py` appended structured experiment records successfully
- the tracker design stayed minimal: `task.md`, `experiments.jsonl`, `playbook.md`
- `quick_validate.py` passed and rerunning `init_task.py` preserved the existing logs

## open risks
- the skill will only stay valuable if future agents keep the tracker files current instead of treating them as one-time setup
- promotion discipline matters; `playbook.md` will decay if one-off hacks are promoted as reusable rules
