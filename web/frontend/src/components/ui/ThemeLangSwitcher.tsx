/**
 * Compact dropdown for theme + language. Sits in the AppShell topbar.
 */
import { useEffect, useRef, useState } from "react";
import { Moon, Sun, Monitor, Languages, Check } from "lucide-react";
import { useTranslation } from "react-i18next";

import { applyTheme, detectTheme, type Theme } from "@/lib/theme";
import { setLocale, getLocale, type Locale } from "@/lib/i18n";

const THEMES: { id: Theme; Icon: typeof Sun; key: "light" | "dark" | "system" }[] = [
  { id: "light", Icon: Sun, key: "light" },
  { id: "dark", Icon: Moon, key: "dark" },
  { id: "system", Icon: Monitor, key: "system" },
];

const LANGS: { id: Locale; label: string; tag: string }[] = [
  { id: "zh-CN", label: "中文", tag: "中" },
  { id: "en", label: "English", tag: "EN" },
];

export function ThemeLangSwitcher() {
  const { t, i18n } = useTranslation();
  const [theme, setTheme] = useState<Theme>(detectTheme());
  const [, setRerender] = useState(0);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const onPickTheme = (id: Theme) => {
    applyTheme(id);
    setTheme(id);
  };
  const onPickLang = (id: Locale) => {
    setLocale(id);
    setRerender((n) => n + 1);
  };

  const CurrentLang = LANGS.find((l) => l.id === getLocale()) ?? LANGS[0];

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 rounded p-1.5 text-muted hover:bg-bg hover:text-foreground"
        title={t("app.language")}
      >
        <Languages className="size-4" />
        <span className="text-xs font-medium">{CurrentLang.tag}</span>
      </button>
      {open && (
        <div className="absolute right-0 top-full z-50 mt-1 w-44 rounded border border-border bg-card shadow-lg">
          <div className="px-3 pb-1 pt-2 text-[10px] uppercase tracking-wider text-muted">
            {t("app.theme.dark")} / {t("app.theme.light")}
          </div>
          {THEMES.map(({ id, Icon, key }) => (
            <button
              key={id}
              onClick={() => onPickTheme(id)}
              className="flex w-full items-center justify-between px-3 py-1.5 text-sm hover:bg-bg"
            >
              <span className="flex items-center gap-2">
                <Icon className="size-3.5" />
                {t(`app.theme.${key}`)}
              </span>
              {theme === id && <Check className="size-3.5 text-accent" />}
            </button>
          ))}
          <div className="my-1 border-t border-border" />
          <div className="px-3 pb-1 pt-1 text-[10px] uppercase tracking-wider text-muted">
            {t("app.language")}
          </div>
          {LANGS.map((l) => (
            <button
              key={l.id}
              onClick={() => onPickLang(l.id)}
              className="flex w-full items-center justify-between px-3 py-1.5 text-sm hover:bg-bg"
            >
              <span>{l.label}</span>
              {i18n.language === l.id && <Check className="size-3.5 text-accent" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
