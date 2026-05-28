/**
 * Backtest-runner hooks.
 *
 * Server-Sent Events use the browser-native EventSource. React Query
 * isn't used for the stream — we manage a buffer in component state.
 */
import { useEffect, useRef, useState } from "react";
import {
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { api } from "@/lib/api";

export type Task = {
  id: string;
  kind: string;
  status: "queued" | "running" | "success" | "failed" | "cancelled" | string;
  created_by: string | null;
  created_by_username: string | null;
  params: {
    strategy: string;
    from: string;
    to: string;
    initial_cash: number;
    reset: boolean;
  };
  started_at: string | null;
  finished_at: string | null;
  exit_code: number | null;
  result: any;
  created_at: string;
  duration_seconds: number | null;
};

export type BacktestSubmit = {
  strategy: string;
  start: string;
  end: string;
  initial_cash: number;
  reset: boolean;
};

export function useSubmitBacktest() {
  const qc = useQueryClient();
  return useMutation<Task, Error, BacktestSubmit>({
    mutationFn: async (body) => (await api.post("/backtest/run", body)).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["backtest-tasks"] }),
  });
}

export function useTasks(opts?: { mineOnly?: boolean }) {
  return useQuery<Task[]>({
    queryKey: ["backtest-tasks", opts?.mineOnly ?? false],
    queryFn: async () =>
      (await api.get("/backtest/tasks", {
        params: { mine_only: opts?.mineOnly ?? false, limit: 50 },
      })).data,
    refetchInterval: 5_000,    // poll every 5s for status changes
  });
}

export function useTask(id: string | undefined) {
  return useQuery<Task>({
    queryKey: ["backtest-task", id],
    queryFn: async () => (await api.get(`/backtest/tasks/${id}`)).data,
    enabled: !!id,
    refetchInterval: (q) => {
      const t = q.state.data;
      if (!t) return 3_000;
      if (t.status === "running" || t.status === "queued") return 3_000;
      return false;
    },
  });
}

export function useCancelTask() {
  const qc = useQueryClient();
  return useMutation<Task, Error, string>({
    mutationFn: async (id) =>
      (await api.post(`/backtest/tasks/${id}/cancel`)).data,
    onSuccess: (t) => {
      qc.invalidateQueries({ queryKey: ["backtest-tasks"] });
      qc.invalidateQueries({ queryKey: ["backtest-task", t.id] });
    },
  });
}

/**
 * Subscribe to a task's log stream. Returns rolling line buffer + done flag.
 *
 * Auto-reconnects up to 3 times on transport errors; closes cleanly when
 * the server emits the terminal `done` event.
 */
export function useTaskStream(taskId: string | undefined) {
  const [lines, setLines] = useState<string[]>([]);
  const [done, setDone] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!taskId) return;
    setLines([]);
    setDone(null);
    setErr(null);

    const es = new EventSource(`/api/v1/events/tasks/${taskId}`, {
      withCredentials: true,
    });
    esRef.current = es;

    es.addEventListener("message", (e: MessageEvent) => {
      setLines((prev) => {
        // Cap at 5000 lines to avoid DOM blow-up
        const next = [...prev, e.data];
        return next.length > 5000 ? next.slice(-5000) : next;
      });
    });
    es.addEventListener("done", (e: any) => {
      setDone(e.data || "done");
      es.close();
    });
    es.addEventListener("error", (e: any) => {
      // ReadyState=2 means closed. Browser auto-reconnects unless we close.
      if (es.readyState === EventSource.CLOSED) {
        setErr("stream disconnected");
      }
    });

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [taskId]);

  return { lines, done, err };
}
