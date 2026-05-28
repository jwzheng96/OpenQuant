/**
 * Hooks for fetching the active strategy + its dashboard aggregate.
 */
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export type NavPoint = {
  trade_date: string;
  nav: number;
  cash: number;
  market_value: number;
  daily_ret: number;
};

export type FillRow = {
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

export type DashboardResp = {
  strategy: string;
  is_active: boolean;
  last_run: string | null;
  kpis: {
    nav: number;
    initial_cash: number;
    total_return: number;
    today_pnl_amount: number;
    today_pnl_pct: number;
    sharpe: number | null;
    max_drawdown: number | null;
    position_count: number;
    cash: number;
    cash_pct: number;
  };
  nav: NavPoint[];
  benchmark: { trade_date: string; nav: number }[];
  monthly: { month: string; ret: number; end_nav?: number }[];
  recent_fills: FillRow[];
};

export type DataHealth = {
  daily_latest: string | null;
  daily_symbol_count: number | null;
  paper_strategies: string[];
  active_strategy: string | null;
  factors: string[];
};

export function useDataHealth() {
  return useQuery<DataHealth>({
    queryKey: ["data-health"],
    queryFn: async () => (await api.get("/data/health")).data,
    refetchInterval: 60_000,
  });
}

export function useDashboard(strategy: string | undefined) {
  return useQuery<DashboardResp>({
    queryKey: ["dashboard", strategy],
    queryFn: async () => (await api.get(`/paper/${strategy}/dashboard`)).data,
    enabled: !!strategy,
    refetchInterval: 30_000,
  });
}
