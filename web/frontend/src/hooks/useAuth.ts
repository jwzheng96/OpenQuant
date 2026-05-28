/**
 * Auth hooks: useMe / useLogin / useLogout.
 *
 * The access token lives in an HttpOnly cookie set by the backend on /login.
 * React Query manages the cached user object.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";

import { api } from "@/lib/api";

export type Me = {
  id: string;
  username: string;
  email: string;
  role: "admin" | "trader" | "viewer";
  locale: string;
  is_active: boolean;
  last_login: string | null;
};

export function useMe() {
  return useQuery<Me | null>({
    queryKey: ["me"],
    queryFn: async () => {
      try {
        return (await api.get("/auth/me")).data as Me;
      } catch (e: any) {
        if (e?.response?.status === 401) return null;
        throw e;
      }
    },
    retry: false,
    refetchOnWindowFocus: false,
    staleTime: 60_000,
  });
}

export function useLogin() {
  const qc = useQueryClient();
  const nav = useNavigate();
  return useMutation({
    mutationFn: async (body: { username: string; password: string }) => {
      return (await api.post("/auth/login", body)).data;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["me"] });
      nav({ to: "/" });
    },
  });
}

export function useLogout() {
  const qc = useQueryClient();
  const nav = useNavigate();
  return useMutation({
    mutationFn: async () => {
      await api.post("/auth/logout");
    },
    onSuccess: () => {
      qc.setQueryData(["me"], null);
      qc.clear();
      nav({ to: "/login" });
    },
  });
}
