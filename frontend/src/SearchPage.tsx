import { useEffect, useState } from "react";
import { Search as SearchIcon } from "lucide-react";
import { api } from "./api";
import type { DetailTarget } from "./EntryDetail";
import { EntryDetail } from "./EntryDetail";
import type { Lang } from "./i18n";
import { t } from "./i18n";
import { InlineMarkdown } from "./Markdown";
import { Signature } from "./Signature";
import type { Environment, HealthInfo, Library, SearchHit } from "./types";

const ECOSYSTEMS = ["python", "js", "docset", "markdown"];
const KINDS = ["module", "class", "function", "method", "attribute", "property", "section"];
const EXAMPLES = ["send a get request", "parse json", "timeout", "validate a model", "router"];

export function SearchPage({ lang }: { lang: Lang }) {
  const [query, setQuery] = useState("");
  const [ecosystem, setEcosystem] = useState<string>("");
  const [kind, setKind] = useState<string>("");
  const [libraryFilter, setLibraryFilter] = useState<string>("");
  const [envFilter, setEnvFilter] = useState<string>("");
  const [results, setResults] = useState<SearchHit[]>([]);
  const [didYouMean, setDidYouMean] = useState<string[]>([]);
  const [searched, setSearched] = useState(false);
  const [libraries, setLibraries] = useState<Library[]>([]);
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [health, setHealth] = useState<HealthInfo | null>(null);
  const [selected, setSelected] = useState<DetailTarget | null>(null);

  useEffect(() => {
    api.libraries().then((libs) => setLibraries(libs.filter((l) => l.status === "done" || l.status === "partial"))).catch(() => {});
    api.environments().then(setEnvironments).catch(() => {});
    api.health().then(setHealth).catch(() => {});
  }, []);

  useEffect(() => {
    const handle = setTimeout(() => {
      if (!query.trim()) {
        setResults([]);
        setSearched(false);
        return;
      }
      api
        .search(query, {
          ecosystem: ecosystem || undefined,
          kind: kind || undefined,
          library: libraryFilter || undefined,
          env: envFilter || undefined,
          limit: 20,
        })
        .then((r) => {
          setResults(r.results);
          setDidYouMean(r.did_you_mean ?? []);
          setSearched(true);
        })
        .catch(() => setResults([]));
    }, 180);
    return () => clearTimeout(handle);
  }, [query, ecosystem, kind, libraryFilter, envFilter]);

  const libraryNames = [...new Set(libraries.map((l) => l.name))].sort();
  const nothingIndexed = libraries.length === 0;

  return (
    <div className="page">
      <div className="search-bar">
        <SearchIcon size={18} className="text-dim" />
        <input
          autoFocus
          aria-label={t(lang, "nav_search")}
          placeholder={t(lang, "search_placeholder")}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      <div className="chip-row">
        <select className="select" value={ecosystem} onChange={(e) => setEcosystem(e.target.value)} aria-label={t(lang, "filter_ecosystem")}>
          <option value="">
            {t(lang, "filter_ecosystem")}: {t(lang, "all")}
          </option>
          {ECOSYSTEMS.map((e) => (
            <option key={e} value={e}>
              {e}
            </option>
          ))}
        </select>
        <select className="select" value={kind} onChange={(e) => setKind(e.target.value)} aria-label={t(lang, "filter_kind")}>
          <option value="">
            {t(lang, "filter_kind")}: {t(lang, "all")}
          </option>
          {KINDS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
        <select className="select" value={libraryFilter} onChange={(e) => setLibraryFilter(e.target.value)} aria-label={t(lang, "filter_library")}>
          <option value="">
            {t(lang, "filter_library")}: {t(lang, "all")}
          </option>
          {libraryNames.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        <select className="select" value={envFilter} onChange={(e) => setEnvFilter(e.target.value)} aria-label={t(lang, "filter_env")}>
          <option value="">
            {t(lang, "filter_env")}: {t(lang, "all")}
          </option>
          {environments.map((e) => (
            <option key={e.id} value={e.id}>
              {e.label}
            </option>
          ))}
        </select>
      </div>

      {!query.trim() && nothingIndexed && (
        <div className="empty-state">
          <SearchIcon size={40} />
          <div className="empty-state-title">{t(lang, "search_empty_title")}</div>
          <div>{t(lang, "search_empty_body")}</div>
        </div>
      )}

      {!query.trim() && !nothingIndexed && (
        <div className="card search-intro">
          {health && (
            <div className="text-dim small">
              {t(lang, "search_indexed", { libs: health.libraries, entries: health.entries.toLocaleString() })}
            </div>
          )}
          <div className="section-title" style={{ marginTop: 12 }}>
            {t(lang, "search_examples")}
          </div>
          <div className="chips">
            {EXAMPLES.map((ex) => (
              <button key={ex} className="chip" onClick={() => setQuery(ex)}>
                {ex}
              </button>
            ))}
          </div>
          <div className="section-title" style={{ marginTop: 16 }}>
            {t(lang, "libraries_indexed")}
          </div>
          <div className="chips">
            {libraryNames.map((n) => (
              <button key={n} className="chip mono" onClick={() => setLibraryFilter(n)}>
                {n}
              </button>
            ))}
          </div>
        </div>
      )}

      {query.trim() && searched && results.length === 0 && (
        <div className="empty-state">
          <SearchIcon size={40} />
          <div>{t(lang, "search_no_results")}</div>
          {didYouMean.length > 0 && (
            <div className="chips" style={{ justifyContent: "center", marginTop: 8 }}>
              {didYouMean.map((s) => (
                <button key={s} className="chip mono" onClick={() => setQuery(s)}>
                  {s}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="result-list">
        {results.map((hit) => (
          <button
            className="result-item"
            key={hit.id}
            onClick={() => setSelected({ id: hit.id, qualname: hit.qualname, kind: hit.kind, library: hit.library, env_id: hit.env_id })}
          >
            <div className="result-top">
              <span className="result-qualname">{hit.qualname}</span>
              <span className="result-meta">
                <span className={`badge badge-kind kind-${hit.kind}`}>{hit.kind}</span>
                {hit.library && <span className="mono">{hit.library}</span>}
              </span>
            </div>
            {hit.signature && hit.kind !== "section" && hit.kind !== "module" && (
              <div className="result-signature">
                <Signature text={hit.signature} />
              </div>
            )}
            {hit.summary && (
              <div className="result-summary">
                <InlineMarkdown text={hit.summary} />
              </div>
            )}
          </button>
        ))}
      </div>

      {selected && (
        <EntryDetail
          target={selected}
          lang={lang}
          onClose={() => setSelected(null)}
          onOpenSymbol={(qualname) =>
            setSelected({ id: qualname, qualname, kind: "symbol", library: null, env_id: selected.env_id })
          }
        />
      )}
    </div>
  );
}
