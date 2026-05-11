# Railway Redis-Backed Multi-Runtime Deployment

Production is intended to run one Railway API service, one Redis service, and one Lean worker service:

- `lean-ui`: FastAPI API service. It accepts sync requests and enqueues async requests.
- `lean-ui-redis`: Redis queue/result store for durable async jobs.
- `lean-ui-worker`: worker pool process (`python -m server.worker`) that consumes Redis tasks across all supported `runtime_id` queues.

The API and worker images own all seeded Lean/Mathlib runtimes inside one Docker image and select the runtime in-process from the request `runtime_id`. There are no per-version runtime services and no gateway service.

## Seeded Runtimes

- `v4.9.0`
- `v4.15.0`
- `v4.24.0`
- `v4.27.0`
- `v4.28.0`

## Railway Services

Required API posture:

```sh
sleepApplication=true
numReplicas=1
startCommand="python -m server"
rootDirectory="/backend"
railwayConfigFile="/backend/railway.toml"
```

Required env:

```sh
LEAN_SERVER_ENVIRONMENT=prod
LEAN_SERVER_GATEWAY_ENABLED=false
LEAN_SERVER_MULTI_RUNTIME_ENABLED=true
LEAN_SERVER_EMBEDDED_WORKER_ENABLED=false
LEAN_SERVER_ASYNC_ENABLED=true
LEAN_SERVER_ASYNC_USE_IN_MEMORY_BACKEND=false
LEAN_SERVER_REDIS_URL=<railway-redis-url>
LEAN_SERVER_DEFAULT_RUNTIME_ID=v4.9.0
LEAN_SERVER_RUNTIME_ID=v4.9.0
LEAN_SERVER_LEAN_VERSION=v4.9.0
LEAN_SERVER_RUNTIME_IDS=v4.9.0,v4.15.0,v4.24.0,v4.27.0,v4.28.0
LEAN_SERVER_RUNTIME_ROOT=/runtimes
LEAN_SERVER_MAX_REPLS=2
LEAN_SERVER_MAX_TOTAL_REPLS=2
LEAN_SERVER_API_KEY=<shared-api-key>
LEAN_SERVER_INIT_REPLS={}
```

Required worker posture:

```sh
sleepApplication=false
numReplicas=1
startCommand="python -m server.worker"
rootDirectory="/backend"
railwayConfigFile="/backend/railway.worker.toml"
LEAN_SERVER_MULTI_RUNTIME_ENABLED=true
LEAN_SERVER_ASYNC_ENABLED=true
LEAN_SERVER_ASYNC_USE_IN_MEMORY_BACKEND=false
LEAN_SERVER_REDIS_URL=<railway-redis-url>
LEAN_SERVER_MAX_REPLS=4
LEAN_SERVER_MAX_TOTAL_REPLS=3
LEAN_SERVER_ASYNC_WORKER_CONCURRENCY=3
```

The Docker build installs each runtime under `/runtimes/<runtime_id_slug>/`, for example `/runtimes/v4_28_0/mathlib4` and `/runtimes/v4_28_0/repl/.lake/build/bin/repl`.

## Runtime Behavior

The app keeps one `Manager` per requested `runtime_id`. Switching Lean versions is a manager lookup, not a Railway service hop. REPLs are still reused by import header inside each runtime manager.

Use `LEAN_SERVER_MAX_REPLS` as the per-runtime lane limit and `LEAN_SERVER_MAX_TOTAL_REPLS` as the process-wide cap across all runtime managers. This allows more than one warm REPL for burst throughput while keeping memory bounded during multi-version traffic.

## Validation

Use the helpers before and after deploy:

```sh
python scripts/validate_async_env.py single-service
python scripts/validate_async_env.py worker
python scripts/check_railway_state.py
```

The expected production state is `lean-ui`, `lean-ui-redis`, and `lean-ui-worker`. The API and worker should both use Redis-backed async (`LEAN_SERVER_ASYNC_USE_IN_MEMORY_BACKEND=false`), and the worker should have `LEAN_SERVER_MULTI_RUNTIME_ENABLED=true` plus `LEAN_SERVER_MAX_TOTAL_REPLS` set.
