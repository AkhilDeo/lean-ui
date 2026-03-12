import { NextRequest, NextResponse } from 'next/server';

import type { KiminaResponse, VerificationEnvironment } from '@/types/verification';

const KIMINA_LEAN_SERVER_URL = process.env.KIMINA_SERVER_URL || 'http://localhost:10000';

export async function POST(request: NextRequest) {
  let requestedEnvironment: VerificationEnvironment | undefined;
  try {
    const { code, environment } = (await request.json()) as {
      code?: string;
      environment?: VerificationEnvironment;
    };
    requestedEnvironment = environment;

    if (!code || typeof code !== 'string') {
      return NextResponse.json(
        { error: 'Code is required' },
        { status: 400 }
      );
    }

    if (!process.env.KIMINA_SERVER_URL && process.env.NODE_ENV === 'production') {
      return NextResponse.json(
        { 
          pass: false, 
          error: 'KIMINA_SERVER_URL environment variable is not configured. Please set it in your Vercel project settings.',
        },
        { status: 200 }
      );
    }

    const response = await fetch(`${KIMINA_LEAN_SERVER_URL}/api/check`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        snippets: [
          {
            id: 'verification',
            code,
          },
        ],
        reuse: false,
        environment,
      }),
      signal: AbortSignal.timeout(30000),
    });

    if (!response.ok) {
      let errorText = await response.text();
      try {
        const errorJson = JSON.parse(errorText) as { detail?: string };
        errorText = errorJson.detail || errorText;
      } catch {
        // Ignore non-JSON error bodies from the upstream service.
      }
      return NextResponse.json(
        {
          pass: false,
          error: `Server error: ${response.status} - ${errorText}`,
          requestedEnvironment,
        } satisfies KiminaResponse,
        { status: 200 }
      );
    }

    const result = await response.json();
    const resolvedEnvironmentId = response.headers.get('x-lean-environment-id') || undefined;
    const resolvedLeanVersion = response.headers.get('x-lean-version') || undefined;
    const resolvedProjectLabel = response.headers.get('x-lean-project-label') || undefined;
    
    // Parse the kimina-lean-server response format
    // Response format: { results: [{ id, time, response: { env, messages: [{severity, pos, endPos, data}] } }] }
    if (result.results && result.results.length > 0) {
      const firstResult = result.results[0];
      const messages = firstResult.response?.messages || [];
      
      const errors: string[] = [];
      const warnings: string[] = [];
      const infos: string[] = [];
      
      for (const msg of messages) {
        const position = msg.pos ? `Line ${msg.pos.line}, Col ${msg.pos.column}: ` : '';
        const fullMessage = `${position}${msg.data}`;
        
        if (msg.severity === 'error') {
          errors.push(fullMessage);
        } else if (msg.severity === 'warning') {
          warnings.push(fullMessage);
        } else if (msg.severity === 'info') {
          infos.push(fullMessage);
        }
      }
      
      // If there are no errors, it passed
      const pass = errors.length === 0;
      
      return NextResponse.json({
        pass,
        error: errors.length > 0 ? errors.join('\n') : undefined,
        warnings,
        infos,
        time: firstResult.time,
        requestedEnvironment,
        resolvedEnvironmentId,
        resolvedLeanVersion,
        resolvedProjectLabel,
      } satisfies KiminaResponse);
    }

    return NextResponse.json({
      pass: false,
      error: 'No results returned from server',
      requestedEnvironment,
    });
  } catch (error) {
    console.error('Verification error:', error);
    
    let errorMessage = 'Unknown error occurred';
    
    if (error instanceof Error) {
      if (error.name === 'AbortError') {
        errorMessage = 'Request timeout: The Lean server took too long to respond (>30s)';
      } else if (error.message.includes('fetch failed')) {
        errorMessage = `Cannot connect to Lean server at ${KIMINA_LEAN_SERVER_URL}. Please check: 1) KIMINA_SERVER_URL is set correctly in Vercel, 2) The backend server is running and accessible, 3) Network/firewall settings allow the connection.`;
      } else {
        errorMessage = error.message;
      }
    }
    
    return NextResponse.json(
      {
        pass: false,
        error: errorMessage,
        requestedEnvironment,
      },
      { status: 200 }
    );
  }
}
