const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '';

type ErrorLevel = 'error' | 'warning';

type ErrorReportPayload = {
  message: string;
  stack?: string;
  source: string;
  level: ErrorLevel;
  url?: string;
  user_agent?: string;
};

function buildUrl(path: string): string {
  if (!API_BASE_URL) {
    return path;
  }

  return `${API_BASE_URL}${path}`;
}

function normalizeMessage(value: unknown, fallback: string): string {
  if (typeof value === 'string' && value.trim().length > 0) {
    return value;
  }

  if (value instanceof Error && value.message.trim().length > 0) {
    return value.message;
  }

  return fallback;
}

function normalizeStack(value: unknown): string | undefined {
  if (value instanceof Error && value.stack) {
    return value.stack;
  }
  return undefined;
}

export async function reportFrontendError(payload: ErrorReportPayload): Promise<void> {
  try {
    await fetch(buildUrl('/api/v1/observability/frontend-errors'), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(payload),
      keepalive: true,
      credentials: 'include'
    });
  } catch {
    // Intentionally swallow telemetry failures to avoid user-facing regressions.
  }
}

export function installGlobalErrorReporting(): void {
  window.addEventListener('error', (event) => {
    const message = normalizeMessage(event.error ?? event.message, 'Unhandled frontend error');
    const stack = normalizeStack(event.error);
    void reportFrontendError({
      message,
      stack,
      source: 'window.error',
      level: 'error',
      url: window.location.href,
      user_agent: window.navigator.userAgent
    });
  });

  window.addEventListener('unhandledrejection', (event) => {
    const message = normalizeMessage(event.reason, 'Unhandled promise rejection');
    const stack = normalizeStack(event.reason);
    void reportFrontendError({
      message,
      stack,
      source: 'window.unhandledrejection',
      level: 'error',
      url: window.location.href,
      user_agent: window.navigator.userAgent
    });
  });
}
