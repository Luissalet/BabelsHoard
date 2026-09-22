import { useEffect, useState } from "react";
import { BookOpen, Download, FolderSearch, Globe, Loader2 } from "lucide-react";
import { api, ApiError } from "./api";
import type { Lang } from "./i18n";
import { t } from "./i18n";
import type { DocsetCatalogItem, DocsetEntry, Job, Library } from "./types";

/** Index a folder of Markdown/reST docs (a project's docs/) so Search finds
 * its sections next to the API index. Local only, no network. */
function FolderCard({ lang }: { lang: Lang }) {
  const [folders, setFolders] = useState<Library[]>([]);
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const load = () => api.libraries({ ecosystem: "markdown" }).then(setFolders).catch(() => {});
  useEffect(() => {
    load();
  }, []);

  async function indexFolder() {
    if (!path.trim()) return;
    setBusy(true);
    setError(null);
    setDone(null);
    try {
      const r = await api.indexFolder(path.trim(), name.trim() || undefined);
      setDone(t(lang, "folders_done", { name: r.name, note: r.note ?? "" }));
      setPath("");
      setName("");
      load();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <div className="section-title">{t(lang, "folders_title")}</div>
      <div className="text-dim small" style={{ marginBottom: 10 }}>
        {t(lang, "folders_intro")}
      </div>
      <div className="field-row">
        <input
          className="input"
          style={{ flex: 3 }}
          placeholder={t(lang, "folders_path_placeholder")}
          value={path}
          onChange={(e) => setPath(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && indexFolder()}
        />
        <input
          className="input"
          style={{ flex: 1, minWidth: 120 }}
          placeholder={t(lang, "folders_name_placeholder")}
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && indexFolder()}
        />
        <button className="btn btn-primary" onClick={indexFolder} disabled={busy || !path.trim()}>
          {busy ? <Loader2 size={14} className="spin" /> : <FolderSearch size={14} />} {t(lang, "folders_button")}
        </button>
      </div>
      {error && <div className="finding" style={{ marginTop: 10 }}>{error}</div>}
      {done && <div className="finding-ok small" style={{ marginTop: 10 }}>{done}</div>}
      {folders.map((f) => (
        <div className="lib-row" key={f.id}>
          <div className="lib-main">
            <span className="lib-name">{f.name}</span>
            <span className="text-dim small mono">{f.source.replace(/^folder:/, "")}</span>
          </div>
          <span className="text-dim small">
            {f.entry_count.toLocaleString()} {t(lang, "docsets_sections")}
          </span>
        </div>
      ))}
    </div>
  );
}

export function DocsetsPage({ lang }: { lang: Lang }) {
  const [installed, setInstalled] = useState<DocsetEntry[]>([]);
  const [query, setQuery] = useState("");
  const [catalog, setCatalog] = useState<DocsetCatalogItem[] | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [jobs, setJobs] = useState<Record<string, Job>>({});
  // The catalogue lives on devdocs.io: only contact it after the user asks.
  const [browsing, setBrowsing] = useState(false);

  const loadInstalled = () => api.docsets().then(setInstalled).catch(() => {});

  useEffect(() => {
    loadInstalled();
  }, []);

  useEffect(() => {
    if (!browsing) return;
    const handle = setTimeout(() => {
      setCatalogError(null);
      api
        .docsetCatalog(query, 24)
        .then((r) => setCatalog(r.results))
        .catch((e) => setCatalogError(e instanceof ApiError ? e.message : String(e)));
    }, 250);
    return () => clearTimeout(handle);
  }, [query, browsing]);

  useEffect(() => {
    const running = Object.values(jobs).filter((j) => j.status === "queued" || j.status === "running");
    if (running.length === 0) return;
    const id = setInterval(async () => {
      for (const [slug, j] of Object.entries(jobs)) {
        if (j.status !== "queued" && j.status !== "running") continue;
        const updated = await api.job(j.id);
        setJobs((prev) => ({ ...prev, [slug]: updated }));
        if (updated.status === "done") loadInstalled();
      }
    }, 900);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobs]);

  async function install(slug: string) {
    const res = await api.installDocset(slug);
    const job: Job = {
      id: res.job_id,
      kind: "install_docset",
      status: "queued",
      progress: 0,
      message: null,
      error: null,
      created_at: "",
      updated_at: "",
      result: null,
    };
    setJobs((prev) => ({ ...prev, [slug]: job }));
  }

  return (
    <div className="stack-gap">
      <div className="card">
        <div className="section-title">{t(lang, "docsets_installed")}</div>
        {installed.length === 0 && (
          <div className="empty-state" style={{ padding: "30px 0" }}>
            <BookOpen size={32} />
            <div>{t(lang, "docsets_empty")}</div>
          </div>
        )}
        {installed.map((d) => (
          <div className="lib-row" key={d.slug}>
            <div>
              <span className="lib-name">{d.name}</span>
              {d.version && (
                <span className="text-dim" style={{ marginLeft: 8, fontSize: 12 }}>
                  {d.version}
                </span>
              )}
            </div>
            <span className="text-dim small">
              {d.entry_count.toLocaleString()} {t(lang, "docsets_sections")}
            </span>
          </div>
        ))}
      </div>

      <FolderCard lang={lang} />

      <div className="card">
        <div className="section-title">{t(lang, "docsets_catalog")}</div>
        <div className="text-dim small" style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 12 }}>
          <Globe size={13} /> {t(lang, "docsets_network_note")}
        </div>
        {!browsing && (
          <button className="btn" onClick={() => setBrowsing(true)}>
            <Globe size={14} /> {t(lang, "docsets_load_catalog")}
          </button>
        )}
        {browsing && (
          <input
            className="input"
            style={{ width: "100%", marginBottom: 12 }}
            placeholder={t(lang, "docsets_search_placeholder")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        )}
        {catalogError && <div className="finding">{catalogError}</div>}
        {catalog?.map((item) => {
          const job = jobs[item.slug];
          const already = installed.some((d) => d.slug === item.slug);
          return (
            <div className="lib-row" key={item.slug}>
              <div>
                <span className="lib-name">{item.name}</span>
                <span className="text-dim" style={{ marginLeft: 8, fontSize: 12 }}>
                  {item.version} · {item.db_size_kb} KB
                </span>
              </div>
              {job && job.status === "error" ? (
                <span className="badge badge-error" title={job.error ?? ""}>{t(lang, "error")}</span>
              ) : job && job.status !== "done" ? (
                <span className="install-progress">
                  <span className="progress-bar" style={{ width: 120 }}>
                    <span className="progress-bar-fill" style={{ width: `${Math.round(job.progress * 100)}%`, display: "block" }} />
                  </span>
                  <span className="text-dim small">{Math.round(job.progress * 100)}%</span>
                </span>
              ) : (
                <button className="btn" disabled={already} onClick={() => install(item.slug)}>
                  <Download size={13} /> {already ? t(lang, "libraries_status_done") : t(lang, "docsets_install")}
                </button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
