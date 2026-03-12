import assert from 'node:assert/strict';
import test from 'node:test';

import { adaptVerificationResponse } from './verification-adapter.ts';

test('uses explicit valid outcome for empty successful responses', () => {
  const result = adaptVerificationResponse({
    results: [
      {
        id: 'snippet-1',
        status: 'valid',
        passed: true,
        response: {},
      },
    ],
  });

  assert.equal(result.status, 'valid');
  assert.equal(result.passed, true);
  assert.equal(result.error, null);
  assert.deepEqual(result.warnings, []);
});

test('treats sorry as non-passing even when Lean only emitted warnings', () => {
  const result = adaptVerificationResponse({
    results: [
      {
        id: 'snippet-2',
        status: 'sorry',
        passed: false,
        response: {
          messages: [
            {
              severity: 'warning',
              pos: { line: 4, column: 2 },
              data: "declaration uses 'sorry'",
            },
          ],
        },
      },
    ],
  });

  assert.equal(result.status, 'sorry');
  assert.equal(result.passed, false);
  assert.equal(result.error, null);
  assert.deepEqual(result.warnings, ["Line 4, Col 2: declaration uses 'sorry'"]);
});

test('uses explicit non-passing status instead of inferring success from empty messages', () => {
  const result = adaptVerificationResponse({
    results: [
      {
        id: 'snippet-3',
        status: 'server_error',
        passed: false,
        error: 'worker_error: No available REPLs',
        response: null,
      },
    ],
  });

  assert.equal(result.status, 'server_error');
  assert.equal(result.passed, false);
  assert.equal(result.error, 'worker_error: No available REPLs');
});
