/**
 * Alert hooks — list / summary / ack.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";

export type Alert = {
  id: number;
  severity: "info" | "warning" | "critical";
  source: string;
  message: string;
  payload: any;
  acked_by: string | null;
  acked_by_username: string | null;
  acked_at: string | null;
  created_at: string;
};

export type AlertSummary = {
  unacked_count: number;
  critical_unacked: number;
};

export function useAlertSummary() {
  return useQuery<AlertSummary>({
    queryKey: ["alert-summary"],
    queryFn: async () => (await api.get("/alerts/summary")).data,
    refetchInterval: 30_000,
  });
}

export function useAlerts(opts?: { severity?: string; only_unacked?: boolean }) {
  return useQuery<Alert[]>({
    queryKey: ["alerts", opts?.severity, opts?.only_unacked],
    queryFn: async () =>
      (await api.get("/alerts", {
        params: {
          severity: opts?.severity || undefined,
          only_unacked: opts?.only_unacked ?? false,
          limit: 200,
        },
      })).data,
    refetchInterval: 30_000,
  });
}

export function useAckAlert() {
  const qc = useQueryClient();
  return useMutation<Alert, Error, number>({
    mutationFn: async (id) => (await api.post(`/alerts/${id}/ack`)).data,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["alerts"] });
      qc.invalidateQueries({ queryKey: ["alert-summary"] });
    },
  });
}
