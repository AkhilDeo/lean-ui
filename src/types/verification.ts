export interface VerificationResult {
  id: string;
  code: string;
  title: string;
  status: 'pending' | 'success' | 'error' | 'warning';
  errors: string[];
  warnings: string[];
  timestamp: Date;
  jobId?: string | null;
  jobStatus?: VerificationJobStatus | null;
  progressMessage?: string | null;
  jobExpiresAt?: string | null;
  submitLatencyMs?: number | null;
  jobTiming?: VerificationJobTiming | null;
  runtimeId: string;
  runtimeLabel: string;
  leanVersion: string;
}

export interface VerificationJobTiming {
  queuedAt?: string | null;
  startedAt?: string | null;
  finishedAt?: string | null;
  queueWaitMs?: number | null;
  runMs?: number | null;
  totalMs?: number | null;
}

export interface RuntimeOption {
  runtimeId: string;
  displayName: string;
  leanVersion: string;
  isDefault: boolean;
}

export type SnippetOutcomeStatus =
  | 'valid'
  | 'sorry'
  | 'lean_error'
  | 'repl_error'
  | 'timeout_error'
  | 'server_error';

export type VerificationJobStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'failed'
  | 'expired';

export interface VerifyApiResponse {
  status: SnippetOutcomeStatus;
  passed: boolean;
  error: string | null;
  warnings?: string[];
  infos?: string[];
  time?: number;
}

export interface VerifyJobResponse {
  jobId: string | null;
  status: VerificationJobStatus;
  runtimeId?: string | null;
  result?: VerifyApiResponse | null;
  error?: string | null;
  expiresAt?: string | null;
  timing?: VerificationJobTiming | null;
}
