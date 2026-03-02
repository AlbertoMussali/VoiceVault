import { useEffect, useRef, useState, type ChangeEvent } from 'react';
import { useNavigate } from 'react-router-dom';

import { useAuth } from '@/auth/AuthProvider';
import { Button } from '@/components/ui/Button';
import {
  EntryApiError,
  createEntry,
  fetchEntryDetail,
  fetchEntryStatus,
  updateEntryTranscript,
  uploadEntryAudio
} from '@/lib/entries';

type PipelineState = 'idle' | 'creating' | 'uploading' | 'transcribing' | 'ready' | 'error';
type RetryAction = 'restart_pipeline' | 'retry_upload' | 'resume_polling' | null;
type PipelineStep = 'creating' | 'uploading' | 'polling';
type EntryContext = 'work' | 'life';

type EntryIndexingData = {
  type: string;
  context: EntryContext;
  tags: string[];
};

const READY_STATUSES = new Set(['ready', 'completed', 'done']);
const ERROR_STATUSES = new Set(['error', 'failed', 'fatal']);
const MAX_STATUS_POLLS = 40;
const INDEXING_STORAGE_KEY = 'voicevault.entry-indexing.v1';
const ENTRY_TYPE_OPTIONS = ['win', 'blocker', 'idea', 'task', 'learning'];

function parseTags(rawTags: string): string[] {
  const next = rawTags
    .split(',')
    .map((tag) => tag.trim())
    .filter((tag) => tag.length > 0)
    .slice(0, 10);

  return Array.from(new Set(next));
}

function readStoredIndexing(): Record<string, EntryIndexingData> {
  if (typeof window === 'undefined') {
    return {};
  }

  const raw = window.localStorage.getItem(INDEXING_STORAGE_KEY);
  if (!raw) {
    return {};
  }

  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return {};
    }

    const result: Record<string, EntryIndexingData> = {};
    for (const [key, value] of Object.entries(parsed)) {
      if (!value || typeof value !== 'object' || Array.isArray(value)) {
        continue;
      }

      const indexing = value as Record<string, unknown>;
      const type = typeof indexing.type === 'string' ? indexing.type.trim().toLowerCase() : '';
      const context = indexing.context === 'work' || indexing.context === 'life' ? indexing.context : null;
      const tags = Array.isArray(indexing.tags)
        ? indexing.tags.filter((tag): tag is string => typeof tag === 'string').map((tag) => tag.trim()).filter((tag) => tag.length > 0)
        : [];

      if (!type || !context) {
        continue;
      }

      result[key] = {
        type,
        context,
        tags: Array.from(new Set(tags)).slice(0, 10)
      };
    }

    return result;
  } catch {
    return {};
  }
}

function writeStoredIndexing(indexingByEntry: Record<string, EntryIndexingData>) {
  if (typeof window === 'undefined') {
    return;
  }

  window.localStorage.setItem(INDEXING_STORAGE_KEY, JSON.stringify(indexingByEntry));
}

function normalizeStatus(status: string | null): string {
  return (status ?? '').trim().toLowerCase();
}

function formatDuration(totalSeconds: number): string {
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  const minutePart = String(minutes).padStart(2, '0');
  const secondPart = String(seconds).padStart(2, '0');

  if (hours > 0) {
    return `${String(hours).padStart(2, '0')}:${minutePart}:${secondPart}`;
  }

  return `${minutePart}:${secondPart}`;
}

export function AppPage() {
  const { user, logoutCurrentUser } = useAuth();
  const navigate = useNavigate();

  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [recordingUrl, setRecordingUrl] = useState<string | null>(null);
  const [recordingBlob, setRecordingBlob] = useState<Blob | null>(null);
  const [recordingError, setRecordingError] = useState<string | null>(null);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const timerRef = useRef<number | null>(null);
  const startedAtRef = useRef<number | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [pipelineState, setPipelineState] = useState<PipelineState>('idle');
  const [entryId, setEntryId] = useState<string | null>(null);
  const [entryStatus, setEntryStatus] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [retryAction, setRetryAction] = useState<RetryAction>(null);
  const [entryTranscript, setEntryTranscript] = useState<string | null>(null);
  const [transcriptVersion, setTranscriptVersion] = useState<number | null>(null);
  const [isTranscriptEdited, setIsTranscriptEdited] = useState(false);
  const [isEditingTranscript, setIsEditingTranscript] = useState(false);
  const [transcriptDraft, setTranscriptDraft] = useState('');
  const [isSavingTranscript, setIsSavingTranscript] = useState(false);
  const [transcriptError, setTranscriptError] = useState<string | null>(null);
  const [transcriptNotice, setTranscriptNotice] = useState<string | null>(null);
  const [indexingByEntry, setIndexingByEntry] = useState<Record<string, EntryIndexingData>>(() => readStoredIndexing());
  const [isIndexingModalOpen, setIsIndexingModalOpen] = useState(false);
  const [indexType, setIndexType] = useState('');
  const [indexContext, setIndexContext] = useState<EntryContext | null>(null);
  const [indexTagsInput, setIndexTagsInput] = useState('');
  const pollTimerRef = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (timerRef.current !== null) {
        window.clearInterval(timerRef.current);
      }

      if (recordingUrl !== null) {
        URL.revokeObjectURL(recordingUrl);
      }

      if (streamRef.current !== null) {
        streamRef.current.getTracks().forEach((track) => track.stop());
      }
    };
  }, [recordingUrl]);

  useEffect(
    () => () => {
      if (pollTimerRef.current !== null) {
        window.clearInterval(pollTimerRef.current);
      }
    },
    []
  );

  function stopPolling() {
    if (pollTimerRef.current !== null) {
      window.clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }

  function resetTranscriptState() {
    setEntryTranscript(null);
    setTranscriptVersion(null);
    setIsTranscriptEdited(false);
    setIsEditingTranscript(false);
    setTranscriptDraft('');
    setTranscriptError(null);
    setTranscriptNotice(null);
  }

  async function refreshEntryDetail(targetEntryId: string) {
    const detail = await fetchEntryDetail(targetEntryId);
    if (detail.status) {
      setEntryStatus(detail.status);
    }

    if (!detail.transcript) {
      return;
    }

    setEntryTranscript(detail.transcript.text);
    setTranscriptVersion(detail.transcript.version);
    const editedByVersion = detail.transcript.version !== null && detail.transcript.version > 1;
    const editedBySource = detail.transcript.source !== null && detail.transcript.source !== 'stt';
    setIsTranscriptEdited(editedByVersion || editedBySource);
    if (!isEditingTranscript) {
      setTranscriptDraft(detail.transcript.text);
    }
  }

  function resetIndexingDraft() {
    setIndexType('');
    setIndexContext(null);
    setIndexTagsInput('');
  }

  function openIndexingModal(targetEntryId: string) {
    const existing = indexingByEntry[targetEntryId];
    if (existing) {
      setIndexType(existing.type);
      setIndexContext(existing.context);
      setIndexTagsInput(existing.tags.join(', '));
    } else {
      resetIndexingDraft();
    }

    setIsIndexingModalOpen(true);
  }

  async function handleLogout() {
    setIsLoggingOut(true);
    try {
      await logoutCurrentUser();
      navigate('/login', { replace: true });
    } finally {
      setIsLoggingOut(false);
    }
  }

  async function handleStartRecording() {
    if (isRecording) {
      return;
    }

    if (typeof window === 'undefined' || !window.MediaRecorder) {
      setRecordingError('MediaRecorder is not supported in this browser.');
      return;
    }

    try {
      setRecordingError(null);
      const userMediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = userMediaStream;

      const recorder = new MediaRecorder(userMediaStream, {
        mimeType: 'audio/webm'
      });

      chunksRef.current = [];
      recorderRef.current = recorder;
      startedAtRef.current = Date.now();
      setElapsedSeconds(0);
      setRecordingBlob(null);

      setRecordingUrl((previousUrl) => {
        if (previousUrl !== null) {
          URL.revokeObjectURL(previousUrl);
        }
        return null;
      });

      recorder.addEventListener('dataavailable', (event: BlobEvent) => {
        if (event.data.size > 0) {
          chunksRef.current.push(event.data);
        }
      });

      recorder.addEventListener('stop', () => {
        if (timerRef.current !== null) {
          window.clearInterval(timerRef.current);
          timerRef.current = null;
        }

        if (streamRef.current !== null) {
          streamRef.current.getTracks().forEach((track) => track.stop());
          streamRef.current = null;
        }

        const blob = new Blob(chunksRef.current, { type: 'audio/webm' });
        setRecordingBlob(blob);
        setRecordingUrl((previousUrl) => {
          if (previousUrl !== null) {
            URL.revokeObjectURL(previousUrl);
          }
          return URL.createObjectURL(blob);
        });

        setIsRecording(false);
      });

      recorder.start();
      setIsRecording(true);

      timerRef.current = window.setInterval(() => {
        const startedAt = startedAtRef.current;
        if (startedAt === null) {
          return;
        }
        setElapsedSeconds(Math.floor((Date.now() - startedAt) / 1000));
      }, 250);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Microphone access failed.';
      setRecordingError(message);
      setIsRecording(false);

      if (streamRef.current !== null) {
        streamRef.current.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
      }
    }
  }

  function handleStopRecording() {
    const recorder = recorderRef.current;

    if (!isRecording || recorder === null) {
      return;
    }

    if (recorder.state !== 'inactive') {
      recorder.stop();
    }
  }

  function handleAudioSelection(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] ?? null;
    setSelectedFile(file);
    setErrorMessage(null);
    setRetryAction(null);
    setTranscriptError(null);
    setTranscriptNotice(null);
    setIsEditingTranscript(false);
  }

  async function pollUntilComplete(nextEntryId: string) {
    let pollAttempts = 0;

    const poll = async (): Promise<boolean> => {
      try {
        pollAttempts += 1;
        const result = await fetchEntryStatus(nextEntryId);
        const normalized = normalizeStatus(result.status);
        setEntryStatus(result.status ?? 'processing');

        if (READY_STATUSES.has(normalized)) {
          setPipelineState('ready');
          setRetryAction(null);
          stopPolling();
          await refreshEntryDetail(nextEntryId);
          if (!indexingByEntry[nextEntryId]) {
            openIndexingModal(nextEntryId);
          }
          return false;
        }

        if (ERROR_STATUSES.has(normalized)) {
          setPipelineState('error');
          setErrorMessage('Transcription failed on the server.');
          setRetryAction(null);
          stopPolling();
          return false;
        }

        if (!normalized && pollAttempts >= MAX_STATUS_POLLS) {
          setPipelineState('error');
          setErrorMessage('Processing is still pending. Retry status polling.');
          setRetryAction('resume_polling');
          stopPolling();
          return false;
        }

        setPipelineState('transcribing');
        return true;
      } catch (error) {
        setPipelineState('error');
        setRetryAction('resume_polling');
        stopPolling();
        setErrorMessage(error instanceof Error ? error.message : 'Failed to poll entry status.');
        return false;
      }
    };

    const shouldContinuePolling = await poll();
    if (!shouldContinuePolling) {
      return;
    }

    pollTimerRef.current = window.setInterval(() => {
      void poll();
    }, 3000);
  }

  function handlePipelineError(error: unknown, step: PipelineStep, currentEntryId: string | null) {
    setPipelineState('error');

    if (error instanceof EntryApiError) {
      const withCode = error.errorCode ? `${error.message} (${error.errorCode})` : error.message;
      setErrorMessage(withCode);

      if (step === 'polling' && currentEntryId) {
        setRetryAction('resume_polling');
        return;
      }

      if (step === 'uploading' && error.retryable && currentEntryId) {
        setRetryAction('retry_upload');
        return;
      }

      if (error.retryable) {
        setRetryAction('restart_pipeline');
        return;
      }

      setRetryAction(null);
      return;
    }

    setErrorMessage(error instanceof Error ? error.message : 'Upload pipeline failed.');
    if (step === 'polling' && currentEntryId) {
      setRetryAction('resume_polling');
      return;
    }
    setRetryAction(null);
  }

  async function handleStartUpload(options?: { reuseCurrentEntry?: boolean }) {
    if (!selectedFile) {
      setErrorMessage('Pick an audio file first.');
      return;
    }

    stopPolling();
    setErrorMessage(null);
    setRetryAction(null);

    let currentEntryId = options?.reuseCurrentEntry ? entryId : null;
    let step: PipelineStep = 'creating';

    try {
      if (!currentEntryId) {
        setEntryId(null);
        setEntryStatus(null);
        setPipelineState('creating');
        resetTranscriptState();

        const created = await createEntry();
        currentEntryId = created.entryId;
        setEntryId(created.entryId);
        setEntryStatus(created.status);
      }
      if (!currentEntryId) {
        throw new Error('Entry creation did not return an id.');
      }

      step = 'uploading';
      setPipelineState('uploading');
      await uploadEntryAudio(currentEntryId, selectedFile);

      step = 'polling';
      setPipelineState('transcribing');
      await pollUntilComplete(currentEntryId);
    } catch (error) {
      handlePipelineError(error, step, currentEntryId);
    }
  }

  async function handleRetry() {
    if (!retryAction || isBusy) {
      return;
    }

    if (retryAction === 'restart_pipeline') {
      await handleStartUpload();
      return;
    }

    if (retryAction === 'retry_upload' && entryId) {
      await handleStartUpload({ reuseCurrentEntry: true });
      return;
    }

    if (retryAction === 'resume_polling' && entryId) {
      stopPolling();
      setErrorMessage(null);
      setRetryAction(null);
      setPipelineState('transcribing');
      await pollUntilComplete(entryId);
    }
  }

  function handleStartTranscriptEdit() {
    if (!entryTranscript) {
      return;
    }

    setTranscriptDraft(entryTranscript);
    setTranscriptError(null);
    setTranscriptNotice(null);
    setIsEditingTranscript(true);
  }

  function handleCancelTranscriptEdit() {
    setIsEditingTranscript(false);
    setTranscriptDraft(entryTranscript ?? '');
    setTranscriptError(null);
  }

  async function handleSaveTranscriptEdit() {
    if (!entryId) {
      setTranscriptError('Entry id is missing.');
      return;
    }

    const nextText = transcriptDraft.trim();
    if (!nextText) {
      setTranscriptError('Transcript cannot be empty.');
      return;
    }

    setIsSavingTranscript(true);
    setTranscriptError(null);
    setTranscriptNotice(null);

    try {
      const transcript = await updateEntryTranscript(entryId, nextText);
      setEntryTranscript(transcript.text);
      setTranscriptVersion(transcript.version);
      setIsTranscriptEdited(true);
      setIsEditingTranscript(false);
      setTranscriptDraft(transcript.text);
      const versionLabel = transcript.version !== null ? `version ${transcript.version}` : 'a new version';
      setTranscriptNotice(`Saved ${versionLabel}.`);
    } catch (error) {
      setTranscriptError(error instanceof Error ? error.message : 'Failed to save transcript edit.');
    } finally {
      setIsSavingTranscript(false);
    }
  }

  function handleSkipIndexing() {
    setIsIndexingModalOpen(false);
    resetIndexingDraft();
  }

  function handleSaveIndexing() {
    if (!entryId || !indexType || !indexContext) {
      return;
    }

    const next: EntryIndexingData = {
      type: indexType,
      context: indexContext,
      tags: parseTags(indexTagsInput)
    };

    setIndexingByEntry((previous) => {
      const updated = {
        ...previous,
        [entryId]: next
      };
      writeStoredIndexing(updated);
      return updated;
    });
    setTranscriptNotice('Saved quick indexing.');
    setIsIndexingModalOpen(false);
  }

  const isBusy = pipelineState === 'creating' || pipelineState === 'uploading' || pipelineState === 'transcribing';
  const currentIndexing = entryId ? indexingByEntry[entryId] : null;
  const retryLabel =
    retryAction === 'retry_upload'
      ? 'Retry upload'
      : retryAction === 'resume_polling'
        ? 'Retry status check'
        : retryAction === 'restart_pipeline'
          ? 'Restart pipeline'
          : null;

  return (
    <main className="min-h-screen bg-gradient-to-br from-sky-50 via-cyan-50 to-emerald-100 p-6">
      <section className="mx-auto mt-12 w-full max-w-2xl rounded-lg border bg-card p-8 text-card-foreground shadow-sm">
        <h1 className="text-2xl font-semibold tracking-tight">VoiceVault App</h1>
        <p className="mt-2 text-sm text-muted-foreground">Authenticated session is active.</p>
        <dl className="mt-6 rounded-md border bg-background p-4 text-sm">
          <div className="flex justify-between gap-3">
            <dt className="text-muted-foreground">Email</dt>
            <dd className="font-medium">{typeof user?.email === 'string' ? user.email : 'Unknown'}</dd>
          </div>
        </dl>
        <section className="mt-6 rounded-md border bg-background p-4">
          <h2 className="text-base font-semibold">Record audio</h2>
          <p className="mt-1 text-sm text-muted-foreground">Capture a voice note as a WebM audio blob.</p>

          <p className="mt-4 text-sm font-medium">
            Status:{' '}
            <span className={isRecording ? 'text-red-600' : 'text-muted-foreground'}>
              {isRecording ? 'Recording' : 'Idle'}
            </span>
          </p>
          <p className="text-sm text-muted-foreground">Timer: {formatDuration(elapsedSeconds)}</p>

          <div className="mt-4 flex flex-wrap gap-3">
            <Button onClick={handleStartRecording} disabled={isRecording}>
              Start recording
            </Button>
            <Button onClick={handleStopRecording} disabled={!isRecording} variant="outline">
              Stop recording
            </Button>
          </div>

          {recordingError !== null ? (
            <p className="mt-3 text-sm text-destructive" role="alert">
              {recordingError}
            </p>
          ) : null}

          {recordingUrl !== null ? (
            <div className="mt-4 space-y-2">
              <audio controls className="w-full" src={recordingUrl} />
              <p className="text-xs text-muted-foreground">
                Blob type: {recordingBlob?.type ?? 'audio/webm'} | Size: {recordingBlob?.size ?? 0} bytes
              </p>
            </div>
          ) : null}
        </section>
        <div className="mt-6 space-y-4 rounded-md border bg-background p-4">
          <h2 className="text-base font-semibold">Audio Upload Pipeline</h2>
          <p className="text-sm text-muted-foreground">Create entry, upload audio, then poll for transcription status.</p>
          <input
            type="file"
            accept="audio/*"
            onChange={handleAudioSelection}
            className="block w-full text-sm file:mr-3 file:rounded-md file:border file:border-input file:bg-background file:px-3 file:py-2 file:text-sm"
          />
          <div className="flex items-center gap-3">
            <Button onClick={handleStartUpload} disabled={!selectedFile || isBusy}>
              {pipelineState === 'creating' && 'Creating entry...'}
              {pipelineState === 'uploading' && 'Uploading audio...'}
              {pipelineState === 'transcribing' && 'Polling status...'}
              {(pipelineState === 'idle' || pipelineState === 'ready' || pipelineState === 'error') &&
                'Start upload pipeline'}
            </Button>
            {retryLabel ? (
              <Button onClick={handleRetry} disabled={isBusy} variant="outline">
                {retryLabel}
              </Button>
            ) : null}
            <span className="text-sm text-muted-foreground">
              State: <span className="font-medium text-foreground">{pipelineState}</span>
            </span>
          </div>
          {entryId ? (
            <div className="flex flex-wrap items-center gap-3">
              <p className="text-sm">
                Entry ID: <span className="font-mono">{entryId}</span>
              </p>
              <Button onClick={() => navigate(`/app/entries/${entryId}`)} size="sm" variant="outline">
                Open entry detail
              </Button>
            </div>
          ) : null}
          {entryStatus ? (
            <p className="text-sm">
              Entry status: <span className="font-medium">{entryStatus}</span>
            </p>
          ) : null}
          {errorMessage ? <p className="text-sm text-destructive">{errorMessage}</p> : null}
        </div>
        <div className="mt-6 space-y-4 rounded-md border bg-background p-4">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-base font-semibold">Transcript</h2>
            {transcriptVersion !== null ? (
              <span className="rounded-full bg-secondary px-2 py-1 text-xs font-medium text-secondary-foreground">
                Version {transcriptVersion}
              </span>
            ) : null}
            {isTranscriptEdited ? (
              <span className="rounded-full bg-emerald-100 px-2 py-1 text-xs font-medium text-emerald-800">Edited</span>
            ) : null}
          </div>

          {entryTranscript ? (
            isEditingTranscript ? (
              <div className="space-y-3">
                <textarea
                  className="min-h-44 w-full rounded-md border bg-background p-3 text-sm"
                  value={transcriptDraft}
                  onChange={(event) => setTranscriptDraft(event.target.value)}
                  disabled={isSavingTranscript}
                />
                <div className="flex flex-wrap gap-3">
                  <Button onClick={handleSaveTranscriptEdit} disabled={isSavingTranscript}>
                    {isSavingTranscript ? 'Saving...' : 'Save new version'}
                  </Button>
                  <Button onClick={handleCancelTranscriptEdit} disabled={isSavingTranscript} variant="outline">
                    Cancel
                  </Button>
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                <p className="whitespace-pre-wrap text-sm">{entryTranscript}</p>
                <Button onClick={handleStartTranscriptEdit} variant="outline">
                  Edit transcript
                </Button>
              </div>
            )
          ) : (
            <p className="text-sm text-muted-foreground">Transcript will appear after processing is complete.</p>
          )}

          {transcriptNotice ? <p className="text-sm text-emerald-700">{transcriptNotice}</p> : null}
          {transcriptError ? <p className="text-sm text-destructive">{transcriptError}</p> : null}
        </div>
        <div className="mt-6 space-y-4 rounded-md border bg-background p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-base font-semibold">Quick Indexing</h2>
            <Button
              onClick={() => {
                if (entryId) {
                  openIndexingModal(entryId);
                }
              }}
              disabled={!entryId}
              variant="outline"
            >
              {currentIndexing ? 'Edit indexing' : 'Index this entry'}
            </Button>
          </div>
          <p className="text-sm text-muted-foreground">Type, context, and tags to classify this entry in about 5 seconds.</p>
          {currentIndexing ? (
            <div className="space-y-2 text-sm">
              <p>
                Type: <span className="font-medium capitalize">{currentIndexing.type}</span>
              </p>
              <p>
                Context: <span className="font-medium capitalize">{currentIndexing.context}</span>
              </p>
              <p>
                Tags:{' '}
                <span className="font-medium">
                  {currentIndexing.tags.length > 0 ? currentIndexing.tags.map((tag) => `#${tag}`).join(', ') : 'None'}
                </span>
              </p>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No indexing saved for this entry yet.</p>
          )}
        </div>
        <div className="mt-6">
          <Button onClick={handleLogout} disabled={isLoggingOut} variant="outline">
            {isLoggingOut ? 'Logging out...' : 'Logout'}
          </Button>
        </div>
      </section>
      {isIndexingModalOpen && entryId ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4" role="presentation">
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="quick-indexing-title"
            className="w-full max-w-lg rounded-lg border bg-card p-6 text-card-foreground shadow-lg"
          >
            <h2 id="quick-indexing-title" className="text-lg font-semibold">
              Quick indexing
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">Classify this transcript in under 5 seconds.</p>

            <div className="mt-5">
              <p className="text-sm font-medium">Type</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {ENTRY_TYPE_OPTIONS.map((option) => {
                  const isSelected = indexType === option;
                  return (
                    <button
                      key={option}
                      type="button"
                      onClick={() => setIndexType(option)}
                      className={`rounded-md border px-3 py-2 text-sm capitalize transition ${
                        isSelected
                          ? 'border-emerald-600 bg-emerald-100 text-emerald-900'
                          : 'border-input bg-background hover:bg-accent hover:text-accent-foreground'
                      }`}
                    >
                      {option}
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="mt-5">
              <p className="text-sm font-medium">Context</p>
              <div className="mt-2 flex gap-2">
                {(['work', 'life'] as EntryContext[]).map((option) => {
                  const isSelected = indexContext === option;
                  return (
                    <button
                      key={option}
                      type="button"
                      onClick={() => setIndexContext(option)}
                      className={`rounded-md border px-3 py-2 text-sm capitalize transition ${
                        isSelected
                          ? 'border-cyan-600 bg-cyan-100 text-cyan-900'
                          : 'border-input bg-background hover:bg-accent hover:text-accent-foreground'
                      }`}
                    >
                      {option}
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="mt-5">
              <label htmlFor="index-tags" className="text-sm font-medium">
                Tags
              </label>
              <input
                id="index-tags"
                type="text"
                value={indexTagsInput}
                onChange={(event) => setIndexTagsInput(event.target.value)}
                placeholder="comma-separated tags"
                className="mt-2 w-full rounded-md border bg-background px-3 py-2 text-sm"
              />
            </div>

            <div className="mt-6 flex flex-wrap justify-end gap-2">
              <Button onClick={handleSkipIndexing} variant="outline">
                Skip for now
              </Button>
              <Button onClick={handleSaveIndexing} disabled={!indexType || !indexContext}>
                Save indexing
              </Button>
            </div>
          </section>
        </div>
      ) : null}
    </main>
  );
}
