# Railway Multi-Runtime Deployment

This backend now runs in two service modes on Railway:

- `gateway`: always-on API service that validates runtime ids, proxies warm sync checks, wakes cold runtimes, and serves async job status.
- `runtime`: one service per seeded Lean/Mathlib runtime. Each runtime serves `/api/check`, drains only its own async work, and scales back to zero when idle.

## Seeded runtimes

- `v4.9.0`
- `v4.15.0`
- `v4.19.0`
- `v4.21.0`
- `v4.26.0`
- `v4.27.0`
- `v4.28.0`

## Gateway service

Required core env:

```sh
LEAN_SERVER_ENVIRONMENT=prod
LEAN_SERVER_GATEWAY_ENABLED=true
LEAN_SERVER_EMBEDDED_WORKER_ENABLED=false
LEAN_SERVER_ASYNC_ENABLED=true
LEAN_SERVER_DEFAULT_RUNTIME_ID=v4.28.0
LEAN_SERVER_RAILWAY_ENVIRONMENT_ID=<railway-environment-id>
LEAN_SERVER_REDIS_URL=<shared-redis-url>
LEAN_SERVER_API_KEY=<shared-api-key>
```

Required per-runtime Railway wiring:

```sh
LEAN_SERVER_RUNTIME_V4_9_0_SERVICE_ID=<service-id>
LEAN_SERVER_RUNTIME_V4_9_0_BASE_URL=<private-url>
LEAN_SERVER_RUNTIME_V4_15_0_SERVICE_ID=<service-id>
LEAN_SERVER_RUNTIME_V4_15_0_BASE_URL=<private-url>
LEAN_SERVER_RUNTIME_V4_19_0_SERVICE_ID=<service-id>
LEAN_SERVER_RUNTIME_V4_19_0_BASE_URL=<private-url>
LEAN_SERVER_RUNTIME_V4_21_0_SERVICE_ID=<service-id>
LEAN_SERVER_RUNTIME_V4_21_0_BASE_URL=<private-url>
LEAN_SERVER_RUNTIME_V4_26_0_SERVICE_ID=<service-id>
LEAN_SERVER_RUNTIME_V4_26_0_BASE_URL=<private-url>
LEAN_SERVER_RUNTIME_V4_27_0_SERVICE_ID=<service-id>
LEAN_SERVER_RUNTIME_V4_27_0_BASE_URL=<private-url>
LEAN_SERVER_RUNTIME_V4_28_0_SERVICE_ID=<service-id>
LEAN_SERVER_RUNTIME_V4_28_0_BASE_URL=<private-url>
```

The gateway will now fail fast at startup if any seeded runtime is missing `SERVICE_ID` or `BASE_URL`.

## Runtime service

Each runtime service gets its own matching Lean version and service id:

```sh
LEAN_SERVER_ENVIRONMENT=prod
LEAN_SERVER_GATEWAY_ENABLED=false
LEAN_SERVER_EMBEDDED_WORKER_ENABLED=true
LEAN_SERVER_ASYNC_ENABLED=true
LEAN_SERVER_RUNTIME_ID=v4.28.0
LEAN_SERVER_LEAN_VERSION=v4.28.0
LEAN_SERVER_RUNTIME_SERVICE_ID=<this-runtime-service-id>
LEAN_SERVER_RAILWAY_ENVIRONMENT_ID=<railway-environment-id>
LEAN_SERVER_REDIS_URL=<shared-redis-url>
LEAN_SERVER_API_KEY=<shared-api-key>
LEAN_SERVER_INIT_REPLS={}
```

The runtime will fail fast if `LEAN_SERVER_RUNTIME_ID`, `LEAN_SERVER_RUNTIME_SERVICE_ID`, or `LEAN_SERVER_RAILWAY_ENVIRONMENT_ID` is missing, if `LEAN_SERVER_LEAN_VERSION` does not match the runtime id, or if `LEAN_SERVER_INIT_REPLS` is non-empty while the embedded worker is enabled.

## Validation

Use the env validation helper before deploy:

```sh
python scripts/validate_async_env.py gateway
python scripts/validate_async_env.py runtime
```

## Logging

Google Cloud Logging has been removed from the backend. Railway log capture is the supported production log sink.
