# Load Test Scripts

## Dataset Paths

The load tester expects these files:

- `backend/data/loadtest/proof_sft_verified.jsonl`
- `backend/data/loadtest/proof_sft_failed.jsonl`

## Run Baseline Diagnostic

```bash
python backend/scripts/loadtest/loadtest_lean_server.py \
  --mode both \
  --profile diag \
  --base-url https://lean-ui-production.up.railway.app
```

## Run Full Suite

```bash
python backend/scripts/loadtest/loadtest_lean_server.py \
  --mode both \
  --profile full \
  --base-url https://lean-ui-production.up.railway.app
```

Defaults include:

- sync p99 target: `5000 ms`
- async completion target: `>= 99.5%`
- async poll timeout rate target: `0`
- queue stall detector for `queued` + `running=0` persistence
- default code fields: `prompt, code, proof, lean_code, snippet, text`

Artifacts are written to `backend/outputs/loadtests/verification/`.

## Redis Health Check

```bash
python backend/scripts/verify_async_redis_health.py \
  --redis-url "$LEAN_SERVER_REDIS_URL" \
  --queue-name lean_async_check
```

## Railway Tuning (Dry Run)

```bash
python backend/scripts/railway_tune_prod_capacity.py

# Multi-environment gateway service configuration (dry-run)
python backend/scripts/railway_configure_multi_env.py

# The dry-run plan includes internal checker URLs, gateway validation URLs,
# and the pinned FormalConjectures commit used for the v4.27 service.
```

## Railway Tuning (Apply)

```bash
python backend/scripts/railway_tune_prod_capacity.py --execute --apply-limits

# Apply gateway + checker environment metadata / idle TTL settings
python backend/scripts/railway_configure_multi_env.py --execute --apply-limits
```
