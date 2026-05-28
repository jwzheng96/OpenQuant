/**
 * Stock detail side-panel content.
 * Loaded when user clicks a Holdings table row.
 */
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  TrendingUp,
  Layers,
  Briefcase,
} from "lucide-react";

import { api } from "@/lib/api";
import { StockTag } from "@/components/ui/StockTag";
import { PriceDelta } from "@/components/ui/PriceDelta";
import { SideBadge } from "@/components/ui/StatusBadge";
import { KlineMini, type KlineBar, type FillMark } from "@/components/charts/KlineMini";
import { fmtMoney, fmtNum, fmtInt, priceColor } from "@/lib/format";

type FactorSnapshot = {
  name: string;
  latest_date: string | null;
  latest_value: number | null;
  series: { trade_date: string; value: number }[];
};

type Position = {
  symbol: string;
  name: string;
  qty: number;
  sellable_qty: number;
  avg_cost: number;
  last_close: number | null;
  market_value: number | null;
  pnl_amount: number | null;
  pnl_pct: number | null;
  weight: number | null;
  locked_qty: number;
};

type Fill = {
  trade_date: string;
  side: string;
  qty: number;
  price: number;
  amount: number;
};

type StockDetail = {
  symbol: string;
  name: string;
  current_position: Position | null;
  kline: KlineBar[];
  fills: Fill[];
  factors: FactorSnapshot[];
};

export function StockDetail({
  strategy,
  symbol,
}: {
  strategy: string;
  symbol: string;
}) {
  const { data, isLoading, error } = useQuery<StockDetail>({
    queryKey: ["stock-detail", strategy, symbol],
    queryFn: async () =>
      (await api.get(`/paper/${strategy}/stock/${symbol}/detail`)).data,
    refetchInterval: 60_000,
  });

  if (isLoading)
    return (
      <div className="flex h-48 items-center justify-center text-sm text-muted">
        加载…
      </div>
    );
  if (error || !data)
    return (
      <div className="flex h-48 items-center justify-center text-sm text-danger">
        加载失败 {error ? `: ${(error as Error).message}` : ""}
      </div>
    );

  const fills: FillMark[] = data.fills.map((f) => ({
    trade_date: f.trade_date,
    side: f.side as "buy" | "sell",
    qty: f.qty,
    price: f.price,
  }));

  return (
    <div className="space-y-5">
      {/* Position summary card */}
      {data.current_position ? (
        <PositionCard p={data.current_position} />
      ) : (
        <div className="rounded border border-dashed border-border bg-bg p-3 text-xs text-muted">
          当前未持有该股票
        </div>
      )}

      {/* K-line + buy/sell marks */}
      <section>
        <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-muted">
          <TrendingUp className="size-3.5" />
          K 线 + 成交标记（红 ▲ 买 / 绿 ▼ 卖）
        </h3>
        <div className="rounded border border-border bg-bg">
          <KlineMini bars={data.kline} fills={fills} height={280} />
        </div>
      </section>

      {/* Fills timeline */}
      <section>
        <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-muted">
          <Activity className="size-3.5" />
          本策略成交历史 ({data.fills.length} 笔)
        </h3>
        {data.fills.length === 0 ? (
          <div className="rounded border border-dashed border-border bg-bg p-3 text-center text-xs text-muted">
            该策略未交易过此股票
          </div>
        ) : (
          <div className="overflow-hidden rounded border border-border">
            <table className="w-full text-xs">
              <thead className="bg-bg text-muted">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">日期</th>
                  <th className="px-3 py-2 text-left font-medium">方向</th>
                  <th className="px-3 py-2 text-right font-medium">数量</th>
                  <th className="px-3 py-2 text-right font-medium">价格</th>
                  <th className="px-3 py-2 text-right font-medium">成交额</th>
                </tr>
              </thead>
              <tbody>
                {data.fills.map((f, i) => (
                  <tr key={`${f.trade_date}-${i}`} className="border-t border-border">
                    <td className="px-3 py-1.5 tabular text-muted">{f.trade_date}</td>
                    <td className="px-3 py-1.5"><SideBadge side={f.side} /></td>
                    <td className="px-3 py-1.5 text-right tabular">{fmtInt(f.qty)}</td>
                    <td className="px-3 py-1.5 text-right tabular">¥{fmtNum(f.price, 3)}</td>
                    <td className="px-3 py-1.5 text-right tabular">{fmtMoney(f.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Factor snapshots */}
      <section>
        <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-muted">
          <Layers className="size-3.5" />
          因子值（{data.factors.length} 个因子有数据）
        </h3>
        {data.factors.length === 0 ? (
          <div className="rounded border border-dashed border-border bg-bg p-3 text-center text-xs text-muted">
            无因子数据
          </div>
        ) : (
          <div className="grid grid-cols-2 gap-2">
            {data.factors.map((f) => (
              <FactorCard key={f.name} factor={f} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function PositionCard({ p }: { p: Position }) {
  return (
    <div className="rounded-lg border border-border bg-bg p-4">
      <div className="mb-3 flex items-center gap-1.5 text-xs text-muted">
        <Briefcase className="size-3.5" />
        当前持仓
      </div>
      <div className="grid grid-cols-3 gap-3 text-sm">
        <Field label="数量" value={`${fmtInt(p.qty)} 股`} />
        <Field label="可卖" value={`${fmtInt(p.sellable_qty)} 股`} />
        <Field label="权重" value={p.weight != null ? `${(p.weight * 100).toFixed(2)}%` : "—"} />
        <Field label="成本价" value={`¥${fmtNum(p.avg_cost, 2)}`} />
        <Field
          label="现价"
          value={p.last_close != null ? `¥${fmtNum(p.last_close, 2)}` : "—"}
        />
        <Field label="市值" value={fmtMoney(p.market_value)} />
        <div className="col-span-3 mt-2 flex items-baseline justify-between border-t border-border pt-2">
          <span className="text-xs text-muted">浮动盈亏</span>
          <div className="flex items-baseline gap-2">
            <span className={`text-base font-semibold ${priceColor(p.pnl_amount)}`}>
              {p.pnl_amount != null && p.pnl_amount > 0 ? "+" : ""}
              {fmtMoney(p.pnl_amount)}
            </span>
            <PriceDelta value={p.pnl_pct} format="pct" />
          </div>
        </div>
      </div>
    </div>
  );
}

function Field({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] text-muted">{label}</div>
      <div className="mt-0.5 tabular text-foreground">{value}</div>
    </div>
  );
}

function FactorCard({ factor }: { factor: FactorSnapshot }) {
  const v = factor.latest_value;
  return (
    <div className="rounded border border-border bg-bg p-2.5">
      <div className="truncate font-mono text-[10px] text-muted">{factor.name}</div>
      <div className={`mt-1 tabular text-sm font-semibold ${priceColor(v)}`}>
        {v != null ? v.toFixed(4) : "—"}
      </div>
      <div className="mt-0.5 text-[10px] text-muted">{factor.latest_date ?? "—"}</div>
    </div>
  );
}
