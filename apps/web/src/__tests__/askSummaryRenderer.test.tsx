import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { AskSummaryRenderer, type AskSummaryCitation, type AskSummarySentence } from '@/components/AskSummaryRenderer';

describe('AskSummaryRenderer', () => {
  it('renders sentence-level citations and opens linked highlights', async () => {
    const user = userEvent.setup();
    const onOpenCitation = vi.fn();

    const citation: AskSummaryCitation = {
      snippetId: 'entry-1:10:24:0',
      entryId: 'entry-1',
      startChar: 10,
      endChar: 24,
      snippetText: 'Shipped onboarding checklist update.'
    };

    const sentences: AskSummarySentence[] = [
      {
        id: 'sentence-1',
        text: 'You delivered onboarding improvements this week.',
        citationSnippetIds: [citation.snippetId]
      }
    ];

    render(
      <AskSummaryRenderer
        sentences={sentences}
        citationsBySnippetId={{ [citation.snippetId]: citation }}
        onOpenCitation={onOpenCitation}
      />
    );

    expect(screen.getByText('You delivered onboarding improvements this week.')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Open citation 1.1' }));

    expect(onOpenCitation).toHaveBeenCalledTimes(1);
    expect(onOpenCitation).toHaveBeenCalledWith(citation);
  });
});
