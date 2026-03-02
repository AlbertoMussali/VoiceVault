import { ApiError, getAccessToken, refreshSession } from '@/lib/auth';

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '';

type ErrorContract = {
  errorCode?: string;
  errorType?: string;
  retryable?: boolean;
};

export type EntryStatusResponse = {
  entryId: string;
  status: string | null;
  title?: string | null;
};

export type TranscriptResponse = {
  text: string;
  version: number | null;
  source: string | null;
};

export type EntryDetail = EntryStatusResponse & {
  transcript: TranscriptResponse | null;
  transcriptText: string | null;
  audioUrl: string | null;
};

export type EntryDetailResponse = EntryDetail;

export type TimelineEntry = {
  entryId: string;
  status: string | null;
  title: string | null;
  createdAt: string | null;
  occurredAt: string | null;
  entryType: string | null;
  context: string | null;
  tags: string[];
  quote: string | null;
};

export type SearchResult = {
  entryId: string;
  transcriptId: string | null;
  snippetText: string;
  startChar: number;
  endChar: number;
  rank: number | null;
};

export class EntryApiError extends ApiError {
  readonly errorCode?: string;
  readonly errorType?: string;
  readonly retryable: boolean;

  constructor(message: string, status: number, contract?: ErrorContract) {
    super(message, status);
    this.name = 'EntryApiError';
    this.errorCode = contract?.errorCode;
    this.errorType = contract?.errorType;
    this.retryable = contract?.retryable === true;
  }
}

function buildUrl(path: string): string {
  if (!API_BASE_URL) {
    return path;
  }

  return `${API_BASE_URL}${path}`;
}

function parseErrorMessage(payload: unknown, fallback: string): string {
  if (typeof payload === 'string') {
    return payload;
  }

  if (!payload || typeof payload !== 'object') {
    return fallback;
  }

  const record = payload as Record<string, unknown>;
  if (typeof record.message === 'string') {
    return record.message;
  }
  if (typeof record.detail === 'string') {
    return record.detail;
  }

  return fallback;
}

function parseErrorContract(payload: unknown): ErrorContract {
  if (!payload || typeof payload !== 'object') {
    return {};
  }

  const record = payload as Record<string, unknown>;
  return {
    errorCode: typeof record.error_code === 'string' ? record.error_code : undefined,
    errorType: typeof record.error_type === 'string' ? record.error_type : undefined,
    retryable: typeof record.retryable === 'boolean' ? record.retryable : undefined
  };
}

async function parseResponsePayload(response: Response): Promise<unknown> {
  const contentType = response.headers.get('content-type') ?? '';
  if (contentType.includes('application/json')) {
    return response.json();
  }

  return response.text();
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return null;
  }

  return value as Record<string, unknown>;
}

function pickString(...values: unknown[]): string | null {
  for (const value of values) {
    if (typeof value === 'string') {
      return value;
    }
  }

  return null;
}

function pickNumber(...values: unknown[]): number | null {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value)) {
      return value;
    }
  }

  return null;
}

function buildNetworkErrorMessage(): string {
  const defaultMessage = 'Cannot reach the VoiceVault API. Start `make dev-local` and verify API is running.';
  if (!API_BASE_URL) {
    return defaultMessage;
  }

  return `${defaultMessage} Current API base URL: ${API_BASE_URL}`;
}

async function authorizedRequest(path: string, init: RequestInit, retry = true): Promise<Response> {
  const headers = new Headers(init.headers ?? {});
  const token = getAccessToken();
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  let response: Response;
  try {
    response = await fetch(buildUrl(path), {
      ...init,
      headers,
      credentials: 'include'
    });
  } catch (error) {
    // Browser fetch throws TypeError for network/CORS connectivity failures.
    throw new EntryApiError(buildNetworkErrorMessage(), 0, {
      errorCode: 'API_UNREACHABLE',
      errorType: 'transient',
      retryable: true
    });
  }

  if (response.status === 401 && retry) {
    const refreshed = await refreshSession();
    if (refreshed) {
      return authorizedRequest(path, init, false);
    }
  }

  return response;
}

async function authorizedFetch(path: string, init: RequestInit): Promise<unknown> {
  const response = await authorizedRequest(path, init);
  const payload = await parseResponsePayload(response);
  if (!response.ok) {
    throw new EntryApiError(parseErrorMessage(payload, 'Request failed.'), response.status, parseErrorContract(payload));
  }

  return payload;
}

function pickEntryId(payload: unknown): string | null {
  if (!payload || typeof payload !== 'object') {
    return null;
  }

  const data = payload as Record<string, unknown>;
  const value = data.entry_id ?? data.entryId ?? data.id;
  return typeof value === 'string' ? value : null;
}

function pickEntryStatus(payload: unknown): string | null {
  const data = asRecord(payload);
  if (!data) {
    return null;
  }

  return pickString(data.status);
}

function pickEntryTitle(payload: unknown): string | null {
  const data = asRecord(payload);
  if (!data) {
    return null;
  }

  return pickString(data.title, data.entry_title, data.entryTitle);
}

function pickTranscriptText(payload: unknown): string | null {
  const root = asRecord(payload);
  if (!root) {
    return null;
  }

  const direct = pickString(root.transcript_text, root.transcriptText, root.transcript);
  if (direct) {
    return direct;
  }

  const currentTranscript = asRecord(root.current_transcript) ?? asRecord(root.currentTranscript) ?? asRecord(root.transcript);
  if (currentTranscript) {
    const current = pickString(currentTranscript.transcript_text, currentTranscript.transcriptText, currentTranscript.text);
    if (current) {
      return current;
    }
  }

  const transcriptList = root.transcripts;
  if (!Array.isArray(transcriptList)) {
    return null;
  }

  const currentVersion = transcriptList.find((value) => asRecord(value)?.is_current === true);
  const selected = asRecord(currentVersion) ?? asRecord(transcriptList[0]);
  if (!selected) {
    return null;
  }

  return pickString(selected.transcript_text, selected.transcriptText, selected.text);
}

function pickAudioUrl(payload: unknown): string | null {
  const root = asRecord(payload);
  if (!root) {
    return null;
  }

  const direct = pickString(root.audio_url, root.audioUrl);
  if (direct) {
    return direct;
  }

  const audioRecord = asRecord(root.audio) ?? asRecord(root.audio_asset) ?? asRecord(root.audioAsset);
  if (!audioRecord) {
    return null;
  }

  return pickString(audioRecord.url, audioRecord.audio_url, audioRecord.audioUrl);
}

function parseTagsFromEntryPayload(payload: unknown): string[] {
  const root = asRecord(payload);
  if (!root) {
    return [];
  }

  const collection = root.tags;
  if (!Array.isArray(collection)) {
    return [];
  }

  const parsed = collection
    .map((value) => {
      if (typeof value === 'string') {
        return value.trim().toLowerCase();
      }
      const record = asRecord(value);
      const name = pickString(record?.name, record?.normalized_name, record?.normalizedName);
      return name ? name.trim().toLowerCase() : '';
    })
    .filter((value) => value.length > 0);

  return Array.from(new Set(parsed));
}

function parseQuoteFromEntryPayload(payload: unknown): string | null {
  const root = asRecord(payload);
  if (!root) {
    return null;
  }

  const quote = pickString(
    root.quote,
    root.quote_text,
    root.quoteText,
    root.snippet,
    root.snippet_text,
    root.snippetText,
    root.receipt_snippet,
    root.receiptSnippet
  );
  if (!quote) {
    return null;
  }

  const normalized = quote.replace(/\s+/g, ' ').trim();
  return normalized.length > 0 ? normalized : null;
}

function parseTimelineEntry(payload: unknown): TimelineEntry | null {
  const data = asRecord(payload);
  if (!data) {
    return null;
  }

  const entryId = pickEntryId(payload);
  if (!entryId) {
    return null;
  }

  return {
    entryId,
    status: pickEntryStatus(payload),
    title: pickString(data.title, data.entry_title, data.entryTitle),
    createdAt: pickString(data.created_at, data.createdAt),
    occurredAt: pickString(data.occurred_at, data.occurredAt),
    entryType: pickString(data.entry_type, data.entryType, data.type),
    context: pickString(data.context),
    tags: parseTagsFromEntryPayload(payload),
    quote: parseQuoteFromEntryPayload(payload)
  };
}

function parseSearchResult(payload: unknown): SearchResult | null {
  const data = asRecord(payload);
  if (!data) {
    return null;
  }

  const entryId = pickString(data.entry_id, data.entryId);
  const snippetText = pickString(data.snippet_text, data.snippetText);
  const startChar = pickNumber(data.start_char, data.startChar);
  const endChar = pickNumber(data.end_char, data.endChar);
  if (!entryId || !snippetText || startChar === null || endChar === null || endChar <= startChar) {
    return null;
  }

  return {
    entryId,
    transcriptId: pickString(data.transcript_id, data.transcriptId),
    snippetText,
    startChar,
    endChar,
    rank: pickNumber(data.rank)
  };
}

function parseTranscriptPayload(payload: unknown): TranscriptResponse | null {
  if (!payload || typeof payload !== 'object') {
    return null;
  }

  const data = payload as Record<string, unknown>;
  const textValue = data.transcript_text ?? data.text ?? data.content;
  const versionValue = data.version;
  const sourceValue = data.source;

  if (typeof textValue !== 'string') {
    return null;
  }

  return {
    text: textValue,
    version: typeof versionValue === 'number' ? versionValue : null,
    source: typeof sourceValue === 'string' ? sourceValue : null
  };
}

function pickCurrentTranscript(payload: unknown): TranscriptResponse | null {
  if (!payload || typeof payload !== 'object') {
    return null;
  }

  const data = payload as Record<string, unknown>;
  const nestedTranscript =
    parseTranscriptPayload(data.transcript) ??
    parseTranscriptPayload(data.current_transcript) ??
    parseTranscriptPayload(data.currentTranscript);

  if (nestedTranscript) {
    return nestedTranscript;
  }

  return parseTranscriptPayload(data);
}

export async function createEntry(): Promise<EntryStatusResponse> {
  const payload = await authorizedFetch('/api/v1/entries', { method: 'POST' });
  const entryId = pickEntryId(payload);
  if (!entryId) {
    throw new ApiError('Entry creation response did not include an entry id.', 500);
  }

  return {
    entryId,
    status: pickEntryStatus(payload),
    title: pickEntryTitle(payload)
  };
}

export async function uploadEntryAudio(entryId: string, audioFile: File): Promise<void> {
  const mimeType = audioFile.type || 'audio/webm';
  await authorizedFetch(`/api/v1/entries/${entryId}/audio`, {
    method: 'POST',
    headers: {
      'Content-Type': mimeType,
      'x-audio-filename': audioFile.name
    },
    body: audioFile
  });
}

export async function fetchEntryStatus(entryId: string): Promise<EntryStatusResponse> {
  const payload = await authorizedFetch(`/api/v1/entries/${entryId}`, { method: 'GET' });
  const resolvedEntryId = pickEntryId(payload) ?? entryId;

  return {
    entryId: resolvedEntryId,
    status: pickEntryStatus(payload)
  };
}

export async function fetchEntryDetail(entryId: string): Promise<EntryDetail> {
  const payload = await authorizedFetch(`/api/v1/entries/${entryId}`, { method: 'GET' });
  const transcript = pickCurrentTranscript(payload);

  return {
    entryId: pickEntryId(payload) ?? entryId,
    status: pickEntryStatus(payload),
    transcript,
    transcriptText: transcript?.text ?? pickTranscriptText(payload),
    audioUrl: pickAudioUrl(payload)
  };
}

export async function fetchEntryAudioBlob(entryId: string): Promise<Blob | null> {
  const response = await authorizedRequest(`/api/v1/entries/${entryId}/audio`, {
    method: 'GET'
  });

  if (response.status === 404 || response.status === 405) {
    return null;
  }

  if (!response.ok) {
    const payload = await parseResponsePayload(response);
    throw new EntryApiError(parseErrorMessage(payload, 'Failed to load entry audio.'), response.status, parseErrorContract(payload));
  }

  return response.blob();
}

export async function deleteEntry(entryId: string): Promise<void> {
  const response = await authorizedRequest(`/api/v1/entries/${entryId}`, {
    method: 'DELETE'
  });

  if (response.status === 204) {
    return;
  }

  const payload = await parseResponsePayload(response);
  if (!response.ok) {
    throw new EntryApiError(parseErrorMessage(payload, 'Failed to delete entry.'), response.status, parseErrorContract(payload));
  }
}

export async function updateEntryTranscript(entryId: string, transcriptText: string): Promise<TranscriptResponse> {
  const payload = await authorizedFetch(`/api/v1/entries/${entryId}/transcript`, {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({ transcript_text: transcriptText })
  });

  const transcript = pickCurrentTranscript(payload);
  if (!transcript) {
    throw new ApiError('Transcript update response did not include transcript data.', 500);
  }

  return transcript;
}

export async function fetchEntriesTimeline(): Promise<TimelineEntry[]> {
  const payload = await authorizedFetch('/api/v1/entries', { method: 'GET' });
  const root = asRecord(payload);
  if (!root) {
    return [];
  }

  const entries = root.entries;
  if (!Array.isArray(entries)) {
    return [];
  }

  const parsed = entries
    .map((value) => parseTimelineEntry(value))
    .filter((value): value is TimelineEntry => value !== null);

  const deduped = new Map<string, TimelineEntry>();
  for (const entry of parsed) {
    deduped.set(entry.entryId, entry);
  }

  return Array.from(deduped.values());
}

export async function searchEntries(query: string, limit = 10): Promise<SearchResult[]> {
  const normalizedQuery = query.trim();
  if (!normalizedQuery) {
    return [];
  }

  const params = new URLSearchParams({
    q: normalizedQuery,
    limit: String(limit)
  });
  const payload = await authorizedFetch(`/api/v1/search?${params.toString()}`, { method: 'GET' });
  const root = asRecord(payload);
  if (!root || !Array.isArray(root.results)) {
    return [];
  }

  return root.results
    .map((value) => parseSearchResult(value))
    .filter((value): value is SearchResult => value !== null);
}
