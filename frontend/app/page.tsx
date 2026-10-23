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
  SseHandlers,
  Snippet,
  getDocument,
  getQuery,
  resumeQuery,
  runQuery,
} from "@/lib/api";
import { clearAnonDocId, setAnonDocId } from "@/lib/auth";
import { Review, ReviewAction, ReviewVerdict, StageEvent, Verification } from "@/lib/types";

export default function HomePage() {
  const { user } = useAuth();
  const [drawerOpen, setDrawerOpen] = useState(true);
  const [loginOpen, setLoginOpen] = useState(false);
  const [doc, setDoc] = useState<DocumentRecord | null>(null);
  const [snippets, setSnippets] = useState<Snippet[]>([]);
  const [pageTarget, setPageTarget] = useState<number | null>(null);
  // Bumped after a query that prepared the doc, so the HTML viewer refetches the
  // render that preparation just wrote (before it, /html serves a placeholder).
  const [docVersion, setDocVersion] = useState(0);

  const [stage, setStage] = useState<string | null>(null);
  const [stagePayload, setStagePayload] = useState<StageEvent | null>(null);
  const [summary, setSummary] = useState("");
  const [verification, setVerification] = useState<Verification | null>(null);
  // Set while the answer's run is paused for review; cleared once judged or retried.
  const [review, setReview] = useState<Review | null>(null);
  const [verdict, setVerdict] = useState<ReviewVerdict | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [queryError, setQueryError] = useState<string | null>(null);
  const [recoverId, setRecoverId] = useState<string | null>(null);
  const [queriesRefreshKey, setQueriesRefreshKey] = useState(0);

  // Clear the panels before a run that streams a fresh answer.
  function startRun() {
    setStreaming(true);
    setStage("embedding_query");
    setStagePayload(null);
    setSnippets([]);
    setSummary("");
    setVerification(null);
    setReview(null);
    setVerdict(null);
    setQueryError(null);
    setRecoverId(null);
  }

  function streamHandlers(): SseHandlers {
    let prepared = false;
    return {
      onStage: (payload) => {
        if (payload.stage === "extracting") prepared = true;
        // A retried or recovered summarize streams the whole answer again.
        if (payload.stage === "summarizing") setSummary("");
        setStage(payload.stage);
        setStagePayload(payload);
      },
      onSnippets: (items) => setSnippets(items),
      onToken: (tok) => setSummary((prev) => prev + tok),
      onVerification: setVerification,
      onReview: setReview,
      onDone: () => {
        if (prepared) setDocVersion((v) => v + 1);
        setStreaming(false);
        setStage("done");
        setQueriesRefreshKey((k) => k + 1);
      },
      onError: (msg, queryId) => {
        setQueryError(msg);
        setRecoverId(queryId ?? null);
        setStreaming(false);
      },
    };
  }

  async function onAnalyze(query: string, topK: number) {
    if (!doc) return;
    startRun();
    await runQuery(doc.id, query, topK, streamHandlers());
  }

  async function onResume(action: ReviewAction) {
    if (!review) return;
    const id = review.query_id;
    if (action === "retry") {
      startRun();
      await resumeQuery(id, "retry", streamHandlers());
      return;
    }
    // A verdict streams nothing back; keep the answer on screen.
    setReview(null);
    setQueryError(null);
    await resumeQuery(id, action, {
      onDone: () => {
        setVerdict(action === "accept" ? "accepted" : "rejected");
        setQueriesRefreshKey((k) => k + 1);
      },
      onError: setQueryError,
    });
  }

  // Continue a failed run from its last checkpoint; what it already streamed stays on screen.
  async function onRecover() {
    if (!recoverId) return;
    const id = recoverId;
    setRecoverId(null);
    setQueryError(null);
    setStreaming(true);
    await resumeQuery(id, "recover", streamHandlers());
  }

  async function onSelectQuery(queryId: string) {
    setRecoverId(null);
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
      setVerification(cached.verification);
      setReview(cached.review);
      setVerdict(cached.review_status === "pending" ? null : cached.review_status);
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
    setVerification(null);
    setReview(null);
    setVerdict(null);
    setStage(null);
    setStagePayload(null);
    setPageTarget(null);
    setQueryError(null);
    setRecoverId(null);
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
          setVerification(null);
          setReview(null);
          setVerdict(null);
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
          <DocumentViewer doc={doc} page={pageTarget} version={docVersion} />
        </section>
        <section className="w-1/4 flex flex-col gap-6 overflow-hidden">
          <PipelineVisualizer
            activeStage={stage}
            lastPayload={stagePayload}
            snippetCount={snippets.length}
            finished={stage === "done"}
          />
          <SnippetList
            snippets={snippets}
            onJump={setPageTarget}
            emptyText={
              // The evidence gate answered without sources: say so instead of the idle hint.
              verification?.issues.some((i) => i.kind === "low_evidence")
                ? "No passage in this document is close enough to the question to use as a source."
                : undefined
            }
          />
        </section>
        <section className="w-1/4 flex flex-col gap-6 overflow-hidden">
          <QueryInput disabled={!doc} busy={streaming} onSubmit={onAnalyze} />
          {queryError && (
            <div className="text-sm text-error bg-error-container/40 rounded-lg p-3">
              {queryError}
              {recoverId && !streaming && (
                <button
                  className="ml-2 px-2 py-1 rounded bg-surface-container-lowest/70 font-semibold hover:bg-surface-container-lowest"
                  onClick={onRecover}
                >
                  Continue
                </button>
              )}
            </div>
          )}
          <SummaryCard
            text={summary}
            streaming={streaming}
            verification={verification}
            review={review}
            verdict={verdict}
            onResume={onResume}
          />
        </section>
      </main>
    </>
  );
}
