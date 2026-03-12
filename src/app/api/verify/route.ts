import { NextRequest, NextResponse } from 'next/server';
import { adaptVerificationResponse } from '@/lib/verification-adapter';

const KIMINA_LEAN_SERVER_URL = process.env.KIMINA_SERVER_URL || 'http://localhost:10000';

export async function POST(request: NextRequest) {
  try {
    const { code } = await request.json();

    if (!code || typeof code !== 'string') {
      return NextResponse.json(
        { error: 'Code is required' },
        { status: 400 }
      );
    }

    if (!process.env.KIMINA_SERVER_URL && process.env.NODE_ENV === 'production') {
      return NextResponse.json(
        {
          status: 'server_error',
          passed: false,
          error: 'KIMINA_SERVER_URL environment variable is not configured. Please set it in your Vercel project settings.',
          warnings: [],
          infos: [],
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
            code: code,
          },
        ],
        reuse: false,
      }),
      signal: AbortSignal.timeout(30000),
    });

    if (!response.ok) {
      const errorText = await response.text();
      return NextResponse.json(
        {
          status: 'server_error',
          passed: false,
          error: `Server error: ${response.status} - ${errorText}`,
          warnings: [],
          infos: [],
        },
        { status: 200 }
      );
    }

    const result = await response.json();
    return NextResponse.json(adaptVerificationResponse(result));
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
        status: 'server_error',
        passed: false,
        error: errorMessage,
        warnings: [],
        infos: [],
      },
      { status: 200 }
    );
  }
}
