import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
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

describe('E2E: onboarding guide', () => {
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
          return jsonResponse({ id: 'user-1', email: 'onboarding@example.com' });
        }

        if (url.pathname === '/api/v1/entries' && method === 'GET') {
          return jsonResponse({ entries: [] });
        }

        return jsonResponse({ detail: `Unhandled route: ${method} ${url.pathname}` }, 404);
      })
    );
  });

  it('shows guided setup for new users and can be hidden/restored', async () => {
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
    expect(screen.getByRole('heading', { name: 'Guided setup' })).toBeInTheDocument();
    expect(screen.getByText('0 of 4 steps complete.')).toBeInTheDocument();
    expect(screen.getByText('No entries yet.')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Hide' }));
    expect(screen.queryByRole('heading', { name: 'Guided setup' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Show setup guide' }));
    expect(screen.getByRole('heading', { name: 'Guided setup' })).toBeInTheDocument();
  });
});
