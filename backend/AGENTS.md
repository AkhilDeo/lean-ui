# AGENTS.md

## Workflow Orchestration
- Enter plan mode for any non-trivial task (3+ steps or architectural decisions).
- If execution diverges, stop and re-plan before continuing.
- Use subagents and parallel analysis where available to keep context focused.
- Keep one clear objective per worker or parallel branch.
- Use plan mode for verification design, not just implementation.

## Task Management
- Maintain `tasks/todo.md` with checkable plan items for active work.
- Maintain `tasks/lessons.md` and add an entry after every user correction.
- Required lesson entry schema: Date, Correction received, Root cause, New preventive rule, Where applied.
- Review relevant lessons before starting non-trivial work.
- Track progress continuously and record verification evidence before closing tasks.

## Core Principles
- Never mark work done without proof (tests, checks, logs, or explicit validation).
- Prefer the simplest solution that solves the root cause with minimal surface area.
- For non-trivial changes, pause and choose the most elegant maintainable design.
- Own bug reports end-to-end: reproduce, diagnose, fix, and verify without hand-holding.
- Avoid temporary compatibility layers unless explicitly required.

## Repo Appendix
- Repo context: Lean UI monorepo; this file governs backend contribution workflow.
- Setup and commands:
  - `cp .env.template .env`
  - `bash setup.sh`
  - `uv run pre-commit install`
  - `uv run pre-commit install --hook-type pre-push`
- Required verification:
  - `uv run pre-commit run --all-files`
  - `uv run pytest`
- PR title format: `[<project_name>] <Title>`.
