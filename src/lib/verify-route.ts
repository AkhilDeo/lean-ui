import { adaptVerificationResponse } from './verification-adapter.ts';

type AdaptVerificationPayload = Parameters<typeof adaptVerificationResponse>[0];

export interface VerifyRouteConfig {
  backendUrl: string;
  apiKey: string;
  hasExplicitServerUrl: boolean;
  isProduction: boolean;
  syncTimeoutMs?: number;
  asyncSubmitTimeoutMs?: number;
  pollTimeoutMs?: number;
  warmupRetryWindowMs?: number;
  warmupRetryDelayMs?: number;
  warmupRetryMaxAttempts?: number;
}

export interface VerifyRouteResponse {
  body: unknown;
  status: number;
}

interface BackendAsyncSubmitResponse {
  job_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
}

interface BackendAsyncPollResponse {
  job_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed';
  results?: unknown[];
  error?: string | null;
}

interface BackendRuntimeDescriptor {
  runtime_id: string;
  display_name: string;
  lean_version: string;
  is_default: boolean;
}

interface BackendRuntimeRegistryResponse {
  default_runtime_id: string;
  runtimes: BackendRuntimeDescriptor[];
}

interface SyncCheckSuccess {
  kind: 'success';
  payload: AdaptVerificationPayload;
}

interface SyncCheckHttpError {
  kind: 'http_error';
  status: number;
  errorText: string;
}

interface SyncCheckNetworkError {
  kind: 'network_error';
  errorMessage: string;
}

type SyncCheckResult = SyncCheckSuccess | SyncCheckHttpError | SyncCheckNetworkError;

interface AsyncSubmitSuccess {
  kind: 'success';
  payload: BackendAsyncSubmitResponse;
}

interface AsyncSubmitHttpError {
  kind: 'http_error';
  status: number;
  errorText: string;
}

interface AsyncSubmitNetworkError {
  kind: 'network_error';
  errorMessage: string;
}

type AsyncSubmitResult = AsyncSubmitSuccess | AsyncSubmitHttpError | AsyncSubmitNetworkError;

const KIMINA_LEAN_SERVER_URL = process.env.KIMINA_SERVER_URL || 'http://localhost:10000';
const KIMINA_SERVER_API_KEY = process.env.KIMINA_SERVER_API_KEY?.trim() ?? '';
const NODE_ENV = process.env.NODE_ENV;
const DEFAULT_SYNC_TIMEOUT_MS = 2500;
const DEFAULT_ASYNC_SUBMIT_TIMEOUT_MS = 10000;
const DEFAULT_POLL_TIMEOUT_MS = 5000;
const DEFAULT_WARMUP_RETRY_WINDOW_MS = 8000;
const DEFAULT_WARMUP_RETRY_DELAY_MS = 750;
const RUNTIME_WARMING_MESSAGE =
  'Verification server is waking the selected runtime. Please try again in a few seconds.';

const DEFAULT_ROUTE_CONFIG: VerifyRouteConfig = {
  backendUrl: KIMINA_LEAN_SERVER_URL,
  apiKey: KIMINA_SERVER_API_KEY,
  hasExplicitServerUrl: Boolean(process.env.KIMINA_SERVER_URL),
  isProduction: NODE_ENV === 'production',
  syncTimeoutMs: DEFAULT_SYNC_TIMEOUT_MS,
  asyncSubmitTimeoutMs: DEFAULT_ASYNC_SUBMIT_TIMEOUT_MS,
  pollTimeoutMs: DEFAULT_POLL_TIMEOUT_MS,
  warmupRetryWindowMs: DEFAULT_WARMUP_RETRY_WINDOW_MS,
  warmupRetryDelayMs: DEFAULT_WARMUP_RETRY_DELAY_MS,
};

function normalizeBackendUrl(url: string): string {
  return url.replace(/\/+$/, '');
}

function buildBackendHeaders(apiKey: string): Record<string, string> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };

  if (apiKey) {
    headers.Authorization = `Bearer ${apiKey}`;
  }

  return headers;
}

function buildConfigErrorResponse(error: string): VerifyRouteResponse {
  return {
    body: {
      jobId: null,
      status: 'failed',
      error,
      result: {
        status: 'server_error',
        passed: false,
        error,
        warnings: [],
        infos: [],
        time: 0,
      },
    },
    status: 200,
  };
}

function buildWarmupErrorResponse(): VerifyRouteResponse {
  return buildConfigErrorResponse(RUNTIME_WARMING_MESSAGE);
}

function buildGenericErrorMessage(error: unknown, backendUrl: string): string {
  if (error instanceof Error) {
    if (error.name === 'AbortError') {
      return 'Request timeout while contacting the Lean gateway.';
    }
    if (error.message.includes('fetch failed')) {
      return `Cannot connect to Lean server at ${backendUrl}.`;
    }
    return error.message;
  }
  return 'Unknown verification error occurred.';
}

function normalizeErrorText(errorText: string): string {
  const trimmed = errorText.trim();
  if (!trimmed) {
    return 'Unknown server error';
  }

  try {
    const parsed = JSON.parse(trimmed) as { detail?: unknown; error?: unknown; message?: unknown };
    if (typeof parsed.detail === 'string' && parsed.detail.trim()) {
      return parsed.detail.trim();
    }
    if (typeof parsed.error === 'string' && parsed.error.trim()) {
      return parsed.error.trim();
    }
    if (typeof parsed.message === 'string' && parsed.message.trim()) {
      return parsed.message.trim();
    }
  } catch {
    // Fall back to raw upstream body when the payload is not JSON.
  }

  return trimmed;
}

function isGatewayColdStartError(status: number, errorText: string): boolean {
  const normalized = normalizeErrorText(errorText);
  return (
    status === 503 &&
    normalized.includes('is cold and is starting up') &&
    normalized.includes('/api/async/check')
  );
}

function isAsyncQueueDisabledError(status: number, errorText: string): boolean {
  return (
    status === 503 &&
    normalizeErrorText(errorText).includes('Async queue API is not enabled on this service')
  );
}

function buildUpstreamHttpError(prefix: string, status: number, errorText: string): string {
  return `${prefix}: ${status} - ${normalizeErrorText(errorText)}`;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function runSyncCheck(
  backendUrl: string,
  headers: Record<string, string>,
  payload: object,
  timeoutMs: number
): Promise<SyncCheckResult> {
  try {
    const response = await fetch(`${backendUrl}/api/check`, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(timeoutMs),
    });

    if (response.ok) {
      return {
        kind: 'success',
        payload: (await response.json()) as AdaptVerificationPayload,
      };
    }

    return {
      kind: 'http_error',
      status: response.status,
      errorText: await response.text(),
    };
  } catch (error) {
    return {
      kind: 'network_error',
      errorMessage: buildGenericErrorMessage(error, backendUrl),
    };
  }
}

async function runAsyncSubmit(
  backendUrl: string,
  headers: Record<string, string>,
  payload: object,
  timeoutMs: number
): Promise<AsyncSubmitResult> {
  try {
    const response = await fetch(`${backendUrl}/api/async/check`, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(timeoutMs),
    });

    if (response.ok) {
      return {
        kind: 'success',
        payload: (await response.json()) as BackendAsyncSubmitResponse,
      };
    }

    return {
      kind: 'http_error',
      status: response.status,
      errorText: await response.text(),
    };
  } catch (error) {
    return {
      kind: 'network_error',
      errorMessage: buildGenericErrorMessage(error, backendUrl),
    };
  }
}

async function retrySyncUntilWarm(
  backendUrl: string,
  headers: Record<string, string>,
  payload: object,
  config: VerifyRouteConfig
): Promise<SyncCheckResult | 'warmup_timeout'> {
  const deadline = Date.now() + (config.warmupRetryWindowMs ?? DEFAULT_WARMUP_RETRY_WINDOW_MS);
  const delayMs = config.warmupRetryDelayMs ?? DEFAULT_WARMUP_RETRY_DELAY_MS;
  const maxAttempts = config.warmupRetryMaxAttempts ?? Number.POSITIVE_INFINITY;
  let attemptCount = 0;

  while (Date.now() <= deadline && attemptCount < maxAttempts) {
    attemptCount += 1;
    const result = await runSyncCheck(
      backendUrl,
      headers,
      payload,
      config.syncTimeoutMs ?? DEFAULT_SYNC_TIMEOUT_MS
    );

    if (result.kind === 'success') {
      return result;
    }

    if (result.kind === 'network_error') {
      return result;
    }

    if (!isGatewayColdStartError(result.status, result.errorText)) {
      return result;
    }

    if (Date.now() >= deadline || attemptCount >= maxAttempts) {
      break;
    }

    await sleep(Math.min(delayMs, Math.max(deadline - Date.now(), 0)));
  }

  return 'warmup_timeout';
}

function validateServerConfig(config: VerifyRouteConfig): VerifyRouteResponse | null {
  if (!config.hasExplicitServerUrl && config.isProduction) {
    return buildConfigErrorResponse(
      'KIMINA_SERVER_URL environment variable is not configured. Please set it in your Vercel project settings.'
    );
  }

  if (config.isProduction && config.hasExplicitServerUrl && !config.apiKey) {
    return buildConfigErrorResponse(
      'KIMINA_SERVER_API_KEY environment variable is not configured. Please set it in your Vercel project settings to match Railway LEAN_SERVER_API_KEY.'
    );
  }

  return null;
}

export async function handleVerifyPost(
  request: Request,
  config: VerifyRouteConfig = DEFAULT_ROUTE_CONFIG
): Promise<VerifyRouteResponse> {
  try {
    const validation = validateServerConfig(config);
    if (validation) {
      return validation;
    }

    const { code, runtimeId } = await request.json();

    if (!code || typeof code !== 'string') {
      return {
        body: { error: 'Code is required' },
        status: 400,
      };
    }

    if (!runtimeId || typeof runtimeId !== 'string') {
      return {
        body: { error: 'runtimeId is required' },
        status: 400,
      };
    }

    const backendUrl = normalizeBackendUrl(config.backendUrl);
    const payload = {
      snippets: [
        {
          id: 'verification',
          code,
        },
      ],
      runtime_id: runtimeId,
      reuse: false,
    };
    const headers = buildBackendHeaders(config.apiKey);
    const syncResult = await runSyncCheck(
      backendUrl,
      headers,
      payload,
      config.syncTimeoutMs ?? DEFAULT_SYNC_TIMEOUT_MS
    );

    if (syncResult.kind === 'success') {
      return {
        body: {
          jobId: null,
          status: 'completed',
          runtimeId,
          result: adaptVerificationResponse(syncResult.payload),
        },
        status: 200,
      };
    }

    if (syncResult.kind === 'network_error') {
      return buildConfigErrorResponse(syncResult.errorMessage);
    }

    if (!isGatewayColdStartError(syncResult.status, syncResult.errorText)) {
      return buildConfigErrorResponse(
        buildUpstreamHttpError('Sync verification failed', syncResult.status, syncResult.errorText)
      );
    }

    const submitResult = await runAsyncSubmit(
      backendUrl,
      headers,
      payload,
      config.asyncSubmitTimeoutMs ?? DEFAULT_ASYNC_SUBMIT_TIMEOUT_MS
    );

    if (
      submitResult.kind === 'network_error' ||
      (submitResult.kind === 'http_error' &&
        isAsyncQueueDisabledError(submitResult.status, submitResult.errorText))
    ) {
      const warmupResult = await retrySyncUntilWarm(backendUrl, headers, payload, config);
      if (warmupResult === 'warmup_timeout') {
        return buildWarmupErrorResponse();
      }
      if (warmupResult.kind === 'success') {
        return {
          body: {
            jobId: null,
            status: 'completed',
            runtimeId,
            result: adaptVerificationResponse(warmupResult.payload),
          },
          status: 200,
        };
      }
      if (warmupResult.kind === 'network_error') {
        return buildConfigErrorResponse(warmupResult.errorMessage);
      }
      return buildConfigErrorResponse(
        buildUpstreamHttpError(
          'Sync verification failed',
          warmupResult.status,
          warmupResult.errorText
        )
      );
    }

    if (submitResult.kind === 'http_error') {
      return buildConfigErrorResponse(
        buildUpstreamHttpError('Async submit failed', submitResult.status, submitResult.errorText)
      );
    }

    const submit = submitResult.payload;
    return {
      body: {
        jobId: submit.job_id,
        status: submit.status,
        runtimeId,
        result: null,
        error: null,
      },
      status: 200,
    };
  } catch (error) {
    return buildConfigErrorResponse(
      buildGenericErrorMessage(error, normalizeBackendUrl(config.backendUrl))
    );
  }
}

export async function handleVerifyPoll(
  jobId: string,
  config: VerifyRouteConfig = DEFAULT_ROUTE_CONFIG
): Promise<VerifyRouteResponse> {
  try {
    const validation = validateServerConfig(config);
    if (validation) {
      return validation;
    }

    const backendUrl = normalizeBackendUrl(config.backendUrl);
    const response = await fetch(`${backendUrl}/api/async/check/${jobId}?wait_sec=1`, {
      method: 'GET',
      headers: buildBackendHeaders(config.apiKey),
      signal: AbortSignal.timeout(config.pollTimeoutMs ?? DEFAULT_POLL_TIMEOUT_MS),
    });

    if (!response.ok) {
      const errorText = await response.text();
      return buildConfigErrorResponse(`Async poll failed: ${response.status} - ${errorText}`);
    }

    const poll = (await response.json()) as BackendAsyncPollResponse;
    if (poll.status === 'completed' && Array.isArray(poll.results)) {
      return {
        body: {
          jobId: poll.job_id,
          status: 'completed',
          result: adaptVerificationResponse({
            results: poll.results as AdaptVerificationPayload['results'],
          }),
          error: poll.error ?? null,
        },
        status: 200,
      };
    }

    if (poll.status === 'failed') {
      return {
        body: {
          jobId: poll.job_id,
          status: 'failed',
          error: poll.error ?? 'Verification job failed.',
          result: null,
        },
        status: 200,
      };
    }

    return {
      body: {
        jobId: poll.job_id,
        status: poll.status,
        result: null,
        error: poll.error ?? null,
      },
      status: 200,
    };
  } catch (error) {
    return buildConfigErrorResponse(
      buildGenericErrorMessage(error, normalizeBackendUrl(config.backendUrl))
    );
  }
}

export async function handleRuntimesGet(
  config: VerifyRouteConfig = DEFAULT_ROUTE_CONFIG
): Promise<VerifyRouteResponse> {
  try {
    const validation = validateServerConfig(config);
    if (validation) {
      return validation;
    }

    const backendUrl = normalizeBackendUrl(config.backendUrl);
    const response = await fetch(`${backendUrl}/api/runtimes`, {
      method: 'GET',
      headers: buildBackendHeaders(config.apiKey),
      signal: AbortSignal.timeout(5000),
    });

    if (!response.ok) {
      const errorText = await response.text();
      return {
        body: { error: `Failed to load runtimes: ${response.status} - ${errorText}` },
        status: 502,
      };
    }

    const body = (await response.json()) as BackendRuntimeRegistryResponse;
    return {
      body: {
        defaultRuntimeId: body.default_runtime_id,
        runtimes: body.runtimes.map((runtime) => ({
          runtimeId: runtime.runtime_id,
          displayName: runtime.display_name,
          leanVersion: runtime.lean_version.replace(/^v/, ''),
          isDefault: runtime.is_default,
        })),
      },
      status: 200,
    };
  } catch (error) {
    return {
      body: { error: buildGenericErrorMessage(error, normalizeBackendUrl(config.backendUrl)) },
      status: 502,
    };
  }
}
