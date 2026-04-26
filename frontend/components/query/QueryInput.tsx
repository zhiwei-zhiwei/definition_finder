"use client";

import { useState } from "react";

type Props = {
  disabled?: boolean;
  busy?: boolean;
  onSubmit: (query: string, topK: number) => void;
};

export function QueryInput({ disabled, busy, onSubmit }: Props) {
  const [text, setText] = useState("");
  const [topK, setTopK] = useState(5);

  function go() {
    const q = text.trim();
    if (!q || disabled) return;
    onSubmit(q, topK);
  }

  return (
    <div className="bg-surface-container-lowest rounded-lg p-5 ambient-shadow shrink-0 relative group">
      <label className="font-headline font-semibold text-on-surface block mb-3 text-sm" htmlFor="ai-query">
        Ask LexisAI
      </label>
      <div className="relative">
        <textarea
          id="ai-query"
          className="w-full bg-surface-container-low border-none rounded-md p-4 text-sm text-on-surface placeholder:text-outline-variant focus:ring-1 focus:ring-primary/20 transition-all custom-scroll resize-none"
          rows={3}
          placeholder="E.g., What is the definition of 'deferred revenue'?"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              go();
            }
          }}
          disabled={disabled}
        />
      </div>
      <div className="mt-4 flex justify-between items-center gap-3">
        <div className="flex items-center gap-2 text-xs text-on-surface-variant">
          <label htmlFor="topk">Top-K</label>
          <input
            id="topk"
            type="range"
            min={3}
            max={20}
            value={topK}
            onChange={(e) => setTopK(Number(e.target.value))}
            className="accent-primary"
          />
          <span className="font-medium tabular-nums w-6 text-center">{topK}</span>
        </div>
        <button
          disabled={disabled || busy || text.trim().length === 0}
          onClick={go}
          className="gradient-primary text-white text-sm font-medium px-4 py-2 rounded-xl flex items-center gap-2 hover:opacity-90 disabled:opacity-40 transition-opacity"
        >
          {busy ? "Analyzing..." : "Analyze"}
          <span className="material-symbols-outlined text-[16px]">arrow_forward</span>
        </button>
      </div>
    </div>
  );
}
