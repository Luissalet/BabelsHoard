import { useEffect, useState } from "react";
import { ShieldCheck } from "lucide-react";
import { api } from "./api";
import type { Lang } from "./i18n";
import { t } from "./i18n";
import type { CheckResult, Environment } from "./types";

const SAMPLE = `import httpx

client = httpx.Client()
response = client.get("https://example.com")
`;

export function CheckPage({ lang }: { lang: Lang }) {
  const [code, setCode] = useState(SAMPLE);
  const [envId, setEnvId] = useState<string>("");
  const [language, setLanguage] = useState("python");
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [result, setResult] = useState<CheckResult | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.environments().then(setEnvironments).catch(() => {});
  }, []);

  async function run() {
    setBusy(true);
    try {
      const r = await api.checkCode(code, envId || undefined, language);
      setResult(r);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack-gap">
      <div className="card">
        <div className="field-row" style={{ marginBottom: 12 }}>
          <label className="text-dim" style={{ fontSize: 12.5 }}>
            {t(lang, "check_env")}
          </label>
          <select className="select" value={envId} onChange={(e) => setEnvId(e.target.value)}>
            <option value="">{t(lang, "all")}</option>
            {environments.map((e) => (
              <option key={e.id} value={e.id}>
                {e.label}
              </option>
            ))}
          </select>
          <label className="text-dim" style={{ fontSize: 12.5 }}>
            {t(lang, "check_language")}
          </label>
          <select className="select" value={language} onChange={(e) => setLanguage(e.target.value)}>
            <option value="python">python</option>
            <option value="typescript">typescript</option>
          </select>
          <button className="btn btn-primary" onClick={run} disabled={busy}>
            <ShieldCheck size={14} /> {t(lang, "check_button")}
          </button>
        </div>
        <textarea
          className="textarea"
          style={{ width: "100%", minHeight: 220, resize: "vertical", lineHeight: 1.5 }}
          spellCheck={false}
          value={code}
          onChange={(e) => setCode(e.target.value)}
          placeholder={t(lang, "check_placeholder")}
        />
      </div>

      {result && (
        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
            <span className={`badge ${result.ok ? "badge-done" : "badge-error"}`}>
              {result.ok
                ? t(lang, "check_ok")
                : result.findings.length === 1
                ? t(lang, "check_errors_one")
                : t(lang, "check_errors_many", { n: result.findings.length })}
            </span>
            <span className="text-dim" style={{ fontSize: 12 }}>
              {t(lang, "check_checked", { checked: result.checked, unchecked: result.unchecked })}
            </span>
          </div>
          {result.findings.map((f, i) => (
            <div className={`finding ${f.severity === "warning" ? "warning" : ""}`} key={i}>
              <div className="finding-loc">
                line {f.line}:{f.col} · {f.code}
              </div>
              <div>{f.message}</div>
              {f.suggestion && <div className="text-dim">Did you mean: {f.suggestion}?</div>}
            </div>
          ))}
          {result.libraries.length > 0 && (
            <div className="text-dim" style={{ fontSize: 12, marginTop: 8 }}>
              {result.libraries.join(", ")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
