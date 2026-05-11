# Railway Single-Service Multi-Runtime Deployment

Production is intended to run as one Railway web service, `lean-ui`.

The service owns all seeded Lean/Mathlib runtimes inside one Docker image and selects the runtime in-process from the request `runtime_id`. There are no per-version runtime services, no gateway service, and no separate worker service.

## Seeded Runtimes

- `v4.9.0`
- `v4.15.0`
- `v4.24.0`
- `v4.27.0`
- `v4.28.0`

## Railway Service

Required posture:

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
LEAN_SERVER_EMBEDDED_WORKER_ENABLED=true
LEAN_SERVER_ASYNC_ENABLED=true
LEAN_SERVER_ASYNC_USE_IN_MEMORY_BACKEND=true
LEAN_SERVER_DEFAULT_RUNTIME_ID=v4.9.0
LEAN_SERVER_RUNTIME_ID=v4.9.0
LEAN_SERVER_LEAN_VERSION=v4.9.0
LEAN_SERVER_RUNTIME_IDS=v4.9.0,v4.15.0,v4.24.0,v4.27.0,v4.28.0
LEAN_SERVER_RUNTIME_ROOT=/runtimes
LEAN_SERVER_API_KEY=<shared-api-key>
LEAN_SERVER_INIT_REPLS={}
```

The Docker build installs each runtime under `/runtimes/<runtime_id_slug>/`, for example `/runtimes/v4_28_0/mathlib4` and `/runtimes/v4_28_0/repl/.lake/build/bin/repl`.

## Runtime Behavior

The app keeps one `Manager` per requested `runtime_id`. Switching Lean versions is a manager lookup, not a Railway service hop. REPLs are still reused by import header inside each runtime manager.

Set `LEAN_SERVER_MAX_REPLS=1` in the single-service Railway environment unless the Railway memory limit is raised. Because this value applies per runtime manager, increasing it can multiply the total number of live REPL processes across Lean versions.

## Validation

Use the helpers before and after deploy:

```sh
python scripts/validate_async_env.py single-service
python scripts/check_railway_state.py
```

The expected production state is exactly one Railway service in the project: `lean-ui`, with app sleeping enabled.
