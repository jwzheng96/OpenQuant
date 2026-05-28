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
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import { useState } from "react";

type NavItem = {
  to: string;
  label: string;
  Icon: typeof LayoutDashboard;
};

const NAV: NavItem[] = [
  { to: "/", label: "仪表盘", Icon: LayoutDashboard },
  { to: "/holdings", label: "持仓", Icon: Briefcase },
  { to: "/trading", label: "交易流水", Icon: Receipt },
  { to: "/strategies", label: "策略", Icon: ListChecks },
  { to: "/backtest", label: "回测", Icon: Play },
  { to: "/data", label: "数据", Icon: Database },
];

export function Sidebar() {
  const [collapsed, setCollapsed] = useState(false);
  const { location } = useRouterState();

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
        {NAV.map(({ to, label, Icon }) => {
          const active = location.pathname === to;
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
              <Icon className="size-4 shrink-0" />
              {!collapsed && <span>{label}</span>}
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
