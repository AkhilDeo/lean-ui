import assert from 'node:assert/strict';
import test from 'node:test';

import { resolveSelectedRuntimeId } from './runtime-selection.ts';

test('resolveSelectedRuntimeId preserves an already supported selection', () => {
  const runtimes = [
    { runtimeId: 'v4.9.0', displayName: 'Mathlib 4.9.0', leanVersion: '4.9.0', isDefault: true },
    { runtimeId: 'v4.28.0', displayName: 'Mathlib 4.28.0', leanVersion: '4.28.0', isDefault: false },
  ];

  assert.equal(resolveSelectedRuntimeId('v4.28.0', runtimes, 'v4.9.0'), 'v4.28.0');
});

test('resolveSelectedRuntimeId falls back to the backend default when no supported selection exists', () => {
  const runtimes = [
    { runtimeId: 'v4.9.0', displayName: 'Mathlib 4.9.0', leanVersion: '4.9.0', isDefault: true },
    { runtimeId: 'v4.24.0', displayName: 'Mathlib 4.24.0', leanVersion: '4.24.0', isDefault: false },
  ];

  assert.equal(resolveSelectedRuntimeId('', runtimes, 'v4.9.0'), 'v4.9.0');
  assert.equal(resolveSelectedRuntimeId('v4.28.0', runtimes, 'v4.9.0'), 'v4.9.0');
});
