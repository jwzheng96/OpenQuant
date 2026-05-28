/**
 * NAV vs benchmark line chart with drawdown subchart underneath.
 * Both rebased to 100 on the first day for direct comparison.
 */
import ReactECharts from "echarts-for-react";
import { useMemo } from "react";

type NavPoint = { trade_date: string; nav: number };
type BenchPoint = { trade_date: string; nav: number };

export function NavChart({
  nav,
  benchmark = [],
  initialCash,
  height = 400,
}: {
  nav: NavPoint[];
  benchmark?: BenchPoint[];
  initialCash: number;
  height?: number;
}) {
  const option = useMemo(() => {
    if (!nav.length) return {};
    const base = initialCash || nav[0].nav;
    const stratSeries = nav.map((r) => [r.trade_date, (r.nav / base) * 100]);

    // Rebase benchmark to nav's first date and 100
    const benchByDate = new Map(benchmark.map((b) => [b.trade_date, b.nav]));
    const firstBenchVal = benchByDate.get(nav[0].trade_date);
    const benchSeries: [string, number][] = firstBenchVal
      ? nav
          .map((r) => {
            const b = benchByDate.get(r.trade_date);
            return b != null ? [r.trade_date, (b / firstBenchVal) * 100] as [string, number] : null;
          })
          .filter((x): x is [string, number] => x !== null)
      : [];

    // Drawdown series (negative percentages)
    let peak = base;
    const ddSeries = nav.map((r) => {
      peak = Math.max(peak, r.nav);
      return [r.trade_date, ((r.nav - peak) / peak) * 100];
    });

    return {
      backgroundColor: "transparent",
      animation: false,
      tooltip: {
        trigger: "axis",
        backgroundColor: "rgba(24,24,27,0.95)",
        borderColor: "rgba(255,255,255,0.1)",
        textStyle: { color: "#f4f4f5" },
        valueFormatter: (v: number) => `${v?.toFixed(2)}`,
      },
      legend: {
        data: ["策略 NAV", "HS300", "回撤"],
        textStyle: { color: "#a1a1aa" },
        top: 0,
        right: 16,
      },
      grid: [
        { left: 48, right: 16, top: 30, height: "60%" },
        { left: 48, right: 16, top: "78%", height: "20%" },
      ],
      xAxis: [
        {
          type: "category",
          data: nav.map((r) => r.trade_date),
          gridIndex: 0,
          axisLine: { lineStyle: { color: "#52525b" } },
          axisLabel: { color: "#71717a", fontSize: 10 },
          axisTick: { show: false },
        },
        {
          type: "category",
          data: nav.map((r) => r.trade_date),
          gridIndex: 1,
          axisLine: { lineStyle: { color: "#52525b" } },
          axisLabel: { color: "#71717a", fontSize: 10 },
          axisTick: { show: false },
        },
      ],
      yAxis: [
        {
          type: "value",
          gridIndex: 0,
          name: "Rebased=100",
          nameTextStyle: { color: "#71717a", fontSize: 10 },
          axisLine: { lineStyle: { color: "#52525b" } },
          axisLabel: {
            color: "#71717a",
            fontSize: 10,
            formatter: (v: number) => v.toFixed(0),
          },
          splitLine: { lineStyle: { color: "rgba(82,82,91,0.3)" } },
        },
        {
          type: "value",
          gridIndex: 1,
          name: "回撤 %",
          nameTextStyle: { color: "#71717a", fontSize: 10 },
          max: 0,
          axisLine: { lineStyle: { color: "#52525b" } },
          axisLabel: {
            color: "#71717a",
            fontSize: 10,
            formatter: (v: number) => `${v.toFixed(0)}%`,
          },
          splitLine: { lineStyle: { color: "rgba(82,82,91,0.3)" } },
        },
      ],
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      series: [
        {
          name: "策略 NAV",
          type: "line",
          xAxisIndex: 0,
          yAxisIndex: 0,
          showSymbol: false,
          smooth: false,
          lineStyle: { width: 1.5, color: "rgb(99,102,241)" },
          data: stratSeries,
          z: 2,
        },
        ...(benchSeries.length > 0
          ? [
              {
                name: "HS300",
                type: "line",
                xAxisIndex: 0,
                yAxisIndex: 0,
                showSymbol: false,
                smooth: false,
                lineStyle: { width: 1, color: "rgb(161,161,170)", type: "dashed" as const },
                data: benchSeries,
                z: 1,
              },
            ]
          : []),
        {
          name: "回撤",
          type: "line",
          xAxisIndex: 1,
          yAxisIndex: 1,
          showSymbol: false,
          areaStyle: { color: "rgba(239,68,68,0.25)" },
          lineStyle: { width: 1, color: "rgb(239,68,68)" },
          data: ddSeries,
        },
      ],
      dataZoom: [
        { type: "inside", xAxisIndex: [0, 1], start: 0, end: 100 },
      ],
    };
  }, [nav, benchmark, initialCash]);

  if (!nav.length) {
    return (
      <div className="flex h-[400px] items-center justify-center text-muted">
        无数据
      </div>
    );
  }

  return (
    <ReactECharts
      option={option}
      style={{ height, width: "100%" }}
      notMerge
      lazyUpdate
    />
  );
}
