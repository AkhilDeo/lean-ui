import type { RuntimeOption } from '../types/verification.ts';

export const SUPPORTED_RUNTIME_IDS = [
  'v4.9.0',
  'v4.15.0',
  'v4.24.0',
  'v4.27.0',
  'v4.28.0',
] as const;

export const DEFAULT_RUNTIME_ID = 'v4.9.0';

export function isSupportedRuntimeId(runtimeId: string | null | undefined): boolean {
  return runtimeId != null && SUPPORTED_RUNTIME_IDS.includes(runtimeId as (typeof SUPPORTED_RUNTIME_IDS)[number]);
}

export function resolveSelectedRuntimeId(
  currentRuntimeId: string | null | undefined,
  runtimes: RuntimeOption[],
  defaultRuntimeId: string
): string {
  if (currentRuntimeId && runtimes.some((runtime) => runtime.runtimeId === currentRuntimeId)) {
    return currentRuntimeId;
  }

  if (runtimes.some((runtime) => runtime.runtimeId === defaultRuntimeId)) {
    return defaultRuntimeId;
  }

  return runtimes[0]?.runtimeId ?? '';
}
