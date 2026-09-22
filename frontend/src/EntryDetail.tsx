import { useEffect, useState } from "react";
import { AlertTriangle, Check, Copy, FileCode2, X } from "lucide-react";
import { api } from "./api";
import type { Key, Lang } from "./i18n";
import { kindLabel, t } from "./i18n";
import { InlineMarkdown, Markdown } from "./Markdown";
import { Signature } from "./Signature";
import type { LookupResult, Param } from "./types";

export interface DetailTarget {
  id: string;
  qualname: string;
  kind: string;
  library: string | null;
  env_id: string | null;
}

function CopyButton({ text, lang }: { text: string; lang: Lang }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      className="btn btn-ghost btn-small"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1200);
        } catch {
          /* clipboard unavailable (e.g. insecure context) */
        }
      }}
    >
      {copied ? <Check size={13} /> : <Copy size={13} />}
      {copied ? t(lang, "copied") : t(lang, "copy")}
    </button>
  );
}

const KIND_KEY: Record<string, Key> = {
  "positional-only": "kind_positional_only",
  "positional-or-keyword": "kind_positional_or_keyword",
  "keyword-only": "kind_keyword_only",
};

function ParamsTable({ params, lang }: { params: Param[]; lang: Lang }) {
  const described = params.some((p) => p.description);
  return (
    <table className={`table params-table${described ? "" : " no-desc"}`}>
      <thead>
        <tr>
          <th className="col-name">{t(lang, "detail_name")}</th>
          <th className="col-type">{t(lang, "detail_type")}</th>
          <th className="col-default">{t(lang, "detail_default")}</th>
          {described && <th>{t(lang, "detail_description")}</th>}
        </tr>
      </thead>
      <tbody>
        {params.map((p) => (
          <tr key={p.name}>
            <td>
              <div className="mono param-name">
                {p.kind === "*args" ? "*" : p.kind === "**kwargs" ? "**" : ""}
                {p.name}
              </div>
              <div className="param-tags">
                {p.required && <span className="tag tag-accent">{t(lang, "detail_required")}</span>}
                {KIND_KEY[p.kind] && p.kind !== "positional-or-keyword" && (
                  <span className="tag">{t(lang, KIND_KEY[p.kind])}</span>
                )}
              </div>
            </td>
            <td className="mono cell-type">{p.annotation ?? ""}</td>
            <td className="mono cell-default">{p.default ?? ""}</td>
            {described && (
              <td className="cell-desc">
                <InlineMarkdown text={p.description ?? ""} />
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function EntryDetail({
  target,
  lang,
  onClose,
  onOpenSymbol,
}: {
  target: DetailTarget;
  lang: Lang;
  onClose: () => void;
  onOpenSymbol: (qualname: string) => void;
}) {
  const [data, setData] = useState<LookupResult | null>(null);
  const [section, setSection] = useState<{ text: string; signature?: string | null } | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setData(null);
    setSection(null);
    async function run() {
      if (target.kind === "section") {
        const r = await api.readEntry(target.id, 0, 20000);
        if (!cancelled) setSection({ text: r.text, signature: r.signature });
      } else {
        const r = await api.lookup(target.qualname, target.env_id ?? undefined);
        if (!cancelled) setData(r);
      }
      if (!cancelled) setLoading(false);
    }
    run().catch(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [target.id, target.qualname, target.env_id, target.kind]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      <div className="detail-backdrop" onClick={onClose} />
      <aside className="detail-panel" aria-label={target.qualname}>
        <div className="detail-head">
          <div style={{ minWidth: 0 }}>
            <div className="detail-title mono">{target.qualname}</div>
            <div className="detail-sub">
              <span className="badge badge-neutral">{kindLabel(lang, data?.kind ?? target.kind)}</span>
              {(data?.library ?? target.library) && <span>{data?.library ?? target.library}</span>}
            </div>
          </div>
          <button className="btn btn-ghost" onClick={onClose} aria-label={t(lang, "close")}>
            <X size={18} />
          </button>
        </div>

        {loading && <div className="text-dim">{t(lang, "loading")}</div>}

        {!loading && section && <Markdown text={section.text} />}

        {!loading && data?.found && (
          <div className="stack-gap">
            {data.signature && (
              <div className="signature-box">
                <div className="signature-box-bar">
                  <CopyButton text={data.signature} lang={lang} />
                </div>
                <Signature text={data.signature} />
              </div>
            )}
            {data.deprecated && (
              <div className="finding warning">
                <AlertTriangle size={14} style={{ verticalAlign: "-2px", marginRight: 6 }} />
                <strong>{t(lang, "deprecated")}</strong> {data.deprecated_note ?? ""}
              </div>
            )}
            {data.summary && (
              <div className="detail-summary">
                <InlineMarkdown text={data.summary} />
              </div>
            )}
            {data.note && <div className="text-dim small">{t(lang, "detail_partial_note")}</div>}
            {data.same_as && (
              <div className="text-dim small">
                {t(lang, "detail_same_as")}{" "}
                <button className="linklike mono" onClick={() => onOpenSymbol(data.same_as!)}>
                  {data.same_as}
                </button>
              </div>
            )}
            {data.params && data.params.length > 0 && (
              <section>
                <div className="section-title">
                  {t(lang, data.kind === "class" ? "detail_ctor_params" : "detail_params")}
                </div>
                <ParamsTable params={data.params} lang={lang} />
              </section>
            )}
            {data.returns && (
              <section>
                <div className="section-title">{t(lang, "detail_returns")}</div>
                <div className="mono small">{data.returns}</div>
              </section>
            )}
            {data.members && data.members.length > 0 && (
              <section>
                <div className="section-title">{t(lang, "detail_members")}</div>
                <div className="chips">
                  {data.members.map((m) => (
                    <button key={m} className="chip mono" onClick={() => onOpenSymbol(`${data.qualname}.${m}`)}>
                      {m}
                    </button>
                  ))}
                  {(data.members_total ?? 0) > data.members.length && (
                    <span className="text-dim small">
                      {t(lang, "detail_members_more", { n: (data.members_total ?? 0) - data.members.length })}
                    </span>
                  )}
                </div>
              </section>
            )}
            {data.doc && (
              <section>
                <div className="section-title">{t(lang, "detail_doc")}</div>
                <Markdown text={data.doc} />
              </section>
            )}
            {data.other_versions_indexed && data.other_versions_indexed.length > 0 && (
              <div className="text-dim small">
                {t(lang, "other_versions")}: {data.other_versions_indexed.join(", ")}
              </div>
            )}
            {data.source && (
              <div className="detail-source small">
                <FileCode2 size={13} />
                <span className="mono">{data.source}</span>
              </div>
            )}
          </div>
        )}

        {!loading && data && !data.found && (
          <div className="stack-gap">
            <div className="finding">{data.message}</div>
            {data.certain === false && <div className="text-dim small">{t(lang, "not_certain")}</div>}
            {data.suggestions && data.suggestions.length > 0 && (
              <div>
                <div className="section-title">{t(lang, "similar_names")}</div>
                <div className="chips">
                  {data.suggestions.map((s) => (
                    <button key={s} className="chip mono" onClick={() => onOpenSymbol(s)}>
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </aside>
    </>
  );
}
