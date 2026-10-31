export type ReleaseRepo = {
  name: string;
  newest: string | null;
  newest_label: string | null;
  newest_version: string | null;
  newest_released: boolean | null;
  unreleased: number;
  rollouts: number;
  served: string | null;
  served_label: string | null;
};

export type Revision = { seq: number; hash: string; label: string | null; version: string | null; released: boolean; origin: string | null; created_at: string };
export type Rollout = { target_type: "store" | "market" | "all"; target: string | null; head: string; created_at: string };
export type StoreState = "current" | "behind" | "ahead" | "unknown head" | "no report";
export type StoreRow = {
  store: string; market: string | null; state: StoreState; reported: string | null; reported_label: string | null; reported_version: string | null;
  reported_at: string | null; served: string | null; served_label: string | null;
};
export type LogRow = { id: number; target_type: "store" | "market" | "all"; target: string | null; action: string; head: string | null; previous: string | null; actor: string | null; created_at: string };
export type Labels = Record<string, { seq?: number; label: string | null; version: string | null; released?: boolean }>;

export type RepoStatus = {
  repository: string;
  default_head: string | null;
  revisions: Revision[];
  rollouts: Rollout[];
  stores: StoreRow[];
  log: LogRow[];
  labels: Labels;
  markets: string[];
};

export type TargetKind = "store" | "market" | "all";
export const targetText = (t: string, n?: string | null) => (t === "all" ? "everyone" : `${t} ${n ?? ""}`.trim());
