import { useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import { useAuth } from '@/auth/AuthProvider';
import { AskSummaryRenderer, type AskSummaryCitation, type AskSummarySentence } from '@/components/AskSummaryRenderer';
import { Button } from '@/components/ui/Button';
import { deleteAccount } from '@/lib/account';
import { summarizeAskQuery, submitAskQuery, type AskSource, type AskSummaryResult } from '@/lib/ask';
import { listBragDrafts, updateBragDraft, type BragDraft } from '@/lib/bragDrafts';
import {
  downloadBragExportArtifact,
  fetchBragExportJob,
  requestBragExportJob,
  type BragExportJob
} from '@/lib/bragExport';
import {
  downloadDataExportArtifact,
  fetchDataExportJob,
  requestDataExportJob,
  type DataExportJob
} from '@/lib/dataExport';
import {
  EntryApiError,
  archiveEntry,
  createEntry,
  fetchEntriesTimeline,
  fetchEntryDetail,
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
type TimelineWindow = 'all' | '1y' | '6m' | '1m' | '1w' | '1d';

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
  durationSeconds: number | null;
  type: string | null;
  context: EntryContext | null;
  tags: string[];
  quote: string | null;
  sentimentLabel: string | null;
  sentimentScore: number | null;
};

type AskPreviewOptions = {
  redact: boolean;
  mask: boolean;
  includeSensitive: boolean;
};

const READY_STATUSES = new Set(['ready', 'completed', 'done']);
const ERROR_STATUSES = new Set(['error', 'failed', 'fatal']);
const MAX_STATUS_POLLS = 40;
const SEARCH_RESULT_LIMIT = 12;
const INDEXING_STORAGE_KEY = 'voicevault.entry-indexing.v1';
const TIMELINE_STORAGE_KEY = 'voicevault.timeline.v1';
const ONBOARDING_DISMISSED_STORAGE_KEY = 'voicevault.onboarding.dismissed.v1';
const ENTRY_TYPE_OPTIONS = ['win', 'blocker', 'idea', 'task', 'learning'];
const ASK_TEMPLATES = [
  { label: 'Wins this week', query: 'What wins did I mention this week?' },
  { label: 'Blockers', query: 'What blockers keep coming up?' },
  { label: 'Commitments', query: 'What did I say I would do next?' },
  { label: 'Themes', query: 'What themes are repeating lately?' }
] as const;
const DEFAULT_ASK_TEMPLATE_QUERY = ASK_TEMPLATES[0].query;
const MAX_QUOTE_CHARS = 160;
const BRAG_BUCKETS = ['Impact', 'Execution', 'Leadership', 'Collaboration', 'Growth'] as const;
const BRAG_UNASSIGNED_BUCKET = 'Unassigned';
const BRAG_EXPORT_READY_STATUSES = new Set(['completed']);
const BRAG_EXPORT_FAILED_STATUSES = new Set(['failed', 'error']);
const DATA_EXPORT_READY_STATUSES = new Set(['completed', 'ready', 'done']);
const DATA_EXPORT_FAILED_STATUSES = new Set(['failed', 'error', 'fatal']);
const SENSITIVE_TAG_TOKENS = new Set(['sensitive', 'work_sensitive', 'work-sensitive', 'confidential', 'private', 'raw_only', 'raw-only']);
const ASK_SUMMARY_SENTENCE_LIMIT = 5;
const DELETE_ACCOUNT_CONFIRMATION_TEXT = 'DELETE MY ACCOUNT';
const TIMELINE_WINDOW_OPTIONS: TimelineWindow[] = ['all', '1y', '6m', '1m', '1w', '1d'];

function isSensitiveTimelineEntry(entry: TimelineEntryRecord | undefined): boolean {
  if (!entry) {
    return false;
  }

  return entry.tags.some((tag) => SENSITIVE_TAG_TOKENS.has(tag.trim().toLowerCase()));
}

function applyPreviewTransforms(text: string, options: AskPreviewOptions): string {
  let next = text;

  if (options.redact) {
    next = next
      .replace(/\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi, '[REDACTED_EMAIL]')
      .replace(/\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)?\d{3}[-.\s]?\d{4}\b/g, '[REDACTED_PHONE]');
  }

  if (options.mask) {
    next = next
      .replace(/\b\d{9,}\b/g, (value) => `***${value.slice(-4)}`)
      .replace(/\b\d{4}[- ]\d{4}[- ]\d{4}[- ]\d{4}\b/g, '[MASKED_CARD]')
      .replace(/https?:\/\/[^\s]+/gi, '[MASKED_URL]');
  }

  return next;
}

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

function normalizeSnippetKey(text: string): string {
  return text
    .replace(/\s+/g, ' ')
    .replace(/[^\w\s]/g, '')
    .trim()
    .toLowerCase();
}

function inferIndexingGuess(params: {
  transcriptText?: string | null;
  title?: string | null;
  existingTags?: string[];
  sentimentLabel?: string | null;
}): EntryIndexingData {
  const text = `${params.title ?? ''} ${params.transcriptText ?? ''}`.toLowerCase();
  const keywordTagMap: Record<string, string> = {
    incident: 'incident',
    outage: 'incident',
    bug: 'debugging',
    deploy: 'shipping',
    shipped: 'shipping',
    customer: 'customer',
    retro: 'retro',
    planning: 'planning',
    onboarding: 'onboarding',
    quality: 'quality',
    search: 'search'
  };

  const hasAny = (tokens: string[]) => tokens.some((token) => text.includes(token));
  const type: string = hasAny(['incident', 'blocker', 'blocked', 'issue', 'error', 'bug', 'outage'])
    ? 'blocker'
    : hasAny(['learned', 'lesson', 'realized', 'insight'])
      ? 'learning'
      : hasAny(['idea', 'proposal', 'experiment', 'hypothesis'])
        ? 'idea'
        : hasAny(['todo', 'task', 'follow up', 'next step'])
          ? 'task'
          : hasAny(['shipped', 'completed', 'win', 'success', 'delivered'])
            ? 'win'
            : params.sentimentLabel === 'negative'
              ? 'blocker'
              : params.sentimentLabel === 'positive'
                ? 'win'
                : 'task';

  const context: EntryContext = hasAny(['family', 'health', 'home', 'personal', 'life']) ? 'life' : 'work';

  const guessedTags = Object.entries(keywordTagMap)
    .filter(([keyword]) => text.includes(keyword))
    .map(([, tag]) => tag);
  const mergedTags = Array.from(new Set([...(params.existingTags ?? []), ...guessedTags])).slice(0, 8);

  return { type, context, tags: mergedTags };
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
        durationSeconds:
          typeof record.durationSeconds === 'number' && Number.isFinite(record.durationSeconds)
            ? record.durationSeconds
            : typeof record.durationSeconds === 'string'
              ? Number.parseFloat(record.durationSeconds) || null
              : null,
        type,
        context,
        tags: Array.from(new Set(tags)),
        quote: normalizeQuote(typeof record.quote === 'string' ? record.quote : null),
        sentimentLabel: typeof record.sentimentLabel === 'string' ? record.sentimentLabel : null,
        sentimentScore:
          typeof record.sentimentScore === 'number' && Number.isFinite(record.sentimentScore)
            ? record.sentimentScore
            : null
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

function readOnboardingDismissed(): boolean {
  if (typeof window === 'undefined') {
    return false;
  }

  return window.localStorage.getItem(ONBOARDING_DISMISSED_STORAGE_KEY) === '1';
}

function writeOnboardingDismissed(isDismissed: boolean) {
  if (typeof window === 'undefined') {
    return;
  }

  if (isDismissed) {
    window.localStorage.setItem(ONBOARDING_DISMISSED_STORAGE_KEY, '1');
    return;
  }

  window.localStorage.removeItem(ONBOARDING_DISMISSED_STORAGE_KEY);
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
    durationSeconds: typeof entry.durationSeconds === 'number' && Number.isFinite(entry.durationSeconds) ? entry.durationSeconds : null,
    type,
    context,
    tags: Array.from(new Set(entry.tags.map((tag) => tag.trim().toLowerCase()).filter((tag) => tag.length > 0))),
    quote: normalizeQuote(entry.quote),
    sentimentLabel: entry.sentimentLabel ? entry.sentimentLabel.trim().toLowerCase() : null,
    sentimentScore: typeof entry.sentimentScore === 'number' && Number.isFinite(entry.sentimentScore) ? entry.sentimentScore : null
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

function formatDurationMinutes(totalSeconds: number): string {
  if (totalSeconds < 60) {
    return `${Math.round(totalSeconds)}s`;
  }

  const minutes = totalSeconds / 60;
  return `${minutes.toFixed(minutes >= 10 ? 0 : 1)}m`;
}

function buildSplinePath(points: Array<{ x: number; y: number }>): string {
  if (points.length === 0) {
    return '';
  }
  if (points.length === 1) {
    const first = points[0];
    return `M ${first.x} ${first.y}`;
  }

  const commands = [`M ${points[0].x} ${points[0].y}`];

  for (let index = 0; index < points.length - 1; index += 1) {
    const p0 = points[index - 1] ?? points[index];
    const p1 = points[index];
    const p2 = points[index + 1];
    const p3 = points[index + 2] ?? p2;

    const cp1x = p1.x + (p2.x - p0.x) / 6;
    const cp1y = p1.y + (p2.y - p0.y) / 6;
    const cp2x = p2.x - (p3.x - p1.x) / 6;
    const cp2y = p2.y - (p3.y - p1.y) / 6;

    commands.push(`C ${cp1x} ${cp1y}, ${cp2x} ${cp2y}, ${p2.x} ${p2.y}`);
  }

  return commands.join(' ');
}

function resolveTimelineWindowStart(window: TimelineWindow, now = new Date()): string | null {
  if (window === 'all') {
    return null;
  }

  const start = new Date(now);
  if (window === '1y') {
    start.setFullYear(start.getFullYear() - 1);
  } else if (window === '6m') {
    start.setMonth(start.getMonth() - 6);
  } else if (window === '1m') {
    start.setMonth(start.getMonth() - 1);
  } else if (window === '1w') {
    start.setDate(start.getDate() - 7);
  } else if (window === '1d') {
    start.setDate(start.getDate() - 1);
  }

  return start.toISOString().slice(0, 10);
}

function hashTextToUnit(value: string): number {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 31 + value.charCodeAt(index)) >>> 0;
  }

  return (hash % 1000) / 1000;
}

function inferDemoDurationSeconds(entry: TimelineEntryRecord): number {
  const baseByType: Record<string, number> = {
    win: 420,
    blocker: 720,
    idea: 300,
    task: 360,
    learning: 540
  };
  const byType = entry.type ? baseByType[entry.type] : undefined;
  const base = typeof byType === 'number' ? byType : 480;
  const variation = Math.round(hashTextToUnit(entry.entryId) * 180) - 90;
  return Math.max(90, base + variation);
}

function selectSupportedMediaRecorderMimeType(): string | undefined {
  if (typeof window === 'undefined' || !window.MediaRecorder || typeof window.MediaRecorder.isTypeSupported !== 'function') {
    return undefined;
  }

  const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4', 'audio/ogg;codecs=opus'];
  for (const candidate of candidates) {
    if (window.MediaRecorder.isTypeSupported(candidate)) {
      return candidate;
    }
  }

  return undefined;
}

function mapRecordingStartError(error: unknown): string {
  const fallback = 'Microphone access failed.';

  if (!(error instanceof Error)) {
    return fallback;
  }

  if (error instanceof DOMException) {
    if (error.name === 'NotAllowedError') {
      return 'Microphone access was blocked. Allow microphone permissions and try again.';
    }
    if (error.name === 'NotFoundError') {
      return 'No microphone was found on this device.';
    }
    if (error.name === 'NotReadableError') {
      return 'Microphone is already in use by another app.';
    }
    if (error.name === 'NotSupportedError') {
      return 'This browser cannot start audio recording with the selected format.';
    }
    if (error.name === 'SecurityError') {
      return 'Recording requires a secure context (localhost or HTTPS).';
    }
  }

  if (error.message.toLowerCase().includes('object can not be found')) {
    return 'This browser cannot start audio recording with the selected format.';
  }

  return error.message || fallback;
}

function resolveRecordingFileExtension(mimeType: string | null | undefined): string {
  if (!mimeType) {
    return 'webm';
  }

  const normalized = mimeType.toLowerCase();
  if (normalized.includes('mp4') || normalized.includes('m4a')) {
    return 'm4a';
  }
  if (normalized.includes('ogg')) {
    return 'ogg';
  }
  if (normalized.includes('wav')) {
    return 'wav';
  }

  return 'webm';
}

export function AppPage() {
  const { user, logoutCurrentUser } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const isSettingsPage = location.pathname === '/app/settings';

  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [recordingUrl, setRecordingUrl] = useState<string | null>(null);
  const [recordingBlob, setRecordingBlob] = useState<Blob | null>(null);
  const [recordingError, setRecordingError] = useState<string | null>(null);

  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const recordingMimeTypeRef = useRef<string | null>(null);
  const timerRef = useRef<number | null>(null);
  const startedAtRef = useRef<number | null>(null);
  const uploadSectionRef = useRef<HTMLElement | null>(null);
  const askSectionRef = useRef<HTMLElement | null>(null);
  const timelineSectionRef = useRef<HTMLElement | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [pipelineState, setPipelineState] = useState<PipelineState>('idle');
  const [entryId, setEntryId] = useState<string | null>(null);
  const [entryStatus, setEntryStatus] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [retryAction, setRetryAction] = useState<RetryAction>(null);
  const [entryTranscript, setEntryTranscript] = useState<string | null>(null);
  const [captureTranscriptPreview, setCaptureTranscriptPreview] = useState('');
  const [captureTranscriptFinalized, setCaptureTranscriptFinalized] = useState(false);
  const [transcriptVersion, setTranscriptVersion] = useState<number | null>(null);
  const [isTranscriptEdited, setIsTranscriptEdited] = useState(false);
  const [isEditingTranscript, setIsEditingTranscript] = useState(false);
  const [transcriptDraft, setTranscriptDraft] = useState('');
  const [isSavingTranscript, setIsSavingTranscript] = useState(false);
  const [isArchivingEntry, setIsArchivingEntry] = useState(false);
  const [archiveError, setArchiveError] = useState<string | null>(null);
  const [archiveNotice, setArchiveNotice] = useState<string | null>(null);
  const [transcriptError, setTranscriptError] = useState<string | null>(null);
  const [transcriptNotice, setTranscriptNotice] = useState<string | null>(null);
  const [indexingByEntry, setIndexingByEntry] = useState<Record<string, EntryIndexingData>>(() => readStoredIndexing());
  const [timelineByEntry, setTimelineByEntry] = useState<Record<string, TimelineEntryRecord>>(() => readStoredTimeline());
  const [onboardingDismissed, setOnboardingDismissed] = useState<boolean>(() => readOnboardingDismissed());
  const [isTimelineLoading, setIsTimelineLoading] = useState(true);
  const [timelineError, setTimelineError] = useState<string | null>(null);
  const [timelineWindow, setTimelineWindow] = useState<TimelineWindow>('1m');
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
  const [dataExportJob, setDataExportJob] = useState<DataExportJob | null>(null);
  const [isRequestingDataExport, setIsRequestingDataExport] = useState(false);
  const [isCheckingDataExport, setIsCheckingDataExport] = useState(false);
  const [isDownloadingDataExport, setIsDownloadingDataExport] = useState(false);
  const [dataExportNotice, setDataExportNotice] = useState<string | null>(null);
  const [dataExportError, setDataExportError] = useState<string | null>(null);
  const [deleteAccountPassword, setDeleteAccountPassword] = useState('');
  const [deleteAccountConfirmationInput, setDeleteAccountConfirmationInput] = useState('');
  const [deleteAccountAcknowledge, setDeleteAccountAcknowledge] = useState(false);
  const [isDeletingAccount, setIsDeletingAccount] = useState(false);
  const [deleteAccountNotice, setDeleteAccountNotice] = useState<string | null>(null);
  const [deleteAccountError, setDeleteAccountError] = useState<string | null>(null);
  const [searchQueryInput, setSearchQueryInput] = useState('');
  const [submittedSearchQuery, setSubmittedSearchQuery] = useState('');
  const [isSearchLoading, setIsSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [askQueryInput, setAskQueryInput] = useState<string>(DEFAULT_ASK_TEMPLATE_QUERY);
  const [submittedAskQuery, setSubmittedAskQuery] = useState('');
  const [askDateFrom, setAskDateFrom] = useState('');
  const [askDateTo, setAskDateTo] = useState('');
  const [isAskLoading, setIsAskLoading] = useState(false);
  const [askError, setAskError] = useState<string | null>(null);
  const [askSources, setAskSources] = useState<AskSource[]>([]);
  const [askQueryId, setAskQueryId] = useState<string | null>(null);
  const [askSummarySentences, setAskSummarySentences] = useState<AskSummarySentence[]>([]);
  const [askSummaryError, setAskSummaryError] = useState<string | null>(null);
  const [askQuestionInput, setAskQuestionInput] = useState('');
  const hasAutoStartedAskRef = useRef(false);
  const [isWhatGetsSentModalOpen, setIsWhatGetsSentModalOpen] = useState(false);
  const [askPreviewOptions, setAskPreviewOptions] = useState<AskPreviewOptions>({
    redact: true,
    mask: true,
    includeSensitive: false
  });
  const [isIndexingModalOpen, setIsIndexingModalOpen] = useState(false);
  const [indexType, setIndexType] = useState('');
  const [indexContext, setIndexContext] = useState<EntryContext | null>(null);
  const [indexTagsInput, setIndexTagsInput] = useState('');
  const pollTimerRef = useRef<number | null>(null);
  const transcriptStreamTimerRef = useRef<number | null>(null);
  const bragExportPollTimerRef = useRef<number | null>(null);
  const dataExportPollTimerRef = useRef<number | null>(null);
  const indexingModalEntryRef = useRef<string | null>(null);

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
      if (transcriptStreamTimerRef.current !== null) {
        window.clearInterval(transcriptStreamTimerRef.current);
      }
      if (bragExportPollTimerRef.current !== null) {
        window.clearInterval(bragExportPollTimerRef.current);
      }
      if (dataExportPollTimerRef.current !== null) {
        window.clearInterval(dataExportPollTimerRef.current);
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
                type: mapped.type ?? existing?.type ?? null,
                context: mapped.context ?? existing?.context ?? null,
                tags: mapped.tags.length > 0 ? mapped.tags : existing?.tags ?? [],
                quote: mapped.quote ?? existing?.quote ?? null,
                sentimentLabel: mapped.sentimentLabel ?? existing?.sentimentLabel ?? null,
                sentimentScore: mapped.sentimentScore ?? existing?.sentimentScore ?? null
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

  useEffect(() => {
    if (!submittedSearchQuery) {
      return;
    }

    setAskQuestionInput((previous) => (previous.trim().length > 0 ? previous : submittedSearchQuery));
  }, [submittedSearchQuery]);

  useEffect(() => {
    if (isSettingsPage || hasAutoStartedAskRef.current) {
      return;
    }
    hasAutoStartedAskRef.current = true;
    setSubmittedAskQuery(DEFAULT_ASK_TEMPLATE_QUERY);
    void runAskFlow(DEFAULT_ASK_TEMPLATE_QUERY);
  }, [isSettingsPage]);

  useEffect(() => {
    writeOnboardingDismissed(onboardingDismissed);
  }, [onboardingDismissed]);

  function stopPolling() {
    if (pollTimerRef.current !== null) {
      window.clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }

  function stopTranscriptStreaming() {
    if (transcriptStreamTimerRef.current !== null) {
      window.clearInterval(transcriptStreamTimerRef.current);
      transcriptStreamTimerRef.current = null;
    }
  }

  function streamTranscriptPreview(targetText: string, options?: { finalize?: boolean }): Promise<void> {
    const normalized = targetText.trim();
    if (!normalized) {
      setCaptureTranscriptPreview('');
      setCaptureTranscriptFinalized(options?.finalize === true);
      return Promise.resolve();
    }

    stopTranscriptStreaming();

    return new Promise((resolve) => {
      transcriptStreamTimerRef.current = window.setInterval(() => {
        setCaptureTranscriptPreview((previous) => {
          const nextStart = previous.length;
          if (nextStart >= normalized.length) {
            stopTranscriptStreaming();
            if (options?.finalize === true) {
              setCaptureTranscriptFinalized(true);
            }
            resolve();
            return normalized;
          }

          const remaining = normalized.length - nextStart;
          const step = Math.max(3, Math.min(remaining, Math.ceil(normalized.length / 48)));
          const next = normalized.slice(0, nextStart + step);
          if (next.length >= normalized.length) {
            stopTranscriptStreaming();
            if (options?.finalize === true) {
              setCaptureTranscriptFinalized(true);
            }
            resolve();
            return normalized;
          }

          return next;
        });
      }, 36);
    });
  }

  function stopBragExportPolling() {
    if (bragExportPollTimerRef.current !== null) {
      window.clearInterval(bragExportPollTimerRef.current);
      bragExportPollTimerRef.current = null;
    }
  }

  function stopDataExportPolling() {
    if (dataExportPollTimerRef.current !== null) {
      window.clearInterval(dataExportPollTimerRef.current);
      dataExportPollTimerRef.current = null;
    }
  }

  function scrollToSection(sectionRef: { current: HTMLElement | null }, focusSelector?: string) {
    sectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    if (!focusSelector) {
      return;
    }

    const target = sectionRef.current?.querySelector<HTMLElement>(focusSelector);
    target?.focus();
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
        durationSeconds: patch.durationSeconds ?? existing?.durationSeconds ?? null,
        type: patch.type ?? existing?.type ?? null,
        context: patch.context ?? existing?.context ?? null,
        tags: patch.tags ?? existing?.tags ?? [],
        quote: patch.quote ?? existing?.quote ?? null,
        sentimentLabel: patch.sentimentLabel ?? existing?.sentimentLabel ?? null,
        sentimentScore: patch.sentimentScore ?? existing?.sentimentScore ?? null
      };
      const updated = { ...previous, [entryId]: next };
      writeStoredTimeline(updated);
      return updated;
    });
  }

  function resetTranscriptState() {
    stopTranscriptStreaming();
    setEntryTranscript(null);
    setCaptureTranscriptPreview('');
    setCaptureTranscriptFinalized(false);
    setTranscriptVersion(null);
    setIsTranscriptEdited(false);
    setIsEditingTranscript(false);
    setTranscriptDraft('');
    setTranscriptError(null);
    setTranscriptNotice(null);
    setArchiveError(null);
    setArchiveNotice(null);
  }

  async function refreshEntryDetail(targetEntryId: string) {
    const detail = await fetchEntryDetail(targetEntryId);
    if (detail.status) {
      setEntryStatus(detail.status);
    }

    upsertTimelineEntry(targetEntryId, {
      status: detail.status,
      title: detail.title ?? null,
      type: detail.entryType ? detail.entryType.trim().toLowerCase() : null,
      context: detail.context === 'work' || detail.context === 'life' ? detail.context : null,
      tags: Array.from(new Set(detail.tags.map((tag) => tag.trim().toLowerCase()).filter((tag) => tag.length > 0))),
      sentimentLabel: detail.sentimentLabel ? detail.sentimentLabel.trim().toLowerCase() : null,
      sentimentScore: detail.sentimentScore
    });

    if (!detail.transcript) {
      return detail;
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
    return detail;
  }

  function resetIndexingDraft() {
    setIndexType('');
    setIndexContext(null);
    setIndexTagsInput('');
  }

  function openIndexingModal(targetEntryId: string) {
    indexingModalEntryRef.current = targetEntryId;
    const existing = indexingByEntry[targetEntryId];
    if (existing) {
      setIndexType(existing.type);
      setIndexContext(existing.context);
      setIndexTagsInput(existing.tags.join(', '));
    } else {
      const timelineEntry = timelineByEntry[targetEntryId];
      const guess = inferIndexingGuess({
        title: timelineEntry?.title ?? null,
        transcriptText: timelineEntry?.quote ?? null,
        existingTags: timelineEntry?.tags ?? [],
        sentimentLabel: timelineEntry?.sentimentLabel ?? null
      });
      setIndexType(guess.type);
      setIndexContext(guess.context);
      setIndexTagsInput(guess.tags.join(', '));
    }

    setIsIndexingModalOpen(true);

    if (existing) {
      return;
    }

    void (async () => {
      try {
        const detail = await fetchEntryDetail(targetEntryId);
        if (indexingModalEntryRef.current !== targetEntryId) {
          return;
        }
        const refinedGuess = inferIndexingGuess({
          title: detail.title,
          transcriptText: detail.transcriptText,
          existingTags: detail.tags,
          sentimentLabel: detail.sentimentLabel
        });
        setIndexType((previous) => (previous ? previous : refinedGuess.type));
        setIndexContext((previous) => (previous ? previous : refinedGuess.context));
        setIndexTagsInput((previous) => (previous.trim().length > 0 ? previous : refinedGuess.tags.join(', ')));
      } catch {
        // Best-effort refinement only; fallback guesses already shown.
      }
    })();
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
      if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== 'function') {
        setRecordingError('Microphone APIs are unavailable in this browser.');
        return;
      }

      const userMediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = userMediaStream;

      const preferredMimeType = selectSupportedMediaRecorderMimeType();
      const recorder =
        preferredMimeType !== undefined ? new MediaRecorder(userMediaStream, { mimeType: preferredMimeType }) : new MediaRecorder(userMediaStream);

      chunksRef.current = [];
      recorderRef.current = recorder;
      recordingMimeTypeRef.current = recorder.mimeType || preferredMimeType || null;
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

        const blobType = recorder.mimeType || recordingMimeTypeRef.current || undefined;
        const blob = new Blob(chunksRef.current, blobType ? { type: blobType } : undefined);
        setRecordingBlob(blob);
        setRecordingUrl((previousUrl) => {
          if (previousUrl !== null) {
            URL.revokeObjectURL(previousUrl);
          }
          return URL.createObjectURL(blob);
        });
        const extension = resolveRecordingFileExtension(blob.type || recordingMimeTypeRef.current);
        const safeTimestamp = new Date().toISOString().replace(/:/g, '-');
        const recordingFile = new File([blob], `capture-${safeTimestamp}.${extension}`, {
          type: blob.type || 'audio/webm'
        });
        setSelectedFile(recordingFile);
        void runUploadPipeline(recordingFile);

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
      setRecordingError(mapRecordingStartError(error));
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

  async function runAskFlow(query: string) {
    setIsAskLoading(true);
    setAskError(null);
    setAskSummaryError(null);
    try {
      const askQuery = await submitAskQuery(query, SEARCH_RESULT_LIMIT);
      setAskQueryId(askQuery.queryId);
      setAskSources(askQuery.sources);
      setAskSummarySentences([]);
    } catch (error) {
      setAskError(error instanceof Error ? error.message : 'Ask retrieval failed.');
      setAskSources([]);
      setAskQueryId(null);
      setAskSummarySentences([]);
    } finally {
      setIsAskLoading(false);
    }
  }

  function mapAskSummary(summary: AskSummaryResult): AskSummarySentence[] {
    return summary.sentences.slice(0, ASK_SUMMARY_SENTENCE_LIMIT).map((sentence, index) => ({
      id: `${summary.queryId}-${index}`,
      text: sentence.text,
      citationSnippetIds: sentence.snippetIds
    }));
  }

  async function handleAskSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const query = askQueryInput.trim();
    setSubmittedAskQuery(query);

    if (!query) {
      setAskSources([]);
      setAskQueryId(null);
      setAskSummarySentences([]);
      setAskError(null);
      setAskSummaryError(null);
      return;
    }

    await runAskFlow(query);
  }

  function applyAskTemplate(templateQuery: string) {
    setAskQueryInput(templateQuery);
    setSubmittedAskQuery(templateQuery);
    void (async () => {
      await runAskFlow(templateQuery);
    })();
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
        const detail = await fetchEntryDetail(nextEntryId);
        const normalized = normalizeStatus(detail.status);
        const detailStatus = detail.status ?? 'processing';
        setEntryStatus(detailStatus);
        upsertTimelineEntry(nextEntryId, {
          status: detail.status,
          title: detail.title ?? null,
          type: detail.entryType ? detail.entryType.trim().toLowerCase() : null,
          context: detail.context === 'work' || detail.context === 'life' ? detail.context : null,
          tags: Array.from(new Set(detail.tags.map((tag) => tag.trim().toLowerCase()).filter((tag) => tag.length > 0))),
          sentimentLabel: detail.sentimentLabel ? detail.sentimentLabel.trim().toLowerCase() : null,
          sentimentScore: detail.sentimentScore
        });

        const latestTranscriptText = detail.transcript?.text ?? detail.transcriptText;
        if (typeof latestTranscriptText === 'string' && latestTranscriptText.trim().length > 0) {
          setEntryTranscript(latestTranscriptText);
          void streamTranscriptPreview(latestTranscriptText, { finalize: false });
        }

        if (READY_STATUSES.has(normalized)) {
          setRetryAction(null);
          stopPolling();
          const refreshed = await refreshEntryDetail(nextEntryId);
          const finalTranscript = refreshed.transcript?.text ?? refreshed.transcriptText;
          if (typeof finalTranscript === 'string' && finalTranscript.trim().length > 0) {
            setCaptureTranscriptFinalized(false);
            await streamTranscriptPreview(finalTranscript, { finalize: true });
          } else {
            setCaptureTranscriptFinalized(true);
          }
          setPipelineState('ready');
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

  async function runUploadPipeline(audioFile: File, options?: { reuseCurrentEntry?: boolean }) {
    stopPolling();
    setErrorMessage(null);
    setRetryAction(null);
    setArchiveError(null);
    setArchiveNotice(null);
    setCaptureTranscriptFinalized(false);
    setCaptureTranscriptPreview('');
    stopTranscriptStreaming();

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
      await uploadEntryAudio(currentEntryId, audioFile);

      step = 'polling';
      setPipelineState('transcribing');
      await pollUntilComplete(currentEntryId);
    } catch (error) {
      handlePipelineError(error, step, currentEntryId);
    }
  }

  async function handleStartUpload(options?: { reuseCurrentEntry?: boolean }) {
    if (!selectedFile) {
      setErrorMessage('Pick an audio file first.');
      return;
    }

    await runUploadPipeline(selectedFile, options);
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

  async function handleArchiveEntry() {
    if (!entryId || isArchivingEntry) {
      return;
    }

    setIsArchivingEntry(true);
    setArchiveError(null);
    setArchiveNotice(null);

    try {
      const archived = await archiveEntry(entryId);
      setEntryStatus(archived.status);
      upsertTimelineEntry(entryId, { status: archived.status });
      setArchiveNotice('Entry archived.');
    } catch (error) {
      setArchiveError(error instanceof Error ? error.message : 'Failed to archive entry.');
    } finally {
      setIsArchivingEntry(false);
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
      setCaptureTranscriptPreview(transcript.text);
      setCaptureTranscriptFinalized(true);
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
    indexingModalEntryRef.current = null;
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
    indexingModalEntryRef.current = null;
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

  async function loadDataExportStatus(jobId: string, options?: { showCheckingIndicator?: boolean }): Promise<DataExportJob | null> {
    const showCheckingIndicator = options?.showCheckingIndicator === true;

    if (showCheckingIndicator) {
      setIsCheckingDataExport(true);
    }

    try {
      const job = await fetchDataExportJob(jobId);
      setDataExportJob(job);

      const normalizedStatus = job.status.trim().toLowerCase();
      if (DATA_EXPORT_READY_STATUSES.has(normalizedStatus)) {
        stopDataExportPolling();
        setDataExportNotice('Data export is ready to download.');
        setDataExportError(null);
      } else if (DATA_EXPORT_FAILED_STATUSES.has(normalizedStatus)) {
        stopDataExportPolling();
        setDataExportError(job.errorMessage ?? 'Data export failed.');
      }

      return job;
    } catch (error) {
      setDataExportError(error instanceof Error ? error.message : 'Failed to fetch data export status.');
      return null;
    } finally {
      if (showCheckingIndicator) {
        setIsCheckingDataExport(false);
      }
    }
  }

  function startDataExportPolling(jobId: string) {
    stopDataExportPolling();
    dataExportPollTimerRef.current = window.setInterval(() => {
      void loadDataExportStatus(jobId);
    }, 3000);
  }

  async function handleRequestDataExport() {
    if (isRequestingDataExport || isCheckingDataExport || isDownloadingDataExport) {
      return;
    }

    stopDataExportPolling();
    setIsRequestingDataExport(true);
    setDataExportNotice(null);
    setDataExportError(null);

    try {
      const job = await requestDataExportJob();
      setDataExportJob(job);

      const normalizedStatus = job.status.trim().toLowerCase();
      if (DATA_EXPORT_READY_STATUSES.has(normalizedStatus)) {
        setDataExportNotice('Data export is ready to download.');
        return;
      }

      if (DATA_EXPORT_FAILED_STATUSES.has(normalizedStatus)) {
        setDataExportError(job.errorMessage ?? 'Data export failed.');
        return;
      }

      setDataExportNotice('Data export requested. Generating archive now.');
      startDataExportPolling(job.id);
    } catch (error) {
      setDataExportError(error instanceof Error ? error.message : 'Failed to request data export.');
    } finally {
      setIsRequestingDataExport(false);
    }
  }

  async function handleCheckDataExportStatus() {
    if (!dataExportJob || isCheckingDataExport || isRequestingDataExport || isDownloadingDataExport) {
      return;
    }

    setDataExportNotice(null);
    await loadDataExportStatus(dataExportJob.id, { showCheckingIndicator: true });
  }

  async function handleDownloadDataExport() {
    if (!dataExportJob || isDownloadingDataExport || isRequestingDataExport || isCheckingDataExport) {
      return;
    }

    setIsDownloadingDataExport(true);
    setDataExportError(null);

    try {
      const normalizedStatus = dataExportJob.status.trim().toLowerCase();
      const latest =
        DATA_EXPORT_READY_STATUSES.has(normalizedStatus)
          ? dataExportJob
          : await loadDataExportStatus(dataExportJob.id, { showCheckingIndicator: false });

      if (!latest || !DATA_EXPORT_READY_STATUSES.has(latest.status.trim().toLowerCase())) {
        setDataExportError('Data export is not ready yet.');
        return;
      }

      await downloadDataExportArtifact(latest);
      setDataExportNotice('Download started.');
    } catch (error) {
      setDataExportError(error instanceof Error ? error.message : 'Failed to download data export.');
    } finally {
      setIsDownloadingDataExport(false);
    }
  }

  async function handleDeleteAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    const password = deleteAccountPassword.trim();
    const confirmation = deleteAccountConfirmationInput.trim();
    if (!password) {
      setDeleteAccountError('Enter your password to confirm account deletion.');
      setDeleteAccountNotice(null);
      return;
    }
    if (confirmation !== DELETE_ACCOUNT_CONFIRMATION_TEXT) {
      setDeleteAccountError(`Type "${DELETE_ACCOUNT_CONFIRMATION_TEXT}" exactly to continue.`);
      setDeleteAccountNotice(null);
      return;
    }
    if (!deleteAccountAcknowledge) {
      setDeleteAccountError('Confirm that you understand this action is permanent.');
      setDeleteAccountNotice(null);
      return;
    }

    setIsDeletingAccount(true);
    setDeleteAccountError(null);
    setDeleteAccountNotice(null);
    try {
      await deleteAccount({ password });
      setDeleteAccountNotice('Account deleted. Redirecting to login.');
      await logoutCurrentUser();
      navigate('/login', { replace: true });
    } catch (error) {
      setDeleteAccountError(error instanceof Error ? error.message : 'Failed to delete account.');
    } finally {
      setIsDeletingAccount(false);
    }
  }

  const isBusy = pipelineState === 'creating' || pipelineState === 'uploading' || pipelineState === 'transcribing';
  const isCaptureBuffering = pipelineState === 'creating' || pipelineState === 'uploading' || pipelineState === 'transcribing';
  const normalizedEntryStatus = normalizeStatus(entryStatus);
  const isEntryArchived = normalizedEntryStatus === 'archived';
  const canArchiveEntry = Boolean(entryId) && pipelineState === 'ready' && !isEntryArchived;
  const captureStatusLabel = isRecording
    ? 'Recording'
    : pipelineState === 'creating'
      ? 'Creating entry'
      : pipelineState === 'uploading'
        ? 'Uploading audio'
        : pipelineState === 'transcribing'
          ? 'Transcribing'
          : pipelineState === 'ready'
            ? isEntryArchived
              ? 'Archived'
              : 'Ready'
            : pipelineState === 'error'
              ? 'Error'
              : 'Idle';
  const currentTimelineEntry = entryId ? timelineByEntry[entryId] : null;
  const currentIndexing = entryId
    ? indexingByEntry[entryId] ?? (currentTimelineEntry?.type && currentTimelineEntry?.context
      ? {
          type: currentTimelineEntry.type,
          context: currentTimelineEntry.context,
          tags: currentTimelineEntry.tags
        }
      : null)
    : null;
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
  const timelineRangeFrom = resolveTimelineWindowStart(timelineWindow);
  const timelineRangeTo = new Date().toISOString().slice(0, 10);
  const tagFilters = timelineTagFilterInput
    .split(',')
    .map((tag) => tag.trim().toLowerCase())
    .filter((tag) => tag.length > 0);
  const filteredTimelineEntries = timelineEntries.filter((entry) => {
    const entryDateRaw = entry.occurredAt ?? entry.createdAt;
    const entryDate = entryDateRaw ? new Date(entryDateRaw) : null;
    const entryDay = entryDate && !Number.isNaN(entryDate.getTime()) ? entryDate.toISOString().slice(0, 10) : null;

    if (timelineRangeFrom && (!entryDay || entryDay < timelineRangeFrom)) {
      return false;
    }
    if (timelineRangeTo && (!entryDay || entryDay > timelineRangeTo)) {
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
  const hasAnyTimelineEntries = timelineEntries.length > 0;
  const isDemoAccount = typeof user?.email === 'string' && user.email.toLowerCase().includes('demo');
  const timelineChartEntries = filteredTimelineEntries
    .map((entry) => {
      const rawDate = entry.occurredAt ?? entry.createdAt;
      if (!rawDate) {
        return null;
      }
      const resolvedDuration =
        typeof entry.durationSeconds === 'number' && Number.isFinite(entry.durationSeconds)
          ? entry.durationSeconds
          : isDemoAccount
            ? inferDemoDurationSeconds(entry)
            : null;
      if (resolvedDuration === null) {
        return null;
      }

      const ts = Date.parse(rawDate);
      if (!Number.isFinite(ts)) {
        return null;
      }

      return {
        entryId: entry.entryId,
        title: entry.title ?? 'Untitled entry',
        timestamp: ts,
        durationSeconds: Math.max(0, resolvedDuration)
      };
    })
    .filter(
      (
        entry
      ): entry is {
        entryId: string;
        title: string;
        timestamp: number;
        durationSeconds: number;
      } => Boolean(entry)
    )
    .sort((left, right) => left.timestamp - right.timestamp);
  const chartWidth = 980;
  const chartHeight = 360;
  const chartPadding = { top: 24, right: 28, bottom: 54, left: 64 };
  const maxDurationSeconds = Math.max(60, ...timelineChartEntries.map((entry) => entry.durationSeconds));
  const minTimelineTs = timelineChartEntries[0]?.timestamp ?? 0;
  const maxTimelineTs = timelineChartEntries[timelineChartEntries.length - 1]?.timestamp ?? minTimelineTs;
  const yTicks = [0, 1, 2, 3, 4].map((tick) => Math.round((maxDurationSeconds * tick) / 4));
  const xTickCount = Math.min(5, timelineChartEntries.length);
  const xTickIndexes = xTickCount <= 1
    ? [0]
    : Array.from({ length: xTickCount }, (_, index) =>
        Math.round((index * (timelineChartEntries.length - 1)) / (xTickCount - 1))
      );
  const chartPoints = timelineChartEntries.map((entry, index) => {
    const dateRange = Math.max(1, maxTimelineTs - minTimelineTs);
    const x =
      timelineChartEntries.length === 1
        ? (chartWidth - chartPadding.left - chartPadding.right) / 2 + chartPadding.left
        : chartPadding.left + ((entry.timestamp - minTimelineTs) / dateRange) * (chartWidth - chartPadding.left - chartPadding.right);
    const y =
      chartHeight -
      chartPadding.bottom -
      (entry.durationSeconds / maxDurationSeconds) * (chartHeight - chartPadding.top - chartPadding.bottom);
    return {
      ...entry,
      index,
      x,
      y,
      dateLabel: new Date(entry.timestamp).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
    };
  });
  const chartPath = buildSplinePath(chartPoints.map((point) => ({ x: point.x, y: point.y })));
  const latestTimelineEntryId = entryId ?? timelineEntries[0]?.entryId ?? null;
  const hasReadyTimelineEntry = timelineEntries.some((entry) => READY_STATUSES.has(normalizeStatus(entry.status)));
  const hasIndexedTimelineEntry = timelineEntries.some(
    (entry) => Boolean(entry.type) || Boolean(entry.context) || entry.tags.length > 0
  );
  const hasKnowledgeAttempt =
    submittedSearchQuery.trim().length > 0 || submittedAskQuery.trim().length > 0 || searchResults.length > 0 || askSources.length > 0;
  const onboardingSteps = [
    {
      key: 'capture',
      label: 'Capture your first note',
      detail: 'Upload audio to create your first entry and transcript.',
      done: hasAnyTimelineEntries
    },
    {
      key: 'process',
      label: 'Confirm processing',
      detail: 'Wait for at least one entry to reach ready status.',
      done: hasReadyTimelineEntry
    },
    {
      key: 'index',
      label: 'Add quick indexing',
      detail: 'Set type, context, and tags so retrieval has structure.',
      done: hasIndexedTimelineEntry
    },
    {
      key: 'ask',
      label: 'Run your first query',
      detail: 'Use Ask or Search to verify retrieval and highlights.',
      done: hasKnowledgeAttempt
    }
  ] as const;
  const onboardingCompletedCount = onboardingSteps.filter((step) => step.done).length;
  const shouldShowOnboardingCard = !onboardingDismissed && onboardingCompletedCount < onboardingSteps.length;
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
  const hasAskAttempt = useMemo(() => submittedAskQuery.length > 0, [submittedAskQuery]);
  const filteredAskSources = useMemo(() => {
    const dateFiltered = askSources.filter((source) => {
        const timelineEntry = timelineByEntry[source.entryId];
        if (!timelineEntry) {
          return !askDateFrom && !askDateTo;
        }

        const sourceDateRaw = timelineEntry.occurredAt ?? timelineEntry.createdAt;
        const sourceDate = sourceDateRaw ? new Date(sourceDateRaw) : null;
        const sourceDay = sourceDate && !Number.isNaN(sourceDate.getTime()) ? sourceDate.toISOString().slice(0, 10) : null;

        if (askDateFrom && (!sourceDay || sourceDay < askDateFrom)) {
          return false;
        }

        if (askDateTo && (!sourceDay || sourceDay > askDateTo)) {
          return false;
        }

        return true;
      });

    const seen = new Set<string>();
    return dateFiltered.filter((source) => {
      const key = normalizeSnippetKey(source.snippetText);
      if (!key) {
        return true;
      }
      if (seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    });
  }, [askDateFrom, askDateTo, askSources, timelineByEntry]);
  const filteredAskSnippetIds = useMemo(() => filteredAskSources.map((source) => source.snippetId), [filteredAskSources]);
  useEffect(() => {
    if (!askQueryId) {
      return;
    }

    if (filteredAskSnippetIds.length === 0) {
      setAskSummarySentences([]);
      setAskSummaryError(null);
      return;
    }

    let cancelled = false;
    setAskSummaryError(null);
    void (async () => {
      try {
        const summary = await summarizeAskQuery(askQueryId, filteredAskSnippetIds);
        if (cancelled) {
          return;
        }
        setAskSummarySentences(mapAskSummary(summary));
      } catch (error) {
        if (cancelled) {
          return;
        }
        setAskSummarySentences([]);
        setAskSummaryError(error instanceof Error ? error.message : 'Ask summary generation failed.');
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [askQueryId, filteredAskSnippetIds]);
  const askSourcesForPreview = useMemo(() => {
    return filteredAskSources
      .map((result) => {
        const timelineEntry = timelineByEntry[result.entryId];
        const occurredAt = normalizeIsoDate(timelineEntry?.occurredAt ?? timelineEntry?.createdAt ?? null);
        return {
          result,
          snippetId: result.snippetId,
          occurredAt,
          isSensitive: isSensitiveTimelineEntry(timelineEntry)
        };
      })
      .filter((item) => askPreviewOptions.includeSensitive || !item.isSensitive)
      .map((item) => ({
        snippet_id: item.snippetId,
        entry_id: item.result.entryId,
        transcript_id: item.result.transcriptId,
        start_char: item.result.startChar,
        end_char: item.result.endChar,
        occurred_at: item.occurredAt,
        is_sensitive: item.isSensitive,
        snippet_text: applyPreviewTransforms(item.result.snippetText, askPreviewOptions)
      }));
  }, [askPreviewOptions, filteredAskSources, timelineByEntry]);
  const askPreviewPayload = useMemo(() => {
    const query = askQuestionInput.trim() || submittedAskQuery || submittedSearchQuery;
    return {
      query_id: askQueryId,
      query,
      date_range: {
        from: askDateFrom || null,
        to: askDateTo || null
      },
      controls: {
        redact: askPreviewOptions.redact,
        mask: askPreviewOptions.mask,
        include_sensitive: askPreviewOptions.includeSensitive
      },
      snippet_count: askSourcesForPreview.length,
      snippets: askSourcesForPreview
    };
  }, [askDateFrom, askDateTo, askPreviewOptions, askQueryId, askQuestionInput, askSourcesForPreview, submittedAskQuery, submittedSearchQuery]);
  const askPreviewPayloadJson = useMemo(() => JSON.stringify(askPreviewPayload, null, 2), [askPreviewPayload]);
  const askSummaryCitations = useMemo<AskSummaryCitation[]>(
    () =>
      askSources.map((source) => ({
        snippetId: source.snippetId,
        entryId: source.entryId,
        startChar: source.startChar,
        endChar: source.endChar,
        snippetText: source.snippetText
      })),
    [askSources]
  );
  const askSummaryCitationsBySnippetId = useMemo<Record<string, AskSummaryCitation>>(
    () =>
      askSummaryCitations.reduce<Record<string, AskSummaryCitation>>((accumulator, citation) => {
        accumulator[citation.snippetId] = citation;
        return accumulator;
      }, {}),
    [askSummaryCitations]
  );
  const bragExportStatusLabel = bragExportJob?.status ?? null;
  const isBragExportReady = BRAG_EXPORT_READY_STATUSES.has((bragExportJob?.status ?? '').trim().toLowerCase());
  const dataExportStatusLabel = dataExportJob?.status ?? null;
  const isDataExportReady = DATA_EXPORT_READY_STATUSES.has((dataExportJob?.status ?? '').trim().toLowerCase());
  const isDeleteAccountConfirmationValid = deleteAccountConfirmationInput.trim() === DELETE_ACCOUNT_CONFIRMATION_TEXT;
  const canSubmitDeleteAccount =
    deleteAccountPassword.trim().length > 0 &&
    isDeleteAccountConfirmationValid &&
    deleteAccountAcknowledge &&
    !isDeletingAccount;

  function openAskCitation(citation: AskSummaryCitation) {
    const params = new URLSearchParams({
      start: String(citation.startChar),
      end: String(citation.endChar)
    });
    const askQuery = submittedAskQuery || askQuestionInput.trim();
    if (askQuery) {
      params.set('q', askQuery);
    }
    navigate(`/app/entries/${citation.entryId}?${params.toString()}`);
  }

  return (
    <main className="min-h-screen mono-page">
      <section className="mono-shell">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="mono-kicker">Personal Voice Archive</p>
            <h1 className="mt-2 font-display text-6xl leading-none tracking-[-0.05em] md:text-8xl lg:text-9xl">
              {isSettingsPage ? 'SETTINGS' : 'VAULT'}
            </h1>
            <p className="mt-5 max-w-3xl text-lg leading-relaxed text-muted-foreground">
              {isSettingsPage
                ? 'Manage exports, privacy controls, and secondary workspace tools.'
                : 'Authenticated session is active. Capture, classify, and retrieve evidence-backed transcripts.'}
            </p>
          </div>
          <div className="flex items-center gap-2 self-start">
            {!shouldShowOnboardingCard && onboardingCompletedCount < onboardingSteps.length ? (
              <Button variant="outline" size="sm" onClick={() => setOnboardingDismissed(false)}>
                Show setup guide
              </Button>
            ) : null}
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                navigate(isSettingsPage ? '/app' : '/app/settings');
              }}
              aria-label={isSettingsPage ? 'Go to home view' : 'Open settings'}
              title={isSettingsPage ? 'Home' : 'Settings'}
            >
              {isSettingsPage ? (
                'Back to home'
              ) : (
                <>
                  <svg aria-hidden="true" className="mr-2 h-4 w-4" viewBox="0 0 24 24" fill="none">
                    <path
                      d="M10.3 2.9H13.7L14.2 5.1C14.7 5.3 15.2 5.5 15.6 5.8L17.7 4.8L20.1 7.2L19.1 9.3C19.4 9.7 19.6 10.2 19.8 10.7L22 11.2V14.6L19.8 15.1C19.6 15.6 19.4 16.1 19.1 16.5L20.1 18.6L17.7 21L15.6 20C15.2 20.3 14.7 20.5 14.2 20.7L13.7 22.9H10.3L9.8 20.7C9.3 20.5 8.8 20.3 8.4 20L6.3 21L3.9 18.6L4.9 16.5C4.6 16.1 4.4 15.6 4.2 15.1L2 14.6V11.2L4.2 10.7C4.4 10.2 4.6 9.7 4.9 9.3L3.9 7.2L6.3 4.8L8.4 5.8C8.8 5.5 9.3 5.3 9.8 5.1L10.3 2.9Z"
                      stroke="currentColor"
                      strokeWidth="1.5"
                    />
                    <circle cx="12" cy="12.9" r="3.2" stroke="currentColor" strokeWidth="1.5" />
                  </svg>
                  Settings
                </>
              )}
            </Button>
          </div>
        </div>
        {isSettingsPage ? (
          <>
            <dl className="mt-8 border border-foreground bg-background p-4 text-sm">
          <div className="flex flex-wrap justify-between gap-3">
            <dt className="mono-kicker">Email</dt>
            <dd className="font-medium">{typeof user?.email === 'string' ? user.email : 'Unknown'}</dd>
          </div>
            </dl>
            <section className="mono-invert relative mt-8 grid gap-4 p-6 md:grid-cols-3 md:p-8">
          <article className="relative z-10 border border-background p-4">
            <p className="mono-kicker text-background/80">Onboarding</p>
            <p className="mt-2 font-display text-5xl leading-none">{onboardingCompletedCount}</p>
            <p className="mt-2 text-xs uppercase tracking-[0.1em] text-background/80">steps complete</p>
          </article>
          <article className="relative z-10 border border-background p-4">
            <p className="mono-kicker text-background/80">Timeline</p>
            <p className="mt-2 font-display text-5xl leading-none">{timelineEntries.length}</p>
            <p className="mt-2 text-xs uppercase tracking-[0.1em] text-background/80">entries indexed</p>
          </article>
          <article className="relative z-10 border border-background p-4">
            <p className="mono-kicker text-background/80">Pipeline</p>
            <p className="mt-2 font-display text-5xl leading-none">{pipelineState.toUpperCase()}</p>
            <p className="mt-2 text-xs uppercase tracking-[0.1em] text-background/80">current state</p>
          </article>
            </section>
            <div className="mono-rule" />
            {shouldShowOnboardingCard ? (
              <section className="mono-section mt-6 bg-muted">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 className="text-4xl font-semibold leading-none tracking-tight">Guided setup</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  {onboardingCompletedCount} of {onboardingSteps.length} steps complete.
                </p>
              </div>
              <Button variant="outline" size="sm" onClick={() => setOnboardingDismissed(true)}>
                Hide
              </Button>
            </div>
            <div className="mt-4 grid gap-2">
              {onboardingSteps.map((step, index) => (
                <div
                  key={step.key}
                  className={`border border-foreground px-3 py-3 text-sm ${step.done ? 'bg-foreground text-background' : 'bg-background'}`}
                >
                  <p className="font-medium">
                    {index + 1}. {step.label} {step.done ? 'Complete' : 'Pending'}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">{step.detail}</p>
                </div>
              ))}
            </div>
            <div className="mt-4 flex flex-wrap gap-2">
              <Button
                size="sm"
                onClick={() => scrollToSection(uploadSectionRef, 'input[type=\"file\"]')}
                disabled={hasAnyTimelineEntries}
              >
                Upload first note
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  if (latestTimelineEntryId) {
                    setEntryId(latestTimelineEntryId);
                    const nextStatus = timelineByEntry[latestTimelineEntryId]?.status ?? null;
                    setEntryStatus(nextStatus);
                    openIndexingModal(latestTimelineEntryId);
                    return;
                  }
                  scrollToSection(uploadSectionRef, 'input[type=\"file\"]');
                }}
                disabled={hasIndexedTimelineEntry}
              >
                Add indexing
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => navigate('/app')}
                disabled={hasKnowledgeAttempt}
              >
                Ask or search
              </Button>
              <Button size="sm" variant="outline" onClick={() => navigate('/app')}>
                Review timeline
              </Button>
            </div>
              </section>
            ) : null}
          </>
        ) : null}
        {!isSettingsPage ? (
          <>
            <section className="mt-6 grid gap-6 lg:min-h-[60vh] lg:grid-cols-2">
          <article className="mono-section flex flex-col gap-6">
            <div className="flex items-center gap-6">
              <button
                type="button"
                onClick={isRecording ? handleStopRecording : handleStartRecording}
                className={`flex h-36 w-36 items-center justify-center rounded-full border-2 border-foreground transition-none focus-visible:outline focus-visible:outline-3 focus-visible:outline-foreground focus-visible:outline-offset-3 md:h-44 md:w-44 ${
                  isRecording ? 'bg-background text-foreground' : 'bg-foreground text-background'
                }`}
                aria-label={isRecording ? 'Stop recording' : 'Start recording'}
                title={isRecording ? 'Stop recording' : 'Start recording'}
              >
                <svg
                  aria-hidden="true"
                  className="h-16 w-16 md:h-20 md:w-20"
                  viewBox="0 0 24 24"
                  fill="none"
                  xmlns="http://www.w3.org/2000/svg"
                >
                  <rect x="9" y="3" width="6" height="11" rx="3" stroke="currentColor" strokeWidth="1.8" />
                  <path d="M6.5 10.5V11.5C6.5 14.5376 8.96243 17 12 17C15.0376 17 17.5 14.5376 17.5 11.5V10.5" stroke="currentColor" strokeWidth="1.8" />
                  <path d="M12 17V21" stroke="currentColor" strokeWidth="1.8" />
                  <path d="M9 21H15" stroke="currentColor" strokeWidth="1.8" />
                </svg>
              </button>
              <div>
                <p className="mono-kicker">Record</p>
                <h2 className="text-4xl font-semibold leading-none tracking-tight">Capture</h2>
              </div>
            </div>

            <div className="space-y-4 border-t-2 border-foreground pt-4">
              <p className="text-sm font-medium">
                Status:{' '}
                <span className={isRecording || isCaptureBuffering ? 'text-foreground' : 'text-muted-foreground'}>{captureStatusLabel}</span>
              </p>
              <p className="text-sm text-muted-foreground">Timer: {formatDuration(elapsedSeconds)}</p>
              <p className="text-xs uppercase tracking-[0.1em] text-muted-foreground">
                Press the mic to {isRecording ? 'stop' : 'start'} recording.
              </p>
              {isCaptureBuffering ? (
                <div className="flex items-center gap-3 rounded-none border border-foreground/70 bg-muted/30 px-3 py-2 text-sm">
                  <span className="capture-buffering-icon" aria-hidden="true" />
                  <span className="text-foreground">Transcribing in progress...</span>
                </div>
              ) : null}
              {recordingError !== null ? (
                <p className="text-sm text-foreground" role="alert">
                  {recordingError}
                </p>
              ) : null}
              {recordingUrl !== null ? (
                <div className="space-y-2">
                  <audio controls className="w-full" src={recordingUrl} />
                </div>
              ) : null}
              {captureTranscriptPreview.trim().length > 0 || pipelineState === 'transcribing' ? (
                <div className="space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <p className="mono-kicker">Live transcript preview</p>
                    {captureTranscriptFinalized ? (
                      <span className="text-[10px] uppercase tracking-[0.1em] text-muted-foreground">finalized</span>
                    ) : null}
                  </div>
                  <div className="capture-preview-fade relative rounded-none border border-foreground/80 bg-background p-3">
                    <p className="capture-preview-text whitespace-pre-wrap text-sm text-foreground">
                      {captureTranscriptPreview.trim().length > 0 ? captureTranscriptPreview : 'Listening for words...'}
                    </p>
                  </div>
                </div>
              ) : null}
              {canArchiveEntry ? (
                <div className="flex flex-wrap items-center gap-3">
                  <Button onClick={() => void handleArchiveEntry()} disabled={isArchivingEntry}>
                    {isArchivingEntry ? 'Archiving...' : 'Archive'}
                  </Button>
                  <p className="text-xs text-muted-foreground">Archive this entry to trigger downstream processing workflows.</p>
                </div>
              ) : null}
              {archiveNotice ? <p className="text-sm text-foreground">{archiveNotice}</p> : null}
              {archiveError ? <p className="text-sm text-foreground">{archiveError}</p> : null}
            </div>
          </article>

          <article ref={askSectionRef} className="mono-section space-y-6">
            <div className="flex items-center justify-between gap-3 border-b-2 border-foreground pb-3">
              <div>
                <p className="mono-kicker">Ask</p>
                <h2 className="text-4xl font-semibold leading-none tracking-tight">Query</h2>
              </div>
              <span className="text-xs uppercase tracking-[0.1em] text-muted-foreground">Sources-first</span>
            </div>

            <div className="space-y-4">
              <form onSubmit={handleAskSubmit} className="flex flex-col gap-3 sm:flex-row">
                <input
                  type="search"
                  value={askQueryInput}
                  onChange={(event) => setAskQueryInput(event.target.value)}
                  placeholder="Ask from your transcripts..."
                  className="h-10 w-full rounded-none border bg-background px-3 text-sm"
                />
                <Button type="submit" disabled={isAskLoading} className="sm:w-auto">
                  {isAskLoading ? 'Finding sources...' : 'Find sources'}
                </Button>
              </form>
              <div className="flex flex-wrap gap-2">
                {ASK_TEMPLATES.map((template) => (
                  <Button
                    key={template.label}
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => applyAskTemplate(template.query)}
                    disabled={isAskLoading}
                  >
                    {template.label}
                  </Button>
                ))}
              </div>
              <div className="grid gap-3 md:grid-cols-2">
                <label className="space-y-1 text-sm">
                  <span className="font-medium">Date from</span>
                  <input
                    type="date"
                    value={askDateFrom}
                    onChange={(event) => setAskDateFrom(event.target.value)}
                    className="w-full rounded-none border bg-background px-3 py-2 text-sm"
                  />
                </label>
                <label className="space-y-1 text-sm">
                  <span className="font-medium">Date to</span>
                  <input
                    type="date"
                    value={askDateTo}
                    onChange={(event) => setAskDateTo(event.target.value)}
                    className="w-full rounded-none border bg-background px-3 py-2 text-sm"
                  />
                </label>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setAskDateFrom('');
                    setAskDateTo('');
                  }}
                >
                  Clear range
                </Button>
              </div>
              {askError ? <p className="text-sm text-foreground">{askError}</p> : null}
              {askSummaryError ? <p className="text-sm text-foreground">{askSummaryError}</p> : null}
              {isAskLoading ? <p className="text-sm text-muted-foreground">Building AI summary...</p> : null}
              <div className="border border-foreground bg-muted/20 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="text-sm font-semibold">AI Summary</h3>
                  <span className="text-xs text-muted-foreground">3-5 sentences</span>
                </div>
                <p className="mt-1 text-xs text-muted-foreground">
                  Summarized from all matched transcript snippets for this query and date range.
                </p>
                <div className="mt-3">
                  {askSummarySentences.length > 0 ? (
                    <AskSummaryRenderer
                      sentences={askSummarySentences}
                      citationsBySnippetId={askSummaryCitationsBySnippetId}
                      onOpenCitation={openAskCitation}
                    />
                  ) : isAskLoading ? (
                    <p className="text-sm text-muted-foreground">Building summary...</p>
                  ) : hasAskAttempt && filteredAskSources.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No matched notes in the selected range yet.</p>
                  ) : (
                    <p className="text-sm text-muted-foreground">Summary is not ready yet. Try running the query again.</p>
                  )}
                </div>
              </div>
            </div>
          </article>
            </section>

            <div className="mono-rule" />

            <section ref={timelineSectionRef} className="mono-section mt-6 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-base font-semibold">Timeline</h2>
            <span className="text-xs text-muted-foreground">
              Showing {filteredTimelineEntries.length} of {timelineEntries.length}
            </span>
          </div>
          <p className="text-sm text-muted-foreground">Filter by date range, type, context, and tags.</p>
          <div className="space-y-2">
            <p className="font-medium text-sm">Range</p>
            <div className="flex flex-wrap gap-2">
              {TIMELINE_WINDOW_OPTIONS.map((option) => (
                <button
                  key={option}
                  type="button"
                  onClick={() => setTimelineWindow(option)}
                  className={`min-h-10 border-2 px-3 py-2 text-xs uppercase tracking-[0.1em] transition-none focus-visible:outline focus-visible:outline-3 focus-visible:outline-foreground focus-visible:outline-offset-2 ${
                    timelineWindow === option ? 'border-foreground bg-foreground text-background' : 'border-foreground bg-background text-foreground'
                  }`}
                >
                  {option}
                </button>
              ))}
            </div>
            <p className="text-xs text-muted-foreground">
              {timelineWindow === 'all' ? 'Showing all dates.' : `Showing ${timelineRangeFrom} to ${timelineRangeTo}.`}
            </p>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <label className="space-y-1 text-sm">
              <span className="font-medium">Type</span>
              <select
                value={timelineTypeFilter}
                onChange={(event) => setTimelineTypeFilter(event.target.value)}
                className="w-full rounded-none border bg-background px-3 py-2 text-sm capitalize"
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
                className="w-full rounded-none border bg-background px-3 py-2 text-sm capitalize"
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
              className="w-full rounded-none border bg-background px-3 py-2 text-sm"
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
                setTimelineWindow('1m');
                setTimelineTypeFilter('all');
                setTimelineContextFilter('all');
                setTimelineTagFilterInput('');
              }}
            >
              Clear filters
            </Button>
          </div>
          {isTimelineLoading ? <p className="text-sm text-muted-foreground">Loading timeline...</p> : null}
          {timelineError ? <p className="text-sm text-foreground">{timelineError}</p> : null}
          {timelineChartEntries.length > 0 ? (
            <div className="border border-foreground bg-background p-4">
              <svg viewBox={`0 0 ${chartWidth} ${chartHeight}`} className="h-[22rem] w-full" role="img" aria-label="Recording duration over time">
                {yTicks.map((tick) => {
                  const y =
                    chartHeight -
                    chartPadding.bottom -
                    (tick / maxDurationSeconds) * (chartHeight - chartPadding.top - chartPadding.bottom);
                  return (
                    <g key={`y-${tick}`}>
                      <line
                        x1={chartPadding.left}
                        y1={y}
                        x2={chartWidth - chartPadding.right}
                        y2={y}
                        stroke="currentColor"
                        strokeOpacity="0.18"
                        strokeWidth="1"
                      />
                      <text x={chartPadding.left - 10} y={y + 4} textAnchor="end" className="fill-current text-[11px]">
                        {formatDurationMinutes(tick)}
                      </text>
                    </g>
                  );
                })}

                {xTickIndexes.map((index) => {
                  const point = chartPoints[index];
                  if (!point) {
                    return null;
                  }
                  return (
                    <g key={`x-${point.entryId}`}>
                      <line
                        x1={point.x}
                        y1={chartPadding.top}
                        x2={point.x}
                        y2={chartHeight - chartPadding.bottom}
                        stroke="currentColor"
                        strokeOpacity="0.12"
                        strokeWidth="1"
                      />
                      <text x={point.x} y={chartHeight - 14} textAnchor="middle" className="fill-current text-[11px]">
                        {point.dateLabel}
                      </text>
                    </g>
                  );
                })}

                <line
                  x1={chartPadding.left}
                  y1={chartHeight - chartPadding.bottom}
                  x2={chartWidth - chartPadding.right}
                  y2={chartHeight - chartPadding.bottom}
                  stroke="currentColor"
                  strokeWidth="2"
                />
                <line
                  x1={chartPadding.left}
                  y1={chartPadding.top}
                  x2={chartPadding.left}
                  y2={chartHeight - chartPadding.bottom}
                  stroke="currentColor"
                  strokeWidth="2"
                />

                {chartPath ? <path d={chartPath} fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" /> : null}

                {chartPoints.map((point) => (
                  <g
                    key={point.entryId}
                    role="button"
                    tabIndex={0}
                    onClick={() => navigate(`/app/entries/${point.entryId}`)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        navigate(`/app/entries/${point.entryId}`);
                      }
                    }}
                    className="cursor-pointer"
                    aria-label={`Open entry ${point.title} on ${point.dateLabel}, duration ${formatDurationMinutes(point.durationSeconds)}`}
                  >
                    <title>{`${point.title} • ${point.dateLabel} • ${formatDurationMinutes(point.durationSeconds)}`}</title>
                    <circle cx={point.x} cy={point.y} r="5" fill="white" stroke="currentColor" strokeWidth="2" />
                  </g>
                ))}

                <text x={(chartPadding.left + chartWidth - chartPadding.right) / 2} y={chartHeight - 2} textAnchor="middle" className="fill-current text-[12px]">
                  Date
                </text>
                <text
                  x={14}
                  y={(chartPadding.top + chartHeight - chartPadding.bottom) / 2}
                  textAnchor="middle"
                  className="fill-current text-[12px]"
                  transform={`rotate(-90 14 ${(chartPadding.top + chartHeight - chartPadding.bottom) / 2})`}
                >
                  Duration
                </text>
              </svg>
              <p className="mt-3 text-xs text-muted-foreground">Click a marker to open that entry detail.</p>
            </div>
          ) : filteredTimelineEntries.length > 0 ? (
            <p className="text-sm text-muted-foreground">No recording durations are available for the selected filters yet.</p>
          ) : hasAnyTimelineEntries ? (
            <p className="text-sm text-muted-foreground">No entries match the selected filters yet.</p>
          ) : (
            <div className="rounded-none border border-dashed p-4">
              <p className="text-sm font-medium">No entries yet.</p>
              <p className="mt-1 text-sm text-muted-foreground">
                Upload your first note to start transcription, quick indexing, and source-backed retrieval.
              </p>
              <div className="mt-3">
                <Button size="sm" onClick={() => scrollToSection(uploadSectionRef, 'input[type=\"file\"]')}>
                  Upload first note
                </Button>
              </div>
            </div>
          )}
            </section>
          </>
        ) : null}

        {isSettingsPage ? (
          <>
            <div className="mono-section mt-6 flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="mono-kicker">Audit</p>
            <h2 className="text-3xl font-semibold leading-none tracking-tight">Review Activity Log</h2>
          </div>
          <Button variant="outline" onClick={() => navigate('/app/audit')}>
            Open audit log
          </Button>
            </div>

        <section className="mono-section mt-6 space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-base font-semibold">Transcript</h2>
            {transcriptVersion !== null ? (
              <span className="rounded-none bg-secondary px-2 py-1 text-xs font-medium text-secondary-foreground">
                Version {transcriptVersion}
              </span>
            ) : null}
            {isTranscriptEdited ? (
              <span className="rounded-none bg-muted px-2 py-1 text-xs font-medium text-foreground">Edited</span>
            ) : null}
          </div>

          {entryTranscript ? (
            isEditingTranscript ? (
              <div className="space-y-3">
                <textarea
                  className="min-h-44 w-full rounded-none border bg-background p-3 text-sm"
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

          {transcriptNotice ? <p className="text-sm text-foreground">{transcriptNotice}</p> : null}
          {transcriptError ? <p className="text-sm text-foreground">{transcriptError}</p> : null}
        </section>

        <section ref={uploadSectionRef} className="mono-section mt-6 space-y-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="mono-kicker">Capture Setup</p>
              <h2 className="text-3xl font-semibold leading-none tracking-tight">Upload + Indexing</h2>
            </div>
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
          <p className="text-sm text-muted-foreground">Create entry, upload audio, then classify it.</p>
          <input
            type="file"
            accept="audio/*"
            onChange={handleAudioSelection}
            className="block w-full text-sm file:mr-3 file:rounded-none file:border file:border-input file:bg-background file:px-3 file:py-2 file:text-sm"
          />
          <div className="flex flex-wrap items-center gap-3">
            <Button onClick={() => void handleStartUpload()} disabled={!selectedFile || isBusy}>
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
          </div>
          <p className="text-sm text-muted-foreground">
            State: <span className="font-medium text-foreground">{pipelineState}</span>
          </p>
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
          {currentIndexing ? (
            <div className="space-y-1 border-t border-foreground pt-3 text-sm">
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
              {currentTimelineEntry?.sentimentLabel ? (
                <p>
                  Sentiment:{' '}
                  <span className="font-medium capitalize">
                    {currentTimelineEntry.sentimentLabel}
                    {typeof currentTimelineEntry.sentimentScore === 'number'
                      ? ` (${Math.round(currentTimelineEntry.sentimentScore * 100)}%)`
                      : ''}
                  </span>
                </p>
              ) : null}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">No indexing saved for this entry yet.</p>
          )}
          {errorMessage ? <p className="text-sm text-foreground">{errorMessage}</p> : null}
        </section>

            <div className="mono-section mt-6 space-y-4">
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
              className="h-10 w-full rounded-none border bg-background px-3 text-sm"
            />
            <Button type="submit" disabled={isSearchLoading} className="sm:w-auto">
              {isSearchLoading ? 'Searching...' : 'Search'}
            </Button>
          </form>
          {searchError ? <p className="text-sm text-foreground">{searchError}</p> : null}
          {isSearchLoading ? <p className="text-sm text-muted-foreground">Loading results...</p> : null}
          {searchResults.length > 0 ? (
            <div className="space-y-3">
              {searchResults.map((result, index) => (
                <article key={`${result.entryId}-${result.startChar}-${result.endChar}-${index}`} className="border border-foreground p-3">
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

        <div className="mono-section mt-6 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-base font-semibold">What gets sent</h2>
            <span className="text-xs text-muted-foreground">Preview provider-bound payload before summarization</span>
          </div>
          <label className="space-y-1 text-sm">
            <span className="font-medium">Ask query</span>
            <input
              type="text"
              value={askQuestionInput}
              onChange={(event) => setAskQuestionInput(event.target.value)}
              placeholder="What should I summarize from these sources?"
              className="w-full rounded-none border bg-background px-3 py-2 text-sm"
            />
          </label>
          <div className="grid gap-3 md:grid-cols-2">
            <label className="space-y-1 text-sm">
              <span className="font-medium">Date from</span>
              <input
                type="date"
                value={askDateFrom}
                onChange={(event) => setAskDateFrom(event.target.value)}
                className="w-full rounded-none border bg-background px-3 py-2 text-sm"
              />
            </label>
            <label className="space-y-1 text-sm">
              <span className="font-medium">Date to</span>
              <input
                type="date"
                value={askDateTo}
                onChange={(event) => setAskDateTo(event.target.value)}
                className="w-full rounded-none border bg-background px-3 py-2 text-sm"
              />
            </label>
          </div>
          <div className="space-y-2 rounded-none border bg-muted/20 p-3 text-sm">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={askPreviewOptions.redact}
                onChange={(event) =>
                  setAskPreviewOptions((previous) => ({
                    ...previous,
                    redact: event.target.checked
                  }))
                }
              />
              <span>Redact direct identifiers (emails, phones)</span>
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={askPreviewOptions.mask}
                onChange={(event) =>
                  setAskPreviewOptions((previous) => ({
                    ...previous,
                    mask: event.target.checked
                  }))
                }
              />
              <span>Mask long numbers and URLs</span>
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={askPreviewOptions.includeSensitive}
                onChange={(event) =>
                  setAskPreviewOptions((previous) => ({
                    ...previous,
                    includeSensitive: event.target.checked
                  }))
                }
              />
              <span>Include sensitive snippets</span>
            </label>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">
              {askSourcesForPreview.length} snippet{askSourcesForPreview.length === 1 ? '' : 's'} in outbound payload
            </p>
            <Button
              onClick={() => setIsWhatGetsSentModalOpen(true)}
              disabled={askPreviewPayload.query.trim().length === 0 || askSourcesForPreview.length === 0}
            >
              Preview payload
            </Button>
          </div>
        </div>

        <div className="mono-section mt-6 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-base font-semibold">Data export</h2>
            <span className="text-xs text-muted-foreground">Privacy and portability</span>
          </div>
          <p className="text-sm text-muted-foreground">Request a full account export, check status, and download the archive when it is ready.</p>
          <div className="rounded-none border border bg-muted p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-sm font-semibold text-foreground">Export all data</h3>
              {dataExportStatusLabel ? (
                <span className="rounded-none border border bg-white px-2 py-1 text-xs font-medium text-foreground">
                  Status: {dataExportStatusLabel}
                </span>
              ) : null}
            </div>
            <p className="mt-1 text-xs text-foreground">Includes transcripts, tags, brag bullets, and uploaded audio assets.</p>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button onClick={handleRequestDataExport} disabled={isRequestingDataExport || isCheckingDataExport || isDownloadingDataExport}>
                {isRequestingDataExport ? 'Requesting export...' : 'Request export'}
              </Button>
              <Button
                onClick={handleCheckDataExportStatus}
                variant="outline"
                disabled={!dataExportJob || isRequestingDataExport || isCheckingDataExport || isDownloadingDataExport}
              >
                {isCheckingDataExport ? 'Checking status...' : 'Check status'}
              </Button>
              <Button
                onClick={handleDownloadDataExport}
                variant="outline"
                disabled={!dataExportJob || !isDataExportReady || isRequestingDataExport || isCheckingDataExport || isDownloadingDataExport}
              >
                {isDownloadingDataExport ? 'Downloading...' : 'Download export'}
              </Button>
            </div>
            {dataExportJob ? <p className="mt-2 text-xs text-foreground">Job ID: <span className="font-mono">{dataExportJob.id}</span></p> : null}
            {dataExportNotice ? <p className="mt-2 text-xs text-foreground">{dataExportNotice}</p> : null}
            {dataExportError ? <p className="mt-2 text-xs text-foreground">{dataExportError}</p> : null}
          </div>
          <div className="rounded-none border border bg-foreground p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-sm font-semibold text-foreground">Delete account</h3>
              <span className="rounded-none border border bg-white px-2 py-1 text-xs font-medium text-foreground">Permanent</span>
            </div>
            <p className="mt-1 text-xs text-foreground">
              This permanently deletes your account, audio files, transcripts, tags, exports, and saved drafts. This action cannot be undone.
            </p>
            <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-foreground">
              <li>Export your data before deleting so you keep a copy.</li>
              <li>Deletion signs you out on this device.</li>
              <li>Recovery is not possible after confirmation.</li>
            </ul>
            <form className="mt-3 space-y-3" onSubmit={handleDeleteAccount}>
              <label className="space-y-1 text-sm">
                <span className="font-medium text-foreground">Current password</span>
                <input
                  type="password"
                  autoComplete="current-password"
                  value={deleteAccountPassword}
                  onChange={(event) => setDeleteAccountPassword(event.target.value)}
                  className="w-full rounded-none border border bg-white px-3 py-2 text-sm"
                  disabled={isDeletingAccount}
                />
              </label>
              <label className="space-y-1 text-sm">
                <span className="font-medium text-foreground">Type {DELETE_ACCOUNT_CONFIRMATION_TEXT}</span>
                <input
                  type="text"
                  value={deleteAccountConfirmationInput}
                  onChange={(event) => setDeleteAccountConfirmationInput(event.target.value)}
                  className="w-full rounded-none border border bg-white px-3 py-2 font-mono text-sm"
                  disabled={isDeletingAccount}
                />
              </label>
              <label className="flex items-start gap-2 text-xs text-foreground">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={deleteAccountAcknowledge}
                  onChange={(event) => setDeleteAccountAcknowledge(event.target.checked)}
                  disabled={isDeletingAccount}
                />
                <span>I understand this is irreversible and permanently removes my data.</span>
              </label>
              <Button type="submit" disabled={!canSubmitDeleteAccount} className="bg-foreground text-background hover:bg-foreground">
                {isDeletingAccount ? 'Deleting account...' : 'Delete account permanently'}
              </Button>
            </form>
            {deleteAccountNotice ? <p className="mt-2 text-xs text-foreground">{deleteAccountNotice}</p> : null}
            {deleteAccountError ? <p className="mt-2 text-xs text-foreground">{deleteAccountError}</p> : null}
          </div>
        </div>
        <div className="mono-section mt-6 space-y-4">
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
                className="w-full rounded-none border bg-background px-3 py-2 text-sm"
              />
            </label>
            <label className="space-y-1 text-sm">
              <span className="font-medium">Date to</span>
              <input
                type="date"
                value={bragDateTo}
                onChange={(event) => setBragDateTo(event.target.value)}
                className="w-full rounded-none border bg-background px-3 py-2 text-sm"
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
          <div className="rounded-none border border bg-muted p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-sm font-semibold text-foreground">Export report</h3>
              {bragExportStatusLabel ? (
                <span className="rounded-none border border bg-white px-2 py-1 text-xs font-medium text-foreground">
                  Status: {bragExportStatusLabel}
                </span>
              ) : null}
            </div>
            <p className="mt-1 text-xs text-foreground">Request a text export, check job status, and download when ready.</p>
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
            {bragExportJob ? <p className="mt-2 text-xs text-foreground">Job ID: <span className="font-mono">{bragExportJob.id}</span></p> : null}
            {bragExportNotice ? <p className="mt-2 text-xs text-foreground">{bragExportNotice}</p> : null}
            {bragExportError ? <p className="mt-2 text-xs text-foreground">{bragExportError}</p> : null}
          </div>
          <div className="space-y-3">
            {[...BRAG_BUCKETS, BRAG_UNASSIGNED_BUCKET].map((bucket) => (
              <article key={bucket} className="border border-foreground p-3">
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
                            className="min-h-20 w-full rounded-none border bg-background p-2 text-sm"
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
                                className="w-full rounded-none border bg-background px-2 py-1 text-sm"
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
                            <div className="mt-2 space-y-2 rounded-none border bg-background p-2">
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
          </>
        ) : null}
        <div className="mt-6">
          <Button onClick={handleLogout} disabled={isLoggingOut} variant="outline">
            {isLoggingOut ? 'Logging out...' : 'Logout'}
          </Button>
        </div>
      </section>
      {isWhatGetsSentModalOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4" role="presentation">
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="what-gets-sent-title"
            className="w-full max-w-3xl rounded-none border bg-card p-6 text-card-foreground "
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h2 id="what-gets-sent-title" className="text-lg font-semibold">
                  What gets sent
                </h2>
                <p className="mt-1 text-sm text-muted-foreground">This preview shows transformed snippets only. Stored transcript data is unchanged.</p>
              </div>
              <Button onClick={() => setIsWhatGetsSentModalOpen(false)} variant="outline" size="sm">
                Close
              </Button>
            </div>
            <div className="mt-4 rounded-none border bg-muted/20 p-3">
              <pre className="max-h-[60vh] overflow-auto text-xs">{askPreviewPayloadJson}</pre>
            </div>
          </section>
        </div>
      ) : null}
      {isIndexingModalOpen && entryId ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4" role="presentation">
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="quick-indexing-title"
            className="w-full max-w-lg rounded-none border bg-card p-6 text-card-foreground "
          >
            <h2 id="quick-indexing-title" className="text-lg font-semibold">
              Quick indexing
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">Classify this transcript in under 5 seconds.</p>
            <p className="mt-1 text-xs text-muted-foreground">Pre-filled with lightweight AI guesses. Edit before saving.</p>

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
                      className={`rounded-none border px-3 py-2 text-sm capitalize transition-none ${
                        isSelected
                          ? 'border-foreground bg-foreground text-background'
                          : 'border-foreground bg-background hover:bg-foreground hover:text-background'
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
                      className={`rounded-none border px-3 py-2 text-sm capitalize transition-none ${
                        isSelected
                          ? 'border-foreground bg-foreground text-background'
                          : 'border-foreground bg-background hover:bg-foreground hover:text-background'
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
                className="mt-2 w-full rounded-none border bg-background px-3 py-2 text-sm"
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
