'use client';

import { useState, useCallback, useEffect, useRef } from 'react';
import { v4 as uuidv4 } from 'uuid';
import { CodeEditor } from './CodeEditor';
import { VerificationPanel } from './VerificationPanel';
import { HistorySidebar } from './HistorySidebar';
import { useVerificationHistory } from '@/hooks/useVerificationHistory';
import { VerificationResult } from '@/types/verification';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Separator } from '@/components/ui/separator';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Play, Loader2, Code2, Sparkles } from 'lucide-react';
import { generateRandomName } from '@/lib/nameGenerator';

const DEFAULT_CODE = `-- Welcome to Lean 4.15 Verifier!
-- Write your Lean code below and click "Verify" to check it.

theorem hello_world : 1 + 1 = 2 := by
  rfl
`;

export function LeanVerifier() {
  const [code, setCode] = useState(DEFAULT_CODE);
  const [title, setTitle] = useState('');
  const [isVerifying, setIsVerifying] = useState(false);
  const [currentResult, setCurrentResult] = useState<VerificationResult | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showClearDialog, setShowClearDialog] = useState(false);
  const [rightSidebarWidth, setRightSidebarWidth] = useState(400);
  const [isResizing, setIsResizing] = useState(false);
  const resizeRef = useRef<HTMLDivElement>(null);

  const {
    history,
    isLoaded,
    addVerification,
    updateVerification,
    deleteVerification,
    clearHistory,
    getVerification,
  } = useVerificationHistory();

  const handleVerify = useCallback(async () => {
    if (!code.trim()) return;

    setIsVerifying(true);
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
      leanVersion: '4.15',
    };

    addVerification(newVerification);
    setSelectedId(id);
    setCurrentResult(newVerification);

    try {
      const response = await fetch('/api/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code }),
      });

      const result = await response.json();

      const errors: string[] = [];
      const warnings: string[] = [];

      if (result.error) {
        errors.push(result.error);
      }

      if (result.warnings && Array.isArray(result.warnings)) {
        warnings.push(...result.warnings);
      }

      let status: VerificationResult['status'] = 'success';
      if (errors.length > 0) {
        status = 'error';
      } else if (warnings.length > 0) {
        status = 'warning';
      } else if (!result.pass) {
        status = 'error';
        if (!result.error) {
          errors.push('Verification failed');
        }
      }

      const updatedResult: Partial<VerificationResult> = {
        status,
        errors,
        warnings,
      };

      updateVerification(id, updatedResult);
      setCurrentResult((prev) => (prev ? { ...prev, ...updatedResult } : null));
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Unknown error';
      const updatedResult: Partial<VerificationResult> = {
        status: 'error',
        errors: [`Failed to connect to verification server: ${errorMessage}`],
      };
      updateVerification(id, updatedResult);
      setCurrentResult((prev) => (prev ? { ...prev, ...updatedResult } : null));
    } finally {
      setIsVerifying(false);
    }
  }, [code, title, history.length, addVerification, updateVerification]);

  const handleSelectHistory = useCallback(
    (id: string) => {
      const verification = getVerification(id);
      if (verification) {
        setSelectedId(id);
        setCode(verification.code);
        setTitle(verification.title);
        setCurrentResult(verification);
      }
    },
    [getVerification]
  );

  const handleDeleteHistory = useCallback(
    (id: string) => {
      deleteVerification(id);
      if (selectedId === id) {
        setSelectedId(null);
        setCurrentResult(null);
        setCode(DEFAULT_CODE);
        setTitle('');
      }
    },
    [deleteVerification, selectedId]
  );

  const handleClearHistory = useCallback(() => {
    setShowClearDialog(true);
  }, []);

  const confirmClearHistory = useCallback(() => {
    clearHistory();
    setSelectedId(null);
    setCurrentResult(null);
    setCode(DEFAULT_CODE);
    setTitle('');
    setShowClearDialog(false);
  }, [clearHistory]);

  const handleNew = useCallback(() => {
    setSelectedId(null);
    setCurrentResult(null);
    setCode(DEFAULT_CODE);
    setTitle('');
  }, []);

  useEffect(() => {
    if (isLoaded && history.length > 0 && !selectedId) {
      const latest = history[0];
      setSelectedId(latest.id);
      setCode(latest.code);
      setTitle(latest.title);
      setCurrentResult(latest);
    }
  }, [isLoaded, history, selectedId]);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setIsResizing(true);
  }, []);

  useEffect(() => {
    const handleMouseMove = (e: MouseEvent) => {
      if (!isResizing) return;
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
    <div className="h-screen flex bg-background">
      {/* History Sidebar */}
      <div className="w-72 shrink-0">
        <HistorySidebar
          history={history}
          selectedId={selectedId}
          onSelect={handleSelectHistory}
          onDelete={handleDeleteHistory}
          onClear={handleClearHistory}
          onNew={handleNew}
        />
      </div>

      {/* Main Content */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Header */}
        <header className="h-16 border-b border-border flex items-center justify-between px-6 bg-card">
          <div className="flex items-center gap-3">
            <div className="h-10 w-10 rounded-lg bg-gradient-to-br from-primary to-primary/60 flex items-center justify-center">
              <Code2 className="h-5 w-5 text-primary-foreground" />
            </div>
            <div>
              <h1 className="text-lg font-bold">Lean 4.15 Verifier</h1>
              <p className="text-xs text-muted-foreground">Powered by kimina-lean-server</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Input
              placeholder="Verification title (optional)"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="w-64"
            />
            <Button
              onClick={handleVerify}
              disabled={isVerifying || !code.trim()}
              className="gap-2"
            >
              {isVerifying ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Verifying...
                </>
              ) : (
                <>
                  <Play className="h-4 w-4" />
                  Verify
                </>
              )}
            </Button>
          </div>
        </header>

        {/* Editor and Results */}
        <div className="flex-1 flex min-h-0">
          {/* Code Editor */}
          <div className="flex-1 flex flex-col min-w-0 p-4">
            <div className="flex items-center gap-2 mb-3">
              <Sparkles className="h-4 w-4 text-primary" />
              <span className="text-sm font-medium">Lean Code</span>
            </div>
            <div className="flex-1 min-h-0">
              <CodeEditor value={code} onChange={setCode} />
            </div>
          </div>

          {/* Resize Handle */}
          <div
            className="w-1 bg-border hover:bg-primary/50 cursor-col-resize transition-colors relative group"
            onMouseDown={handleMouseDown}
          >
            <div className="absolute inset-y-0 -left-1 -right-1" />
          </div>

          {/* Results Panel */}
          <div
            ref={resizeRef}
            className="shrink-0 bg-card"
            style={{ width: `${rightSidebarWidth}px` }}
          >
            <VerificationPanel result={currentResult} isLoading={isVerifying} />
          </div>
        </div>
      </div>

      {/* Clear History Dialog */}
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
