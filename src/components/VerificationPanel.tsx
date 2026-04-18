'use client';

import { VerificationResult } from '@/types/verification';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { CheckCircle, XCircle, AlertTriangle, Clock } from 'lucide-react';

interface VerificationPanelProps {
  result: VerificationResult | null;
}

function formatDurationMs(value: number | null | undefined): string | null {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
    return null;
  }
  if (value < 1000) {
    return `${value} ms`;
  }
  return `${(value / 1000).toFixed(2)} s`;
}

export function VerificationPanel({ result }: VerificationPanelProps) {
  if (!result) {
    return (
      <div className="h-full flex flex-col items-center justify-center text-muted-foreground">
        <Clock className="h-12 w-12 mb-4 opacity-50" />
        <p className="text-lg font-medium">No verification yet</p>
        <p className="text-sm">Write some Lean code and click Verify</p>
      </div>
    );
  }

  const getStatusIcon = () => {
    switch (result.status) {
      case 'success':
        return <CheckCircle className="h-6 w-6 text-green-500" />;
      case 'error':
        return <XCircle className="h-6 w-6 text-red-500" />;
      case 'warning':
        return <AlertTriangle className="h-6 w-6 text-yellow-500" />;
      default:
        return <Clock className="h-6 w-6 text-muted-foreground" />;
    }
  };

  const submitLatencyLabel = formatDurationMs(result.submitLatencyMs);
  const queueWaitLabel = formatDurationMs(result.jobTiming?.queueWaitMs);
  const runLabel = formatDurationMs(result.jobTiming?.runMs);
  const backendTotalLabel = formatDurationMs(result.jobTiming?.totalMs);
  const hasTimingDetails = Boolean(
    submitLatencyLabel || queueWaitLabel || runLabel || backendTotalLabel
  );

  const getStatusBadge = () => {
    if (result.status === 'pending') {
      return (
        <Badge variant="secondary">
          {result.jobStatus === 'running' ? 'Running' : 'Queued'}
        </Badge>
      );
    }
    switch (result.status) {
      case 'success':
        return <Badge className="bg-green-500/20 text-green-500 border-green-500/30">Verified</Badge>;
      case 'error':
        return <Badge variant="destructive">Error</Badge>;
      case 'warning':
        return <Badge className="bg-yellow-500/20 text-yellow-500 border-yellow-500/30">Warning</Badge>;
      default:
        return <Badge variant="secondary">Pending</Badge>;
    }
  };

  return (
    <div className="h-full min-h-0 flex flex-col">
      <div className="flex items-center gap-3 p-4 border-b border-border">
        {getStatusIcon()}
        <div className="flex-1">
          <h3 className="font-semibold">{result.title || 'Verification Result'}</h3>
          <p className="text-xs text-muted-foreground">
            {result.runtimeLabel} • Lean {result.leanVersion} • {new Date(result.timestamp).toLocaleString()}
          </p>
        </div>
        {getStatusBadge()}
      </div>

      <ScrollArea className="flex-1 min-h-0">
        <div className="p-4">
          {result.status === 'pending' && (
            <div className="flex flex-col items-center justify-center py-8 text-center text-muted-foreground">
              <div className="mb-4 h-10 w-10 animate-spin rounded-full border-b-2 border-primary" />
              <p className="text-lg font-medium text-foreground">
                {result.jobStatus === 'running' ? 'Verification in progress' : 'Verification queued'}
              </p>
              <p className="mt-2 max-w-sm text-sm">
                {result.progressMessage ?? 'Submitting verification job...'}
              </p>
              <p className="mt-2 max-w-sm text-xs text-muted-foreground">
                Complex proofs can take several minutes. This job will continue even if you reload the page.
              </p>
              {result.jobExpiresAt && (
                <p className="mt-2 text-xs text-muted-foreground">
                  Job expires at {new Date(result.jobExpiresAt).toLocaleString()}.
                </p>
              )}
            </div>
          )}

          {hasTimingDetails && (
            <div className="mb-6 rounded-lg border border-border/70 bg-background/50 p-4 text-sm">
              <p className="font-medium text-foreground">Timing Breakdown</p>
              <div className="mt-2 space-y-1 text-xs text-muted-foreground">
                {submitLatencyLabel && <p>Submit latency: {submitLatencyLabel}</p>}
                {queueWaitLabel && <p>Queue wait: {queueWaitLabel}</p>}
                {runLabel && <p>Execution: {runLabel}</p>}
                {backendTotalLabel && <p>Total backend time: {backendTotalLabel}</p>}
              </div>
            </div>
          )}

          {result.status === 'success' &&
            result.errors.length === 0 &&
            result.warnings.length === 0 && (
              <div className="flex flex-col items-center justify-center py-8 text-green-500">
                <CheckCircle className="h-16 w-16 mb-4" />
                <p className="text-lg font-medium">Verification Successful!</p>
                <p className="text-sm text-muted-foreground mt-1">Your Lean code is valid</p>
              </div>
            )}

          {result.status === 'error' && result.jobStatus === 'expired' && (
            <div className="mb-6 rounded-lg border border-amber-500/20 bg-amber-500/10 p-4 text-sm text-amber-100">
              <p className="font-medium text-amber-200">Verification job expired</p>
              <p className="mt-1 text-amber-100/90">
                The queued proof is no longer available. Resubmit it to start a new async verification.
              </p>
            </div>
          )}

          {result.errors.length > 0 && (
            <div className="mb-6">
              <h4 className="text-sm font-semibold text-red-500 mb-3 flex items-center gap-2">
                <XCircle className="h-4 w-4" />
                Errors ({result.errors.length})
              </h4>
              <div className="space-y-2">
                {result.errors.map((error, index) => (
                  <div
                    key={index}
                    className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-sm font-mono whitespace-pre-wrap break-words overflow-wrap-anywhere"
                  >
                    {error}
                  </div>
                ))}
              </div>
            </div>
          )}

          {result.warnings.length > 0 && (
            <div>
              <h4 className="text-sm font-semibold text-yellow-500 mb-3 flex items-center gap-2">
                <AlertTriangle className="h-4 w-4" />
                Warnings ({result.warnings.length})
              </h4>
              <div className="space-y-2">
                {result.warnings.map((warning, index) => (
                  <div
                    key={index}
                    className="p-3 rounded-lg bg-yellow-500/10 border border-yellow-500/20 text-sm font-mono whitespace-pre-wrap break-words overflow-wrap-anywhere"
                  >
                    {warning}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
