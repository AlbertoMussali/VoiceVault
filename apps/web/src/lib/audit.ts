import { apiFetch } from '@/lib/auth';

export type AuditLogEvent = {
  id: string;
  eventType: string;
  createdAt: string | null;
  entryId: string | null;
  userId: string | null;
  metadata: Record<string, unknown> | null;
};

export type AuditLogPage = {
  items: AuditLogEvent[];
  page: number;
  pageSize: number;
  total: number | null;
  hasNext: boolean;
  hasPrevious: boolean;
};

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
    if (typeof value === 'string' && value.trim().length > 0) {
      const parsed = Number(value);
      if (Number.isFinite(parsed)) {
        return parsed;
      }
    }
  }

  return null;
}

function pickBoolean(...values: unknown[]): boolean | null {
  for (const value of values) {
    if (typeof value === 'boolean') {
      return value;
    }
  }

  return null;
}

function mapAuditEvent(payload: unknown, index: number): AuditLogEvent {
  const row = asRecord(payload);
  if (!row) {
    return {
      id: `audit-${index}`,
      eventType: 'unknown',
      createdAt: null,
      entryId: null,
      userId: null,
      metadata: null
    };
  }

  const idValue = pickString(row.id) ?? String(pickNumber(row.id) ?? `audit-${index}`);
  const metadata = asRecord(row.metadata_json) ?? asRecord(row.metadata) ?? null;

  return {
    id: idValue,
    eventType: pickString(row.event_type, row.eventType) ?? 'unknown',
    createdAt: pickString(row.created_at, row.createdAt),
    entryId: pickString(row.entry_id, row.entryId),
    userId: pickString(row.user_id, row.userId),
    metadata
  };
}

function pickItems(payload: unknown): unknown[] {
  if (Array.isArray(payload)) {
    return payload;
  }

  const root = asRecord(payload);
  if (!root) {
    return [];
  }

  const list =
    (Array.isArray(root.items) ? root.items : null) ??
    (Array.isArray(root.audit_log) ? root.audit_log : null) ??
    (Array.isArray(root.auditLog) ? root.auditLog : null) ??
    (Array.isArray(root.results) ? root.results : null) ??
    (Array.isArray(root.data) ? root.data : null);

  return list ?? [];
}

export async function fetchAuditLogPage(page: number, pageSize: number): Promise<AuditLogPage> {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize)
  });
  const payload = await apiFetch<unknown>(`/api/v1/audit?${params.toString()}`, {
    method: 'GET'
  });

  const root = asRecord(payload);
  const items = pickItems(payload).map((item, index) => mapAuditEvent(item, index));
  const parsedPage = pickNumber(root?.page, root?.page_index, root?.pageIndex);
  const parsedPageSize = pickNumber(root?.page_size, root?.pageSize, root?.limit, root?.per_page, root?.perPage);
  const total = pickNumber(root?.total, root?.total_count, root?.totalCount);

  const hasNextFromServer = pickBoolean(root?.has_next, root?.hasNext);
  const nextPage = pickNumber(root?.next_page, root?.nextPage);
  const hasNext =
    hasNextFromServer ??
    (nextPage !== null ? nextPage > (parsedPage ?? page) : null) ??
    (total !== null ? (parsedPage ?? page) * (parsedPageSize ?? pageSize) < total : null) ??
    items.length >= (parsedPageSize ?? pageSize);

  const normalizedPage = Math.max(1, parsedPage ?? page);

  return {
    items,
    page: normalizedPage,
    pageSize: Math.max(1, parsedPageSize ?? pageSize),
    total,
    hasNext,
    hasPrevious: normalizedPage > 1
  };
}
