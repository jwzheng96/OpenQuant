/**
 * Risk dashboard — concentration + board distribution + exposure.
 *
 * Pure-frontend derivation from /paper/{name}/positions response.
 * Board derived from ticker prefix (industry data is mostly empty in
 * stock_basic right now, so we use board as the rough exposure axis).
 */
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import ReactECharts from "echarts-for-react";
import { Activity, Layers, Briefcase, AlertTriangle } from "lucide-react";

import { api } from "@/lib/api";
import { useDashboard, useDataHealth } from "@/hooks/useDashboard";
import { KpiCard } from "@/components/ui/KpiCard";
import { StockTag } from "@/components/ui/StockTag";
import { fmtMoney, fmtPct, fmtInt } from "@/lib/format";

type Position = {
  symbol: string;
  name: string;
  qty: number;
  market_value: number | null;
  pnl_pct: number | null;
  weight: number | null;
};

type BoardKey = "sh_main" | "sz_main" | "chinext" | "star" | "bse" | "other";

function classifyBoard(symbol: string): BoardKey {
  if (symbol.startsWith("688") || symbol.startsWith("689")) return "star";
  if (symbol.endsWith(".SH")) return "sh_main";
  if (symbol.startsWith("30")) return "chinext";
  if (symbol.endsWith(".SZ")) return "sz_main";
  if (symbol.endsWith(".BJ")) return "bse";
  return "other";
}

function usePositions(strategy: string | undefined) {
  return useQuery<Position[]>({
    queryKey: ["positions", strategy],
    queryFn: async () => (await api.get(`/paper/${strategy}/positions`)).data,
    enabled: !!strategy,
    refetchInterval: 30_000,
  });
}

export function Risk() {
  const { t } = useTranslation();
  const health = useDataHealth();
  const active = health.data?.active_strategy ?? undefined;
  const dash = useDashboard(active);
  const { data: positions = [] } = usePositions(active);

  const stats = useMemo(() => {
    if (!positions.length || !dash.data) return null;
    const mv = positions.reduce((a, r) => a + (r.market_value ?? 0), 0);
    const nav = dash.data.kpis.nav;
    const weighted = [...positions]
      .filter((p) => p.weight != null)
      .sort((a, b) => (b.weight ?? 0) - (a.weight ?? 0));
    const top5 = weighted.slice(0, 5).reduce((a, r) => a + (r.weight ?? 0), 0);
    const top10 = weighted.slice(0, 10).reduce((a, r) => a + (r.weight ?? 0), 0);
    const maxWeight = weighted[0]?.weight ?? 0;

    // Board buckets
    const boards: Record<BoardKey, number> = {
      sh_main: 0,
      sz_main: 0,
      chinext: 0,
      star: 0,
      bse: 0,
      other: 0,
    };
    for (const p of positions) {
      boards[classifyBoard(p.symbol)] += p.market_value ?? 0;
    }

    return {
      mv,
      grossExposure: mv / nav,
      cashPct: dash.data.kpis.cash_pct,
      n: positions.length,
      maxWeight,
      top5,
      top10,
      top10List: weighted.slice(0, 10),
      boards,
    };
  }, [positions, dash.data]);

  if (!active) return <Placeholder>无活跃策略</Placeholder>;
  if (!dash.data || positions.length === 0) return <Placeholder>{t("risk.noPositions")}</Placeholder>;
  if (!stats) return null;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">{t("risk.title")}</h1>
        <div className="mt-1 text-xs text-muted">
          {t("risk.subtitle")} · 策略 <span className="font-mono">{active}</span>
        </div>
      </div>

      {/* KPI cards */}
      <div className="grid gap-4 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-6">
        <KpiCard label={t("risk.kpi.gross")} value={<span className="text-xl">{fmtMoney(stats.mv)}</span>} />
        <KpiCard
          label={t("risk.kpi.grossExposure")}
          value={<span className="text-xl">{fmtPct(stats.grossExposure)}</span>}
          accent={stats.grossExposure > 1 ? "warning" : "default"}
        />
        <KpiCard
          label={t("risk.kpi.cashPct")}
          value={<span className="text-xl">{fmtPct(stats.cashPct)}</span>}
        />
        <KpiCard
          label={t("risk.kpi.positions")}
          value={<span className="text-xl">{fmtInt(stats.n)}</span>}
        />
        <KpiCard
          label={t("risk.kpi.maxWeight")}
          value={<span className="text-xl">{fmtPct(stats.maxWeight)}</span>}
          accent={stats.maxWeight > 0.08 ? "warning" : "default"}
        />
        <KpiCard
          label={t("risk.kpi.top5Concentration")}
          value={<span className="text-xl">{fmtPct(stats.top5)}</span>}
          hint={`Top 10: ${fmtPct(stats.top10)}`}
          accent={stats.top5 > 0.35 ? "warning" : "default"}
        />
      </div>

      {/* Board pie + Top-10 bar */}
      <div className="grid gap-6 lg:grid-cols-2">
        <section className="rounded-lg border border-border bg-card p-5">
          <h2 className="mb-3 flex items-center gap-1.5 text-sm font-semibold text-foreground/90">
            <Briefcase className="size-4" />
            {t("risk.boardDistribution")}
          </h2>
          <BoardPie boards={stats.boards} totalMv={stats.mv} />
        </section>

        <section className="rounded-lg border border-border bg-card p-5">
          <h2 className="mb-3 flex items-center gap-1.5 text-sm font-semibold text-foreground/90">
            <Layers className="size-4" />
            {t("risk.topConcentration")}
          </h2>
          <TopConcentration list={stats.top10List} />
        </section>
      </div>

      {/* Weight histogram */}
      <section className="rounded-lg border border-border bg-card p-5">
        <h2 className="mb-3 flex items-center gap-1.5 text-sm font-semibold text-foreground/90">
          <Activity className="size-4" />
          {t("risk.weightHist")}
        </h2>
        <WeightHistogram positions={positions} />
      </section>

      {/* Warning if concentration risk is high */}
      {(stats.maxWeight > 0.08 || stats.top5 > 0.35) && (
        <div className="rounded-lg border border-warning/40 bg-warning/10 p-4 text-sm">
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warning" />
            <div>
              <div className="font-medium text-warning">集中度风险提示</div>
              <ul className="mt-1 list-inside list-disc text-xs text-foreground/80">
                {stats.maxWeight > 0.08 && (
                  <li>最大单股权重 {fmtPct(stats.maxWeight)} 超过 8%</li>
                )}
                {stats.top5 > 0.35 && (
                  <li>Top 5 集中度 {fmtPct(stats.top5)} 超过 35%</li>
                )}
              </ul>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------------
// Sub-components
// ----------------------------------------------------------------------------

function BoardPie({
  boards,
  totalMv,
}: {
  boards: Record<BoardKey, number>;
  totalMv: number;
}) {
  const { t } = useTranslation();
  const data = (Object.entries(boards) as [BoardKey, number][])
    .filter(([, v]) => v > 0)
    .map(([k, v]) => ({ name: t(`risk.boards.${k}`), value: v }));

  const option = {
    backgroundColor: "transparent",
    animation: false,
    tooltip: {
      trigger: "item",
      backgroundColor: "rgba(24,24,27,0.95)",
      borderColor: "rgba(255,255,255,0.1)",
      textStyle: { color: "#f4f4f5", fontSize: 11 },
      formatter: (p: any) =>
        `${p.name}<br/>市值 ¥${(p.value / 10000).toFixed(0)}万 (${((p.value / totalMv) * 100).toFixed(1)}%)`,
    },
    legend: { bottom: 0, textStyle: { color: "#a1a1aa", fontSize: 11 } },
    series: [
      {
        type: "pie",
        radius: ["45%", "70%"],
        center: ["50%", "45%"],
        avoidLabelOverlap: true,
        itemStyle: {
          borderColor: "rgb(24,24,27)",
          borderWidth: 2,
        },
        label: {
          color: "#f4f4f5",
          fontSize: 10,
          formatter: (p: any) => `${p.name}\n${((p.value / totalMv) * 100).toFixed(1)}%`,
        },
        data,
      },
    ],
    color: ["rgb(99,102,241)", "rgb(239,68,68)", "rgb(34,197,94)", "rgb(245,158,11)", "rgb(6,182,212)", "rgb(168,85,247)"],
  };

  return <ReactECharts option={option} style={{ height: 280, width: "100%" }} notMerge lazyUpdate />;
}

function TopConcentration({ list }: { list: Position[] }) {
  return (
    <div className="space-y-1.5">
      {list.map((p) => {
        const w = (p.weight ?? 0) * 100;
        return (
          <div key={p.symbol} className="flex items-center gap-3">
            <div className="w-28 shrink-0">
              <StockTag symbol={p.symbol} name={p.name} />
            </div>
            <div className="relative h-5 flex-1 rounded bg-bg">
              <div
                className="absolute inset-y-0 left-0 rounded bg-accent/70"
                style={{ width: `${Math.min(100, w * 8)}%` }}
              />
              <div className="absolute inset-0 flex items-center justify-end pr-2 tabular text-[11px] font-medium">
                {w.toFixed(2)}%
              </div>
            </div>
            <div className="w-20 shrink-0 text-right tabular text-xs text-muted">
              {p.market_value ? `¥${(p.market_value / 10000).toFixed(0)}万` : "—"}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function WeightHistogram({ positions }: { positions: Position[] }) {
  const bins = [0.5, 1, 2, 3, 4, 5, 7, 10, 15];   // upper-bound percentages
  const counts = new Array(bins.length).fill(0);
  for (const p of positions) {
    const w = (p.weight ?? 0) * 100;
    for (let i = 0; i < bins.length; i++) {
      if (w <= bins[i]) { counts[i]++; break; }
    }
  }
  const labels = bins.map((b, i) => i === 0 ? `≤${b}%` : `${bins[i-1]}-${b}%`);

  const option = {
    backgroundColor: "transparent",
    animation: false,
    tooltip: {
      trigger: "axis",
      backgroundColor: "rgba(24,24,27,0.95)",
      borderColor: "rgba(255,255,255,0.1)",
      textStyle: { color: "#f4f4f5", fontSize: 11 },
    },
    grid: { left: 40, right: 16, top: 20, bottom: 30 },
    xAxis: {
      type: "category",
      data: labels,
      axisLine: { lineStyle: { color: "#52525b" } },
      axisLabel: { color: "#71717a", fontSize: 10 },
    },
    yAxis: {
      type: "value",
      axisLine: { lineStyle: { color: "#52525b" } },
      axisLabel: { color: "#71717a", fontSize: 10 },
      splitLine: { lineStyle: { color: "rgba(82,82,91,0.3)" } },
    },
    series: [
      {
        type: "bar",
        data: counts,
        itemStyle: { color: "rgba(99,102,241,0.7)" },
        label: { show: true, position: "top", color: "#a1a1aa", fontSize: 10 },
      },
    ],
  };
  return <ReactECharts option={option} style={{ height: 240, width: "100%" }} notMerge lazyUpdate />;
}

function Placeholder({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-[60vh] items-center justify-center text-sm text-muted">{children}</div>
  );
}
