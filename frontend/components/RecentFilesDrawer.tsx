"use client";

import { useEffect, useRef, useState } from "react";
import {
  DocumentRecord,
  deleteDocument,
  listDocuments,
  uploadDocument,
} from "@/lib/api";

type Props = {
  open: boolean;
  onClose: () => void;
  activeDocId: string | null;
  onSelect: (doc: DocumentRecord) => void;
  onActiveDeleted: () => void;
};

export function RecentFilesDrawer({
  open,
  onClose,
  activeDocId,
  onSelect,
  onActiveDeleted,
}: Props) {
  const [docs, setDocs] = useState<DocumentRecord[]>([]);
  const [uploadingName, setUploadingName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  async function refresh() {
    try {
      const rows = await listDocuments();
      setDocs(rows);
    } catch (e: any) {
      setError(e.message ?? "failed to list");
    }
  }

  useEffect(() => {
    if (open) refresh();
  }, [open]);

  async function handleUpload(file: File) {
    setUploadingName(file.name);
    setError(null);
    try {
      const doc = await uploadDocument(file);
      await refresh();
      onSelect(doc);
      onClose();
    } catch (e: any) {
      setError(e.message ?? "upload failed");
    } finally {
      setUploadingName(null);
    }
  }

  async function handleDelete(id: string) {
    try {
      await deleteDocument(id);
      if (id === activeDocId) onActiveDeleted();
      await refresh();
    } catch (e: any) {
      setError(e.message ?? "failed to delete");
    }
  }

  return (
    <>
      <div
        className={`fixed inset-0 bg-black/20 z-40 transition-opacity ${
          open ? "opacity-100 pointer-events-auto" : "opacity-0 pointer-events-none"
        }`}
        onClick={onClose}
      />
      <aside
        className={`fixed left-0 top-0 h-full w-96 bg-surface-container-lowest z-50 shadow-2xl transition-transform flex flex-col ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center justify-between p-4 border-b border-outline-variant/30">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-primary">folder_open</span>
            <h2 className="font-headline font-semibold text-on-surface text-lg">Recent Files</h2>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-surface-container text-on-surface-variant">
            <span className="material-symbols-outlined text-sm">close</span>
          </button>
        </div>

        <div className="p-4 border-b border-outline-variant/30">
          <input
            ref={fileInputRef}
            type="file"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) handleUpload(f);
              if (fileInputRef.current) fileInputRef.current.value = "";
            }}
          />
          <button
            disabled={!!uploadingName}
            onClick={() => fileInputRef.current?.click()}
            className="w-full gradient-primary text-white text-sm font-medium px-4 py-2.5 rounded-xl flex items-center justify-center gap-2 hover:opacity-90 disabled:opacity-50"
          >
            <span className="material-symbols-outlined text-[16px]">upload</span>
            {uploadingName ? `Uploading: ${uploadingName}` : "Upload document"}
          </button>
          {error && <div className="mt-2 text-xs text-error">{error}</div>}
        </div>

        <div className="flex-1 overflow-y-auto custom-scroll p-2">
          {docs.length === 0 && (
            <div className="p-6 text-center text-sm text-on-surface-variant">
              No documents yet. Upload one to begin.
            </div>
          )}
          {docs.map((d) => (
            <button
              key={d.id}
              onClick={() => {
                onSelect(d);
                onClose();
              }}
              className={`w-full text-left p-3 rounded-lg mb-1 flex items-start justify-between gap-2 hover:bg-surface-container-low transition-colors ${
                activeDocId === d.id ? "bg-surface-container-low ring-1 ring-primary/30" : ""
              }`}
            >
              <div className="flex-1 min-w-0">
                <div className="font-medium text-sm text-on-surface truncate">{d.filename}</div>
                <div className="text-[10px] uppercase tracking-wide text-on-surface-variant">
                  {d.status} · {d.pages ?? "—"} pages · {(d.size_bytes / 1024).toFixed(0)} KB
                </div>
              </div>
              <span
                role="button"
                aria-label="Delete"
                onClick={(e) => {
                  e.stopPropagation();
                  handleDelete(d.id);
                }}
                className="material-symbols-outlined text-on-surface-variant hover:text-error text-[18px]"
              >
                delete
              </span>
            </button>
          ))}
        </div>
      </aside>
    </>
  );
}
