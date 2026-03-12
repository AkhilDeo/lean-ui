export type VerificationEnvironment =
  | 'auto'
  | 'mathlib-v4.15'
  | 'mathlib-v4.27'
  | 'formal-conjectures-v4.27';

export interface EnvironmentInfo {
  id: Exclude<VerificationEnvironment, 'auto'>;
  display_name: string;
  lean_version: string;
  project_label: string;
  project_type: string;
  selectable: boolean;
  auto_routable: boolean;
  is_default: boolean;
}

export interface EnvironmentsResponse {
  default_environment: Exclude<VerificationEnvironment, 'auto'>;
  environments: EnvironmentInfo[];
}

export interface VerificationResult {
  id: string;
  code: string;
  title: string;
  status: 'pending' | 'success' | 'error' | 'warning';
  errors: string[];
  warnings: string[];
  timestamp: Date;
  leanVersion: string;
  requestedEnvironment: VerificationEnvironment;
  resolvedEnvironmentId: string;
  resolvedProjectLabel: string;
}

export interface KiminaResponse {
  pass: boolean;
  error?: string;
  warnings?: string[];
  infos?: string[];
  time?: number;
  requestedEnvironment?: VerificationEnvironment;
  resolvedEnvironmentId?: string;
  resolvedLeanVersion?: string;
  resolvedProjectLabel?: string;
}
