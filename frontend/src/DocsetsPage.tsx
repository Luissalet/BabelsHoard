import { useEffect, useState } from "react";
import { BookOpen, Download, Globe } from "lucide-react";
import { api, ApiError } from "./api";
import type { Lang } from "./i18n";
import { t } from "./i18n";
import type { DocsetCatalogItem, DocsetEntry, Job } from "./types";

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
