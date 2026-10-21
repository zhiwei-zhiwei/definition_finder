"use client";

import ReactMarkdown from "react-markdown";
import { Verification } from "@/lib/types";

type Props = {
  text: string;
  streaming: boolean;
  verification: Verification | null;
};

const BANNER: Record<Verification["status"], { icon: string; title: string; cls: string }> = {
  ok: {
    icon: "verified",
    title: "Cited pages and numbers match the excerpts",
    cls: "bg-primary/5 text-primary",
  },
  warning: {
    icon: "warning",
    title: "Needs review",
    cls: "bg-tertiary-fixed/60 text-on-tertiary-fixed-variant",
  },
  insufficient_evidence: {
    icon: "help",
    title: "Not enough evidence in this document. Review before relying on it",
    cls: "bg-surface-container-high text-on-surface-variant",
  },
};

// Shown once the answer has streamed: verification runs after the last token.
function VerificationBanner({ v }: { v: Verification }) {
  const b = BANNER[v.status];
  return (
    <div className={`mb-4 p-3 rounded-lg text-xs ${b.cls}`} role="status">
      <div className="flex items-center gap-2 font-semibold">
        <span className="material-symbols-outlined text-[16px]">{b.icon}</span>
        {b.title}
      </div>
      {v.issues.length > 0 && (
        <ul className="mt-1 ml-6 list-disc">
          {v.issues.map((i) => (
            <li key={i.kind}>{i.detail}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function splitKeyTakeaway(md: string): { body: string; takeaway: string | null } {
  const marker = "**Key Takeaway:**";
  const idx = md.indexOf(marker);
  if (idx < 0) return { body: md, takeaway: null };
  return {
    body: md.slice(0, idx).trimEnd(),
    takeaway: md.slice(idx + marker.length).trim(),
  };
}

export function SummaryCard({ text, streaming, verification }: Props) {
  const { body, takeaway } = splitKeyTakeaway(text);
  const empty = text.trim().length === 0;
  return (
    <div className="flex-1 bg-surface-container-lowest rounded-lg p-6 ambient-shadow overflow-y-auto custom-scroll flex flex-col">
      <div className="flex items-center gap-3 mb-6">
        <div className="w-8 h-8 rounded-full gradient-primary flex items-center justify-center text-white shrink-0">
          <span className="material-symbols-outlined text-sm">auto_awesome</span>
        </div>
        <h3 className="font-headline font-semibold text-on-surface text-lg">Executive Summary</h3>
        {streaming && <span className="text-xs text-primary animate-pulse ml-auto">streaming…</span>}
      </div>
      {verification && !streaming && <VerificationBanner v={verification} />}
      <div className="prose prose-sm prose-slate font-body text-on-surface flex-1 max-w-none">
        {empty ? (
          <p className="text-on-surface-variant">
            Upload a document and ask a question to see a streamed summary here.
          </p>
        ) : (
          <ReactMarkdown>{body}</ReactMarkdown>
        )}
        {takeaway && (
          <div className="mt-6 p-4 bg-surface-container-low rounded-lg border-l-2 border-primary not-prose">
            <h4 className="text-sm font-semibold mb-2 flex items-center gap-2">
              <span className="material-symbols-outlined text-[14px] text-primary">lightbulb</span>
              Key Takeaway
            </h4>
            <p className="text-xs text-on-surface-variant leading-relaxed">{takeaway}</p>
          </div>
        )}
      </div>
      <div className="mt-6 pt-4 border-t border-outline-variant/20 flex justify-end gap-3">
        <button className="text-sm font-medium text-on-surface-variant hover:text-primary transition-colors flex items-center gap-1">
          <span className="material-symbols-outlined text-[16px]">thumb_up</span> Helpful
        </button>
        <button
          className="text-sm font-medium text-on-surface-variant hover:text-primary transition-colors flex items-center gap-1"
          onClick={() => navigator.clipboard.writeText(text)}
          disabled={empty}
        >
          <span className="material-symbols-outlined text-[16px]">content_copy</span> Copy
        </button>
      </div>
    </div>
  );
}
