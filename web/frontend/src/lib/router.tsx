/**
 * TanStack Router config with auth guard.
 *
 * - /login                 → public (Login route)
 * - everything else        → goes through AppShellRoute which calls
 *                            useMe() on render; redirects to /login if null.
 */
import {
  Outlet,
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
} from "@tanstack/react-router";

import { AppShell } from "@/layouts/AppShell";
import { Dashboard } from "@/routes/Dashboard";
import { Holdings } from "@/routes/Holdings";
import { Trading } from "@/routes/Trading";
import { Strategies } from "@/routes/Strategies";
import { Backtest } from "@/routes/Backtest";
import { Data } from "@/routes/Data";
import { Login } from "@/routes/Login";
import { ProtectedShell } from "@/layouts/ProtectedShell";

const rootRoute = createRootRoute({
  component: () => <Outlet />,
});

// Public routes (no AppShell)
const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  component: Login,
});

// Protected — wraps children in AppShell after auth check
const protectedRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "protected",
  component: () => (
    <ProtectedShell>
      <AppShell>
        <Outlet />
      </AppShell>
    </ProtectedShell>
  ),
});

const dashboardRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: "/",
  component: Dashboard,
});
const holdingsRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: "/holdings",
  component: Holdings,
});
const tradingRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: "/trading",
  component: Trading,
});
const strategiesRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: "/strategies",
  component: Strategies,
});
const backtestRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: "/backtest",
  component: Backtest,
});
const dataRoute = createRoute({
  getParentRoute: () => protectedRoute,
  path: "/data",
  component: Data,
});

const routeTree = rootRoute.addChildren([
  loginRoute,
  protectedRoute.addChildren([
    dashboardRoute,
    holdingsRoute,
    tradingRoute,
    strategiesRoute,
    backtestRoute,
    dataRoute,
  ]),
]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
