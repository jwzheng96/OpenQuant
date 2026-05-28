/**
 * App shell: sidebar + topbar + content area.
 */
import { ReactNode, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { LogOut, User as UserIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Sidebar } from "@/components/Sidebar";
import { bare } from "@/lib/api";
import { useMe, useLogout } from "@/hooks/useAuth";
import { ThemeLangSwitcher } from "@/components/ui/ThemeLangSwitcher";

type Health = {
  status: string;
  app: string;
  environment: string;
  version: string;
};

function HealthIndicator() {
  const { t } = useTranslation();
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
        {t("app.backend")} {ok ? data.environment : "?"}
      </span>
    </div>
  );
}

function NowClock() {
  const { i18n } = useTranslation();
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  // Always show Shanghai time; locale just changes the date-format
  return (
    <span className="tabular text-xs text-muted">
      {now.toLocaleString(i18n.language, { hour12: false, timeZone: "Asia/Shanghai" })}
    </span>
  );
}

function UserMenu() {
  const { t } = useTranslation();
  const me = useMe();
  const logout = useLogout();
  if (!me.data) return null;
  return (
    <div className="flex items-center gap-2">
      <div className="flex items-center gap-1.5 text-xs text-foreground/80">
        <UserIcon className="size-3.5 text-muted" />
        <span>{me.data.username}</span>
        <span className="rounded bg-accent/15 px-1.5 py-0.5 font-medium text-accent ring-1 ring-accent/30">
          {me.data.role}
        </span>
      </div>
      <button
        type="button"
        onClick={() => logout.mutate()}
        className="inline-flex items-center gap-1 rounded p-1.5 text-muted hover:bg-bg hover:text-foreground"
        title={t("app.logout")}
      >
        <LogOut className="size-3.5" />
      </button>
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  return (
    <div className="flex h-screen overflow-hidden bg-bg text-foreground">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-hidden">
        <header className="flex h-14 items-center justify-between border-b border-border bg-card px-6">
          <div className="text-sm font-medium text-foreground">
            {t("app.title")}
            <span className="ml-2 text-xs text-muted">/ {t("app.tagline")}</span>
          </div>
          <div className="flex items-center gap-4">
            <NowClock />
            <HealthIndicator />
            <div className="h-4 w-px bg-border" />
            <ThemeLangSwitcher />
            <div className="h-4 w-px bg-border" />
            <UserMenu />
          </div>
        </header>
        <main className="flex-1 overflow-auto bg-bg p-6">{children}</main>
      </div>
    </div>
  );
}
