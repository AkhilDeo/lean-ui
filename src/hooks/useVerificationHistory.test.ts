import assert from 'node:assert/strict';
import test from 'node:test';

import { normalizeStoredHistory } from './verificationHistoryStorage.ts';

test('normalizeStoredHistory preserves the supported runtime set and drops unknown entries', () => {
  const normalized = normalizeStoredHistory([
    {
      id: 'supported-runtime-oldest',
      code: '#check Nat',
      title: 'old',
      status: 'success',
      errors: [],
      warnings: [],
      timestamp: new Date('2026-03-27T16:00:00Z') as unknown as Date,
      runtimeId: 'v4.9.0',
      runtimeLabel: 'Mathlib 4.9.0',
      leanVersion: '4.9.0',
    },
    {
      id: 'supported-runtime-default',
      code: '#check Int',
      title: 'new',
      status: 'success',
      errors: [],
      warnings: [],
      timestamp: new Date('2026-03-27T17:00:00Z') as unknown as Date,
      runtimeId: 'v4.15.0',
      runtimeLabel: 'Mathlib 4.15.0',
      leanVersion: '4.15.0',
    },
    {
      id: 'supported-runtime-424',
      code: '#check Rat',
      title: 'mid',
      status: 'success',
      errors: [],
      warnings: [],
      timestamp: new Date('2026-03-27T18:00:00Z') as unknown as Date,
      runtimeId: 'v4.24.0',
      runtimeLabel: 'Mathlib 4.24.0',
      leanVersion: '4.24.0',
    },
    {
      id: 'supported-runtime-427',
      code: '#check Real',
      title: 'newer',
      status: 'success',
      errors: [],
      warnings: [],
      timestamp: new Date('2026-03-27T19:00:00Z') as unknown as Date,
      runtimeId: 'v4.27.0',
      runtimeLabel: 'Mathlib 4.27.0',
      leanVersion: '4.27.0',
    },
    {
      id: 'supported-runtime-428',
      code: '#check Complex',
      title: 'latest',
      status: 'success',
      errors: [],
      warnings: [],
      timestamp: new Date('2026-03-27T20:00:00Z') as unknown as Date,
      runtimeId: 'v4.28.0',
      runtimeLabel: 'Mathlib 4.28.0',
      leanVersion: '4.28.0',
    },
    {
      id: 'unsupported-runtime',
      code: '#check Bool',
      title: 'unknown',
      status: 'success',
      errors: [],
      warnings: [],
      timestamp: new Date('2026-03-27T21:00:00Z') as unknown as Date,
      runtimeId: 'v4.99.0',
      runtimeLabel: 'Mathlib 4.99.0',
      leanVersion: '4.99.0',
    },
  ]);

  assert.deepEqual(
    normalized.map((item) => item.id),
    [
      'supported-runtime-oldest',
      'supported-runtime-default',
      'supported-runtime-424',
      'supported-runtime-427',
      'supported-runtime-428',
    ]
  );
  assert.ok(normalized[0]?.timestamp instanceof Date);
});
