# Lessons

Capture a new entry after every user correction.

## Entry Template
- Date:
- Correction received:
- Root cause:
- New preventive rule:
- Where applied:

## Entries
- Add newest entries at the top.
- Date: 2026-03-12
- Correction received: When rolling back the multi-environment work, preserve the separate Railway throughput changes that are already being deployed instead of reverting Railway indiscriminately.
- Root cause: I initially framed the rollback as a broad return to the pre-rollout state and did not explicitly separate multi-environment routing changes from the independent async-capacity tuning work.
- New preventive rule: When reverting a feature that overlaps with active infra work, identify and preserve orthogonal production-tuning changes before applying repo or deploy rollbacks.
- Where applied: `tasks/todo.md`, the single-environment rollback plan, and the targeted Railway cleanup for `lean-ui`.
- Date: 2026-03-12
- Correction received: Push the full worktree and take the feature all the way through commit, push, Railway deploy, and production verification instead of stopping at local implementation.
- Root cause: I treated repository implementation and local validation as a sufficient handoff point even though the explicit request required finishing the release path on the live deployment.
- New preventive rule: When the user asks to push and deploy, treat local implementation as incomplete until the changes are committed, pushed, deployed, and production-checked.
- Where applied: `tasks/todo.md`, this multi-environment gateway rollout, and the Railway deploy/debug loop.
- Date: 2026-03-11
- Correction received: Scope the rich `sorry` details work to the verification microservice only, not downstream gateway/client/prover/UI consumers.
- Root cause: I treated the broader contract discussion as an end-to-end implementation candidate instead of locking scope to the explicitly corrected subsystem.
- New preventive rule: When a user narrows scope after exploratory planning, update the implementation target immediately and avoid carrying speculative downstream changes into the active task.
- Where applied: `tasks/todo.md`, the rich `sorry` details implementation plan, and this execution pass.
- Date: 2026-03-09
- Correction received: Do not stop after blocked or partial rounds; continue iterating until at least 10 valid production loops have completed.
- Root cause: I treated earlier blocked deploys and incomplete iteration counts as acceptable pause points instead of resuming the commit/push/deploy benchmark loop immediately.
- New preventive rule: For this throughput initiative, keep running autonomous production iterations until at least 10 valid loops are recorded, unless an explicit blocker is documented with proof.
- Where applied: `tasks/todo.md`, the current async recovery iteration, and the remaining production throughput loop.
- Date: 2026-03-09
- Correction received: Throughput work must run as an autonomous autoresearch loop with repeated web research, commit/push/deploy validation, and revert-on-regression behavior instead of a one-pass optimization sweep.
- Root cause: I treated research as a front-loaded phase and did not lock the workflow around iterative deployed experiments on `main`.
- New preventive rule: For production performance initiatives on this repo, structure the work as repeated research -> smallest change -> local verification -> commit/push -> Railway deploy -> production benchmark -> keep or revert, and record every iteration.
- Where applied: `tasks/todo.md`, backend throughput experiment loop design, iteration artifact plan, and all subsequent backend async/sync tuning work.
- Date: 2026-03-02
- Correction received: Railway production-tuning scripts are temporary ops tooling and should not be treated as permanent checked-in product code by default.
- Root cause: I optimized for rapid operability and reuse, but did not explicitly classify the scripts as ephemeral tooling in task tracking.
- New preventive rule: For infra-tuning helpers, default to "temporary local ops artifact" unless the user explicitly asks for long-term repository ownership.
- Where applied: `backend/scripts/railway_tune_prod_capacity.py`, `backend/scripts/check_railway_state.py`, `backend/scripts/validate_async_env.py`, execution/report notes for this recovery run.
- Date: 2026-03-02
- Correction received: Deliver full implementation now, including async-first reliability validation, revised sync p99 target (5s), and production Railway env-tuning support.
- Root cause: Initial responses over-indexed on planning/report structure before complete executable tooling.
- New preventive rule: For explicit "IMPLEMENT THIS PLAN" requests, immediately build runnable scripts/tests and only treat unavailable external data as blockers.
- Where applied: `backend/scripts/loadtest/loadtest_lean_server.py`, `backend/scripts/verify_async_redis_health.py`, `backend/scripts/railway_tune_prod_capacity.py`, `backend/tests/test_loadtest_script.py`, `tasks/todo.md`.
