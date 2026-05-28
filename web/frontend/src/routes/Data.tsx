/**
 * Data health — shows what data is available.
 */
import { useDataHealth } from "@/hooks/useDashboard";

export function Data() {
  const { data, isLoading } = useDataHealth();

  if (isLoading) return <Placeholder>加载…</Placeholder>;
  if (!data) return <Placeholder>无数据</Placeholder>;

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold tracking-tight">数据健康度</h1>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <Card title="日线最新日期" value={data.daily_latest ?? "—"} />
        <Card
          title="最新交易日股票数"
          value={`${data.daily_symbol_count ?? "—"} 只`}
        />
        <Card title="活跃策略" value={data.active_strategy ?? "—"} accent="accent" />
        <Card title="已回测策略" value={`${data.paper_strategies.length} 个`} />
        <Card title="因子库" value={`${data.factors.length} 个`} />
      </div>

      <section className="rounded-lg border border-border bg-card p-5">
        <h2 className="mb-3 text-sm font-semibold text-foreground/90">已回测策略列表</h2>
        <div className="flex flex-wrap gap-2">
          {data.paper_strategies.map((s) => (
            <span
              key={s}
              className={`rounded px-2 py-1 text-xs font-mono ${
                s === data.active_strategy
                  ? "bg-accent/15 text-accent ring-1 ring-accent/30"
                  : "bg-muted/10 text-muted"
              }`}
            >
              {s}
            </span>
          ))}
        </div>
      </section>

      <section className="rounded-lg border border-border bg-card p-5">
        <h2 className="mb-3 text-sm font-semibold text-foreground/90">因子库</h2>
        <div className="flex flex-wrap gap-2">
          {data.factors.map((f) => (
            <span
              key={f}
              className="rounded bg-muted/10 px-2 py-1 text-xs font-mono text-muted"
            >
              {f}
            </span>
          ))}
        </div>
      </section>
    </div>
  );
}

function Card({
  title,
  value,
  accent,
}: {
  title: string;
  value: string;
  accent?: "accent";
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="text-xs text-muted">{title}</div>
      <div
        className={`mt-1 text-lg font-semibold tabular ${
          accent === "accent" ? "text-accent" : "text-foreground"
        }`}
      >
        {value}
      </div>
    </div>
  );
}

function Placeholder({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-[60vh] items-center justify-center text-sm text-muted">{children}</div>
  );
}
