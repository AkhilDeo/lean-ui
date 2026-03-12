'use client';

import { useState, useEffect, useCallback } from 'react';
import { VerificationResult } from '@/types/verification';

const STORAGE_KEY = 'lean-verification-history';

export function useVerificationHistory() {
  const [history, setHistory] = useState<VerificationResult[]>([]);
  const [isLoaded, setIsLoaded] = useState(false);

  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      try {
        const parsed = JSON.parse(stored);
        const withDates = parsed.map((item: VerificationResult) => ({
          ...item,
          timestamp: new Date(item.timestamp),
        }));
        setHistory(withDates);
      } catch (e) {
        console.error('Failed to parse history:', e);
        setHistory([]);
      }
    }
    setIsLoaded(true);
  }, []);

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
