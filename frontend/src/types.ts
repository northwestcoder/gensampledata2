export type Scenario = {
  schema_version: number;
  profile: string;
  name: string;
  people: number;
  seed: number;
  reference_time: string;
  batch_size: number;
  sources: string[];
  formats: string[];
  duplicate_fraction: number;
  copies_per_duplicate: number;
  overlap_fraction: number;
  corruption_probability: number;
  missing_probability: number;
  max_transactions: number;
  transaction_window: string;
  points_percent: number;
  tracer_file: string | null;
  date_mode?: "relative" | "original";
  field_pools?: Record<string, string>;
};
export type SyntheticRecord = {
  source: string;
  record_id: string;
  entity_id: string;
  duplicate: boolean;
  data: Record<string, string | number | null>;
  original: Record<string, string | number | null>;
  mutations: {
    field: string;
    operation: string;
    before: string;
    after: string;
  }[];
};
export type Job = {
  id: string;
  status: string;
  scenario: Scenario;
  records: number;
  counts: Record<string, number>;
  error: string | null;
  created: string;
};
export type Manifest = {
  counts: Record<string, number>;
  duplicate_records: Record<string, number>;
  realized_duplicate_row_fraction: Record<string, number>;
  total_records: number;
  elapsed_seconds: number;
  files: Record<string, { role: string; bytes: number; sha256: string }>;
  verification: { valid: boolean; unique_entities: number; checks: string[] };
  corpus_sha256: string;
};
export const ACTIVE = new Set(["queued", "running", "verifying"]);
export const LABELS: Record<string, string> = {
  customers: "Customers",
  transactions: "E-commerce orders",
  pos: "POS customers",
  pos_transactions: "POS orders",
  loyalty: "Loyalty",
  email: "Email campaigns",
  clickstream: "Clickstream",
  mobile: "Mobile",
  wifi: "Wi-Fi",
  safety: "Safety reports",
  locations: "Locations",
  custom: "Custom object",
};
export function toggleSource(sources: string[], source: string): string[] {
  const next = new Set(sources);
  if (next.has(source)) {
    next.delete(source);
    if (source === "customers") next.delete("transactions");
    if (source === "pos") next.delete("pos_transactions");
  } else {
    next.add(source);
    if (source === "transactions") next.add("customers");
    if (source === "pos_transactions") next.add("pos");
  }
  return Object.keys(LABELS).filter((key) => next.has(key));
}
export function errorText(detail: unknown): string {
  if (Array.isArray(detail))
    return detail
      .map(
        (item) => `${item.loc?.slice(1).join(".") || "Scenario"}: ${item.msg}`,
      )
      .join("; ");
  return typeof detail === "string"
    ? detail
    : "The request could not be completed.";
}
