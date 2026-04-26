"use client";

import { useState } from "react";
import { RecentFilesDrawer } from "@/components/RecentFilesDrawer";
import { TopNav } from "@/components/TopNav";
import { PipelineVisualizer } from "@/components/process/PipelineVisualizer";
import { SnippetList } from "@/components/process/SnippetList";
import { QueryInput } from "@/components/query/QueryInput";
import { SummaryCard } from "@/components/query/SummaryCard";
import { DocumentViewer } from "@/components/viewer/DocumentViewer";
import { DocumentRecord, Snippet, runQuery } from "@/lib/api";
import { StageEvent } from "@/lib/types";

export default function HomePage() {
  const [drawerOpen, setDrawerOpen] = useState(true);
  const [doc, setDoc] = useState<DocumentRecord | null>(null);
  const [snippets, setSnippets] = useState<Snippet[]>([]);
  const [pageTarget, setPageTarget] = useState<number | null>(null);

  const [stage, setStage] = useState<string | null>(null);
  const [stagePayload, setStagePayload] = useState<StageEvent | null>(null);
  const [summary, setSummary] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [queryError, setQueryError] = useState<string | null>(null);

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
      },
      onError: (msg) => {
        setQueryError(msg);
        setStreaming(false);
      },
    });
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
  }

  return (
    <>
      <TopNav onToggleDrawer={() => setDrawerOpen((o) => !o)} />
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
            <div className="text-sm text-error bg-error-container/40 rounded-lg p-3">{queryError}</div>
          )}
          <SummaryCard text={summary} streaming={streaming} />
        </section>
      </main>
    </>
  );
}
