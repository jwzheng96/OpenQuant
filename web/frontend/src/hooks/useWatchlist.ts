/** Watchlist hooks. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";

export type WatchlistItem = {
  symbol: string;
  name: string;
  note: string | null;
  added_at: string;
  last_close: number | null;
  pct_chg_today: number | null;
  pct_chg_5d: number | null;
  pct_chg_20d: number | null;
};

export function useWatchlist() {
  return useQuery<WatchlistItem[]>({
    queryKey: ["watchlist"],
    queryFn: async () => (await api.get("/watchlist")).data,
    refetchInterval: 60_000,
  });
}

export function useAddWatch() {
  const qc = useQueryClient();
  return useMutation<WatchlistItem, Error, { symbol: string; note?: string }>({
    mutationFn: async (body) => (await api.post("/watchlist", body)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist"] }),
  });
}

export function useRemoveWatch() {
  const qc = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: async (symbol) => {
      await api.delete(`/watchlist/${symbol}`);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist"] }),
  });
}
