/**
 * Client-side CSV helpers. Generates a download-as-file string from rows
 * + emits a Blob using a transient <a> link.
 *
 * UTF-8 BOM is prepended so Excel on Windows renders Chinese correctly.
 */
const BOM = "﻿";

function escape(v: unknown): string {
  if (v == null) return "";
  const s = String(v);
  // Quote if it contains comma, double-quote, or newline
  if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
  return s;
}

export type Column<T> = {
  /** Header label */
  header: string;
  /** Extractor — returns the raw value for the row */
  get: (row: T) => unknown;
};

export function toCSV<T>(rows: T[], cols: Column<T>[]): string {
  const lines: string[] = [];
  lines.push(cols.map((c) => escape(c.header)).join(","));
  for (const r of rows) {
    lines.push(cols.map((c) => escape(c.get(r))).join(","));
  }
  return BOM + lines.join("\n");
}

export function downloadCSV<T>(filename: string, rows: T[], cols: Column<T>[]): void {
  const csv = toCSV(rows, cols);
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename.endsWith(".csv") ? filename : `${filename}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

/** Convenience for timestamped filenames: "fills_2026-05-28.csv" */
export function tsFilename(prefix: string): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${prefix}_${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}.csv`;
}
