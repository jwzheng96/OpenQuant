/**
 * Strategies — all strategy yamls + their backtested KPIs.
 *
 * Multi-select rows → "对比" button → opens A/B comparison side panel.
 */
import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { GitCompare } from "lucide-react";

import { api } from "@/lib/api";
import { fmtPct, fmtNum, priceColor } from "@/lib/format";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { SidePanel } from "@/components/ui/SidePanel";
import { ExportCsvButton } from "@/components/ui/ExportCsvButton";
import { CompareView } from "@/components/CompareView";

type StrategyRow = {
  meta: {
    name: string;
    type: string;
    factors: { name: string; weight: number; direction: number }[];
    top_n: number;
    rebalance_freq: string;
    benchmark: string;
    enabled: boolean;
    is_active: boolean;
    yaml_path: string;
  };
  kpi: {
    available: boolean;
    total_return: number | null;
    sharpe: number | null;
    max_drawdown: number | null;
    annualized_vol: number | null;
    last_run: string | null;
    last_date: string | null;
  };
};

export function Strategies() {
  const { data: rows = [], isLoading, error } = useQuery<StrategyRow[]>({
    queryKey: ["strategies"],
    queryFn: async () => (await api.get("/strategies")).data,
    refetchInterval: 60_000,
  });
  const [filter, setFilter] = useState("");
  const [showOnlyBacktested, setShowOnlyBacktested] = useState(true);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [compareOpen, setCompareOpen] = useState(false);

  const filtered = useMemo(() => {
    return rows.filter((r) => {
      if (showOnlyBacktested && !r.kpi.available) return false;
      if (filter && !r.meta.name.toLowerCase().includes(filter.toLowerCase())) return false;
      return true;
    });
  }, [rows, filter, showOnlyBacktested]);

  const toggleSelect = (name: string, available: boolean) => {
    if (!available) return;
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const clearSelection = () => setSelected(new Set());

  const selectedList = useMemo(
    () => filtered.filter((r) => selected.has(r.meta.name)).map((r) => r.meta.name),
    [filtered, selected],
  );

  if (isLoading) return <Placeholder>加载策略…</Placeholder>;
  if (error) return <Placeholder>加载失败: {(error as Error).message}</Placeholder>;

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">策略</h1>
          <div className="mt-1 text-xs text-muted">
            共 {rows.length} 个 · 已回测 {rows.filter((r) => r.kpi.available).length} · 当前活跃{" "}
            <span className="font-mono text-accent">
              {rows.find((r) => r.meta.is_active)?.meta.name ?? "—"}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {selected.size > 0 && (
            <button
              type="button"
              onClick={clearSelection}
              className="rounded border border-border px-3 py-1.5 text-xs text-muted hover:text-foreground"
            >
              清空选择 ({selected.size})
            </button>
          )}
          <button
            type="button"
            disabled={selected.size < 2}
            onClick={() => setCompareOpen(true)}
            className={`inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium transition-colors ${
              selected.size >= 2
                ? "bg-accent text-white hover:opacity-90"
                : "cursor-not-allowed bg-muted/10 text-muted"
            }`}
          >
            <GitCompare className="size-3.5" />
            对比 ({selected.size})
          </button>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <input
          type="text"
          placeholder="按名称筛选…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="w-64 rounded border border-border bg-card px-3 py-1.5 text-sm text-foreground placeholder:text-muted"
        />
        <label className="inline-flex items-center gap-1.5 text-xs text-muted">
          <input
            type="checkbox"
            checked={showOnlyBacktested}
            onChange={(e) => setShowOnlyBacktested(e.target.checked)}
          />
          仅显示已回测
        </label>
        <div className="ml-auto flex items-center gap-3 text-xs text-muted">
          <span>提示: 勾选 ≥2 个策略后点 "对比"</span>
          <ExportCsvButton<StrategyRow>
            rows={filtered}
            filenamePrefix="strategies"
            cols={[
              { header: "name", get: (r) => r.meta.name },
              { header: "type", get: (r) => r.meta.type },
              { header: "factors", get: (r) => r.meta.factors.map((f) => `${f.name}x${f.weight}`).join("|") },
              { header: "top_n", get: (r) => r.meta.top_n },
              { header: "rebalance_freq", get: (r) => r.meta.rebalance_freq },
              { header: "is_active", get: (r) => r.meta.is_active ? "yes" : "no" },
              { header: "total_return_pct", get: (r) => r.kpi.total_return != null ? (r.kpi.total_return * 100).toFixed(4) : "" },
              { header: "sharpe", get: (r) => r.kpi.sharpe?.toFixed(4) ?? "" },
              { header: "max_drawdown_pct", get: (r) => r.kpi.max_drawdown != null ? (r.kpi.max_drawdown * 100).toFixed(4) : "" },
              { header: "annualized_vol_pct", get: (r) => r.kpi.annualized_vol != null ? (r.kpi.annualized_vol * 100).toFixed(4) : "" },
              { header: "last_run", get: (r) => r.kpi.last_run ?? "" },
            ]}
          />
        </div>
      </div>

      <div className="overflow-hidden rounded-lg border border-border bg-card">
        <table className="w-full text-sm">
          <thead className="bg-card text-xs text-muted">
            <tr className="border-b border-border">
              <th className="px-3 py-3 text-left font-medium">
                <span className="sr-only">选择</span>
              </th>
              <th className="px-4 py-3 text-left font-medium">策略</th>
              <th className="px-4 py-3 text-left font-medium">类型</th>
              <th className="px-4 py-3 text-left font-medium">因子</th>
              <th className="px-4 py-3 text-left font-medium">调仓</th>
              <th className="px-4 py-3 text-right font-medium">top_n</th>
              <th className="px-4 py-3 text-right font-medium">累计</th>
              <th className="px-4 py-3 text-right font-medium">Sharpe</th>
              <th className="px-4 py-3 text-right font-medium">MDD</th>
              <th className="px-4 py-3 text-left font-medium">上次跑</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((r) => {
              const isSel = selected.has(r.meta.name);
              const isAvail = r.kpi.available;
              return (
                <tr
                  key={r.meta.name}
                  className={`border-b border-border last:border-b-0 hover:bg-bg ${
                    isSel ? "bg-accent/5" : ""
                  }`}
                >
                  <td className="px-3 py-2">
                    <input
                      type="checkbox"
                      checked={isSel}
                      disabled={!isAvail}
                      onChange={() => toggleSelect(r.meta.name, isAvail)}
                      title={isAvail ? "勾选以对比" : "未回测无法对比"}
                    />
                  </td>
                  <td className="px-4 py-2">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-foreground">{r.meta.name}</span>
                      {r.meta.is_active && <StatusBadge text="活跃" tone="accent" />}
                    </div>
                  </td>
                  <td className="px-4 py-2 text-xs text-muted">{r.meta.type}</td>
                  <td className="px-4 py-2 text-xs">
                    <div className="flex flex-wrap gap-1">
                      {r.meta.factors.slice(0, 3).map((f) => (
                        <span
                          key={f.name}
                          className="rounded bg-muted/10 px-1.5 py-0.5 font-mono text-muted"
                        >
                          {f.name}×{f.weight}
                        </span>
                      ))}
                      {r.meta.factors.length > 3 && (
                        <span className="text-muted">+{r.meta.factors.length - 3}</span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-2 text-xs text-muted">{r.meta.rebalance_freq}</td>
                  <td className="px-4 py-2 text-right tabular text-xs">{r.meta.top_n}</td>
                  <td className={`px-4 py-2 text-right tabular ${priceColor(r.kpi.total_return)}`}>
                    {r.kpi.total_return != null ? fmtPct(r.kpi.total_return) : "—"}
                  </td>
                  <td className="px-4 py-2 text-right tabular">
                    {r.kpi.sharpe != null ? fmtNum(r.kpi.sharpe, 2) : "—"}
                  </td>
                  <td className="px-4 py-2 text-right tabular text-down">
                    {r.kpi.max_drawdown != null ? fmtPct(r.kpi.max_drawdown) : "—"}
                  </td>
                  <td className="px-4 py-2 text-xs text-muted">{r.kpi.last_run ?? "—"}</td>
                </tr>
              );
            })}
            {filtered.length === 0 && (
              <tr>
                <td colSpan={10} className="py-8 text-center text-sm text-muted">
                  无匹配策略
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <SidePanel
        open={compareOpen && selectedList.length >= 2}
        onClose={() => setCompareOpen(false)}
        title={
          <div className="flex items-center gap-2">
            <GitCompare className="size-4 text-accent" />
            <span className="font-semibold">策略 A/B 对比</span>
            <span className="text-xs text-muted">{selectedList.length} 个策略</span>
          </div>
        }
        width="w-[900px]"
      >
        {compareOpen && selectedList.length >= 2 && <CompareView names={selectedList} />}
      </SidePanel>
    </div>
  );
}

function Placeholder({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-[60vh] items-center justify-center text-sm text-muted">{children}</div>
  );
}
