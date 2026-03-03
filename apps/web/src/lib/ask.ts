import { ApiError, apiFetch } from '@/lib/auth';

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

export type AskSource = {
  snippetId: string;
  entryId: string;
  transcriptId: string | null;
  snippetText: string;
  startChar: number;
  endChar: number;
  rank: number | null;
};

export type AskSummarySentenceApi = {
  text: string;
  snippetIds: string[];
};

export type AskQueryResult = {
  queryId: string;
  queryText: string;
  sources: AskSource[];
};

export type AskSummaryResult = {
  queryId: string;
  summaryStatus: string | null;
  sentences: AskSummarySentenceApi[];
};

export async function submitAskQuery(queryText: string, limit: number): Promise<AskQueryResult> {
  const payload = await apiFetch<unknown>('/api/v1/ask/query', {
    method: 'POST',
    body: {
      query_text: queryText,
      limit
    }
  });
  const data = asRecord(payload);
  const queryId = pickString(data?.query_id, data?.queryId);
  const normalizedQuery = pickString(data?.query_text, data?.queryText) ?? queryText;
  const sources = parseAskSources(data?.sources);
  if (!queryId) {
    throw new ApiError('Ask response did not include query id.', 500);
  }
  return { queryId, queryText: normalizedQuery, sources };
}

export async function summarizeAskQuery(queryId: string, snippetIds?: string[]): Promise<AskSummaryResult> {
  const payload = await apiFetch<unknown>(`/api/v1/ask/${queryId}/summarize`, {
    method: 'POST',
    body: {
      snippet_ids: snippetIds && snippetIds.length > 0 ? snippetIds : undefined
    }
  });
  const data = asRecord(payload);
  return {
    queryId: pickString(data?.query_id, data?.queryId) ?? queryId,
    summaryStatus: pickString(data?.summary_status, data?.summaryStatus),
    sentences: parseSummarySentences(asRecord(data?.summary)?.sentences)
  };
}

function parseAskSources(value: unknown): AskSource[] {
  if (!Array.isArray(value)) {
    return [];
  }

  return value
    .map((item) => {
      const data = asRecord(item);
      const snippetId = pickString(data?.snippet_id, data?.snippetId, data?.id);
      const entryId = pickString(data?.entry_id, data?.entryId);
      const snippetText = pickString(data?.snippet_text, data?.snippetText);
      const startChar = pickNumber(data?.start_char, data?.startChar);
      const endChar = pickNumber(data?.end_char, data?.endChar);
      if (!snippetId || !entryId || !snippetText || startChar === null || endChar === null || endChar <= startChar) {
        return null;
      }
      return {
        snippetId,
        entryId,
        transcriptId: pickString(data?.transcript_id, data?.transcriptId),
        snippetText,
        startChar,
        endChar,
        rank: pickNumber(data?.rank)
      };
    })
    .filter((item): item is AskSource => item !== null);
}

function parseSummarySentences(value: unknown): AskSummarySentenceApi[] {
  if (!Array.isArray(value)) {
    return [];
  }

  const parsed: AskSummarySentenceApi[] = [];
  for (const item of value) {
    const data = asRecord(item);
    const text = pickString(data?.text)?.trim();
    const rawSnippetIds = data?.snippet_ids ?? data?.snippetIds;
    if (!text || !Array.isArray(rawSnippetIds)) {
      continue;
    }
    const snippetIds = rawSnippetIds
      .map((entry) => (typeof entry === 'string' ? entry.trim() : ''))
      .filter((entry) => entry.length > 0);
    if (snippetIds.length === 0) {
      continue;
    }
    parsed.push({ text, snippetIds });
  }
  return parsed;
}
