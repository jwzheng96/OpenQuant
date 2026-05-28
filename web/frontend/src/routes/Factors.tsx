/**
 * Factors index — list + IC/ICIR summary per factor. Click row → detail page.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "@tanstack/react-router";
import { ChevronRight, Loader2 } from "lucide-react";

import { useFactors, type FactorListItem } from "@/hooks/useFactors";
import { ExportCsvButton } from "@/components/ui/ExportCsvButton";
import { fmtNum, priceColor } from "@/lib/format";

export function Factors() {
  const { t } = useTranslation();
  const [horizon, setHorizon] = useState(5);
  const { data: rows = [], isLoading } = useFactors(horizon);

  const sorted = [...rows].sort((a, b) => {
    if (!a.available && !b.available) return 0;
    if (!a.available) return 1;
    if (!b.available) return -1;
    return (b.icir ?? 0) - (a.icir ?? 0);
  });

  return (
    <div className="space-y-4">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">{t("factors.title", "因子库")}</h1>
          <div className="mt-1 text-xs text-muted">
            {t("factors.subtitle", "因子研究工作台 — IC 时序 / 分位回测 / 衰减半衰期")}
          </div>
        </div>
        <div className="flex items-center gap-3">
          <label className="text-xs text-muted">
            {t("factors.horizon", "Horizon")}:
            <select
              value={horizon}
              onChange={(e) => setHorizon(Number(e.target.value))}
              className="ml-2 rounded border border-border bg-card px-2 py-1 text-sm"
            >
              <option value="1">1 day</option>
              <option value="3">3 days</option>
              <option value="5">5 days</option>
              <option value="10">10 days</option>
              <option value="20">20 days</option>
            </select>
          </label>
          <ExportCsvButton<FactorListItem>
            rows={sorted}
            filenamePrefix={`factors_h${horizon}`}
            cols={[
              { header: "name", get: (r) => r.name },
              { header: "available", get: (r) => r.available ? "yes" : "no" },
              { header: "mean_rank_ic", get: (r) => r.mean_rank_ic?.toFixed(6) ?? "" },
              { header: "icir", get: (r) => r.icir?.toFixed(4) ?? "" },
              { header: "n_days", get: (r) => r.n_days ?? "" },
              { header: "pos_days_pct", get: (r) => r.pos_days_pct != null ? (r.pos_days_pct * 100).toFixed(2) : "" },
            ]}
          />
        </div>
      </div>

      <div className="overflow-hidden rounded-lg border border-border bg-card">
        <table className="w-full text-sm">
          <thead className="bg-card text-xs text-muted">
            <tr className="border-b border-border">
              <th className="px-4 py-3 text-left font-medium">{t("factors.cols.name", "因子")}</th>
              <th className="px-4 py-3 text-right font-medium">{t("factors.cols.meanIc", "平均 Rank IC")}</th>
              <th className="px-4 py-3 text-right font-medium">{t("factors.cols.icir", "ICIR")}</th>
              <th className="px-4 py-3 text-right font-medium">{t("factors.cols.posPct", "正 IC 占比")}</th>
              <th className="px-4 py-3 text-right font-medium">{t("factors.cols.nDays", "天数")}</th>
              <th className="px-4 py-3 text-left font-medium">{t("factors.cols.period", "区间")}</th>
              <th className="px-4 py-3 text-right font-medium"></th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr>
                <td colSpan={7} className="py-8 text-center text-muted">
                  <Loader2 className="mx-auto size-4 animate-spin" />
                </td>
              </tr>
            )}
            {sorted.map((r) => (
              <FactorRow key={r.name} factor={r} horizon={horizon} />
            ))}
            {!isLoading && sorted.length === 0 && (
              <tr>
                <td colSpan={7} className="py-8 text-center text-sm text-muted">
                  {t("factors.empty", "无可用因子")}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <p className="text-xs text-muted">
        💡 首次点击 "未计算" 的因子时后端会自动计算 IC 时序（~30-60s），结果会缓存到磁盘。
      </p>
    </div>
  );
}

function FactorRow({ factor, horizon }: { factor: FactorListItem; horizon: number }) {
  return (
    <tr className="border-b border-border last:border-b-0 hover:bg-bg">
      <td className="px-4 py-2 font-mono">
        <Link
          to="/factors/$name"
          params={{ name: factor.name }}
          search={{ horizon }}
          className="text-foreground hover:text-accent hover:underline"
        >
          {factor.name}
        </Link>
        {!factor.available && (
          <span className="ml-2 rounded bg-muted/10 px-1.5 py-0.5 text-[10px] text-muted">
            未计算
          </span>
        )}
      </td>
      <td className={`px-4 py-2 text-right tabular ${priceColor(factor.mean_rank_ic)}`}>
        {factor.mean_rank_ic != null ? factor.mean_rank_ic.toFixed(4) : "—"}
      </td>
      <td className="px-4 py-2 text-right tabular">
        {factor.icir != null ? fmtNum(factor.icir, 2) : "—"}
      </td>
      <td className="px-4 py-2 text-right tabular text-muted">
        {factor.pos_days_pct != null ? `${(factor.pos_days_pct * 100).toFixed(1)}%` : "—"}
      </td>
      <td className="px-4 py-2 text-right tabular text-muted">{factor.n_days ?? "—"}</td>
      <td className="px-4 py-2 text-xs text-muted">
        {factor.first_date ? `${factor.first_date} → ${factor.last_date}` : "—"}
      </td>
      <td className="px-4 py-2 text-right">
        <Link
          to="/factors/$name"
          params={{ name: factor.name }}
          search={{ horizon }}
          className="text-muted hover:text-accent"
        >
          <ChevronRight className="size-4" />
        </Link>
      </td>
    </tr>
  );
}
