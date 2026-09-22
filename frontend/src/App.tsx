import { useEffect, useState } from "react";
import { Languages, Loader2, Moon, Sun } from "lucide-react";
import { ActivityPage } from "./ActivityPage";
import { api } from "./api";
import { CheckPage } from "./CheckPage";
import { DocsetsPage } from "./DocsetsPage";
import { useHashView, useLang, useTheme } from "./hooks";
import { t } from "./i18n";
import { LibrariesPage } from "./LibrariesPage";
import { SearchPage } from "./SearchPage";
import { SettingsPage } from "./SettingsPage";
import type { View } from "./Sidebar";
import { Sidebar, VIEWS } from "./Sidebar";
import type { Job } from "./types";

const TITLE_KEY: Record<View, Parameters<typeof t>[1]> = {
  search: "nav_search",
  libraries: "nav_libraries",
  check: "nav_check",
  docsets: "nav_docsets",
  activity: "nav_activity",
  settings: "nav_settings",
};

export default function App() {
  const [view, setView] = useHashView<View>(VIEWS, "search");
  const [lang, setLang] = useLang();
  const [theme, setTheme] = useTheme();
  const [runningJobs, setRunningJobs] = useState<Job[]>([]);

  useEffect(() => {
    const load = () =>
      api
        .jobs(10)
        .then((jobs) => setRunningJobs(jobs.filter((j) => j.status === "queued" || j.status === "running")))
        .catch(() => {});
    load();
    const id = setInterval(load, 2000);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="app-shell">
      <Sidebar view={view} onChange={setView} lang={lang} />
      <div className="main">
        <div className="header">
          <div className="header-title">{t(lang, TITLE_KEY[view])}</div>
          <div className="header-actions">
            {runningJobs.length > 0 && (
              <span className="badge badge-indexing" title={runningJobs.map((j) => j.message ?? j.kind).join("\n")}>
                <Loader2 size={12} className="spin" />
                {t(lang, "jobs_running")}: {runningJobs.length}
              </span>
            )}
            <button className="btn btn-ghost btn-icon" onClick={() => setLang(lang === "es" ? "en" : "es")} title={t(lang, "settings_language")}>
              <Languages size={16} /> {lang === "es" ? "ES" : "EN"}
            </button>
            <button
              className="btn btn-ghost btn-icon"
              aria-label={t(lang, "toggle_theme")}
              title={t(lang, "toggle_theme")}
              onClick={() => {
                const dark = theme === "dark" || (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
                setTheme(dark ? "light" : "dark");
              }}
            >
              {theme === "dark" || (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches) ? <Sun size={16} /> : <Moon size={16} />}
            </button>
          </div>
        </div>
        <div className="content">
          {view === "search" && <SearchPage lang={lang} />}
          {view === "libraries" && <LibrariesPage lang={lang} />}
          {view === "check" && <CheckPage lang={lang} />}
          {view === "docsets" && <DocsetsPage lang={lang} />}
          {view === "activity" && <ActivityPage lang={lang} />}
          {view === "settings" && <SettingsPage lang={lang} setLang={setLang} theme={theme} setTheme={setTheme} />}
        </div>
      </div>
    </div>
  );
}
