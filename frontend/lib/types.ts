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
