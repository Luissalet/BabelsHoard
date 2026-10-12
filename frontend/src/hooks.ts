import { useEffect, useRef, useState } from "react";
import { addRefreshHandler, type EditorSnapshot } from "./copilot-context";
import { detectLang, type Lang } from "./i18n";

/** Reload this view's data in place when the shared assistant finishes a turn (no remount: forms keep what was typed). */
export function useCopilotRefresh(reload: () => void) {
  const latest = useRef(reload);
  latest.current = reload;
  useEffect(() => addRefreshHandler(() => latest.current()), []);
}

/** Answer the assistant's "what is open?" request with this form's snapshot (null = nothing to report). */
export function useContextProvider(snapshot: () => EditorSnapshot | null) {
  const latest = useRef(snapshot);
  latest.current = snapshot;
  useEffect(() => {
    const onRequest = (e: Event) => {
      const detail = (e as CustomEvent<{ provide?: (s: EditorSnapshot) => void }>).detail;
      try {
        const s = latest.current();
        if (s) detail?.provide?.(s);
      } catch {
        /* a form that cannot describe itself is simply left out */
      }
    };
    window.addEventListener("babel:context-request", onRequest);
    return () => window.removeEventListener("babel:context-request", onRequest);
  }, []);
}

export type Theme = "light" | "dark" | "system";

export function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setThemeState] = useState<Theme>(() => {
    try {
      return (localStorage.getItem("babel.theme") as Theme) ?? "system";
    } catch {
      return "system";
    }
  });

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") {
      root.removeAttribute("data-theme");
    } else {
      root.setAttribute("data-theme", theme);
    }
    try {
      localStorage.setItem("babel.theme", theme);
    } catch {
      /* ignore */
    }
  }, [theme]);

  return [theme, setThemeState];
}

export function useLang(): [Lang, (l: Lang) => void] {
  const [lang, setLangState] = useState<Lang>(detectLang);

  useEffect(() => {
    document.documentElement.lang = lang; // screen readers and hyphenation follow the UI language
    try {
      localStorage.setItem("babel.lang", lang);
    } catch {
      /* ignore */
    }
  }, [lang]);

  return [lang, setLangState];
}

export function useHashView<T extends string>(views: readonly T[], fallback: T): [T, (v: T) => void] {
  const read = (): T => {
    const h = window.location.hash.replace(/^#\/?/, "") as T;
    return views.includes(h) ? h : fallback;
  };
  const [view, setViewState] = useState<T>(read);
  useEffect(() => {
    const onHash = () => setViewState(read());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const setView = (v: T) => {
    if (window.location.hash !== `#/${v}`) window.location.hash = `/${v}`;
    setViewState(v);
  };
  return [view, setView];
}
