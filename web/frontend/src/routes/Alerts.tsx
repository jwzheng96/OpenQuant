/**
 * Alerts dashboard.
 *
 * Phase 1: read + ack. Auto-sources (cron failures, MDD breaches) land later.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, AlertTriangle, AlertCircle, Info } from "lucide-react";

import { useAlerts, useAckAlert } from "@/hooks/useAlerts";
import { StatusBadge, type Tone } from "@/components/ui/StatusBadge";

const severityTone: Record<string, Tone> = {
  critical: "danger",
  warning: "warning",
  info: "muted",
};

const SeverityIcon = ({ s }: { s: string }) => {
  const Cmp = s === "critical" ? AlertCircle : s === "warning" ? AlertTriangle : Info;
  const cls =
    s === "critical" ? "text-danger"
      : s === "warning" ? "text-warning"
      : "text-muted";
  return <Cmp className={`size-4 ${cls}`} />;
};

export function Alerts() {
  const { t } = useTranslation();
  const [onlyUnacked, setOnlyUnacked] = useState(false);
  const [severityFilter, setSeverityFilter] = useState<string>("");
  const { data: rows = [], isLoading } = useAlerts({
    severity: severityFilter,
    only_unacked: onlyUnacked,
  });
  const ack = useAckAlert();

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">{t("alerts.title")}</h1>
        <div className="mt-1 text-xs text-muted">{t("alerts.subtitle")}</div>
      </div>

      <div className="flex items-center gap-3">
        <select
          value={severityFilter}
          onChange={(e) => setSeverityFilter(e.target.value)}
          className="rounded border border-border bg-card px-2 py-1 text-sm text-foreground"
        >
          <option value="">{t("alerts.filterAll")}</option>
          <option value="critical">{t("alerts.severity.critical")}</option>
          <option value="warning">{t("alerts.severity.warning")}</option>
          <option value="info">{t("alerts.severity.info")}</option>
        </select>
        <label className="inline-flex items-center gap-1.5 text-xs text-muted">
          <input
            type="checkbox"
            checked={onlyUnacked}
            onChange={(e) => setOnlyUnacked(e.target.checked)}
          />
          {t("alerts.filterUnacked")}
        </label>
      </div>

      <div className="overflow-hidden rounded-lg border border-border bg-card">
        <table className="w-full text-sm">
          <thead className="bg-card text-xs text-muted">
            <tr className="border-b border-border">
              <th className="px-4 py-3 text-left font-medium">{t("alerts.cols.severity")}</th>
              <th className="px-4 py-3 text-left font-medium">{t("alerts.cols.source")}</th>
              <th className="px-4 py-3 text-left font-medium">{t("alerts.cols.message")}</th>
              <th className="px-4 py-3 text-left font-medium">{t("alerts.cols.createdAt")}</th>
              <th className="px-4 py-3 text-left font-medium">{t("alerts.cols.ackedBy")}</th>
              <th className="px-4 py-3 text-right font-medium">{t("alerts.cols.action")}</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td colSpan={6} className="py-8 text-center text-muted">{t("common.loading")}</td></tr>
            )}
            {rows.map((a) => (
              <tr key={a.id} className="border-b border-border last:border-b-0 hover:bg-bg">
                <td className="px-4 py-2">
                  <div className="flex items-center gap-2">
                    <SeverityIcon s={a.severity} />
                    <StatusBadge
                      text={t(`alerts.severity.${a.severity}`)}
                      tone={severityTone[a.severity]}
                    />
                  </div>
                </td>
                <td className="px-4 py-2 font-mono text-xs text-muted">{a.source}</td>
                <td className="px-4 py-2 text-foreground/90">{a.message}</td>
                <td className="px-4 py-2 tabular text-xs text-muted">
                  {new Date(a.created_at).toLocaleString("zh-CN", { hour12: false })}
                </td>
                <td className="px-4 py-2 text-xs">
                  {a.acked_by ? (
                    <span className="inline-flex items-center gap-1 text-success">
                      <Check className="size-3" /> {a.acked_by_username ?? a.acked_by.slice(0, 8)}
                    </span>
                  ) : (
                    <span className="text-muted">—</span>
                  )}
                </td>
                <td className="px-4 py-2 text-right">
                  {!a.acked_by && (
                    <button
                      type="button"
                      onClick={() => ack.mutate(a.id)}
                      disabled={ack.isPending}
                      className="inline-flex items-center gap-1 rounded bg-accent/10 px-2 py-1 text-xs text-accent hover:bg-accent/20"
                    >
                      <Check className="size-3" /> {t("alerts.ack")}
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {!isLoading && rows.length === 0 && (
              <tr><td colSpan={6} className="py-8 text-center text-sm text-muted">{t("alerts.empty")}</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
