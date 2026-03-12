# Task Plan

- [ ] Revert the pushed multi-environment Lean rollout commits while preserving the separate async-throughput Railway tuning edits. Proof: pending.
- [ ] Verify the repo is back to single-environment Lean `v4.15.0` behavior with backend tests and a frontend production build. Proof: pending.
- [ ] Roll Railway back to one public `lean-ui` Lean `v4.15.0` service, keep `lean-ui-worker`/`lean-ui-redis`, and delete the extra 4.27 checker services. Proof: pending.
- [ ] Validate production with authenticated sync and async smoke checks plus an async-heavy load test. Proof: pending.
