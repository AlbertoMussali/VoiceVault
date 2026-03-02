export type BragDraftEvidence = {
  entryId: string;
  transcriptVersion: number | null;
  startChar: number;
  endChar: number;
  snippetText: string;
};

export type BragDraft = {
  id: string;
  claimText: string;
  bucket: string | null;
  source: 'entry_detail';
  createdAt: string;
  evidence: BragDraftEvidence[];
};

const BRAG_DRAFTS_STORAGE_KEY = 'voicevault.brag-drafts.v1';

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function readStoredDrafts(): BragDraft[] {
  if (typeof window === 'undefined') {
    return [];
  }

  const raw = window.localStorage.getItem(BRAG_DRAFTS_STORAGE_KEY);
  if (!raw) {
    return [];
  }

  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return [];
    }

    return parsed.filter((value): value is BragDraft => {
      if (!isRecord(value)) {
        return false;
      }

      return (
        typeof value.id === 'string' &&
        typeof value.claimText === 'string' &&
        (typeof value.bucket === 'string' || value.bucket === null) &&
        value.source === 'entry_detail' &&
        typeof value.createdAt === 'string' &&
        Array.isArray(value.evidence)
      );
    });
  } catch {
    return [];
  }
}

function writeStoredDrafts(drafts: BragDraft[]) {
  if (typeof window === 'undefined') {
    return;
  }

  window.localStorage.setItem(BRAG_DRAFTS_STORAGE_KEY, JSON.stringify(drafts));
}

export function listBragDrafts(): BragDraft[] {
  return readStoredDrafts();
}

export function updateBragDraft(
  draftId: string,
  patch: {
    claimText?: string;
    bucket?: string | null;
  }
): BragDraft | null {
  const drafts = readStoredDrafts();
  const index = drafts.findIndex((draft) => draft.id === draftId);
  if (index === -1) {
    return null;
  }

  const next: BragDraft = {
    ...drafts[index],
    claimText: patch.claimText ?? drafts[index].claimText,
    bucket: patch.bucket !== undefined ? patch.bucket : drafts[index].bucket
  };
  drafts[index] = next;
  writeStoredDrafts(drafts);
  return next;
}

export function createBragDraftFromSnippet(input: {
  entryId: string;
  transcriptVersion: number | null;
  startChar: number;
  endChar: number;
  snippetText: string;
}): BragDraft {
  const draft: BragDraft = {
    id: typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : `draft-${Date.now()}`,
    claimText: input.snippetText,
    bucket: null,
    source: 'entry_detail',
    createdAt: new Date().toISOString(),
    evidence: [
      {
        entryId: input.entryId,
        transcriptVersion: input.transcriptVersion,
        startChar: input.startChar,
        endChar: input.endChar,
        snippetText: input.snippetText
      }
    ]
  };

  const drafts = readStoredDrafts();
  drafts.unshift(draft);
  writeStoredDrafts(drafts);
  return draft;
}
