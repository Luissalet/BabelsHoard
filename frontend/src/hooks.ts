import { useEffect, useState } from "react";
import { detectLang, type Lang } from "./i18n";

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
