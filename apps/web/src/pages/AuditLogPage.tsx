import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { Button } from '@/components/ui/Button';
import { fetchAuditLogPage, type AuditLogEvent } from '@/lib/audit';

const PAGE_SIZE = 25;

function formatAuditDate(value: string | null): string {
  if (!value) {
    return 'Unknown date';
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return parsed.toLocaleString();
}

function formatEventLabel(eventType: string): string {
  const normalized = eventType.trim();
  if (!normalized) {
    return 'unknown';
  }
  return normalized.replace(/[_-]+/g, ' ');
}

function readMetadataValue(event: AuditLogEvent, key: string): string | null {
  const value = event.metadata?.[key];
  if (typeof value === 'string' && value.trim().length > 0) {
    return value;
  }
  if (typeof value === 'number' && Number.isFinite(value)) {
    return String(value);
  }
  return null;
}

export function AuditLogPage() {
  const navigate = useNavigate();
  const [events, setEvents] = useState<AuditLogEvent[]>([]);
  const [page, setPage] = useState(1);
  const [reloadToken, setReloadToken] = useState(0);
  const [hasNext, setHasNext] = useState(false);
  const [total, setTotal] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      setIsLoading(true);
      setErrorMessage(null);
      try {
        const result = await fetchAuditLogPage(page, PAGE_SIZE);
        if (cancelled) {
          return;
        }
        setEvents(result.items);
        setHasNext(result.hasNext);
        setTotal(result.total);
      } catch (error) {
        if (cancelled) {
          return;
        }
        setErrorMessage(error instanceof Error ? error.message : 'Failed to load audit log.');
        setEvents([]);
        setHasNext(false);
        setTotal(null);
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    };

    void load();
    return () => {
      cancelled = true;
    };
  }, [page, reloadToken]);

  return (
    <main className="min-h-screen bg-gradient-to-br from-amber-50 via-orange-50 to-rose-100 p-6">
      <section className="mx-auto mt-12 w-full max-w-4xl rounded-lg border bg-card p-8 text-card-foreground shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Audit Log</h1>
            <p className="mt-2 text-sm text-muted-foreground">Review processing history and data operation events.</p>
            <p className="mt-1 text-xs text-muted-foreground">Content is excluded from this history by design.</p>
          </div>
          <Button variant="outline" onClick={() => navigate('/app')}>
            Back to app
          </Button>
        </div>

        <div className="mt-6 rounded-md border bg-background p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-muted-foreground">
              Page {page}
              {total !== null ? ` • ${total} total event${total === 1 ? '' : 's'}` : ''}
            </p>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" size="sm" onClick={() => setPage((value) => Math.max(1, value - 1))} disabled={isLoading || page <= 1}>
                Previous
              </Button>
              <Button variant="outline" size="sm" onClick={() => setPage((value) => value + 1)} disabled={isLoading || !hasNext}>
                Next
              </Button>
              <Button variant="outline" size="sm" onClick={() => setReloadToken((value) => value + 1)} disabled={isLoading}>
                Refresh
              </Button>
            </div>
          </div>

          {isLoading ? <p className="mt-4 text-sm text-muted-foreground">Loading audit events...</p> : null}
          {errorMessage ? <p className="mt-4 text-sm text-destructive">{errorMessage}</p> : null}

          {!isLoading && !errorMessage && events.length === 0 ? (
            <p className="mt-4 text-sm text-muted-foreground">No audit events found for this page.</p>
          ) : null}

          {!isLoading && !errorMessage && events.length > 0 ? (
            <div className="mt-4 space-y-3">
              {events.map((event) => {
                const method = readMetadataValue(event, 'method');
                const path = readMetadataValue(event, 'path');
                const statusCode = readMetadataValue(event, 'status_code');
                return (
                  <article key={event.id} className="rounded-md border p-3">
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <div className="space-y-1">
                        <p className="text-sm font-semibold">{formatEventLabel(event.eventType)}</p>
                        <p className="text-xs text-muted-foreground">{formatAuditDate(event.createdAt)}</p>
                      </div>
                      <span className="rounded-full bg-secondary px-2 py-1 text-xs">ID: {event.id}</span>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-2 text-xs">
                      {method ? <span className="rounded-full bg-slate-100 px-2 py-1 text-slate-700">Method: {method}</span> : null}
                      {path ? <span className="rounded-full bg-slate-100 px-2 py-1 text-slate-700">Path: {path}</span> : null}
                      {statusCode ? <span className="rounded-full bg-slate-100 px-2 py-1 text-slate-700">Status: {statusCode}</span> : null}
                      {event.entryId ? <span className="rounded-full bg-slate-100 px-2 py-1 text-slate-700">Entry: {event.entryId}</span> : null}
                      {event.userId ? <span className="rounded-full bg-slate-100 px-2 py-1 text-slate-700">User: {event.userId}</span> : null}
                    </div>
                  </article>
                );
              })}
            </div>
          ) : null}
        </div>
      </section>
    </main>
  );
}
