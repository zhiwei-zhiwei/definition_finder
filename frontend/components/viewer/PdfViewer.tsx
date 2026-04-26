"use client";

import { API_BASE, DocumentRecord } from "@/lib/api";

export function PdfViewer({ doc, page }: { doc: DocumentRecord; page: number | null }) {
  const src = `${API_BASE}/documents/${doc.id}/file${page ? `#page=${page}` : ""}`;
  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between p-3 border-b border-outline-variant/30 bg-surface-container-low">
        <div className="flex items-center gap-2 text-sm text-on-surface-variant truncate">
          <span className="material-symbols-outlined text-on-surface-variant">description</span>
          <span className="truncate">{doc.filename}</span>
        </div>
      </div>
      <iframe
        key={page ?? "no-page"}
        src={src}
        className="flex-1 w-full bg-surface-container-low border-0"
        title={doc.filename}
      />
    </div>
  );
}
