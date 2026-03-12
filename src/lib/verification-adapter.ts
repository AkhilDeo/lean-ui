type SnippetOutcomeStatus =
  | 'valid'
  | 'sorry'
  | 'lean_error'
  | 'repl_error'
  | 'timeout_error'
  | 'server_error';

interface VerifyApiResponse {
  status: SnippetOutcomeStatus;
  passed: boolean;
  error: string | null;
  warnings?: string[];
  infos?: string[];
  time?: number;
}

type MessageSeverity = 'trace' | 'info' | 'warning' | 'error';

interface VerifyMessage {
  severity?: MessageSeverity;
  data?: string;
  pos?: {
    line: number;
    column: number;
  };
}

interface VerifyCommandResponse {
  messages?: VerifyMessage[] | null;
}

interface VerifyReplError {
  message?: string;
}

interface VerifySnippetResult {
  id: string;
  time?: number;
  status?: SnippetOutcomeStatus;
  passed?: boolean;
  error?: string | null;
  response?: VerifyCommandResponse | VerifyReplError | null;
}

interface VerifyCheckResponse {
  results?: VerifySnippetResult[];
}

function isReplErrorResponse(
  response: VerifySnippetResult['response']
): response is VerifyReplError {
  return Boolean(response && typeof response === 'object' && 'message' in response);
}

function formatMessage(message: VerifyMessage): string {
  const position = message.pos
    ? `Line ${message.pos.line}, Col ${message.pos.column}: `
    : '';
  return `${position}${message.data ?? ''}`;
}

export function adaptVerificationResponse(
  payload: VerifyCheckResponse
): VerifyApiResponse {
  const firstResult = payload.results?.[0];

  if (!firstResult) {
    return {
      status: 'server_error',
      passed: false,
      error: 'No results returned from server',
      warnings: [],
      infos: [],
      time: 0,
    };
  }

  const status = firstResult.status ?? 'server_error';
  const passed = firstResult.passed ?? status === 'valid';
  const messages =
    firstResult.response && !isReplErrorResponse(firstResult.response)
      ? firstResult.response.messages ?? []
      : [];

  const errors: string[] = [];
  const warnings: string[] = [];
  const infos: string[] = [];

  if (firstResult.error) {
    errors.push(firstResult.error);
  }

  if (isReplErrorResponse(firstResult.response) && firstResult.response.message) {
    errors.push(firstResult.response.message);
  }

  for (const message of messages) {
    const formatted = formatMessage(message);
    if (message.severity === 'error') {
      errors.push(formatted);
    } else if (message.severity === 'warning') {
      warnings.push(formatted);
    } else if (message.severity === 'info') {
      infos.push(formatted);
    }
  }

  if (!passed && errors.length === 0) {
    if (status === 'sorry') {
      if (warnings.length === 0) {
        warnings.push("Verification contains 'sorry' placeholders");
      }
    } else {
      errors.push('Verification failed');
    }
  }

  return {
    status,
    passed,
    error: errors[0] ?? null,
    warnings,
    infos,
    time: firstResult.time ?? 0,
  };
}
