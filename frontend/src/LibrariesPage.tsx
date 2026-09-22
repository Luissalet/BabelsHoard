import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, FolderPlus, Library as LibraryIcon, Loader2, RefreshCw } from "lucide-react";
import { api, ApiError } from "./api";
import type { Key, Lang } from "./i18n";
import { t } from "./i18n";
import type { Environment, InstalledPackage, Job, Library } from "./types";

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
  const key = `libraries_status_${status}` as Key;
  return t(lang, key);
}

function PackageList({ env, lang, onIndexed }: { env: Environment; lang: Lang; onIndexed: () => void }) {
  const [filter, setFilter] = useState("");
  const [packages, setPackages] = useState<InstalledPackage[]>([]);
  const [total, setTotal] = useState(0);
  const [busyName, setBusyName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = (q: string) =>
    api
      .packages(env.id, q)
      .then((r) => {
        setPackages(r.packages);
        setTotal(r.total);
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));

  useEffect(() => {
    const h = setTimeout(() => load(filter), 150);
    return () => clearTimeout(h);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter, env.id]);

  async function index(pkg: InstalledPackage) {
    // A distribution can ship several import names (pytest: py and pytest);
    // each one is its own index.
    const importNames = pkg.import_names.length > 0 ? pkg.import_names : [pkg.name];
    setBusyName(pkg.name);
    setError(null);
    try {
      for (const importName of importNames) {
        await api.indexLibrary(env.id, importName, "python", pkg.status !== "not_indexed");
      }
      await load(filter);
      onIndexed();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusyName(null);
    }
  }

  return (
    <div className="packages">
      <div className="field-row" style={{ marginBottom: 6 }}>
        <input className="input" style={{ flex: 1 }} placeholder={t(lang, "packages_filter")} value={filter} onChange={(e) => setFilter(e.target.value)} />
        <span className="text-dim small">
          {filter.trim() ? t(lang, "packages_matching", { n: total }) : t(lang, "packages_count", { n: total })}
        </span>
      </div>
      {error && <div className="finding">{error}</div>}
      <div className="package-scroll">
        {packages.map((pkg) => (
          <div className="lib-row" key={`${pkg.name}@${pkg.version}`}>
            <div className="lib-main">
              <span className="lib-name">{pkg.name}</span>
              <span className="lib-version">{pkg.version}</span>
              {pkg.import_names.length > 0 && pkg.import_names[0].toLowerCase() !== pkg.name.toLowerCase().replace(/-/g, "_") && (
                <span className="text-dim small mono">import {pkg.import_names.join(", ")}</span>
              )}
            </div>
            <div className="lib-side">
              {pkg.entry_count > 0 && (
                <span className="text-dim small">
                  {pkg.entry_count.toLocaleString()} {t(lang, "libraries_entries")}
                </span>
              )}
              <span className={`badge ${statusBadgeClass(pkg.status)}`}>{statusLabel(lang, pkg.status)}</span>
              <button className="btn btn-small" onClick={() => index(pkg)} disabled={busyName !== null}>
                {busyName === pkg.name ? <Loader2 size={12} className="spin" /> : null}
                {pkg.status === "not_indexed" ? t(lang, "packages_index") : t(lang, "libraries_reindex")}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function EnvCard({ env, lang, onChanged }: { env: Environment; lang: Lang; onChanged: () => void }) {
  const [libs, setLibs] = useState<Library[]>([]);
  const [showPackages, setShowPackages] = useState(false);
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

  const current = libs.filter((l) => l.status !== "superseded");
  const older = libs.filter((l) => l.status === "superseded");

  return (
    <div className="card env-card">
      <div className="env-card-header">
        <div style={{ minWidth: 0 }}>
          <div className="env-title">
            {env.label}
            {(env.default_for ?? (env.is_default ? ["python"] : [])).map((l) => (
              <span className="tag tag-accent" key={l}>
                {t(lang, "libraries_default")} · {l === "python" ? "Python" : "TypeScript"}
              </span>
            ))}
          </div>
          <div className="env-sub mono">
            {env.python_path && `Python ${env.python_version} · ${env.python_path}`}
            {env.python_path && env.node_modules_path && " · "}
            {env.node_modules_path && `node_modules: ${env.node_modules_path}`}
          </div>
        </div>
        {env.project_path && env.python_path && !env.is_builtin && (
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
          <div className="text-dim small" style={{ marginTop: 4 }}>
            {job.message ?? job.status}
          </div>
        </div>
      )}

      <div className="section-title" style={{ marginTop: 8 }}>
        {t(lang, "libraries_indexed")}
      </div>
      {current.length === 0 && <div className="text-dim small">{t(lang, "libraries_none_indexed")}</div>}
      <div>
        {current.map((lib) => (
          <div className="lib-row" key={lib.id}>
            <div className="lib-main">
              <span className="lib-name">{lib.name.replace(/^stdlib\//, "")}</span>
              <span className="lib-version">{lib.name.startsWith("stdlib/") ? `stdlib ${lib.version}` : lib.version}</span>
              {lib.note && lib.status === "partial" && <span className="text-dim small">{lib.note}</span>}
            </div>
            <div className="lib-side">
              <span className="text-dim small">
                {lib.entry_count.toLocaleString()} {t(lang, "libraries_entries")}
              </span>
              <span className={`badge ${statusBadgeClass(lib.status)}`}>{statusLabel(lang, lib.status)}</span>
            </div>
          </div>
        ))}
        {older.length > 0 && (
          <div className="text-dim small" style={{ paddingTop: 6 }}>
            {t(lang, "other_versions")}: {older.map((l) => `${l.name.replace(/^stdlib\//, "")} ${l.version}`).join(", ")}
          </div>
        )}
      </div>

      {env.python_path && (
        <>
          <button className="linklike disclosure" onClick={() => setShowPackages((v) => !v)}>
            {showPackages ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            {showPackages ? t(lang, "packages_hide") : t(lang, "packages_show")}
          </button>
          {showPackages && <PackageList env={env} lang={lang} onIndexed={load} />}
        </>
      )}

      <div className="field-row" style={{ marginTop: 8 }}>
        <input
          className="input"
          style={{ flex: 1 }}
          placeholder={t(lang, "packages_other")}
          value={newImport}
          onChange={(e) => setNewImport(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && indexOne()}
        />
        <select className="select" value={ecosystem} onChange={(e) => setEcosystem(e.target.value)}>
          {env.python_path && <option value="python">python</option>}
          {env.node_modules_path && <option value="js">npm</option>}
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

  useEffect(() => {
    api.environments().then((envs) => setEnvironments([...envs].reverse())).catch(() => {});
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
    <div className="page stack-gap">
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
            {busy ? <Loader2 size={14} className="spin" /> : <FolderPlus size={14} />} {t(lang, "libraries_add_button")}
          </button>
        </div>
        {error && <div className="finding" style={{ marginTop: 10 }}>{error}</div>}
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
