"use client";

import { useState } from "react";
import { RecentFilesDrawer } from "@/components/RecentFilesDrawer";
import { TopNav } from "@/components/TopNav";
import { LoginModal } from "@/components/LoginModal";
import { PipelineVisualizer } from "@/components/process/PipelineVisualizer";
import { SnippetList } from "@/components/process/SnippetList";
import { QueryInput } from "@/components/query/QueryInput";
import { SummaryCard } from "@/components/query/SummaryCard";
import { DocumentViewer } from "@/components/viewer/DocumentViewer";
import { useAuth } from "@/components/AuthContext";
import {
  DocumentRecord,
  Snippet,
  getDocument,
  getQuery,
  runQuery,
} from "@/lib/api";
import { clearAnonDocId, setAnonDocId } from "@/lib/auth";
import { StageEvent } from "@/lib/types";

export default function HomePage() {
  const { user } = useAuth();
  const [drawerOpen, setDrawerOpen] = useState(true);
  const [loginOpen, setLoginOpen] = useState(false);
  const [doc, setDoc] = useState<DocumentRecord | null>(null);
  const [snippets, setSnippets] = useState<Snippet[]>([]);
  const [pageTarget, setPageTarget] = useState<number | null>(null);

  const [stage, setStage] = useState<string | null>(null);
  const [stagePayload, setStagePayload] = useState<StageEvent | null>(null);
  const [summary, setSummary] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [queryError, setQueryError] = useState<string | null>(null);
  const [queriesRefreshKey, setQueriesRefreshKey] = useState(0);

  async function onAnalyze(query: string, topK: number) {
    if (!doc) return;
    setStreaming(true);
    setStage("embedding_query");
    setStagePayload(null);
    setSnippets([]);
    setSummary("");
    setQueryError(null);

    await runQuery(doc.id, query, topK, {
      onStage: (payload) => {
        setStage(payload.stage);
        setStagePayload(payload);
      },
      onSnippets: (items) => setSnippets(items),
      onToken: (tok) => setSummary((prev) => prev + tok),
      onDone: () => {
        setStreaming(false);
        setStage("done");
        setQueriesRefreshKey((k) => k + 1);
      },
      onError: (msg) => {
        setQueryError(msg);
        setStreaming(false);
      },
    });
  }

  async function onSelectQuery(queryId: string) {
    try {
      const cached = await getQuery(queryId);
      // Make sure the active doc matches the cached query's doc.
      if (!doc || doc.id !== cached.doc_id) {
        try {
          const d = await getDocument(cached.doc_id);
          setDoc(d);
        } catch {
          // ignore — fall through with current doc
        }
      }
      setSnippets(cached.snippets);
      setSummary(cached.summary_text);
      setStage("done");
      setStagePayload(null);
      setStreaming(false);
      setQueryError(null);
    } catch (e: any) {
      setQueryError(e?.message ?? "failed to load cached query");
    }
  }

  function resetWorkspace() {
    setDoc(null);
    setSnippets([]);
    setSummary("");
    setStage(null);
    setStagePayload(null);
    setPageTarget(null);
    setQueryError(null);
    setStreaming(false);
    clearAnonDocId();
  }

  function handleUploaded(uploaded: DocumentRecord) {
    if (!user) setAnonDocId(uploaded.id);
  }

  return (
    <>
      <TopNav
        onToggleDrawer={() => setDrawerOpen((o) => !o)}
        onOpenLogin={() => setLoginOpen(true)}
      />
      <RecentFilesDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        activeDocId={doc?.id ?? null}
        onSelect={(d) => {
          setDoc(d);
          setSnippets([]);
          setSummary("");
          setStage(null);
          setPageTarget(null);
        }}
        onActiveDeleted={resetWorkspace}
        onOpenLogin={() => setLoginOpen(true)}
        onUploaded={handleUploaded}
        onSelectQuery={onSelectQuery}
        queriesRefreshKey={queriesRefreshKey}
      />
      <LoginModal
        open={loginOpen}
        onClose={() => setLoginOpen(false)}
        onLoggedIn={(claimed) => {
          // The just-claimed doc is now owned by the user; refresh keys.
          setQueriesRefreshKey((k) => k + 1);
          if (doc && claimed.includes(doc.id)) {
            // no-op: the doc id is unchanged, ownership flipped server-side
          }
        }}
      />
      <main className="flex-1 flex overflow-hidden w-full max-w-[1920px] mx-auto p-4 gap-6">
        <section className="w-1/2 flex flex-col bg-surface-container rounded-lg ambient-shadow overflow-hidden relative">
          <DocumentViewer doc={doc} page={pageTarget} />
        </section>
        <section className="w-1/4 flex flex-col gap-6 overflow-hidden">
          <PipelineVisualizer
            activeStage={stage}
            lastPayload={stagePayload}
            snippetCount={snippets.length}
            finished={stage === "done"}
          />
          <SnippetList snippets={snippets} onJump={setPageTarget} />
        </section>
        <section className="w-1/4 flex flex-col gap-6 overflow-hidden">
          <QueryInput disabled={!doc} busy={streaming} onSubmit={onAnalyze} />
          {queryError && (
            <div className="text-sm text-error bg-error-container/40 rounded-lg p-3">
              {queryError}
            </div>
          )}
          <SummaryCard text={summary} streaming={streaming} />
        </section>
      </main>
    </>
  );
}
