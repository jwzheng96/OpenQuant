/**
 * Stock detail full-page route — /stock/:symbol
 *
 * Re-uses the StockDetail component (originally built for Holdings side
 * panel) at full page width. Symbol comes from URL params; strategy is
 * the currently-active one (from data health).
 */
import { useTranslation } from "react-i18next";
import { Link, useParams } from "@tanstack/react-router";
import { ArrowLeft, ExternalLink } from "lucide-react";

import { StockDetail } from "@/components/StockDetail";
import { useDataHealth } from "@/hooks/useDashboard";
import { StockTag } from "@/components/ui/StockTag";

export function Stock() {
  const { t } = useTranslation();
  const { symbol } = useParams({ strict: false }) as { symbol: string };
  const health = useDataHealth();
  const active = health.data?.active_strategy ?? undefined;

  if (!symbol) {
    return <Placeholder>无效股票代码</Placeholder>;
  }
  if (!active) {
    return <Placeholder>无活跃策略</Placeholder>;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <Link
          to="/holdings"
          className="inline-flex items-center gap-1 rounded p-1 text-muted hover:bg-card hover:text-foreground"
        >
          <ArrowLeft className="size-4" />
        </Link>
        <div className="flex items-baseline gap-3">
          <h1 className="text-2xl font-bold tracking-tight">
            <StockTag symbol={symbol} size="md" />
          </h1>
          <div className="text-xs text-muted">
            {t("nav.stock")} · {active}
          </div>
        </div>
        <a
          href={`https://xueqiu.com/S/${normalizeForXueqiu(symbol)}`}
          target="_blank"
          rel="noreferrer"
          className="ml-auto inline-flex items-center gap-1 rounded border border-border bg-card px-2 py-1 text-xs text-muted hover:bg-bg hover:text-foreground"
        >
          雪球 <ExternalLink className="size-3" />
        </a>
      </div>

      <div className="rounded-lg border border-border bg-card p-5">
        <StockDetail strategy={active} symbol={symbol} />
      </div>
    </div>
  );
}

function normalizeForXueqiu(sym: string): string {
  // 600519.SH → SH600519, 002421.SZ → SZ002421
  const [code, ex] = sym.split(".");
  if (!code || !ex) return sym;
  return `${ex.toUpperCase()}${code}`;
}

function Placeholder({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-[60vh] items-center justify-center text-sm text-muted">{children}</div>
  );
}
