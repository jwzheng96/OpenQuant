/**
 * Backtest — stub. Real implementation lands in Phase 2:
 *  - submit yaml + date range form
 *  - subprocess paper_daily.py → tasks DB row
 *  - SSE log stream
 *  - completed task: NAV + KPI + fills
 */
import { Play } from "lucide-react";

export function Backtest() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold tracking-tight">回测</h1>
      <div className="rounded-lg border border-dashed border-border bg-card p-12 text-center">
        <Play className="mx-auto size-8 text-muted" />
        <div className="mt-3 text-sm font-medium text-foreground">回测运行器即将上线</div>
        <div className="mt-1 text-xs text-muted">
          Phase 2 实施：在网页提交 backtest 任务 → 实时 SSE 日志流 → NAV/KPI/成交一站式查看
        </div>
      </div>
    </div>
  );
}
