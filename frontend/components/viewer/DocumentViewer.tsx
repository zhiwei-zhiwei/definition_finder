"use client";

import { DocumentRecord } from "@/lib/api";
import { HtmlViewer } from "./HtmlViewer";
import { PdfViewer } from "./PdfViewer";

export function DocumentViewer({
  doc,
  page,
}: {
  doc: DocumentRecord | null;
  page: number | null;
}) {
  if (!doc) {
    return (
      <div className="flex-1 flex items-center justify-center text-on-surface-variant text-sm p-6 text-center">
        <div>
          <span className="material-symbols-outlined text-4xl block mb-2">upload_file</span>
          Open the drawer (top-left) to upload or pick a document.
        </div>
      </div>
    );
  }
  const isPdf = doc.mime === "application/pdf" || doc.filename.toLowerCase().endsWith(".pdf");
  return isPdf ? <PdfViewer doc={doc} page={page} /> : <HtmlViewer doc={doc} />;
}
