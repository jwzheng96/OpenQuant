/**
 * Trading — fills + orders tabs with filtering.
 */
import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import { useDataHealth } from "@/hooks/useDashboard";
import { StockTag } from "@/components/ui/StockTag";
import { SideBadge, OrderStatusBadge } from "@/components/ui/StatusBadge";
import { fmtMoney, fmtInt, fmtNum, priceColor } from "@/lib/format";

type FillRow = {
  trade_date: string;
  symbol: string;
  name: string;
  side: string;
  qty: number;
  price: number;
  amount: number;
  cost: number;
  strategy: string;
};

type OrderRow = {
  client_id: string;
  trade_date: string;
  symbol: string;
  name: string;
  side: string;
  qty: number;
  order_type: string;
  status: string;
  fill_qty: number;
  fill_price: number | null;
  rejected_reason: string | null;
  strategy: string;
};

export function Trading() {
  const health = useDataHealth();
  const active = health.data?.active_strategy ?? undefined;
  const [tab, setTab] = useState<"fills" | "orders">("fills");
  const [sideFilter, setSideFilter] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<string>("");

  if (!active) return <Placeholder>无活跃策略</Placeholder>;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">交易流水</h1>
        <div className="mt-1 text-xs text-muted">
          策略 <span className="font-mono">{active}</span>
        </div>
      </div>

      <div className="flex items-center gap-3 border-b border-border">
        <button
          onClick={() => setTab("fills")}
          className={`px-3 py-2 text-sm transition-colors ${
            tab === "fills"
              ? "border-b-2 border-accent text-foreground"
              : "text-muted hover:text-foreground"
          }`}
        >
          成交 (Fills)
        </button>
        <button
          onClick={() => setTab("orders")}
          className={`px-3 py-2 text-sm transition-colors ${
            tab === "orders"
              ? "border-b-2 border-accent text-foreground"
              : "text-muted hover:text-foreground"
          }`}
        >
          订单 (Orders)
        </button>
      </div>

      {tab === "fills" ? (
        <FillsView strategy={active} sideFilter={sideFilter} setSideFilter={setSideFilter} />
      ) : (
        <OrdersView
          strategy={active}
          statusFilter={statusFilter}
          setStatusFilter={setStatusFilter}
        />
      )}
    </div>
  );
}

function FillsView({
  strategy,
  sideFilter,
  setSideFilter,
}: {
  strategy: string;
  sideFilter: string;
  setSideFilter: (v: string) => void;
}) {
  const { data: rows = [], isLoading } = useQuery<FillRow[]>({
    queryKey: ["fills", strategy, sideFilter],
    queryFn: async () =>
      (await api.get(`/paper/${strategy}/fills`, {
        params: { side: sideFilter || undefined, limit: 500 },
      })).data,
    refetchInterval: 60_000,
  });

  const totalAmt = useMemo(() => rows.reduce((a, r) => a + r.amount, 0), [rows]);
  const totalCost = useMemo(() => rows.reduce((a, r) => a + r.cost, 0), [rows]);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <select
          value={sideFilter}
          onChange={(e) => setSideFilter(e.target.value)}
          className="rounded border border-border bg-card px-2 py-1 text-sm text-foreground"
        >
          <option value="">全部方向</option>
          <option value="buy">仅买入</option>
          <option value="sell">仅卖出</option>
        </select>
        <div className="text-xs text-muted">
          {rows.length} 笔 · 总额 {fmtMoney(totalAmt)} · 总成本 {fmtMoney(totalCost)}
        </div>
      </div>

      <div className="overflow-hidden rounded-lg border border-border bg-card">
        <table className="w-full text-sm">
          <thead className="bg-card text-xs text-muted">
            <tr className="border-b border-border">
              <th className="px-4 py-3 text-left font-medium">日期</th>
              <th className="px-4 py-3 text-left font-medium">代码 / 名称</th>
              <th className="px-4 py-3 text-left font-medium">方向</th>
              <th className="px-4 py-3 text-right font-medium">数量</th>
              <th className="px-4 py-3 text-right font-medium">成交价</th>
              <th className="px-4 py-3 text-right font-medium">成交额</th>
              <th className="px-4 py-3 text-right font-medium">成本</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td colSpan={7} className="py-8 text-center text-muted">加载中…</td></tr>
            )}
            {rows.map((r, i) => (
              <tr key={`${r.trade_date}-${r.symbol}-${i}`} className="border-b border-border last:border-b-0 hover:bg-card/70">
                <td className="px-4 py-2 tabular text-muted">{r.trade_date}</td>
                <td className="px-4 py-2"><StockTag symbol={r.symbol} name={r.name} /></td>
                <td className="px-4 py-2"><SideBadge side={r.side} /></td>
                <td className="px-4 py-2 text-right tabular">{fmtInt(r.qty)}</td>
                <td className="px-4 py-2 text-right tabular">¥{fmtNum(r.price, 3)}</td>
                <td className="px-4 py-2 text-right tabular">{fmtMoney(r.amount)}</td>
                <td className="px-4 py-2 text-right tabular text-muted">¥{fmtNum(r.cost, 2)}</td>
              </tr>
            ))}
            {!isLoading && rows.length === 0 && (
              <tr><td colSpan={7} className="py-8 text-center text-sm text-muted">无成交记录</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function OrdersView({
  strategy,
  statusFilter,
  setStatusFilter,
}: {
  strategy: string;
  statusFilter: string;
  setStatusFilter: (v: string) => void;
}) {
  const { data: rows = [], isLoading } = useQuery<OrderRow[]>({
    queryKey: ["orders", strategy, statusFilter],
    queryFn: async () =>
      (await api.get(`/paper/${strategy}/orders`, {
        params: { status: statusFilter || undefined, limit: 500 },
      })).data,
    refetchInterval: 60_000,
  });

  // Rejection reason aggregation
  const rejStats = useMemo(() => {
    const buckets: Record<string, number> = {};
    let total = 0;
    let rejected = 0;
    for (const o of rows) {
      total++;
      if (o.status === "rejected") {
        rejected++;
        const r = o.rejected_reason ?? "unknown";
        buckets[r] = (buckets[r] ?? 0) + 1;
      }
    }
    return { total, rejected, buckets };
  }, [rows]);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="rounded border border-border bg-card px-2 py-1 text-sm text-foreground"
        >
          <option value="">全部状态</option>
          <option value="filled">已成交</option>
          <option value="rejected">已拒</option>
          <option value="pending">挂起</option>
        </select>
        <div className="text-xs text-muted">
          {rejStats.total} 单 · 拒单率{" "}
          <span className="font-mono">
            {rejStats.total > 0 ? ((rejStats.rejected / rejStats.total) * 100).toFixed(1) : "0"}%
          </span>
        </div>
      </div>

      {/* Rejection bucket summary */}
      {Object.keys(rejStats.buckets).length > 0 && (
        <div className="rounded border border-border bg-card p-3 text-xs">
          <div className="mb-2 font-medium text-muted">拒单原因分布</div>
          <div className="flex flex-wrap gap-2">
            {Object.entries(rejStats.buckets)
              .sort(([, a], [, b]) => b - a)
              .map(([reason, count]) => (
                <span
                  key={reason}
                  className="rounded bg-danger/10 px-2 py-0.5 text-danger"
                >
                  {reason} × {count}
                </span>
              ))}
          </div>
        </div>
      )}

      <div className="overflow-hidden rounded-lg border border-border bg-card">
        <table className="w-full text-sm">
          <thead className="bg-card text-xs text-muted">
            <tr className="border-b border-border">
              <th className="px-4 py-3 text-left font-medium">日期</th>
              <th className="px-4 py-3 text-left font-medium">代码 / 名称</th>
              <th className="px-4 py-3 text-left font-medium">方向</th>
              <th className="px-4 py-3 text-right font-medium">数量</th>
              <th className="px-4 py-3 text-left font-medium">状态</th>
              <th className="px-4 py-3 text-right font-medium">成交价</th>
              <th className="px-4 py-3 text-left font-medium">拒单原因</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr><td colSpan={7} className="py-8 text-center text-muted">加载中…</td></tr>
            )}
            {rows.map((o) => (
              <tr key={o.client_id} className="border-b border-border last:border-b-0 hover:bg-card/70">
                <td className="px-4 py-2 tabular text-muted">{o.trade_date}</td>
                <td className="px-4 py-2"><StockTag symbol={o.symbol} name={o.name} /></td>
                <td className="px-4 py-2"><SideBadge side={o.side} /></td>
                <td className="px-4 py-2 text-right tabular">{fmtInt(o.qty)}</td>
                <td className="px-4 py-2"><OrderStatusBadge status={o.status} /></td>
                <td className="px-4 py-2 text-right tabular">
                  {o.fill_price != null ? `¥${fmtNum(o.fill_price, 3)}` : "—"}
                </td>
                <td className="px-4 py-2 text-xs text-muted">
                  {o.rejected_reason ?? ""}
                </td>
              </tr>
            ))}
            {!isLoading && rows.length === 0 && (
              <tr><td colSpan={7} className="py-8 text-center text-sm text-muted">无订单记录</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Placeholder({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-[60vh] items-center justify-center text-sm text-muted">{children}</div>
  );
}
