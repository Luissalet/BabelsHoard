import { useEffect, useState } from "react";
import { Moon, Sun, SunMoon } from "lucide-react";
import { api } from "./api";
import type { Theme } from "./hooks";
import type { Lang } from "./i18n";
import { t } from "./i18n";
import type { HealthInfo } from "./types";

export function SettingsPage({
  lang,
  setLang,
  theme,
  setTheme,
}: {
  lang: Lang;
  setLang: (l: Lang) => void;
  theme: Theme;
  setTheme: (t: Theme) => void;
}) {
  const [health, setHealth] = useState<HealthInfo | null>(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => {});
  }, []);

  return (
    <div className="stack-gap">
      <div className="card">
        <div className="section-title">{t(lang, "settings_theme")}</div>
        <div className="chip-row" style={{ margin: 0 }}>
          <button className={`btn${theme === "light" ? " btn-primary" : ""}`} onClick={() => setTheme("light")}>
            <Sun size={14} /> {t(lang, "settings_theme_light")}
          </button>
          <button className={`btn${theme === "dark" ? " btn-primary" : ""}`} onClick={() => setTheme("dark")}>
            <Moon size={14} /> {t(lang, "settings_theme_dark")}
          </button>
          <button className={`btn${theme === "system" ? " btn-primary" : ""}`} onClick={() => setTheme("system")}>
            <SunMoon size={14} /> {t(lang, "settings_theme_system")}
          </button>
        </div>
      </div>

      <div className="card">
        <div className="section-title">{t(lang, "settings_language")}</div>
        <div className="chip-row" style={{ margin: 0 }}>
          <button className={`btn${lang === "en" ? " btn-primary" : ""}`} onClick={() => setLang("en")}>
            English
          </button>
          <button className={`btn${lang === "es" ? " btn-primary" : ""}`} onClick={() => setLang("es")}>
            Español
          </button>
        </div>
      </div>

      <div className="card">
        <div className="section-title">{t(lang, "settings_about")}</div>
        <p style={{ fontSize: 13.5, lineHeight: 1.6 }}>{t(lang, "settings_about_body")}</p>
        <p className="text-dim" style={{ fontSize: 12.5 }}>{t(lang, "connect_faustus")}</p>
        {health && (
          <p className="text-dim" style={{ fontSize: 12 }}>
            {t(lang, "settings_version")}: {health.version} · {health.libraries} libraries · {health.entries} entries
          </p>
        )}
      </div>
    </div>
  );
}
