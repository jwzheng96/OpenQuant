/** React Query hooks for the factor research workbench. */
import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

export type FactorListItem = {
  name: string;
  available: boolean;
  mean_rank_ic: number | null;
  icir: number | null;
  n_days: number | null;
  pos_days_pct: number | null;
  first_date: string | null;
  last_date: string | null;
};

export type IcPoint = {
  trade_date: string;
  ic: number;
  rank_ic: number;
  n_obs: number;
};

export type QuintilePoint = {
  trade_date: string;
  q1: number; q2: number; q3: number; q4: number; q5: number;
  top_minus_bottom: number;
  cum_q1: number; cum_q2: number; cum_q3: number; cum_q4: number; cum_q5: number;
  cum_top_minus_bottom: number;
};

export type DecayPoint = {
  horizon: number;
  mean_rank_ic: number;
  icir: number;
};

export type FactorDetail = {
  summary: FactorListItem;
  ic_series: IcPoint[];
  quintile_series: QuintilePoint[];
  decay: DecayPoint[];
};

export function useFactors(horizon: number = 5) {
  return useQuery<FactorListItem[]>({
    queryKey: ["factors", horizon],
    queryFn: async () =>
      (await api.get("/factors", { params: { horizon } })).data,
    staleTime: 60_000,
  });
}

export function useFactorDetail(name: string | undefined, horizon: number = 5) {
  return useQuery<FactorDetail>({
    queryKey: ["factor-detail", name, horizon],
    queryFn: async () =>
      (await api.get(`/factors/${name}`, { params: { horizon } })).data,
    enabled: !!name,
    staleTime: 60_000,
  });
}
