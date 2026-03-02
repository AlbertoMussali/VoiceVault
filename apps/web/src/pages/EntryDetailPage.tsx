import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';

import { Button } from '@/components/ui/Button';
import { EntryApiError, fetchEntryAudioBlob, fetchEntryDetail, type EntryDetail } from '@/lib/entries';

type AudioState = {
  src: string | null;
  isObjectUrl: boolean;
};

function formatStatus(status: string | null): string {
  if (!status) {
    return 'unknown';
  }

  return status.replace(/[_-]+/g, ' ');
}

function buildEntryErrorMessage(error: unknown): string {
  if (error instanceof EntryApiError) {
    if (error.status === 404) {
      return 'Entry not found.';
    }

    return error.message;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return 'Failed to load entry details.';
}

export function EntryDetailPage() {
  const { entryId } = useParams<{ entryId: string }>();
  const navigate = useNavigate();

  const [entry, setEntry] = useState<EntryDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [audioState, setAudioState] = useState<AudioState>({ src: null, isObjectUrl: false });

  useEffect(() => {
    if (!entryId) {
      setErrorMessage('Entry id is missing.');
      setIsLoading(false);
      return;
    }

    let cancelled = false;

    const load = async () => {
      setIsLoading(true);
      setErrorMessage(null);
      setAudioState({ src: null, isObjectUrl: false });

      try {
        const detail = await fetchEntryDetail(entryId);
        if (cancelled) {
          return;
        }

        setEntry(detail);

        if (detail.audioUrl) {
          setAudioState({ src: detail.audioUrl, isObjectUrl: false });
          return;
        }

        const audioBlob = await fetchEntryAudioBlob(entryId);
        if (cancelled) {
          return;
        }

        if (!audioBlob) {
          setAudioState({ src: null, isObjectUrl: false });
          return;
        }

        const objectUrl = URL.createObjectURL(audioBlob);
        setAudioState({ src: objectUrl, isObjectUrl: true });
      } catch (error) {
        if (cancelled) {
          return;
        }
        setErrorMessage(buildEntryErrorMessage(error));
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
  }, [entryId]);

  useEffect(() => {
    return () => {
      if (audioState.isObjectUrl && audioState.src) {
        URL.revokeObjectURL(audioState.src);
      }
    };
  }, [audioState]);

  const transcriptText = useMemo(() => {
    if (!entry?.transcriptText) {
      return null;
    }

    const normalized = entry.transcriptText.trim();
    return normalized.length > 0 ? normalized : null;
  }, [entry?.transcriptText]);

  return (
    <main className="min-h-screen bg-gradient-to-br from-sky-50 via-cyan-50 to-emerald-100 p-6">
      <section className="mx-auto mt-12 w-full max-w-3xl rounded-lg border bg-card p-8 text-card-foreground shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">Entry Detail</h1>
          <Button onClick={() => navigate('/app')} variant="outline">
            Back to app
          </Button>
        </div>

        {isLoading ? <p className="mt-4 text-sm text-muted-foreground">Loading entry...</p> : null}

        {errorMessage ? (
          <p className="mt-4 rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">{errorMessage}</p>
        ) : null}

        {entry && !errorMessage ? (
          <>
            <dl className="mt-6 rounded-md border bg-background p-4 text-sm">
              <div className="flex flex-wrap justify-between gap-3">
                <dt className="text-muted-foreground">Entry ID</dt>
                <dd className="font-mono text-xs sm:text-sm">{entry.entryId}</dd>
              </div>
              <div className="mt-3 flex flex-wrap justify-between gap-3">
                <dt className="text-muted-foreground">Status</dt>
                <dd className="font-medium capitalize">{formatStatus(entry.status)}</dd>
              </div>
            </dl>

            <section className="mt-6 rounded-md border bg-background p-4">
              <h2 className="text-base font-semibold">Audio</h2>
              {audioState.src ? (
                <audio controls className="mt-3 w-full" src={audioState.src} preload="metadata" />
              ) : (
                <p className="mt-3 text-sm text-muted-foreground">Audio is not available yet for this entry.</p>
              )}
            </section>

            <section className="mt-6 rounded-md border bg-background p-4">
              <h2 className="text-base font-semibold">Transcript</h2>
              {transcriptText ? (
                <pre className="mt-3 whitespace-pre-wrap break-words text-sm leading-6 text-foreground">{transcriptText}</pre>
              ) : (
                <p className="mt-3 text-sm text-muted-foreground">Transcript is not available yet for this entry.</p>
              )}
            </section>
          </>
        ) : null}
      </section>
    </main>
  );
}
