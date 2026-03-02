import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';

import { Button } from '@/components/ui/Button';
import { createBragDraftFromSnippet } from '@/lib/bragDrafts';
import { EntryApiError, deleteEntry, fetchEntryAudioBlob, fetchEntryDetail, type EntryDetail } from '@/lib/entries';

type AudioState = {
  src: string | null;
  isObjectUrl: boolean;
};

type HighlightRange = {
  start: number;
  end: number;
};

type TranscriptSnippet = HighlightRange & {
  text: string;
};

function parseHighlightRange(searchParams: URLSearchParams): HighlightRange | null {
  const startRaw = searchParams.get('start');
  const endRaw = searchParams.get('end');
  if (startRaw === null || endRaw === null) {
    return null;
  }

  const start = Number(startRaw);
  const end = Number(endRaw);
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end <= start) {
    return null;
  }

  return { start, end };
}

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

function buildDeleteErrorMessage(error: unknown): string {
  if (error instanceof EntryApiError) {
    if (error.status === 404) {
      return 'Entry not found. It may already be deleted.';
    }

    return error.message;
  }

  if (error instanceof Error) {
    return error.message;
  }

  return 'Failed to delete entry.';
}

function normalizeSnippetText(value: string): string {
  return value.replace(/\s+/g, ' ').trim();
}

function getSelectedSnippet(container: HTMLElement, transcriptText: string): TranscriptSnippet | null {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed) {
    return null;
  }

  const range = selection.getRangeAt(0);
  if (!container.contains(range.commonAncestorContainer)) {
    return null;
  }

  const selectedRaw = selection.toString();
  if (!selectedRaw) {
    return null;
  }

  const prefixRange = document.createRange();
  prefixRange.selectNodeContents(container);
  try {
    prefixRange.setEnd(range.startContainer, range.startOffset);
  } catch {
    return null;
  }

  const start = prefixRange.toString().length;
  if (start < 0 || start >= transcriptText.length) {
    return null;
  }

  const end = Math.min(start + selectedRaw.length, transcriptText.length);
  if (end <= start) {
    return null;
  }

  const text = normalizeSnippetText(transcriptText.slice(start, end));
  if (!text) {
    return null;
  }

  return { start, end, text };
}

export function EntryDetailPage() {
  const { entryId } = useParams<{ entryId: string }>();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();

  const [entry, setEntry] = useState<EntryDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [audioState, setAudioState] = useState<AudioState>({ src: null, isObjectUrl: false });
  const [selectedSnippet, setSelectedSnippet] = useState<TranscriptSnippet | null>(null);
  const [bragNotice, setBragNotice] = useState<string | null>(null);
  const [bragError, setBragError] = useState<string | null>(null);
  const [isDeleteModalOpen, setIsDeleteModalOpen] = useState(false);
  const [isDeletingEntry, setIsDeletingEntry] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const transcriptRef = useRef<HTMLPreElement | null>(null);
  const highlightRef = useRef<HTMLSpanElement | null>(null);

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
    if (typeof entry?.transcriptText !== 'string') {
      return null;
    }

    return entry.transcriptText.length > 0 ? entry.transcriptText : null;
  }, [entry?.transcriptText]);
  const highlightRange = useMemo(() => parseHighlightRange(searchParams), [searchParams]);
  const highlightQuery = useMemo(() => searchParams.get('q')?.trim() ?? '', [searchParams]);
  const clampedHighlightRange = useMemo(() => {
    if (!transcriptText || !highlightRange) {
      return null;
    }

    if (highlightRange.start >= transcriptText.length) {
      return null;
    }

    const safeEnd = Math.min(highlightRange.end, transcriptText.length);
    if (safeEnd <= highlightRange.start) {
      return null;
    }

    return { start: highlightRange.start, end: safeEnd };
  }, [highlightRange, transcriptText]);
  const highlightedSnippet = useMemo(() => {
    if (!transcriptText || !clampedHighlightRange) {
      return null;
    }

    const text = normalizeSnippetText(transcriptText.slice(clampedHighlightRange.start, clampedHighlightRange.end));
    if (!text) {
      return null;
    }

    return {
      start: clampedHighlightRange.start,
      end: clampedHighlightRange.end,
      text
    };
  }, [clampedHighlightRange, transcriptText]);
  const activeSnippet = selectedSnippet ?? highlightedSnippet;

  useEffect(() => {
    if (!clampedHighlightRange) {
      return;
    }

    const target = highlightRef.current;
    if (!target) {
      return;
    }

    const frame = window.requestAnimationFrame(() => {
      target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });

    return () => {
      window.cancelAnimationFrame(frame);
    };
  }, [clampedHighlightRange, transcriptText]);

  useEffect(() => {
    setSelectedSnippet(null);
    setBragNotice(null);
    setBragError(null);
  }, [entry?.entryId, transcriptText]);

  function handleTranscriptSelection() {
    if (!transcriptText || !transcriptRef.current) {
      return;
    }

    const snippet = getSelectedSnippet(transcriptRef.current, transcriptText);
    if (!snippet) {
      return;
    }

    setSelectedSnippet(snippet);
    setBragNotice(null);
    setBragError(null);
  }

  function handleAddToBrag() {
    if (!entry || !activeSnippet) {
      setBragError('Select transcript text or open from a highlighted snippet first.');
      setBragNotice(null);
      return;
    }

    try {
      createBragDraftFromSnippet({
        entryId: entry.entryId,
        transcriptVersion: entry.transcript?.version ?? null,
        startChar: activeSnippet.start,
        endChar: activeSnippet.end,
        snippetText: activeSnippet.text
      });
      setBragError(null);
      setBragNotice(`Saved draft from chars ${activeSnippet.start}-${activeSnippet.end}.`);
    } catch (error) {
      setBragNotice(null);
      setBragError(error instanceof Error ? error.message : 'Failed to add draft to Brag.');
    }
  }

  async function handleConfirmDelete() {
    if (!entry || isDeletingEntry) {
      return;
    }

    setIsDeletingEntry(true);
    setDeleteError(null);
    try {
      await deleteEntry(entry.entryId);
      navigate('/app');
    } catch (error) {
      setDeleteError(buildDeleteErrorMessage(error));
    } finally {
      setIsDeletingEntry(false);
    }
  }

  return (
    <main className="min-h-screen bg-gradient-to-br from-sky-50 via-cyan-50 to-emerald-100 p-6">
      <section className="mx-auto mt-12 w-full max-w-3xl rounded-lg border bg-card p-8 text-card-foreground shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-2xl font-semibold tracking-tight">Entry Detail</h1>
          <div className="flex flex-wrap gap-2">
            {entry && !errorMessage ? (
              <Button
                onClick={() => {
                  setDeleteError(null);
                  setIsDeleteModalOpen(true);
                }}
                variant="outline"
                className="border-destructive/50 text-destructive hover:bg-destructive/10 hover:text-destructive"
              >
                Delete entry
              </Button>
            ) : null}
            <Button onClick={() => navigate('/app')} variant="outline">
              Back to app
            </Button>
          </div>
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
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 className="text-base font-semibold">Transcript</h2>
                <Button onClick={handleAddToBrag} disabled={!activeSnippet} size="sm" variant="outline">
                  Add to Brag
                </Button>
              </div>
              {clampedHighlightRange ? (
                <p className="mt-2 text-xs text-muted-foreground">
                  Highlighted match
                  {highlightQuery ? ` for "${highlightQuery}"` : ''}:{' '}
                  {clampedHighlightRange.start}-{clampedHighlightRange.end}
                </p>
              ) : null}
              {selectedSnippet ? (
                <p className="mt-2 text-xs text-muted-foreground">
                  Selected snippet: {selectedSnippet.start}-{selectedSnippet.end}
                </p>
              ) : null}
              {transcriptText ? (
                <pre
                  ref={transcriptRef}
                  className="mt-3 whitespace-pre-wrap break-words text-sm leading-6 text-foreground"
                  onMouseUp={handleTranscriptSelection}
                >
                  {clampedHighlightRange ? (
                    <>
                      {transcriptText.slice(0, clampedHighlightRange.start)}
                      <span
                        ref={highlightRef}
                        className="rounded bg-amber-200/90 px-0.5 text-foreground shadow-[0_0_0_2px_rgba(245,158,11,0.25)]"
                      >
                        {transcriptText.slice(clampedHighlightRange.start, clampedHighlightRange.end)}
                      </span>
                      {transcriptText.slice(clampedHighlightRange.end)}
                    </>
                  ) : (
                    transcriptText
                  )}
                </pre>
              ) : (
                <p className="mt-3 text-sm text-muted-foreground">Transcript is not available yet for this entry.</p>
              )}
              {bragNotice ? <p className="mt-3 text-sm text-emerald-700">{bragNotice}</p> : null}
              {bragError ? <p className="mt-3 text-sm text-destructive">{bragError}</p> : null}
            </section>
          </>
        ) : null}
      </section>
      {isDeleteModalOpen && entry ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4" role="presentation">
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-entry-title"
            className="w-full max-w-lg rounded-lg border bg-card p-6 text-card-foreground shadow-lg"
          >
            <h2 id="delete-entry-title" className="text-lg font-semibold text-destructive">
              Delete entry permanently?
            </h2>
            <p className="mt-2 text-sm text-muted-foreground">
              This action is irreversible. Audio, transcript versions, and linked evidence will be removed.
            </p>
            <p className="mt-2 rounded-md border bg-muted/20 p-2 text-xs font-mono">{entry.entryId}</p>
            {deleteError ? <p className="mt-3 text-sm text-destructive">{deleteError}</p> : null}
            <div className="mt-5 flex flex-wrap justify-end gap-2">
              <Button
                variant="outline"
                onClick={() => {
                  if (isDeletingEntry) {
                    return;
                  }
                  setIsDeleteModalOpen(false);
                  setDeleteError(null);
                }}
                disabled={isDeletingEntry}
              >
                Cancel
              </Button>
              <Button
                onClick={() => {
                  void handleConfirmDelete();
                }}
                disabled={isDeletingEntry}
                className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              >
                {isDeletingEntry ? 'Deleting...' : 'Yes, delete entry'}
              </Button>
            </div>
          </section>
        </div>
      ) : null}
    </main>
  );
}
