'use client';

import { useState, useCallback, useEffect, useRef } from 'react';
import { v4 as uuidv4 } from 'uuid';
import { CodeEditor } from './CodeEditor';
import { VerificationPanel } from './VerificationPanel';
import { HistorySidebar } from './HistorySidebar';
import { useVerificationHistory } from '@/hooks/useVerificationHistory';
import {
  RuntimeOption,
  VerificationResult,
  VerifyApiResponse,
  VerifyJobResponse,
} from '@/types/verification';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Play, Loader2, Code2, Sparkles, ChevronDown } from 'lucide-react';
import { generateRandomName } from '@/lib/nameGenerator';

const DEFAULT_CODE = `-- Welcome to Lean Verifier!
-- Supported runtimes: Lean 4.15.0 and Lean 4.9.0.
-- Pick a Lean runtime, write your code, and verify it.

theorem hello_world : 1 + 1 = 2 := by
  rfl
`;

const POLL_RETRY_LIMIT = 3;
const POLL_RETRY_DELAY_MS = 1500;
const POLL_INTERVAL_MS = 1200;

interface RuntimeApiResponse {
  defaultRuntimeId: string;
  runtimes: RuntimeOption[];
}

function toUiStatus(result: VerifyApiResponse): VerificationResult['status'] {
  if (result.status === 'sorry') {
    return 'warning';
  }
  if (!result.passed || result.error) {
    return 'error';
  }
  if (result.warnings && result.warnings.length > 0) {
    return 'warning';
  }
  return 'success';
}

function buildProgressMessage(status: VerifyJobResponse['status'], runtimeLabel: string): string {
  switch (status) {
    case 'queued':
      return `Queued on ${runtimeLabel}. Waiting for worker and runtime capacity.`;
    case 'running':
      return `Running on ${runtimeLabel}. Complex proofs can take several minutes.`;
    case 'failed':
      return `Verification failed on ${runtimeLabel}.`;
    case 'expired':
      return `Verification job expired on ${runtimeLabel}.`;
    default:
      return `Submitting to ${runtimeLabel}...`;
  }
}

function isPendingJobStatus(
  status: VerifyJobResponse['status'] | VerificationResult['jobStatus'] | null | undefined
): status is 'queued' | 'running' {
  return status === 'queued' || status === 'running';
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function LeanVerifier() {
  const [code, setCode] = useState(DEFAULT_CODE);
  const [title, setTitle] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showClearDialog, setShowClearDialog] = useState(false);
  const [rightSidebarWidth, setRightSidebarWidth] = useState(400);
  const [isResizing, setIsResizing] = useState(false);
  const [runtimes, setRuntimes] = useState<RuntimeOption[]>([]);
  const [selectedRuntimeId, setSelectedRuntimeId] = useState('v4.15.0');
  const [draftMode, setDraftMode] = useState(false);
  const resizeRef = useRef<HTMLDivElement>(null);
  const activePollsRef = useRef<Set<string>>(new Set());

  const {
    history,
    isLoaded,
    addVerification,
    updateVerification,
    deleteVerification,
    clearHistory,
    getVerification,
  } = useVerificationHistory();

  const currentResult =
    selectedId === null ? null : history.find((item) => item.id === selectedId) ?? null;

  const selectedRuntime =
    runtimes.find((runtime) => runtime.runtimeId === selectedRuntimeId) ??
    runtimes.find((runtime) => runtime.isDefault) ??
    null;

  const stopPollingJob = useCallback((jobId: string | null | undefined) => {
    if (!jobId) {
      return;
    }
    activePollsRef.current.delete(jobId);
  }, []);

  const stopAllPolling = useCallback(() => {
    activePollsRef.current.clear();
  }, []);

  const applyVerificationOutcome = useCallback(
    (id: string, runtime: RuntimeOption, result: VerifyApiResponse) => {
      const updatedResult: Partial<VerificationResult> = {
        status: toUiStatus(result),
        errors: result.error ? [result.error] : [],
        warnings: Array.isArray(result.warnings) ? result.warnings : [],
        progressMessage: null,
        jobId: null,
        jobStatus: 'completed',
        jobExpiresAt: null,
        runtimeId: runtime.runtimeId,
        runtimeLabel: runtime.displayName,
        leanVersion: runtime.leanVersion,
      };
      updateVerification(id, updatedResult);
    },
    [updateVerification]
  );

  const markVerificationPending = useCallback(
    (
      id: string,
      runtime: RuntimeOption,
      status: 'queued' | 'running',
      jobId: string,
      expiresAt: string | null,
      progressMessage?: string
    ) => {
      updateVerification(id, {
        status: 'pending',
        errors: [],
        warnings: [],
        progressMessage: progressMessage ?? buildProgressMessage(status, runtime.displayName),
        jobId,
        jobStatus: status,
        jobExpiresAt: expiresAt,
        runtimeId: runtime.runtimeId,
        runtimeLabel: runtime.displayName,
        leanVersion: runtime.leanVersion,
      });
    },
    [updateVerification]
  );

  const markVerificationTerminal = useCallback(
    (
      id: string,
      runtime: RuntimeOption,
      jobStatus: 'failed' | 'expired',
      error: string,
      expiresAt: string | null
    ) => {
      updateVerification(id, {
        status: 'error',
        errors: [error],
        warnings: [],
        progressMessage: null,
        jobStatus,
        jobExpiresAt: expiresAt,
        runtimeId: runtime.runtimeId,
        runtimeLabel: runtime.displayName,
        leanVersion: runtime.leanVersion,
      });
    },
    [updateVerification]
  );

  const pollVerificationJob = useCallback(
    (id: string, jobId: string, runtime: RuntimeOption) => {
      if (activePollsRef.current.has(jobId)) {
        return;
      }

      activePollsRef.current.add(jobId);

      void (async () => {
        let consecutiveFailures = 0;

        try {
          while (activePollsRef.current.has(jobId)) {
            try {
              const response = await fetch(`/api/verify/${jobId}`);
              const poll = (await response.json()) as VerifyJobResponse;

              if (!activePollsRef.current.has(jobId)) {
                return;
              }

              if (poll.status === 'completed' && poll.result) {
                applyVerificationOutcome(id, runtime, poll.result);
                return;
              }

              if (poll.status === 'failed' || poll.status === 'expired') {
                markVerificationTerminal(
                  id,
                  runtime,
                  poll.status,
                  poll.error || 'Verification job failed.',
                  poll.expiresAt ?? null
                );
                return;
              }

              if (!isPendingJobStatus(poll.status)) {
                markVerificationTerminal(
                  id,
                  runtime,
                  'failed',
                  poll.error || 'Verification job completed without a result payload.',
                  poll.expiresAt ?? null
                );
                return;
              }

              markVerificationPending(
                id,
                runtime,
                poll.status,
                jobId,
                poll.expiresAt ?? null
              );
              consecutiveFailures = 0;
              await sleep(POLL_INTERVAL_MS);
            } catch (error) {
              consecutiveFailures += 1;
              const message = error instanceof Error ? error.message : 'Unknown polling error';

              if (consecutiveFailures >= POLL_RETRY_LIMIT) {
                markVerificationTerminal(
                  id,
                  runtime,
                  'failed',
                  `Failed while polling verification job: ${message}`,
                  null
                );
                return;
              }

              markVerificationPending(
                id,
                runtime,
                'running',
                jobId,
                null,
                `Connection hiccup while checking ${runtime.displayName}. Retrying...`
              );
              await sleep(POLL_RETRY_DELAY_MS * consecutiveFailures);
            }
          }
        } finally {
          activePollsRef.current.delete(jobId);
        }
      })();
    },
    [applyVerificationOutcome, markVerificationPending, markVerificationTerminal]
  );

  const handleVerify = useCallback(async () => {
    if (!code.trim() || !selectedRuntime) {
      return;
    }

    setIsSubmitting(true);
    const id = uuidv4();
    const verificationTitle = title.trim() || generateRandomName();

    const newVerification: VerificationResult = {
      id,
      code,
      title: verificationTitle,
      status: 'pending',
      errors: [],
      warnings: [],
      timestamp: new Date(),
      progressMessage: `Submitting to ${selectedRuntime.displayName}...`,
      jobId: null,
      jobStatus: 'queued',
      jobExpiresAt: null,
      runtimeId: selectedRuntime.runtimeId,
      runtimeLabel: selectedRuntime.displayName,
      leanVersion: selectedRuntime.leanVersion,
    };

    addVerification(newVerification);
    setSelectedId(id);
    setDraftMode(false);
    setTitle('');

    try {
      const response = await fetch('/api/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code, runtimeId: selectedRuntime.runtimeId }),
      });

      const result = (await response.json()) as VerifyJobResponse;

      if (result.status === 'completed' && result.result) {
        applyVerificationOutcome(id, selectedRuntime, result.result);
        return;
      }

      if (result.status === 'failed' || result.status === 'expired') {
        markVerificationTerminal(
          id,
          selectedRuntime,
          result.status,
          result.error || 'Verification submission failed.',
          result.expiresAt ?? null
        );
        return;
      }

      if (!result.jobId || !isPendingJobStatus(result.status)) {
        markVerificationTerminal(
          id,
          selectedRuntime,
          'failed',
          result.error || 'Verification submission failed.',
          result.expiresAt ?? null
        );
        return;
      }

      markVerificationPending(
        id,
        selectedRuntime,
        result.status,
        result.jobId,
        result.expiresAt ?? null
      );
      pollVerificationJob(id, result.jobId, selectedRuntime);
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Unknown error';
      markVerificationTerminal(
        id,
        selectedRuntime,
        'failed',
        `Failed to connect to verification server: ${errorMessage}`,
        null
      );
    } finally {
      setIsSubmitting(false);
    }
  }, [
    addVerification,
    applyVerificationOutcome,
    code,
    markVerificationPending,
    markVerificationTerminal,
    pollVerificationJob,
    selectedRuntime,
    title,
  ]);

  const handleSelectHistory = useCallback(
    (id: string) => {
      const verification = getVerification(id);
      if (verification) {
        setSelectedId(id);
        setDraftMode(false);
        setCode(verification.code);
        setTitle(verification.title);
        setSelectedRuntimeId(verification.runtimeId);
      }
    },
    [getVerification]
  );

  const handleDeleteHistory = useCallback(
    (id: string) => {
      const verification = getVerification(id);
      stopPollingJob(verification?.jobId);
      deleteVerification(id);
      if (selectedId === id) {
        setSelectedId(null);
        setDraftMode(true);
        setCode(DEFAULT_CODE);
        setTitle('');
      }
    },
    [deleteVerification, getVerification, selectedId, stopPollingJob]
  );

  const handleClearHistory = useCallback(() => {
    setShowClearDialog(true);
  }, []);

  const confirmClearHistory = useCallback(() => {
    stopAllPolling();
    clearHistory();
    setSelectedId(null);
    setDraftMode(true);
    setCode(DEFAULT_CODE);
    setTitle('');
    setShowClearDialog(false);
  }, [clearHistory, stopAllPolling]);

  const handleNew = useCallback(() => {
    setSelectedId(null);
    setDraftMode(true);
    setCode(DEFAULT_CODE);
    setTitle('');
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function loadRuntimes() {
      try {
        const response = await fetch('/api/runtimes');
        if (!response.ok) {
          return;
        }
        const payload = (await response.json()) as RuntimeApiResponse;
        if (cancelled) {
          return;
        }
        setRuntimes(payload.runtimes);
        setSelectedRuntimeId(payload.defaultRuntimeId);
      } catch (error) {
        console.error('Failed to load runtimes:', error);
      }
    }

    void loadRuntimes();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (isLoaded && history.length > 0 && !selectedId && !draftMode) {
      const latest = history[0];
      setSelectedId(latest.id);
      setCode(latest.code);
      setTitle(latest.title);
      setSelectedRuntimeId(latest.runtimeId);
    }
  }, [draftMode, history, isLoaded, selectedId]);

  useEffect(() => {
    if (!isLoaded) {
      return;
    }

    for (const item of history) {
      if (item.jobId && isPendingJobStatus(item.jobStatus)) {
        const runtime =
          runtimes.find((candidate) => candidate.runtimeId === item.runtimeId) ?? {
            runtimeId: item.runtimeId,
            displayName: item.runtimeLabel,
            leanVersion: item.leanVersion,
            isDefault: false,
          };
        pollVerificationJob(item.id, item.jobId, runtime);
      }
    }
  }, [history, isLoaded, pollVerificationJob, runtimes]);

  useEffect(() => {
    return () => {
      stopAllPolling();
    };
  }, [stopAllPolling]);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setIsResizing(true);
  }, []);

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isResizing) {
        return;
      }
      const newWidth = window.innerWidth - e.clientX;
      if (newWidth >= 300 && newWidth <= 800) {
        setRightSidebarWidth(newWidth);
      }
    };

    const handleMouseUp = () => {
      setIsResizing(false);
    };

    if (isResizing) {
      document.addEventListener('mousemove', handleMouseMove);
      document.addEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
    }

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
  }, [isResizing]);

  if (!isLoaded) {
    return (
      <div className="h-screen flex items-center justify-center bg-background">
        <div className="flex flex-col items-center gap-4">
          <Loader2 className="h-8 w-8 animate-spin text-primary" />
          <p className="text-muted-foreground">Loading...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="h-screen overflow-hidden flex bg-background">
      <div className="w-72 shrink-0 min-h-0">
        <HistorySidebar
          history={history}
          selectedId={selectedId}
          onSelect={handleSelectHistory}
          onDelete={handleDeleteHistory}
          onClear={handleClearHistory}
          onNew={handleNew}
        />
      </div>

      <div className="flex-1 flex flex-col min-w-0 min-h-0">
        <header className="border-b border-border bg-card px-6 py-3">
          <div className="flex min-h-10 flex-wrap items-center gap-4">
            <div className="flex min-w-0 items-center gap-3">
              <div className="h-10 w-10 rounded-lg bg-gradient-to-br from-primary to-primary/60 flex items-center justify-center">
                <Code2 className="h-5 w-5 text-primary-foreground" />
              </div>
              <div className="min-w-0">
                <h1 className="text-lg font-bold">Lean Runtime Gateway</h1>
                <p className="text-xs text-muted-foreground">
                  Async-first verification across Lean 4 runtimes
                </p>
              </div>
            </div>
            <div className="ml-auto flex flex-1 flex-wrap items-center justify-end gap-3">
              <div className="relative min-w-[12rem] flex-1 basis-[12rem] sm:max-w-56 sm:flex-none">
                <select
                  value={selectedRuntimeId}
                  onChange={(e) => setSelectedRuntimeId(e.target.value)}
                  className="h-9 w-full appearance-none rounded-md border border-input bg-background/70 pl-3 pr-10 text-sm leading-none shadow-xs outline-none transition-[border-color,box-shadow,background-color] focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {runtimes.map((runtime) => (
                    <option key={runtime.runtimeId} value={runtime.runtimeId}>
                      {runtime.displayName}
                    </option>
                  ))}
                </select>
                <ChevronDown className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              </div>
              <Input
                placeholder="Verification title (optional)"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="h-9 min-w-[14rem] flex-1 basis-64 bg-background/70"
              />
              <Button
                onClick={handleVerify}
                disabled={isSubmitting || !code.trim() || !selectedRuntime}
                className="h-9 w-[9.5rem] justify-center gap-2"
              >
                {isSubmitting ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Starting...
                  </>
                ) : (
                  <>
                    <Play className="h-4 w-4" />
                    Verify
                  </>
                )}
              </Button>
            </div>
          </div>
        </header>

        <div className="flex-1 flex min-h-0 overflow-hidden">
          <div className="flex-1 flex flex-col min-w-0 min-h-0 p-4">
            <div className="flex items-center gap-2 mb-3">
              <Sparkles className="h-4 w-4 text-primary" />
              <span className="text-sm font-medium">Lean Code</span>
            </div>
            <div className="flex-1 min-h-0">
              <CodeEditor value={code} onChange={setCode} />
            </div>
          </div>

          <div
            className="w-1 bg-border hover:bg-primary/50 cursor-col-resize transition-colors relative group"
            onMouseDown={handleMouseDown}
          >
            <div className="absolute inset-y-0 -left-1 -right-1" />
          </div>

          <div
            ref={resizeRef}
            className="shrink-0 bg-card min-h-0 flex flex-col"
            style={{ width: `${rightSidebarWidth}px` }}
          >
            <VerificationPanel result={currentResult} />
          </div>
        </div>
      </div>

      <Dialog open={showClearDialog} onOpenChange={setShowClearDialog}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Clear All History</DialogTitle>
            <DialogDescription>
              Are you sure you want to clear all verification history? This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setShowClearDialog(false)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={confirmClearHistory}>
              Clear All
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
