/**
 * Watchlist — user's saved stocks with live price + multi-window returns.
 */
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Plus, X, Loader2 } from "lucide-react";

import { useWatchlist, useAddWatch, useRemoveWatch } from "@/hooks/useWatchlist";
import { StockTag } from "@/components/ui/StockTag";
import { PriceDelta } from "@/components/ui/PriceDelta";
import { ExportCsvButton } from "@/components/ui/ExportCsvButton";
import { fmtMoney, fmtNum } from "@/lib/format";

export function Watchlist() {
  const { t } = useTranslation();
  const { data: rows = [], isLoading } = useWatchlist();
  const add = useAddWatch();
  const remove = useRemoveWatch();
  const [symbolInput, setSymbolInput] = useState("");
  const [noteInput, setNoteInput] = useState("");

  const onAdd = (e: React.FormEvent) => {
    e.preventDefault();
    const sym = symbolInput.trim().toUpperCase();
    if (!/^\d{6}\.[A-Z]{2}$/.test(sym)) {
      alert("格式: 600519.SH / 000001.SZ");
      return;
    }
    add.mutate(
      { symbol: sym, note: noteInput.trim() || undefined },
      {
        onSuccess: () => {
          setSymbolInput("");
          setNoteInput("");
        },
      },
    );
  };

  const errMsg =
    add.error && (add.error as any).response?.data?.detail
      ? String((add.error as any).response.data.detail)
      : null;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">{t("watchlist.title", "观察列表")}</h1>
        <div className="mt-1 text-xs text-muted">
          {t("watchlist.subtitle", "保存关注的股票，实时跟踪 1/5/20 日涨跌")}
        </div>
      </div>

      {/* Add form */}
      <form onSubmit={onAdd} className="flex items-center gap-3 rounded-lg border border-border bg-card p-3">
        <input
          type="text"
          placeholder="600519.SH"
          value={symbolInput}
          onChange={(e) => setSymbolInput(e.target.value)}
          className="w-32 rounded border border-border bg-bg px-2 py-1.5 font-mono text-sm tabular text-foreground"
          required
        />
        <input
          type="text"
          placeholder="备注 (可选)"
          value={noteInput}
          onChange={(e) => setNoteInput(e.target.value)}
          className="flex-1 rounded border border-border bg-bg px-2 py-1.5 text-sm text-foreground"
        />
        <button
          type="submit"
          disabled={add.isPending}
          className="inline-flex items-center gap-1.5 rounded bg-accent px-3 py-1.5 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          {add.isPending ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
          {t("watchlist.add", "加入")}
        </button>
        <ExportCsvButton
          rows={rows}
          filenamePrefix="watchlist"
          cols={[
            { header: "symbol", get: (r) => r.symbol },
            { header: "name", get: (r) => r.name },
            { header: "note", get: (r) => r.note ?? "" },
            { header: "last_close", get: (r) => r.last_close ?? "" },
            { header: "pct_today", get: (r) => r.pct_chg_today != null ? (r.pct_chg_today * 100).toFixed(4) : "" },
            { header: "pct_5d", get: (r) => r.pct_chg_5d != null ? (r.pct_chg_5d * 100).toFixed(4) : "" },
            { header: "pct_20d", get: (r) => r.pct_chg_20d != null ? (r.pct_chg_20d * 100).toFixed(4) : "" },
            { header: "added_at", get: (r) => r.added_at },
          ]}
        />
      </form>
      {errMsg && (
        <div className="rounded border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">{errMsg}</div>
      )}

      {/* Table */}
      <div className="overflow-hidden rounded-lg border border-border bg-card">
        <table className="w-full text-sm">
          <thead className="bg-card text-xs text-muted">
            <tr className="border-b border-border">
              <th className="px-4 py-3 text-left font-medium">代码 / 名称</th>
              <th className="px-4 py-3 text-right font-medium">现价</th>
              <th className="px-4 py-3 text-right font-medium">日内</th>
              <th className="px-4 py-3 text-right font-medium">5 日</th>
              <th className="px-4 py-3 text-right font-medium">20 日</th>
              <th className="px-4 py-3 text-left font-medium">备注</th>
              <th className="px-4 py-3 text-left font-medium">加入</th>
              <th className="px-4 py-3 text-right font-medium"></th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr>
                <td colSpan={8} className="py-8 text-center text-muted">
                  <Loader2 className="mx-auto size-4 animate-spin" />
                </td>
              </tr>
            )}
            {rows.map((r) => (
              <tr key={r.symbol} className="border-b border-border last:border-b-0 hover:bg-bg">
                <td className="px-4 py-2"><StockTag symbol={r.symbol} name={r.name} /></td>
                <td className="px-4 py-2 text-right tabular">
                  {r.last_close != null ? `¥${fmtNum(r.last_close, 2)}` : "—"}
                </td>
                <td className="px-4 py-2 text-right">
                  <PriceDelta value={r.pct_chg_today} format="pct" />
                </td>
                <td className="px-4 py-2 text-right">
                  <PriceDelta value={r.pct_chg_5d} format="pct" />
                </td>
                <td className="px-4 py-2 text-right">
                  <PriceDelta value={r.pct_chg_20d} format="pct" />
                </td>
                <td className="px-4 py-2 text-xs text-foreground/80">{r.note || ""}</td>
                <td className="px-4 py-2 tabular text-xs text-muted">
                  {new Date(r.added_at).toLocaleDateString("zh-CN")}
                </td>
                <td className="px-4 py-2 text-right">
                  <button
                    type="button"
                    onClick={() => remove.mutate(r.symbol)}
                    className="rounded p-1 text-muted hover:bg-danger/10 hover:text-danger"
                    title="移除"
                  >
                    <X className="size-3.5" />
                  </button>
                </td>
              </tr>
            ))}
            {!isLoading && rows.length === 0 && (
              <tr>
                <td colSpan={8} className="py-8 text-center text-sm text-muted">
                  {t("watchlist.empty", "观察列表为空")}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
