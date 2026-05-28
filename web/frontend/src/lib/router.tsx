/**
 * TanStack Router (code-based) config.
 *
 * Routes:
 *   /             → Dashboard
 *   /holdings     → Holdings
 *   /trading      → Trading (fills + orders tabs)
 *   /strategies   → Strategies
 *   /backtest     → Backtest runner + tasks
 *   /data         → Data health
 */
import {
  Outlet,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";

import { AppShell } from "@/layouts/AppShell";
import { Dashboard } from "@/routes/Dashboard";
import { Holdings } from "@/routes/Holdings";
import { Trading } from "@/routes/Trading";
import { Strategies } from "@/routes/Strategies";
import { Backtest } from "@/routes/Backtest";
import { Data } from "@/routes/Data";

const rootRoute = createRootRoute({
  component: () => (
    <AppShell>
      <Outlet />
    </AppShell>
  ),
});

const dashboardRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: Dashboard,
});
const holdingsRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/holdings",
  component: Holdings,
});
const tradingRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/trading",
  component: Trading,
});
const strategiesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/strategies",
  component: Strategies,
});
const backtestRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/backtest",
  component: Backtest,
});
const dataRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/data",
  component: Data,
});

const routeTree = rootRoute.addChildren([
  dashboardRoute,
  holdingsRoute,
  tradingRoute,
  strategiesRoute,
  backtestRoute,
  dataRoute,
]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
