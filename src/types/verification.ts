export interface VerificationResult {
  id: string;
  code: string;
  title: string;
  status: 'pending' | 'success' | 'error' | 'warning';
  errors: string[];
  warnings: string[];
  timestamp: Date;
  jobId?: string | null;
  progressMessage?: string | null;
  runtimeId: string;
  runtimeLabel: string;
  leanVersion: string;
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
  | 'failed';

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
}
