import { useEffect, useState } from "react";
import { Loader2, MessageSquareText, Search as SearchIcon, Sparkles } from "lucide-react";
import { api, ApiError } from "./api";
import type { DetailTarget } from "./EntryDetail";
import { EntryDetail } from "./EntryDetail";
import type { Lang } from "./i18n";
import { kindLabel, t } from "./i18n";
import { InlineMarkdown } from "./Markdown";
import { Signature } from "./Signature";
import type { AskResult, Environment, HealthInfo, Library, SearchHit } from "./types";

/** Short label for a citation: the last two parts of the qualname
 * ("lib-..:httpx.Client.get" -> "Client.get"). */
function citeLabel(id: string): string {
  const qualname = id.split(":").pop() ?? id;
  return qualname.split(".").slice(-2).join(".");
}

/** Splits an answer on "[id]" citations and renders known ones as buttons
 * that open the cited entry, exactly like clicking a search result. An id
 * the model made up (not one of the excerpts it was given) is shown struck
 * through, so an unsupported claim is visible as such. The rest goes
 * through the inline Markdown renderer (models love `code`). */
function AnswerText({
  text,
  ids,
  onOpen,
  unknownTitle,
}: {
  text: string;
  ids: Set<string>;
  onOpen: (id: string) => void;
  unknownTitle: string;
}) {
  const parts = text.split(/(\[[^\[\]\s]{1,200}\])/g);
  return (
    <>
      {parts.map((part, i) => {
        const m = /^\[([^\[\]\s]{1,200})\]$/.exec(part);
        if (m && ids.has(m[1])) {
          return (
            <button key={i} className="ask-cite" onClick={() => onOpen(m[1])} title={m[1]}>
              {citeLabel(m[1])}
            </button>
          );
        }
        if (m && m[1].includes(":")) {
          return (
            <span key={i} className="ask-cite ask-cite-unknown" title={`${unknownTitle}: ${m[1]}`}>
              {citeLabel(m[1])}
            </span>
          );
        }
        return <InlineMarkdown key={i} text={part} />;
      })}
    </>
  );
}

const ECOSYSTEMS = ["python", "js", "docset", "markdown"];
const KINDS = ["module", "class", "function", "method", "attribute", "property", "section"];
const EXAMPLES = ["send a get request", "parse json", "timeout", "validate a model", "router"];
// "sqlalchemy.ext.asyncio.async_sessionmaker", "@tanstack/react-query.useQuery"
const DOTTED = /^(@[\w.-]+\/)?[A-Za-z_][\w-]*(\.[A-Za-z_]\w*)+$/;

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
  const [llmReason, setLlmReason] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const [askResult, setAskResult] = useState<AskResult | null>(null);

  useEffect(() => {
    api.libraries().then((libs) => setLibraries(libs.filter((l) => l.status === "done" || l.status === "partial"))).catch(() => {});
    api.environments().then(setEnvironments).catch(() => {});
    api.health().then(setHealth).catch(() => {});
    api
      .backendStatus()
      .then((s) => setLlmReason(s.capabilities.llm?.state === "resolved" ? null : s.capabilities.llm?.reason ?? null))
      .catch(() => {});
  }, []);

  const askTheDocs = () => {
    if (!query.trim()) return;
    setAsking(true);
    setAskResult(null);
    api
      .ask(query, { ecosystem: ecosystem || undefined, kind: kind || undefined, library: libraryFilter || undefined, env: envFilter || undefined })
      .then(setAskResult)
      .catch((err: unknown) =>
        setAskResult({ available: false, reason: err instanceof ApiError ? err.message : String(err) })
      )
      .finally(() => setAsking(false));
  };

  useEffect(() => {
    setAskResult(null);
  }, [query, ecosystem, kind, libraryFilter, envFilter]);

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
  const dotted = DOTTED.test(query.trim()) ? query.trim() : null;
  const exactHit = dotted !== null && results.some((r) => r.qualname === dotted);
  const openSymbol = (qualname: string) =>
    setSelected({ id: qualname, qualname, kind: "symbol", library: null, env_id: envFilter || null });

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
              {kindLabel(lang, k)}
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

      {dotted && !exactHit && (
        <button className="lookup-exact" onClick={() => openSymbol(dotted)}>
          <SearchIcon size={14} />
          <span>
            {t(lang, "search_lookup_exact")} <code>{dotted}</code>
          </span>
          <span className="text-dim small">{t(lang, "search_lookup_exact_hint")}</span>
        </button>
      )}

      {query.trim() && results.length > 0 && (
        <div className="stack-gap" style={{ gap: 8, marginBottom: 4 }}>
          <button
            className="btn btn-ghost btn-small"
            style={{ alignSelf: "flex-start" }}
            onClick={askTheDocs}
            disabled={asking || !!llmReason}
            title={llmReason ?? undefined}
          >
            {asking ? <Loader2 size={14} className="spin" /> : <MessageSquareText size={14} />}
            {t(lang, "ask_button")}
          </button>
          {llmReason && <div className="text-dim small">{t(lang, "ask_disabled_tooltip")}</div>}

          {askResult && askResult.available && askResult.answered && (
            <div className="ask-answer">
              {askResult.semantic_rerank && (
                <div className="text-dim small" style={{ marginBottom: 6, display: "flex", alignItems: "center", gap: 4 }}>
                  <Sparkles size={12} /> {t(lang, "ask_semantic_on")}
                </div>
              )}
              <div className="ask-answer-text">
                <AnswerText
                  unknownTitle={t(lang, "ask_cite_unknown")}
                  text={askResult.answer ?? ""}
                  ids={new Set((askResult.entries ?? []).map((e) => e.id))}
                  onOpen={(id) => {
                    const e = askResult.entries?.find((x) => x.id === id);
                    if (e) setSelected({ id: e.id, qualname: e.qualname, kind: e.kind, library: e.library, env_id: envFilter || null });
                  }}
                />
              </div>
              {(askResult.cited ?? []).length > 0 && (
                <div className="ask-sources small">
                  <span className="text-dim">{t(lang, "ask_sources")}:</span>
                  {(askResult.cited ?? []).map((id) => {
                    const e = askResult.entries?.find((x) => x.id === id);
                    return e ? (
                      <button
                        key={id}
                        className="ask-cite"
                        title={id}
                        onClick={() => setSelected({ id: e.id, qualname: e.qualname, kind: e.kind, library: e.library, env_id: envFilter || null })}
                      >
                        {e.qualname}
                      </button>
                    ) : null;
                  })}
                </div>
              )}
              {askResult.model && (
                <div className="text-dim small" style={{ marginTop: 8 }}>
                  {t(lang, "ask_answered_by")} {askResult.model}
                </div>
              )}
            </div>
          )}
          {askResult && askResult.available && askResult.answered === false && (
            <div className="ask-answer text-dim small">{askResult.message ?? t(lang, "ask_no_hits")}</div>
          )}
          {askResult && !askResult.available && (
            <div className="ask-answer text-dim small">{askResult.reason}</div>
          )}
        </div>
      )}

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
          {!dotted && <div className="text-dim small">{t(lang, "search_no_results_hint")}</div>}
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
                <span className={`badge badge-kind kind-${hit.kind}`}>{kindLabel(lang, hit.kind)}</span>
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
