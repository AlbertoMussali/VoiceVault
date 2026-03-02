import { ApiError, apiFetch, getAccessToken, refreshSession } from '@/lib/auth';

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '';

export type DataExportJob = {
  id: string;
  status: string;
  exportType: string | null;
  format: string | null;
  createdAt: string | null;
  updatedAt: string | null;
  downloadUrl: string | null;
  errorMessage: string | null;
};

function buildUrl(path: string): string {
  if (!API_BASE_URL) {
    return path;
  }

  return `${API_BASE_URL}${path}`;
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

function parseDataExportJob(payload: unknown): DataExportJob | null {
  const data = asRecord(payload);
  if (!data) {
    return null;
  }

  const jobPayload = asRecord(data.job) ?? asRecord(data.export_job) ?? data;
  const id = pickString(jobPayload.id);
  const status = pickString(jobPayload.status);
  if (!id || !status) {
    return null;
  }

  return {
    id,
    status,
    exportType: pickString(jobPayload.export_type, jobPayload.exportType),
    format: pickString(jobPayload.format),
    createdAt: pickString(jobPayload.created_at, jobPayload.createdAt),
    updatedAt: pickString(jobPayload.updated_at, jobPayload.updatedAt),
    downloadUrl: pickString(jobPayload.download_url, jobPayload.downloadUrl),
    errorMessage: pickString(jobPayload.error_message, jobPayload.errorMessage)
  };
}

export async function requestDataExportJob(): Promise<DataExportJob> {
  const payload = await apiFetch<unknown>('/api/v1/exports', {
    method: 'POST'
  });
  const job = parseDataExportJob(payload);
  if (!job) {
    throw new ApiError('Export response did not include a valid job payload.', 500);
  }
  return job;
}

export async function fetchDataExportJob(jobId: string): Promise<DataExportJob> {
  const payload = await apiFetch<unknown>(`/api/v1/exports/${jobId}`, {
    method: 'GET'
  });
  const job = parseDataExportJob(payload);
  if (!job) {
    throw new ApiError('Export status response was invalid.', 500);
  }
  return job;
}

async function authorizedDownloadRequest(path: string, retry = true): Promise<Response> {
  const headers = new Headers();
  const token = getAccessToken();
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  const response = await fetch(buildUrl(path), {
    method: 'GET',
    headers,
    credentials: 'include'
  });

  if (response.status === 401 && retry) {
    const refreshed = await refreshSession();
    if (refreshed) {
      return authorizedDownloadRequest(path, false);
    }
  }

  return response;
}

function extractFilename(response: Response): string {
  const fallback = 'voicevault-data-export.zip';
  const disposition = response.headers.get('content-disposition');
  if (!disposition) {
    return fallback;
  }

  const match = disposition.match(/filename="([^"]+)"/i);
  const candidate = match?.[1]?.trim();
  return candidate && candidate.length > 0 ? candidate : fallback;
}

export async function downloadDataExportArtifact(job: DataExportJob): Promise<void> {
  const path = job.downloadUrl ?? `/api/v1/exports/${job.id}/download`;
  const response = await authorizedDownloadRequest(path);
  if (!response.ok) {
    throw new ApiError('Failed to download export artifact.', response.status);
  }

  const blob = await response.blob();
  const filename = extractFilename(response);
  const objectUrl = URL.createObjectURL(blob);

  try {
    const link = document.createElement('a');
    link.href = objectUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}
