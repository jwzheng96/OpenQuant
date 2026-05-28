/**
 * Hand-rolled dark/light theme switcher — applies `light` class to <html>
 * (which our index.css consumes via `:root.light { ... }`).
 *
 * Persists choice in localStorage. Default = dark (per the A-share quant
 * convention noted in WEB_ARCHITECTURE.md).
 */
const STORAGE_KEY = "openquant.theme";
export type Theme = "dark" | "light" | "system";

export function detectTheme(): Theme {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved === "light" || saved === "dark" || saved === "system") return saved;
  return "dark";
}

export function applyTheme(t: Theme) {
  const root = document.documentElement;
  let effective: "dark" | "light" = "dark";
  if (t === "light") effective = "light";
  else if (t === "system")
    effective = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  root.classList.toggle("light", effective === "light");
  root.classList.toggle("dark", effective === "dark");
  localStorage.setItem(STORAGE_KEY, t);
}

/** Call once at app boot. */
export function initTheme() {
  applyTheme(detectTheme());
  // React to system preference changes when in "system" mode
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (detectTheme() === "system") applyTheme("system");
  });
}
