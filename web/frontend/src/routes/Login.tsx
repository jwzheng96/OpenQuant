/**
 * Login page — single dialog, no shell.
 */
import { useState, FormEvent } from "react";
import { Navigate } from "@tanstack/react-router";
import { Lock, User as UserIcon, AlertCircle } from "lucide-react";

import { useLogin, useMe } from "@/hooks/useAuth";

export function Login() {
  const me = useMe();
  const login = useLogin();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  if (me.isLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-bg text-muted">
        加载…
      </div>
    );
  }
  if (me.data) {
    return <Navigate to="/" />;
  }

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!username || !password) return;
    login.mutate({ username, password });
  };

  const errorMsg =
    login.error && (login.error as any).response?.data?.detail
      ? String((login.error as any).response.data.detail)
      : login.error
        ? "登录失败"
        : null;

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg p-6">
      <div className="w-full max-w-sm">
        {/* Brand */}
        <div className="mb-8 flex flex-col items-center gap-3">
          <div className="size-14 rounded-xl bg-accent" />
          <div className="text-center">
            <div className="text-2xl font-bold tracking-tight text-foreground">
              OpenQuant
            </div>
            <div className="mt-1 text-xs text-muted">量化交易驾驶舱</div>
          </div>
        </div>

        <form
          onSubmit={onSubmit}
          className="space-y-4 rounded-lg border border-border bg-card p-6 shadow-lg"
        >
          <h2 className="text-base font-semibold text-foreground">登录</h2>

          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted" htmlFor="username">
              用户名
            </label>
            <div className="relative">
              <UserIcon className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted" />
              <input
                id="username"
                type="text"
                autoComplete="username"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="w-full rounded border border-border bg-bg py-2 pl-9 pr-3 text-sm text-foreground placeholder:text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/40"
                placeholder="admin"
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted" htmlFor="password">
              密码
            </label>
            <div className="relative">
              <Lock className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted" />
              <input
                id="password"
                type="password"
                autoComplete="current-password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full rounded border border-border bg-bg py-2 pl-9 pr-3 text-sm text-foreground placeholder:text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/40"
                placeholder="••••••"
              />
            </div>
          </div>

          {errorMsg && (
            <div className="flex items-start gap-2 rounded border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger">
              <AlertCircle className="mt-0.5 size-3.5 shrink-0" />
              <span>{errorMsg}</span>
            </div>
          )}

          <button
            type="submit"
            disabled={login.isPending || !username || !password}
            className="w-full rounded bg-accent py-2 text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {login.isPending ? "登录中…" : "登录"}
          </button>
        </form>

        <div className="mt-4 text-center text-xs text-muted">
          首次登录使用 <code className="rounded bg-card px-1.5 py-0.5 font-mono">admin / admin</code>
        </div>
      </div>
    </div>
  );
}
