import { useEffect, useState } from "react";
import { Brain, Layers, RefreshCw } from "lucide-react";
import { api, ApiError } from "./api";
import type { Lang } from "./i18n";
import { t } from "./i18n";
import type { BackendCapability, BackendConfig, BackendStatus } from "./types";

const CAP_ICON: Record<string, typeof Brain> = {
  llm: Brain,
  embeddings: Layers,
};

function CapabilityRow({ lang, cap }: { lang: Lang; cap: BackendCapability }) {
  const Icon = CAP_ICON[cap.capability] ?? Brain;
  const resolved = cap.state === "resolved";
  // An explicit override is taken as given (not probed), so it is
  // "configured", not "connected": the server may not be running yet.
  const explicit = resolved && cap.details?.source === "explicit";
  return (
    <div className="models-row">
      <div className="models-row-top">
        <Icon size={16} className="text-dim" />
        <span className="mono" style={{ fontWeight: 600 }}>
          {cap.capability}
        </span>
        <span className={`badge ${resolved ? "badge-done" : "badge-pending"}`}>
          {explicit ? t(lang, "models_configured") : resolved ? t(lang, "models_resolved") : t(lang, "models_unavailable")}
        </span>
        {resolved && cap.provider && !(explicit && cap.provider === "configured") && <span className="text-dim small mono">{cap.provider}</span>}
        {resolved && cap.model && <span className="text-dim small mono">{cap.model}</span>}
      </div>
      <div className="text-dim small" style={{ marginTop: 4 }}>
        {cap.reason}
      </div>
    </div>
  );
}

export function ModelsPanel({ lang }: { lang: Lang }) {
  const [status, setStatus] = useState<BackendStatus | null>(null);
  const [config, setConfig] = useState<BackendConfig | null>(null);
  const [checking, setChecking] = useState(false);
  const [showOverrides, setShowOverrides] = useState(false);
  const [faustusUrl, setFaustusUrl] = useState("");
  const [faustusToken, setFaustusToken] = useState("");
  const [llmUrl, setLlmUrl] = useState("");
  const [llmModel, setLlmModel] = useState("");
  const [embedUrl, setEmbedUrl] = useState("");
  const [embedModel, setEmbedModel] = useState("");
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const load = () => {
    api.backendStatus().then(setStatus).catch(() => {});
    api.backendConfigGet().then((c) => {
      setConfig(c);
      setFaustusUrl(c.faustus.url ?? "");
      setLlmUrl(c.capabilities.llm?.url ?? "");
      setLlmModel(c.capabilities.llm?.model ?? "");
      setEmbedUrl(c.capabilities.embeddings?.url ?? "");
      setEmbedModel(c.capabilities.embeddings?.model ?? "");
    }).catch(() => {});
  };

  useEffect(load, []);

  const recheck = () => {
    setChecking(true);
    api
      .backendRecheck()
      .then(setStatus)
      .catch(() => {})
      .finally(() => setChecking(false));
  };

  // An empty field is sent as "" so clearing it removes that override on
  // the server; the token is only sent when typed (or "" to forget it).
  const send = async (patch: Parameters<typeof api.backendConfig>[0]) => {
    setSaveError(null);
    try {
      const result = await api.backendConfig(patch);
      setConfig(result.config);
      setStatus(result.status);
      setFaustusToken("");
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : String(err));
    }
  };

  const save = () =>
    send({
      faustus: faustusToken ? { url: faustusUrl.trim(), token: faustusToken } : { url: faustusUrl.trim() },
      capabilities: {
        llm: { url: llmUrl.trim(), model: llmModel.trim() },
        embeddings: { url: embedUrl.trim(), model: embedModel.trim() },
      },
    });

  const forgetToken = () => send({ faustus: { token: "" } });

  return (
    <div className="card">
      <div className="section-title" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span>{t(lang, "models_title")}</span>
        <button className="btn btn-ghost btn-small" onClick={recheck} disabled={checking}>
          <RefreshCw size={13} className={checking ? "spin" : ""} /> {t(lang, "models_recheck")}
        </button>
      </div>
      <p className="text-dim small" style={{ marginTop: 0 }}>
        {t(lang, "models_intro")}
      </p>
      {status?.config_error && (
        <div className="models-warning small">
          {t(lang, "models_config_error")} {status.config_error}
        </div>
      )}
      <div className="stack-gap" style={{ gap: 10 }}>
        {status?.used.map((capName) => {
          const cap = status.capabilities[capName];
          return cap ? <CapabilityRow key={capName} lang={lang} cap={cap} /> : null;
        })}
      </div>

      <button className="btn btn-ghost btn-small" style={{ marginTop: 12 }} onClick={() => setShowOverrides((v) => !v)}>
        {t(lang, "models_overrides_toggle")}
      </button>

      {showOverrides && (
        <div className="stack-gap" style={{ marginTop: 10, gap: 8 }}>
          <div className="text-dim small">{t(lang, "models_faustus_hint")}</div>
          <input className="input" placeholder="http://127.0.0.1:7000" value={faustusUrl} onChange={(e) => setFaustusUrl(e.target.value)} />
          <input
            className="input"
            type="password"
            placeholder={config?.faustus.token_set ? t(lang, "models_token_set_placeholder") : t(lang, "models_token_placeholder")}
            value={faustusToken}
            onChange={(e) => setFaustusToken(e.target.value)}
          />
          {config?.faustus.token_set && (
            <div>
              <button className="btn btn-ghost btn-small" onClick={forgetToken}>
                {t(lang, "models_forget_token")}
              </button>
            </div>
          )}
          <div className="text-dim small">llm</div>
          <input className="input" placeholder="http://127.0.0.1:8081/v1/chat/completions" value={llmUrl} onChange={(e) => setLlmUrl(e.target.value)} />
          <input className="input" placeholder={t(lang, "models_model_placeholder")} value={llmModel} onChange={(e) => setLlmModel(e.target.value)} />
          <div className="text-dim small">embeddings</div>
          <input className="input" placeholder="http://127.0.0.1:11434" value={embedUrl} onChange={(e) => setEmbedUrl(e.target.value)} />
          <input className="input" placeholder={t(lang, "models_model_placeholder")} value={embedModel} onChange={(e) => setEmbedModel(e.target.value)} />
          <div>
            <button className="btn btn-primary btn-small" onClick={save}>
              {saved ? t(lang, "models_saved") : t(lang, "models_save")}
            </button>
          </div>
          {saveError && <div className="models-warning small">{saveError}</div>}
        </div>
      )}
    </div>
  );
}
