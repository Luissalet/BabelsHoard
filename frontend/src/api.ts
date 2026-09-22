import type {
  AgentCall,
  AskResult,
  BackendConfig,
  BackendStatus,
  CheckResult,
  DocsetCatalogItem,
  DocsetEntry,
  Environment,
  HealthInfo,
  Job,
  Library,
  LookupResult,
  PackageList,
  SearchResult,
} from "./types";

export class ApiError extends Error {
  code: string;
  status: number;
  constructor(code: string, message: string, status: number) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let body: { error?: string; message?: string } = {};
    try {
      body = await res.json();
    } catch {
      /* ignore */
    }
    throw new ApiError(body.error ?? "http_error", body.message ?? res.statusText, res.status);
  }
  return (await res.json()) as T;
}

function post<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "POST", body: JSON.stringify(body) });
}

function put<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "PUT", body: JSON.stringify(body) });
}

export const api = {
  health: () => request<HealthInfo>("/api/health"),
  environments: () => request<Environment[]>("/api/environments"),
  registerEnvironment: (path: string) => post<Environment>("/api/environments/register", { path }),
  indexDependencies: (envId: string) =>
    post<{ job_id: string; dependencies: string[] }>(`/api/environments/${envId}/index-dependencies`, {}),
  libraries: (params?: { ecosystem?: string; env?: string }) => {
    const qs = new URLSearchParams();
    if (params?.ecosystem) qs.set("ecosystem", params.ecosystem);
    if (params?.env) qs.set("env", params.env);
    const suffix = qs.toString() ? `?${qs}` : "";
    return request<Library[]>(`/api/libraries${suffix}`);
  },
  indexLibrary: (env: string, import_name: string, ecosystem = "python", force = false) =>
    post<Library>("/api/libraries/index", { env, import_name, ecosystem, force }),
  search: (q: string, filters?: { library?: string; ecosystem?: string; kind?: string; env?: string; limit?: number }) => {
    const qs = new URLSearchParams({ q });
    if (filters?.library) qs.set("library", filters.library);
    if (filters?.ecosystem) qs.set("ecosystem", filters.ecosystem);
    if (filters?.kind) qs.set("kind", filters.kind);
    if (filters?.env) qs.set("env", filters.env);
    if (filters?.limit) qs.set("limit", String(filters.limit));
    return request<SearchResult>(`/api/search?${qs}`);
  },
  // UI endpoints on purpose: /api/agent/* is the assistant's surface and is
  // recorded in "Assistant activity".
  lookup: (symbol: string, env?: string, library?: string) => {
    const qs = new URLSearchParams({ symbol });
    if (env) qs.set("env", env);
    if (library) qs.set("library", library);
    return request<LookupResult>(`/api/lookup?${qs}`);
  },
  packages: (envId: string, q = "", limit = 300) =>
    request<PackageList>(`/api/environments/${encodeURIComponent(envId)}/packages?q=${encodeURIComponent(q)}&limit=${limit}`),
  readEntry: (id: string, offset = 0, max_chars = 4000) =>
    request<{ found: boolean; text: string; signature?: string | null; total_chars: number; has_more: boolean; next_offset: number | null }>(
      `/api/entries/${encodeURIComponent(id)}?offset=${offset}&max_chars=${max_chars}`
    ),
  checkCode: (code: string, env?: string, language = "python") =>
    post<CheckResult>("/api/check", { code, env, language }),
  docsets: () => request<DocsetEntry[]>("/api/docsets"),
  docsetCatalog: (q: string, limit = 20) =>
    request<{ results: DocsetCatalogItem[]; total: number }>(`/api/docsets/catalog?q=${encodeURIComponent(q)}&limit=${limit}`),
  installDocset: (slug: string) => post<{ job_id: string }>("/api/docsets/install", { slug }),
  indexFolder: (path: string, name?: string) => post<Library>("/api/folders/index", { path, name }),
  jobs: (limit = 20) => request<Job[]>(`/api/jobs?limit=${limit}`),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  agentCalls: (limit = 50) => request<AgentCall[]>(`/api/agent_calls?limit=${limit}`),
  // Shared model backend (Hoard Link): Settings -> Models.
  backendStatus: () => request<BackendStatus>("/api/backend"),
  backendConfigGet: () => request<BackendConfig>("/api/backend/config"),
  backendConfig: (patch: {
    only_resident?: boolean;
    faustus?: { url?: string; token?: string };
    capabilities?: Record<string, { url?: string; model?: string }>;
  }) => put<{ config: BackendConfig; status: BackendStatus }>("/api/backend/config", patch),
  backendRecheck: () => post<BackendStatus>("/api/backend/recheck", {}),
  // "Ask the docs": UI-only, like /api/search and /api/lookup.
  ask: (question: string, filters?: { library?: string; ecosystem?: string; kind?: string; env?: string }) =>
    post<AskResult>("/api/ask", { question, ...filters }),
};
