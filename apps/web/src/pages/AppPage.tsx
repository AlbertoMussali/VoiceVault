import { useEffect, useRef, useState, type ChangeEvent } from 'react';
import { useNavigate } from 'react-router-dom';

import { useAuth } from '@/auth/AuthProvider';
import { Button } from '@/components/ui/Button';
import { EntryApiError, createEntry, fetchEntryStatus, uploadEntryAudio } from '@/lib/entries';

type PipelineState = 'idle' | 'creating' | 'uploading' | 'transcribing' | 'ready' | 'error';
type RetryAction = 'restart_pipeline' | 'retry_upload' | 'resume_polling' | null;
type PipelineStep = 'creating' | 'uploading' | 'polling';

const READY_STATUSES = new Set(['ready', 'completed', 'done']);
const ERROR_STATUSES = new Set(['error', 'failed', 'fatal']);
const MAX_STATUS_POLLS = 40;

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

  const isBusy = pipelineState === 'creating' || pipelineState === 'uploading' || pipelineState === 'transcribing';
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
            <p className="text-sm">
              Entry ID: <span className="font-mono">{entryId}</span>
            </p>
          ) : null}
          {entryStatus ? (
            <p className="text-sm">
              Entry status: <span className="font-medium">{entryStatus}</span>
            </p>
          ) : null}
          {errorMessage ? <p className="text-sm text-destructive">{errorMessage}</p> : null}
        </div>
        <div className="mt-6">
          <Button onClick={handleLogout} disabled={isLoggingOut} variant="outline">
            {isLoggingOut ? 'Logging out...' : 'Logout'}
          </Button>
        </div>
      </section>
    </main>
  );
}
