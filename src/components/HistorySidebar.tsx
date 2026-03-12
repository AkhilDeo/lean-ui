'use client';

import { VerificationResult } from '@/types/verification';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Badge } from '@/components/ui/badge';
import {
  CheckCircle,
  XCircle,
  AlertTriangle,
  Clock,
  Trash2,
  History,
  Plus,
} from 'lucide-react';

interface HistorySidebarProps {
  history: VerificationResult[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onClear: () => void;
  onNew: () => void;
}

export function HistorySidebar({
  history,
  selectedId,
  onSelect,
  onDelete,
  onClear,
  onNew,
}: HistorySidebarProps) {
  const getStatusIcon = (status: VerificationResult['status']) => {
    switch (status) {
      case 'success':
        return <CheckCircle className="h-4 w-4 text-green-500 shrink-0" />;
      case 'error':
        return <XCircle className="h-4 w-4 text-red-500 shrink-0" />;
      case 'warning':
        return <AlertTriangle className="h-4 w-4 text-yellow-500 shrink-0" />;
      default:
        return <Clock className="h-4 w-4 text-muted-foreground shrink-0" />;
    }
  };

  const formatTime = (date: Date) => {
    const now = new Date();
    const diff = now.getTime() - new Date(date).getTime();
    const minutes = Math.floor(diff / 60000);
    const hours = Math.floor(diff / 3600000);
    const days = Math.floor(diff / 86400000);

    if (minutes < 1) return 'Just now';
    if (minutes < 60) return `${minutes}m ago`;
    if (hours < 24) return `${hours}h ago`;
    return `${days}d ago`;
  };

  return (
    <div className="h-full flex flex-col bg-card border-r border-border">
      <div className="p-4 border-b border-border">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <History className="h-5 w-5 text-primary" />
            <h2 className="font-semibold">History</h2>
          </div>
          <Badge variant="secondary" className="text-xs">
            {history.length}
          </Badge>
        </div>
        <Button onClick={onNew} className="w-full" size="sm">
          <Plus className="h-4 w-4 mr-2" />
          New Verification
        </Button>
      </div>

      <ScrollArea className="flex-1">
        <div className="p-2 space-y-1">
          {history.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground text-sm">
              <Clock className="h-8 w-8 mx-auto mb-2 opacity-50" />
              <p>No verifications yet</p>
            </div>
          ) : (
            history.map((item) => (
              <div
                key={item.id}
                className={`group relative rounded-md p-3 cursor-pointer transition-all ${
                  selectedId === item.id
                    ? 'bg-primary/10 border border-primary/30 shadow-sm'
                    : 'hover:bg-muted/50 border border-transparent hover:border-border/50'
                }`}
                onClick={() => onSelect(item.id)}
              >
                <div className="flex items-start gap-3">
                  <div className="mt-0.5">
                    {getStatusIcon(item.status)}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-semibold truncate mb-1">
                      {item.title || 'Untitled'}
                    </p>
                    <p className="text-xs text-muted-foreground/80 line-clamp-2 leading-relaxed mb-2">
                      {item.code.replace(/^--.*\n/gm, '').trim().slice(0, 60)}
                      {item.code.length > 60 ? '...' : ''}
                    </p>
                    <div className="flex items-center gap-2">
                      <p className="text-xs text-muted-foreground/60">
                        {formatTime(item.timestamp)}
                      </p>
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 opacity-0 group-hover:opacity-100 transition-opacity shrink-0 -mt-1"
                    onClick={(e) => {
                      e.stopPropagation();
                      onDelete(item.id);
                    }}
                  >
                    <Trash2 className="h-3.5 w-3.5 text-muted-foreground hover:text-destructive" />
                  </Button>
                </div>
              </div>
            ))
          )}
        </div>
      </ScrollArea>

      {history.length > 0 && (
        <div className="p-4 border-t border-border">
          <Button
            variant="outline"
            size="sm"
            className="w-full text-muted-foreground hover:text-destructive"
            onClick={onClear}
          >
            <Trash2 className="h-4 w-4 mr-2" />
            Clear All
          </Button>
        </div>
      )}
    </div>
  );
}
