/**
 * Big-number KPI card for the Dashboard header.
 *  ┌─────────────────────────┐
 *  │ Label                   │  small muted
 *  │ ¥2,118,327              │  big tabular
 *  │ +111.83%   ← optional   │  colored delta
 *  └─────────────────────────┘
 */
import { ReactNode } from "react";

export type KpiCardProps = {
  label: string;
  value: ReactNode;
  delta?: ReactNode;          // pre-colored / pre-formatted
  hint?: string;              // tiny line under value
  accent?: "default" | "success" | "danger" | "warning";
};

export function KpiCard({ label, value, delta, hint, accent = "default" }: KpiCardProps) {
  const accentRing =
    accent === "success" ? "ring-success/30"
      : accent === "danger" ? "ring-danger/30"
      : accent === "warning" ? "ring-warning/30"
      : "";
  return (
    <div
      className={`rounded-lg border border-border bg-card p-4 shadow-sm ${accentRing ? `ring-1 ${accentRing}` : ""}`}
    >
      <div className="text-xs font-medium text-muted">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular text-foreground">
        {value}
      </div>
      <div className="mt-1 flex items-baseline gap-2">
        {delta != null && <div className="text-sm tabular">{delta}</div>}
        {hint && <div className="text-xs text-muted">{hint}</div>}
      </div>
    </div>
  );
}
