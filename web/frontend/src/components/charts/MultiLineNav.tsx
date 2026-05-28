/**
 * N-way strategy NAV overlay. All series rebased to 100 at the common start.
 */
import ReactECharts from "echarts-for-react";
import { useMemo } from "react";

type Series = {
  name: string;
  nav_rebased: { trade_date: string; value: number }[];
};

const COLORS = [
  "rgb(99,102,241)",   // indigo
  "rgb(239,68,68)",    // red
  "rgb(34,197,94)",    // green
  "rgb(245,158,11)",   // amber
  "rgb(6,182,212)",    // cyan
  "rgb(168,85,247)",   // purple
  "rgb(236,72,153)",   // pink
];

export function MultiLineNav({
  series,
  height = 360,
}: {
  series: Series[];
  height?: number;
}) {
  const option = useMemo(() => {
    if (!series.length) return {};
    // Union of all dates
    const all = new Set<string>();
    for (const s of series) for (const r of s.nav_rebased) all.add(r.trade_date);
    const dates = Array.from(all).sort();

    return {
      backgroundColor: "transparent",
      animation: false,
      tooltip: {
        trigger: "axis",
        backgroundColor: "rgba(24,24,27,0.95)",
        borderColor: "rgba(255,255,255,0.1)",
        textStyle: { color: "#f4f4f5", fontSize: 11 },
      },
      legend: {
        data: series.map((s) => s.name),
        textStyle: { color: "#a1a1aa", fontSize: 11 },
        top: 0,
        type: "scroll",
        right: 12,
      },
      grid: { left: 48, right: 16, top: 36, bottom: 30 },
      xAxis: {
        type: "category",
        data: dates,
        axisLine: { lineStyle: { color: "#52525b" } },
        axisLabel: { color: "#71717a", fontSize: 10 },
        axisTick: { show: false },
      },
      yAxis: {
        type: "value",
        name: "Rebased=100",
        nameTextStyle: { color: "#71717a", fontSize: 10 },
        axisLine: { lineStyle: { color: "#52525b" } },
        axisLabel: { color: "#71717a", fontSize: 10 },
        splitLine: { lineStyle: { color: "rgba(82,82,91,0.3)" } },
      },
      series: series.map((s, i) => {
        const byDate = new Map(s.nav_rebased.map((r) => [r.trade_date, r.value]));
        return {
          name: s.name,
          type: "line",
          showSymbol: false,
          smooth: false,
          lineStyle: { width: 1.5, color: COLORS[i % COLORS.length] },
          data: dates.map((d) => [d, byDate.get(d) ?? null]),
          connectNulls: true,
        };
      }),
      dataZoom: [{ type: "inside", start: 0, end: 100 }],
    };
  }, [series]);

  if (!series.length) {
    return <div className="flex h-[360px] items-center justify-center text-muted">无数据</div>;
  }
  return <ReactECharts option={option} style={{ height, width: "100%" }} notMerge lazyUpdate />;
}
