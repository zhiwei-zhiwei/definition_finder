"use client";

import { motion } from "framer-motion";
import { StageEvent } from "@/lib/types";

type StageSpec = { key: string; label: string; icon: string };

const STAGES: StageSpec[] = [
  { key: "embedding_query", label: "Embedding your query", icon: "bolt" },
  { key: "searching", label: "Searching candidate passages", icon: "search" },
  { key: "consolidating", label: "Consolidating by parent context", icon: "hub" },
  { key: "fetching_context", label: "Fetching targeted context", icon: "filter_alt" },
  { key: "summarizing", label: "Synthesizing summary", icon: "psychology" },
];

const PREP_STAGES: Record<string, string> = {
  extracting: "Extracting text",
  cleaning: "Cleaning text",
  chunking_parents: "Chunking (parents)",
  chunking_children: "Chunking (children)",
  embedding: "Embedding document",
};

type Props = {
  activeStage: string | null;
  lastPayload: StageEvent | null;
  snippetCount: number;
  finished: boolean;
};

function stateOf(stageKey: string, active: string | null, finished: boolean): "pending" | "active" | "done" {
  const order = STAGES.findIndex((s) => s.key === stageKey);
  const activeIdx = active ? STAGES.findIndex((s) => s.key === active) : -1;
  if (finished) return "done";
  if (activeIdx < 0) return "pending";
  if (order < activeIdx) return "done";
  if (order === activeIdx) return "active";
  return "pending";
}

export function PipelineVisualizer({ activeStage, lastPayload, snippetCount, finished }: Props) {
  const prepLabel = activeStage && PREP_STAGES[activeStage] ? PREP_STAGES[activeStage] : null;
  const progressPct =
    prepLabel && typeof lastPayload?.progress === "number"
      ? Math.round(lastPayload.progress * 100)
      : null;
  return (
    <div className="bg-surface-container-lowest rounded-lg p-5 ambient-shadow shrink-0 relative overflow-hidden">
      <div className="absolute top-0 left-0 w-1 h-full gradient-primary" />
      <h3 className="font-headline font-semibold text-on-surface mb-4 flex items-center gap-2">
        <span className="material-symbols-outlined text-primary text-sm">psychology</span>
        Analysis Engine
      </h3>
      {prepLabel && (
        <div className="mb-4 p-3 rounded-lg bg-primary/5 border border-primary/20 text-[12px] text-on-surface flex items-center gap-2">
          <motion.div
            className="w-3 h-3 rounded-full border-2 border-primary border-t-transparent"
            animate={{ rotate: 360 }}
            transition={{ repeat: Infinity, duration: 0.8, ease: "linear" }}
          />
          <span>
            Preparing document (first-time only): {prepLabel}
            {progressPct !== null ? ` — ${progressPct}%` : ""}
          </span>
        </div>
      )}
      <div className="space-y-3 font-label text-sm">
        {STAGES.map((s, i) => {
          const state = stateOf(s.key, activeStage, finished);
          const showConnector = i < STAGES.length - 1;
          return (
            <div key={s.key}>
              <div className="flex items-start gap-3">
                {state === "active" ? (
                  <motion.div
                    className="w-4 h-4 rounded-full border-2 border-primary border-t-transparent mt-0.5"
                    animate={{ rotate: 360 }}
                    transition={{ repeat: Infinity, duration: 0.8, ease: "linear" }}
                  />
                ) : state === "done" ? (
                  <span className="material-symbols-outlined text-primary text-[16px] mt-0.5">check_circle</span>
                ) : (
                  <span className="material-symbols-outlined text-on-surface-variant/50 text-[16px] mt-0.5">
                    {s.icon}
                  </span>
                )}
                <div className="flex-1">
                  <span
                    className={`block font-medium ${
                      state === "active" ? "text-primary" : state === "done" ? "text-on-surface" : "text-on-surface-variant"
                    }`}
                  >
                    {s.label}
                  </span>
                  <StageCaption stage={s.key} active={activeStage} payload={lastPayload} snippetCount={snippetCount} />
                </div>
              </div>
              {showConnector && <div className="w-px h-3 bg-outline-variant/30 ml-2" />}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function StageCaption({
  stage,
  active,
  payload,
  snippetCount,
}: {
  stage: string;
  active: string | null;
  payload: StageEvent | null;
  snippetCount: number;
}) {
  const isActive = stage === active && payload;
  if (!isActive) {
    if (stage === "searching" && snippetCount > 0) {
      return <span className="text-on-surface-variant text-[11px]">Returned {snippetCount} snippets</span>;
    }
    return null;
  }
  const p = payload as StageEvent;
  if (stage === "searching" && p.top_k) {
    return <span className="text-on-surface-variant text-[11px]">Top-K = {p.top_k}</span>;
  }
  if (stage === "consolidating" && p.parent_votes) {
    const items = Object.entries(p.parent_votes)
      .map(([k, v]) => `${k}:${v}`)
      .slice(0, 3)
      .join(", ");
    return <span className="text-on-surface-variant text-[11px]">Votes: {items || "—"}</span>;
  }
  if (stage === "fetching_context" && p.winning_parents) {
    return (
      <span className="text-on-surface-variant text-[11px]">
        Winners: {p.winning_parents.join(", ") || "—"}
      </span>
    );
  }
  if (stage === "summarizing") {
    return <span className="text-on-surface-variant text-[11px]">Generating summary...</span>;
  }
  if (stage === "embedding_query") {
    return <span className="text-on-surface-variant text-[11px]">{p.message ?? ""}</span>;
  }
  return null;
}
