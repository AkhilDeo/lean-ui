# Cloudflare Migration Plan

This plan covers the backend services currently hosted on Railway. The frontend stays on Vercel.

## Current Railway Surface

Railway project `striking-fascination` contains the active Lean UI stack:

| Service | Current status | Role | Current size |
| --- | --- | --- | --- |
| `lean-ui` | sleeping | FastAPI API, sync checks, async submit/poll endpoints | 4 vCPU / 8 GB |
| `lean-ui-worker` | running | Redis-backed async worker pool | 4 vCPU / 32 GB |
| `lean-ui-redis` | running | Redis queue/result store | `redis:7` |

The same Railway project also contains failed or inactive `autoprover-*` services. The owner has confirmed they are unused; they will be deleted (not migrated) during Railway teardown, after a final log check confirms they are idle.

## Cloudflare Fit

Ordinary Cloudflare Workers are not a fit for Lean verification. The backend needs Python, Linux subprocesses, Lean/Mathlib files, long-running proof execution, and REPL reuse. Cloudflare Containers are the only Cloudflare runtime in scope for this backend.

Cloudflare Containers currently top out at `standard-4`: 4 vCPU, 12 GiB memory, and 20 GB disk per instance. That can plausibly fit the API service and a single conservative REPL worker, but it does not directly replace the current 32 GB Railway worker. The worker migration therefore needs proof before cutover.

## Target Architecture

Implemented architecture (see `backend/cloudflare/`):

- Keep Vercel as the frontend host.
- One Worker (`lean-ui-backend`) proxies all API routes to a single Durable-Object-backed
  `standard-4` container running the existing FastAPI server.
- No Redis and no separate worker service: the container runs with
  `LEAN_SERVER_EMBEDDED_WORKER_ENABLED=true` and
  `LEAN_SERVER_ASYNC_USE_IN_MEMORY_BACKEND=true`, so the async contract
  (`/api/async/check` submit/poll) is served in-process.
- Single Lean runtime `v4.9.0` (`LEAN_SERVER_MULTI_RUNTIME_ENABLED=false`) to fit the
  20 GB container disk cap; one REPL lane (`MAX_REPLS=1`, `MAX_REPL_MEM=8G`).
- Scale-to-zero with `sleepAfter = 10m`. Known tradeoff: in-memory async job state is
  lost when the container sleeps or restarts; results have a TTL and the frontend
  falls back to sync verification. Revisit with KV mirroring if it bites.
- `LEAN_SERVER_API_KEY` is a Worker secret forwarded into the container; auth stays
  in FastAPI, unchanged.

Cutover:

- Point Vercel `KIMINA_SERVER_URL` to the verified Cloudflare API endpoint.
- Keep `KIMINA_SERVER_API_KEY` matched with Cloudflare `LEAN_SERVER_API_KEY`.
- Run browser/API verification through the Vercel app.
- Delete only the replaced Railway services after Vercel is passing against Cloudflare.

## Cost and Latency Guardrails

- Do not leave multiple `standard-4` containers warm unless production traffic proves they are needed.
- Prefer one hot default-runtime lane over many cold lanes; cold Lean/Mathlib startup is the main latency risk.
- A single always-warm `standard-4` container is materially cheaper than the current 32 GB Railway worker only if CPU usage and extra worker instances stay low.
- Sharding workers into multiple 12 GiB Cloudflare containers may recover throughput, but each warm shard adds memory and disk billing.
- Rewriting Redis to Cloudflare Queues/Durable Objects is a separate product-risk project because the current async path depends on Redis lists, hashes, TTLs, polling, recovery, and metrics.

## Proof Required Before Railway Deletion

- `docker info`
- `npm run lint`
- `npm run test:frontend`
- `cd backend && uv run pytest`
- Cloudflare image build output with final image size
- `npx wrangler whoami`
- `npx wrangler deploy --config backend/cloudflare/wrangler.jsonc`
- `npx wrangler containers list`
- Live `GET /health`
- Live `GET /api/runtimes`
- Live `POST /api/check` with `#check Nat`
- Vercel app verification through `/api/verify`

Railway deletion is blocked until the live Vercel app is using Cloudflare and the above proof is recorded.
