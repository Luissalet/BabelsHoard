import { Activity, BookOpen, Library, Search, Settings, ShieldCheck } from "lucide-react";
import type { Lang } from "./i18n";
import { t } from "./i18n";

export const VIEWS = ["search", "libraries", "check", "docsets", "activity", "settings"] as const;
export type View = (typeof VIEWS)[number];

const ITEMS: { view: View; icon: typeof Search; key: Parameters<typeof t>[1] }[] = [
  { view: "search", icon: Search, key: "nav_search" },
  { view: "libraries", icon: Library, key: "nav_libraries" },
  { view: "check", icon: ShieldCheck, key: "nav_check" },
  { view: "docsets", icon: BookOpen, key: "nav_docsets" },
  { view: "activity", icon: Activity, key: "nav_activity" },
  { view: "settings", icon: Settings, key: "nav_settings" },
];

export function Sidebar({ view, onChange, lang }: { view: View; onChange: (v: View) => void; lang: Lang }) {
  return (
    <nav className="sidebar" aria-label="Sections">
      <div className="sidebar-brand">
        <div className="sidebar-brand-icon" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 20h16" />
            <path d="M6 20V9l6-5 6 5v11" />
            <path d="M10 20v-5h4v5" />
            <path d="M9 10h6" />
          </svg>
        </div>
        <div className="sidebar-brand-text">
          <div className="sidebar-brand-name">{t(lang, "appName")}</div>
          <div className="sidebar-brand-tagline">{t(lang, "tagline")}</div>
        </div>
      </div>
      <div className="nav">
        {ITEMS.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.view}
              className={`nav-item${view === item.view ? " active" : ""}`}
              aria-current={view === item.view ? "page" : undefined}
              onClick={() => onChange(item.view)}
            >
              <Icon size={17} />
              <span>{t(lang, item.key)}</span>
            </button>
          );
        })}
      </div>
      <div className="sidebar-footer">
        <span className="status-dot" /> {t(lang, "local_only")}
      </div>
    </nav>
  );
}
