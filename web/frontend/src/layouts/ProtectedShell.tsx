/**
 * Wraps protected routes — redirects to /login if `useMe()` returns null.
 */
import { ReactNode } from "react";
import { Navigate } from "@tanstack/react-router";

import { useMe } from "@/hooks/useAuth";

export function ProtectedShell({ children }: { children: ReactNode }) {
  const { data, isLoading } = useMe();

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-bg text-sm text-muted">
        验证身份…
      </div>
    );
  }
  if (!data) {
    return <Navigate to="/login" />;
  }
  return <>{children}</>;
}
