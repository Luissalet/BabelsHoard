import { useEffect, useState } from "react";
import { Search as SearchIcon } from "lucide-react";
import { api } from "./api";
import { EntryDetail } from "./EntryDetail";
import type { Lang } from "./i18n";
import { t } from "./i18n";
import type { Environment, Library, SearchHit } from "./types";

const ECOSYSTEMS = ["python", "js", "docset", "markdown"];
const KINDS = ["module", "class", "function", "method", "attribute", "property", "section"];

export function SearchPage({ lang }: { lang: Lang }) {
  const [query, setQuery] = useState("");
  const [ecosystem, setEcosystem] = useState<string>("");
  const [kind, setKind] = useState<string>("");
  const [libraryFilter, setLibraryFilter] = useState<string>("");
  const [envFilter, setEnvFilter] = useState<string>("");
  const [results, setResults] = useState<SearchHit[]>([]);
  const [libraries, setLibraries] = useState<Library[]>([]);
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [selected, setSelected] = useState<SearchHit | null>(null);
  const [hasIndexed, setHasIndexed] = useState<boolean | null>(null);

  useEffect(() => {
    api.libraries().then(setLibraries).catch(() => {});
    api.environments().then(setEnvironments).catch(() => {});
  }, []);

  useEffect(() => {
    setHasIndexed(libraries.length > 0);
  }, [libraries]);

  useEffect(() => {
    const handle = setTimeout(() => {
      if (!query.trim()) {
        setResults([]);
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
        .then((r) => setResults(r.results))
        .catch(() => setResults([]));
    }, 200);
    return () => clearTimeout(handle);
  }, [query, ecosystem, kind, libraryFilter, envFilter]);

  return (
    <div>
      <div className="search-bar">
        <SearchIcon size={18} className="text-dim" />
        <input
          autoFocus
          placeholder={t(lang, "search_placeholder")}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      <div className="chip-row">
        <select className="select" value={ecosystem} onChange={(e) => setEcosystem(e.target.value)}>
          <option value="">{t(lang, "filter_ecosystem")}: {t(lang, "all")}</option>
          {ECOSYSTEMS.map((e) => (
            <option key={e} value={e}>
              {e}
            </option>
          ))}
        </select>
        <select className="select" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">{t(lang, "filter_kind")}: {t(lang, "all")}</option>
          {KINDS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
        <select className="select" value={libraryFilter} onChange={(e) => setLibraryFilter(e.target.value)}>
          <option value="">{t(lang, "filter_library")}: {t(lang, "all")}</option>
          {[...new Set(libraries.map((l) => l.name))].map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        <select className="select" value={envFilter} onChange={(e) => setEnvFilter(e.target.value)}>
          <option value="">{t(lang, "filter_env")}: {t(lang, "all")}</option>
          {environments.map((e) => (
            <option key={e.id} value={e.id}>
              {e.label}
            </option>
          ))}
        </select>
      </div>

      {!query.trim() && hasIndexed === false && (
        <div className="empty-state">
          <SearchIcon size={40} />
          <div className="empty-state-title">{t(lang, "search_empty_title")}</div>
          <div>{t(lang, "search_empty_body")}</div>
        </div>
      )}

      {query.trim() && results.length === 0 && (
        <div className="empty-state">
          <SearchIcon size={40} />
          <div>{t(lang, "search_no_results")}</div>
        </div>
      )}

      <div className="result-list">
        {results.map((hit) => (
          <div className="result-item" key={hit.id} onClick={() => setSelected(hit)}>
            <div className="result-qualname">{hit.qualname}</div>
            {hit.signature && hit.kind !== "section" && (
              <div className="text-dim mono" style={{ fontSize: 12, marginTop: 3 }}>
                {hit.signature}
              </div>
            )}
            {hit.summary && <div className="result-summary">{hit.summary}</div>}
            <div className="result-meta">
              <span className="badge badge-neutral">{hit.kind}</span>
              {hit.library && <span>{hit.library}</span>}
            </div>
          </div>
        ))}
      </div>

      {selected && <EntryDetail hit={selected} lang={lang} onClose={() => setSelected(null)} />}
    </div>
  );
}
