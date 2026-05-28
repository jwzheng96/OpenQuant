/**
 * Persistent left sidebar with nav links.
 */
import { Link, useRouterState } from "@tanstack/react-router";
import {
  LayoutDashboard,
  Briefcase,
  Receipt,
  ListChecks,
  Play,
  Database,
  Bell,
  ShieldAlert,
  Microscope,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useAlertSummary } from "@/hooks/useAlerts";

type NavItem = {
  to: string;
  i18nKey: string;
  Icon: typeof LayoutDashboard;
  badgeKey?: "unacked_count";
};

const NAV: NavItem[] = [
  { to: "/", i18nKey: "nav.dashboard", Icon: LayoutDashboard },
  { to: "/holdings", i18nKey: "nav.holdings", Icon: Briefcase },
  { to: "/trading", i18nKey: "nav.trading", Icon: Receipt },
  { to: "/strategies", i18nKey: "nav.strategies", Icon: ListChecks },
  { to: "/factors", i18nKey: "nav.factors", Icon: Microscope },
  { to: "/backtest", i18nKey: "nav.backtest", Icon: Play },
  { to: "/data", i18nKey: "nav.data", Icon: Database },
  { to: "/risk", i18nKey: "nav.risk", Icon: ShieldAlert },
  { to: "/alerts", i18nKey: "nav.alerts", Icon: Bell, badgeKey: "unacked_count" },
];

export function Sidebar() {
  const { t } = useTranslation();
  const [collapsed, setCollapsed] = useState(false);
  const { location } = useRouterState();
  const summary = useAlertSummary();

  const badgeFor = (item: NavItem): { count: number; critical: boolean } | null => {
    if (item.badgeKey !== "unacked_count") return null;
    const count = summary.data?.unacked_count ?? 0;
    if (count === 0) return null;
    return { count, critical: (summary.data?.critical_unacked ?? 0) > 0 };
  };

  return (
    <aside
      className={`flex h-screen flex-col border-r border-border bg-card transition-all ${
        collapsed ? "w-14" : "w-56"
      }`}
    >
      {/* Brand */}
      <div className="flex items-center gap-2.5 border-b border-border px-4 py-4">
        <div className="size-7 shrink-0 rounded-md bg-accent" />
        {!collapsed && (
          <div className="text-base font-bold tracking-tight text-foreground">OpenQuant</div>
        )}
      </div>

      {/* Nav */}
      <nav className="flex-1 space-y-0.5 px-2 py-3">
        {NAV.map((item) => {
          const { to, i18nKey, Icon } = item;
          const active = location.pathname === to;
          const label = t(i18nKey);
          const badge = badgeFor(item);
          return (
            <Link
              key={to}
              to={to}
              className={`flex items-center gap-3 rounded px-3 py-2 text-sm transition-colors ${
                active
                  ? "bg-accent/15 text-accent"
                  : "text-foreground/80 hover:bg-card hover:text-foreground"
              }`}
              title={collapsed ? label : undefined}
            >
              <div className="relative shrink-0">
                <Icon className="size-4" />
                {collapsed && badge && (
                  <span
                    className={`absolute -right-1 -top-1 size-2 rounded-full ${
                      badge.critical ? "bg-danger" : "bg-warning"
                    }`}
                  />
                )}
              </div>
              {!collapsed && (
                <span className="flex flex-1 items-center justify-between">
                  <span>{label}</span>
                  {badge && (
                    <span
                      className={`rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${
                        badge.critical
                          ? "bg-danger text-white"
                          : "bg-warning/20 text-warning"
                      }`}
                    >
                      {badge.count}
                    </span>
                  )}
                </span>
              )}
            </Link>
          );
        })}
      </nav>

      {/* Collapse button */}
      <button
        type="button"
        onClick={() => setCollapsed(!collapsed)}
        className="flex items-center justify-center border-t border-border py-2 text-muted hover:text-foreground"
      >
        {collapsed ? <ChevronRight className="size-4" /> : <ChevronLeft className="size-4" />}
      </button>
    </aside>
  );
}
