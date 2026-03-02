import { describe, expect, it, vi } from 'vitest';

import { fetchEntriesTimeline } from '@/lib/entries';

describe('entries API network error handling', () => {
  it('returns a user-friendly error when API is unreachable', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('NetworkError when attempting to fetch resource.');
      })
    );

    await expect(fetchEntriesTimeline()).rejects.toMatchObject({
      message: expect.stringContaining('Cannot reach the VoiceVault API.'),
      status: 0
    });
  });
});
