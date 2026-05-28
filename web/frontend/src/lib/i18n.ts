/**
 * i18n setup — zh-CN (default) + en.
 * User preference persists in localStorage so it survives full reloads.
 * (Backend `users.locale` will sync this in a future iteration.)
 */
import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import zhCN from "./locales/zh-CN.json";
import en from "./locales/en.json";

const STORAGE_KEY = "openquant.locale";
const SUPPORTED = ["zh-CN", "en"] as const;
export type Locale = (typeof SUPPORTED)[number];

function detect(): Locale {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved && SUPPORTED.includes(saved as Locale)) return saved as Locale;
  const nav = navigator.language.toLowerCase();
  if (nav.startsWith("zh")) return "zh-CN";
  return "en";
}

void i18n
  .use(initReactI18next)
  .init({
    resources: {
      "zh-CN": { translation: zhCN },
      en: { translation: en },
    },
    lng: detect(),
    fallbackLng: "zh-CN",
    interpolation: { escapeValue: false }, // React already escapes
  });

export function setLocale(loc: Locale) {
  void i18n.changeLanguage(loc);
  localStorage.setItem(STORAGE_KEY, loc);
}

export function getLocale(): Locale {
  return (i18n.language as Locale) ?? "zh-CN";
}

export default i18n;
