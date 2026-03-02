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

async function authorizedRequest(path: string, init: RequestInit, retry = true): Promise<Response> {
  const headers = new Headers(init.headers ?? {});
  const token = getAccessToken();
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  const response = await fetch(buildUrl(path), {
    ...init,
    headers,
    credentials: 'include'
  });

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
    status: pickEntryStatus(payload)
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
