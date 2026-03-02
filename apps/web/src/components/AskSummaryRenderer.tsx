import { Button } from '@/components/ui/Button';

export type AskSummaryCitation = {
  snippetId: string;
  entryId: string;
  startChar: number;
  endChar: number;
  snippetText: string;
};

export type AskSummarySentence = {
  id: string;
  text: string;
  citationSnippetIds: string[];
};

type AskSummaryRendererProps = {
  sentences: AskSummarySentence[];
  citationsBySnippetId: Record<string, AskSummaryCitation>;
  onOpenCitation: (citation: AskSummaryCitation) => void;
};

export function AskSummaryRenderer({ sentences, citationsBySnippetId, onOpenCitation }: AskSummaryRendererProps) {
  if (sentences.length === 0) {
    return <p className="text-sm text-muted-foreground">No summary sentences available yet.</p>;
  }

  return (
    <ol className="space-y-3">
      {sentences.map((sentence, sentenceIndex) => {
        const sentenceCitations = sentence.citationSnippetIds
          .map((snippetId) => citationsBySnippetId[snippetId])
          .filter((citation): citation is AskSummaryCitation => Boolean(citation));

        return (
          <li key={sentence.id} className="rounded-md border bg-muted/10 p-3">
            <p className="text-sm leading-6">{sentence.text}</p>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <span className="text-xs text-muted-foreground">Evidence:</span>
              {sentenceCitations.length > 0 ? (
                sentenceCitations.map((citation, citationIndex) => (
                  <Button
                    key={`${sentence.id}-${citation.snippetId}`}
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => onOpenCitation(citation)}
                    title={citation.snippetText}
                    aria-label={`Open citation ${sentenceIndex + 1}.${citationIndex + 1}`}
                  >
                    [{sentenceIndex + 1}.{citationIndex + 1}]
                  </Button>
                ))
              ) : (
                <span className="text-xs text-destructive">Missing citation mapping</span>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
