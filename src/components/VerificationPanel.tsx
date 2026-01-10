'use client';

import { VerificationResult } from '@/types/verification';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { CheckCircle, XCircle, AlertTriangle, Clock } from 'lucide-react';

interface VerificationPanelProps {
  result: VerificationResult | null;
  isLoading: boolean;
}

export function VerificationPanel({ result, isLoading }: VerificationPanelProps) {
  if (isLoading) {
    return (
      <div className="h-full flex flex-col items-center justify-center text-muted-foreground">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary mb-4" />
        <p>Verifying with Lean 4.15...</p>
      </div>
    );
  }

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

  const getStatusBadge = () => {
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
    <div className="h-full flex flex-col">
      <div className="flex items-center gap-3 p-4 border-b border-border">
        {getStatusIcon()}
        <div className="flex-1">
          <h3 className="font-semibold">{result.title || 'Verification Result'}</h3>
          <p className="text-xs text-muted-foreground">
            Lean {result.leanVersion} • {new Date(result.timestamp).toLocaleString()}
          </p>
        </div>
        {getStatusBadge()}
      </div>

      <ScrollArea className="flex-1 p-4">
        {result.status === 'success' && result.errors.length === 0 && result.warnings.length === 0 && (
          <div className="flex flex-col items-center justify-center py-8 text-green-500">
            <CheckCircle className="h-16 w-16 mb-4" />
            <p className="text-lg font-medium">Verification Successful!</p>
            <p className="text-sm text-muted-foreground mt-1">Your Lean code is valid</p>
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
                  className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-sm font-mono whitespace-pre-wrap"
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
                  className="p-3 rounded-lg bg-yellow-500/10 border border-yellow-500/20 text-sm font-mono whitespace-pre-wrap"
                >
                  {warning}
                </div>
              ))}
            </div>
          </div>
        )}
      </ScrollArea>
    </div>
  );
}
