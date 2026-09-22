export interface Environment {
  id: string;
  label: string;
  project_path: string | null;
  python_path: string | null;
  python_version: string | null;
  node_modules_path: string | null;
  node_version: string | null;
  is_builtin: number;
  is_default?: boolean;
  created_at: string;
}

export interface InstalledPackage {
  name: string;
  version: string;
  import_names: string[];
  status: "not_indexed" | "done" | "partial" | "error" | "indexing" | "superseded";
  entry_count: number;
}

export interface PackageList {
  packages: InstalledPackage[];
  total: number;
  truncated: boolean;
}

export interface Library {
  id: string;
  ecosystem: "python" | "js" | "docset" | "markdown";
  name: string;
  version: string;
  source: string;
  env_id: string | null;
  status: "pending" | "indexing" | "done" | "partial" | "error" | "superseded";
  entry_count: number;
  note: string | null;
  indexed_at: string | null;
  created_at: string;
}

export interface SearchHit {
  id: string;
  qualname: string;
  kind: string;
  library: string | null;
  ecosystem: string | null;
  env_id: string | null;
  signature: string;
  summary: string;
  score: number;
}

export interface SearchResult {
  query: string;
  results: SearchHit[];
  count: number;
  truncated: boolean;
  did_you_mean?: string[];
  hint?: string;
}

export interface Param {
  name: string;
  kind: string;
  annotation?: string;
  default?: string;
  required?: boolean;
  description?: string;
}

export interface LookupResult {
  found: boolean;
  symbol?: string;
  id?: string;
  qualname?: string;
  kind?: string;
  signature?: string | null;
  params?: Param[];
  params_are?: string;
  returns?: string | null;
  summary?: string | null;
  doc?: string | null;
  doc_truncated?: boolean;
  deprecated?: boolean;
  deprecated_note?: string | null;
  source?: string | null;
  library?: string;
  env?: string;
  members?: string[];
  members_total?: number;
  other_versions_indexed?: string[];
  note?: string;
  same_as?: string;
  certain?: boolean;
  suggestions?: string[];
  message?: string;
}

export interface Finding {
  line: number;
  col: number;
  severity: "error" | "warning";
  code: string;
  symbol: string;
  message: string;
  suggestion?: string;
}

export interface CheckResult {
  ok: boolean;
  truncated?: boolean;
  findings: Finding[];
  checked: number;
  unchecked: number;
  env: string;
  libraries: string[];
}

export interface DocsetEntry {
  slug: string;
  name: string;
  version: string | null;
  library_id: string;
  installed_at: string;
  entry_count: number;
}

export interface DocsetCatalogItem {
  slug: string;
  name: string;
  version: string;
  db_size_kb: number;
}

export interface Job {
  id: string;
  kind: string;
  status: "queued" | "running" | "done" | "error";
  progress: number;
  message: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  result: unknown;
}

export interface AgentCall {
  id: number;
  ts: string;
  tool: string;
  args_summary: string | null;
  duration_ms: number | null;
  ok: number;
  error: string | null;
}

export interface BackendCapability {
  capability: string;
  provider: string | null;
  url: string | null;
  model: string | null;
  api: string | null;
  state: "resolved" | "unavailable";
  reason: string;
  details?: Record<string, unknown>;
}

export interface BackendStatus {
  used: string[];
  capabilities: Record<string, BackendCapability>;
  /** Why data/backend.json was ignored (broken by hand), if it was. */
  config_error?: string | null;
}

export interface BackendConfig {
  only_resident: boolean;
  faustus: { url?: string; token_set: boolean };
  comfy: { url?: string };
  capabilities: Record<string, { url?: string; model?: string }>;
}

export interface AskEntry {
  id: string;
  qualname: string;
  kind: string;
  library: string | null;
  signature: string;
  summary: string;
}

export interface AskResult {
  available: boolean;
  answered?: boolean;
  reason?: string;
  message?: string;
  question?: string;
  answer?: string;
  cited?: string[];
  entries?: AskEntry[];
  semantic_rerank?: boolean;
  model?: string | null;
  provider?: string | null;
  did_you_mean?: string[];
}

export interface HealthInfo {
  service: string;
  name: string;
  version: string;
  status: string;
  libraries: number;
  entries: number;
  environments: number;
  jobs_running?: number;
}
