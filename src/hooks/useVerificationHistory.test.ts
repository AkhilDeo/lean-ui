import assert from 'node:assert/strict';
import test from 'node:test';

import { normalizeStoredHistory } from './verificationHistoryStorage.ts';

test('normalizeStoredHistory drops unsupported v4.9.0 entries and preserves v4.15.0', () => {
  const normalized = normalizeStoredHistory([
    {
      id: 'old-runtime',
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
      id: 'supported-runtime',
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
  ]);

  assert.equal(normalized.length, 1);
  assert.equal(normalized[0]?.id, 'supported-runtime');
  assert.ok(normalized[0]?.timestamp instanceof Date);
});
