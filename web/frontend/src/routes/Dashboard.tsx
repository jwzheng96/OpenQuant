/**
 * Dashboard — 主驾驶舱.
 *
 * Top: 6 KPI cards (NAV / 累计收益 / 今日盈亏 / Sharpe / MDD / 持仓数)
 * Mid: NAV chart + drawdown subplot
 * Bottom: Monthly heatmap | Recent 10 fills
 */
import { useDashboard, useDataHealth } from "@/hooks/useDashboard";
import { KpiCard } from "@/components/ui/KpiCard";
import { PriceDelta } from "@/components/ui/PriceDelta";
import { StockTag } from "@/components/ui/StockTag";
import { SideBadge } from "@/components/ui/StatusBadge";
import { NavChart } from "@/components/charts/NavChart";
import { MonthlyHeatmap } from "@/components/charts/MonthlyHeatmap";
import { fmtMoney, fmtPct, fmtNum, priceColor } from "@/lib/format";
import { TrendingUp, Wallet, Activity, Shield, Layers } from "lucide-react";

export function Dashboard() {
  const health = useDataHealth();
  const active = health.data?.active_strategy ?? undefined;
  const dash = useDashboard(active);

  if (!active) {
    return <SimplePlaceholder text={health.isLoading ? "加载策略…" : "无活跃策略"} />;
  }
  if (dash.isLoading) return <SimplePlaceholder text={`加载 ${active} …`} />;
  if (dash.error)
    return <SimplePlaceholder text={`加载失败: ${(dash.error as Error).message}`} />;
  if (!dash.data) return null;

  const { kpis, nav, benchmark, monthly, recent_fills, last_run, is_active } = dash.data;

  return (
    <div className="space-y-6">
      {/* Header — current strategy + meta */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">
            {dash.data.strategy}
          </h1>
          <div className="mt-1 flex items-center gap-3 text-xs text-muted">
            {is_active && (
              <span className="inline-flex items-center rounded bg-accent/15 px-1.5 py-0.5 font-medium text-accent ring-1 ring-accent/30">
                活跃
              </span>
            )}
            <span>上次跑: {last_run ?? "—"}</span>
            <span>·</span>
            <span>区间: {nav[0]?.trade_date} → {nav[nav.length - 1]?.trade_date}</span>
            <span>·</span>
            <span>共 {nav.length} 个交易日</span>
          </div>
        </div>
      </div>

      {/* KPI cards */}
      <div className="grid gap-4 sm:grid-cols-2 md:grid-cols-3 xl:grid-cols-6">
        <KpiCard
          label="当前 NAV"
          value={<span className="text-2xl">{fmtMoney(kpis.nav)}</span>}
          delta={
            <PriceDelta value={kpis.total_return} format="pct" />
          }
          hint={`vs 初始 ¥${(kpis.initial_cash / 10000).toFixed(0)}万`}
        />
        <KpiCard
          label="累计收益"
          value={
            <span className={`text-2xl ${priceColor(kpis.total_return)}`}>
              {fmtPct(kpis.total_return)}
            </span>
          }
          hint="vs initial cash"
        />
        <KpiCard
          label="今日盈亏"
          value={
            <span className={`text-2xl ${priceColor(kpis.today_pnl_amount)}`}>
              {fmtMoney(kpis.today_pnl_amount)}
            </span>
          }
          delta={<PriceDelta value={kpis.today_pnl_pct} format="pct" />}
        />
        <KpiCard
          label="Sharpe (年化)"
          value={<span className="text-2xl">{fmtNum(kpis.sharpe ?? 0, 2)}</span>}
          accent={kpis.sharpe && kpis.sharpe > 1 ? "success" : "default"}
        />
        <KpiCard
          label="最大回撤"
          value={
            <span className="text-2xl text-down">
              {kpis.max_drawdown != null ? fmtPct(kpis.max_drawdown, 2) : "—"}
            </span>
          }
        />
        <KpiCard
          label="持仓 / 现金"
          value={
            <span className="text-2xl">
              {kpis.position_count}
              <span className="ml-1 text-base text-muted">只</span>
            </span>
          }
          hint={`现金 ${fmtMoney(kpis.cash)} (${(kpis.cash_pct * 100).toFixed(1)}%)`}
        />
      </div>

      {/* NAV chart */}
      <section className="rounded-lg border border-border bg-card p-5">
        <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold text-foreground/90">
          <TrendingUp className="size-4" />
          净值曲线 vs HS300（rebased=100）+ 回撤
        </h2>
        <NavChart nav={nav} benchmark={benchmark} initialCash={kpis.initial_cash} height={420} />
      </section>

      {/* Monthly heatmap + recent fills */}
      <div className="grid gap-4 md:grid-cols-3">
        <section className="rounded-lg border border-border bg-card p-5 md:col-span-2">
          <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold text-foreground/90">
            <Layers className="size-4" />
            月度收益热力图（红涨绿跌）
          </h2>
          <MonthlyHeatmap data={monthly} height={240} />
        </section>
        <section className="rounded-lg border border-border bg-card p-5">
          <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-foreground/90">
            <Activity className="size-4" />
            最近成交
          </h2>
          <div className="space-y-1.5 text-sm">
            {recent_fills.length === 0 && (
              <div className="text-xs text-muted">无成交记录</div>
            )}
            {recent_fills.map((f, i) => (
              <div
                key={`${f.trade_date}-${f.symbol}-${i}`}
                className="flex items-center justify-between gap-2 border-b border-border py-1.5 last:border-b-0"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <SideBadge side={f.side} />
                    <StockTag symbol={f.symbol} name={f.name} />
                  </div>
                  <div className="mt-0.5 text-xs text-muted">{f.trade_date}</div>
                </div>
                <div className="shrink-0 text-right tabular text-xs">
                  <div className="text-foreground">{f.qty.toLocaleString()} 股</div>
                  <div className="text-muted">@¥{f.price.toFixed(2)}</div>
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>

      {/* Footer info bar */}
      <section className="flex items-center justify-between rounded-lg border border-border bg-card px-5 py-2 text-xs text-muted">
        <div className="flex items-center gap-4">
          <span className="inline-flex items-center gap-1.5">
            <Wallet className="size-3" />
            数据最新: {health.data?.daily_latest ?? "—"}（{health.data?.daily_symbol_count ?? "—"} 只）
          </span>
          <span>·</span>
          <span>策略池: {health.data?.paper_strategies.length ?? 0}</span>
          <span>·</span>
          <span>因子: {health.data?.factors.length ?? 0}</span>
        </div>
        <span className="inline-flex items-center gap-1.5">
          <Shield className="size-3" /> Phase 1 · 富面板
        </span>
      </section>
    </div>
  );
}

function SimplePlaceholder({ text }: { text: string }) {
  return (
    <div className="flex h-[60vh] items-center justify-center text-sm text-muted">
      {text}
    </div>
  );
}
