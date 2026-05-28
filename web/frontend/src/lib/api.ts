/**
 * Thin axios wrapper. Phase 0: just exposes a base client. Later phases will
 * replace ad-hoc calls with the generated `api.gen.ts` types.
 */
import axios from "axios";

export const api = axios.create({
  baseURL: "/api/v1",
  withCredentials: true,        // send HttpOnly cookies for auth
  timeout: 15_000,
});

// Bare base for /healthz, /readyz which live outside /api/v1
export const bare = axios.create({
  baseURL: "/",
  withCredentials: false,
  timeout: 5_000,
});

export type ApiError = {
  code: string;
  message: string;
  details?: unknown;
};
