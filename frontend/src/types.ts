export interface Environment {
  id: string;
  label: string;
  project_path: string | null;
  python_path: string | null;
  python_version: string | null;
  node_modules_path: string | null;
  node_version: string | null;
  is_builtin: number;
  created_at: string;
}

export interface Library {
  id: string;
  ecosystem: "python" | "js" | "docset" | "markdown";
  name: string;
  version: string;
  source: string;
  env_id: string | null;
  status: "pending" | "indexing" | "done" | "partial" | "error";
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
}

export interface Param {
  name: string;
  kind: string;
  annotation: string | null;
  default: string | null;
  description: string | null;
}

export interface LookupResult {
  found: boolean;
  symbol: string;
  qualname?: string;
  kind?: string;
  signature?: string;
  params?: Param[];
  returns?: string | null;
  summary?: string | null;
  doc?: string | null;
  deprecated?: boolean;
  deprecated_note?: string | null;
  source_path?: string | null;
  source_line?: number | null;
  library?: string;
  ecosystem?: string;
  env_id?: string;
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

export interface HealthInfo {
  service: string;
  name: string;
  version: string;
  status: string;
  libraries: number;
  entries: number;
  environments: number;
}
