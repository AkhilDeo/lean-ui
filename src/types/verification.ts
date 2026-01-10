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

export interface KiminaResponse {
  pass: boolean;
  complete_proof: string;
  error?: string;
  warnings?: string[];
}
