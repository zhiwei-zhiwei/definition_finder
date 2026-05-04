"use client";

import { ReactNode, useMemo, useState } from "react";
import { Snippet } from "@/lib/api";

type Props = {
  snippets: Snippet[];
  onJump?: (page: number) => void;
};

export function SnippetList({ snippets, onJump }: Props) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});

  const allCollapsed = useMemo(
    () => snippets.length > 0 && snippets.every((s) => collapsed[s.id]),
    [snippets, collapsed],
  );

  function toggle(id: string) {
    setCollapsed((prev) => ({ ...prev, [id]: !prev[id] }));
  }

  function setAll(value: boolean) {
    const next: Record<string, boolean> = {};
    for (const s of snippets) next[s.id] = value;
    setCollapsed(next);
  }

  return (
    <div className="flex-1 overflow-y-auto custom-scroll pr-2 flex flex-col gap-3">
      <div className="sticky top-0 bg-background/90 backdrop-blur-sm py-2 z-10 flex justify-between items-center gap-2">
        <h3 className="font-headline font-semibold text-on-surface">Extracted Context</h3>
        <div className="flex items-center gap-2">
          {snippets.length > 0 && (
            <button
              type="button"
              onClick={() => setAll(!allCollapsed)}
              className="text-[11px] text-on-surface-variant hover:text-primary transition-colors"
            >
              {allCollapsed ? "Expand all" : "Collapse all"}
            </button>
          )}
          <span className="bg-surface-container text-on-surface-variant text-xs px-2 py-0.5 rounded-full">
            {snippets.length} {snippets.length === 1 ? "Match" : "Matches"}
          </span>
        </div>
      </div>

      {snippets.length === 0 && (
        <div className="text-sm text-on-surface-variant px-2">
          Ask a question on the right to see source snippets here.
        </div>
      )}

      {snippets.map((s) => {
        const isCollapsed = !!collapsed[s.id];
        const jumpable = s.page_start != null && !!onJump;
        const pageLabel = s.page_start != null ? `Page ${s.page_start}` : "Snippet";
        return (
          <div
            key={s.id}
            className="snippet-card shrink-0 rounded-lg bg-surface-container-low overflow-hidden"
          >
            <button
              type="button"
              onClick={() => toggle(s.id)}
              aria-expanded={!isCollapsed}
              aria-controls={`snippet-body-${s.id}`}
              className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-surface-container/70 transition-colors"
            >
              <Chevron expanded={!isCollapsed} />
              <span className="flex-1 min-w-0 flex items-baseline gap-2">
                <span className="text-sm font-bold text-on-surface shrink-0">{pageLabel}</span>
                <span className="text-[10px] tracking-wider uppercase text-on-surface-variant truncate">
                  {s.parent_id}
                </span>
              </span>
              <span className="text-[11px] font-medium text-on-surface-variant shrink-0">
                {(s.score * 100).toFixed(0)}%
              </span>
            </button>

            {!isCollapsed && (
              <div id={`snippet-body-${s.id}`} className="px-4 pb-4 pt-1">
                <p className="font-body text-sm leading-relaxed text-on-surface whitespace-pre-line">
                  <HighlightedText text={s.text} spans={s.highlight_spans} />
                </p>
                {jumpable && (
                  <button
                    type="button"
                    onClick={() => onJump!(s.page_start as number)}
                    className="mt-3 inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
                  >
                    Jump to page {s.page_start}
                    <span aria-hidden>→</span>
                  </button>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function HighlightedText({
  text,
  spans,
}: {
  text: string;
  spans?: Array<[number, number]>;
}) {
  if (!spans || spans.length === 0) return <>{text}</>;
  const parts: ReactNode[] = [];
  let cursor = 0;
  spans.forEach(([s, e], i) => {
    if (s > cursor) parts.push(text.slice(cursor, s));
    parts.push(
      <strong
        key={i}
        className="font-semibold text-on-surface bg-primary/15 rounded px-0.5"
      >
        {text.slice(s, e)}
      </strong>,
    );
    cursor = e;
  });
  if (cursor < text.length) parts.push(text.slice(cursor));
  return <>{parts}</>;
}

function Chevron({ expanded }: { expanded: boolean }) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={`shrink-0 text-on-surface-variant transition-transform duration-200 ${expanded ? "rotate-90" : ""}`}
    >
      <polyline points="9 18 15 12 9 6" />
    </svg>
  );
}
