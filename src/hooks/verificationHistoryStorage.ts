import type { VerificationResult } from '../types/verification.ts';
import { isSupportedRuntimeId } from '../lib/runtime-selection.ts';

export function normalizeStoredHistory(
  items: VerificationResult[]
): VerificationResult[] {
  return items
    .map((item) => ({
      ...item,
      timestamp: new Date(item.timestamp),
    }))
    .filter((item) => isSupportedRuntimeId(item.runtimeId));
}
