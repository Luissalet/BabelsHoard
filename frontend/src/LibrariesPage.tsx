import { useEffect, useState } from "react";
import { FolderPlus, Library as LibraryIcon, RefreshCw } from "lucide-react";
import { api, ApiError } from "./api";
import type { Lang } from "./i18n";
import { t } from "./i18n";
import type { Environment, Job, Library } from "./types";

function statusBadgeClass(status: string): string {
  switch (status) {
    case "done":
      return "badge-done";
    case "partial":
    case "indexing":
      return "badge-indexing";
    case "error":
      return "badge-error";
    default:
      return "badge-pending";
  }
}

function statusLabel(lang: Lang, status: string): string {
  const key = (`libraries_status_${status}` as const) as Parameters<typeof t>[1];
  return t(lang, key);
}

function EnvCard({ env, lang, onChanged }: { env: Environment; lang: Lang; onChanged: () => void }) {
  const [libs, setLibs] = useState<Library[]>([]);
  const [newImport, setNewImport] = useState("");
  const [ecosystem, setEcosystem] = useState(env.python_path ? "python" : "js");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);

  const load = () => api.libraries({ env: env.id }).then(setLibs).catch(() => {});

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [env.id]);

  useEffect(() => {
    if (!job || job.status === "done" || job.status === "error") return;
    const id = setInterval(async () => {
      const j = await api.job(job.id);
      setJob(j);
      if (j.status === "done" || j.status === "error") {
        load();
        onChanged();
      }
    }, 900);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.id, job?.status]);

  async function indexOne() {
    if (!newImport.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.indexLibrary(env.id, newImport.trim(), ecosystem);
      setNewImport("");
      load();
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function indexDeps() {
    setBusy(true);
    setError(null);
    try {
      const res = await api.indexDependencies(env.id);
      setJob({ id: res.job_id, kind: "index_dependencies", status: "queued", progress: 0, message: null, error: null, created_at: "", updated_at: "", result: null });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card env-card">
      <div className="env-card-header">
        <div>
          <div style={{ fontWeight: 600 }}>{env.label}</div>
          <div className="text-dim" style={{ fontSize: 12, marginTop: 2 }}>
            {env.python_path && `Python ${env.python_version} · ${env.python_path}`}
            {env.python_path && env.node_modules_path && " · "}
            {env.node_modules_path && `node_modules: ${env.node_modules_path}`}
          </div>
        </div>
        {env.project_path && (
          <button className="btn" onClick={indexDeps} disabled={busy}>
            <RefreshCw size={13} /> {t(lang, "libraries_index_deps")}
          </button>
        )}
      </div>

      {job && (
        <div>
          <div className="progress-bar">
            <div className="progress-bar-fill" style={{ width: `${Math.round(job.progress * 100)}%` }} />
          </div>
          <div className="text-dim" style={{ fontSize: 11.5, marginTop: 4 }}>
            {job.message ?? job.status}
          </div>
        </div>
      )}

      <div>
        {libs.length === 0 && <div className="text-dim" style={{ fontSize: 13 }}>&mdash;</div>}
        {libs.map((lib) => (
          <div className="lib-row" key={lib.id}>
            <div>
              <span className="lib-name">{lib.name}</span>
              <span className="text-dim" style={{ marginLeft: 8, fontSize: 12 }}>
                {lib.version}
              </span>
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <span className="text-dim" style={{ fontSize: 11.5 }}>
                {lib.entry_count} entries
              </span>
              <span className={`badge ${statusBadgeClass(lib.status)}`}>{statusLabel(lang, lib.status)}</span>
              <button
                className="btn btn-ghost"
                style={{ padding: "2px 8px" }}
                onClick={async () => {
                  await api.indexLibrary(env.id, lib.name.replace(/^stdlib\//, ""), lib.ecosystem, true);
                  load();
                  onChanged();
                }}
              >
                {t(lang, "libraries_reindex")}
              </button>
            </div>
          </div>
        ))}
      </div>

      <div className="field-row">
        <input
          className="input"
          style={{ flex: 1 }}
          placeholder="e.g. pandas, react"
          value={newImport}
          onChange={(e) => setNewImport(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && indexOne()}
        />
        <select className="select" value={ecosystem} onChange={(e) => setEcosystem(e.target.value)}>
          <option value="python">python</option>
          <option value="js">js</option>
        </select>
        <button className="btn btn-primary" onClick={indexOne} disabled={busy || !newImport.trim()}>
          {t(lang, "libraries_index_now")}
        </button>
      </div>
      {error && <div className="finding">{error}</div>}
    </div>
  );
}

export function LibrariesPage({ lang }: { lang: Lang }) {
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);

  const load = () => api.environments().then(setEnvironments).catch(() => {});

  useEffect(() => {
    load();
  }, [refreshTick]);

  async function addEnv() {
    if (!path.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.registerEnvironment(path.trim());
      setPath("");
      setRefreshTick((n) => n + 1);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack-gap">
      <div className="card">
        <div className="section-title">{t(lang, "libraries_add")}</div>
        <div className="field-row">
          <input
            className="input"
            style={{ flex: 1 }}
            placeholder={t(lang, "libraries_add_placeholder")}
            value={path}
            onChange={(e) => setPath(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && addEnv()}
          />
          <button className="btn btn-primary" onClick={addEnv} disabled={busy || !path.trim()}>
            <FolderPlus size={14} /> {t(lang, "libraries_add_button")}
          </button>
        </div>
        {error && <div className="finding">{error}</div>}
      </div>

      {environments.length === 0 && (
        <div className="empty-state">
          <LibraryIcon size={40} />
          <div>{t(lang, "libraries_empty")}</div>
        </div>
      )}

      {environments.map((env) => (
        <EnvCard key={env.id} env={env} lang={lang} onChanged={() => setRefreshTick((n) => n + 1)} />
      ))}
    </div>
  );
}
