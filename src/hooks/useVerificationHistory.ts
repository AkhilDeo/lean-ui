'use client';

import { useState, useEffect, useCallback } from 'react';
import { VerificationResult } from '@/types/verification';

const STORAGE_KEY = 'lean-verification-history';

const LEGACY_ENVIRONMENT = {
  requestedEnvironment: 'auto' as const,
  resolvedEnvironmentId: 'mathlib-v4.15',
  resolvedProjectLabel: 'Mathlib',
  leanVersion: '4.15',
};

export function useVerificationHistory() {
  const [history, setHistory] = useState<VerificationResult[]>(() => {
    if (typeof window === 'undefined') {
      return [];
    }

    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (!stored) {
      return [];
    }

    try {
      const parsed = JSON.parse(stored);
      return parsed.map((item: VerificationResult) => ({
        ...LEGACY_ENVIRONMENT,
        ...item,
        timestamp: new Date(item.timestamp),
      }));
    } catch (e) {
      console.error('Failed to parse history:', e);
      return [];
    }
  });
  const isLoaded = true;

  useEffect(() => {
    if (isLoaded) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(history));
    }
  }, [history, isLoaded]);

  const addVerification = useCallback((verification: VerificationResult) => {
    setHistory((prev) => [verification, ...prev]);
  }, []);

  const updateVerification = useCallback((id: string, updates: Partial<VerificationResult>) => {
    setHistory((prev) =>
      prev.map((item) => (item.id === id ? { ...item, ...updates } : item))
    );
  }, []);

  const deleteVerification = useCallback((id: string) => {
    setHistory((prev) => prev.filter((item) => item.id !== id));
  }, []);

  const clearHistory = useCallback(() => {
    setHistory([]);
  }, []);

  const getVerification = useCallback(
    (id: string) => history.find((item) => item.id === id),
    [history]
  );

  return {
    history,
    isLoaded,
    addVerification,
    updateVerification,
    deleteVerification,
    clearHistory,
    getVerification,
  };
}
