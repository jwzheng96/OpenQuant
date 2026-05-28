/**
 * Thin axios wrapper.
 *
 *   - `api`  — talks to /api/v1, sends auth cookies
 *   - `bare` — health probe, no cookies
 *
 * A 401 from /api/v1 (except /auth/login + /auth/me which the auth flow
 * handles itself) kicks the user back to /login.
 */
import axios from "axios";

export const api = axios.create({
  baseURL: "/api/v1",
  withCredentials: true,        // send HttpOnly cookies for auth
  timeout: 15_000,
});

export const bare = axios.create({
  baseURL: "/",
  withCredentials: false,
  timeout: 5_000,
});

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err?.response?.status === 401) {
      const url: string = err?.config?.url ?? "";
      const isAuthPath =
        url.startsWith("/auth/login") ||
        url.startsWith("/auth/me") ||
        url.startsWith("/auth/logout");
      if (!isAuthPath && window.location.pathname !== "/login") {
        window.location.href = "/login";
      }
    }
    return Promise.reject(err);
  },
);

export type ApiError = {
  code: string;
  message: string;
  details?: unknown;
};
