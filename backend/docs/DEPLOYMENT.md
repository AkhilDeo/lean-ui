# Railway Multi-Runtime Deployment

This backend now runs in two service modes on Railway:

- `gateway`: always-on API service that validates runtime ids, proxies warm sync checks, wakes cold runtimes, and serves async job status.
- `runtime`: one service per seeded Lean/Mathlib runtime. Each runtime serves `/api/check` and drains only its own async work. The default runtime stays warm; the non-default runtimes use Railway Serverless so they can sleep when idle and wake on traffic.

## Seeded runtimes

- `v4.9.0`
- `v4.15.0`
- `v4.24.0`
- `v4.27.0`
- `v4.28.0`

## Gateway service

Required core env:

```sh
LEAN_SERVER_ENVIRONMENT=prod
LEAN_SERVER_GATEWAY_ENABLED=true
LEAN_SERVER_EMBEDDED_WORKER_ENABLED=false
LEAN_SERVER_ASYNC_ENABLED=true
LEAN_SERVER_DEFAULT_RUNTIME_ID=v4.9.0
LEAN_SERVER_RAILWAY_ENVIRONMENT_ID=<railway-environment-id>
LEAN_SERVER_REDIS_URL=<shared-redis-url>
LEAN_SERVER_API_KEY=<shared-api-key>
LEAN_SERVER_AUTOSCALE_RAILWAY_TOKEN=<railway-api-token>
```

Required per-runtime Railway wiring:

```sh
LEAN_SERVER_RUNTIME_V4_9_0_SERVICE_ID=<service-id>
LEAN_SERVER_RUNTIME_V4_9_0_BASE_URL=<private-url>
LEAN_SERVER_RUNTIME_V4_15_0_SERVICE_ID=<service-id>
LEAN_SERVER_RUNTIME_V4_15_0_BASE_URL=<private-url>
LEAN_SERVER_RUNTIME_V4_24_0_SERVICE_ID=<service-id>
LEAN_SERVER_RUNTIME_V4_24_0_BASE_URL=<private-url>
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
LEAN_SERVER_DEFAULT_RUNTIME_ID=v4.9.0
LEAN_SERVER_RUNTIME_ID=v4.15.0
LEAN_SERVER_LEAN_VERSION=v4.15.0
LEAN_SERVER_RUNTIME_SERVICE_ID=<this-runtime-service-id>
LEAN_SERVER_RAILWAY_ENVIRONMENT_ID=<railway-environment-id>
LEAN_SERVER_REDIS_URL=<shared-redis-url>
LEAN_SERVER_API_KEY=<shared-api-key>
LEAN_SERVER_AUTOSCALE_RAILWAY_TOKEN=<railway-api-token>
LEAN_SERVER_INIT_REPLS={}
LEAN_SERVER_ASYNC_RESULT_TTL_SEC=3600
```

The runtime will fail fast if `LEAN_SERVER_RUNTIME_ID`, `LEAN_SERVER_RUNTIME_SERVICE_ID`, or `LEAN_SERVER_RAILWAY_ENVIRONMENT_ID` is missing, if `LEAN_SERVER_LEAN_VERSION` does not match the runtime id, or if `LEAN_SERVER_INIT_REPLS` is non-empty while the embedded worker is enabled.

Recommended runtime posture:

- `v4.9.0`: keep Railway Serverless disabled so one service replica stays warm, set `LEAN_SERVER_MAX_REPLS=4`, `LEAN_SERVER_ASYNC_WORKER_CONCURRENCY=2`, and keep only a small warm pool.
- non-default runtimes: enable Railway Serverless so Railway sleeps the service when idle and wakes it on private-network traffic from the gateway; keep `LEAN_SERVER_MAX_REPLS=1` and `LEAN_SERVER_ASYNC_WORKER_CONCURRENCY=1`.

## Validation

Use the env validation helper before deploy:

```sh
python scripts/validate_async_env.py gateway
python scripts/validate_async_env.py runtime
```

## Logging

Google Cloud Logging has been removed from the backend. Railway log capture is the supported production log sink.
