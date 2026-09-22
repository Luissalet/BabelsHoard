import { useEffect, useState } from "react";
import { Activity } from "lucide-react";
import { api } from "./api";
import type { Lang } from "./i18n";
import { t } from "./i18n";
import type { AgentCall } from "./types";

export function ActivityPage({ lang }: { lang: Lang }) {
  const [calls, setCalls] = useState<AgentCall[]>([]);

  useEffect(() => {
    const load = () => api.agentCalls(80).then(setCalls).catch(() => {});
    load();
    const id = setInterval(load, 4000);
    return () => clearInterval(id);
  }, []);

  if (calls.length === 0) {
    return (
      <div className="empty-state">
        <Activity size={40} />
        <div className="empty-state-title">{t(lang, "activity_title")}</div>
        <div>{t(lang, "activity_empty")}</div>
      </div>
    );
  }

  return (
    <div className="card">
      <table className="table">
        <thead>
          <tr>
            <th>{t(lang, "activity_time")}</th>
            <th>{t(lang, "activity_tool")}</th>
            <th>Args</th>
            <th>{t(lang, "activity_duration")}</th>
            <th>{t(lang, "activity_status")}</th>
          </tr>
        </thead>
        <tbody>
          {calls.map((c) => (
            <tr key={c.id}>
              <td className="text-dim">{c.ts.replace("T", " ").replace("Z", "")}</td>
              <td className="mono">{c.tool}</td>
              <td className="text-dim">{c.args_summary}</td>
              <td className="text-dim">{c.duration_ms ? `${Math.round(c.duration_ms)} ms` : "—"}</td>
              <td>
                <span className={`badge ${c.ok ? "badge-done" : "badge-error"}`}>
                  {c.ok ? t(lang, "ok") : t(lang, "error")}
                </span>
                {!c.ok && c.error && <div className="text-dim" style={{ fontSize: 11 }}>{c.error}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
