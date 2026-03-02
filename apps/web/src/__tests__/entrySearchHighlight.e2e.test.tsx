import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import App from '@/App';
import { AuthProvider } from '@/auth/AuthProvider';

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      'Content-Type': 'application/json'
    }
  });
}

describe('E2E: create entry -> search -> open highlight', () => {
  const entryId = 'entry-e2e-001';
  const transcriptText = 'I completed the alpha rollout and documented the fix.';
  const searchTerm = 'alpha';
  const startChar = transcriptText.indexOf(searchTerm);
  const endChar = startChar + searchTerm.length;

  beforeEach(() => {
    localStorage.setItem('voicevault.accessToken', 'test-access-token');
    localStorage.setItem('voicevault.refreshToken', 'test-refresh-token');

    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const urlValue = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
        const url = new URL(urlValue, 'http://localhost');
        const method = (init?.method ?? 'GET').toUpperCase();

        if (url.pathname === '/api/v1/me' && method === 'GET') {
          return jsonResponse({ id: 'user-1', email: 'e2e@example.com' });
        }

        if (url.pathname === '/api/v1/entries' && method === 'GET') {
          return jsonResponse({ entries: [] });
        }

        if (url.pathname === '/api/v1/entries' && method === 'POST') {
          return jsonResponse({ entry_id: entryId, status: 'created' });
        }

        if (url.pathname === `/api/v1/entries/${entryId}/audio` && method === 'POST') {
          return new Response(null, { status: 202 });
        }

        if (url.pathname === `/api/v1/entries/${entryId}` && method === 'GET') {
          return jsonResponse({
            entry_id: entryId,
            status: 'ready',
            transcript: {
              transcript_text: transcriptText,
              version: 1,
              source: 'stt'
            },
            audio_url: 'https://example.invalid/audio.webm'
          });
        }

        if (url.pathname === '/api/v1/search' && method === 'GET') {
          return jsonResponse({
            results: [
              {
                entry_id: entryId,
                transcript_id: 'transcript-1',
                snippet_text: '...alpha rollout...',
                start_char: startChar,
                end_char: endChar,
                rank: 1
              }
            ]
          });
        }

        return jsonResponse({ detail: `Unhandled route: ${method} ${url.pathname}` }, 404);
      })
    );
  });

  it('creates an entry, finds a term, and opens the highlighted match', async () => {
    const user = userEvent.setup();
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false }
      }
    });

    render(
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <MemoryRouter initialEntries={['/app']}>
            <App />
          </MemoryRouter>
        </AuthProvider>
      </QueryClientProvider>
    );

    await screen.findByRole('heading', { name: 'VoiceVault App' });

    const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement | null;
    expect(fileInput).not.toBeNull();
    await user.upload(fileInput as HTMLInputElement, new File(['fake-audio'], 'note.webm', { type: 'audio/webm' }));

    await user.click(screen.getByRole('button', { name: 'Start upload pipeline' }));
    await screen.findByText('Entry status:');
    await screen.findByText('Skip for now');
    await user.click(screen.getByRole('button', { name: 'Skip for now' }));

    await user.type(screen.getByPlaceholderText('Search transcripts...'), searchTerm);
    await user.click(screen.getByRole('button', { name: 'Search' }));
    await screen.findByText('...alpha rollout...');

    await user.click(screen.getByRole('button', { name: 'Open highlight' }));

    await screen.findByRole('heading', { name: 'Entry Detail' });
    await screen.findByText(`Highlighted match for "${searchTerm}": ${startChar}-${endChar}`);

    await waitFor(() => {
      const highlight = document.querySelector('mark');
      expect(highlight).not.toBeNull();
      expect(highlight?.textContent).toBe(searchTerm);
    });
  });
});
