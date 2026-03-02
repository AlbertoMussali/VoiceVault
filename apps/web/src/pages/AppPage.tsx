import { useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';

import { useAuth } from '@/auth/AuthProvider';
import { Button } from '@/components/ui/Button';
import { listBragDrafts, updateBragDraft, type BragDraft } from '@/lib/bragDrafts';
import {
  downloadBragExportArtifact,
  fetchBragExportJob,
  requestBragExportJob,
  type BragExportJob
} from '@/lib/bragExport';
import {
  EntryApiError,
  createEntry,
  fetchEntriesTimeline,
  fetchEntryDetail,
  fetchEntryStatus,
  searchEntries,
  type SearchResult,
  type TimelineEntry,
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

type TimelineEntryRecord = {
  entryId: string;
  status: string | null;
  title: string | null;
  createdAt: string | null;
  occurredAt: string | null;
  type: string | null;
  context: EntryContext | null;
  tags: string[];
  quote: string | null;
};

const READY_STATUSES = new Set(['ready', 'completed', 'done']);
const ERROR_STATUSES = new Set(['error', 'failed', 'fatal']);
const MAX_STATUS_POLLS = 40;
const SEARCH_RESULT_LIMIT = 12;
const INDEXING_STORAGE_KEY = 'voicevault.entry-indexing.v1';
const TIMELINE_STORAGE_KEY = 'voicevault.timeline.v1';
const ENTRY_TYPE_OPTIONS = ['win', 'blocker', 'idea', 'task', 'learning'];
const MAX_QUOTE_CHARS = 160;
const BRAG_BUCKETS = ['Impact', 'Execution', 'Leadership', 'Collaboration', 'Growth'] as const;
const BRAG_UNASSIGNED_BUCKET = 'Unassigned';
const BRAG_EXPORT_READY_STATUSES = new Set(['completed']);
const BRAG_EXPORT_FAILED_STATUSES = new Set(['failed', 'error']);

function normalizeQuote(raw: string | null | undefined): string | null {
  if (typeof raw !== 'string') {
    return null;
  }

  const normalized = raw.replace(/\s+/g, ' ').trim();
  if (!normalized) {
    return null;
  }

  if (normalized.length <= MAX_QUOTE_CHARS) {
    return normalized;
  }

  return `${normalized.slice(0, MAX_QUOTE_CHARS - 1).trimEnd()}…`;
}

function quoteFromTranscript(text: string | null | undefined): string | null {
  if (typeof text !== 'string') {
    return null;
  }

  const normalized = text.replace(/\s+/g, ' ').trim();
  if (!normalized) {
    return null;
  }

  const firstSentence = normalized.match(/.*?[.!?](?:\s|$)/)?.[0]?.trim();
  return normalizeQuote(firstSentence ?? normalized);
}

function isFallbackEntryTitle(title: string | null): boolean {
  if (!title) {
    return true;
  }
  return /^Entry [a-f0-9]{8}$/i.test(title.trim());
}

function getTimelineQuoteChipText(entry: TimelineEntryRecord): string {
  if (entry.quote) {
    return `"${entry.quote}"`;
  }

  if (entry.title && !isFallbackEntryTitle(entry.title)) {
    return `"${normalizeQuote(entry.title) ?? entry.title}"`;
  }

  return 'Receipt pending: open entry for transcript quote.';
}

function parseTags(rawTags: string): string[] {
  const next = rawTags
    .split(',')
    .map((tag) => tag.trim())
    .filter((tag) => tag.length > 0)
    .slice(0, 10);

  return Array.from(new Set(next));
}

function isKnownBragBucket(bucket: string | null): bucket is (typeof BRAG_BUCKETS)[number] {
  return typeof bucket === 'string' && BRAG_BUCKETS.includes(bucket as (typeof BRAG_BUCKETS)[number]);
}

function formatBragDate(raw: string): string {
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) {
    return 'Unknown date';
  }

  return date.toLocaleDateString();
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

function readStoredTimeline(): Record<string, TimelineEntryRecord> {
  if (typeof window === 'undefined') {
    return {};
  }

  const raw = window.localStorage.getItem(TIMELINE_STORAGE_KEY);
  if (!raw) {
    return {};
  }

  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return {};
    }

    const result: Record<string, TimelineEntryRecord> = {};
    for (const [entryId, value] of Object.entries(parsed)) {
      if (!value || typeof value !== 'object' || Array.isArray(value)) {
        continue;
      }

      const record = value as Record<string, unknown>;
      const context = record.context === 'work' || record.context === 'life' ? record.context : null;
      const type = typeof record.type === 'string' ? record.type.trim().toLowerCase() : null;
      const tags = Array.isArray(record.tags)
        ? record.tags
            .filter((tag): tag is string => typeof tag === 'string')
            .map((tag) => tag.trim().toLowerCase())
            .filter((tag) => tag.length > 0)
        : [];

      result[entryId] = {
        entryId,
        status: typeof record.status === 'string' ? record.status : null,
        title: typeof record.title === 'string' ? record.title : null,
        createdAt: typeof record.createdAt === 'string' ? record.createdAt : null,
        occurredAt: typeof record.occurredAt === 'string' ? record.occurredAt : null,
        type,
        context,
        tags: Array.from(new Set(tags)),
        quote: normalizeQuote(typeof record.quote === 'string' ? record.quote : null)
      };
    }

    return result;
  } catch {
    return {};
  }
}

function writeStoredTimeline(timelineByEntry: Record<string, TimelineEntryRecord>) {
  if (typeof window === 'undefined') {
    return;
  }

  window.localStorage.setItem(TIMELINE_STORAGE_KEY, JSON.stringify(timelineByEntry));
}

function normalizeIsoDate(raw: string | null): string | null {
  if (!raw) {
    return null;
  }

  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) {
    return null;
  }

  return date.toISOString();
}

function formatTimelineDate(raw: string | null): string {
  const iso = normalizeIsoDate(raw);
  if (!iso) {
    return 'Unknown date';
  }

  return new Date(iso).toLocaleString();
}

function mapTimelineEntry(entry: TimelineEntry): TimelineEntryRecord {
  const context = entry.context === 'work' || entry.context === 'life' ? entry.context : null;
  const type = entry.entryType ? entry.entryType.trim().toLowerCase() : null;
  return {
    entryId: entry.entryId,
    status: entry.status,
    title: entry.title,
    createdAt: normalizeIsoDate(entry.createdAt),
    occurredAt: normalizeIsoDate(entry.occurredAt),
    type,
    context,
    tags: Array.from(new Set(entry.tags.map((tag) => tag.trim().toLowerCase()).filter((tag) => tag.length > 0))),
    quote: normalizeQuote(entry.quote)
  };
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
  const [timelineByEntry, setTimelineByEntry] = useState<Record<string, TimelineEntryRecord>>(() => readStoredTimeline());
  const [isTimelineLoading, setIsTimelineLoading] = useState(true);
  const [timelineError, setTimelineError] = useState<string | null>(null);
  const [timelineDateFrom, setTimelineDateFrom] = useState('');
  const [timelineDateTo, setTimelineDateTo] = useState('');
  const [timelineTypeFilter, setTimelineTypeFilter] = useState<'all' | string>('all');
  const [timelineContextFilter, setTimelineContextFilter] = useState<'all' | EntryContext>('all');
  const [timelineTagFilterInput, setTimelineTagFilterInput] = useState('');
  const [bragDateFrom, setBragDateFrom] = useState('');
  const [bragDateTo, setBragDateTo] = useState('');
  const [bragDrafts, setBragDrafts] = useState<BragDraft[]>(() => listBragDrafts());
  const [expandedEvidenceByDraft, setExpandedEvidenceByDraft] = useState<Record<string, boolean>>({});
  const [bragExportJob, setBragExportJob] = useState<BragExportJob | null>(null);
  const [isRequestingBragExport, setIsRequestingBragExport] = useState(false);
  const [isCheckingBragExport, setIsCheckingBragExport] = useState(false);
  const [isDownloadingBragExport, setIsDownloadingBragExport] = useState(false);
  const [bragExportNotice, setBragExportNotice] = useState<string | null>(null);
  const [bragExportError, setBragExportError] = useState<string | null>(null);
  const [searchQueryInput, setSearchQueryInput] = useState('');
  const [submittedSearchQuery, setSubmittedSearchQuery] = useState('');
  const [isSearchLoading, setIsSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [isIndexingModalOpen, setIsIndexingModalOpen] = useState(false);
  const [indexType, setIndexType] = useState('');
  const [indexContext, setIndexContext] = useState<EntryContext | null>(null);
  const [indexTagsInput, setIndexTagsInput] = useState('');
  const pollTimerRef = useRef<number | null>(null);
  const bragExportPollTimerRef = useRef<number | null>(null);

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
      if (bragExportPollTimerRef.current !== null) {
        window.clearInterval(bragExportPollTimerRef.current);
      }
    },
    []
  );

  useEffect(() => {
    let cancelled = false;

    const loadTimeline = async () => {
      setIsTimelineLoading(true);
      setTimelineError(null);
      try {
        const entries = await fetchEntriesTimeline();
        if (cancelled) {
          return;
        }
        if (entries.length > 0) {
          setTimelineByEntry((previous) => {
            const next = { ...previous };
            for (const entry of entries) {
              const mapped = mapTimelineEntry(entry);
              const existing = next[mapped.entryId];
              next[mapped.entryId] = {
                ...mapped,
                type: existing?.type ?? mapped.type,
                context: existing?.context ?? mapped.context,
                tags: existing?.tags && existing.tags.length > 0 ? existing.tags : mapped.tags,
                quote: existing?.quote ?? mapped.quote
              };
            }
            writeStoredTimeline(next);
            return next;
          });
        }
      } catch (error) {
        if (!cancelled) {
          setTimelineError(error instanceof Error ? error.message : 'Failed to load timeline entries.');
        }
      } finally {
        if (!cancelled) {
          setIsTimelineLoading(false);
        }
      }
    };

    void loadTimeline();

    return () => {
      cancelled = true;
    };
  }, []);

  function stopPolling() {
    if (pollTimerRef.current !== null) {
      window.clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }

  function stopBragExportPolling() {
    if (bragExportPollTimerRef.current !== null) {
      window.clearInterval(bragExportPollTimerRef.current);
      bragExportPollTimerRef.current = null;
    }
  }

  function upsertTimelineEntry(entryId: string, patch: Partial<TimelineEntryRecord>) {
    setTimelineByEntry((previous) => {
      const existing = previous[entryId];
      const next: TimelineEntryRecord = {
        entryId,
        status: patch.status ?? existing?.status ?? null,
        title: patch.title ?? existing?.title ?? null,
        createdAt: patch.createdAt ?? existing?.createdAt ?? new Date().toISOString(),
        occurredAt: patch.occurredAt ?? existing?.occurredAt ?? null,
        type: patch.type ?? existing?.type ?? null,
        context: patch.context ?? existing?.context ?? null,
        tags: patch.tags ?? existing?.tags ?? [],
        quote: patch.quote ?? existing?.quote ?? null
      };
      const updated = { ...previous, [entryId]: next };
      writeStoredTimeline(updated);
      return updated;
    });
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
      upsertTimelineEntry(targetEntryId, { status: detail.status });
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
    upsertTimelineEntry(targetEntryId, { quote: quoteFromTranscript(detail.transcript.text) });
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

  async function handleSearchSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const query = searchQueryInput.trim();
    setSubmittedSearchQuery(query);
    setSearchError(null);

    if (!query) {
      setSearchResults([]);
      return;
    }

    setIsSearchLoading(true);
    try {
      const results = await searchEntries(query, SEARCH_RESULT_LIMIT);
      setSearchResults(results);
    } catch (error) {
      setSearchError(error instanceof Error ? error.message : 'Search failed.');
      setSearchResults([]);
    } finally {
      setIsSearchLoading(false);
    }
  }

  async function pollUntilComplete(nextEntryId: string) {
    let pollAttempts = 0;

    const poll = async (): Promise<boolean> => {
      try {
        pollAttempts += 1;
        const result = await fetchEntryStatus(nextEntryId);
        const normalized = normalizeStatus(result.status);
        setEntryStatus(result.status ?? 'processing');
        upsertTimelineEntry(nextEntryId, { status: result.status });

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
        upsertTimelineEntry(created.entryId, {
          status: created.status,
          title: created.title ?? null,
          createdAt: new Date().toISOString(),
          quote: null
        });
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
      upsertTimelineEntry(entryId, { quote: quoteFromTranscript(transcript.text) });
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

    const parsedTags = parseTags(indexTagsInput);
    const next: EntryIndexingData = {
      type: indexType,
      context: indexContext,
      tags: parsedTags
    };

    setIndexingByEntry((previous) => {
      const updated = {
        ...previous,
        [entryId]: next
      };
      writeStoredIndexing(updated);
      return updated;
    });
    upsertTimelineEntry(entryId, {
      type: indexType,
      context: indexContext,
      tags: parsedTags
    });
    setTranscriptNotice('Saved quick indexing.');
    setIsIndexingModalOpen(false);
  }

  function handleBragClaimTextChange(draftId: string, value: string) {
    setBragDrafts((previous) => previous.map((draft) => (draft.id === draftId ? { ...draft, claimText: value } : draft)));
    updateBragDraft(draftId, { claimText: value });
  }

  function handleBragBucketChange(draftId: string, bucketValue: string) {
    const nextBucket = bucketValue === '' ? null : bucketValue;
    setBragDrafts((previous) => previous.map((draft) => (draft.id === draftId ? { ...draft, bucket: nextBucket } : draft)));
    updateBragDraft(draftId, { bucket: nextBucket });
  }

  function toggleEvidenceList(draftId: string) {
    setExpandedEvidenceByDraft((previous) => ({
      ...previous,
      [draftId]: !previous[draftId]
    }));
  }

  async function loadBragExportStatus(jobId: string, options?: { showCheckingIndicator?: boolean }): Promise<BragExportJob | null> {
    const showCheckingIndicator = options?.showCheckingIndicator === true;

    if (showCheckingIndicator) {
      setIsCheckingBragExport(true);
    }

    try {
      const job = await fetchBragExportJob(jobId);
      setBragExportJob(job);

      const normalizedStatus = job.status.trim().toLowerCase();
      if (BRAG_EXPORT_READY_STATUSES.has(normalizedStatus)) {
        stopBragExportPolling();
        setBragExportNotice('Export is ready to download.');
        setBragExportError(null);
      } else if (BRAG_EXPORT_FAILED_STATUSES.has(normalizedStatus)) {
        stopBragExportPolling();
        setBragExportError(job.errorMessage ?? 'Export failed.');
      }

      return job;
    } catch (error) {
      setBragExportError(error instanceof Error ? error.message : 'Failed to fetch export status.');
      return null;
    } finally {
      if (showCheckingIndicator) {
        setIsCheckingBragExport(false);
      }
    }
  }

  function startBragExportPolling(jobId: string) {
    stopBragExportPolling();
    bragExportPollTimerRef.current = window.setInterval(() => {
      void loadBragExportStatus(jobId);
    }, 3000);
  }

  async function handleRequestBragExport() {
    if (isRequestingBragExport || isCheckingBragExport || isDownloadingBragExport) {
      return;
    }

    stopBragExportPolling();
    setIsRequestingBragExport(true);
    setBragExportNotice(null);
    setBragExportError(null);

    try {
      const job = await requestBragExportJob();
      setBragExportJob(job);

      const normalizedStatus = job.status.trim().toLowerCase();
      if (BRAG_EXPORT_READY_STATUSES.has(normalizedStatus)) {
        setBragExportNotice('Export is ready to download.');
        return;
      }

      if (BRAG_EXPORT_FAILED_STATUSES.has(normalizedStatus)) {
        setBragExportError(job.errorMessage ?? 'Export failed.');
        return;
      }

      setBragExportNotice('Export requested. Generating report now.');
      startBragExportPolling(job.id);
    } catch (error) {
      setBragExportError(error instanceof Error ? error.message : 'Failed to request export.');
    } finally {
      setIsRequestingBragExport(false);
    }
  }

  async function handleCheckBragExportStatus() {
    if (!bragExportJob || isCheckingBragExport || isRequestingBragExport || isDownloadingBragExport) {
      return;
    }

    setBragExportNotice(null);
    await loadBragExportStatus(bragExportJob.id, { showCheckingIndicator: true });
  }

  async function handleDownloadBragExport() {
    if (!bragExportJob || isDownloadingBragExport || isRequestingBragExport || isCheckingBragExport) {
      return;
    }

    setIsDownloadingBragExport(true);
    setBragExportError(null);

    try {
      const normalizedStatus = bragExportJob.status.trim().toLowerCase();
      const latest =
        BRAG_EXPORT_READY_STATUSES.has(normalizedStatus)
          ? bragExportJob
          : await loadBragExportStatus(bragExportJob.id, { showCheckingIndicator: false });

      if (!latest || !BRAG_EXPORT_READY_STATUSES.has(latest.status.trim().toLowerCase())) {
        setBragExportError('Export is not ready yet.');
        return;
      }

      await downloadBragExportArtifact(latest);
      setBragExportNotice('Download started.');
    } catch (error) {
      setBragExportError(error instanceof Error ? error.message : 'Failed to download export.');
    } finally {
      setIsDownloadingBragExport(false);
    }
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
  const timelineEntries = Object.values(timelineByEntry).sort((left, right) => {
    const leftRaw = left.occurredAt ?? left.createdAt;
    const rightRaw = right.occurredAt ?? right.createdAt;
    const leftTs = leftRaw ? Date.parse(leftRaw) : 0;
    const rightTs = rightRaw ? Date.parse(rightRaw) : 0;
    return rightTs - leftTs;
  });
  const typeFilterOptions = Array.from(
    new Set(timelineEntries.map((entry) => entry.type).filter((value): value is string => Boolean(value)))
  ).sort();
  const allKnownTags = Array.from(new Set(timelineEntries.flatMap((entry) => entry.tags))).sort();
  const tagFilters = timelineTagFilterInput
    .split(',')
    .map((tag) => tag.trim().toLowerCase())
    .filter((tag) => tag.length > 0);
  const filteredTimelineEntries = timelineEntries.filter((entry) => {
    const entryDateRaw = entry.occurredAt ?? entry.createdAt;
    const entryDate = entryDateRaw ? new Date(entryDateRaw) : null;
    const entryDay = entryDate && !Number.isNaN(entryDate.getTime()) ? entryDate.toISOString().slice(0, 10) : null;

    if (timelineDateFrom && (!entryDay || entryDay < timelineDateFrom)) {
      return false;
    }
    if (timelineDateTo && (!entryDay || entryDay > timelineDateTo)) {
      return false;
    }
    if (timelineTypeFilter !== 'all' && entry.type !== timelineTypeFilter) {
      return false;
    }
    if (timelineContextFilter !== 'all' && entry.context !== timelineContextFilter) {
      return false;
    }
    if (tagFilters.length > 0 && !tagFilters.every((tag) => entry.tags.includes(tag))) {
      return false;
    }

    return true;
  });
  const bragDraftsInRange = bragDrafts.filter((draft) => {
    const date = new Date(draft.createdAt);
    if (Number.isNaN(date.getTime())) {
      return false;
    }

    const day = date.toISOString().slice(0, 10);
    if (bragDateFrom && day < bragDateFrom) {
      return false;
    }
    if (bragDateTo && day > bragDateTo) {
      return false;
    }

    return true;
  });
  const bragDraftsByBucket = useMemo(() => {
    const byBucket: Record<string, BragDraft[]> = {};
    for (const bucket of BRAG_BUCKETS) {
      byBucket[bucket] = [];
    }
    byBucket[BRAG_UNASSIGNED_BUCKET] = [];

    for (const draft of bragDraftsInRange) {
      if (isKnownBragBucket(draft.bucket)) {
        byBucket[draft.bucket].push(draft);
        continue;
      }
      byBucket[BRAG_UNASSIGNED_BUCKET].push(draft);
    }

    return byBucket;
  }, [bragDraftsInRange]);
  const hasSearchAttempt = useMemo(() => submittedSearchQuery.length > 0, [submittedSearchQuery]);
  const bragExportStatusLabel = bragExportJob?.status ?? null;
  const isBragExportReady = BRAG_EXPORT_READY_STATUSES.has((bragExportJob?.status ?? '').trim().toLowerCase());

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
        <div className="mt-6 space-y-4 rounded-md border bg-background p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-base font-semibold">Search</h2>
            <span className="text-xs text-muted-foreground">Transcript search with highlight jump</span>
          </div>
          <form onSubmit={handleSearchSubmit} className="flex flex-col gap-3 sm:flex-row">
            <input
              type="search"
              value={searchQueryInput}
              onChange={(event) => setSearchQueryInput(event.target.value)}
              placeholder="Search transcripts..."
              className="h-10 w-full rounded-md border bg-background px-3 text-sm"
            />
            <Button type="submit" disabled={isSearchLoading} className="sm:w-auto">
              {isSearchLoading ? 'Searching...' : 'Search'}
            </Button>
          </form>
          {searchError ? <p className="text-sm text-destructive">{searchError}</p> : null}
          {isSearchLoading ? <p className="text-sm text-muted-foreground">Loading results...</p> : null}
          {searchResults.length > 0 ? (
            <div className="space-y-3">
              {searchResults.map((result, index) => (
                <article key={`${result.entryId}-${result.startChar}-${result.endChar}-${index}`} className="rounded-md border p-3">
                  <p className="text-xs text-muted-foreground">Entry: {result.entryId}</p>
                  <p className="mt-2 text-sm">{result.snippetText}</p>
                  <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
                    <p className="text-xs text-muted-foreground">
                      Match offsets: {result.startChar}-{result.endChar}
                    </p>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        const params = new URLSearchParams({
                          start: String(result.startChar),
                          end: String(result.endChar)
                        });
                        if (submittedSearchQuery) {
                          params.set('q', submittedSearchQuery);
                        }
                        navigate(`/app/entries/${result.entryId}?${params.toString()}`);
                      }}
                    >
                      Open highlight
                    </Button>
                  </div>
                </article>
              ))}
            </div>
          ) : null}
          {hasSearchAttempt && !isSearchLoading && !searchError && searchResults.length === 0 ? (
            <p className="text-sm text-muted-foreground">No matches found for "{submittedSearchQuery}".</p>
          ) : null}
        </div>
        <div className="mt-6 space-y-4 rounded-md border bg-background p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-base font-semibold">Brag Doc</h2>
            <span className="text-xs text-muted-foreground">{bragDraftsInRange.length} bullets in range</span>
          </div>
          <p className="text-sm text-muted-foreground">Select a date range and organize evidence-backed bullets by bucket.</p>
          <div className="grid gap-3 md:grid-cols-2">
            <label className="space-y-1 text-sm">
              <span className="font-medium">Date from</span>
              <input
                type="date"
                value={bragDateFrom}
                onChange={(event) => setBragDateFrom(event.target.value)}
                className="w-full rounded-md border bg-background px-3 py-2 text-sm"
              />
            </label>
            <label className="space-y-1 text-sm">
              <span className="font-medium">Date to</span>
              <input
                type="date"
                value={bragDateTo}
                onChange={(event) => setBragDateTo(event.target.value)}
                className="w-full rounded-md border bg-background px-3 py-2 text-sm"
              />
            </label>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setBragDateFrom('');
                setBragDateTo('');
              }}
            >
              Clear range
            </Button>
          </div>
          <div className="rounded-md border border-cyan-200 bg-cyan-50 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-sm font-semibold text-cyan-900">Export report</h3>
              {bragExportStatusLabel ? (
                <span className="rounded-full border border-cyan-300 bg-white px-2 py-1 text-xs font-medium text-cyan-900">
                  Status: {bragExportStatusLabel}
                </span>
              ) : null}
            </div>
            <p className="mt-1 text-xs text-cyan-900/80">Request a text export, check job status, and download when ready.</p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button onClick={handleRequestBragExport} disabled={isRequestingBragExport || isCheckingBragExport || isDownloadingBragExport}>
                {isRequestingBragExport ? 'Requesting export...' : 'Request export'}
              </Button>
              <Button
                onClick={handleCheckBragExportStatus}
                variant="outline"
                disabled={!bragExportJob || isRequestingBragExport || isCheckingBragExport || isDownloadingBragExport}
              >
                {isCheckingBragExport ? 'Checking status...' : 'Check status'}
              </Button>
              <Button
                onClick={handleDownloadBragExport}
                variant="outline"
                disabled={!bragExportJob || !isBragExportReady || isRequestingBragExport || isCheckingBragExport || isDownloadingBragExport}
              >
                {isDownloadingBragExport ? 'Downloading...' : 'Download export'}
              </Button>
            </div>
            {bragExportJob ? <p className="mt-2 text-xs text-cyan-900/80">Job ID: <span className="font-mono">{bragExportJob.id}</span></p> : null}
            {bragExportNotice ? <p className="mt-2 text-xs text-emerald-700">{bragExportNotice}</p> : null}
            {bragExportError ? <p className="mt-2 text-xs text-destructive">{bragExportError}</p> : null}
          </div>
          <div className="space-y-3">
            {[...BRAG_BUCKETS, BRAG_UNASSIGNED_BUCKET].map((bucket) => (
              <article key={bucket} className="rounded-md border p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="text-sm font-semibold">{bucket}</h3>
                  <span className="text-xs text-muted-foreground">{bragDraftsByBucket[bucket].length} bullets</span>
                </div>
                {bragDraftsByBucket[bucket].length === 0 ? (
                  <p className="mt-1 text-xs text-muted-foreground">No bullets yet. Add evidence from entries to start this bucket.</p>
                ) : (
                  <div className="mt-3 space-y-3">
                    {bragDraftsByBucket[bucket].map((draft) => {
                      const isEvidenceExpanded = Boolean(expandedEvidenceByDraft[draft.id]);
                      const evidenceCount = draft.evidence.length;
                      return (
                        <div key={draft.id} className="rounded border bg-muted/20 p-3">
                          <textarea
                            value={draft.claimText}
                            onChange={(event) => handleBragClaimTextChange(draft.id, event.target.value)}
                            placeholder="Write an impact claim..."
                            className="min-h-20 w-full rounded-md border bg-background p-2 text-sm"
                          />
                          <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
                            <span className="text-xs text-muted-foreground">
                              {evidenceCount} {evidenceCount === 1 ? 'source' : 'sources'}
                            </span>
                            <Button size="sm" variant="outline" onClick={() => toggleEvidenceList(draft.id)}>
                              {isEvidenceExpanded ? 'Hide evidence' : 'Show evidence'}
                            </Button>
                          </div>
                          <div className="mt-2">
                            <label className="space-y-1 text-xs">
                              <span className="font-medium">Bucket</span>
                              <select
                                value={isKnownBragBucket(draft.bucket) ? draft.bucket : ''}
                                onChange={(event) => handleBragBucketChange(draft.id, event.target.value)}
                                className="w-full rounded-md border bg-background px-2 py-1 text-sm"
                              >
                                <option value="">Unassigned</option>
                                {BRAG_BUCKETS.map((option) => (
                                  <option key={option} value={option}>
                                    {option}
                                  </option>
                                ))}
                              </select>
                            </label>
                          </div>
                          <p className="mt-2 text-xs text-muted-foreground">Added {formatBragDate(draft.createdAt)}</p>
                          {isEvidenceExpanded ? (
                            <div className="mt-2 space-y-2 rounded-md border bg-background p-2">
                              {draft.evidence.map((evidence, evidenceIndex) => (
                                <div key={`${draft.id}-${evidence.entryId}-${evidence.startChar}-${evidenceIndex}`} className="rounded border p-2">
                                  <p className="text-xs text-muted-foreground">
                                    Entry {evidence.entryId} | Transcript v{evidence.transcriptVersion ?? 'n/a'} | Chars {evidence.startChar}-
                                    {evidence.endChar}
                                  </p>
                                  <p className="mt-1 text-sm">{evidence.snippetText}</p>
                                </div>
                              ))}
                            </div>
                          ) : null}
                        </div>
                      );
                    })}
                  </div>
                )}
              </article>
            ))}
          </div>
        </div>
        <div className="mt-6 space-y-4 rounded-md border bg-background p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-base font-semibold">Timeline</h2>
            <span className="text-xs text-muted-foreground">
              Showing {filteredTimelineEntries.length} of {timelineEntries.length}
            </span>
          </div>
          <p className="text-sm text-muted-foreground">Filter by date range, type, context, and tags.</p>
          <div className="grid gap-3 md:grid-cols-2">
            <label className="space-y-1 text-sm">
              <span className="font-medium">Date from</span>
              <input
                type="date"
                value={timelineDateFrom}
                onChange={(event) => setTimelineDateFrom(event.target.value)}
                className="w-full rounded-md border bg-background px-3 py-2 text-sm"
              />
            </label>
            <label className="space-y-1 text-sm">
              <span className="font-medium">Date to</span>
              <input
                type="date"
                value={timelineDateTo}
                onChange={(event) => setTimelineDateTo(event.target.value)}
                className="w-full rounded-md border bg-background px-3 py-2 text-sm"
              />
            </label>
            <label className="space-y-1 text-sm">
              <span className="font-medium">Type</span>
              <select
                value={timelineTypeFilter}
                onChange={(event) => setTimelineTypeFilter(event.target.value)}
                className="w-full rounded-md border bg-background px-3 py-2 text-sm capitalize"
              >
                <option value="all">All types</option>
                {typeFilterOptions.map((option) => (
                  <option key={option} value={option} className="capitalize">
                    {option}
                  </option>
                ))}
              </select>
            </label>
            <label className="space-y-1 text-sm">
              <span className="font-medium">Context</span>
              <select
                value={timelineContextFilter}
                onChange={(event) => setTimelineContextFilter(event.target.value as 'all' | EntryContext)}
                className="w-full rounded-md border bg-background px-3 py-2 text-sm capitalize"
              >
                <option value="all">All contexts</option>
                <option value="work">Work</option>
                <option value="life">Life</option>
              </select>
            </label>
          </div>
          <label className="space-y-1 text-sm">
            <span className="font-medium">Tags (comma-separated)</span>
            <input
              type="text"
              value={timelineTagFilterInput}
              onChange={(event) => setTimelineTagFilterInput(event.target.value)}
              placeholder="e.g. project-x, retro"
              className="w-full rounded-md border bg-background px-3 py-2 text-sm"
            />
            {allKnownTags.length > 0 ? (
              <span className="block text-xs text-muted-foreground">Known tags: {allKnownTags.map((tag) => `#${tag}`).join(', ')}</span>
            ) : null}
          </label>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                setTimelineDateFrom('');
                setTimelineDateTo('');
                setTimelineTypeFilter('all');
                setTimelineContextFilter('all');
                setTimelineTagFilterInput('');
              }}
            >
              Clear filters
            </Button>
          </div>
          {isTimelineLoading ? <p className="text-sm text-muted-foreground">Loading timeline...</p> : null}
          {timelineError ? <p className="text-sm text-destructive">{timelineError}</p> : null}
          {filteredTimelineEntries.length > 0 ? (
            <div className="space-y-3">
              {filteredTimelineEntries.map((entry) => (
                <article key={entry.entryId} className="rounded-md border p-3">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <h3 className="text-sm font-semibold">{entry.title ?? 'Untitled entry'}</h3>
                      <p className="text-xs text-muted-foreground">{formatTimelineDate(entry.occurredAt ?? entry.createdAt)}</p>
                    </div>
                    <Button size="sm" variant="outline" onClick={() => navigate(`/app/entries/${entry.entryId}`)}>
                      Open
                    </Button>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-2 text-xs">
                    <span className="rounded-full bg-secondary px-2 py-1">Status: {entry.status ?? 'unknown'}</span>
                    {entry.type ? <span className="rounded-full bg-emerald-100 px-2 py-1 text-emerald-900 capitalize">{entry.type}</span> : null}
                    {entry.context ? <span className="rounded-full bg-cyan-100 px-2 py-1 text-cyan-900 capitalize">{entry.context}</span> : null}
                    {entry.tags.map((tag) => (
                      <span key={`${entry.entryId}-${tag}`} className="rounded-full bg-slate-100 px-2 py-1 text-slate-700">
                        #{tag}
                      </span>
                    ))}
                  </div>
                  <p className="mt-2 rounded-md border border-cyan-200 bg-cyan-50 px-2 py-1 text-xs italic text-cyan-900">
                    {getTimelineQuoteChipText(entry)}
                  </p>
                  <p className="mt-2 truncate font-mono text-xs text-muted-foreground">{entry.entryId}</p>
                </article>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No entries match the selected filters yet.</p>
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
