"use client";

import { useEffect, useRef, useState } from "react";
import {
  CachedQueryRow,
  DocumentRecord,
  deleteDocument,
  listDocuments,
  listQueries,
  uploadDocument,
} from "@/lib/api";
import { useAuth } from "@/components/AuthContext";

type Props = {
  open: boolean;
  onClose: () => void;
  activeDocId: string | null;
  onSelect: (doc: DocumentRecord) => void;
  onActiveDeleted: () => void;
  onOpenLogin: () => void;
  onUploaded?: (doc: DocumentRecord) => void;
  onSelectQuery: (queryId: string) => void;
  /** Bumped by the parent when something external should force a queries-tab refetch (e.g. a new query just finished). */
  queriesRefreshKey?: number;
};

type Tab = "files" | "queries";

export function RecentFilesDrawer({
  open,
  onClose,
  activeDocId,
  onSelect,
  onActiveDeleted,
  onOpenLogin,
  onUploaded,
  onSelectQuery,
  queriesRefreshKey = 0,
}: Props) {
  const { user } = useAuth();
  const [docs, setDocs] = useState<DocumentRecord[]>([]);
  const [queries, setQueries] = useState<CachedQueryRow[]>([]);
  const [tab, setTab] = useState<Tab>("files");
  const [uploadingName, setUploadingName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  async function refreshDocs() {
    try {
      const rows = await listDocuments();
      setDocs(rows);
    } catch (e: any) {
      setError(e.message ?? "failed to list");
    }
  }

  async function refreshQueries() {
    if (!user || !activeDocId) {
      setQueries([]);
      return;
    }
    try {
      const rows = await listQueries(activeDocId);
      setQueries(rows);
    } catch (e: any) {
      setError(e.message ?? "failed to list queries");
    }
  }

  useEffect(() => {
    if (!open) return;
    if (user) {
      refreshDocs();
    } else {
      setDocs([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, user?.user_id]);

  useEffect(() => {
    if (!open || tab !== "queries") return;
    refreshQueries();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, tab, user?.user_id, activeDocId, queriesRefreshKey]);

  async function handleUpload(file: File) {
    setUploadingName(file.name);
    setError(null);
    try {
      const doc = await uploadDocument(file);
      if (user) await refreshDocs();
      onUploaded?.(doc);
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
      await refreshDocs();
    } catch (e: any) {
      setError(e.message ?? "failed to delete");
    }
  }

  return (
    <>
      <div
        className={`fixed inset-0 bg-black/20 z-40 transition-opacity ${
          open
            ? "opacity-100 pointer-events-auto"
            : "opacity-0 pointer-events-none"
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
            <span className="material-symbols-outlined text-primary">
              folder_open
            </span>
            <h2 className="font-headline font-semibold text-on-surface text-lg">
              Recent Files
            </h2>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-surface-container text-on-surface-variant"
          >
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
            <span className="material-symbols-outlined text-[16px]">
              upload
            </span>
            {uploadingName ? `Uploading: ${uploadingName}` : "Upload document"}
          </button>
          {error && <div className="mt-2 text-xs text-error">{error}</div>}
        </div>

        {!user && (
          <div className="p-4 m-4 rounded-xl bg-surface-container-low border border-outline-variant/40 flex flex-col gap-2 items-start">
            <div className="flex items-center gap-2 text-on-surface">
              <span className="material-symbols-outlined text-primary text-[18px]">
                lock
              </span>
              <span className="font-headline font-semibold text-sm">
                Sign in to save your work
              </span>
            </div>
            <p className="text-xs text-on-surface-variant">
              Anonymous uploads stay only in this browser tab. Sign in to keep
              your files and revisit past queries.
            </p>
            <button
              onClick={onOpenLogin}
              className="mt-1 px-3 py-1.5 text-xs font-medium text-blue-700 border border-blue-200 rounded-lg hover:bg-blue-50"
            >
              Sign in
            </button>
          </div>
        )}

        {user && (
          <div className="px-4 pt-3 flex gap-1 border-b border-outline-variant/30">
            <TabButton
              active={tab === "files"}
              onClick={() => setTab("files")}
              label="Files"
            />
            <TabButton
              active={tab === "queries"}
              onClick={() => setTab("queries")}
              label="Queries"
            />
          </div>
        )}

        <div className="flex-1 overflow-y-auto custom-scroll p-2">
          {user && tab === "files" && (
            <>
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
                    activeDocId === d.id
                      ? "bg-surface-container-low ring-1 ring-primary/30"
                      : ""
                  }`}
                >
                  <div className="flex-1 min-w-0">
                    <div className="font-medium text-sm text-on-surface truncate">
                      {d.filename}
                    </div>
                    <div className="text-[10px] uppercase tracking-wide text-on-surface-variant">
                      {d.status} · {d.pages ?? "—"} pages ·{" "}
                      {(d.size_bytes / 1024).toFixed(0)} KB
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
            </>
          )}

          {user && tab === "queries" && !activeDocId && (
            <div className="p-6 text-center text-sm text-on-surface-variant">
              Pick a document to see its query history.
            </div>
          )}

          {user && tab === "queries" && activeDocId && queries.length === 0 && (
            <div className="p-6 text-center text-sm text-on-surface-variant">
              No saved queries yet for this document.
            </div>
          )}

          {user &&
            tab === "queries" &&
            activeDocId &&
            queries.length > 0 &&
            queries.map((q) => (
              <button
                key={q.id}
                onClick={() => {
                  onSelectQuery(q.id);
                  onClose();
                }}
                className="w-full text-left p-3 rounded-lg mb-1 flex flex-col gap-1 hover:bg-surface-container-low transition-colors"
              >
                <div className="font-medium text-sm text-on-surface line-clamp-2">
                  {q.query_text.length > 80
                    ? q.query_text.slice(0, 80) + "…"
                    : q.query_text}
                </div>
                {q.key_takeaway && (
                  <div className="text-xs text-on-surface-variant line-clamp-2">
                    {q.key_takeaway}
                  </div>
                )}
                <div className="text-[10px] uppercase tracking-wide text-on-surface-variant">
                  {q.created_at ? new Date(q.created_at).toLocaleString() : ""}
                </div>
              </button>
            ))}
        </div>
      </aside>
    </>
  );
}

function TabButton({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-1.5 text-sm font-medium rounded-t-md ${
        active
          ? "text-blue-700 border-b-2 border-blue-600 -mb-px"
          : "text-on-surface-variant hover:text-on-surface"
      }`}
    >
      {label}
    </button>
  );
}
