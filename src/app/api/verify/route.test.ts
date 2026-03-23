import assert from 'node:assert/strict';
import test from 'node:test';

import {
  handleRuntimesGet,
  handleVerifyPoll,
  handleVerifyPost,
} from '../../../lib/verify-route.ts';

function createVerifyRequest(code = '#check Nat', runtimeId = 'v4.28.0'): Request {
  return new Request('http://localhost/api/verify', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ code, runtimeId }),
  });
}

test('returns a completed job when the fast sync path succeeds', async (t) => {
  const originalFetch = global.fetch;
  const requests: Array<{ url: string; init?: RequestInit }> = [];

  t.after(() => {
    global.fetch = originalFetch;
  });

  global.fetch = (async (input, init) => {
    requests.push({ url: String(input), init });
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
    backendUrl: 'https://lean-ui-production.up.railway.app/',
    apiKey: 'test-secret',
    hasExplicitServerUrl: true,
    isProduction: true,
  });

  assert.equal(requests[0]?.url, 'https://lean-ui-production.up.railway.app/api/check');
  assert.deepEqual(requests[0]?.init?.headers, {
    'Content-Type': 'application/json',
    Authorization: 'Bearer test-secret',
  });
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

test('falls back to async submit when sync path is unavailable', async (t) => {
  const originalFetch = global.fetch;
  const requests: string[] = [];

  t.after(() => {
    global.fetch = originalFetch;
  });

  global.fetch = (async (input) => {
    const url = String(input);
    requests.push(url);
    if (url.endsWith('/api/check')) {
      return new Response('cold runtime', { status: 503 });
    }
    return new Response(
      JSON.stringify({
        job_id: 'job-123',
        status: 'queued',
      }),
      { status: 200, headers: { 'Content-Type': 'application/json' } }
    );
  }) as typeof fetch;

  const response = await handleVerifyPost(createVerifyRequest('import Mathlib', 'v4.9.0'), {
    backendUrl: 'https://lean-ui-production.up.railway.app',
    apiKey: 'test-secret',
    hasExplicitServerUrl: true,
    isProduction: true,
  });

  assert.deepEqual(requests, [
    'https://lean-ui-production.up.railway.app/api/check',
    'https://lean-ui-production.up.railway.app/api/async/check',
  ]);
  assert.deepEqual(response.body, {
    jobId: 'job-123',
    status: 'queued',
    runtimeId: 'v4.9.0',
    result: null,
    error: null,
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
        default_runtime_id: 'v4.28.0',
        runtimes: [
          {
            runtime_id: 'v4.9.0',
            display_name: 'Mathlib 4.9.0',
            lean_version: 'v4.9.0',
            is_default: false,
          },
          {
            runtime_id: 'v4.28.0',
            display_name: 'Mathlib 4.28.0',
            lean_version: 'v4.28.0',
            is_default: true,
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
    defaultRuntimeId: 'v4.28.0',
    runtimes: [
      {
        runtimeId: 'v4.9.0',
        displayName: 'Mathlib 4.9.0',
        leanVersion: '4.9.0',
        isDefault: false,
      },
      {
        runtimeId: 'v4.28.0',
        displayName: 'Mathlib 4.28.0',
        leanVersion: '4.28.0',
        isDefault: true,
      },
    ],
  });
});
