import { adaptVerificationResponse } from './verification-adapter.ts';

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

const KIMINA_LEAN_SERVER_URL = process.env.KIMINA_SERVER_URL || 'http://localhost:10000';
const KIMINA_SERVER_API_KEY = process.env.KIMINA_SERVER_API_KEY?.trim() ?? '';
const NODE_ENV = process.env.NODE_ENV;

const DEFAULT_ROUTE_CONFIG: VerifyRouteConfig = {
  backendUrl: KIMINA_LEAN_SERVER_URL,
  apiKey: KIMINA_SERVER_API_KEY,
  hasExplicitServerUrl: Boolean(process.env.KIMINA_SERVER_URL),
  isProduction: NODE_ENV === 'production',
};

function buildVerifyErrorResponse(error: string): VerifyRouteResponse {
  return {
    body: {
      status: 'server_error',
      passed: false,
      error,
      warnings: [],
      infos: [],
    },
    status: 200,
  };
}

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

export async function handleVerifyPost(
  request: Request,
  config: VerifyRouteConfig = DEFAULT_ROUTE_CONFIG
): Promise<VerifyRouteResponse> {
  try {
    const { code } = await request.json();

    if (!code || typeof code !== 'string') {
      return {
        body: { error: 'Code is required' },
        status: 400,
      };
    }

    if (!config.hasExplicitServerUrl && config.isProduction) {
      return buildVerifyErrorResponse(
        'KIMINA_SERVER_URL environment variable is not configured. Please set it in your Vercel project settings.'
      );
    }

    if (config.isProduction && config.hasExplicitServerUrl && !config.apiKey) {
      return buildVerifyErrorResponse(
        'KIMINA_SERVER_API_KEY environment variable is not configured. Please set it in your Vercel project settings to match Railway LEAN_SERVER_API_KEY.'
      );
    }

    const backendUrl = normalizeBackendUrl(config.backendUrl);
    const response = await fetch(`${backendUrl}/api/check`, {
      method: 'POST',
      headers: buildBackendHeaders(config.apiKey),
      body: JSON.stringify({
        snippets: [
          {
            id: 'verification',
            code,
          },
        ],
        reuse: false,
      }),
      signal: AbortSignal.timeout(30000),
    });

    if (!response.ok) {
      const errorText = await response.text();
      return buildVerifyErrorResponse(`Server error: ${response.status} - ${errorText}`);
    }

    const result = await response.json();
    return {
      body: adaptVerificationResponse(result),
      status: 200,
    };
  } catch (error) {
    console.error('Verification error:', error);

    let errorMessage = 'Unknown error occurred';

    if (error instanceof Error) {
      if (error.name === 'AbortError') {
        errorMessage = 'Request timeout: The Lean server took too long to respond (>30s)';
      } else if (error.message.includes('fetch failed')) {
        errorMessage = `Cannot connect to Lean server at ${config.backendUrl}. Please check: 1) KIMINA_SERVER_URL is set correctly in Vercel, 2) The backend server is running and accessible, 3) Network/firewall settings allow the connection.`;
      } else {
        errorMessage = error.message;
      }
    }

    return buildVerifyErrorResponse(errorMessage);
  }
}
