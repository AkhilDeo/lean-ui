export interface VerificationResult {
  id: string;
  code: string;
  title: string;
  status: 'pending' | 'success' | 'error' | 'warning';
  errors: string[];
  warnings: string[];
  timestamp: Date;
  leanVersion: string;
}

export type SnippetOutcomeStatus =
  | 'valid'
  | 'sorry'
  | 'lean_error'
  | 'repl_error'
  | 'timeout_error'
  | 'server_error';

export interface VerifyApiResponse {
  status: SnippetOutcomeStatus;
  passed: boolean;
  error: string | null;
  warnings?: string[];
  infos?: string[];
  time?: number;
}
