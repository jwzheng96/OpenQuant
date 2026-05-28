/**
 * Holdings — current open positions for the active strategy.
 */
import { useMemo, useState } from "react";
import {
  ColumnDef,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  SortingState,
  useReactTable,
} from "@tanstack/react-table";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpDown } from "lucide-react";

import { api } from "@/lib/api";
import { useDataHealth } from "@/hooks/useDashboard";
import { StockTag } from "@/components/ui/StockTag";
import { PriceDelta } from "@/components/ui/PriceDelta";
import { SidePanel } from "@/components/ui/SidePanel";
import { StockDetail } from "@/components/StockDetail";
import { fmtMoney, fmtNum, fmtInt, priceColor } from "@/lib/format";

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

function usePositions(strategy: string | undefined) {
  return useQuery<Position[]>({
    queryKey: ["positions", strategy],
    queryFn: async () => (await api.get(`/paper/${strategy}/positions`)).data,
    enabled: !!strategy,
    refetchInterval: 30_000,
  });
}

export function Holdings() {
  const health = useDataHealth();
  const active = health.data?.active_strategy ?? undefined;
  const { data: rows = [], isLoading, error } = usePositions(active);
  const [sorting, setSorting] = useState<SortingState>([{ id: "market_value", desc: true }]);
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const selectedRow = rows.find((r) => r.symbol === selectedSymbol);

  const columns = useMemo<ColumnDef<Position>[]>(
    () => [
      {
        id: "symbol",
        header: "代码 / 名称",
        accessorFn: (r) => r.symbol,
        cell: ({ row }) => <StockTag symbol={row.original.symbol} name={row.original.name} size="md" />,
      },
      {
        id: "qty",
        header: "持仓 / 可卖",
        accessorFn: (r) => r.qty,
        cell: ({ row }) => (
          <div className="text-right tabular">
            <div>{fmtInt(row.original.qty)}</div>
            <div className="text-xs text-muted">{fmtInt(row.original.sellable_qty)} 可卖</div>
          </div>
        ),
      },
      {
        id: "avg_cost",
        header: "成本价",
        accessorFn: (r) => r.avg_cost,
        cell: ({ row }) => (
          <div className="text-right tabular">¥{fmtNum(row.original.avg_cost, 2)}</div>
        ),
      },
      {
        id: "last_close",
        header: "现价",
        accessorFn: (r) => r.last_close ?? 0,
        cell: ({ row }) =>
          row.original.last_close != null ? (
            <div className="text-right tabular">¥{fmtNum(row.original.last_close, 2)}</div>
          ) : (
            <div className="text-right text-muted">—</div>
          ),
      },
      {
        id: "market_value",
        header: "市值",
        accessorFn: (r) => r.market_value ?? 0,
        cell: ({ row }) => (
          <div className="text-right tabular">{fmtMoney(row.original.market_value)}</div>
        ),
      },
      {
        id: "pnl_amount",
        header: "浮动盈亏 ¥",
        accessorFn: (r) => r.pnl_amount ?? 0,
        cell: ({ row }) => (
          <div className="text-right">
            <PriceDelta value={row.original.pnl_amount} format="money" />
          </div>
        ),
      },
      {
        id: "pnl_pct",
        header: "盈亏 %",
        accessorFn: (r) => r.pnl_pct ?? 0,
        cell: ({ row }) => (
          <div className="text-right">
            <PriceDelta value={row.original.pnl_pct} format="pct" />
          </div>
        ),
      },
      {
        id: "weight",
        header: "权重",
        accessorFn: (r) => r.weight ?? 0,
        cell: ({ row }) => (
          <div className="text-right tabular">
            {row.original.weight != null ? `${(row.original.weight * 100).toFixed(2)}%` : "—"}
          </div>
        ),
      },
    ],
    [],
  );

  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  // Aggregate metrics
  const stats = useMemo(() => {
    if (!rows.length) return null;
    const mv = rows.reduce((a, r) => a + (r.market_value ?? 0), 0);
    const pnl = rows.reduce((a, r) => a + (r.pnl_amount ?? 0), 0);
    const winners = rows.filter((r) => (r.pnl_pct ?? 0) > 0).length;
    const losers = rows.filter((r) => (r.pnl_pct ?? 0) < 0).length;
    return { mv, pnl, winners, losers };
  }, [rows]);

  if (!active) return <Placeholder>无活跃策略</Placeholder>;
  if (isLoading) return <Placeholder>加载持仓…</Placeholder>;
  if (error) return <Placeholder>加载失败: {(error as Error).message}</Placeholder>;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">当前持仓</h1>
          <div className="mt-1 text-xs text-muted">
            策略 <span className="font-mono">{active}</span>
            {stats && (
              <>
                <span className="mx-2">·</span>
                {rows.length} 只持仓
                <span className="mx-2">·</span>
                总市值 {fmtMoney(stats.mv)}
                <span className="mx-2">·</span>
                浮动盈亏 <span className={priceColor(stats.pnl)}>{fmtMoney(stats.pnl)}</span>
                <span className="mx-2">·</span>
                <span className="text-up">{stats.winners}↑</span>
                <span className="ml-1 text-down">{stats.losers}↓</span>
              </>
            )}
          </div>
        </div>
      </div>

      <div className="text-xs text-muted">提示: 点击任意行查看个股 K 线 + 成交历史 + 因子值</div>

      <div className="overflow-hidden rounded-lg border border-border bg-card">
        <table className="w-full text-sm">
          <thead className="bg-card text-xs text-muted">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id} className="border-b border-border">
                {hg.headers.map((h) => (
                  <th
                    key={h.id}
                    className="px-4 py-3 text-left font-medium first:pl-5"
                    onClick={h.column.getToggleSortingHandler()}
                  >
                    <div className="inline-flex cursor-pointer items-center gap-1 select-none">
                      {flexRender(h.column.columnDef.header, h.getContext())}
                      <ArrowUpDown className="size-3 opacity-50" />
                    </div>
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => (
              <tr
                key={row.id}
                onClick={() => setSelectedSymbol(row.original.symbol)}
                className="cursor-pointer border-b border-border last:border-b-0 hover:bg-bg"
              >
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="px-4 py-2.5 first:pl-5">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
            {table.getRowModel().rows.length === 0 && (
              <tr>
                <td colSpan={columns.length} className="px-4 py-8 text-center text-sm text-muted">
                  无持仓
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Side panel — stock detail */}
      <SidePanel
        open={selectedSymbol != null}
        onClose={() => setSelectedSymbol(null)}
        title={
          selectedRow ? (
            <div className="flex items-baseline gap-2">
              <StockTag symbol={selectedRow.symbol} name={selectedRow.name} size="md" />
              {selectedRow.pnl_pct != null && (
                <PriceDelta value={selectedRow.pnl_pct} format="pct" />
              )}
            </div>
          ) : (
            <span className="font-mono text-foreground">{selectedSymbol}</span>
          )
        }
      >
        {active && selectedSymbol && (
          <StockDetail strategy={active} symbol={selectedSymbol} />
        )}
      </SidePanel>
    </div>
  );
}

function Placeholder({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-[60vh] items-center justify-center text-sm text-muted">{children}</div>
  );
}
