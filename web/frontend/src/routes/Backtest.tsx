/**
 * Backtest runner.
 *
 *  Tab "新建"   — form submits POST /backtest/run, jumps to the new task
 *  Tab "任务"   — list of recent tasks across users
 *  When a task is selected: side panel with live log (SSE) + result.
 */
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Play,
  Square,
  CheckCircle2,
  XCircle,
  Loader2,
  Clock,
} from "lucide-react";

import { api } from "@/lib/api";
import {
  Task,
  useSubmitBacktest,
  useTasks,
  useTask,
  useCancelTask,
  useTaskStream,
} from "@/hooks/useBacktest";
import { useMe } from "@/hooks/useAuth";
import { SidePanel } from "@/components/ui/SidePanel";
import { StatusBadge, type Tone } from "@/components/ui/StatusBadge";
import { fmtMoney, fmtPct, priceColor } from "@/lib/format";

type StrategyOption = { name: string; available: boolean };

function useStrategyOptions() {
  return useQuery<StrategyOption[]>({
    queryKey: ["strategy-options"],
    queryFn: async () => {
      const rows = (await api.get("/strategies")).data as any[];
      return rows.map((r) => ({
        name: r.meta.name,
        available: r.kpi.available,
      }));
    },
    staleTime: 60_000,
  });
}

const statusTone = (st: string): Tone =>
  st === "success" ? "success"
    : st === "failed" ? "danger"
    : st === "cancelled" ? "muted"
    : st === "running" ? "accent"
    : "warning"; // queued

function StatusPill({ status }: { status: string }) {
  return <StatusBadge text={status} tone={statusTone(status)} />;
}

export function Backtest() {
  const me = useMe();
  const role = me.data?.role ?? "viewer";
  const canRun = role === "admin" || role === "trader";

  const [tab, setTab] = useState<"new" | "tasks">("new");
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">回测</h1>
        <div className="mt-1 text-xs text-muted">
          网页提交 + 实时日志 + 任务历史。
          {!canRun && (
            <span className="ml-2 inline-flex items-center rounded bg-warning/10 px-1.5 py-0.5 text-warning">
              你是 viewer，只能查看不能提交
            </span>
          )}
        </div>
      </div>

      <div className="flex items-center gap-3 border-b border-border">
        <button
          type="button"
          onClick={() => setTab("new")}
          className={`px-3 py-2 text-sm transition-colors ${
            tab === "new"
              ? "border-b-2 border-accent text-foreground"
              : "text-muted hover:text-foreground"
          }`}
        >
          新建
        </button>
        <button
          type="button"
          onClick={() => setTab("tasks")}
          className={`px-3 py-2 text-sm transition-colors ${
            tab === "tasks"
              ? "border-b-2 border-accent text-foreground"
              : "text-muted hover:text-foreground"
          }`}
        >
          任务历史
        </button>
      </div>

      {tab === "new" ? (
        <NewBacktestForm
          canRun={canRun}
          onSubmitted={(id) => {
            setSelectedTaskId(id);
            setTab("tasks");
          }}
        />
      ) : (
        <TasksList onSelect={setSelectedTaskId} />
      )}

      <SidePanel
        open={!!selectedTaskId}
        onClose={() => setSelectedTaskId(null)}
        title={
          <div className="flex items-center gap-2 text-sm">
            <Clock className="size-4" />
            任务详情
            <span className="font-mono text-xs text-muted">
              {selectedTaskId?.slice(0, 8)}
            </span>
          </div>
        }
        width="w-[860px]"
      >
        {selectedTaskId && <TaskDetail taskId={selectedTaskId} />}
      </SidePanel>
    </div>
  );
}

// ----------------------------------------------------------------------------
// New backtest form
// ----------------------------------------------------------------------------

function NewBacktestForm({
  canRun,
  onSubmitted,
}: {
  canRun: boolean;
  onSubmitted: (id: string) => void;
}) {
  const opts = useStrategyOptions();
  const submit = useSubmitBacktest();

  const [strategy, setStrategy] = useState("");
  const [start, setStart] = useState("2024-01-02");
  const [end, setEnd] = useState(new Date().toISOString().slice(0, 10));
  const [initialCash, setInitialCash] = useState(1_000_000);
  const [reset, setReset] = useState(true);

  // Default to first available strategy when options arrive
  useEffect(() => {
    if (!strategy && opts.data && opts.data.length > 0) {
      const first = opts.data.find((o) => o.available) ?? opts.data[0];
      if (first) setStrategy(first.name);
    }
  }, [opts.data, strategy]);

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!strategy) return;
    submit.mutate(
      { strategy, start, end, initial_cash: initialCash, reset },
      { onSuccess: (task) => onSubmitted(task.id) },
    );
  };

  const errMsg =
    submit.error && (submit.error as any).response?.data?.detail
      ? String((submit.error as any).response.data.detail)
      : submit.error
        ? "提交失败"
        : null;

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-4 rounded-lg border border-border bg-card p-5"
    >
      <h2 className="text-sm font-semibold text-foreground/90">参数</h2>

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label className="text-xs text-muted">策略 yaml</label>
          <select
            value={strategy}
            onChange={(e) => setStrategy(e.target.value)}
            className="mt-1 w-full rounded border border-border bg-bg px-2 py-1.5 text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-accent/40"
          >
            {opts.data?.map((o) => (
              <option key={o.name} value={o.name}>
                {o.name} {o.available ? "" : "(未回测)"}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="text-xs text-muted">初始资金 (¥)</label>
          <input
            type="number"
            step="10000"
            min="1000"
            value={initialCash}
            onChange={(e) => setInitialCash(Number(e.target.value))}
            className="mt-1 w-full rounded border border-border bg-bg px-2 py-1.5 text-sm tabular text-foreground focus:outline-none focus:ring-1 focus:ring-accent/40"
          />
        </div>

        <div>
          <label className="text-xs text-muted">开始日期</label>
          <input
            type="date"
            value={start}
            onChange={(e) => setStart(e.target.value)}
            className="mt-1 w-full rounded border border-border bg-bg px-2 py-1.5 text-sm tabular text-foreground focus:outline-none focus:ring-1 focus:ring-accent/40"
          />
        </div>

        <div>
          <label className="text-xs text-muted">结束日期</label>
          <input
            type="date"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
            className="mt-1 w-full rounded border border-border bg-bg px-2 py-1.5 text-sm tabular text-foreground focus:outline-none focus:ring-1 focus:ring-accent/40"
          />
        </div>
      </div>

      <label className="inline-flex items-center gap-2 text-xs text-muted">
        <input
          type="checkbox"
          checked={reset}
          onChange={(e) => setReset(e.target.checked)}
        />
        Reset state (重置 paper_state 从初始资金开始)
      </label>

      {errMsg && (
        <div className="rounded border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
          {errMsg}
        </div>
      )}

      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={!canRun || !strategy || submit.isPending}
          className="inline-flex items-center gap-2 rounded bg-accent px-4 py-2 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <Play className="size-4" />
          {submit.isPending ? "提交中…" : "提交"}
        </button>
        <span className="text-xs text-muted">
          {canRun ? "提交后跳到任务详情查看实时日志" : "需要 trader+ 角色才能提交"}
        </span>
      </div>
    </form>
  );
}

// ----------------------------------------------------------------------------
// Task list
// ----------------------------------------------------------------------------

function TasksList({ onSelect }: { onSelect: (id: string) => void }) {
  const [mineOnly, setMineOnly] = useState(false);
  const { data: tasks = [], isLoading } = useTasks({ mineOnly });

  return (
    <div className="space-y-3">
      <label className="inline-flex items-center gap-1.5 text-xs text-muted">
        <input
          type="checkbox"
          checked={mineOnly}
          onChange={(e) => setMineOnly(e.target.checked)}
        />
        仅我的任务
      </label>

      <div className="overflow-hidden rounded-lg border border-border bg-card">
        <table className="w-full text-sm">
          <thead className="bg-card text-xs text-muted">
            <tr className="border-b border-border">
              <th className="px-4 py-3 text-left font-medium">ID</th>
              <th className="px-4 py-3 text-left font-medium">策略</th>
              <th className="px-4 py-3 text-left font-medium">区间</th>
              <th className="px-4 py-3 text-left font-medium">状态</th>
              <th className="px-4 py-3 text-left font-medium">提交人</th>
              <th className="px-4 py-3 text-left font-medium">创建</th>
              <th className="px-4 py-3 text-right font-medium">耗时</th>
              <th className="px-4 py-3 text-right font-medium">累计</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && (
              <tr>
                <td colSpan={8} className="py-8 text-center text-muted">加载中…</td>
              </tr>
            )}
            {tasks.map((t) => (
              <tr
                key={t.id}
                onClick={() => onSelect(t.id)}
                className="cursor-pointer border-b border-border last:border-b-0 hover:bg-bg"
              >
                <td className="px-4 py-2 font-mono text-xs text-muted">{t.id.slice(0, 8)}</td>
                <td className="px-4 py-2 font-mono">{t.params.strategy}</td>
                <td className="px-4 py-2 text-xs tabular text-muted">
                  {t.params.from} → {t.params.to}
                </td>
                <td className="px-4 py-2"><StatusPill status={t.status} /></td>
                <td className="px-4 py-2 text-xs text-foreground/80">
                  {t.created_by_username ?? "—"}
                </td>
                <td className="px-4 py-2 tabular text-xs text-muted">
                  {new Date(t.created_at).toLocaleString("zh-CN", { hour12: false })}
                </td>
                <td className="px-4 py-2 text-right tabular text-xs text-muted">
                  {t.duration_seconds != null ? `${t.duration_seconds.toFixed(0)}s` : "—"}
                </td>
                <td className="px-4 py-2 text-right tabular">
                  {t.result?.total_return != null ? (
                    <span className={priceColor(t.result.total_return)}>
                      {fmtPct(t.result.total_return)}
                    </span>
                  ) : (
                    <span className="text-muted">—</span>
                  )}
                </td>
              </tr>
            ))}
            {!isLoading && tasks.length === 0 && (
              <tr>
                <td colSpan={8} className="py-8 text-center text-sm text-muted">
                  暂无任务记录
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Task detail + live log
// ----------------------------------------------------------------------------

function TaskDetail({ taskId }: { taskId: string }) {
  const { data: task } = useTask(taskId);
  const cancel = useCancelTask();
  const { lines, done } = useTaskStream(taskId);
  const logEndRef = useRef<HTMLDivElement>(null);

  // Auto-scroll log to bottom
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [lines.length]);

  const isRunning = task?.status === "running" || task?.status === "queued";

  return (
    <div className="space-y-4">
      {task && <TaskHeader task={task} onCancel={() => cancel.mutate(task.id)} />}

      {/* Live log */}
      <section>
        <h3 className="mb-2 text-xs font-semibold text-muted">
          实时日志 {isRunning && <Loader2 className="ml-1 inline size-3 animate-spin" />}
        </h3>
        <div className="max-h-[420px] overflow-y-auto rounded border border-border bg-bg p-3 font-mono text-[11px] leading-snug">
          {lines.length === 0 ? (
            <div className="text-muted">等待日志…</div>
          ) : (
            lines.map((l, i) => (
              <div key={i} className="whitespace-pre-wrap text-foreground/90">
                {l}
              </div>
            ))
          )}
          <div ref={logEndRef} />
        </div>
        {done && (
          <div className="mt-2 flex items-center gap-2 text-xs text-muted">
            流结束 · 最终状态:&nbsp;<StatusPill status={done} />
          </div>
        )}
      </section>

      {/* Result */}
      {task?.result && (
        <section>
          <h3 className="mb-2 text-xs font-semibold text-muted">结果</h3>
          <div className="grid gap-3 sm:grid-cols-3">
            {task.result.nav != null && (
              <Stat label="终值 NAV" value={fmtMoney(task.result.nav)} />
            )}
            {task.result.total_return != null && (
              <Stat
                label="累计收益"
                value={
                  <span className={priceColor(task.result.total_return)}>
                    {fmtPct(task.result.total_return)}
                  </span>
                }
              />
            )}
            {task.result.n_days != null && (
              <Stat label="交易日" value={`${task.result.n_days}`} />
            )}
          </div>
        </section>
      )}
    </div>
  );
}

function TaskHeader({ task, onCancel }: { task: Task; onCancel: () => void }) {
  const isRunning = task.status === "running" || task.status === "queued";
  const Icon =
    task.status === "success" ? CheckCircle2
      : task.status === "failed" ? XCircle
      : task.status === "cancelled" ? Square
      : Loader2;
  return (
    <div className="rounded-lg border border-border bg-bg p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Icon
            className={`size-5 ${
              task.status === "success" ? "text-success"
                : task.status === "failed" ? "text-danger"
                : task.status === "running" ? "animate-spin text-accent"
                : "text-muted"
            }`}
          />
          <div>
            <div className="font-mono text-sm text-foreground">{task.params.strategy}</div>
            <div className="text-xs text-muted">
              {task.params.from} → {task.params.to} · 初始 {fmtMoney(task.params.initial_cash)}
              {task.params.reset && <span className="ml-1">(reset)</span>}
            </div>
          </div>
        </div>
        {isRunning && (
          <button
            type="button"
            onClick={onCancel}
            className="inline-flex items-center gap-1 rounded bg-danger/10 px-2.5 py-1.5 text-xs text-danger hover:bg-danger/20"
          >
            <Square className="size-3" />
            取消
          </button>
        )}
      </div>
      <div className="mt-3 flex items-center gap-4 text-xs text-muted">
        <span>状态:&nbsp;<StatusPill status={task.status} /></span>
        {task.duration_seconds != null && (
          <span>耗时: {task.duration_seconds.toFixed(0)}s</span>
        )}
        {task.exit_code != null && task.exit_code !== 0 && (
          <span className="text-danger">exit_code: {task.exit_code}</span>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded border border-border bg-bg p-3">
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-0.5 tabular text-base font-semibold text-foreground">{value}</div>
    </div>
  );
}
