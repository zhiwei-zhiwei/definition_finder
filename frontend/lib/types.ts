export type Verification = {
  status: "ok" | "warning" | "insufficient_evidence";
  issues: { kind: string; detail: string }[];
  needs_review: boolean;
};

export type StageEvent = {
  stage: string;
  message?: string;
  top_k?: number;
  parent_votes?: Record<string, number>;
  winning_parents?: string[];
  progress?: number;
  count?: number;
  stats?: { chars_before: number; chars_after: number };
};
