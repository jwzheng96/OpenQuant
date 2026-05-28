/**
 * Monthly returns heatmap. Rows = years, cols = Jan..Dec.
 * Red = positive (A-share convention), green = negative.
 */
import ReactECharts from "echarts-for-react";
import { useMemo } from "react";

type MonthRow = { month: string; ret: number; end_nav?: number };

export function MonthlyHeatmap({ data, height = 220 }: { data: MonthRow[]; height?: number }) {
  const option = useMemo(() => {
    if (!data.length) return {};
    const years = Array.from(new Set(data.map((r) => r.month.split("-")[0]))).sort();
    const months = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"];

    const points: [number, number, number][] = data
      .map((r) => {
        const [y, m] = r.month.split("-");
        const yi = years.indexOf(y);
        const mi = parseInt(m, 10) - 1;
        if (yi < 0 || mi < 0) return null;
        return [mi, yi, r.ret * 100] as [number, number, number];
      })
      .filter((p): p is [number, number, number] => p !== null);

    const max = Math.max(...points.map((p) => Math.abs(p[2])), 5);

    return {
      backgroundColor: "transparent",
      animation: false,
      tooltip: {
        position: "top",
        backgroundColor: "rgba(24,24,27,0.95)",
        borderColor: "rgba(255,255,255,0.1)",
        textStyle: { color: "#f4f4f5" },
        formatter: (p: any) => {
          const yi = p.data[1];
          const mi = p.data[0];
          const v = p.data[2];
          return `${years[yi]}-${String(mi + 1).padStart(2, "0")}<br/>收益: <b>${v.toFixed(2)}%</b>`;
        },
      },
      grid: { left: 40, right: 16, top: 20, bottom: 28 },
      xAxis: {
        type: "category",
        data: months,
        axisLabel: { color: "#71717a", fontSize: 10 },
        axisLine: { show: false },
        axisTick: { show: false },
        splitArea: { show: false },
      },
      yAxis: {
        type: "category",
        data: years,
        inverse: true,
        axisLabel: { color: "#71717a", fontSize: 10 },
        axisLine: { show: false },
        axisTick: { show: false },
        splitArea: { show: false },
      },
      visualMap: {
        show: false,
        min: -max,
        max,
        inRange: {
          // green → grey → red (A-share: red up)
          color: ["#10b981", "#3f3f46", "#ef4444"],
        },
      },
      series: [
        {
          type: "heatmap",
          data: points,
          label: {
            show: true,
            color: "#f4f4f5",
            fontSize: 9,
            formatter: (p: any) => (p.data[2] != null ? p.data[2].toFixed(1) : ""),
          },
          itemStyle: {
            borderColor: "#0a0a0a",
            borderWidth: 2,
          },
          emphasis: {
            itemStyle: {
              borderColor: "#fff",
              borderWidth: 1,
            },
          },
        },
      ],
    };
  }, [data]);

  if (!data.length) {
    return <div className="flex h-[220px] items-center justify-center text-muted">无数据</div>;
  }
  return <ReactECharts option={option} style={{ height, width: "100%" }} notMerge lazyUpdate />;
}
