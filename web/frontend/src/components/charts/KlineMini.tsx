/**
 * Small candlestick chart for the stock detail side panel.
 * A-share convention: red = up, green = down.
 *
 * Optional `marks` overlay shows our buy/sell points.
 */
import ReactECharts from "echarts-for-react";
import { useMemo } from "react";

export type KlineBar = {
  trade_date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  vol: number | null;
};

export type FillMark = {
  trade_date: string;
  side: "buy" | "sell" | string;
  qty: number;
  price: number;
};

export function KlineMini({
  bars,
  fills = [],
  height = 260,
}: {
  bars: KlineBar[];
  fills?: FillMark[];
  height?: number;
}) {
  const option = useMemo(() => {
    if (!bars.length) return {};
    const cleanBars = bars.filter(
      (b) => b.open != null && b.close != null && b.high != null && b.low != null,
    );
    const dates = cleanBars.map((b) => b.trade_date);
    const ohlc = cleanBars.map((b) => [b.open!, b.close!, b.low!, b.high!]);
    const volumes = cleanBars.map((b) => b.vol ?? 0);

    // Build markPoint data per fill matched to date index
    const buyMarks: any[] = [];
    const sellMarks: any[] = [];
    const idxByDate = new Map(dates.map((d, i) => [d, i]));
    for (const f of fills) {
      const i = idxByDate.get(f.trade_date);
      if (i == null) continue;
      const bar = cleanBars[i];
      const yVal = f.side === "buy" ? bar.low! : bar.high!;
      const mark = {
        name: `${f.side === "buy" ? "买入" : "卖出"} ${f.qty}@${f.price.toFixed(2)}`,
        coord: [f.trade_date, yVal],
        symbol: f.side === "buy" ? "triangle" : "pin",
        symbolSize: 9,
        symbolRotate: f.side === "buy" ? 0 : 180,
        itemStyle: {
          color: f.side === "buy" ? "#ef4444" : "#10b981",
          borderColor: "#fff",
          borderWidth: 1,
        },
        label: { show: false },
      };
      (f.side === "buy" ? buyMarks : sellMarks).push(mark);
    }

    return {
      backgroundColor: "transparent",
      animation: false,
      tooltip: {
        trigger: "axis",
        backgroundColor: "rgba(24,24,27,0.95)",
        borderColor: "rgba(255,255,255,0.1)",
        textStyle: { color: "#f4f4f5", fontSize: 11 },
      },
      grid: [
        { left: 40, right: 12, top: 10, height: "65%" },
        { left: 40, right: 12, top: "78%", height: "20%" },
      ],
      xAxis: [
        {
          type: "category",
          data: dates,
          gridIndex: 0,
          axisLine: { show: false },
          axisLabel: { color: "#71717a", fontSize: 9 },
          axisTick: { show: false },
        },
        {
          type: "category",
          data: dates,
          gridIndex: 1,
          axisLine: { show: false },
          axisLabel: { show: false },
          axisTick: { show: false },
        },
      ],
      yAxis: [
        {
          gridIndex: 0,
          scale: true,
          axisLine: { show: false },
          splitLine: { lineStyle: { color: "rgba(82,82,91,0.3)" } },
          axisLabel: { color: "#71717a", fontSize: 9 },
        },
        {
          gridIndex: 1,
          axisLine: { show: false },
          splitLine: { show: false },
          axisLabel: { color: "#71717a", fontSize: 9 },
        },
      ],
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      series: [
        {
          name: "K线",
          type: "candlestick",
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: ohlc,
          itemStyle: {
            // A-share: red = up, green = down
            color: "#ef4444",       // up bar (close > open) body
            color0: "#10b981",      // down bar body
            borderColor: "#ef4444",
            borderColor0: "#10b981",
          },
          markPoint: { data: [...buyMarks, ...sellMarks] },
        },
        {
          name: "Vol",
          type: "bar",
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: volumes,
          itemStyle: { color: "rgba(99,102,241,0.5)" },
        },
      ],
    };
  }, [bars, fills]);

  if (!bars.length) {
    return <div className="flex h-[260px] items-center justify-center text-xs text-muted">无K线数据</div>;
  }
  return <ReactECharts option={option} style={{ height, width: "100%" }} notMerge lazyUpdate />;
}
