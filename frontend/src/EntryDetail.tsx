import { useEffect, useState } from "react";
import { Check, Copy, ExternalLink, X } from "lucide-react";
import { api } from "./api";
import type { Lang } from "./i18n";
import { t } from "./i18n";
import type { LookupResult, SearchHit } from "./types";

function CopyButton({ text, lang }: { text: string; lang: Lang }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      className="btn btn-ghost"
      style={{ position: "absolute", top: 8, right: 8, padding: "4px 8px" }}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1200);
        } catch {
          /* clipboard unavailable */
        }
      }}
    >
      {copied ? <Check size={13} /> : <Copy size={13} />}
      {copied ? t(lang, "copied") : t(lang, "copy")}
    </button>
  );
}

export function EntryDetail({ hit, lang, onClose }: { hit: SearchHit; lang: Lang; onClose: () => void }) {
  const [data, setData] = useState<LookupResult | null>(null);
  const [sectionText, setSectionText] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setData(null);
    setSectionText(null);
    async function run() {
      if (hit.kind === "section") {
        const r = await api.readEntry(hit.id, 0, 6000);
        if (!cancelled) setSectionText(r.text);
      } else {
        const libName = hit.library?.split("@")[0];
        const r = await api.lookup(hit.qualname, hit.env_id ?? undefined, libName);
        if (!cancelled) setData(r);
      }
      if (!cancelled) setLoading(false);
    }
    run().catch(() => setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [hit.id, hit.qualname, hit.env_id, hit.library, hit.kind]);

  return (
    <>
      <div className="detail-backdrop" onClick={onClose} />
      <div className="detail-panel">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 14 }}>
          <div>
            <div className="result-qualname" style={{ fontSize: 16 }}>
              {hit.qualname}
            </div>
            {hit.library && <div className="text-dim" style={{ fontSize: 12.5, marginTop: 3 }}>{hit.library}</div>}
          </div>
          <button className="btn btn-ghost" onClick={onClose} aria-label={t(lang, "close")}>
            <X size={18} />
          </button>
        </div>

        {loading && <div className="text-dim">{t(lang, "loading")}</div>}

        {!loading && sectionText !== null && (
          <div className="doc-text">{sectionText}</div>
        )}

        {!loading && data && data.found && (
          <div className="stack-gap">
            {data.signature && (
              <div className="signature-box">
                <CopyButton text={data.signature} lang={lang} />
                {data.signature}
              </div>
            )}
            {data.deprecated && (
              <div className="finding warning">
                <span className="badge badge-partial">{t(lang, "deprecated")}</span>{" "}
                {data.deprecated_note ?? ""}
              </div>
            )}
            {data.params && data.params.length > 0 && (
              <div>
                <div className="section-title">Parameters</div>
                <table className="table">
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th>Kind</th>
                      <th>Default</th>
                      <th>Description</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.params
                      .filter((p) => p.name !== "self" && p.name !== "cls")
                      .map((p) => (
                        <tr key={p.name}>
                          <td className="mono">{p.name}</td>
                          <td className="text-dim">{p.kind}</td>
                          <td className="mono">{p.default ?? "—"}</td>
                          <td>{p.description ?? ""}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </div>
            )}
            {data.returns && (
              <div>
                <div className="section-title">Returns</div>
                <div>{data.returns}</div>
              </div>
            )}
            {data.doc && (
              <div>
                <div className="section-title">Documentation</div>
                <div className="doc-text">{data.doc}</div>
              </div>
            )}
            {data.source_path && (
              <div className="text-dim" style={{ fontSize: 12 }}>
                <ExternalLink size={12} style={{ verticalAlign: "-2px", marginRight: 4 }} />
                {t(lang, "source")}: {data.source_path}
                {data.source_line ? `:${data.source_line}` : ""}
              </div>
            )}
          </div>
        )}

        {!loading && data && !data.found && (
          <div className="text-dim">
            <p>{data.message}</p>
            {data.suggestions && data.suggestions.length > 0 && (
              <p>Did you mean: {data.suggestions.join(", ")}?</p>
            )}
          </div>
        )}
      </div>
    </>
  );
}
