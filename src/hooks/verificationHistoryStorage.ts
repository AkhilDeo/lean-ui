import type { VerificationResult } from '../types/verification.ts';

const SUPPORTED_RUNTIME_IDS = new Set(['v4.15.0']);

export function normalizeStoredHistory(
  items: VerificationResult[]
): VerificationResult[] {
  return items
    .map((item) => ({
      ...item,
      timestamp: new Date(item.timestamp),
    }))
    .filter((item) => SUPPORTED_RUNTIME_IDS.has(item.runtimeId));
}
