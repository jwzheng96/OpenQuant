/**
 * Full-featured candlestick chart with toggleable indicators.
 *
 *   Main panel:    candles + MA/EMA/BOLL overlays  + buy/sell fill markers
 *   Sub panel 1:   Volume bars (colored red/green by close vs open)
 *   Sub panel 2:   selectable — MACD | RSI | KDJ
 *
 *   A-share colors: red = up, green = down.
 */
import ReactECharts from "echarts-for-react";
import { useMemo, useState } from "react";

import {
  Bar,
  boll,
  ema,
  kdj,
  macd,
  rsi,
  sma,
} from "@/lib/indicators";

export type FillMark = {
  trade_date: string;
  side: "buy" | "sell" | string;
  qty: number;
  price: number;
};

type SubPanel = "macd" | "rsi" | "kdj" | "none";

type IndicatorToggles = {
  ma5: boolean;
  ma10: boolean;
  ma20: boolean;
  ma60: boolean;
  ema12: boolean;
  ema26: boolean;
  boll: boolean;
};

const DEFAULT_TOGGLES: IndicatorToggles = {
  ma5: true,
  ma10: true,
  ma20: true,
  ma60: false,
  ema12: false,
  ema26: false,
  boll: false,
};

export function KlineFull({
  bars,
  fills = [],
  height = 480,
}: {
  bars: Bar[];
  fills?: FillMark[];
  height?: number;
}) {
  const [toggles, setToggles] = useState<IndicatorToggles>(DEFAULT_TOGGLES);
  const [sub, setSub] = useState<SubPanel>("macd");

  const option = useMemo(() => {
    const clean = bars.filter(
      (b) => b.open != null && b.close != null && b.high != null && b.low != null,
    );
    if (clean.length === 0) return {};

    const dates = clean.map((b) => b.trade_date);
    const opens = clean.map((b) => b.open!);
    const closes = clean.map((b) => b.close!);
    const highs = clean.map((b) => b.high!);
    const lows = clean.map((b) => b.low!);
    const vols = clean.map((b) => b.vol ?? 0);
    const ohlc = clean.map((b, i) => [opens[i], closes[i], lows[i], highs[i]]);

    // Volume color by candle direction
    const volBars = vols.map((v, i) => ({
      value: v,
      itemStyle: {
        color: closes[i] >= opens[i] ? "rgba(239,68,68,0.55)" : "rgba(16,185,129,0.55)",
      },
    }));

    // Indicators
    const ma5 = toggles.ma5 ? sma(closes, 5) : null;
    const ma10 = toggles.ma10 ? sma(closes, 10) : null;
    const ma20 = toggles.ma20 ? sma(closes, 20) : null;
    const ma60 = toggles.ma60 ? sma(closes, 60) : null;
    const e12 = toggles.ema12 ? ema(closes, 12) : null;
    const e26 = toggles.ema26 ? ema(closes, 26) : null;
    const bb = toggles.boll ? boll(closes, 20, 2) : null;

    // Mark points from fills
    const idxByDate = new Map(dates.map((d, i) => [d, i]));
    const buyMarks: any[] = [];
    const sellMarks: any[] = [];
    for (const f of fills) {
      const i = idxByDate.get(f.trade_date);
      if (i == null) continue;
      const y = f.side === "buy" ? lows[i] : highs[i];
      const cfg = {
        name: `${f.side === "buy" ? "买" : "卖"} ${f.qty}@${f.price.toFixed(2)}`,
        coord: [f.trade_date, y],
        symbol: f.side === "buy" ? "triangle" : "pin",
        symbolSize: 10,
        symbolRotate: f.side === "buy" ? 0 : 180,
        itemStyle: {
          color: f.side === "buy" ? "#ef4444" : "#10b981",
          borderColor: "#fff",
          borderWidth: 1,
        },
        label: { show: false },
      };
      (f.side === "buy" ? buyMarks : sellMarks).push(cfg);
    }

    // Sub-panel series
    let subSeries: any[] = [];
    let subYAxis: any[] = [];
    if (sub === "macd") {
      const m = macd(closes);
      subSeries = [
        {
          name: "DIF",
          type: "line", xAxisIndex: 2, yAxisIndex: 2, showSymbol: false,
          lineStyle: { width: 1, color: "rgb(99,102,241)" },
          data: m.macd,
        },
        {
          name: "DEA",
          type: "line", xAxisIndex: 2, yAxisIndex: 2, showSymbol: false,
          lineStyle: { width: 1, color: "rgb(245,158,11)" },
          data: m.signal,
        },
        {
          name: "MACD柱",
          type: "bar", xAxisIndex: 2, yAxisIndex: 2,
          data: m.hist.map((v) => ({
            value: v,
            itemStyle: { color: v >= 0 ? "rgba(239,68,68,0.7)" : "rgba(16,185,129,0.7)" },
          })),
        },
      ];
      subYAxis = [{
        gridIndex: 2,
        axisLine: { show: false },
        axisLabel: { color: "#71717a", fontSize: 9 },
        splitLine: { lineStyle: { color: "rgba(82,82,91,0.25)" } },
      }];
    } else if (sub === "rsi") {
      const r6 = rsi(closes, 6);
      const r12 = rsi(closes, 12);
      const r24 = rsi(closes, 24);
      subSeries = [
        { name: "RSI6", type: "line", xAxisIndex: 2, yAxisIndex: 2, showSymbol: false,
          lineStyle: { width: 1, color: "rgb(99,102,241)" }, data: r6 },
        { name: "RSI12", type: "line", xAxisIndex: 2, yAxisIndex: 2, showSymbol: false,
          lineStyle: { width: 1, color: "rgb(245,158,11)" }, data: r12 },
        { name: "RSI24", type: "line", xAxisIndex: 2, yAxisIndex: 2, showSymbol: false,
          lineStyle: { width: 1, color: "rgb(168,85,247)" }, data: r24 },
      ];
      subYAxis = [{
        gridIndex: 2, min: 0, max: 100,
        axisLine: { show: false },
        axisLabel: { color: "#71717a", fontSize: 9 },
        splitLine: { lineStyle: { color: "rgba(82,82,91,0.25)" } },
      }];
    } else if (sub === "kdj") {
      const { k, d, j } = kdj(highs, lows, closes);
      subSeries = [
        { name: "K", type: "line", xAxisIndex: 2, yAxisIndex: 2, showSymbol: false,
          lineStyle: { width: 1, color: "rgb(99,102,241)" }, data: k },
        { name: "D", type: "line", xAxisIndex: 2, yAxisIndex: 2, showSymbol: false,
          lineStyle: { width: 1, color: "rgb(245,158,11)" }, data: d },
        { name: "J", type: "line", xAxisIndex: 2, yAxisIndex: 2, showSymbol: false,
          lineStyle: { width: 1, color: "rgb(239,68,68)" }, data: j },
      ];
      subYAxis = [{
        gridIndex: 2,
        axisLine: { show: false },
        axisLabel: { color: "#71717a", fontSize: 9 },
        splitLine: { lineStyle: { color: "rgba(82,82,91,0.25)" } },
      }];
    }

    const hasSub = sub !== "none";
    const mainHeight = hasSub ? "52%" : "78%";
    const volTop = hasSub ? "57%" : "82%";
    const volHeight = hasSub ? "16%" : "13%";
    const subTop = "76%";
    const subHeight = "20%";

    return {
      backgroundColor: "transparent",
      animation: false,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross", lineStyle: { color: "rgba(255,255,255,0.3)" } },
        backgroundColor: "rgba(24,24,27,0.95)",
        borderColor: "rgba(255,255,255,0.1)",
        textStyle: { color: "#f4f4f5", fontSize: 11 },
      },
      legend: {
        data: [
          "K线",
          ma5 ? "MA5" : null, ma10 ? "MA10" : null, ma20 ? "MA20" : null, ma60 ? "MA60" : null,
          e12 ? "EMA12" : null, e26 ? "EMA26" : null,
          bb ? "BOLL上" : null, bb ? "BOLL中" : null, bb ? "BOLL下" : null,
        ].filter(Boolean),
        textStyle: { color: "#a1a1aa", fontSize: 10 },
        top: 0,
      },
      grid: [
        { left: 50, right: 12, top: 28, height: mainHeight },
        { left: 50, right: 12, top: volTop, height: volHeight },
        ...(hasSub ? [{ left: 50, right: 12, top: subTop, height: subHeight }] : []),
      ],
      xAxis: [
        {
          type: "category", data: dates, gridIndex: 0,
          axisLine: { show: false },
          axisLabel: { show: false },
          axisTick: { show: false },
        },
        {
          type: "category", data: dates, gridIndex: 1,
          axisLine: { show: false },
          axisLabel: hasSub ? { show: false } : { color: "#71717a", fontSize: 10 },
          axisTick: { show: false },
        },
        ...(hasSub ? [{
          type: "category", data: dates, gridIndex: 2,
          axisLine: { show: false },
          axisLabel: { color: "#71717a", fontSize: 10 },
          axisTick: { show: false },
        }] : []),
      ],
      yAxis: [
        {
          gridIndex: 0, scale: true,
          axisLine: { show: false },
          splitLine: { lineStyle: { color: "rgba(82,82,91,0.3)" } },
          axisLabel: { color: "#71717a", fontSize: 10 },
        },
        {
          gridIndex: 1,
          axisLine: { show: false },
          splitLine: { show: false },
          axisLabel: { color: "#71717a", fontSize: 9 },
        },
        ...subYAxis,
      ],
      axisPointer: { link: [{ xAxisIndex: "all" }] },
      dataZoom: [
        { type: "inside", xAxisIndex: hasSub ? [0, 1, 2] : [0, 1], start: 50, end: 100 },
        {
          type: "slider",
          xAxisIndex: hasSub ? [0, 1, 2] : [0, 1],
          height: 14,
          bottom: 4,
          borderColor: "transparent",
          fillerColor: "rgba(99,102,241,0.15)",
          handleStyle: { color: "rgb(99,102,241)" },
          textStyle: { color: "#71717a", fontSize: 9 },
        },
      ],
      series: [
        {
          name: "K线",
          type: "candlestick",
          xAxisIndex: 0, yAxisIndex: 0,
          data: ohlc,
          itemStyle: {
            color: "#ef4444",         // 阳线
            color0: "#10b981",        // 阴线
            borderColor: "#ef4444",
            borderColor0: "#10b981",
          },
          markPoint: { data: [...buyMarks, ...sellMarks] },
        },
        ...(ma5 ? [{ name: "MA5", type: "line", xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, smooth: true, lineStyle: { width: 1, color: "rgb(245,158,11)" }, data: ma5 }] : []),
        ...(ma10 ? [{ name: "MA10", type: "line", xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, smooth: true, lineStyle: { width: 1, color: "rgb(99,102,241)" }, data: ma10 }] : []),
        ...(ma20 ? [{ name: "MA20", type: "line", xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, smooth: true, lineStyle: { width: 1, color: "rgb(168,85,247)" }, data: ma20 }] : []),
        ...(ma60 ? [{ name: "MA60", type: "line", xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, smooth: true, lineStyle: { width: 1, color: "rgb(6,182,212)" }, data: ma60 }] : []),
        ...(e12 ? [{ name: "EMA12", type: "line", xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { width: 1, type: "dashed", color: "rgb(245,158,11)" }, data: e12 }] : []),
        ...(e26 ? [{ name: "EMA26", type: "line", xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { width: 1, type: "dashed", color: "rgb(168,85,247)" }, data: e26 }] : []),
        ...(bb ? [
          { name: "BOLL上", type: "line", xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { width: 1, color: "rgba(99,102,241,0.6)" }, data: bb.upper },
          { name: "BOLL中", type: "line", xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { width: 1, color: "rgba(245,158,11,0.6)" }, data: bb.middle },
          { name: "BOLL下", type: "line", xAxisIndex: 0, yAxisIndex: 0, showSymbol: false, lineStyle: { width: 1, color: "rgba(99,102,241,0.6)" }, data: bb.lower },
        ] : []),
        {
          name: "成交量", type: "bar",
          xAxisIndex: 1, yAxisIndex: 1,
          data: volBars,
        },
        ...subSeries,
      ],
    };
  }, [bars, fills, toggles, sub]);

  if (!bars.length) {
    return <div className="flex h-[480px] items-center justify-center text-sm text-muted">无 K 线数据</div>;
  }

  return (
    <div>
      {/* Indicator toggles */}
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
        <span className="text-muted">叠加:</span>
        {(["ma5", "ma10", "ma20", "ma60", "ema12", "ema26", "boll"] as const).map((k) => (
          <button
            key={k}
            type="button"
            onClick={() => setToggles((t) => ({ ...t, [k]: !t[k] }))}
            className={`rounded px-2 py-0.5 font-mono ${
              toggles[k] ? "bg-accent/20 text-accent" : "bg-muted/10 text-muted"
            }`}
          >
            {k.toUpperCase().replace("MA", "MA").replace("EMA", "EMA").replace("BOLL", "BOLL")}
          </button>
        ))}
        <span className="mx-2 h-3 w-px bg-border" />
        <span className="text-muted">副图:</span>
        {(["macd", "rsi", "kdj", "none"] as const).map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => setSub(s)}
            className={`rounded px-2 py-0.5 font-mono uppercase ${
              sub === s ? "bg-accent/20 text-accent" : "bg-muted/10 text-muted"
            }`}
          >
            {s === "none" ? "关" : s}
          </button>
        ))}
      </div>
      <ReactECharts option={option} style={{ height, width: "100%" }} notMerge lazyUpdate />
    </div>
  );
}
