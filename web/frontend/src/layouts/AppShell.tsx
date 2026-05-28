/**
 * App shell: sidebar + topbar + content area.
 */
import { ReactNode, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { Sidebar } from "@/components/Sidebar";
import { bare } from "@/lib/api";

type Health = {
  status: string;
  app: string;
  environment: string;
  version: string;
};

function HealthIndicator() {
  const { data, isError } = useQuery<Health>({
    queryKey: ["health"],
    queryFn: async () => (await bare.get("/healthz")).data,
    refetchInterval: 30_000,
  });
  const ok = !isError && data?.status === "ok";
  return (
    <div className="flex items-center gap-2 text-xs text-muted">
      <span
        className={`size-2 rounded-full ${
          ok ? "bg-success animate-pulse" : "bg-danger"
        }`}
      />
      <span className="tabular">
        backend {ok ? data.environment : "?"}
      </span>
    </div>
  );
}

function NowClock() {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return (
    <span className="tabular text-xs text-muted">
      {now.toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" })}
    </span>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-screen overflow-hidden bg-bg text-foreground">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <header className="flex h-14 items-center justify-between border-b border-border bg-card px-6">
          <div className="text-sm font-medium text-foreground">
            OpenQuant
            <span className="ml-2 text-xs text-muted">/ 量化驾驶舱</span>
          </div>
          <div className="flex items-center gap-4">
            <NowClock />
            <HealthIndicator />
          </div>
        </header>
        <main className="flex-1 overflow-auto bg-bg p-6">{children}</main>
      </div>
    </div>
  );
}
