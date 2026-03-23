import { adaptVerificationResponse } from './verification-adapter.ts';

type AdaptVerificationPayload = Parameters<typeof adaptVerificationResponse>[0];

export interface VerifyRouteConfig {
  backendUrl: string;
  apiKey: string;
  hasExplicitServerUrl: boolean;
  isProduction: boolean;
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

const KIMINA_LEAN_SERVER_URL = process.env.KIMINA_SERVER_URL || 'http://localhost:10000';
const KIMINA_SERVER_API_KEY = process.env.KIMINA_SERVER_API_KEY?.trim() ?? '';
const NODE_ENV = process.env.NODE_ENV;

const DEFAULT_ROUTE_CONFIG: VerifyRouteConfig = {
  backendUrl: KIMINA_LEAN_SERVER_URL,
  apiKey: KIMINA_SERVER_API_KEY,
  hasExplicitServerUrl: Boolean(process.env.KIMINA_SERVER_URL),
  isProduction: NODE_ENV === 'production',
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

    try {
      const syncResponse = await fetch(`${backendUrl}/api/check`, {
        method: 'POST',
        headers,
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(2500),
      });
      if (syncResponse.ok) {
        return {
          body: {
            jobId: null,
            status: 'completed',
            runtimeId,
            result: adaptVerificationResponse(await syncResponse.json()),
          },
          status: 200,
        };
      }
    } catch {
      // Cold runtime fast-path misses are handled by async submit below.
    }

    const submitResponse = await fetch(`${backendUrl}/api/async/check`, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(10000),
    });

    if (!submitResponse.ok) {
      const errorText = await submitResponse.text();
      return buildConfigErrorResponse(
        `Async submit failed: ${submitResponse.status} - ${errorText}`
      );
    }

    const submit = (await submitResponse.json()) as BackendAsyncSubmitResponse;
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
      signal: AbortSignal.timeout(5000),
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
