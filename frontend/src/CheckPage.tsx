import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Info, Loader2, ShieldCheck, XCircle } from "lucide-react";
import { api, ApiError } from "./api";
import { CodeEditor } from "./CodeEditor";
import type { Lang } from "./i18n";
import { t } from "./i18n";
import type { CheckResult, Environment, Finding } from "./types";

const SAMPLES: Record<string, string> = {
  python: `import httpx

with httpx.Client(timeout=10) as client:
    response = client.get("https://example.com", follow_redirects=True)
    response.raise_for_status()
    print(response.json())
`,
  typescript: `import { useState, useEffect } from "react";
`,
};

function supports(env: Environment | undefined, language: string): boolean {
  if (!env) return false;
  return language === "python" ? Boolean(env.python_path) : Boolean(env.node_modules_path);
}

/** The environment a check in `language` uses when none is chosen. */
function defaultFor(envs: Environment[], language: string): Environment | undefined {
  return (
    envs.find((e) => e.default_for?.includes(language)) ??
    [...envs].reverse().find((e) => supports(e, language)) ??
    envs.find((e) => e.is_default) ??
    envs[envs.length - 1]
  );
}

export function CheckPage({ lang }: { lang: Lang }) {
  const [language, setLanguage] = useState("python");
  const [code, setCode] = useState(SAMPLES.python);
  const [envId, setEnvId] = useState<string>("");
  const [environments, setEnvironments] = useState<Environment[]>([]);
  const [result, setResult] = useState<CheckResult | null>(null);
  const [checkedCode, setCheckedCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .environments()
      .then((envs) => {
        setEnvironments(envs);
        const def = defaultFor(envs, "python");
        if (def) setEnvId(def.id);
      })
      .catch(() => {});
  }, []);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const r = await api.checkCode(code, envId || undefined, language);
      setResult(r);
      setCheckedCode(code);
    } catch (e) {
      setResult(null); // never show the previous result next to an error
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const byLine = useMemo(() => {
    const map = new Map<number, Finding[]>();
    for (const f of result?.findings ?? []) {
      map.set(f.line, [...(map.get(f.line) ?? []), f]);
    }
    return [...map.entries()].sort((a, b) => a[0] - b[0]);
  }, [result]);

  const errors = result?.findings.filter((f) => f.severity === "error").length ?? 0;
  const warnings = (result?.findings.length ?? 0) - errors;
  const codeLines = checkedCode.split("\n");
  const stale = result !== null && checkedCode !== code;
  const selectedEnv = environments.find((e) => e.id === envId);
  const envMismatch = selectedEnv !== undefined && !supports(selectedEnv, language);

  return (
    <div className="page stack-gap">
      <div className="card">
        <div className="toolbar">
          <label className="field">
            <span>{t(lang, "check_env")}</span>
            <select
              className="select"
              value={envId}
              onChange={(e) => {
                setEnvId(e.target.value);
                setResult(null); // a result belongs to the environment it was checked against
                setError(null);
              }}
            >
              {environments.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.label}
                  {e.python_version ? ` · Python ${e.python_version}` : ""}
                  {e.node_modules_path ? " · node_modules" : ""}
                  {e.default_for?.includes(language) ? ` (${t(lang, "check_env_default")})` : ""}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span>{t(lang, "check_language")}</span>
            <select
              className="select"
              value={language}
              onChange={(e) => {
                const next = e.target.value;
                if (code === SAMPLES[language]) setCode(SAMPLES[next]);
                setLanguage(next);
                setResult(null);
                // A frontend-only environment cannot check Python (and vice versa).
                if (!supports(selectedEnv, next)) {
                  const def = defaultFor(environments, next);
                  if (def) setEnvId(def.id);
                }
              }}
            >
              <option value="python">Python</option>
              <option value="typescript">TypeScript</option>
            </select>
          </label>
          <div className="toolbar-spacer" />
          <span className="text-dim small">{t(lang, "check_shortcut")}</span>
          <button className="btn btn-primary" onClick={run} disabled={busy || !code.trim() || envMismatch}>
            {busy ? <Loader2 size={14} className="spin" /> : <ShieldCheck size={14} />} {t(lang, "check_button")}
          </button>
        </div>
        {envMismatch && (
          <div className="finding warning" style={{ marginBottom: 10 }}>
            {t(lang, language === "python" ? "check_env_no_python" : "check_env_no_node")}
          </div>
        )}
        <CodeEditor
          value={code}
          onChange={setCode}
          findings={stale ? [] : result?.findings ?? []}
          placeholder={t(lang, "check_placeholder")}
          onSubmit={() => !envMismatch && run()}
        />
        {busy && <div className="text-dim small" style={{ marginTop: 8 }}>{t(lang, "check_checking")}</div>}
        {error && <div className="finding" style={{ marginTop: 10 }}>{error}</div>}
      </div>

      {result && (
        <div className={`card check-result${stale ? " is-stale" : ""}`}>
          <div className="check-summary">
            {result.ok && warnings === 0 ? (
              <span className="summary-pill ok">
                <CheckCircle2 size={15} /> {t(lang, "check_ok")}
              </span>
            ) : (
              <>
                {errors > 0 && (
                  <span className="summary-pill err">
                    <XCircle size={15} /> {t(lang, errors === 1 ? "check_errors_1" : "check_errors", { n: errors })}
                  </span>
                )}
                {warnings > 0 && (
                  <span className="summary-pill warn">
                    <AlertTriangle size={15} /> {t(lang, warnings === 1 ? "check_warnings_1" : "check_warnings", { n: warnings })}
                  </span>
                )}
              </>
            )}
            <span className="text-dim small" title={t(lang, "check_unchecked_help")}>
              {t(lang, "check_checked", { checked: result.checked, unchecked: result.unchecked })}
              <Info size={12} style={{ verticalAlign: "-2px", marginLeft: 4 }} />
            </span>
          </div>

          {byLine.map(([line, findings]) => (
            <div className="annotated" key={line}>
              <div className="annotated-code">
                <span className="annotated-ln">{line}</span>
                <code>{codeLines[line - 1] ?? ""}</code>
              </div>
              {findings.map((f, i) => (
                <div className={`finding ${f.severity === "warning" ? "warning" : ""}`} key={i}>
                  <div className="finding-loc">
                    {t(lang, "check_line")} {f.line}:{f.col + 1} · {f.code}
                  </div>
                  <div>{f.message}</div>
                  {f.suggestion && (
                    <div className="finding-suggestion">
                      {t(lang, "did_you_mean")} <code>{f.suggestion}</code>?
                    </div>
                  )}
                </div>
              ))}
            </div>
          ))}

          {result.libraries.length > 0 && (
            <div className="chips" style={{ marginTop: 12 }}>
              {result.libraries.map((l) => (
                <span key={l} className="chip chip-static mono">
                  {l.replace(/^stdlib\//, "")}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
