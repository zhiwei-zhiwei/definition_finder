"use client";

import { Snippet } from "@/lib/api";

type Props = {
  snippets: Snippet[];
  onJump?: (page: number) => void;
};

export function SnippetList({ snippets, onJump }: Props) {
  return (
    <div className="flex-1 overflow-y-auto custom-scroll pr-2 flex flex-col gap-4">
      <h3 className="font-headline font-semibold text-on-surface sticky top-0 bg-background/90 backdrop-blur-sm py-2 z-10 flex justify-between items-center">
        Extracted Context
        <span className="bg-surface-container text-on-surface-variant text-xs px-2 py-0.5 rounded-full">
          {snippets.length} {snippets.length === 1 ? "Match" : "Matches"}
        </span>
      </h3>
      {snippets.length === 0 && (
        <div className="text-sm text-on-surface-variant px-2">
          Ask a question on the right to see source snippets here.
        </div>
      )}
      {snippets.map((s) => {
        const pageLabel = s.page_start != null ? `Page ${s.page_start}` : s.id;
        const jumpable = s.page_start != null && !!onJump;
        return (
          <button
            key={s.id}
            type="button"
            disabled={!jumpable}
            onClick={() => jumpable && onJump!(s.page_start as number)}
            title={jumpable ? `Jump to page ${s.page_start}` : undefined}
            className="snippet-card text-left rounded-lg p-4 bg-surface-container-low hover:bg-surface-container transition-colors enabled:hover:ring-1 enabled:hover:ring-primary/40 disabled:cursor-default"
          >
            <div className="flex justify-between items-start mb-2">
              <span className="text-[10px] font-bold tracking-wider uppercase text-on-surface-variant">
                {pageLabel} · {s.parent_id}
              </span>
              <span className="text-[10px] text-on-surface-variant">{(s.score * 100).toFixed(0)}%</span>
            </div>
            <p className="font-body text-sm leading-relaxed text-on-surface">{s.text}</p>
          </button>
        );
      })}
    </div>
  );
}
