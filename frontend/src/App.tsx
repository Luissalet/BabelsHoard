import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { ActivityPage } from "./ActivityPage";
import { api } from "./api";
import { CheckPage } from "./CheckPage";
import { DocsetsPage } from "./DocsetsPage";
import { useLang, useTheme } from "./hooks";
import { t } from "./i18n";
import { LibrariesPage } from "./LibrariesPage";
import { SearchPage } from "./SearchPage";
import { SettingsPage } from "./SettingsPage";
import type { View } from "./Sidebar";
import { Sidebar } from "./Sidebar";
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
  const [view, setView] = useState<View>("search");
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
              <span className="badge badge-indexing" style={{ display: "inline-flex", gap: 5, alignItems: "center" }}>
                <Loader2 size={12} className="spin" />
                {t(lang, "jobs_running")}: {runningJobs.length}
              </span>
            )}
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
