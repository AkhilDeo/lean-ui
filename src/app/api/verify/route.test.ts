import assert from 'node:assert/strict';
import test from 'node:test';

import { handleVerifyPost } from '../../../lib/verify-route.ts';

function createVerifyRequest(code = '#check Nat'): Request {
  return new Request('http://localhost/api/verify', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ code }),
  });
}

test('forwards Authorization header to the backend when configured', async (t) => {
  const originalFetch = global.fetch;
  let receivedUrl = '';
  let receivedInit: RequestInit | undefined;

  t.after(() => {
    global.fetch = originalFetch;
  });

  global.fetch = (async (input, init) => {
    receivedUrl = String(input);
    receivedInit = init;

    return new Response(
      JSON.stringify({
        results: [
          {
            id: 'verification',
            status: 'valid',
            passed: true,
            response: {},
          },
        ],
      }),
      {
        status: 200,
        headers: {
          'Content-Type': 'application/json',
        },
      }
    );
  }) as typeof fetch;

  const response = await handleVerifyPost(createVerifyRequest(), {
    backendUrl: 'https://lean-ui-production.up.railway.app/',
    apiKey: 'test-secret',
    hasExplicitServerUrl: true,
    isProduction: true,
  });

  assert.equal(receivedUrl, 'https://lean-ui-production.up.railway.app/api/check');
  assert.deepEqual(receivedInit?.headers, {
    'Content-Type': 'application/json',
    Authorization: 'Bearer test-secret',
  });
  assert.equal(response.status, 200);
  assert.deepEqual(response.body, {
    error: null,
    infos: [],
    passed: true,
    status: 'valid',
    time: 0,
    warnings: [],
  });
});

test('returns a clear production config error when backend auth is not configured on Vercel', async (t) => {
  const originalFetch = global.fetch;
  let fetchCalled = false;

  t.after(() => {
    global.fetch = originalFetch;
  });

  global.fetch = (async () => {
    fetchCalled = true;
    throw new Error('fetch should not be called');
  }) as typeof fetch;

  const response = await handleVerifyPost(createVerifyRequest(), {
    backendUrl: 'https://lean-ui-production.up.railway.app',
    apiKey: '',
    hasExplicitServerUrl: true,
    isProduction: true,
  });

  assert.equal(fetchCalled, false);
  assert.equal(response.status, 200);
  assert.equal(
    (response.body as { status: string }).status,
    'server_error'
  );
  assert.match(
    (response.body as { error: string }).error,
    /KIMINA_SERVER_API_KEY environment variable is not configured/
  );
});
