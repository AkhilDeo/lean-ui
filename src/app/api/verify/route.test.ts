import assert from 'node:assert/strict';
import test from 'node:test';

import {
  handleRuntimesGet,
  handleVerifyPoll,
  handleVerifyPost,
} from '../../../lib/verify-route.ts';

function createVerifyRequest(code = '#check Nat', runtimeId = 'v4.9.0'): Request {
  return new Request('http://localhost/api/verify', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ code, runtimeId }),
  });
}

test('submits async jobs first and includes the long-proof timeout in the payload', async (t) => {
  const originalFetch = global.fetch;
  const requests: Array<{ url: string; init?: RequestInit }> = [];

  t.after(() => {
    global.fetch = originalFetch;
  });

  global.fetch = (async (input, init) => {
    requests.push({ url: String(input), init });
    return new Response(
      JSON.stringify({
        job_id: 'job-123',
        status: 'queued',
        expires_at: '2026-03-27T17:00:00Z',
      }),
      { status: 200, headers: { 'Content-Type': 'application/json' } }
    );
  }) as typeof fetch;

  const response = await handleVerifyPost(createVerifyRequest(), {
    backendUrl: 'https://lean-ui-production.up.railway.app/',
    apiKey: 'test-secret',
    hasExplicitServerUrl: true,
    isProduction: true,
  });

  assert.equal(requests[0]?.url, 'https://lean-ui-production.up.railway.app/api/async/check');
  assert.deepEqual(requests[0]?.init?.headers, {
    'Content-Type': 'application/json',
    Authorization: 'Bearer test-secret',
  });
  assert.deepEqual(JSON.parse(String(requests[0]?.init?.body)), {
    snippets: [
      {
        id: 'verification',
        code: '#check Nat',
      },
    ],
    timeout: 300,
    runtime_id: 'v4.9.0',
    reuse: false,
  });
  assert.deepEqual(response.body, {
    jobId: 'job-123',
    status: 'queued',
    runtimeId: 'v4.9.0',
    result: null,
    error: null,
    expiresAt: '2026-03-27T17:00:00Z',
  });
});

test('falls back to sync verification when async submit is disabled and sync succeeds', async (t) => {
  const originalFetch = global.fetch;
  const requests: string[] = [];

  t.after(() => {
    global.fetch = originalFetch;
  });

  global.fetch = (async (input) => {
    const url = String(input);
    requests.push(url);

    if (url.endsWith('/api/async/check')) {
      return new Response(
        JSON.stringify({
          detail: 'Async queue API is not enabled on this service',
        }),
        { status: 503, headers: { 'Content-Type': 'application/json' } }
      );
    }

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
      { status: 200, headers: { 'Content-Type': 'application/json' } }
    );
  }) as typeof fetch;

  const response = await handleVerifyPost(createVerifyRequest('import Mathlib', 'v4.28.0'), {
    backendUrl: 'https://lean-ui-production.up.railway.app',
    apiKey: 'test-secret',
    hasExplicitServerUrl: true,
    isProduction: true,
  });

  assert.deepEqual(requests, [
    'https://lean-ui-production.up.railway.app/api/async/check',
    'https://lean-ui-production.up.railway.app/api/check',
  ]);
  assert.deepEqual(response.body, {
    jobId: null,
    status: 'completed',
    runtimeId: 'v4.28.0',
    result: {
      error: null,
      infos: [],
      passed: true,
      status: 'valid',
      time: 0,
      warnings: [],
    },
  });
});

test('retries sync warmup when async submit times out and the fallback sync path becomes ready', async (t) => {
  const originalFetch = global.fetch;
  const requests: string[] = [];
  let syncAttempts = 0;

  t.after(() => {
    global.fetch = originalFetch;
  });

  global.fetch = (async (input) => {
    const url = String(input);
    requests.push(url);

    if (url.endsWith('/api/async/check')) {
      throw new DOMException('The operation was aborted.', 'AbortError');
    }

    syncAttempts += 1;
    if (syncAttempts < 3) {
      return new Response(
        JSON.stringify({
          detail:
            'Runtime v4.9.0 is cold and is starting up. Retry asynchronously via /api/async/check.',
        }),
        { status: 503, headers: { 'Content-Type': 'application/json' } }
      );
    }

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
      { status: 200, headers: { 'Content-Type': 'application/json' } }
    );
  }) as typeof fetch;

  const response = await handleVerifyPost(createVerifyRequest(), {
    backendUrl: 'https://lean-ui-production.up.railway.app',
    apiKey: 'test-secret',
    hasExplicitServerUrl: true,
    isProduction: true,
    asyncSubmitTimeoutMs: 1,
    warmupRetryDelayMs: 0,
    warmupRetryMaxAttempts: 3,
  });

  assert.deepEqual(requests, [
    'https://lean-ui-production.up.railway.app/api/async/check',
    'https://lean-ui-production.up.railway.app/api/check',
    'https://lean-ui-production.up.railway.app/api/check',
    'https://lean-ui-production.up.railway.app/api/check',
  ]);
  assert.deepEqual(response.body, {
    jobId: null,
    status: 'completed',
    runtimeId: 'v4.9.0',
    result: {
      error: null,
      infos: [],
      passed: true,
      status: 'valid',
      time: 0,
      warnings: [],
    },
  });
});

test('returns a friendly warmup error when async submit is disabled and sync never becomes ready', async (t) => {
  const originalFetch = global.fetch;
  const requests: string[] = [];

  t.after(() => {
    global.fetch = originalFetch;
  });

  global.fetch = (async (input) => {
    const url = String(input);
    requests.push(url);

    if (url.endsWith('/api/async/check')) {
      return new Response(
        JSON.stringify({
          detail: 'Async queue API is not enabled on this service',
        }),
        { status: 503, headers: { 'Content-Type': 'application/json' } }
      );
    }

    return new Response(
      JSON.stringify({
        detail:
          'Runtime v4.9.0 is cold and is starting up. Retry asynchronously via /api/async/check.',
      }),
      { status: 503, headers: { 'Content-Type': 'application/json' } }
    );
  }) as typeof fetch;

  const response = await handleVerifyPost(createVerifyRequest(), {
    backendUrl: 'https://lean-ui-production.up.railway.app',
    apiKey: 'test-secret',
    hasExplicitServerUrl: true,
    isProduction: true,
    warmupRetryDelayMs: 0,
    warmupRetryMaxAttempts: 2,
  });

  assert.deepEqual(requests, [
    'https://lean-ui-production.up.railway.app/api/async/check',
    'https://lean-ui-production.up.railway.app/api/check',
    'https://lean-ui-production.up.railway.app/api/check',
    'https://lean-ui-production.up.railway.app/api/check',
  ]);
  assert.deepEqual(response.body, {
    jobId: null,
    status: 'failed',
    error: 'Verification server is waking the selected runtime. Please try again in a few seconds.',
    result: {
      status: 'server_error',
      passed: false,
      error: 'Verification server is waking the selected runtime. Please try again in a few seconds.',
      warnings: [],
      infos: [],
      time: 0,
    },
  });
});

test('poll adapts completed async jobs', async (t) => {
  const originalFetch = global.fetch;

  t.after(() => {
    global.fetch = originalFetch;
  });

  global.fetch = (async () =>
    new Response(
      JSON.stringify({
        job_id: 'job-123',
        status: 'completed',
        expires_at: '2026-03-27T17:00:00Z',
        results: [
          {
            id: 'verification',
            status: 'valid',
            passed: true,
            response: {},
          },
        ],
      }),
      { status: 200, headers: { 'Content-Type': 'application/json' } }
    )) as typeof fetch;

  const response = await handleVerifyPoll('job-123', {
    backendUrl: 'https://lean-ui-production.up.railway.app',
    apiKey: 'test-secret',
    hasExplicitServerUrl: true,
    isProduction: true,
  });

  assert.deepEqual(response.body, {
    jobId: 'job-123',
    status: 'completed',
    result: {
      error: null,
      infos: [],
      passed: true,
      status: 'valid',
      time: 0,
      warnings: [],
    },
    error: null,
    expiresAt: '2026-03-27T17:00:00Z',
  });
});

test('poll returns an expired job state when the backend no longer has the job', async (t) => {
  const originalFetch = global.fetch;

  t.after(() => {
    global.fetch = originalFetch;
  });

  global.fetch = (async () =>
    new Response(
      JSON.stringify({
        detail: 'Async job not found or expired',
      }),
      { status: 404, headers: { 'Content-Type': 'application/json' } }
    )) as typeof fetch;

  const response = await handleVerifyPoll('job-expired', {
    backendUrl: 'https://lean-ui-production.up.railway.app',
    apiKey: 'test-secret',
    hasExplicitServerUrl: true,
    isProduction: true,
  });

  assert.deepEqual(response.body, {
    jobId: 'job-expired',
    status: 'expired',
    error: 'Verification job expired or is no longer available. Please resubmit your proof.',
    result: null,
    expiresAt: null,
  });
});

test('returns the runtime registry for the frontend picker', async (t) => {
  const originalFetch = global.fetch;

  t.after(() => {
    global.fetch = originalFetch;
  });

  global.fetch = (async () =>
    new Response(
      JSON.stringify({
        default_runtime_id: 'v4.9.0',
        runtimes: [
          {
            runtime_id: 'v4.9.0',
            display_name: 'Mathlib 4.9.0',
            lean_version: 'v4.9.0',
            is_default: true,
          },
          {
            runtime_id: 'v4.15.0',
            display_name: 'Mathlib 4.15.0',
            lean_version: 'v4.15.0',
            is_default: false,
          },
          {
            runtime_id: 'v4.24.0',
            display_name: 'Mathlib 4.24.0',
            lean_version: 'v4.24.0',
            is_default: false,
          },
          {
            runtime_id: 'v4.27.0',
            display_name: 'Mathlib 4.27.0',
            lean_version: 'v4.27.0',
            is_default: false,
          },
          {
            runtime_id: 'v4.28.0',
            display_name: 'Mathlib 4.28.0',
            lean_version: 'v4.28.0',
            is_default: false,
          },
        ],
      }),
      { status: 200, headers: { 'Content-Type': 'application/json' } }
    )) as typeof fetch;

  const response = await handleRuntimesGet({
    backendUrl: 'https://lean-ui-production.up.railway.app',
    apiKey: 'test-secret',
    hasExplicitServerUrl: true,
    isProduction: true,
  });

  assert.deepEqual(response.body, {
    defaultRuntimeId: 'v4.9.0',
    runtimes: [
      {
        runtimeId: 'v4.9.0',
        displayName: 'Mathlib 4.9.0',
        leanVersion: '4.9.0',
        isDefault: true,
      },
      {
        runtimeId: 'v4.15.0',
        displayName: 'Mathlib 4.15.0',
        leanVersion: '4.15.0',
        isDefault: false,
      },
      {
        runtimeId: 'v4.24.0',
        displayName: 'Mathlib 4.24.0',
        leanVersion: '4.24.0',
        isDefault: false,
      },
      {
        runtimeId: 'v4.27.0',
        displayName: 'Mathlib 4.27.0',
        leanVersion: '4.27.0',
        isDefault: false,
      },
      {
        runtimeId: 'v4.28.0',
        displayName: 'Mathlib 4.28.0',
        leanVersion: '4.28.0',
        isDefault: false,
      },
    ],
  });
});

test('returns an explicit upstream error for actually unknown runtimes', async (t) => {
  const originalFetch = global.fetch;

  t.after(() => {
    global.fetch = originalFetch;
  });

  global.fetch = (async () =>
    new Response(
      JSON.stringify({
        detail: 'Unknown runtime_id: v4.99.0',
      }),
      { status: 400, headers: { 'Content-Type': 'application/json' } }
    )) as typeof fetch;

  const response = await handleVerifyPost(createVerifyRequest('import Mathlib', 'v4.99.0'), {
    backendUrl: 'https://lean-ui-production.up.railway.app',
    apiKey: 'test-secret',
    hasExplicitServerUrl: true,
    isProduction: true,
  });

  assert.deepEqual(response.body, {
    jobId: null,
    status: 'failed',
    error: 'Async submit failed: 400 - Unknown runtime_id: v4.99.0',
    result: {
      status: 'server_error',
      passed: false,
      error: 'Async submit failed: 400 - Unknown runtime_id: v4.99.0',
      warnings: [],
      infos: [],
      time: 0,
    },
  });
});
