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
