/**
 * Side-panel content for A/B (or N-way) strategy comparison.
 *
 * Three sections:
 *   1. Overlaid NAV chart
 *   2. KPI table (Sharpe / cumret / MDD / vol / win_rate)
 *   3. Daily-return correlation heatmap
 */
import { useQuery } from "@tanstack/react-query";
import { TrendingUp, Layers, GitCompare } from "lucide-react";

import { api } from "@/lib/api";
import { MultiLineNav } from "@/components/charts/MultiLineNav";
import { fmtPct, fmtNum, priceColor } from "@/lib/format";

type CompareItem = {
  name: string;
  kpi: {
    total_return: number | null;
    annualized_return: number | null;
    annualized_vol: number | null;
    sharpe: number | null;
    max_drawdown: number | null;
    win_rate: number | null;
    n_days: number | null;
    n_fills: number | null;
  };
  nav_rebased: { trade_date: string; value: number }[];
};

type CompareResp = {
  items: CompareItem[];
  common_start: string | null;
  common_end: string | null;
  correlation: (number | null)[][];
};

export function CompareView({ names }: { names: string[] }) {
  const { data, isLoading, error } = useQuery<CompareResp>({
    queryKey: ["compare", names],
    queryFn: async () =>
      (await api.get("/strategies/_compare", {
        params: { names: names.join(",") },
      })).data,
    enabled: names.length >= 2,
  });

  if (isLoading)
    return <div className="flex h-48 items-center justify-center text-sm text-muted">加载…</div>;
  if (error || !data)
    return (
      <div className="flex h-48 items-center justify-center text-sm text-danger">
        加载失败 {error ? `: ${(error as Error).message}` : ""}
      </div>
    );

  return (
    <div className="space-y-5">
      {/* Range info */}
      <div className="text-xs text-muted">
        共同区间：<span className="tabular text-foreground">{data.common_start}</span> →{" "}
        <span className="tabular text-foreground">{data.common_end}</span>
        <span className="mx-2">·</span>
        共 {data.items.length} 个策略
      </div>

      {/* NAV overlay */}
      <section>
        <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-muted">
          <TrendingUp className="size-3.5" />
          净值曲线对比（rebased=100）
        </h3>
        <div className="rounded border border-border bg-bg p-2">
          <MultiLineNav
            series={data.items.map((i) => ({
              name: i.name,
              nav_rebased: i.nav_rebased,
            }))}
            height={340}
          />
        </div>
      </section>

      {/* KPI table */}
      <section>
        <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-muted">
          <Layers className="size-3.5" />
          关键指标对比
        </h3>
        <div className="overflow-hidden rounded border border-border">
          <table className="w-full text-xs">
            <thead className="bg-bg text-muted">
              <tr>
                <th className="px-3 py-2 text-left font-medium">策略</th>
                <th className="px-3 py-2 text-right font-medium">累计</th>
                <th className="px-3 py-2 text-right font-medium">年化</th>
                <th className="px-3 py-2 text-right font-medium">Sharpe</th>
                <th className="px-3 py-2 text-right font-medium">MDD</th>
                <th className="px-3 py-2 text-right font-medium">波动</th>
                <th className="px-3 py-2 text-right font-medium">胜率</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((it) => {
                const k = it.kpi;
                return (
                  <tr key={it.name} className="border-t border-border">
                    <td className="px-3 py-1.5 font-mono">{it.name}</td>
                    <td className={`px-3 py-1.5 text-right tabular ${priceColor(k.total_return)}`}>
                      {fmtPct(k.total_return)}
                    </td>
                    <td className={`px-3 py-1.5 text-right tabular ${priceColor(k.annualized_return)}`}>
                      {fmtPct(k.annualized_return)}
                    </td>
                    <td className="px-3 py-1.5 text-right tabular">{fmtNum(k.sharpe ?? 0, 2)}</td>
                    <td className="px-3 py-1.5 text-right tabular text-down">{fmtPct(k.max_drawdown)}</td>
                    <td className="px-3 py-1.5 text-right tabular text-muted">{fmtPct(k.annualized_vol)}</td>
                    <td className="px-3 py-1.5 text-right tabular">
                      {k.win_rate != null ? `${(k.win_rate * 100).toFixed(1)}%` : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* Correlation matrix */}
      <section>
        <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-muted">
          <GitCompare className="size-3.5" />
          日收益相关性
        </h3>
        <CorrelationMatrix names={data.items.map((i) => i.name)} matrix={data.correlation} />
        <div className="mt-1 text-[10px] text-muted">
          📖 低相关 (ρ &lt; 0.5) 意味着策略可组合分散，高相关 (ρ &gt; 0.7) 表示策略本质相同
        </div>
      </section>
    </div>
  );
}

function CorrelationMatrix({
  names,
  matrix,
}: {
  names: string[];
  matrix: (number | null)[][];
}) {
  const cellBg = (v: number | null) => {
    if (v == null) return "bg-muted/10 text-muted";
    if (v >= 0.85) return "bg-up/30 text-up";
    if (v >= 0.6) return "bg-up/15 text-up";
    if (v >= 0.3) return "bg-warning/15 text-warning";
    if (v >= -0.3) return "bg-muted/10 text-foreground";
    return "bg-down/20 text-down";
  };
  return (
    <div className="overflow-x-auto rounded border border-border bg-bg p-2">
      <table className="text-xs">
        <thead>
          <tr>
            <th className="px-2 py-1"></th>
            {names.map((n) => (
              <th key={n} className="px-2 py-1 text-left font-mono text-muted">
                {n}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {matrix.map((row, i) => (
            <tr key={i}>
              <th className="px-2 py-1 text-left font-mono text-muted">{names[i]}</th>
              {row.map((v, j) => (
                <td key={j} className={`px-2 py-1 text-center tabular ${cellBg(v)} `}>
                  {v != null ? v.toFixed(3) : "—"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
