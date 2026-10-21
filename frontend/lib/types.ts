export type Verification = {
  status: "ok" | "warning" | "insufficient_evidence";
  issues: { kind: string; detail: string }[];
  needs_review: boolean;
};

// A run paused for human review (the `review` SSE event, or a cached pending query).
export type Review = {
  query_id: string;
  can_retry: boolean;
  top_k: number;
};

export type ReviewAction = "accept" | "reject" | "retry";
export type ReviewVerdict = "accepted" | "rejected";

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
