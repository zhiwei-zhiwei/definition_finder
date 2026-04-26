"use client";

import DOMPurify from "dompurify";
import { useEffect, useState } from "react";
import { API_BASE, DocumentRecord } from "@/lib/api";

export function HtmlViewer({ doc }: { doc: DocumentRecord }) {
  const [html, setHtml] = useState<string>("");

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const r = await fetch(`${API_BASE}/documents/${doc.id}/html`);
        if (!r.ok) throw new Error(`html fetch: ${r.status}`);
        const txt = await r.text();
        if (!active) return;
        setHtml(DOMPurify.sanitize(txt));
      } catch {
        if (!active) return;
        setHtml("<p>Failed to render document.</p>");
      }
    })();
    return () => {
      active = false;
    };
  }, [doc.id]);

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between p-3 border-b border-outline-variant/30 bg-surface-container-low">
        <div className="flex items-center gap-2 text-sm text-on-surface-variant truncate">
          <span className="material-symbols-outlined text-on-surface-variant">description</span>
          <span className="truncate">{doc.filename}</span>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto custom-scroll p-8 bg-surface-container-low flex justify-center">
        <div
          className="bg-surface-container-lowest w-full max-w-3xl shadow-sm rounded-md p-10 prose prose-sm prose-slate max-w-none font-body text-on-surface"
          dangerouslySetInnerHTML={{ __html: html }}
        />
      </div>
    </div>
  );
}
