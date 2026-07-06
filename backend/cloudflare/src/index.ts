import { Container, getContainer } from "@cloudflare/containers";

// Env for the FastAPI process inside the container. Single Lean runtime,
// embedded async worker with the in-memory queue backend — no Redis, no
// separate worker service. Sized for one 8G REPL on a standard-4 (12 GiB).
const LEAN_CONTAINER_ENV: Record<string, string> = {
  LEAN_SERVER_ENVIRONMENT: "prod",
  LEAN_SERVER_HOST: "0.0.0.0",
  LEAN_SERVER_PORT: "8080",
  LEAN_SERVER_GATEWAY_ENABLED: "false",
  LEAN_SERVER_AUTOSCALE_ENABLED: "false",
  // Multi-runtime with a single runtime id: the single-runtime + embedded
  // worker path hard-requires Railway service ids in runtime_registry.py.
  LEAN_SERVER_MULTI_RUNTIME_ENABLED: "true",
  LEAN_SERVER_EMBEDDED_WORKER_ENABLED: "true",
  LEAN_SERVER_ASYNC_ENABLED: "true",
  LEAN_SERVER_ASYNC_USE_IN_MEMORY_BACKEND: "true",
  LEAN_SERVER_ASYNC_WORKER_CONCURRENCY: "1",
  LEAN_SERVER_ASYNC_LIGHT_WARM_REPLS: '{"import Mathlib": 1}',
  LEAN_SERVER_ASYNC_HEAVY_WARM_REPLS: "{}",
  LEAN_SERVER_DEFAULT_RUNTIME_ID: "v4.9.0",
  LEAN_SERVER_RUNTIME_ID: "v4.9.0",
  LEAN_SERVER_LEAN_VERSION: "v4.9.0",
  LEAN_SERVER_RUNTIME_IDS: "v4.9.0",
  LEAN_SERVER_RUNTIME_ROOT: "/runtimes",
  LEAN_SERVER_MAX_REPLS: "1",
  LEAN_SERVER_MAX_TOTAL_REPLS: "1",
  LEAN_SERVER_MAX_REPL_MEM: "8G",
  LEAN_SERVER_MIN_HOST_FREE_MEM: "2G",
  LEAN_SERVER_MAX_REPL_USES: "64",
  LEAN_SERVER_MAX_WAIT: "120",
  LEAN_SERVER_INIT_REPLS: "{}",
  LEAN_SERVER_REQUEST_TIMEOUT_MAX_SEC: "180",
  LEAN_SERVER_DATABASE_URL: "",
};

interface WorkerEnv {
  LEAN_API: DurableObjectNamespace<LeanApiContainer>;
  LEAN_SERVER_API_KEY?: string;
}

export class LeanApiContainer extends Container<WorkerEnv> {
  defaultPort = 8080;
  sleepAfter = "10m";

  constructor(ctx: DurableObjectState<{}>, env: WorkerEnv) {
    super(ctx, env);
    this.envVars = {
      ...LEAN_CONTAINER_ENV,
      ...(env.LEAN_SERVER_API_KEY
        ? { LEAN_SERVER_API_KEY: env.LEAN_SERVER_API_KEY }
        : {}),
    };
  }

  override onError(error: unknown) {
    console.error("Lean API container error", error);
    throw error;
  }
}

export default {
  async fetch(request: Request, env: WorkerEnv): Promise<Response> {
    const container = getContainer(env.LEAN_API, "lean-api-v490");
    return container.fetch(request);
  },
};
