/**
 * Factor detail — IC time series + quintile cumulative returns + decay curve.
 */
import { useTranslation } from "react-i18next";
import { Link, useParams, useSearch } from "@tanstack/react-router";
import { ArrowLeft, Loader2 } from "lucide-react";
import ReactECharts from "echarts-for-react";
import { useMemo } from "react";

import { useFactorDetail, type FactorDetail } from "@/hooks/useFactors";
import { KpiCard } from "@/components/ui/KpiCard";
import { fmtNum, priceColor } from "@/lib/format";

const QUINTILE_COLORS = [
  "rgb(16,185,129)",   // q1 — lowest (绿)
  "rgb(245,158,11)",
  "rgb(99,102,241)",
  "rgb(168,85,247)",
  "rgb(239,68,68)",    // q5 — highest (红)
];

export function FactorDetail() {
  const { t } = useTranslation();
  const { name } = useParams({ strict: false }) as { name: string };
  const search = useSearch({ strict: false }) as { horizon?: number };
  const horizon = search.horizon ?? 5;
  const { data, isLoading, error } = useFactorDetail(name, horizon);

  if (isLoading) {
    return (
      <div className="flex h-[60vh] items-center justify-center text-sm text-muted">
        <Loader2 className="mr-2 size-4 animate-spin" />
        {t("common.loading")} · 首次计算约 30-60s
      </div>
    );
  }
  if (error || !data) {
    return <div className="flex h-[60vh] items-center justify-center text-sm text-danger">{t("common.loadFailed")}</div>;
  }

  const s = data.summary;

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <Link
          to="/factors"
          className="inline-flex items-center gap-1 rounded p-1 text-muted hover:bg-card hover:text-foreground"
        >
          <ArrowLeft className="size-4" />
        </Link>
        <h1 className="text-2xl font-bold tracking-tight font-mono">{name}</h1>
        <div className="text-xs text-muted">{t("factors.horizon", "Horizon")}: {horizon}d</div>
      </div>

      {/* KPI cards */}
      <div className="grid gap-4 sm:grid-cols-2 md:grid-cols-4">
        <KpiCard
          label={t("factors.cols.meanIc", "Rank IC 均值")}
          value={<span className={`text-xl ${priceColor(s.mean_rank_ic ?? 0)}`}>{s.mean_rank_ic?.toFixed(4) ?? "—"}</span>}
        />
        <KpiCard
          label={t("factors.cols.icir", "ICIR (年化)")}
          value={<span className="text-xl">{fmtNum(s.icir ?? 0, 2)}</span>}
          accent={
            (s.icir ?? 0) >= 2 ? "success"
              : (s.icir ?? 0) >= 1 ? "warning"
              : (s.icir ?? 0) <= 0 ? "danger"
              : "default"
          }
        />
        <KpiCard
          label={t("factors.cols.posPct", "正 IC 占比")}
          value={<span className="text-xl">{s.pos_days_pct != null ? `${(s.pos_days_pct * 100).toFixed(1)}%` : "—"}</span>}
        />
        <KpiCard
          label={t("factors.cols.nDays", "回测天数")}
          value={<span className="text-xl">{s.n_days}</span>}
          hint={`${s.first_date} → ${s.last_date}`}
        />
      </div>

      {/* IC time series */}
      <section className="rounded-lg border border-border bg-card p-5">
        <h2 className="mb-3 text-sm font-semibold text-foreground/90">Rank IC 时序</h2>
        <IcTimeSeries data={data.ic_series} />
      </section>

      {/* Quintile cumulative returns */}
      <section className="rounded-lg border border-border bg-card p-5">
        <h2 className="mb-3 text-sm font-semibold text-foreground/90">
          5 分位累计收益（Q1=因子值最小, Q5=最大）
        </h2>
        <QuintileCumChart data={data.quintile_series} />
      </section>

      {/* Decay */}
      <section className="rounded-lg border border-border bg-card p-5">
        <h2 className="mb-3 text-sm font-semibold text-foreground/90">IC 衰减曲线（不同持有期）</h2>
        <DecayChart data={data.decay} />
      </section>
    </div>
  );
}


// ----------------------------------------------------------------------------
// Charts
// ----------------------------------------------------------------------------

function IcTimeSeries({ data }: { data: FactorDetail["ic_series"] }) {
  const option = useMemo(() => {
    if (!data.length) return {};
    const dates = data.map((r) => r.trade_date);
    const ric = data.map((r) => r.rank_ic);
    // 20-day rolling mean
    const rolling: number[] = [];
    const window = 20;
    for (let i = 0; i < ric.length; i++) {
      if (i < window - 1) { rolling.push(NaN); continue; }
      let s = 0;
      for (let j = i - window + 1; j <= i; j++) s += ric[j];
      rolling.push(s / window);
    }

    return {
      backgroundColor: "transparent",
      animation: false,
      tooltip: { trigger: "axis", backgroundColor: "rgba(24,24,27,0.95)", borderColor: "rgba(255,255,255,0.1)", textStyle: { color: "#f4f4f5", fontSize: 11 } },
      legend: { textStyle: { color: "#a1a1aa", fontSize: 11 }, top: 0 },
      grid: { left: 50, right: 16, top: 28, bottom: 30 },
      xAxis: {
        type: "category",
        data: dates,
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
          name: "Rank IC",
          type: "bar",
          data: ric.map((v) => ({
            value: v,
            itemStyle: { color: v >= 0 ? "rgba(239,68,68,0.4)" : "rgba(16,185,129,0.4)" },
          })),
        },
        {
          name: "MA20",
          type: "line",
          data: rolling,
          showSymbol: false,
          smooth: true,
          lineStyle: { width: 2, color: "rgb(99,102,241)" },
        },
      ],
      dataZoom: [{ type: "inside" }, { type: "slider", height: 14, bottom: 4, borderColor: "transparent", fillerColor: "rgba(99,102,241,0.15)", handleStyle: { color: "rgb(99,102,241)" } }],
    };
  }, [data]);

  if (!data.length) return <div className="text-muted">无数据</div>;
  return <ReactECharts option={option} style={{ height: 320, width: "100%" }} notMerge lazyUpdate />;
}

function QuintileCumChart({ data }: { data: FactorDetail["quintile_series"] }) {
  const option = useMemo(() => {
    if (!data.length) return {};
    const dates = data.map((r) => r.trade_date);
    const buckets = [1, 2, 3, 4, 5];
    const series = buckets.map((b) => ({
      name: `Q${b}`,
      type: "line",
      showSymbol: false,
      smooth: false,
      lineStyle: { width: 1.5, color: QUINTILE_COLORS[b - 1] },
      data: data.map((r) => (r as any)[`cum_q${b}`] * 100),
    }));
    series.push({
      name: "Q5-Q1",
      type: "line",
      showSymbol: false,
      smooth: false,
      lineStyle: { width: 2, type: "dashed", color: "rgb(255,255,255)" } as any,
      data: data.map((r) => r.cum_top_minus_bottom * 100),
    });

    return {
      backgroundColor: "transparent",
      animation: false,
      tooltip: {
        trigger: "axis",
        backgroundColor: "rgba(24,24,27,0.95)",
        borderColor: "rgba(255,255,255,0.1)",
        textStyle: { color: "#f4f4f5", fontSize: 11 },
        valueFormatter: (v: number) => v?.toFixed(2) + "%",
      },
      legend: { textStyle: { color: "#a1a1aa", fontSize: 11 }, top: 0 },
      grid: { left: 50, right: 16, top: 28, bottom: 30 },
      xAxis: {
        type: "category",
        data: dates,
        axisLine: { lineStyle: { color: "#52525b" } },
        axisLabel: { color: "#71717a", fontSize: 10 },
      },
      yAxis: {
        type: "value",
        name: "累计收益 %",
        nameTextStyle: { color: "#71717a", fontSize: 10 },
        axisLine: { lineStyle: { color: "#52525b" } },
        axisLabel: { color: "#71717a", fontSize: 10 },
        splitLine: { lineStyle: { color: "rgba(82,82,91,0.3)" } },
      },
      series,
      dataZoom: [{ type: "inside" }, { type: "slider", height: 14, bottom: 4, borderColor: "transparent", fillerColor: "rgba(99,102,241,0.15)", handleStyle: { color: "rgb(99,102,241)" } }],
    };
  }, [data]);

  if (!data.length) return <div className="text-muted">无数据</div>;
  return <ReactECharts option={option} style={{ height: 320, width: "100%" }} notMerge lazyUpdate />;
}

function DecayChart({ data }: { data: FactorDetail["decay"] }) {
  const option = useMemo(() => {
    if (!data.length) return {};
    return {
      backgroundColor: "transparent",
      animation: false,
      tooltip: { trigger: "axis", backgroundColor: "rgba(24,24,27,0.95)", borderColor: "rgba(255,255,255,0.1)", textStyle: { color: "#f4f4f5", fontSize: 11 } },
      legend: { textStyle: { color: "#a1a1aa", fontSize: 11 }, top: 0 },
      grid: { left: 50, right: 16, top: 28, bottom: 24 },
      xAxis: {
        type: "category",
        data: data.map((r) => `${r.horizon}d`),
        axisLine: { lineStyle: { color: "#52525b" } },
        axisLabel: { color: "#71717a", fontSize: 10 },
      },
      yAxis: [
        {
          type: "value",
          name: "Rank IC",
          nameTextStyle: { color: "#71717a", fontSize: 10 },
          axisLine: { lineStyle: { color: "#52525b" } },
          axisLabel: { color: "#71717a", fontSize: 10 },
          splitLine: { lineStyle: { color: "rgba(82,82,91,0.3)" } },
        },
        {
          type: "value",
          name: "ICIR",
          nameTextStyle: { color: "#71717a", fontSize: 10 },
          axisLine: { lineStyle: { color: "#52525b" } },
          axisLabel: { color: "#71717a", fontSize: 10 },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: "Rank IC",
          type: "bar",
          data: data.map((r) => r.mean_rank_ic),
          itemStyle: { color: "rgb(99,102,241)" },
          yAxisIndex: 0,
        },
        {
          name: "ICIR",
          type: "line",
          data: data.map((r) => r.icir),
          yAxisIndex: 1,
          showSymbol: true,
          symbolSize: 6,
          lineStyle: { width: 2, color: "rgb(245,158,11)" },
          itemStyle: { color: "rgb(245,158,11)" },
        },
      ],
    };
  }, [data]);

  if (!data.length) return <div className="text-muted">无数据</div>;
  return <ReactECharts option={option} style={{ height: 240, width: "100%" }} notMerge lazyUpdate />;
}
